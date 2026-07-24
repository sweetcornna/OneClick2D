"""Mask-cleaning source-preserving entrypoint for the pinned See-through V3 NF4 spike."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import numpy as np
import torch
from diffusers.models.modeling_utils import ModelMixin
from diffusers.utils import logging as diffusers_logging
from PIL import Image
from transformers.utils import logging as transformers_logging

from modules.layerdiffuse.diffusers_kdiffusion_sdxl import KDiffusionStableDiffusionXLPipeline
from utils import inference_utils


UPSTREAM_SCRIPT = Path("inference/scripts/inference_psd_quantized.py")
ALPHA_NOISE_FLOOR = 31
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
_ORIGINAL_FURTHER_EXTR = inference_utils.further_extr


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

    for index, (name, rgba) in enumerate(zip(PART_NAMES, layers)):
        visible_source = winner_index == index
        rgba[visible_source, :3] = source[visible_source, :3]
        Image.fromarray(rgba, mode="RGBA").save(source_directory / f"{name}.png", format="PNG")

    union_alpha = np.maximum.reduce(cleaned_alphas)
    reconstruction = source.copy()
    reconstruction[union_alpha == 0, :3] = 0
    reconstruction[..., 3] = union_alpha
    Image.fromarray(reconstruction, mode="RGBA").save(source_directory / "reconstruction.png", format="PNG")


def _source_preserving_further_extr(srcd: str, *args, **kwargs):
    _source_preserve_visible_pixels(Path(srcd))
    return _ORIGINAL_FURTHER_EXTR(srcd, *args, **kwargs)


def install_patches() -> None:
    ModelMixin.device = property(_execution_aware_device)
    KDiffusionStableDiffusionXLPipeline.encode_cropped_prompt_77tokens = _encode_prompt
    KDiffusionStableDiffusionXLPipeline.__call__ = _offload_safe_pipeline_call
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
    else:
        main()
