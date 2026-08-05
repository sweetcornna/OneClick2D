"""Mask-cleaning source-preserving entrypoint for the pinned See-through V3 NF4 spike."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import sys
from pathlib import Path

import numpy as np
import torch
from accelerate import cpu_offload
from diffusers.models.modeling_utils import ModelMixin
from diffusers.utils import logging as diffusers_logging
from PIL import Image
from transformers.utils import logging as transformers_logging

from modules.layerdiffuse.diffusers_kdiffusion_sdxl import KDiffusionStableDiffusionXLPipeline
from modules.marigold import MarigoldDepthPipeline
from nf4_marigold_device_policy_v6 import Nf4MarigoldOffloadAdapter
from utils import inference_utils


UPSTREAM_SCRIPT = Path("inference/scripts/inference_psd_quantized.py")
ALPHA_NOISE_FLOOR = 31
SOURCE_VISIBLE_ALPHA_THRESHOLD = ALPHA_NOISE_FLOOR
MAX_ATTESTATION_SOURCE_BYTES = 25 * 1024 * 1024
MAX_ARTIFACT_MANIFEST_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_MANIFEST_ENTRIES = 256
MAX_ARTIFACT_MANIFEST_DIRECTORIES = 64
MAX_ARTIFACT_MANIFEST_NODES = 320
MAX_ARTIFACT_MANIFEST_DEPTH = 8
MAX_ARTIFACT_RELATIVE_PATH_BYTES = 512
PART_NAMES = (
    "front hair",
    "back hair",
    "headwear",
    "face",
    "eyebrow",
    "eyelash",
    "irides",
    "eyewhite",
    "eyewear",
    "ears",
    "earwear",
    "nose",
    "mouth",
    "neck",
    "neckwear",
    "topwear",
    "handwear",
    "bottomwear",
    "legwear",
    "footwear",
    "tail",
    "wings",
    "objects",
)
VISIBLE_PRIORITY = {
    name: index
    for index, name in enumerate(
        (
            "back hair",
            "tail",
            "wings",
            "neck",
            "topwear",
            "bottomwear",
            "legwear",
            "footwear",
            "face",
            "ears",
            "eyewhite",
            "irides",
            "eyebrow",
            "nose",
            "mouth",
            "eyelash",
            "front hair",
            "headwear",
            "neckwear",
            "handwear",
            "eyewear",
            "earwear",
            "objects",
        )
    )
}
_ORIGINAL_MODEL_DEVICE = ModelMixin.device.fget
_ORIGINAL_PIPELINE_CALL = KDiffusionStableDiffusionXLPipeline.__call__
_ORIGINAL_MARIGOLD_FROM_PRETRAINED = MarigoldDepthPipeline.from_pretrained
_ORIGINAL_FURTHER_EXTR = inference_utils.further_extr
_MARIGOLD_ADAPTER: Nf4MarigoldOffloadAdapter | None = None
_PSD_PROJECTION_EXECUTED = False


def _execution_aware_device(module: ModelMixin) -> torch.device:
    hook = getattr(module, "_hf_hook", None)
    execution_device = getattr(hook, "execution_device", None)
    if execution_device is not None:
        return torch.device(execution_device)
    inventory = getattr(module, "modules", None)
    if callable(inventory):
        for child in inventory():
            if child is module:
                continue
            child_hook = getattr(child, "_hf_hook", None)
            execution_device = getattr(child_hook, "execution_device", None)
            if execution_device is not None:
                return torch.device(execution_device)
    if _ORIGINAL_MODEL_DEVICE is None:
        raise RuntimeError("model device property is unavailable")
    return _ORIGINAL_MODEL_DEVICE(module)


@torch.inference_mode()
def _encode_prompt(self: KDiffusionStableDiffusionXLPipeline, prompt: object):
    device = self._execution_device
    prompt_embeds_list = []
    pooled_prompt_embeds = None
    batch_size = None
    for tokenizer, text_encoder in zip(
        (self.tokenizer, self.tokenizer_2),
        (self.text_encoder, self.text_encoder_2),
    ):
        text_input_ids = tokenizer(
            prompt,
            padding="max_length",
            max_length=tokenizer.model_max_length,
            truncation=True,
            return_tensors="pt",
        ).input_ids
        encoded = text_encoder(
            text_input_ids.to(device),
            output_hidden_states=True,
            return_dict=False,
        )
        pooled_prompt_embeds = encoded[0]
        prompt_embeds = encoded[-1][-2]
        batch_size, sequence_length, _ = prompt_embeds.shape
        prompt_embeds_list.append(prompt_embeds.view(batch_size, sequence_length, -1))
    if pooled_prompt_embeds is None or batch_size is None:
        raise RuntimeError("prompt encoder inventory is empty")
    prompt_embeds = torch.concat(prompt_embeds_list, dim=-1).to(
        dtype=self.unet.dtype,
        device=device,
    )
    return prompt_embeds, pooled_prompt_embeds.view(batch_size, -1)


def _offload_safe_pipeline_call(self: KDiffusionStableDiffusionXLPipeline, *args, **kwargs):
    if getattr(self, "_offload_device", None) is not None:
        self.trans_vae.to(self._execution_device)
    return _ORIGINAL_PIPELINE_CALL(self, *args, **kwargs)


def _build_fixed_nf4_marigold(cls, *args, **kwargs):
    del cls
    if "--cpu_offload" not in sys.argv or "--no_cpu_offload" in sys.argv:
        raise RuntimeError("v5 NF4 Marigold requires the fixed CPU-offload policy")
    pipeline = _ORIGINAL_MARIGOLD_FROM_PRETRAINED(*args, **kwargs)
    adapter = Nf4MarigoldOffloadAdapter(
        pipeline,
        cpu_offload=cpu_offload,
        execution_device="cuda:0",
    )
    global _MARIGOLD_ADAPTER
    _MARIGOLD_ADAPTER = adapter
    return adapter


def _clean_alpha(alpha: np.ndarray) -> np.ndarray:
    alpha_u16 = alpha.astype(np.uint16)
    delta = np.maximum(alpha_u16, ALPHA_NOISE_FLOOR) - ALPHA_NOISE_FLOOR
    scale = 255 - ALPHA_NOISE_FLOOR
    return ((delta * 255 + scale // 2) // scale).astype(np.uint8)


def _source_preserve_visible_pixels(source_directory: Path) -> None:
    source_path = source_directory / "src_img.png"
    with Image.open(source_path, formats=("PNG",)) as image:
        source = np.array(image.convert("RGBA"), dtype=np.uint8)
    height, width = source.shape[:2]
    winner_depth = np.full((height, width), 256, dtype=np.uint16)
    winner_priority = np.full((height, width), -1, dtype=np.int16)
    winner_index = np.full((height, width), -1, dtype=np.int16)
    layers: list[np.ndarray] = []
    cleaned_alphas: list[np.ndarray] = []

    for index, name in enumerate(PART_NAMES):
        with Image.open(source_directory / f"{name}.png", formats=("PNG",)) as image:
            rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
        with Image.open(source_directory / f"{name}_depth.png", formats=("PNG",)) as image:
            depth = np.array(image.convert("L"), dtype=np.uint8)
        if rgba.shape != source.shape or depth.shape != source.shape[:2]:
            raise RuntimeError("source-preservation input canvas mismatch")
        alpha = _clean_alpha(rgba[..., 3])
        rgba[..., 3] = alpha
        layers.append(rgba)
        cleaned_alphas.append(alpha)
        visible = alpha > 0
        priority = VISIBLE_PRIORITY[name]
        closer = visible & (
            (depth.astype(np.uint16) < winner_depth)
            | ((depth.astype(np.uint16) == winner_depth) & (priority > winner_priority))
        )
        winner_depth[closer] = depth[closer]
        winner_priority[closer] = priority
        winner_index[closer] = index

    source_visible = source[..., 3] > SOURCE_VISIBLE_ALPHA_THRESHOLD
    for index, (name, rgba) in enumerate(zip(PART_NAMES, layers)):
        visible_source = (winner_index == index) & source_visible
        rgba[visible_source, :3] = source[visible_source, :3]
        Image.fromarray(rgba, mode="RGBA").save(source_directory / f"{name}.png", format="PNG")

    union_alpha = np.maximum.reduce(cleaned_alphas)
    reconstruction = source.copy()
    reconstruction[union_alpha == 0, :3] = 0
    reconstruction[..., 3] = union_alpha
    Image.fromarray(reconstruction, mode="RGBA").save(source_directory / "reconstruction.png", format="PNG")


def _project_postprocessed_pixels_into_psd(source_directory: Path) -> None:
    from psd_tools import PSDImage

    psd_path = source_directory.parent / f"{source_directory.name}.psd"
    original = PSDImage.open(psd_path)
    with Image.open(source_directory / "src_img.png", formats=("PNG",)) as source_image:
        source_size = source_image.size
    if original.size != source_size:
        raise RuntimeError("PSD source-preservation canvas mismatch")

    rebuilt = PSDImage.new(mode="RGBA", size=original.size, depth=8)
    expected: list[tuple[str, tuple[int, int, int, int], bytes]] = []
    for layer in original:
        layer_image = layer.topil(apply_icc=False)
        if layer_image is None:
            raise RuntimeError("PSD source-preservation layer is unreadable")
        layer_image = layer_image.convert("RGBA")
        left, top, right, bottom = layer.bbox
        if (
            left < 0
            or top < 0
            or right > original.width
            or bottom > original.height
            or layer_image.size != (right - left, bottom - top)
        ):
            raise RuntimeError("PSD source-preservation layer bounds are invalid")
        base_name = layer.name[:-2] if layer.name.endswith(("-l", "-r")) else layer.name
        semantic_path = source_directory / f"{base_name}.png"
        if not semantic_path.is_file():
            raise RuntimeError("PSD source-preservation semantic layer is missing")
        with Image.open(semantic_path, formats=("PNG",)) as semantic_image:
            semantic_crop = semantic_image.convert("RGBA").crop((left, top, right, bottom))
        projected = Image.merge(
            "RGBA",
            (*semantic_crop.split()[:3], layer_image.getchannel("A")),
        )
        rebuilt.create_pixel_layer(
            projected,
            name=layer.name,
            top=top,
            left=left,
            opacity=255,
        )
        expected.append((layer.name, (left, top, right, bottom), projected.tobytes()))

    temporary_path = psd_path.with_name(f".{psd_path.name}.{os.getpid()}.v5.tmp")
    try:
        rebuilt.save(temporary_path)
        readback = PSDImage.open(temporary_path)
        actual_layers = list(readback)
        if len(actual_layers) != len(expected):
            raise RuntimeError("PSD source-preservation readback layer count mismatch")
        for actual, (name, bbox, pixels) in zip(actual_layers, expected, strict=True):
            image = actual.topil(apply_icc=False)
            if (
                actual.name != name
                or tuple(actual.bbox) != bbox
                or image is None
                or image.convert("RGBA").tobytes() != pixels
            ):
                raise RuntimeError("PSD source-preservation readback mismatch")
        os.replace(temporary_path, psd_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256_file(path: Path, maximum: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_length = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(min(1024 * 1024, maximum - byte_length + 1)):
                byte_length += len(chunk)
                if byte_length > maximum:
                    raise RuntimeError("attestation input byte count exceeded its bound")
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeError("attestation input file is unreadable") from exc
    return digest.hexdigest(), byte_length


def _bounded_artifact_files(
    root: Path,
    excluded_path: Path | None = None,
) -> list[tuple[str, Path, int]]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("attestation artifact directory is invalid")
    files: list[tuple[str, Path, int]] = []
    directories = 0
    nodes = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    try:
        while stack:
            directory, depth = stack.pop()
            if depth > MAX_ARTIFACT_MANIFEST_DEPTH:
                raise RuntimeError("attestation artifact manifest depth exceeded its bound")
            with os.scandir(directory) as entries:
                for entry in entries:
                    candidate = Path(entry.path)
                    if excluded_path is not None and candidate == excluded_path:
                        continue
                    nodes += 1
                    if nodes > MAX_ARTIFACT_MANIFEST_NODES:
                        raise RuntimeError("attestation artifact manifest node count exceeded its bound")
                    relative = candidate.relative_to(root).as_posix()
                    if len(relative.encode("utf-8")) > MAX_ARTIFACT_RELATIVE_PATH_BYTES:
                        raise RuntimeError("attestation artifact manifest path length exceeded its bound")
                    if entry.is_symlink():
                        raise RuntimeError("attestation artifact manifest contains a symlink")
                    if entry.is_dir(follow_symlinks=False):
                        directories += 1
                        if directories > MAX_ARTIFACT_MANIFEST_DIRECTORIES:
                            raise RuntimeError(
                                "attestation artifact manifest directory count exceeded its bound"
                            )
                        stack.append((candidate, depth + 1))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        raise RuntimeError("attestation artifact manifest contains a non-regular node")
                    if len(files) >= MAX_ARTIFACT_MANIFEST_ENTRIES:
                        raise RuntimeError("attestation artifact manifest entry count exceeded its bound")
                    size = entry.stat(follow_symlinks=False).st_size
                    if size < 0:
                        raise RuntimeError("attestation artifact manifest file size is invalid")
                    files.append((relative, candidate, size))
    except OSError as exc:
        raise RuntimeError("attestation artifact directory is unreadable") from exc
    return sorted(files, key=lambda item: item[0])


def _artifact_manifest(root: Path, attestation_path: Path) -> list[dict[str, object]]:
    manifest: list[dict[str, object]] = []
    total = 0
    for relative, candidate, size in _bounded_artifact_files(root, attestation_path):
        if size > MAX_ARTIFACT_MANIFEST_BYTES - total:
            raise RuntimeError("attestation artifact manifest byte count exceeded its bound")
        digest, byte_length = _sha256_file(candidate, MAX_ARTIFACT_MANIFEST_BYTES - total)
        if byte_length != size:
            raise RuntimeError("attestation artifact manifest file changed while hashing")
        total += byte_length
        manifest.append(
            {
                "path": relative,
                "sha256": digest,
                "byte_length": byte_length,
            }
        )
    if not any(str(item["path"]).endswith(".psd") for item in manifest):
        raise RuntimeError("attestation artifact manifest contains no PSD")
    return manifest


def _write_entrypoint_attestation() -> None:
    if not _PSD_PROJECTION_EXECUTED:
        raise RuntimeError("PSD pixel projection did not complete; attestation refused")
    if _MARIGOLD_ADAPTER is None:
        raise RuntimeError("NF4 Marigold device attestation is unavailable")
    destination = os.environ.get("ONECLICK2D_ENTRYPOINT_ATTESTATION")
    if not destination:
        raise RuntimeError("entrypoint attestation destination is missing")
    path = Path(destination)
    challenge = os.environ.get("ONECLICK2D_ATTESTATION_CHALLENGE")
    if not challenge:
        raise RuntimeError("entrypoint attestation challenge is missing")
    source = os.environ.get("ONECLICK2D_ATTESTATION_SOURCE")
    if not source:
        raise RuntimeError("entrypoint attestation source is missing")
    source_sha256, _ = _sha256_file(Path(source), MAX_ATTESTATION_SOURCE_BYTES)
    artifact_manifest = _artifact_manifest(path.parent, path)
    artifact_manifest_exact = json.dumps(
        artifact_manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    attestation = _MARIGOLD_ADAPTER.attestation(psd_projection_verified=True)
    attestation["binding"] = {
        "challenge": challenge,
        "source_sha256": source_sha256,
        "artifact_manifest_digest": hashlib.sha256(artifact_manifest_exact).hexdigest(),
        "artifact_manifest": artifact_manifest,
    }
    exact = json.dumps(
        attestation,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_bytes(exact)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _source_preserving_further_extr(srcd: str, *args, **kwargs):
    source_directory = Path(srcd)
    _source_preserve_visible_pixels(source_directory)
    result = _ORIGINAL_FURTHER_EXTR(srcd, *args, **kwargs)
    save_to_psd = kwargs.get("save_to_psd", args[1] if len(args) > 1 else False)
    if not save_to_psd:
        raise RuntimeError("v5 source-preserving entrypoint requires PSD assembly")
    _project_postprocessed_pixels_into_psd(source_directory)
    global _PSD_PROJECTION_EXECUTED
    _PSD_PROJECTION_EXECUTED = True
    return result


def install_patches() -> None:
    ModelMixin.device = property(_execution_aware_device)
    KDiffusionStableDiffusionXLPipeline.encode_cropped_prompt_77tokens = _encode_prompt
    KDiffusionStableDiffusionXLPipeline.__call__ = _offload_safe_pipeline_call
    MarigoldDepthPipeline.from_pretrained = classmethod(_build_fixed_nf4_marigold)
    inference_utils.further_extr = _source_preserving_further_extr
    diffusers_logging.disable_progress_bar()
    transformers_logging.disable_progress_bar()


def main() -> None:
    global _PSD_PROJECTION_EXECUTED
    _PSD_PROJECTION_EXECUTED = False
    install_patches()
    source = os.environ.get("ONECLICK2D_ATTESTATION_SOURCE")
    if not source:
        raise RuntimeError("v5 source path environment is missing")
    if "--srcp" in sys.argv:
        raise RuntimeError("v5 source path must not be supplied in process arguments")
    sys.argv[0] = str(UPSTREAM_SCRIPT)
    sys.argv[1:1] = ["--srcp", source]
    try:
        runpy.run_path(str(UPSTREAM_SCRIPT), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None or exc.code == 0:
            _write_entrypoint_attestation()
        raise
    _write_entrypoint_attestation()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--source-preserve-only":
        _source_preserve_visible_pixels(Path(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--source-preserve-and-assemble-only":
        _source_preserving_further_extr(
            sys.argv[2],
            rotate=False,
            save_to_psd=True,
            tblr_split=False,
        )
        if _MARIGOLD_ADAPTER is not None:
            _write_entrypoint_attestation()
    else:
        main()
