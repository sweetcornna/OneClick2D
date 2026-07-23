"""Fixed Pillow simple-cutout comparator for the disposable Gate F spike."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .contracts import (
    ArtifactRef,
    Determinism,
    ProducerKind,
    ResourceLimitExceeded,
    SpecValidationError,
    StageContext,
    StageContractError,
    StageOutcome,
    StageStatus,
)
from .raster import RasterBlocked, _load_pillow, _verify_output_png, build_raster_registry
from .runner import AdapterRegistry
from .runtime import canonical_json_bytes, sha256_bytes, strict_load_json_bytes

PARAMETER_ORDER = (
    "head.yaw",
    "head.pitch",
    "eye.left.open",
    "eye.right.open",
    "mouth.open",
)
PATCH_SPECS = (
    ("head", None, "not-applicable", (20, 5, 80, 60)),
    ("eye.screen-left", "eye.right.open", "right", (27, 25, 47, 40)),
    ("eye.screen-right", "eye.left.open", "left", (53, 25, 73, 40)),
    ("mouth", "mouth.open", "not-applicable", (40, 42, 60, 56)),
)
FROZEN_CONFIG_SHA256 = "4e14dfab2fc3b284363a614111c0a6a677b288ae839d72b7d0eaf4b004217e47"
FROZEN_FRAME_IDS = (
    "neutral",
    "yaw.min",
    "yaw.max",
    "pitch.min",
    "pitch.max",
    "eye.left.closed",
    "eye.right.closed",
    "eyes.closed",
    "mouth.max",
    "yaw.min-pitch.min",
    "yaw.max-eyes.closed",
    "yaw.min-pitch.max-mouth.max",
)


@dataclass(frozen=True)
class Affine:
    a: Fraction
    b: Fraction
    c: Fraction
    d: Fraction
    e: Fraction
    f: Fraction

    @classmethod
    def identity(cls) -> "Affine":
        return cls(Fraction(1), Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(0))

    def compose(self, inner: "Affine") -> "Affine":
        """Return this transform applied after ``inner``."""
        return Affine(
            self.a * inner.a + self.b * inner.d,
            self.a * inner.b + self.b * inner.e,
            self.a * inner.c + self.b * inner.f + self.c,
            self.d * inner.a + self.e * inner.d,
            self.d * inner.b + self.e * inner.e,
            self.d * inner.c + self.e * inner.f + self.f,
        )

    def inverse_tuple(self) -> tuple[float, float, float, float, float, float]:
        determinant = self.a * self.e - self.b * self.d
        if determinant == 0:
            raise StageContractError("simple-cutout transform is not invertible")
        values = (
            self.e / determinant,
            -self.b / determinant,
            (self.b * self.f - self.e * self.c) / determinant,
            -self.d / determinant,
            self.a / determinant,
            (self.d * self.c - self.a * self.f) / determinant,
        )
        result = tuple(float(value) for value in values)
        if not all(math.isfinite(value) for value in result):
            raise StageContractError("simple-cutout transform is not finite")
        return result

    def map_point(self, x: Fraction, y: Fraction) -> tuple[Fraction, Fraction]:
        return self.a * x + self.b * y + self.c, self.d * x + self.e * y + self.f


@dataclass(frozen=True)
class Patch:
    id: str
    parameter_id: str | None
    character_side: str
    percent_xyxy: tuple[int, int, int, int]
    box: tuple[int, int, int, int]

    @property
    def pivot(self) -> tuple[Fraction, Fraction]:
        left, top, right, bottom = self.box
        return Fraction(left + right, 2), Fraction(top + bottom, 2)


@dataclass(frozen=True)
class Frame:
    id: str
    parameters: dict[str, int]


def _parse_frozen_config(data: bytes) -> tuple[Frame, ...]:
    value = strict_load_json_bytes(data)
    if sha256_bytes(canonical_json_bytes(value)) != FROZEN_CONFIG_SHA256:
        raise StageContractError("simple-cutout config does not match frozen v1 profile")
    if not isinstance(value, dict) or not isinstance(value.get("frames"), list):
        raise StageContractError("simple-cutout config is invalid")
    frames: list[Frame] = []
    for expected_id, item in zip(FROZEN_FRAME_IDS, value["frames"], strict=True):
        if not isinstance(item, dict) or item.get("id") != expected_id or not isinstance(item.get("parameters"), dict):
            raise StageContractError("simple-cutout config is invalid")
        parameters = item["parameters"]
        if tuple(parameters) != PARAMETER_ORDER:
            raise StageContractError("simple-cutout config is invalid")
        frames.append(Frame(expected_id, dict(parameters)))
    return tuple(frames)


def _resolve_box(percent_xyxy: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = percent_xyxy
    left = x0 * width // 100
    top = y0 * height // 100
    right = (x1 * width + 99) // 100
    bottom = (y1 * height + 99) // 100
    box = (max(0, left), max(0, top), min(width, right), min(height, bottom))
    if box[0] >= box[2] or box[1] >= box[3]:
        raise StageContractError("simple-cutout patch is empty")
    return box


def _build_patches(width: int, height: int) -> tuple[Patch, ...]:
    return tuple(
        Patch(patch_id, parameter_id, side, percent, _resolve_box(percent, width, height))
        for patch_id, parameter_id, side, percent in PATCH_SPECS
    )


def _head_affine(parameters: dict[str, int], width: int, height: int, pivot_y: Fraction) -> Affine:
    yaw = Fraction(parameters["head.yaw"], 15)
    pitch = Fraction(parameters["head.pitch"], 10)
    shear = Fraction(3, 100) * yaw
    translate_x = Fraction(width, 40) * yaw
    translate_y = -Fraction(height, 50) * pitch
    return Affine(Fraction(1), shear, translate_x - shear * pivot_y, Fraction(0), Fraction(1), translate_y)


def _scale_y_affine(scale: Fraction, pivot_y: Fraction) -> Affine:
    return Affine(Fraction(1), Fraction(0), Fraction(0), Fraction(0), scale, (Fraction(1) - scale) * pivot_y)


def _patch_affine(patch: Patch, parameters: dict[str, int], head: Affine) -> Affine:
    if patch.id == "head":
        return head
    if patch.id.startswith("eye."):
        openness = Fraction(parameters[patch.parameter_id or ""])
        local = _scale_y_affine(Fraction(3, 20) + Fraction(17, 20) * openness, patch.pivot[1])
    else:
        openness = Fraction(parameters["mouth.open"])
        local = _scale_y_affine(Fraction(1) + Fraction(7, 20) * openness, patch.pivot[1])
    return head.compose(local)


def _patch_is_active(patch: Patch, parameters: dict[str, int]) -> bool:
    if patch.id == "head":
        return parameters["head.yaw"] != 0 or parameters["head.pitch"] != 0
    if patch.id.startswith("eye."):
        return parameters[patch.parameter_id or ""] != 1
    return parameters["mouth.open"] != 0


def _feather_mask(size: tuple[int, int], backend: Any) -> Any:
    width, height = size
    values = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            distance_x2 = min(2 * x + 1, 2 * (width - x) - 1)
            distance_y2 = min(2 * y + 1, 2 * (height - y) - 1)
            distance_x2 = min(distance_x2, distance_y2)
            values[y * width + x] = min(255, (255 * distance_x2 + 2) // 4)
    return backend.Image.frombytes("L", size, bytes(values))


def _prepare_layers(source: Any, patches: tuple[Patch, ...], backend: Any) -> dict[str, Any]:
    from PIL import ImageChops

    layers: dict[str, Any] = {}
    try:
        for patch in patches:
            crop = source.crop(patch.box)
            mask = _feather_mask(crop.size, backend)
            alpha = crop.getchannel("A")
            try:
                crop.putalpha(ImageChops.multiply(alpha, mask))
            finally:
                alpha.close()
                mask.close()
            layers[patch.id] = crop
    except Exception:
        for layer in layers.values():
            layer.close()
        raise
    return layers


def _render_frame(
    source: Any,
    layers: dict[str, Any],
    patches: tuple[Patch, ...],
    frame: Frame,
    backend: Any,
    context: StageContext,
) -> Any:
    result = source.copy()
    head = _head_affine(frame.parameters, source.width, source.height, patches[0].pivot[1])
    for patch in patches:
        context.cancellation.checkpoint()
        if not _patch_is_active(patch, frame.parameters):
            continue
        transform = _patch_affine(patch, frame.parameters, head)
        if transform == Affine.identity():
            continue
        inverse = transform.inverse_tuple()
        left, top, _, _ = patch.box
        local_inverse = (
            inverse[0],
            inverse[1],
            inverse[2] - left,
            inverse[3],
            inverse[4],
            inverse[5] - top,
        )
        transformed = layers[patch.id].transform(
            source.size,
            backend.Image.Transform.AFFINE,
            local_inverse,
            resample=backend.Image.Resampling.BILINEAR,
            fillcolor=(0, 0, 0, 0),
        )
        result.alpha_composite(transformed)
        transformed.close()
    return result


def _write_frame(image: Any, index: int, frame_id: str, context: StageContext, backend: Any) -> tuple[str, ArtifactRef]:
    name = f"frame.{index:03d}.{frame_id}.png"
    writer = context.sink.open_binary(name, role="simple_cutout_frame", media_type="image/png")
    pnginfo = backend.PngImagePlugin.PngInfo()
    pnginfo.add(b"sRGB", b"\x00")
    with writer:
        image.save(
            writer,
            format="PNG",
            optimize=False,
            compress_level=9,
            pnginfo=pnginfo,
            icc_profile=None,
            exif=b"",
        )
    artifact = writer.artifact
    _verify_output_png(artifact.path.read_bytes(), image.size)
    return name, artifact


def _select_inputs(context: StageContext) -> tuple[ArtifactRef, ArtifactRef]:
    by_role: dict[str, ArtifactRef] = {}
    for artifact in context.spec.input_artifacts:
        if artifact.role in by_role or artifact.role not in {"normalized_raster", "raster_normalization_report"}:
            raise StageContractError("simple-cutout inputs do not match the normalization contract")
        by_role[artifact.role] = artifact
    if set(by_role) != {"normalized_raster", "raster_normalization_report"}:
        raise StageContractError("simple-cutout inputs do not match the normalization contract")
    raster = by_role["normalized_raster"]
    report = by_role["raster_normalization_report"]
    if raster.media_type != "image/png" or report.media_type != "application/vnd.oneclick2d.raster-normalization-report+json":
        raise StageContractError("simple-cutout input media types do not match the normalization contract")
    return raster, report


def _validate_normalized_report(raster: ArtifactRef, report: ArtifactRef) -> tuple[dict[str, object], int, int]:
    try:
        value = strict_load_json_bytes(report.path.read_bytes())
    except (SpecValidationError, OSError) as exc:
        raise StageContractError("normalization report is invalid") from exc
    report_keys = {
        "format", "format_version", "scope", "adapter_id", "adapter_version", "contract_id",
        "input", "orientation", "color_policy", "output", "metadata_removed", "finding_codes",
        "runtime", "gate_f_feasibility_proven",
    }
    output_keys = {"width", "height", "mode", "bit_depth", "color_space", "alpha_mode", "sha256", "byte_length"}
    if not isinstance(value, dict) or set(value) != report_keys:
        raise StageContractError("normalization report is invalid")
    input_value = value["input"]
    orientation = value["orientation"]
    output = value["output"]
    findings = value["finding_codes"]
    input_keys = {"format", "media_type", "width", "height", "mode", "bit_depth", "frame_count"}
    if (
        value["format"] != "oneclick2d.raster-normalization-report"
        or value["format_version"] != "0.1.0"
        or value["scope"] != "disposable-gate-f-spike"
        or value["adapter_id"] != "raster.normalize.pillow.v1"
        or value["adapter_version"] != "0.1.0"
        or value["contract_id"] != "oc2d.spike.raster-normalize.v1"
        or not isinstance(input_value, dict)
        or set(input_value) != input_keys
        or (input_value["format"], input_value["media_type"]) not in {("PNG", "image/png"), ("JPEG", "image/jpeg")}
        or input_value["frame_count"] != 1
        or isinstance(input_value["width"], bool)
        or isinstance(input_value["height"], bool)
        or not isinstance(input_value["width"], int)
        or not isinstance(input_value["height"], int)
        or not 1 <= input_value["width"] <= 8192
        or not 1 <= input_value["height"] <= 8192
        or not isinstance(input_value["mode"], str)
        or not 1 <= len(input_value["mode"]) <= 16
        or isinstance(input_value["bit_depth"], bool)
        or not isinstance(input_value["bit_depth"], int)
        or not 1 <= input_value["bit_depth"] <= 8
        or not isinstance(orientation, dict)
        or set(orientation) != {"value", "applied"}
        or isinstance(orientation["value"], bool)
        or not isinstance(orientation["value"], int)
        or not 1 <= orientation["value"] <= 8
        or not isinstance(orientation["applied"], bool)
        or value["color_policy"] not in {"embedded-icc-to-srgb", "png-srgb-declared", "untagged-assumed-srgb"}
        or value["metadata_removed"] != ["exif", "icc", "text", "comment", "dpi", "xmp"]
        or value["runtime"] != {"pillow": "12.1.0"}
        or value["gate_f_feasibility_proven"] is not False
        or not isinstance(output, dict)
        or set(output) != output_keys
        or output["sha256"] != raster.sha256
        or output["byte_length"] != raster.byte_length
        or output["mode"] != "RGBA"
        or output["bit_depth"] != 8
        or output["color_space"] != "srgb"
        or output["alpha_mode"] != "straight"
        or not isinstance(findings, list)
        or any(not isinstance(item, str) for item in findings)
        or len(findings) != len(set(findings))
        or any(item != "RASTER_UNTAGGED_ASSUMED_SRGB" for item in findings)
        or (value["color_policy"] == "untagged-assumed-srgb") != (findings == ["RASTER_UNTAGGED_ASSUMED_SRGB"])
    ):
        raise StageContractError("normalization report does not match normalized raster")
    width = output["width"]
    height = output["height"]
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or not 1 <= width <= 8192
        or not 1 <= height <= 8192
    ):
        raise StageContractError("normalization report dimensions are invalid")
    return value, width, height


class SimpleCutoutComparatorAdapter:
    adapter_id = "simple-cutout.comparator.pillow.v1"
    contract_id = "oc2d.spike.simple-cutout-comparator.v1"
    stage_type = "oc2d.spike.simple-cutout-comparator"
    implementation_version = "0.1.0"
    execution_profile = "python-pillow-12.1.0-in-process-v1"
    execution_provider = "pillow-12.1.0"
    producer_kind = ProducerKind.DETERMINISTIC
    determinism = Determinism.NUMERIC_TOLERANCE

    def execute(self, context: StageContext) -> StageOutcome:
        backend = None
        source = None
        layers: dict[str, Any] = {}
        try:
            frames = _parse_frozen_config(context.spec.config_bytes)
            raster, normalization_report = _select_inputs(context)
            report_value, width, height = _validate_normalized_report(raster, normalization_report)
            raster_data = raster.path.read_bytes()
            _verify_output_png(raster_data, (width, height))
            backend = _load_pillow()
            with backend.Image.open(io.BytesIO(raster_data), formats=("PNG",)) as verify_image:
                verify_image.verify()
            with backend.Image.open(io.BytesIO(raster_data), formats=("PNG",)) as decoded:
                decoded.load()
                if decoded.mode != "RGBA" or decoded.size != (width, height):
                    raise StageContractError("normalized raster pixels do not match the report")
                source = decoded.copy()
            patches = _build_patches(width, height)
            layers = _prepare_layers(source, patches, backend)
            outputs: list[ArtifactRef] = []
            frame_reports: list[dict[str, object]] = []
            for index, frame in enumerate(frames):
                context.cancellation.checkpoint()
                rendered = _render_frame(source, layers, patches, frame, backend, context)
                try:
                    name, artifact = _write_frame(rendered, index, frame.id, context, backend)
                finally:
                    rendered.close()
                outputs.append(artifact)
                frame_reports.append(
                    {
                        "index": index,
                        "id": frame.id,
                        "parameters": frame.parameters,
                        "artifact": {
                            "name": name,
                            "role": artifact.role,
                            "media_type": artifact.media_type,
                            "sha256": artifact.sha256,
                            "byte_length": artifact.byte_length,
                        },
                    }
                )
            context.cancellation.checkpoint()
            report_document = {
                "format": "oneclick2d.simple-cutout-comparator-report",
                "format_version": "0.1.0",
                "scope": "disposable-gate-f-spike",
                "adapter_id": self.adapter_id,
                "adapter_version": self.implementation_version,
                "contract_id": self.contract_id,
                "config_sha256": context.spec.stage.config_sha256,
                "seed_u64": context.spec.seed_u64,
                "randomness_used": False,
                "sequence_scope": "fixed-neutral-endpoint-combination-preflight",
                "input": {
                    "normalized_raster_sha256": raster.sha256,
                    "normalization_report_sha256": normalization_report.sha256,
                    "normalization_finding_codes": report_value["finding_codes"],
                    "width": width,
                    "height": height,
                    "mode": "RGBA",
                    "bit_depth": 8,
                    "color_space": "srgb",
                    "alpha_mode": "straight",
                },
                "parameters": {"order": list(PARAMETER_ORDER), "side_convention": "character-anatomical"},
                "patches": [
                    {
                        "id": patch.id,
                        "parameter_id": patch.parameter_id,
                        "character_side": patch.character_side,
                        "percent_xyxy": list(patch.percent_xyxy),
                        "pixel_box_ltrb": list(patch.box),
                        "pivot_source_pixel_x2": [patch.box[0] + patch.box[2], patch.box[1] + patch.box[3]],
                    }
                    for patch in patches
                ],
                "rendering": {
                    "coordinate_origin": "top-left",
                    "rectangle_quantization": "floor-min-ceil-max-half-open",
                    "pivot": "resolved-box-center",
                    "feather": {"source_pixels": 2, "coverage": "linear-inward-min-edge-distance"},
                    "head_transform_inheritance": "locally-active-child-patches",
                    "identity_patch_policy": "skip-inactive-local-controls",
                    "composition_order": [patch.id for patch in patches],
                    "resampling": "pillow-bilinear",
                    "rgba_filter_space": "straight-srgb-u8",
                    "alpha_composite": "porter-duff-source-over",
                    "outside_rgba": [0, 0, 0, 0],
                    "base_erased": False,
                    "png": {"compress_level": 9, "optimize": False, "metadata": "srgb-only"},
                },
                "frames": frame_reports,
                "runtime": {"pillow": backend.version},
                "finding_codes": [],
                "claims": {
                    "semantic_decomposition_performed": False,
                    "hidden_region_completion_performed": False,
                    "psd_produced": False,
                    "gate_f_feasibility_proven": False,
                },
            }
            report_artifact = context.sink.write_bytes(
                "simple-cutout-report.json",
                canonical_json_bytes(report_document),
                role="simple_cutout_comparator_report",
                media_type="application/vnd.oneclick2d.simple-cutout-comparator-report+json",
            )
            outputs.append(report_artifact)
            return StageOutcome(StageStatus.SUCCEEDED, outputs=tuple(outputs))
        except RasterBlocked as blocked:
            return StageOutcome(StageStatus.BLOCKED, reason_code=blocked.reason_code, finding_codes=blocked.finding_codes)
        except MemoryError as exc:
            raise ResourceLimitExceeded("simple-cutout memory limit exceeded") from exc
        finally:
            for layer in layers.values():
                layer.close()
            if source is not None:
                source.close()


def build_simple_cutout_registry() -> AdapterRegistry:
    registry = build_raster_registry()
    registry.register(SimpleCutoutComparatorAdapter())
    return registry
