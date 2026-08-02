"""Deterministic model-candidate bridge for a single local Gate F preflight."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .contracts import StageContractError, StageStatus
from .local_workbench import _find_output, _normalization_config, _stage_outputs
from .model_motion_draft import (
    ALGORITHM_ID as MOTION_ALGORITHM_ID,
    CANVAS_SIZE,
    MotionRecomputation,
    PROFILE_ID as MOTION_PROFILE_ID,
    recompute_model_motion_draft,
)
from .model_worker import MODEL_PART_NAMES, PROFILE_ID as MODEL_PROFILE_ID
from .paired_experiment import arm_identity_from_report, validate_arm_parity
from .raster import _load_pillow, _verify_output_png
from .rendering import RENDERER_CONTRACT_ID, RENDERER_PROFILE_ID
from .runner import PipelineRunner
from .runtime import (
    SHA256_RE,
    canonical_json_bytes,
    contained_run_path,
    read_bounded_file,
    sha256_bytes,
    sha256_file,
    strict_load_json_bytes,
)
from .simple_cutout import build_simple_cutout_registry

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "examples" / "gate-f-model-candidate" / "config.json"
ONTOLOGY_PATH = ROOT / "registries" / "ontology-v0.1.yaml"
ONTOLOGY_SHA256 = "ea03fdf0e757a9e15519b6fe7bad7ed50b7d736214cca3b8b74bfb9b57aa1c76"
CONFIG_SHA256 = "e1a2e713c1f0ce27775794b05efac338fe6e6d9bd750169b577caf311b8dd37b"
PROFILE_ID = "oc2d.spike.model-candidate.source-preserve-v4.v1"
REPORT_NAME = "candidate-report.json"
PREFLIGHT_REPORT_NAME = "preflight-report.json"
OUTPUT_DIRECTORY = "model-candidate-preflight"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
SEMANTIC_ALPHA_THRESHOLD = 0
SOURCE_VISIBLE_ALPHA_THRESHOLD = 31
ACTIVATION_BLOCKERS = (
    "GATE_0_NOT_APPROVED",
    "D_003_NOT_CLOSED",
    "D_009_NOT_CLOSED",
    "EXTERNAL_PSD_EDITOR_NOT_EVALUATED",
    "TWENTY_ITEM_PROTOCOL_NOT_RUN",
)
VISIBLE_PRIORITY = (
    "back hair", "tail", "wings", "neck", "topwear", "bottomwear", "legwear", "footwear",
    "face", "ears", "eyewhite", "irides", "eyebrow", "nose", "mouth", "eyelash",
    "front hair", "headwear", "neckwear", "handwear", "eyewear", "earwear", "objects",
)
EYE_SEMANTICS = ("eyewhite", "irides", "eyelash")
ONTOLOGY_SLOTS = (
    ("oc2d.character", "required", "not-applicable"),
    ("oc2d.background", "optional", "not-applicable"),
    ("oc2d.face.base", "required", "not-applicable"),
    ("oc2d.eye.left", "required", "left"),
    ("oc2d.eye.right", "required", "right"),
    ("oc2d.brow.left", "conditional", "left"),
    ("oc2d.brow.right", "conditional", "right"),
    ("oc2d.mouth", "required", "not-applicable"),
    ("oc2d.hair.front", "conditional", "not-applicable"),
    ("oc2d.hair.side", "conditional", "not-applicable"),
    ("oc2d.hair.back", "conditional", "not-applicable"),
    ("oc2d.neck", "conditional", "not-applicable"),
    ("oc2d.torso", "required", "not-applicable"),
    ("oc2d.clothing", "conditional", "not-applicable"),
    ("oc2d.accessory.front", "optional", "not-applicable"),
    ("oc2d.accessory.back", "optional", "not-applicable"),
)
SLOT_SEMANTICS = {
    "oc2d.character": VISIBLE_PRIORITY,
    "oc2d.background": (),
    "oc2d.face.base": ("face",),
    "oc2d.eye.left": EYE_SEMANTICS,
    "oc2d.eye.right": EYE_SEMANTICS,
    "oc2d.brow.left": ("eyebrow",),
    "oc2d.brow.right": ("eyebrow",),
    "oc2d.mouth": ("mouth",),
    "oc2d.hair.front": ("front hair",),
    "oc2d.hair.side": (),
    "oc2d.hair.back": ("back hair",),
    "oc2d.neck": ("neck",),
    "oc2d.torso": ("topwear", "bottomwear", "legwear"),
    "oc2d.clothing": ("topwear", "bottomwear", "legwear", "footwear"),
    "oc2d.accessory.front": ("headwear", "neckwear", "handwear", "eyewear", "earwear", "objects"),
    "oc2d.accessory.back": ("tail", "wings"),
}


def _config() -> dict[str, object]:
    data = read_bounded_file(CONFIG_PATH, 256 * 1024)
    value = strict_load_json_bytes(data)
    if not isinstance(value, dict) or sha256_bytes(canonical_json_bytes(value)) != CONFIG_SHA256:
        raise StageContractError("model candidate config does not match the frozen profile")
    expected = {
        "format": "oneclick2d.model-candidate-config",
        "format_version": "0.1.0",
        "profile_id": PROFILE_ID,
        "required_model_profile_id": MODEL_PROFILE_ID,
        "required_motion_profile_id": MOTION_PROFILE_ID,
        "required_pillow_version": "12.1.0",
        "renderer_profile_id": RENDERER_PROFILE_ID,
        "ontology_registry": {
            "id": "oneclick2d.ontology",
            "version": "0.1.0",
            "sha256": ONTOLOGY_SHA256,
        },
        "postprocess": {
            "semantic_alpha_state": "source-preserve-v4-cleaned",
            "visible_alpha_threshold": SOURCE_VISIBLE_ALPHA_THRESHOLD,
            "visible_priority": list(VISIBLE_PRIORITY),
        },
        "frame_sequence": {
            "format": "oneclick2d.gate-f-frame-sequence-config",
            "format_version": "0.1.0",
            "profile_id": "oc2d.spike.gate-f-frame-sequence.v1",
            "seed_u64": "00000000000000000042",
        },
    }
    if value != expected or sha256_file(ONTOLOGY_PATH) != ONTOLOGY_SHA256:
        raise StageContractError("model candidate fixed identities do not match")
    return value


def _contained_run_directory(run_dir: Path) -> Path:
    try:
        return contained_run_path(run_dir.parent, run_dir.name, kind="directory")
    except ValueError as exc:
        raise StageContractError("model candidate run directory is invalid") from exc


def _contained_run_file(run_dir: Path, relative: str) -> Path:
    try:
        return contained_run_path(run_dir.parent, run_dir.name, relative, kind="file")
    except ValueError as exc:
        raise StageContractError("model candidate artifact path is invalid") from exc


def _safe_artifact_path(run_dir: Path, descriptor: dict[str, object]) -> Path:
    uri = descriptor.get("uri")
    if not isinstance(uri, str):
        raise StageContractError("model candidate artifact URI is invalid")
    path = _contained_run_file(run_dir, uri)
    length = descriptor.get("byte_length")
    digest = descriptor.get("sha256")
    if (
        isinstance(length, bool)
        or not isinstance(length, int)
        or not 1 <= length <= MAX_ARTIFACT_BYTES
        or not isinstance(digest, str)
        or SHA256_RE.fullmatch(digest) is None
        or path.stat().st_size != length
        or sha256_file(path) != digest
    ):
        raise StageContractError("model candidate artifact identity does not match")
    return path


def _load_rgba(path: Path, backend: Any) -> Any:
    try:
        with backend.Image.open(path, formats=("PNG",)) as image:
            image.load()
            if image.mode != "RGBA" or image.size != (CANVAS_SIZE, CANVAS_SIZE):
                raise StageContractError("model candidate RGBA canvas is invalid")
            return image.copy()
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError("model candidate PNG is invalid") from exc


def _binary_alpha(image: Any, threshold: int) -> Any:
    alpha = image.getchannel("A")
    try:
        return alpha.point(lambda value: 255 if value > threshold else 0)
    finally:
        alpha.close()


def _pixel_count(mask: Any) -> int:
    return int(mask.histogram()[255])


def _mask_png(mask: Any, backend: Any) -> bytes:
    stream = io.BytesIO()
    pnginfo = backend.PngImagePlugin.PngInfo()
    pnginfo.add(b"sRGB", b"\x00")
    mask.save(stream, format="PNG", optimize=False, compress_level=9, pnginfo=pnginfo, icc_profile=None, exif=b"")
    data = stream.getvalue()
    try:
        with backend.Image.open(io.BytesIO(data), formats=("PNG",)) as image:
            image.load()
            if image.mode != "L" or image.size != (CANVAS_SIZE, CANVAS_SIZE):
                raise StageContractError("model candidate mask PNG is invalid")
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError("model candidate mask PNG is invalid") from exc
    return data


def _mask_descriptor(name: str, data: bytes, pixel_count: int) -> dict[str, object]:
    return {
        "id": name.removesuffix(".png"),
        "uri": f"{OUTPUT_DIRECTORY}/{name}",
        "media_type": "image/png",
        "byte_length": len(data),
        "sha256": sha256_bytes(data),
        "width": CANVAS_SIZE,
        "height": CANVAS_SIZE,
        "mode": "L",
        "pixel_count": pixel_count,
    }


def _difference_count(left: Any, right: Any, mask: Any) -> int:
    from PIL import ImageChops

    left_rgb = left.convert("RGB")
    right_rgb = right.convert("RGB")
    difference = ImageChops.difference(left_rgb, right_rgb)
    channels = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    try:
        histogram = maximum.histogram(mask=mask)
        return sum(histogram[1:])
    finally:
        maximum.close()
        for channel in channels:
            channel.close()
        difference.close()
        left_rgb.close()
        right_rgb.close()


def _source_region_masks(union: Any, source_alpha: Any) -> tuple[Any, Any, Any]:
    from PIL import ImageChops

    inverse_source = ImageChops.invert(source_alpha)
    inverse_union = ImageChops.invert(union)
    try:
        protected = ImageChops.multiply(union, source_alpha)
        exposed = ImageChops.multiply(union, inverse_source)
        omission = ImageChops.multiply(source_alpha, inverse_union)
        return protected, exposed, omission
    finally:
        inverse_source.close()
        inverse_union.close()


def _verify_source_partition(protected: Any, exposed: Any, union: Any) -> None:
    from PIL import ImageChops

    intersection = ImageChops.multiply(protected, exposed)
    combined = ImageChops.lighter(protected, exposed)
    difference = ImageChops.difference(combined, union)
    try:
        if intersection.getbbox() is not None or difference.getbbox() is not None:
            raise StageContractError("model candidate source provenance partition is invalid")
    finally:
        difference.close()
        combined.close()
        intersection.close()


def _semantic_inventory(run_dir: Path, model: dict[str, object], backend: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, object]]]:
    layers_value = model.get("layers")
    if not isinstance(layers_value, list):
        raise StageContractError("model candidate semantic inventory is unavailable")
    by_name = {
        str(item["name"]): item
        for item in layers_value
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if not set(MODEL_PART_NAMES).issubset(by_name):
        raise StageContractError("model candidate semantic inventory is incomplete")
    semantic_images: dict[str, Any] = {}
    depth_images: dict[str, Any] = {}
    try:
        for name in MODEL_PART_NAMES:
            item = by_name[name]
            artifact = item.get("artifact")
            depth_artifact = item.get("depth_artifact")
            if not isinstance(artifact, dict) or not isinstance(depth_artifact, dict):
                raise StageContractError("model candidate semantic depth evidence is incomplete")
            semantic_images[name] = _load_rgba(_safe_artifact_path(run_dir, artifact), backend)
            depth_path = _safe_artifact_path(run_dir, depth_artifact)
            with backend.Image.open(depth_path, formats=("PNG",)) as image:
                image.load()
                if image.mode != "L" or image.size != (CANVAS_SIZE, CANVAS_SIZE):
                    raise StageContractError("model candidate depth canvas is invalid")
                depth_images[name] = image.copy()
        return semantic_images, depth_images, by_name
    except Exception:
        for image in (*semantic_images.values(), *depth_images.values()):
            image.close()
        raise


def _winner_evidence(semantic_images: dict[str, Any], depth_images: dict[str, Any], backend: Any) -> tuple[Any, Any]:
    from array import array

    pixel_count = CANVAS_SIZE * CANVAS_SIZE
    winner_depth = array("H", [256]) * pixel_count
    winner_priority = array("b", [-1]) * pixel_count
    winner_index = bytearray([255]) * pixel_count
    model_index_by_name = {name: index for index, name in enumerate(MODEL_PART_NAMES)}
    priority_by_name = {name: index for index, name in enumerate(VISIBLE_PRIORITY)}
    for name in MODEL_PART_NAMES:
        alpha = semantic_images[name].getchannel("A")
        try:
            alpha_data = alpha.tobytes()
        finally:
            alpha.close()
        depth_data = depth_images[name].tobytes()
        priority = priority_by_name[name]
        model_index = model_index_by_name[name]
        for offset, (alpha_value, depth_value) in enumerate(zip(alpha_data, depth_data, strict=True)):
            if alpha_value > SEMANTIC_ALPHA_THRESHOLD and (
                depth_value < winner_depth[offset]
                or (depth_value == winner_depth[offset] and priority > winner_priority[offset])
            ):
                winner_depth[offset] = depth_value
                winner_priority[offset] = priority
                winner_index[offset] = model_index
    return (
        backend.Image.frombytes("I;16", (CANVAS_SIZE, CANVAS_SIZE), winner_depth.tobytes()),
        backend.Image.frombytes("L", (CANVAS_SIZE, CANVAS_SIZE), bytes(winner_index)),
    )


def _ontology(semantic_masks: dict[str, Any], union: Any) -> list[dict[str, object]]:
    from PIL import ImageChops

    records: list[dict[str, object]] = []
    for slot_id, applicability, side in ONTOLOGY_SLOTS:
        semantics = SLOT_SEMANTICS[slot_id]
        if slot_id == "oc2d.character":
            count = _pixel_count(union)
        elif not semantics:
            count = 0
        else:
            combined = semantic_masks[semantics[0]].copy()
            try:
                for semantic in semantics[1:]:
                    merged = ImageChops.lighter(combined, semantic_masks[semantic])
                    combined.close()
                    combined = merged
                if side in {"left", "right"}:
                    screen_range = (CANVAS_SIZE // 2, 0, CANVAS_SIZE, CANVAS_SIZE) if side == "left" else (0, 0, CANVAS_SIZE // 2, CANVAS_SIZE)
                    local = combined.crop(screen_range)
                    try:
                        count = _pixel_count(local)
                    finally:
                        local.close()
                else:
                    count = _pixel_count(combined)
            finally:
                combined.close()
        status = "PRESENT" if count > 0 else "LOW_CONFIDENCE" if applicability == "required" else "NOT_APPLICABLE"
        records.append({
            "slot_id": slot_id,
            "applicability": applicability,
            "side": side,
            "status": status,
            "semantic_sources": list(semantics),
            "visible_pixel_count": count,
        })
    return records


def _motion_lineage(motion: MotionRecomputation) -> list[dict[str, object]]:
    layers = motion.layers
    bindings = motion.bindings
    records = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise StageContractError("model candidate motion layer is invalid")
        semantic = str(layer["semantic"])
        operations = ["source-preserve-v4-alpha-clean", "subject-matte", "bbox-crop-padding-4"]
        if semantic == "face":
            operations.append("deterministic-face-underpaint")
        if semantic in {"eye", "eyebrow", "nose", "mouth"}:
            operations.append("source-visible-feature-rgb")
        if semantic == "eye":
            operations.append("grouped-eye-mask")
        binding_ids = [
            str(binding["id"])
            for binding in bindings
            if isinstance(binding, dict) and layer.get("id") in binding.get("target_layer_ids", [])
        ]
        records.append({
            "motion_layer_id": layer["id"],
            "semantic": semantic,
            "side": layer["side"],
            "source_artifact_id": layer["source_artifact_id"],
            "operations": operations,
            "binding_ids": binding_ids,
            "motion_reveal_scope": "shared-37-frame-research-draft",
        })
    return records


def _analyze_model(
    run_dir: Path,
    workbench: dict[str, object],
    motion: MotionRecomputation,
    backend: Any,
) -> tuple[dict[str, object], dict[str, bytes]]:
    from PIL import ImageChops

    model = workbench.get("model")
    if not isinstance(model, dict):
        raise StageContractError("model candidate requires a validated motion draft")
    source_descriptor = model.get("source")
    reconstruction_descriptor = model.get("reconstruction")
    if not isinstance(source_descriptor, dict) or not isinstance(reconstruction_descriptor, dict):
        raise StageContractError("model candidate source evidence is incomplete")
    source = _load_rgba(_safe_artifact_path(run_dir, source_descriptor), backend)
    reconstruction = _load_rgba(_safe_artifact_path(run_dir, reconstruction_descriptor), backend)
    semantic_images: dict[str, Any] = {}
    depth_images: dict[str, Any] = {}
    masks: dict[str, Any] = {}
    try:
        semantic_images, depth_images, _ = _semantic_inventory(run_dir, model, backend)
        semantic_masks = {
            name: _binary_alpha(image, SEMANTIC_ALPHA_THRESHOLD)
            for name, image in semantic_images.items()
        }
        masks.update({f"semantic:{name}": mask for name, mask in semantic_masks.items()})
        winner_depth, winner_index = _winner_evidence(semantic_images, depth_images, backend)
        masks["winner-depth"] = winner_depth
        masks["winner-index"] = winner_index
        union = semantic_masks[VISIBLE_PRIORITY[0]].copy()
        for name in VISIBLE_PRIORITY[1:]:
            merged = ImageChops.lighter(union, semantic_masks[name])
            union.close()
            union = merged
        masks["union"] = union
        source_alpha = _binary_alpha(source, SOURCE_VISIBLE_ALPHA_THRESHOLD)
        protected, exposed, omission = _source_region_masks(union, source_alpha)
        _verify_source_partition(protected, exposed, union)
        masks.update({"source-alpha": source_alpha, "protected": protected, "exposed": exposed, "omission": omission})

        visible_layer_mismatches = 0
        hidden_masks: dict[str, Any] = {}
        model_index_by_name = {name: index for index, name in enumerate(MODEL_PART_NAMES)}
        for name in VISIBLE_PRIORITY:
            model_index = model_index_by_name[name]
            visible = winner_index.point(
                lambda value, expected=model_index: 255 if value == expected else 0
            )
            source_visible = ImageChops.multiply(visible, source_alpha)
            try:
                visible_layer_mismatches += _difference_count(source, semantic_images[name], source_visible)
                hidden = ImageChops.subtract(semantic_masks[name], visible)
                masks[f"hidden:{name}"] = hidden
                hidden_masks[name] = hidden
            finally:
                source_visible.close()
                visible.close()
        reconstruction_mismatches = _difference_count(source, reconstruction, union)
        reconstruction_alpha = reconstruction.getchannel("A")
        try:
            alpha_mismatch = ImageChops.difference(reconstruction_alpha, union)
            reconstruction_alpha_mismatch_count = sum(alpha_mismatch.histogram()[1:])
        finally:
            reconstruction_alpha.close()
            alpha_mismatch.close()
        if visible_layer_mismatches or reconstruction_mismatches or reconstruction_alpha_mismatch_count:
            raise StageContractError("model candidate source-preservation evidence does not match v4")

        underpaint = motion.underpaint_mask.copy()
        masks["underpaint"] = underpaint

        published_masks: dict[str, tuple[Any, str]] = {
            "mask.semantic-union.png": (union, "semantic-union"),
            "mask.source-visible.png": (protected, "source-visible"),
            "mask.source-omission.png": (omission, "source-omission"),
            "mask.source-transparent-exposed.png": (exposed, "source-transparent-exposed"),
            "mask.deterministic-underpaint.face.png": (underpaint, "deterministic-underpaint-face"),
        }
        for name in VISIBLE_PRIORITY:
            published_masks[f"mask.hidden-completion.{name.replace(' ', '-')}.png"] = (hidden_masks[name], f"hidden-completion-{name.replace(' ', '-')}")
        mask_bytes = {name: _mask_png(mask, backend) for name, (mask, _) in published_masks.items()}
        descriptors = {
            name: _mask_descriptor(name, mask_bytes[name], _pixel_count(mask))
            for name, (mask, _) in published_masks.items()
        }
        ontology = _ontology(semantic_masks, union)
        required = [item for item in ontology if item["applicability"] == "required"]
        required_present = sum(item["status"] == "PRESENT" for item in required)
        generated_regions = [
            {
                "kind": "source-transparent-exposed",
                "semantic": "union",
                "mask": descriptors["mask.source-transparent-exposed.png"],
                "producer": "model-source-preserve-v4",
                "confidence": "not_available_review_required",
                "motion_reveal_scope": "shared-37-frame-research-draft",
                "feather_pixels": 0,
            },
            {
                "kind": "deterministic-underpaint",
                "semantic": "face",
                "mask": descriptors["mask.deterministic-underpaint.face.png"],
                "producer": MOTION_PROFILE_ID,
                "confidence": "not_available_review_required",
                "motion_reveal_scope": "head.yaw/head.pitch",
                "feather_pixels": 4,
            },
        ]
        generated_regions.extend(
            {
                "kind": "model-hidden-completion",
                "semantic": name,
                "mask": descriptors[f"mask.hidden-completion.{name.replace(' ', '-')}.png"],
                "producer": MODEL_PROFILE_ID,
                "confidence": "not_available_review_required",
                "motion_reveal_scope": "shared-37-frame-research-draft",
                "feather_pixels": 0,
            }
            for name in VISIBLE_PRIORITY
        )
        analysis = {
            "ontology": ontology,
            "required_slot_facts": {
                "present": required_present,
                "required": len(required),
                "single_item_gate_threshold_evaluated": False,
            },
            "provenance": {
                "semantic_union": descriptors["mask.semantic-union.png"],
                "source_visible": descriptors["mask.source-visible.png"],
                "source_omission": descriptors["mask.source-omission.png"],
                "source_transparent_exposed": descriptors["mask.source-transparent-exposed.png"],
                "generated_regions": generated_regions,
                "motion_lineage": _motion_lineage(motion),
                "model_profile_id": MODEL_PROFILE_ID,
                "model_config_sha256": model["identity"]["profile_sha256"],
                "model_seed": model["identity"]["seed"],
                "source_artifact_id": source_descriptor["id"],
            },
            "source_pixel_facts": {
                "source_visible_pixel_count": _pixel_count(protected),
                "source_protected_overlap_pixel_count": _pixel_count(protected),
                "source_omission_pixel_count": _pixel_count(omission),
                "source_transparent_exposed_pixel_count": _pixel_count(exposed),
                "visible_layer_rgb_mismatch_count": visible_layer_mismatches,
                "neutral_reconstruction_rgb_mismatch_count": reconstruction_mismatches,
                "neutral_reconstruction_alpha_mismatch_count": reconstruction_alpha_mismatch_count,
                "dynamic_frame_source_pixel_protection": "not_evaluated",
            },
        }
        return analysis, mask_bytes
    finally:
        source.close()
        reconstruction.close()
        for image in (*semantic_images.values(), *depth_images.values(), *masks.values()):
            image.close()


def _comparator_run(source_data: bytes) -> dict[str, object]:
    normalize = _normalization_config()
    comparator = read_bounded_file(ROOT / "examples" / "gate-f-simple-cutout-comparator" / "config.json", 64 * 1024)
    common_limits = {
        "max_wall_time_ms": 120_000,
        "max_cpu_time_ms": 120_000,
        "max_peak_ram_bytes": 1_073_741_824,
        "max_scratch_bytes": 1_048_576,
        "max_output_bytes": 512 * 1024 * 1024,
        "max_output_files": 2,
        "max_peak_vram_bytes": 0,
        "gpu_allowed": False,
    }
    spec_value = {
        "$schema": str(ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
        "format": "oneclick2d.gate-f-run-spec",
        "format_version": "0.1.0",
        "scope": "disposable-gate-f-spike",
        "execution_profile": "python-pillow-12.1.0-in-process-v1",
        "root_seed_u64": "00000000000000000042",
        "source": {"role": "source_raster", "sha256": sha256_bytes(source_data), "media_type": "image/png", "max_bytes": 25 * 1024 * 1024},
        "expected_result_role": "simple_cutout_comparator_report",
        "stages": [
            {"id": "stage.raster-normalize", "stage_type": "oc2d.spike.raster-normalize", "adapter_id": "raster.normalize.pillow.v1", "config_uri": "configs/normalize.json", "config_sha256": sha256_bytes(normalize), "limits": {**common_limits, "max_output_bytes": 64 * 1024 * 1024}},
            {"id": "stage.arm-render", "stage_type": "oc2d.spike.simple-cutout-comparator", "adapter_id": "simple-cutout.comparator.pillow.v1", "config_uri": "configs/comparator.json", "config_sha256": sha256_bytes(comparator), "limits": {**common_limits, "max_output_files": 38}},
        ],
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fixture = root / "fixture"
        (fixture / "configs").mkdir(parents=True)
        source_path = fixture / "source.png"
        source_path.write_bytes(source_data)
        (fixture / "configs" / "normalize.json").write_bytes(normalize)
        (fixture / "configs" / "comparator.json").write_bytes(comparator)
        spec_path = fixture / "run-spec.json"
        spec_path.write_bytes(canonical_json_bytes(spec_value))
        status, manifest_path = PipelineRunner(build_simple_cutout_registry(), root / "workspace").run(
            spec_path=spec_path,
            source_path=source_path,
            run_id="run.model-candidate-comparator",
            source_revision="source.model-candidate",
            build_id="build.model-candidate",
        )
        if status is not StageStatus.SUCCEEDED:
            raise StageContractError("model candidate comparator did not succeed")
        manifest = strict_load_json_bytes(read_bounded_file(manifest_path))
        if not isinstance(manifest, dict):
            raise StageContractError("model candidate comparator manifest is invalid")
        run_dir = manifest_path.parent
        normalized_outputs = _stage_outputs(manifest, 0)
        arm_outputs = _stage_outputs(manifest, 1)
        normalized_value = _find_output(normalized_outputs, role="normalized_raster")
        normalization_value = _find_output(normalized_outputs, role="raster_normalization_report")
        report_value = _find_output(arm_outputs, role="simple_cutout_comparator_report")
        if normalized_value is None or normalization_value is None or report_value is None:
            raise StageContractError("model candidate comparator output is incomplete")
        normalized_data = read_bounded_file(run_dir / str(normalized_value["uri"]), MAX_ARTIFACT_BYTES)
        normalization_data = read_bounded_file(run_dir / str(normalization_value["uri"]), MAX_JSON_BYTES)
        report_data = read_bounded_file(run_dir / str(report_value["uri"]), MAX_JSON_BYTES)
        report = strict_load_json_bytes(report_data)
        if not isinstance(report, dict):
            raise StageContractError("model candidate comparator report is invalid")
        by_name = {
            Path(str(item["uri"])).name: item
            for item in arm_outputs
            if item.get("role") == "simple_cutout_frame" and isinstance(item.get("uri"), str)
        }
        frame_data = []
        for frame in report.get("frames", []):
            artifact = frame.get("artifact") if isinstance(frame, dict) else None
            item = by_name.get(str(artifact.get("name"))) if isinstance(artifact, dict) else None
            if item is None or item.get("sha256") != artifact.get("sha256") or item.get("byte_length") != artifact.get("byte_length"):
                raise StageContractError("model candidate comparator frame is missing")
            frame_data.append(read_bounded_file(run_dir / str(item["uri"]), MAX_ARTIFACT_BYTES))
        if len(frame_data) != 37:
            raise StageContractError("model candidate comparator frame inventory is invalid")
        return {
            "normalized": normalized_data,
            "normalization_report": normalization_data,
            "report": report_data,
            "report_value": report,
            "frames": frame_data,
        }


def _pixels_equal(left_data: bytes, right_data: bytes, backend: Any) -> bool:
    with backend.Image.open(io.BytesIO(left_data), formats=("PNG",)) as left_image, backend.Image.open(io.BytesIO(right_data), formats=("PNG",)) as right_image:
        left_image.load()
        right_image.load()
        return left_image.mode == right_image.mode == "RGBA" and left_image.size == right_image.size == (CANVAS_SIZE, CANVAS_SIZE) and left_image.tobytes() == right_image.tobytes()


def _file_descriptor(name: str, data: bytes, artifact_id: str, media_type: str) -> dict[str, object]:
    return {"id": artifact_id, "uri": f"{OUTPUT_DIRECTORY}/{name}", "media_type": media_type, "byte_length": len(data), "sha256": sha256_bytes(data)}


def _candidate_document(
    run_dir: Path,
    workbench: dict[str, object],
    motion: MotionRecomputation,
    analysis: dict[str, object],
    normalized_data: bytes,
    normalization_data: bytes,
    candidate_frames: list[bytes],
) -> dict[str, object]:
    model = workbench["model"]
    frames = []
    for frame, data in zip(motion.frames, candidate_frames, strict=True):
        frames.append({
            "index": frame["index"],
            "id": frame["id"],
            "source": frame["source"],
            "parameters": frame["parameters"],
            "artifact": {
                **_file_descriptor(f"candidate-frame-{int(frame['index']):03d}.png", data, f"candidate-frame-{int(frame['index']):02d}", "image/png"),
                "width": CANVAS_SIZE,
                "height": CANVAS_SIZE,
                "mode": "RGBA",
            },
        })
    return {
        "format": "oneclick2d.model-candidate-report",
        "format_version": "0.1.0",
        "scope": "disposable-local-model-candidate-preflight",
        "run_id": run_dir.name,
        "profile": {
            "id": PROFILE_ID,
            "config_sha256": CONFIG_SHA256,
            "model_profile_id": MODEL_PROFILE_ID,
            "motion_profile_id": MOTION_PROFILE_ID,
            "motion_algorithm_id": MOTION_ALGORITHM_ID,
            "ontology_registry_sha256": ONTOLOGY_SHA256,
        },
        "input": {
            "normalized_raster_sha256": sha256_bytes(normalized_data),
            "normalization_report_sha256": sha256_bytes(normalization_data),
            "model_source_sha256": model["source"]["sha256"],
            "model_result_sha256": sha256_file(run_dir / "model-result.json"),
            "reconstruction_sha256": model["reconstruction"]["sha256"],
            "width": CANVAS_SIZE,
            "height": CANVAS_SIZE,
        },
        "ontology": analysis["ontology"],
        "required_slot_facts": analysis["required_slot_facts"],
        "provenance": analysis["provenance"],
        "geometry": motion.geometry,
        "parameters": motion.parameters,
        "bindings": motion.bindings,
        "sequence": motion.sequence,
        "rendering": {
            "contract_id": RENDERER_CONTRACT_ID,
            "profile_id": RENDERER_PROFILE_ID,
            "canvas": [CANVAS_SIZE, CANVAS_SIZE],
            "color_space": "srgb",
            "input_alpha_mode": "straight",
            "filter_space": "premultiplied-srgb-u8",
        },
        "frames": frames,
        "psd": {
            "sha256": workbench["psd"]["sha256"],
            "structural_readback_pass": True,
            "external_editor_status": "not_evaluated",
        },
        "validation": {
            **analysis["source_pixel_facts"],
            "geometry": motion.validation,
            "semantic_correctness": "not_evaluated",
            "hidden_region_quality": "not_evaluated",
            "identity_changing_completion": "not_evaluated",
            "single_item_required_slot_threshold": "not_evaluated",
        },
        "quality": {
            "status": "review_required",
            "review_items": ["semantic_correctness", "hidden_region_completion", "dynamic_visual_quality", "external_editor_interoperability"],
        },
        "claims": {
            "model_used": True,
            "model_candidate_adapter_completed": True,
            "activated_gate_f_scoring_ready": False,
            "f_usable_evaluated": False,
            "paired_review_performed": False,
            "oc2d_produced": False,
            "moc3_produced": False,
            "gate_f_feasibility_proven": False,
        },
    }


def _preflight_document(run_dir: Path, candidate_data: bytes, comparator_data: bytes, normalized_data: bytes, normalization_data: bytes) -> dict[str, object]:
    return {
        "format": "oneclick2d.model-candidate-preflight-report",
        "format_version": "0.1.0",
        "scope": "single-item-disposable-local-technical-preflight",
        "run_id": run_dir.name,
        "local_status": "LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED",
        "gate_f_status": "GATE_F_NOT_EVALUATED",
        "config_sha256": CONFIG_SHA256,
        "artifacts": {
            "candidate_report": _file_descriptor(REPORT_NAME, candidate_data, "model-candidate-report", "application/vnd.oneclick2d.model-candidate-report+json"),
            "comparator_report": _file_descriptor("comparator-report.json", comparator_data, "model-candidate-comparator-report", "application/vnd.oneclick2d.simple-cutout-comparator-report+json"),
            "normalized_raster": _file_descriptor("normalized-raster.png", normalized_data, "model-candidate-normalized-raster", "image/png"),
            "normalization_report": _file_descriptor("normalization-report.json", normalization_data, "model-candidate-normalization-report", "application/vnd.oneclick2d.raster-normalization-report+json"),
        },
        "arm_parity": True,
        "ready_for_activated_scoring": False,
        "activation_blockers": list(ACTIVATION_BLOCKERS),
        "claims": {
            "single_item_only": True,
            "review_ballots_present": False,
            "paired_outcomes_present": False,
            "f_usable_evaluated": False,
            "oc2d_produced": False,
            "moc3_produced": False,
            "gate_f_feasibility_proven": False,
        },
    }


def _verified_motion_recomputation(
    run_dir: Path,
    workbench: dict[str, object],
    backend: Any,
) -> MotionRecomputation:
    published = workbench.get("motion_draft")
    if not isinstance(published, dict):
        raise StageContractError("model candidate motion draft evidence is invalid")
    recomputed = recompute_model_motion_draft(run_dir, workbench, backend)
    expected_sections: tuple[tuple[str, object], ...] = (
        ("layers", recomputed.layers),
        ("geometry", recomputed.geometry),
        ("parameters", recomputed.parameters),
        ("bindings", recomputed.bindings),
        ("sequence", recomputed.sequence),
        ("frames", recomputed.frames),
    )
    try:
        for name, expected in expected_sections:
            if canonical_json_bytes(published.get(name)) != canonical_json_bytes(expected):
                raise StageContractError("model candidate recomputed motion contract does not match")
        for record, expected in zip(recomputed.layers, recomputed.layer_bytes, strict=True):
            artifact = record["artifact"]
            if not isinstance(artifact, dict):
                raise StageContractError("model candidate recomputed motion layer is invalid")
            actual = read_bounded_file(_safe_artifact_path(run_dir, artifact), MAX_ARTIFACT_BYTES)
            if actual != expected:
                raise StageContractError("model candidate recomputed motion layer does not match")
        for record, expected in zip(recomputed.frames, recomputed.frame_bytes, strict=True):
            artifact = record["artifact"]
            if not isinstance(artifact, dict):
                raise StageContractError("model candidate recomputed motion frame is invalid")
            actual = read_bounded_file(_safe_artifact_path(run_dir, artifact), MAX_ARTIFACT_BYTES)
            if actual != expected:
                raise StageContractError("model candidate recomputed motion frame does not match")
        return recomputed
    except Exception:
        recomputed.close()
        raise


def _expected_files() -> set[str]:
    names = {REPORT_NAME, PREFLIGHT_REPORT_NAME, "comparator-report.json", "normalized-raster.png", "normalization-report.json"}
    names.update(f"candidate-frame-{index:03d}.png" for index in range(37))
    names.update(f"comparator-frame-{index:03d}.png" for index in range(37))
    names.update({"mask.semantic-union.png", "mask.source-visible.png", "mask.source-omission.png", "mask.source-transparent-exposed.png", "mask.deterministic-underpaint.face.png"})
    names.update(f"mask.hidden-completion.{name.replace(' ', '-')}.png" for name in VISIBLE_PRIORITY)
    return names


def _write_exact(directory: Path, name: str, data: bytes, total: list[int]) -> None:
    if name not in _expected_files() or "/" in name or "\\" in name:
        raise StageContractError("model candidate output name is invalid")
    total[0] += len(data)
    if total[0] > MAX_OUTPUT_BYTES:
        raise StageContractError("model candidate output exceeded its bound")
    (directory / name).write_bytes(data)


def generate_model_candidate_preflight(run_dir: Path) -> tuple[Path, dict[str, object]]:
    from .model_workbench import load_model_workbench_report

    run_dir = _contained_run_directory(run_dir)
    target = run_dir / OUTPUT_DIRECTORY
    if target.exists() or target.is_symlink():
        raise StageContractError("model candidate preflight already exists")
    _config()
    workbench = load_model_workbench_report(run_dir)
    identity = workbench.get("model", {}).get("identity") if isinstance(workbench.get("model"), dict) else None
    neutral = workbench.get("quality", {}).get("neutral_fidelity") if isinstance(workbench.get("quality"), dict) else None
    if not isinstance(identity, dict) or identity.get("profile_id") != MODEL_PROFILE_ID or not isinstance(neutral, dict) or neutral.get("status") != "pass":
        raise StageContractError("model candidate requires a fidelity-passing active model profile")
    backend = _load_pillow()
    motion = _verified_motion_recomputation(run_dir, workbench, backend)
    try:
        analysis, mask_bytes = _analyze_model(run_dir, workbench, motion, backend)
    finally:
        motion.close()
    source_path = _safe_artifact_path(run_dir, workbench["model"]["source"])
    source_data = read_bounded_file(source_path, MAX_ARTIFACT_BYTES)
    comparator = _comparator_run(source_data)
    if not _pixels_equal(source_data, comparator["normalized"], backend):
        raise StageContractError("model candidate normalized pixels do not match model input")
    candidate_frames = list(motion.frame_bytes)
    candidate = _candidate_document(run_dir, workbench, motion, analysis, comparator["normalized"], comparator["normalization_report"], candidate_frames)
    validate_arm_parity(arm_identity_from_report(candidate), arm_identity_from_report(comparator["report_value"]))
    candidate_data = canonical_json_bytes(candidate)
    preflight = _preflight_document(run_dir, candidate_data, comparator["report"], comparator["normalized"], comparator["normalization_report"])
    preflight_data = canonical_json_bytes(preflight)
    temporary = Path(tempfile.mkdtemp(prefix=".model-candidate-preflight-", dir=run_dir))
    published = False
    total = [0]
    try:
        _write_exact(temporary, "normalized-raster.png", comparator["normalized"], total)
        _write_exact(temporary, "normalization-report.json", comparator["normalization_report"], total)
        _write_exact(temporary, "comparator-report.json", comparator["report"], total)
        for index, data in enumerate(comparator["frames"]):
            _write_exact(temporary, f"comparator-frame-{index:03d}.png", data, total)
        for index, data in enumerate(candidate_frames):
            _write_exact(temporary, f"candidate-frame-{index:03d}.png", data, total)
        for name, data in mask_bytes.items():
            _write_exact(temporary, name, data, total)
        _write_exact(temporary, REPORT_NAME, candidate_data, total)
        _write_exact(temporary, PREFLIGHT_REPORT_NAME, preflight_data, total)
        if {path.name for path in temporary.iterdir()} != _expected_files():
            raise StageContractError("model candidate output inventory is incomplete")
        os.replace(temporary, target)
        published = True
        validated = load_model_candidate_preflight_report(run_dir)
        return target / PREFLIGHT_REPORT_NAME, validated
    except Exception:
        shutil.rmtree(target if published else temporary, ignore_errors=True)
        raise


def load_model_candidate_preflight_report(run_dir: Path) -> dict[str, object]:
    from .model_workbench import load_model_workbench_report

    run_dir = _contained_run_directory(run_dir)
    try:
        directory = contained_run_path(
            run_dir.parent,
            run_dir.name,
            OUTPUT_DIRECTORY,
            kind="directory",
        )
    except ValueError as exc:
        raise StageContractError("model candidate preflight directory is invalid") from exc
    entries = list(directory.iterdir())
    if {path.name for path in entries} != _expected_files():
        raise StageContractError("model candidate preflight inventory is invalid")
    try:
        checked_entries = [
            contained_run_path(
                run_dir.parent,
                run_dir.name,
                f"{OUTPUT_DIRECTORY}/{path.name}",
                kind="file",
            )
            for path in entries
        ]
    except ValueError as exc:
        raise StageContractError("model candidate preflight inventory is invalid") from exc
    total = sum(path.stat().st_size for path in checked_entries)
    if total > MAX_OUTPUT_BYTES:
        raise StageContractError("model candidate preflight exceeded its bound")
    _config()
    workbench = load_model_workbench_report(run_dir)
    identity = workbench.get("model", {}).get("identity") if isinstance(workbench.get("model"), dict) else None
    neutral = workbench.get("quality", {}).get("neutral_fidelity") if isinstance(workbench.get("quality"), dict) else None
    if not isinstance(identity, dict) or identity.get("profile_id") != MODEL_PROFILE_ID or not isinstance(neutral, dict) or neutral.get("status") != "pass":
        raise StageContractError("model candidate requires a fidelity-passing active model profile")
    backend = _load_pillow()
    motion = _verified_motion_recomputation(run_dir, workbench, backend)
    try:
        analysis, expected_masks = _analyze_model(run_dir, workbench, motion, backend)
    finally:
        motion.close()
    for name, data in expected_masks.items():
        if read_bounded_file(directory / name, MAX_ARTIFACT_BYTES) != data:
            raise StageContractError("model candidate provenance mask does not match")
    source_data = read_bounded_file(_safe_artifact_path(run_dir, workbench["model"]["source"]), MAX_ARTIFACT_BYTES)
    rerun = _comparator_run(source_data)
    stored_normalized = read_bounded_file(directory / "normalized-raster.png", MAX_ARTIFACT_BYTES)
    stored_normalization = read_bounded_file(directory / "normalization-report.json", MAX_JSON_BYTES)
    stored_comparator = read_bounded_file(directory / "comparator-report.json", MAX_JSON_BYTES)
    if stored_normalized != rerun["normalized"] or stored_normalization != rerun["normalization_report"] or stored_comparator != rerun["report"]:
        raise StageContractError("model candidate comparator evidence does not match")
    for index, expected in enumerate(rerun["frames"]):
        if read_bounded_file(directory / f"comparator-frame-{index:03d}.png", MAX_ARTIFACT_BYTES) != expected:
            raise StageContractError("model candidate comparator frame does not match")
    source_frames = list(motion.frame_bytes)
    for index, expected in enumerate(source_frames):
        if read_bounded_file(directory / f"candidate-frame-{index:03d}.png", MAX_ARTIFACT_BYTES) != expected:
            raise StageContractError("model candidate frame does not match motion evidence")
    expected_candidate = _candidate_document(run_dir, workbench, motion, analysis, stored_normalized, stored_normalization, source_frames)
    stored_candidate_data = read_bounded_file(directory / REPORT_NAME, MAX_JSON_BYTES)
    expected_candidate_data = canonical_json_bytes(expected_candidate)
    if stored_candidate_data != expected_candidate_data:
        raise StageContractError("model candidate report does not match validated evidence")
    stored_candidate = strict_load_json_bytes(stored_candidate_data)
    if not isinstance(stored_candidate, dict):
        raise StageContractError("model candidate report is invalid")
    comparator_value = strict_load_json_bytes(stored_comparator)
    if not isinstance(comparator_value, dict):
        raise StageContractError("model candidate comparator report is invalid")
    validate_arm_parity(arm_identity_from_report(stored_candidate), arm_identity_from_report(comparator_value))
    expected_preflight = _preflight_document(run_dir, stored_candidate_data, stored_comparator, stored_normalized, stored_normalization)
    stored_preflight_data = read_bounded_file(directory / PREFLIGHT_REPORT_NAME, MAX_JSON_BYTES)
    expected_preflight_data = canonical_json_bytes(expected_preflight)
    if stored_preflight_data != expected_preflight_data:
        raise StageContractError("model candidate preflight report does not match validated evidence")
    stored_preflight = strict_load_json_bytes(stored_preflight_data)
    if not isinstance(stored_preflight, dict):
        raise StageContractError("model candidate preflight report is invalid")
    return expected_preflight
