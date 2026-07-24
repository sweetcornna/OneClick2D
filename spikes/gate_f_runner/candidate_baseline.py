"""Deterministic fixed-region candidate baseline for disposable Gate F preflights."""

from __future__ import annotations

import io
from fractions import Fraction
from typing import Any

from .contracts import (
    ArtifactRef,
    Determinism,
    ProducerKind,
    ResourceLimitExceeded,
    StageContext,
    StageContractError,
    StageOutcome,
    StageStatus,
)
from .frame_sequence import (
    ALGORITHM_ID,
    FRAME_COUNT,
    MANDATORY_FRAME_COUNT,
    PARAMETER_ORDER,
    PARAMETER_SCALE,
    PROFILE_ID,
    TRAJECTORY_FRAME_COUNT,
    GateFFrameSequenceConfig,
    build_gate_f_frame_sequence,
    parse_gate_f_frame_sequence_config,
)
from .raster import RasterBlocked, _load_pillow, _verify_output_png
from .rendering import Affine, RENDERER_CONTRACT_ID, RENDERER_PROFILE_ID, RenderLayer, render_rgba_layers, write_rgba_png
from .runner import AdapterRegistry
from .runtime import canonical_json_bytes, sha256_bytes, strict_load_json_bytes
from .simple_cutout import (
    Patch,
    _build_patches,
    _head_affine,
    _patch_affine,
    _select_inputs,
    _validate_normalized_report,
    build_simple_cutout_registry,
)

FROZEN_CANDIDATE_CONFIG_SHA256 = "88c49f7c83d896dd9c486efc1cb746bae84bf4bbbf4a08a3c4257e4fa4e3f146"
REQUIRED_SLOTS = (
    "oc2d.character",
    "oc2d.face.base",
    "oc2d.eye.left",
    "oc2d.eye.right",
    "oc2d.mouth",
    "oc2d.torso",
)


def _parse_config(data: bytes) -> GateFFrameSequenceConfig:
    value = strict_load_json_bytes(data)
    if sha256_bytes(canonical_json_bytes(value)) != FROZEN_CANDIDATE_CONFIG_SHA256:
        raise StageContractError("candidate config does not match frozen baseline profile")
    keys = {"format", "format_version", "profile_id", "required_pillow_version", "frame_sequence"}
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or value["format"] != "oneclick2d.candidate-baseline-config"
        or value["format_version"] != "0.1.0"
        or value["profile_id"] != "oc2d.spike.candidate-baseline.fixed-regions.v1"
        or value["required_pillow_version"] != "12.1.0"
    ):
        raise StageContractError("candidate config is invalid")
    return parse_gate_f_frame_sequence_config(value["frame_sequence"])


def _ensure_suitable(source: Any, patches: tuple[Patch, ...]) -> None:
    if source.width < 64 or source.height < 64:
        raise RasterBlocked("RASTER_DIMENSIONS_UNSUPPORTED")
    head = patches[0]
    alpha = source.crop(head.box).getchannel("A")
    try:
        minimum, maximum = alpha.getextrema()
    finally:
        alpha.close()
    if minimum != 255 or maximum != 255:
        raise RasterBlocked("CANDIDATE_SUITABILITY_UNSUPPORTED")


