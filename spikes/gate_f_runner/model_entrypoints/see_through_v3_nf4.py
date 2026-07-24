"""Offload-safe entrypoint for the pinned See-through V3 NF4 spike."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import torch
from diffusers.models.modeling_utils import ModelMixin
from diffusers.utils import logging as diffusers_logging
from transformers.utils import logging as transformers_logging

from modules.layerdiffuse.diffusers_kdiffusion_sdxl import KDiffusionStableDiffusionXLPipeline


UPSTREAM_SCRIPT = Path("inference/scripts/inference_psd_quantized.py")
_ORIGINAL_MODEL_DEVICE = ModelMixin.device.fget


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


_ORIGINAL_PIPELINE_CALL = KDiffusionStableDiffusionXLPipeline.__call__


def _offload_safe_pipeline_call(self: KDiffusionStableDiffusionXLPipeline, *args, **kwargs):
    if getattr(self, "_offload_device", None) is not None:
        self.trans_vae.to(self._execution_device)
    return _ORIGINAL_PIPELINE_CALL(self, *args, **kwargs)


def install_patches() -> None:
    ModelMixin.device = property(_execution_aware_device)
    KDiffusionStableDiffusionXLPipeline.encode_cropped_prompt_77tokens = _encode_prompt
    KDiffusionStableDiffusionXLPipeline.__call__ = _offload_safe_pipeline_call
    diffusers_logging.disable_progress_bar()
    transformers_logging.disable_progress_bar()


def main() -> None:
    install_patches()
    sys.argv[0] = str(UPSTREAM_SCRIPT)
    runpy.run_path(str(UPSTREAM_SCRIPT), run_name="__main__")


if __name__ == "__main__":
    main()
