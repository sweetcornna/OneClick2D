"""Deterministic quad/affine motion draft for validated model layers."""

from __future__ import annotations

import io
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import StageContractError
from .frame_sequence import (
    ALGORITHM_ID as SEQUENCE_ALGORITHM_ID,
    FRAME_COUNT,
    MANDATORY_FRAME_COUNT,
    PARAMETER_ORDER,
    PROFILE_ID as SEQUENCE_PROFILE_ID,
    TRAJECTORY_FRAME_COUNT,
    build_gate_f_frame_sequence,
    parse_gate_f_frame_sequence_config,
)
from .model_worker import MODEL_PART_NAMES, PROFILE_ID as MODEL_PROFILE_ID
from .raster import _load_pillow, _verify_output_png
from .rendering import (
    Affine,
    RENDERER_CONTRACT_ID,
    RENDERER_PROFILE_ID,
    RenderLayer,
    render_rgba_layers,
)
from .runtime import (
    SHA256_RE,
    canonical_json_bytes,
    contained_run_path,
    read_bounded_file,
    sha256_bytes,
    sha256_file,
    strict_load_json_bytes,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "examples" / "gate-f-model-motion-draft" / "config.json"
CONFIG_SHA256 = "b9fea23f0f78cad83a5a87ae453ef957107bb065cf482cde85e531781d0e1db9"
PROFILE_ID = "oc2d.spike.model-motion-draft.affine-semantic.v14"
ALGORITHM_ID = "source-visible-features-feathered-underpaint-grouped-eye-subject-matte-hard-edge-padded-quad-affine-premultiplied.v13"
REPORT_NAME = "motion-report.json"
OUTPUT_DIRECTORY = "motion-draft"
CANVAS_SIZE = 1280
PART_PADDING = 4
SUBJECT_MATTE_EROSION_SIZE = 5
SUBJECT_MATTE_ALPHA_THRESHOLD = 248
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 512 * 1024 * 1024

DRAW_ORDER = (
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
STATIC_SEMANTICS = {
    "tail",
    "wings",
    "neck",
    "neckwear",
    "topwear",
    "handwear",
    "bottomwear",
    "legwear",
    "footwear",
    "objects",
}
EYE_SEMANTICS = {"eyewhite", "irides", "eyelash"}
NEUTRAL_PARAMETERS = {
    "head.yaw": Fraction(0),
    "head.pitch": Fraction(0),
    "eye.left.open": Fraction(1),
    "eye.right.open": Fraction(1),
    "mouth.open": Fraction(0),
}


@dataclass
class MotionPart:
    id: str
    semantic: str
    side: str
    source_artifact_id: str
    box: tuple[int, int, int, int]
    draw_order: int
    motion_group: str
    image: Any


@dataclass
class MotionRecomputation:
    layers: list[dict[str, object]]
    layer_bytes: tuple[bytes, ...]
    geometry: list[dict[str, object]]
    parameters: list[dict[str, object]]
    bindings: list[dict[str, object]]
    sequence: dict[str, object]
    frames: list[dict[str, object]]
    frame_bytes: tuple[bytes, ...]
    validation: dict[str, object]
    underpaint_mask: Any

    def close(self) -> None:
        self.underpaint_mask.close()


def _config() -> tuple[dict[str, object], Any, Any]:
    value = strict_load_json_bytes(read_bounded_file(CONFIG_PATH, 64 * 1024))
    if not isinstance(value, dict) or sha256_bytes(canonical_json_bytes(value)) != CONFIG_SHA256:
        raise StageContractError("model motion draft config does not match the frozen profile")
    keys = {
        "format",
        "format_version",
        "profile_id",
        "required_model_profile_id",
        "required_pillow_version",
        "frame_sequence",
    }
    if (
        set(value) != keys
        or value.get("format") != "oneclick2d.model-motion-draft-config"
        or value.get("format_version") != "0.2.0"
        or value.get("profile_id") != PROFILE_ID
        or value.get("required_model_profile_id") != MODEL_PROFILE_ID
        or value.get("required_pillow_version") != "12.1.0"
    ):
        raise StageContractError("model motion draft config is invalid")
    sequence_config = parse_gate_f_frame_sequence_config(value["frame_sequence"])
    return value, sequence_config, build_gate_f_frame_sequence(sequence_config)


def _safe_identifier(value: str) -> str:
    return value.replace(" ", "-")


def _contained_run_file(run_dir: Path, relative: str) -> Path:
    try:
        return contained_run_path(run_dir.parent, run_dir.name, relative, kind="file")
    except ValueError as exc:
        raise StageContractError("model motion draft artifact path is invalid") from exc


def _contained_run_directory(run_dir: Path) -> Path:
    try:
        return contained_run_path(run_dir.parent, run_dir.name, kind="directory")
    except ValueError as exc:
        raise StageContractError("model motion draft run directory is invalid") from exc


def _rgba_difference_mask(left: Any, right: Any) -> Any:
    from PIL import ImageChops

    if left.mode != right.mode or left.mode != "RGBA" or left.size != right.size:
        raise StageContractError("model motion draft difference inputs do not match")
    difference = ImageChops.difference(left, right)
    channels = difference.split()
    maximum = channels[0].copy()
    try:
        for channel in channels[1:]:
            merged = ImageChops.lighter(maximum, channel)
            maximum.close()
            maximum = merged
        return maximum.point(lambda value: 255 if value > 0 else 0)
    finally:
        maximum.close()
        for channel in channels:
            channel.close()
        difference.close()


def _underpaint_face_with_mask(
    image: Any,
    backend: Any,
    feature_boxes: tuple[tuple[int, int, int, int], ...] = (),
) -> tuple[Any, Any]:
    from PIL import ImageChops, ImageDraw, ImageFilter, ImageStat

    alpha = image.getchannel("A")
    binary = alpha.point(lambda value: 255 if value > 15 else 0)
    flooded = binary.copy()
    try:
        ImageDraw.floodfill(flooded, (0, 0), 128, thresh=0)
        holes = flooded.point(lambda value: 255 if value == 0 else 0)
        try:
            expanded_holes = holes.filter(ImageFilter.MaxFilter(5))
        finally:
            holes.close()
    finally:
        flooded.close()
        binary.close()
    feature_mask = backend.Image.new("L", image.size, 0)
    feature_draw = ImageDraw.Draw(feature_mask)
    for left, top, right, bottom in feature_boxes:
        feature_draw.rectangle(
            (max(0, left - PART_PADDING), max(0, top - PART_PADDING), min(image.width, right + PART_PADDING), min(image.height, bottom + PART_PADDING)),
            fill=255,
        )
    targeted = feature_mask.filter(ImageFilter.GaussianBlur(4))
    underpaint_mask = ImageChops.lighter(expanded_holes, targeted)
    feature_mask.close()
    targeted.close()
    expanded_holes.close()
    if underpaint_mask.getbbox() is None:
        underpaint_mask.close()
        alpha.close()
        return image.copy(), backend.Image.new("L", image.size, 0)
    opaque = alpha.point(lambda value: 255 if value >= 240 else 0)
    rgb = image.convert("RGB")
    try:
        means = ImageStat.Stat(rgb, mask=opaque).mean
        if len(means) != 3:
            raise StageContractError("model motion draft face underpaint is unavailable")
        color = tuple(max(0, min(255, round(value))) for value in means) + (255,)
        fill = backend.Image.new("RGBA", image.size, color)
        result = image.copy()
        try:
            result.paste(fill, (0, 0), underpaint_mask)
            operation_mask = _rgba_difference_mask(result, image)
            return result, operation_mask
        except Exception:
            result.close()
            raise
        finally:
            fill.close()
    finally:
        underpaint_mask.close()
        opaque.close()
        rgb.close()
        alpha.close()


def _underpaint_face(image: Any, backend: Any, feature_boxes: tuple[tuple[int, int, int, int], ...] = ()) -> Any:
    result, operation_mask = _underpaint_face_with_mask(image, backend, feature_boxes)
    operation_mask.close()
    return result


def _tighten_alpha(image: Any, *, feature: bool) -> Any:
    low, high = (28, 220) if feature else (8, 247)
    alpha = image.getchannel("A")
    adjusted = alpha.point(
        lambda value: 0 if value <= low else 255 if value >= high else round((value - low) * 255 / (high - low))
    )
    result = image.copy()
    try:
        result.putalpha(adjusted)
        return result
    except Exception:
        result.close()
        raise
    finally:
        adjusted.close()
        alpha.close()


def _subject_matte(image: Any) -> Any:
    from PIL import ImageDraw, ImageFilter

    alpha = image.getchannel("A")
    connected = alpha.point(lambda value: 255 if value >= SUBJECT_MATTE_ALPHA_THRESHOLD else 0)
    try:
        border = (
            *((x, 0) for x in range(image.width)),
            *((x, image.height - 1) for x in range(image.width)),
            *((0, y) for y in range(1, image.height - 1)),
            *((image.width - 1, y) for y in range(1, image.height - 1)),
        )
        for seed in border:
            if connected.getpixel(seed) == 0:
                ImageDraw.floodfill(connected, seed, 128, thresh=0)
        exterior = connected.point(lambda value: 255 if value == 128 else 0)
        try:
            expanded_exterior = exterior.filter(ImageFilter.MaxFilter(SUBJECT_MATTE_EROSION_SIZE))
        finally:
            exterior.close()
    finally:
        connected.close()
        alpha.close()
    try:
        return expanded_exterior.point(lambda value: 0 if value else 255)
    finally:
        expanded_exterior.close()


def _apply_subject_matte(image: Any, matte: Any) -> Any:
    from PIL import ImageChops

    if image.size != matte.size:
        raise StageContractError("model motion draft subject matte canvas does not match")
    alpha = image.getchannel("A")
    adjusted = ImageChops.darker(alpha, matte)
    result = image.copy()
    try:
        result.putalpha(adjusted)
        return result
    except Exception:
        result.close()
        raise
    finally:
        adjusted.close()
        alpha.close()


def _source_feature_layer(source: Any, semantic_layer: Any) -> Any:
    from PIL import ImageChops

    luminance = source.convert("L")
    semantic_alpha = semantic_layer.getchannel("A")
    try:
        detail = luminance.point(
            lambda value: 255 if value <= 145 else 0 if value >= 210 else round((210 - value) * 255 / 65)
        )
        try:
            feature_alpha = ImageChops.multiply(detail, semantic_alpha)
        finally:
            detail.close()
    finally:
        luminance.close()
        semantic_alpha.close()
    result = source.copy()
    try:
        result.putalpha(feature_alpha)
        return result
    except Exception:
        result.close()
        raise
    finally:
        feature_alpha.close()


def _feature_boxes(by_name: dict[str, dict[str, object]], run_dir: Path, backend: Any) -> tuple[tuple[int, int, int, int], ...]:
    boxes: list[tuple[int, int, int, int]] = []
    for semantic in sorted(EYE_SEMANTICS | {"eyebrow", "nose", "mouth"}):
        artifact = by_name[semantic]["artifact"]
        if not isinstance(artifact, dict) or not isinstance(artifact.get("uri"), str):
            raise StageContractError("model motion draft feature artifact is invalid")
        path = _contained_run_file(run_dir, str(artifact["uri"]))
        with backend.Image.open(path, formats=("PNG",)) as source:
            source.load()
            rgba = source.convert("RGBA")
        try:
            alpha = rgba.getchannel("A")
        finally:
            rgba.close()
        try:
            visible = alpha.point(lambda value: 255 if value > 15 else 0)
            try:
                box = visible.getbbox()
            finally:
                visible.close()
        finally:
            alpha.close()
        if box is not None:
            boxes.append(box)
    return tuple(boxes)


def _crop_part(
    image: Any,
    semantic: str,
    artifact_id: str,
    draw_order: int,
    *,
    side: str = "not-applicable",
    x_range: tuple[int, int] | None = None,
) -> MotionPart | None:
    source_alpha = image.getchannel("A")
    alpha = source_alpha.point(lambda value: 255 if value > 15 else 0)
    source_alpha.close()
    try:
        if x_range is None:
            box = alpha.getbbox()
        else:
            left, right = x_range
            local = alpha.crop((left, 0, right, image.height))
            try:
                local_box = local.getbbox()
            finally:
                local.close()
            box = None if local_box is None else (
                local_box[0] + left,
                local_box[1],
                local_box[2] + left,
                local_box[3],
            )
    finally:
        alpha.close()
    if box is None:
        return None
    horizontal_minimum, horizontal_maximum = x_range or (0, image.width)
    box = (
        max(horizontal_minimum, box[0] - PART_PADDING),
        max(0, box[1] - PART_PADDING),
        min(horizontal_maximum, box[2] + PART_PADDING),
        min(image.height, box[3] + PART_PADDING),
    )
    crop = image.crop(box)
    try:
        tightened = _tighten_alpha(crop, feature=semantic in {"eye", "eyebrow", "nose", "mouth"})
    finally:
        crop.close()
    if x_range is not None:
        side_suffix = f".character-{side}"
        group = "eye"
    else:
        side_suffix = ""
        group = "static" if semantic in STATIC_SEMANTICS else "mouth" if semantic == "mouth" else "head"
    return MotionPart(
        id=f"layer.{_safe_identifier(semantic)}{side_suffix}",
        semantic=semantic,
        side=side,
        source_artifact_id=artifact_id,
        box=box,
        draw_order=draw_order,
        motion_group=group,
        image=tightened,
    )


def _prepare_parts(
    model_report: dict[str, object],
    backend: Any,
    *,
    evidence: dict[str, Any] | None = None,
) -> list[MotionPart]:
    from PIL import ImageChops

    model = model_report.get("model")
    layers = model.get("layers") if isinstance(model, dict) else None
    if not isinstance(layers, list):
        raise StageContractError("model motion draft semantic layers are unavailable")
    by_name: dict[str, dict[str, object]] = {}
    for item in layers:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("artifact"), dict):
            by_name[str(item["name"])] = item
    if not set(MODEL_PART_NAMES).issubset(by_name):
        raise StageContractError("model motion draft semantic inventory is incomplete")

    source_descriptor = model.get("source")
    if not isinstance(source_descriptor, dict) or not isinstance(source_descriptor.get("uri"), str):
        raise StageContractError("model motion draft source image is unavailable")
    run_dir = Path(str(model_report["_run_dir"]))
    source_path = _contained_run_file(run_dir, str(source_descriptor["uri"]))
    with backend.Image.open(source_path, formats=("PNG",)) as source:
        source.load()
        source_reference = source.convert("RGBA")
    if source_reference.size != (CANVAS_SIZE, CANVAS_SIZE):
        source_reference.close()
        raise StageContractError("model motion draft source canvas is invalid")
    feature_boxes = _feature_boxes(by_name, run_dir, backend)

    reconstruction_descriptor = model.get("reconstruction")
    if not isinstance(reconstruction_descriptor, dict) or not isinstance(reconstruction_descriptor.get("uri"), str):
        source_reference.close()
        raise StageContractError("model motion draft reconstruction image is unavailable")
    reconstruction_path = _contained_run_file(run_dir, str(reconstruction_descriptor["uri"]))
    with backend.Image.open(reconstruction_path, formats=("PNG",)) as reconstruction_source:
        reconstruction_source.load()
        reconstruction_reference = reconstruction_source.convert("RGBA")
    if reconstruction_reference.size != (CANVAS_SIZE, CANVAS_SIZE):
        reconstruction_reference.close()
        source_reference.close()
        raise StageContractError("model motion draft reconstruction canvas is invalid")
    subject_matte = _subject_matte(reconstruction_reference)
    reconstruction_reference.close()

    parts: list[MotionPart] = []
    eye_canvases = {
        "right": backend.Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0)),
        "left": backend.Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0)),
    }
    eye_source_ids: list[str] = []
    try:
        for draw_order, semantic in enumerate(DRAW_ORDER):
            artifact = by_name[semantic]["artifact"]
            if not isinstance(artifact, dict) or not isinstance(artifact.get("uri"), str) or not isinstance(artifact.get("id"), str):
                raise StageContractError("model motion draft source artifact is invalid")
            path = _contained_run_file(Path(str(model_report["_run_dir"])), str(artifact["uri"]))
            with backend.Image.open(path, formats=("PNG",)) as source:
                source.load()
                image = source.convert("RGBA")
            underpaint_operation = None
            try:
                if image.size != (CANVAS_SIZE, CANVAS_SIZE):
                    raise StageContractError("model motion draft source canvas is invalid")
                if semantic == "face":
                    if evidence is None:
                        render_source = _underpaint_face(image, backend, feature_boxes)
                    else:
                        render_source, underpaint_operation = _underpaint_face_with_mask(image, backend, feature_boxes)
                elif semantic in {"eyebrow", "nose", "mouth"}:
                    render_source = _source_feature_layer(source_reference, image)
                else:
                    render_source = image
                try:
                    matted_source = _apply_subject_matte(render_source, subject_matte)
                    try:
                        if semantic in EYE_SEMANTICS:
                            candidates = [
                                _crop_part(matted_source, semantic, str(artifact["id"]), draw_order, side="right", x_range=(0, CANVAS_SIZE // 2)),
                                _crop_part(matted_source, semantic, str(artifact["id"]), draw_order, side="left", x_range=(CANVAS_SIZE // 2, CANVAS_SIZE)),
                            ]
                            eye_source_ids.append(str(artifact["id"]))
                            for candidate in candidates:
                                if candidate is None:
                                    continue
                                try:
                                    eye_canvases[candidate.side].alpha_composite(candidate.image, dest=candidate.box[:2])
                                finally:
                                    candidate.image.close()
                        else:
                            candidates = (_crop_part(matted_source, semantic, str(artifact["id"]), draw_order),)
                            prepared = [part for part in candidates if part is not None]
                            parts.extend(prepared)
                            if semantic == "face" and evidence is not None and prepared:
                                baseline_matted = _apply_subject_matte(image, subject_matte)
                                try:
                                    face_part = prepared[0]
                                    baseline_crop = baseline_matted.crop(face_part.box)
                                    try:
                                        baseline_part = _tighten_alpha(baseline_crop, feature=False)
                                    finally:
                                        baseline_crop.close()
                                finally:
                                    baseline_matted.close()
                                try:
                                    affected = _rgba_difference_mask(face_part.image, baseline_part)
                                finally:
                                    baseline_part.close()
                                try:
                                    operation_crop = underpaint_operation.crop(face_part.box)
                                    try:
                                        unexplained = ImageChops.subtract(affected, operation_crop)
                                        try:
                                            if unexplained.getbbox() is not None:
                                                raise StageContractError("model motion draft underpaint evidence does not match")
                                        finally:
                                            unexplained.close()
                                    finally:
                                        operation_crop.close()
                                    canvas_mask = backend.Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), 0)
                                    canvas_mask.paste(affected, face_part.box[:2])
                                    previous = evidence.get("underpaint_mask")
                                    if previous is not None:
                                        previous.close()
                                    evidence["underpaint_mask"] = canvas_mask
                                finally:
                                    affected.close()
                    finally:
                        matted_source.close()
                finally:
                    if render_source is not image:
                        render_source.close()
            finally:
                if underpaint_operation is not None:
                    underpaint_operation.close()
                image.close()
        eye_draw_order = DRAW_ORDER.index("eyelash")
        eye_source_id = "+".join(eye_source_ids)
        for side, x_range in (("right", (0, CANVAS_SIZE // 2)), ("left", (CANVAS_SIZE // 2, CANVAS_SIZE))):
            eye_texture = source_reference.copy()
            eye_alpha = eye_canvases[side].getchannel("A")
            try:
                eye_texture.putalpha(eye_alpha)
                part = _crop_part(eye_texture, "eye", eye_source_id, eye_draw_order, side=side, x_range=x_range)
                if part is not None:
                    parts.append(part)
            finally:
                eye_alpha.close()
                eye_texture.close()
    except Exception:
        for part in parts:
            part.image.close()
        raise
    finally:
        for canvas in eye_canvases.values():
            canvas.close()
        subject_matte.close()
        source_reference.close()
    if not any(part.motion_group == "eye" and part.side == "left" for part in parts) or not any(
        part.motion_group == "eye" and part.side == "right" for part in parts
    ) or not any(part.motion_group == "mouth" for part in parts):
        for part in parts:
            part.image.close()
        raise StageContractError("model motion draft mandatory feature layers are empty")
    return parts


def _head_pivot_y(parts: list[MotionPart]) -> Fraction:
    moving = [part.box for part in parts if part.motion_group != "static"]
    if not moving:
        raise StageContractError("model motion draft has no moving geometry")
    return Fraction(min(box[1] for box in moving) + max(box[3] for box in moving), 2)


def _head_affine(parameters: dict[str, Fraction], pivot_y: Fraction) -> Affine:
    yaw = Fraction(parameters["head.yaw"], 15)
    pitch = Fraction(parameters["head.pitch"], 10)
    shear = Fraction(3, 100) * yaw
    translate_x = Fraction(CANVAS_SIZE, 50) * yaw
    translate_y = -Fraction(CANVAS_SIZE, 64) * pitch
    return Affine(Fraction(1), shear, translate_x - shear * pivot_y, Fraction(0), Fraction(1), translate_y)


def _scale_y(scale: Fraction, pivot_y: Fraction) -> Affine:
    return Affine(Fraction(1), Fraction(0), Fraction(0), Fraction(0), scale, (Fraction(1) - scale) * pivot_y)


def _part_affine(part: MotionPart, parameters: dict[str, Fraction], head: Affine) -> Affine:
    if part.motion_group == "static":
        return Affine.identity()
    if part.motion_group == "head":
        return head
    pivot_y = Fraction(part.box[1] + part.box[3], 2)
    if part.motion_group == "eye":
        openness = parameters[f"eye.{part.side}.open"]
        local = _scale_y(Fraction(1, 20) + Fraction(19, 20) * openness, pivot_y)
    elif part.motion_group == "mouth":
        local = _scale_y(Fraction(1) + Fraction(1, 2) * parameters["mouth.open"], pivot_y)
    else:
        raise StageContractError("model motion draft motion group is invalid")
    return head.compose(local)


def _render(parts: list[MotionPart], parameters: dict[str, Fraction], backend: Any) -> Any:
    base = backend.Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    pivot_y = _head_pivot_y(parts)
    head = _head_affine(parameters, pivot_y)
    layers = tuple(
        RenderLayer(part.image, part.box, _part_affine(part, parameters, head))
        for part in sorted(parts, key=lambda item: (item.draw_order, item.id))
    )
    try:
        return render_rgba_layers(base, layers, backend, None, profile_id=RENDERER_PROFILE_ID)
    finally:
        base.close()


def _is_neutral_frame(frame: Any) -> bool:
    return frame.parameter_fractions() == NEUTRAL_PARAMETERS


def _geometry_record(part: MotionPart) -> dict[str, object]:
    left, top, right, bottom = part.box
    return {
        "id": f"mesh.{part.id.removeprefix('layer.')}",
        "layer_id": part.id,
        "vertices_xy": [[left, top], [right, top], [right, bottom], [left, bottom]],
        "triangle_indices": [0, 1, 2, 0, 2, 3],
        "winding": "positive-screen-y-down",
    }


def _layer_record(part: MotionPart, artifact: dict[str, object]) -> dict[str, object]:
    return {
        "id": part.id,
        "semantic": part.semantic,
        "side": part.side,
        "source_artifact_id": part.source_artifact_id,
        "box_ltrb": list(part.box),
        "draw_order": part.draw_order,
        "motion_group": part.motion_group,
        "artifact": artifact,
    }


def _parameter_records() -> list[dict[str, object]]:
    ranges = ((-15, 15, 0), (-10, 10, 0), (0, 1, 1), (0, 1, 1), (0, 1, 0))
    return [
        {
            "id": parameter_id,
            "range": [minimum, maximum],
            "default": default,
            "interpolation": "linear",
            "extrapolation": "clamp",
        }
        for parameter_id, (minimum, maximum, default) in zip(PARAMETER_ORDER, ranges, strict=True)
    ]


def _binding_records(layer_records: list[dict[str, object]]) -> list[dict[str, object]]:
    head_targets = [str(item["id"]) for item in layer_records if item["motion_group"] != "static"]
    left_targets = [str(item["id"]) for item in layer_records if item["motion_group"] == "eye" and item["side"] == "left"]
    right_targets = [str(item["id"]) for item in layer_records if item["motion_group"] == "eye" and item["side"] == "right"]
    mouth_targets = [str(item["id"]) for item in layer_records if item["motion_group"] == "mouth"]
    return [
        {"id": "binding.head-yaw", "parameter_id": "head.yaw", "target_layer_ids": head_targets, "kind": "affine-shear-translate", "interpolation": "linear", "extrapolation": "clamp"},
        {"id": "binding.head-pitch", "parameter_id": "head.pitch", "target_layer_ids": head_targets, "kind": "affine-translate", "interpolation": "linear", "extrapolation": "clamp"},
        {"id": "binding.eye-left-open", "parameter_id": "eye.left.open", "target_layer_ids": left_targets, "kind": "affine-scale-y", "interpolation": "linear", "extrapolation": "clamp"},
        {"id": "binding.eye-right-open", "parameter_id": "eye.right.open", "target_layer_ids": right_targets, "kind": "affine-scale-y", "interpolation": "linear", "extrapolation": "clamp"},
        {"id": "binding.mouth-open", "parameter_id": "mouth.open", "target_layer_ids": mouth_targets, "kind": "affine-scale-y", "interpolation": "linear", "extrapolation": "clamp"},
    ]


def _signed_area2(points: tuple[tuple[Fraction, Fraction], ...]) -> Fraction:
    return sum(left[0] * right[1] - right[0] * left[1] for left, right in zip(points, points[1:] + points[:1]))


def _geometry_validation(parts: list[MotionPart], sequence: Any) -> dict[str, object]:
    minimum: Fraction | None = None
    samples = 0
    pivot_y = _head_pivot_y(parts)
    for frame in sequence.frames:
        parameters = frame.parameter_fractions()
        head = _head_affine(parameters, pivot_y)
        for part in parts:
            transform = _part_affine(part, parameters, head)
            if not all(math.isfinite(value) for value in transform.inverse_tuple()):
                raise StageContractError("model motion draft transform is not finite")
            left, top, right, bottom = part.box
            points = tuple(transform.map_point(Fraction(x), Fraction(y)) for x, y in ((left, top), (right, top), (right, bottom), (left, bottom)))
            area2 = _signed_area2(points)
            if area2 <= 0:
                raise StageContractError("model motion draft geometry folded over")
            minimum = area2 if minimum is None else min(minimum, area2)
            samples += 1
    if minimum is None:
        raise StageContractError("model motion draft geometry validation had no samples")
    return {
        "all_finite": True,
        "valid_indices": True,
        "positive_area_all_frames": True,
        "sample_count": samples,
        "minimum_signed_area2": float(minimum),
    }


def _write_png(image: Any, path: Path, backend: Any) -> dict[str, object]:
    pnginfo = backend.PngImagePlugin.PngInfo()
    pnginfo.add(b"sRGB", b"\x00")
    image.save(path, format="PNG", optimize=False, compress_level=9, pnginfo=pnginfo, icc_profile=None, exif=b"")
    data = read_bounded_file(path, MAX_FRAME_BYTES)
    _verify_output_png(data, image.size)
    return {
        "byte_length": len(data),
        "sha256": sha256_bytes(data),
        "width": image.width,
        "height": image.height,
        "mode": "RGBA",
    }


def _png_bytes(image: Any, backend: Any) -> bytes:
    stream = io.BytesIO()
    pnginfo = backend.PngImagePlugin.PngInfo()
    pnginfo.add(b"sRGB", b"\x00")
    image.save(stream, format="PNG", optimize=False, compress_level=9, pnginfo=pnginfo, icc_profile=None, exif=b"")
    data = stream.getvalue()
    _verify_output_png(data, image.size)
    return data


def _rgb_mismatch_mask(difference: Any) -> Any:
    from PIL import ImageChops

    channels = difference.split()
    red_green = ImageChops.lighter(channels[0], channels[1])
    try:
        return ImageChops.lighter(red_green, channels[2])
    finally:
        red_green.close()
        for channel in channels:
            channel.close()


def _neutral_comparison_images(expected: Any, actual: Any) -> dict[str, object]:
    from PIL import ImageChops, ImageStat

    if expected.size != actual.size or expected.size != (CANVAS_SIZE, CANVAS_SIZE):
        raise StageContractError("model motion draft neutral comparison canvas is invalid")
    visible = expected.getchannel("A").point(lambda value: 255 if value > 15 else 0)
    expected_rgb = expected.convert("RGB")
    actual_rgb = actual.convert("RGB")
    difference = ImageChops.difference(expected_rgb, actual_rgb)
    mismatch_mask = _rgb_mismatch_mask(difference)
    try:
        visible_pixels = visible.histogram()[255]
        if visible_pixels <= 0:
            raise StageContractError("model motion draft neutral comparison has no visible pixels")
        exact_pixels = mismatch_mask.histogram(mask=visible)[0]
        channel_mae = ImageStat.Stat(difference, mask=visible).mean
    finally:
        mismatch_mask.close()
        visible.close()
        difference.close()
        expected_rgb.close()
        actual_rgb.close()
    return {
        "neutral_visible_pixel_count": visible_pixels,
        "neutral_reconstruction_rgb_exact_ratio": round(exact_pixels / visible_pixels, 6),
        "neutral_reconstruction_rgb_mae": round(sum(channel_mae) / len(channel_mae), 6),
    }


def _neutral_comparison(reconstruction_path: Path, neutral_path: Path) -> dict[str, object]:
    from PIL import Image

    with Image.open(reconstruction_path, formats=("PNG",)) as expected_image, Image.open(neutral_path, formats=("PNG",)) as actual_image:
        expected = expected_image.convert("RGBA")
        actual = actual_image.convert("RGBA")
    try:
        return _neutral_comparison_images(expected, actual)
    finally:
        expected.close()
        actual.close()


def _sequence_record(sequence_config: Any, sequence: Any) -> dict[str, object]:
    return {
        "profile_id": SEQUENCE_PROFILE_ID,
        "algorithm_id": SEQUENCE_ALGORITHM_ID,
        "config_sha256": sequence_config.canonical_sha256,
        "seed_u64": sequence.seed_u64,
        "sha256": sequence.sha256,
        "mandatory_frame_count": MANDATORY_FRAME_COUNT,
        "trajectory_frame_count": TRAJECTORY_FRAME_COUNT,
        "frame_count": FRAME_COUNT,
    }


def recompute_model_motion_draft(
    run_dir: Path,
    model_report: dict[str, object],
    backend: Any | None = None,
) -> MotionRecomputation:
    """Recompute motion evidence in memory without consulting published motion artifacts."""

    run_dir = _contained_run_directory(run_dir)
    _, sequence_config, sequence = _config()
    backend = _load_pillow() if backend is None else backend
    preparation_input = dict(model_report)
    preparation_input["_run_dir"] = str(run_dir)
    evidence: dict[str, Any] = {}
    try:
        parts = _prepare_parts(preparation_input, backend, evidence=evidence)
    except Exception:
        underpaint_mask = evidence.get("underpaint_mask")
        if underpaint_mask is not None:
            underpaint_mask.close()
        raise
    underpaint_mask = evidence.get("underpaint_mask")
    if underpaint_mask is None:
        for part in parts:
            part.image.close()
        raise StageContractError("model motion draft underpaint evidence is unavailable")
    try:
        layer_records: list[dict[str, object]] = []
        layer_bytes: list[bytes] = []
        total_bytes = 0
        for part in parts:
            name = f"{part.id}.png"
            data = _png_bytes(part.image, backend)
            total_bytes += len(data)
            if total_bytes > MAX_OUTPUT_BYTES:
                raise StageContractError("model motion draft recomputation exceeded its bound")
            layer_bytes.append(data)
            layer_records.append(_layer_record(part, {
                "id": f"motion-{part.id}",
                "uri": f"{OUTPUT_DIRECTORY}/{name}",
                "media_type": "image/png",
                "byte_length": len(data),
                "sha256": sha256_bytes(data),
                "width": part.image.width,
                "height": part.image.height,
                "mode": "RGBA",
            }))
        geometry = [_geometry_record(part) for part in parts]
        parameters = _parameter_records()
        bindings = _binding_records(layer_records)
        frames: list[dict[str, object]] = []
        frame_bytes: list[bytes] = []
        for index, frame in enumerate(sequence.frames):
            rendered = _render(parts, frame.parameter_fractions(), backend)
            try:
                data = _png_bytes(rendered, backend)
            finally:
                rendered.close()
            total_bytes += len(data)
            if total_bytes > MAX_OUTPUT_BYTES:
                raise StageContractError("model motion draft recomputation exceeded its bound")
            name = f"frame.{index:03d}.{frame.id}.png"
            frame_bytes.append(data)
            frames.append({
                "index": index,
                "id": frame.id,
                "source": frame.source,
                "parameters": frame.parameter_document(),
                "artifact": {
                    "id": f"motion-frame-{index:02d}",
                    "uri": f"{OUTPUT_DIRECTORY}/{name}",
                    "media_type": "image/png",
                    "byte_length": len(data),
                    "sha256": sha256_bytes(data),
                    "width": CANVAS_SIZE,
                    "height": CANVAS_SIZE,
                    "mode": "RGBA",
                },
            })
        model = model_report.get("model")
        reconstruction = model.get("reconstruction") if isinstance(model, dict) else None
        if not isinstance(reconstruction, dict) or not isinstance(reconstruction.get("uri"), str):
            raise StageContractError("model motion draft reconstruction descriptor is invalid")
        reconstruction_path = _contained_run_file(run_dir, str(reconstruction["uri"]))
        with backend.Image.open(reconstruction_path, formats=("PNG",)) as reconstruction_image:
            reconstruction_image.load()
            expected_neutral = reconstruction_image.convert("RGBA")
        with backend.Image.open(io.BytesIO(frame_bytes[0]), formats=("PNG",)) as neutral_image:
            neutral_image.load()
            actual_neutral = neutral_image.convert("RGBA")
        try:
            neutral_validation = _neutral_comparison_images(expected_neutral, actual_neutral)
        finally:
            expected_neutral.close()
            actual_neutral.close()
        return MotionRecomputation(
            layers=layer_records,
            layer_bytes=tuple(layer_bytes),
            geometry=geometry,
            parameters=parameters,
            bindings=bindings,
            sequence=_sequence_record(sequence_config, sequence),
            frames=frames,
            frame_bytes=tuple(frame_bytes),
            validation={**_geometry_validation(parts, sequence), **neutral_validation},
            underpaint_mask=underpaint_mask,
        )
    except Exception:
        underpaint_mask.close()
        raise
    finally:
        for part in parts:
            part.image.close()


def generate_model_motion_draft(run_dir: Path) -> tuple[Path, dict[str, object]]:
    from .model_workbench import load_model_workbench_report

    run_dir = _contained_run_directory(run_dir)
    target = run_dir / OUTPUT_DIRECTORY
    if target.exists() or target.is_symlink() or (run_dir / "workbench-report.json").is_symlink():
        raise StageContractError("model motion draft output already exists or is invalid")
    model_report = load_model_workbench_report(run_dir)
    identity = model_report.get("model", {}).get("identity") if isinstance(model_report.get("model"), dict) else None
    quality = model_report.get("quality")
    if (
        not isinstance(identity, dict)
        or identity.get("profile_id") != MODEL_PROFILE_ID
        or not isinstance(quality, dict)
        or not isinstance(quality.get("neutral_fidelity"), dict)
        or quality["neutral_fidelity"].get("status") != "pass"
    ):
        raise StageContractError("model motion draft requires a fidelity-passing active model profile")
    _, sequence_config, sequence = _config()
    backend = _load_pillow()
    model_report["_run_dir"] = str(run_dir)
    parts = _prepare_parts(model_report, backend)
    model_report.pop("_run_dir", None)
    temporary = Path(tempfile.mkdtemp(prefix="motion-draft-", dir=run_dir))
    published = False
    total_bytes = 0
    try:
        layer_records: list[dict[str, object]] = []
        for part in parts:
            name = f"{part.id}.png"
            facts = _write_png(part.image, temporary / name, backend)
            total_bytes += int(facts["byte_length"])
            if total_bytes > MAX_OUTPUT_BYTES:
                raise StageContractError("model motion draft output exceeded its bound")
            layer_records.append(_layer_record(part, {
                "id": f"motion-{part.id}",
                "uri": f"{OUTPUT_DIRECTORY}/{name}",
                "media_type": "image/png",
                **facts,
            }))
        geometry = [_geometry_record(part) for part in parts]
        validation = _geometry_validation(parts, sequence)
        reconstruction = model_report["model"]["reconstruction"]
        if not isinstance(reconstruction, dict) or not isinstance(reconstruction.get("uri"), str):
            raise StageContractError("model motion draft reconstruction descriptor is invalid")
        reconstruction_path = _contained_run_file(run_dir, str(reconstruction["uri"]))
        frames: list[dict[str, object]] = []
        for index, frame in enumerate(sequence.frames):
            rendered = _render(parts, frame.parameter_fractions(), backend)
            try:
                name = f"frame.{index:03d}.{frame.id}.png"
                path = temporary / name
                facts = _write_png(rendered, path, backend)
            finally:
                rendered.close()
            total_bytes += int(facts["byte_length"])
            if total_bytes > MAX_OUTPUT_BYTES:
                raise StageContractError("model motion draft output exceeded its bound")
            frames.append(
                {
                    "index": index,
                    "id": frame.id,
                    "source": frame.source,
                    "parameters": frame.parameter_document(),
                    "artifact": {
                        "id": f"motion-frame-{index:02d}",
                        "uri": f"{OUTPUT_DIRECTORY}/{name}",
                        "media_type": "image/png",
                        **facts,
                    },
                }
            )
        comparison = _neutral_comparison(reconstruction_path, temporary / str(Path(frames[0]["artifact"]["uri"]).name))
        report = {
            "format": "oneclick2d.model-motion-draft-report",
            "format_version": "0.2.0",
            "scope": "disposable-local-model-spike",
            "run_id": run_dir.name,
            "profile": {
                "id": PROFILE_ID,
                "algorithm_id": ALGORITHM_ID,
                "config_sha256": CONFIG_SHA256,
                "renderer_contract_id": RENDERER_CONTRACT_ID,
                "renderer_profile_id": RENDERER_PROFILE_ID,
            },
            "input": {
                "model_result_sha256": sha256_file(run_dir / "model-result.json"),
                "model_profile_id": MODEL_PROFILE_ID,
                "reconstruction_sha256": reconstruction["sha256"],
                "canvas": [CANVAS_SIZE, CANVAS_SIZE],
            },
            "layers": layer_records,
            "geometry": geometry,
            "parameters": _parameter_records(),
            "bindings": _binding_records(layer_records),
            "sequence": _sequence_record(sequence_config, sequence),
            "frames": frames,
            "validation": {**validation, **comparison},
            "quality": {
                "status": "review_required",
                "review_items": ["semantic_correctness", "hidden_region_completion", "dynamic_visual_quality"],
            },
            "claims": {
                "model_used": True,
                "quad_mesh_research_draft": True,
                "affine_binding_research_draft": True,
                "dynamic_preview_research_draft": True,
                "mesh_delta_generated": False,
                "oc2d_produced": False,
                "moc3_produced": False,
                "gate_f_feasibility_proven": False,
            },
        }
        (temporary / REPORT_NAME).write_bytes(canonical_json_bytes(report))
        os.replace(temporary, target)
        published = True
        validated = load_model_motion_draft_report(
            run_dir,
            expected_model_result_sha256=report["input"]["model_result_sha256"],
            expected_reconstruction_sha256=str(reconstruction["sha256"]),
            expected_reconstruction_uri=str(reconstruction["uri"]),
        )
        return target / REPORT_NAME, validated
    except Exception:
        shutil.rmtree(target if published else temporary, ignore_errors=True)
        raise
    finally:
        for part in parts:
            part.image.close()


def load_model_motion_draft_report(
    run_dir: Path,
    *,
    expected_model_result_sha256: str,
    expected_reconstruction_sha256: str,
    expected_reconstruction_uri: str,
) -> dict[str, object]:
    run_dir = _contained_run_directory(run_dir)
    directory = run_dir / OUTPUT_DIRECTORY
    report_path = directory / REPORT_NAME
    if not directory.exists() and not report_path.exists():
        raise StageContractError("model motion draft is unavailable")
    try:
        directory = contained_run_path(
            run_dir.parent,
            run_dir.name,
            OUTPUT_DIRECTORY,
            kind="directory",
        )
    except ValueError as exc:
        raise StageContractError("model motion draft directory is invalid") from exc
    report_path = _contained_run_file(run_dir, f"{OUTPUT_DIRECTORY}/{REPORT_NAME}")
    report = strict_load_json_bytes(read_bounded_file(report_path, MAX_REPORT_BYTES))
    required = {
        "format", "format_version", "scope", "run_id", "profile", "input", "layers", "geometry",
        "parameters", "bindings", "sequence", "frames", "validation", "quality", "claims",
    }
    if (
        not isinstance(report, dict)
        or set(report) != required
        or report.get("format") != "oneclick2d.model-motion-draft-report"
        or report.get("format_version") != "0.2.0"
        or report.get("scope") != "disposable-local-model-spike"
        or report.get("run_id") != run_dir.name
    ):
        raise StageContractError("model motion draft report is invalid")
    profile = report.get("profile")
    input_value = report.get("input")
    if profile != {
        "id": PROFILE_ID,
        "algorithm_id": ALGORITHM_ID,
        "config_sha256": CONFIG_SHA256,
        "renderer_contract_id": RENDERER_CONTRACT_ID,
        "renderer_profile_id": RENDERER_PROFILE_ID,
    } or input_value != {
        "model_result_sha256": expected_model_result_sha256,
        "model_profile_id": MODEL_PROFILE_ID,
        "reconstruction_sha256": expected_reconstruction_sha256,
        "canvas": [CANVAS_SIZE, CANVAS_SIZE],
    }:
        raise StageContractError("model motion draft input identity does not match")
    layers = report.get("layers")
    geometry = report.get("geometry")
    if not isinstance(layers, list) or not 6 <= len(layers) <= 40 or not isinstance(geometry, list) or len(geometry) != len(layers):
        raise StageContractError("model motion draft geometry inventory is invalid")
    layer_ids: set[str] = set()
    expected_geometry: list[dict[str, object]] = []
    layer_artifacts: list[tuple[str, dict[str, object], tuple[int, int]]] = []
    for item in layers:
        if not isinstance(item, dict) or set(item) != {"id", "semantic", "side", "source_artifact_id", "box_ltrb", "draw_order", "motion_group", "artifact"}:
            raise StageContractError("model motion draft layer is invalid")
        layer_id = item.get("id")
        box = item.get("box_ltrb")
        artifact = item.get("artifact")
        if (
            not isinstance(layer_id, str)
            or layer_id in layer_ids
            or not isinstance(item.get("semantic"), str)
            or item.get("side") not in {"left", "right", "not-applicable"}
            or not isinstance(item.get("source_artifact_id"), str)
            or not isinstance(item.get("draw_order"), int)
            or item.get("motion_group") not in {"static", "head", "eye", "mouth"}
            or not isinstance(box, list)
            or len(box) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in box)
            or not (0 <= box[0] < box[2] <= CANVAS_SIZE and 0 <= box[1] < box[3] <= CANVAS_SIZE)
            or not isinstance(artifact, dict)
        ):
            raise StageContractError("model motion draft layer is invalid")
        layer_ids.add(layer_id)
        left, top, right, bottom = box
        name = f"{layer_id}.png"
        if (
            set(artifact) != {"id", "uri", "media_type", "byte_length", "sha256", "width", "height", "mode"}
            or artifact.get("id") != f"motion-{layer_id}"
            or artifact.get("uri") != f"{OUTPUT_DIRECTORY}/{name}"
            or artifact.get("media_type") != "image/png"
            or artifact.get("width") != right - left
            or artifact.get("height") != bottom - top
            or artifact.get("mode") != "RGBA"
            or isinstance(artifact.get("byte_length"), bool)
            or not isinstance(artifact.get("byte_length"), int)
            or not isinstance(artifact.get("sha256"), str)
            or SHA256_RE.fullmatch(str(artifact["sha256"])) is None
        ):
            raise StageContractError("model motion draft layer artifact is invalid")
        layer_artifacts.append((name, artifact, (right - left, bottom - top)))
        expected_geometry.append({
            "id": f"mesh.{layer_id.removeprefix('layer.')}",
            "layer_id": layer_id,
            "vertices_xy": [[left, top], [right, top], [right, bottom], [left, bottom]],
            "triangle_indices": [0, 1, 2, 0, 2, 3],
            "winding": "positive-screen-y-down",
        })
    if geometry != expected_geometry or report.get("parameters") != _parameter_records() or report.get("bindings") != _binding_records(layers):
        raise StageContractError("model motion draft geometry or binding contract does not match")
    _, sequence_config, sequence = _config()
    if report.get("sequence") != _sequence_record(sequence_config, sequence):
        raise StageContractError("model motion draft sequence identity does not match")
    frames = report.get("frames")
    if not isinstance(frames, list) or len(frames) != FRAME_COUNT:
        raise StageContractError("model motion draft frame inventory is invalid")
    total = report_path.stat().st_size
    expected_files = {REPORT_NAME}
    for name, artifact, size in layer_artifacts:
        path = _contained_run_file(run_dir, f"{OUTPUT_DIRECTORY}/{name}")
        if path.stat().st_size != artifact["byte_length"] or sha256_file(path) != artifact["sha256"]:
            raise StageContractError("model motion draft layer artifact identity does not match")
        data = read_bounded_file(path, MAX_FRAME_BYTES)
        _verify_output_png(data, size)
        total += len(data)
        if total > MAX_OUTPUT_BYTES:
            raise StageContractError("model motion draft output exceeded its bound")
        expected_files.add(name)
    for index, (frame_value, sequence_frame) in enumerate(zip(frames, sequence.frames, strict=True)):
        artifact = frame_value.get("artifact") if isinstance(frame_value, dict) else None
        name = f"frame.{index:03d}.{sequence_frame.id}.png"
        uri = f"{OUTPUT_DIRECTORY}/{name}"
        if (
            not isinstance(frame_value, dict)
            or set(frame_value) != {"index", "id", "source", "parameters", "artifact"}
            or frame_value.get("index") != index
            or frame_value.get("id") != sequence_frame.id
            or frame_value.get("source") != sequence_frame.source
            or frame_value.get("parameters") != sequence_frame.parameter_document()
            or not isinstance(artifact, dict)
            or set(artifact) != {"id", "uri", "media_type", "byte_length", "sha256", "width", "height", "mode"}
            or artifact.get("id") != f"motion-frame-{index:02d}"
            or artifact.get("uri") != uri
            or artifact.get("media_type") != "image/png"
            or artifact.get("width") != CANVAS_SIZE
            or artifact.get("height") != CANVAS_SIZE
            or artifact.get("mode") != "RGBA"
            or isinstance(artifact.get("byte_length"), bool)
            or not isinstance(artifact.get("byte_length"), int)
            or not isinstance(artifact.get("sha256"), str)
            or SHA256_RE.fullmatch(str(artifact["sha256"])) is None
        ):
            raise StageContractError("model motion draft frame descriptor is invalid")
        path = _contained_run_file(run_dir, f"{OUTPUT_DIRECTORY}/{name}")
        if path.stat().st_size != artifact["byte_length"] or sha256_file(path) != artifact["sha256"]:
            raise StageContractError("model motion draft frame identity does not match")
        data = read_bounded_file(path, MAX_FRAME_BYTES)
        _verify_output_png(data, (CANVAS_SIZE, CANVAS_SIZE))
        total += len(data)
        if total > MAX_OUTPUT_BYTES:
            raise StageContractError("model motion draft output exceeded its bound")
        expected_files.add(name)
    neutral_hashes = {
        str(frame_value["artifact"]["sha256"])
        for frame_value, sequence_frame in zip(frames, sequence.frames, strict=True)
        if _is_neutral_frame(sequence_frame)
    }
    if len(neutral_hashes) != 1:
        raise StageContractError("model motion draft neutral frames do not match")
    entries = list(directory.iterdir())
    if {path.name for path in entries} != expected_files:
        raise StageContractError("model motion draft output inventory is incomplete")
    try:
        for path in entries:
            contained_run_path(
                run_dir.parent,
                run_dir.name,
                f"{OUTPUT_DIRECTORY}/{path.name}",
                kind="file",
            )
    except ValueError as exc:
        raise StageContractError("model motion draft output inventory is incomplete") from exc
    reconstruction_path = _contained_run_file(run_dir, expected_reconstruction_uri)
    if sha256_file(reconstruction_path) != expected_reconstruction_sha256:
        raise StageContractError("model motion draft reconstruction identity does not match")
    validation_parts = [
        MotionPart(
            id=str(item["id"]),
            semantic=str(item["semantic"]),
            side=str(item["side"]),
            source_artifact_id=str(item["source_artifact_id"]),
            box=tuple(item["box_ltrb"]),
            draw_order=int(item["draw_order"]),
            motion_group=str(item["motion_group"]),
            image=None,
        )
        for item in layers
    ]
    expected_validation = {
        **_geometry_validation(validation_parts, sequence),
        **_neutral_comparison(
            reconstruction_path,
            _contained_run_file(run_dir, str(frames[0]["artifact"]["uri"])),
        ),
    }
    validation = report.get("validation")
    quality = report.get("quality")
    claims = report.get("claims")
    if (
        validation != expected_validation
        or quality != {"status": "review_required", "review_items": ["semantic_correctness", "hidden_region_completion", "dynamic_visual_quality"]}
        or claims != {"model_used": True, "quad_mesh_research_draft": True, "affine_binding_research_draft": True, "dynamic_preview_research_draft": True, "mesh_delta_generated": False, "oc2d_produced": False, "moc3_produced": False, "gate_f_feasibility_proven": False}
    ):
        raise StageContractError("model motion draft validation or claims are invalid")
    return report