def _prepare_layers(source: Any, patches: tuple[Patch, ...], backend: Any) -> tuple[Any, dict[str, Any]]:
    head = patches[0]
    base = source.copy()
    corners = (source.getpixel((0, 0)), source.getpixel((source.width - 1, 0)), source.getpixel((0, source.height - 1)), source.getpixel((source.width - 1, source.height - 1)))
    fill = tuple(sum(pixel[channel] for pixel in corners) // 4 for channel in range(4))
    base.paste(fill, head.box)
    layers: dict[str, Any] = {}
    try:
        for patch in patches:
            layers[patch.id] = source.crop(patch.box)
        head_crop = layers["head"]
        head_left, head_top, _, _ = head.box
        for patch in patches[1:]:
            left, top, right, bottom = patch.box
            head_crop.paste((0, 0, 0, 0), (left - head_left, top - head_top, right - head_left, bottom - head_top))
    except Exception:
        base.close()
        for layer in layers.values():
            layer.close()
        raise
    return base, layers


def _candidate_render_layers(patches: tuple[Patch, ...], layers: dict[str, Any], parameters: dict[str, Fraction], width: int, height: int) -> tuple[RenderLayer, ...]:
    head = _head_affine(parameters, width, height, patches[0].pivot[1])
    return tuple(RenderLayer(layers[patch.id], patch.box, _patch_affine(patch, parameters, head)) for patch in patches)


def _quad_record(patch: Patch) -> dict[str, object]:
    left, top, right, bottom = patch.box
    return {
        "id": f"mesh.{patch.id}",
        "layer_id": f"layer.{patch.id}",
        "vertices_xy": [[left, top], [right, top], [right, bottom], [left, bottom]],
        "triangle_indices": [0, 1, 2, 0, 2, 3],
        "winding": "positive-screen-y-down",
    }


def _signed_area2(points: tuple[tuple[Fraction, Fraction], ...]) -> Fraction:
    return sum(left[0] * right[1] - right[0] * left[1] for left, right in zip(points, points[1:] + points[:1]))


def _validate_geometry(patches: tuple[Patch, ...], sequence: Any, width: int, height: int) -> dict[str, object]:
    minimum_area2: Fraction | None = None
    samples = 0
    for frame in sequence.frames:
        parameters = frame.parameter_fractions()
        head = _head_affine(parameters, width, height, patches[0].pivot[1])
        for patch in patches:
            transform = _patch_affine(patch, parameters, head)
            left, top, right, bottom = patch.box
            points = tuple(transform.map_point(Fraction(x), Fraction(y)) for x, y in ((left, top), (right, top), (right, bottom), (left, bottom)))
            area2 = _signed_area2(points)
            if area2 <= 0:
                raise StageContractError("candidate geometry folded over")
            minimum_area2 = area2 if minimum_area2 is None else min(minimum_area2, area2)
            samples += 1
    if minimum_area2 is None:
        raise StageContractError("candidate geometry validation had no samples")
    return {"all_finite": True, "valid_indices": True, "positive_area_all_frames": True, "sample_count": samples, "minimum_signed_area2": float(minimum_area2)}


class CandidateBaselineAdapter:
    adapter_id = "candidate.baseline.pillow.v1"
    contract_id = "oc2d.spike.candidate-baseline.v1"
    stage_type = "oc2d.spike.candidate-baseline"
    implementation_version = "0.1.0"
    execution_profile = "python-pillow-12.1.0-in-process-v1"
    execution_provider = "pillow-12.1.0"
    producer_kind = ProducerKind.DETERMINISTIC
    determinism = Determinism.NUMERIC_TOLERANCE

    def execute(self, context: StageContext) -> StageOutcome:
        backend = None
        source = base = None
        layers: dict[str, Any] = {}
        try:
            sequence_config = _parse_config(context.spec.config_bytes)
            sequence = build_gate_f_frame_sequence(sequence_config)
            raster, normalization_report = _select_inputs(context)
            normalization_value, width, height = _validate_normalized_report(raster, normalization_report)
            raster_data = raster.path.read_bytes()
            _verify_output_png(raster_data, (width, height))
            backend = _load_pillow()
            with backend.Image.open(io.BytesIO(raster_data), formats=("PNG",)) as decoded:
                decoded.load()
                if decoded.mode != "RGBA" or decoded.size != (width, height):
                    raise StageContractError("normalized raster pixels do not match the report")
                source = decoded.copy()
            patches = _build_patches(width, height)
            _ensure_suitable(source, patches)
            base, layers = _prepare_layers(source, patches, backend)
            validation = _validate_geometry(patches, sequence, width, height)
            outputs: list[ArtifactRef] = []
            layer_reports: list[dict[str, object]] = []
            base_artifact = write_rgba_png(base, "layer.torso-base.png", "candidate_layer", context, backend)
            outputs.append(base_artifact)
            layer_reports.append({"id": "layer.torso-base", "slot_id": "oc2d.torso", "side": "not-applicable", "box_ltrb": [0, 0, width, height], "generated_fill": True, "artifact": {"name": base_artifact.path.name, "sha256": base_artifact.sha256, "byte_length": base_artifact.byte_length}})
            slot_by_patch = {"head": "oc2d.face.base", "eye.screen-left": "oc2d.eye.right", "eye.screen-right": "oc2d.eye.left", "mouth": "oc2d.mouth"}
            for patch in patches:
                artifact = write_rgba_png(layers[patch.id], f"layer.{patch.id}.png", "candidate_layer", context, backend)
                outputs.append(artifact)
                layer_reports.append({"id": f"layer.{patch.id}", "slot_id": slot_by_patch[patch.id], "side": patch.character_side, "box_ltrb": list(patch.box), "generated_fill": False, "artifact": {"name": artifact.path.name, "sha256": artifact.sha256, "byte_length": artifact.byte_length}})
            frame_reports: list[dict[str, object]] = []
            for index, frame in enumerate(sequence.frames):
                context.cancellation.checkpoint()
                parameters = frame.parameter_fractions()
                rendered = render_rgba_layers(base, _candidate_render_layers(patches, layers, parameters, width, height), backend, context)
                try:
                    name = f"candidate.{index:03d}.{frame.id}.png"
                    artifact = write_rgba_png(rendered, name, "candidate_frame", context, backend)
                finally:
                    rendered.close()
                outputs.append(artifact)
                frame_reports.append({"index": index, "id": frame.id, "source": frame.source, "parameters": frame.parameter_document(), "artifact": {"name": name, "sha256": artifact.sha256, "byte_length": artifact.byte_length}})
            report = {
                "format": "oneclick2d.candidate-baseline-report",
                "format_version": "0.1.0",
                "scope": "disposable-gate-f-spike",
                "adapter_id": self.adapter_id,
                "adapter_version": self.implementation_version,
                "contract_id": self.contract_id,
                "input": {"normalized_raster_sha256": raster.sha256, "normalization_report_sha256": normalization_report.sha256, "width": width, "height": height, "normalization_finding_codes": normalization_value["finding_codes"]},
                "suitability": {"outcome": "accept", "policy": "fixed-regions-min64-opaque-head.v1"},
                "ontology": [{"slot_id": slot, "status": "PRESENT"} for slot in REQUIRED_SLOTS],
                "layers": layer_reports,
                "geometry": [_quad_record(patch) for patch in patches],
                "parameters": [
                    {"id": parameter_id, "range": ranges, "interpolation": "linear", "extrapolation": "clamp"}
                    for parameter_id, ranges in zip(PARAMETER_ORDER, ([-15, 15], [-10, 10], [0, 1], [0, 1], [0, 1]), strict=True)
                ],
                "sequence": {"profile_id": PROFILE_ID, "algorithm_id": ALGORITHM_ID, "config_sha256": sequence_config.canonical_sha256, "seed_u64": sequence.seed_u64, "sha256": sequence.sha256, "parameter_scale": PARAMETER_SCALE, "mandatory_frame_count": MANDATORY_FRAME_COUNT, "trajectory_frame_count": TRAJECTORY_FRAME_COUNT, "frame_count": FRAME_COUNT},
                "rendering": {"contract_id": RENDERER_CONTRACT_ID, "profile_id": RENDERER_PROFILE_ID, "canvas": [width, height], "color_space": "srgb", "alpha_mode": "straight"},
                "frames": frame_reports,
                "validation": {**validation, "source_pixels_modified_outside_generated_region": 0, "required_slot_presence": "6/6"},
                "claims": {"model_used": False, "semantic_correctness_on_real_art_proven": False, "psd_produced": False, "gate_f_feasibility_proven": False},
            }
            report_artifact = context.sink.write_bytes("candidate-report.json", canonical_json_bytes(report), role="candidate_baseline_report", media_type="application/vnd.oneclick2d.candidate-baseline-report+json")
            outputs.append(report_artifact)
            return StageOutcome(StageStatus.SUCCEEDED, outputs=tuple(outputs))
        except RasterBlocked as blocked:
            return StageOutcome(StageStatus.BLOCKED, reason_code=blocked.reason_code, finding_codes=blocked.finding_codes)
        except MemoryError as exc:
            raise ResourceLimitExceeded("candidate baseline memory limit exceeded") from exc
        finally:
            for layer in layers.values():
                layer.close()
            if base is not None:
                base.close()
            if source is not None:
                source.close()


def build_gate_f_registry() -> AdapterRegistry:
    registry = build_simple_cutout_registry()
    registry.register(CandidateBaselineAdapter())
    return registry
