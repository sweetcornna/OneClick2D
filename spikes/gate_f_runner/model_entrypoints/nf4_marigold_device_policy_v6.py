"""Fixed bounded device policy for the v6 NF4 Marigold entrypoint.

Bumped from the v4 policy because the effective-policy validation changed: a hook
whose execution device is missing is no longer silently dropped, so the identifier
no longer describes the same behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


POLICY_ID = "see-through.v6.nf4-marigold-bounded-offload.v2"
PSD_PIXEL_PROJECTION_ALGORITHM_ID = "source-visible-rgb-by-depth-mask-clean.v2.psd-postcorrect.v1"


def _device_name(value: object) -> str:
    return str(value).lower()


def _is_cuda(value: str) -> bool:
    return value == "cuda" or value.startswith("cuda:")


def _module_devices(module: object) -> list[str]:
    devices: set[str] = set()
    for inventory_name in ("parameters", "buffers"):
        inventory = getattr(module, inventory_name, None)
        if not callable(inventory):
            continue
        try:
            values = inventory(recurse=True)
        except TypeError:
            values = inventory()
        for value in values:
            device = getattr(value, "device", None)
            if device is not None:
                devices.add(_device_name(device))
    if not devices:
        device = getattr(module, "device", None)
        if device is not None:
            devices.add(_device_name(device))
    return sorted(devices)


def _hook_execution_devices(module: object) -> list[str | None]:
    modules: list[object]
    inventory = getattr(module, "modules", None)
    if callable(inventory):
        modules = list(inventory())
    else:
        modules = [module]
    devices: set[str | None] = set()
    for item in modules:
        hook = getattr(item, "_hf_hook", None)
        if hook is None:
            continue
        execution_device = getattr(hook, "execution_device", None)
        devices.add(None if execution_device is None else _device_name(execution_device))
    return sorted(devices, key=lambda device: "" if device is None else device)


class Nf4MarigoldExecutionDeviceMissingError(RuntimeError):
    pass


class Nf4MarigoldNonCudaExecutionDeviceError(RuntimeError):
    pass


class _UpstreamCudaMoveGuard:
    def __init__(self, component: object, name: str, suppressed: set[str]) -> None:
        self._component = component
        self._name = name
        self._suppressed = suppressed

    def to(self, *args: object, **kwargs: object) -> object:
        requested = kwargs.get("device")
        if requested is None and args:
            first = args[0]
            if isinstance(first, str) or hasattr(first, "type"):
                requested = first
        if requested is not None and _is_cuda(_device_name(requested)):
            self._suppressed.add(self._name)
            return self
        return self._component.to(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._component, name)


class Nf4MarigoldOffloadAdapter:
    """Adapts the pinned NF4 builder to a bounded, attestable device policy."""

    def __init__(
        self,
        pipeline: object,
        *,
        cpu_offload: Callable[..., object],
        execution_device: str = "cuda:0",
    ) -> None:
        self._pipeline = pipeline
        self._cpu_offload = cpu_offload
        self._execution_device = execution_device
        self._suppressed: set[str] = set()
        self._configured = False
        self._guards = {
            name: _UpstreamCudaMoveGuard(getattr(pipeline, name), name, self._suppressed)
            for name in ("vae", "unet", "text_encoder")
        }

    @property
    def vae(self) -> object:
        return self._guards["vae"]

    @property
    def unet(self) -> object:
        return self._guards["unet"]

    @property
    def text_encoder(self) -> object:
        return self._guards["text_encoder"]

    def cache_tag_embeds(self, *args: object, **kwargs: object) -> object:
        if self._configured:
            return self._pipeline.cache_tag_embeds(*args, **kwargs)

        text_encoder = getattr(self._pipeline, "text_encoder")
        is_quantized = bool(
            getattr(text_encoder, "is_quantized", False)
            or getattr(text_encoder, "quantization_method", None)
        )
        if not is_quantized:
            text_encoder.to(device=self._execution_device)
        result = self._pipeline.cache_tag_embeds(*args, **kwargs)

        getattr(self._pipeline, "unet").to(device=self._execution_device)
        vae = getattr(self._pipeline, "vae")
        parameters = getattr(vae, "parameters", None)
        offload_buffers = False
        if callable(parameters):
            try:
                offload_buffers = any(True for _ in parameters(recurse=False))
            except TypeError:
                offload_buffers = any(True for _ in parameters())
        self._cpu_offload(
            vae,
            execution_device=self._execution_device,
            offload_buffers=offload_buffers,
        )
        self._configured = True
        self._validate_effective_policy()
        return result

    def _validate_effective_policy(self) -> None:
        components = self.component_attestation()
        vae = components["vae"]
        unet = components["unet"]
        text_encoder = components["text_encoder"]
        if any(_is_cuda(device) for device in vae["storage_devices"]):
            raise RuntimeError("NF4 Marigold VAE was not offloaded")
        execution_hook_devices = vae["execution_hook_devices"]
        if not execution_hook_devices:
            raise Nf4MarigoldExecutionDeviceMissingError(
                "NF4 Marigold VAE execution hook is missing"
            )
        effective_execution_devices = [
            device for device in execution_hook_devices if device is not None
        ]
        if not effective_execution_devices:
            raise Nf4MarigoldExecutionDeviceMissingError(
                "NF4 Marigold VAE execution hook device is missing"
            )
        if not all(_is_cuda(device) for device in effective_execution_devices):
            raise Nf4MarigoldNonCudaExecutionDeviceError(
                "NF4 Marigold VAE execution hook points to a non-CUDA device"
            )
        if not unet["storage_devices"] or not all(_is_cuda(device) for device in unet["storage_devices"]):
            raise RuntimeError("NF4 Marigold UNet is not on the execution device")
        if any(_is_cuda(device) for device in text_encoder["storage_devices"]):
            raise RuntimeError("NF4 Marigold text encoder was not released")
        if not {"vae", "unet"}.issubset(self._suppressed):
            raise RuntimeError("pinned upstream NF4 CUDA moves were not intercepted")

    def component_attestation(self) -> dict[str, dict[str, object]]:
        pipeline = self._pipeline
        return {
            "vae": {
                "storage_devices": _module_devices(getattr(pipeline, "vae")),
                "execution_hook_devices": _hook_execution_devices(getattr(pipeline, "vae")),
                "upstream_cuda_move_suppressed": "vae" in self._suppressed,
                "disposition": "sequential-cpu-offload",
            },
            "unet": {
                "storage_devices": _module_devices(getattr(pipeline, "unet")),
                "execution_hook_devices": _hook_execution_devices(getattr(pipeline, "unet")),
                "upstream_cuda_move_suppressed": "unet" in self._suppressed,
                "disposition": "resident-quantized",
            },
            "text_encoder": {
                "storage_devices": _module_devices(getattr(pipeline, "text_encoder")),
                "execution_hook_devices": _hook_execution_devices(getattr(pipeline, "text_encoder")),
                "upstream_cuda_move_suppressed": "text_encoder" in self._suppressed,
                "disposition": "cached-and-released",
            },
        }

    def attestation(self, *, psd_projection_verified: bool) -> dict[str, object]:
        if not self._configured:
            raise RuntimeError("NF4 Marigold device policy is not configured")
        self._validate_effective_policy()
        return {
            "format": "oneclick2d.model-entrypoint-attestation",
            "format_version": "0.1.0",
            "policy_id": POLICY_ID,
            "requested_cpu_offload": True,
            "execution_device": self._execution_device,
            "components": self.component_attestation(),
            "psd_pixel_projection_algorithm_id": PSD_PIXEL_PROJECTION_ALGORITHM_ID,
            "psd_projection_verified": psd_projection_verified,
        }

    def __call__(self, *args: object, **kwargs: object) -> object:
        if not self._configured:
            raise RuntimeError("NF4 Marigold device policy was not configured")
        return self._pipeline(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pipeline, name)
