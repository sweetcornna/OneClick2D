"""Mask-cleaning source-preserving entrypoint for the pinned See-through V3 NF4 spike."""

from __future__ import annotations

import runpy
import json
import os
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
from nf4_marigold_device_policy import Nf4MarigoldOffloadAdapter
from utils import inference_utils


UPSTREAM_SCRIPT = Path("inference/scripts/inference_psd_quantized.py")
ALPHA_NOISE_FLOOR = 31
SOURCE_VISIBLE_ALPHA_THRESHOLD = ALPHA_NOISE_FLOOR
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


def _execution_aware_device(module: ModelMixin) -> torch.device:
    hook = getattr(module, "_hf_hook", None)
    execution_device = getattr(hook, "execution_device", None)
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
        raise RuntimeError("v4 NF4 Marigold requires the fixed CPU-offload policy")
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

    temporary_path = psd_path.with_name(f".{psd_path.name}.{os.getpid()}.v4.tmp")
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


def _write_entrypoint_attestation() -> None:
    if _MARIGOLD_ADAPTER is None:
        raise RuntimeError("NF4 Marigold device attestation is unavailable")
    destination = os.environ.get("ONECLICK2D_ENTRYPOINT_ATTESTATION")
    if not destination:
        raise RuntimeError("entrypoint attestation destination is missing")
    path = Path(destination)
    exact = json.dumps(
        _MARIGOLD_ADAPTER.attestation(psd_projection_verified=True),
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
        raise RuntimeError("v4 source-preserving entrypoint requires PSD assembly")
    _project_postprocessed_pixels_into_psd(source_directory)
    if _MARIGOLD_ADAPTER is not None:
        _write_entrypoint_attestation()
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
    install_patches()
    sys.argv[0] = str(UPSTREAM_SCRIPT)
    runpy.run_path(str(UPSTREAM_SCRIPT), run_name="__main__")


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
    else:
        main()
