"""GUI adapter for importing validated See-through model results."""

from __future__ import annotations

import io
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Callable

from .contracts import StageContractError, StageStatus
from .local_workbench import (
    MAX_SOURCE_BYTES,
    _descriptor,
    _find_output,
    _normalization_config,
    _stage_outputs,
)
from .model_psd_validator import validate_model_psd
from .model_worker import (
    ENTRYPOINT_ROOT,
    LEGACY_DEPENDENCIES_SHA256,
    LEGACY_ENTRYPOINT_SHA256,
    LEGACY_PROFILE_ID,
    LEGACY_PROFILE_SHA256,
    LEGACY_SOURCE_PRESERVE_ENTRYPOINT_SHA256,
    LEGACY_SOURCE_PRESERVE_PROFILE_ID,
    LEGACY_SOURCE_PRESERVE_PROFILE_SHA256,
    LEGACY_UPSTREAM_COMMIT,
    MAX_MODEL_RESULT_BYTES,
    MODEL_PART_NAMES,
    PROFILE_ID,
    SOURCE_PRESERVE_ALGORITHM_ID,
    _load_profile,
    _validated_entrypoint,
    run_model_worker,
)
from .raster import _load_pillow, build_raster_registry
from .runner import PipelineRunner
from .runtime import (
    ID_RE,
    MAX_JSON_BYTES,
    SHA256_RE,
    canonical_json_bytes,
    contained_run_path,
    contained_workspace_path,
    prepare_regular_directory,
    read_bounded_file,
    sha256_bytes,
    sha256_file,
    strict_load_json_bytes,
)

MODEL_PHASES = (
    "UPLOAD_RECEIVED",
    "RASTER_NORMALIZE",
    "PINNED_MODEL_INFERENCE",
    "MODEL_ARTIFACT_VALIDATE",
    "MODEL_RESULT_PUBLISH",
)
IMPORTED_MODEL_PHASES = tuple(phase for phase in MODEL_PHASES if phase != "RASTER_NORMALIZE")
MODEL_TIMEOUT_SECONDS = 3600
MODEL_RESULT_NAME = "model-result.json"
WORKBENCH_REPORT_NAME = "workbench-report.json"
ROOT = Path(__file__).resolve().parents[2]
MODEL_CANVAS_SIZE = 1280
MODEL_VISIBLE_ALPHA_THRESHOLD = 15
MODEL_NEUTRAL_EXACT_RATIO_MINIMUM = 0.995
MODEL_NEUTRAL_RGB_MAE_MAXIMUM = 0.5
TRUSTED_MODEL_SOURCE_NAME = "trusted-model-source.png"
TRUSTED_MODEL_SOURCE_CANONICALIZATION = "transparent-center-pad-pillow-bilinear-up-box-down.v1"
MODEL_SOURCE_REASON_CODES = frozenset(
    {
        "MODEL_TRUSTED_SOURCE_EVIDENCE_MISSING",
        "MODEL_TRUSTED_SOURCE_IDENTITY_MISMATCH",
        "MODEL_TRUSTED_SOURCE_CANONICAL_MISMATCH",
        "MODEL_SOURCE_REFERENCE_RGBA_MISMATCH",
    }
)
NORMALIZATION_SOURCE_URI = "inputs/source.bin"
NORMALIZATION_RASTER_URI = "committed/stage.raster-normalize/attempt.001/normalized.png"
NORMALIZATION_REPORT_URI = "committed/stage.raster-normalize/attempt.001/normalization-report.json"
NORMALIZATION_ARTIFACT_LIMITS = {
    NORMALIZATION_SOURCE_URI: MAX_SOURCE_BYTES,
    NORMALIZATION_RASTER_URI: 64 * 1024 * 1024,
    NORMALIZATION_REPORT_URI: MAX_JSON_BYTES,
}
NORMALIZATION_DIRECTORY_INVENTORY = {
    "inputs": frozenset({"source.bin"}),
    "committed": frozenset({"stage.raster-normalize"}),
    "committed/stage.raster-normalize": frozenset({"attempt.001"}),
    "committed/stage.raster-normalize/attempt.001": frozenset(
        {"normalized.png", "normalization-report.json"}
    ),
}


def _publish_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(canonical_json_bytes(value))
    os.replace(temporary, path)


def _publish_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        temporary.write_bytes(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalization_run_spec(source: bytes, media_type: str, normalize: bytes) -> bytes:
    return canonical_json_bytes(
        {
            "$schema": str(ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
            "format": "oneclick2d.gate-f-run-spec",
            "format_version": "0.1.0",
            "scope": "disposable-gate-f-spike",
            "execution_profile": "python-pillow-12.1.0-in-process-v1",
            "root_seed_u64": "00000000000000000042",
            "source": {
                "role": "source_raster",
                "sha256": sha256_bytes(source),
                "media_type": media_type,
                "max_bytes": MAX_SOURCE_BYTES,
            },
            "expected_result_role": "raster_normalization_report",
            "stages": [
                {
                    "id": "stage.raster-normalize",
                    "stage_type": "oc2d.spike.raster-normalize",
                    "adapter_id": "raster.normalize.pillow.v1",
                    "config_uri": "configs/normalize.json",
                    "config_sha256": sha256_bytes(normalize),
                    "limits": {
                        "max_wall_time_ms": 120_000,
                        "max_cpu_time_ms": 120_000,
                        "max_peak_ram_bytes": 1_073_741_824,
                        "max_scratch_bytes": 1_048_576,
                        "max_output_bytes": 64 * 1024 * 1024,
                        "max_output_files": 2,
                        "max_peak_vram_bytes": 0,
                        "gpu_allowed": False,
                    },
                }
            ],
        }
    )


def _normalize_upload(
    workspace_root: Path,
    run_id: str,
    source_bytes: bytes,
    media_type: str,
    notify: Callable[[str, str], None],
) -> tuple[Path, dict[str, object], dict[str, object]]:
    normalize = _normalization_config()
    with tempfile.TemporaryDirectory() as temporary:
        fixture = Path(temporary)
        (fixture / "configs").mkdir()
        source_path = fixture / "source.bin"
        source_path.write_bytes(source_bytes)
        (fixture / "configs" / "normalize.json").write_bytes(normalize)
        spec = fixture / "run-spec.json"
        spec.write_bytes(_normalization_run_spec(source_bytes, media_type, normalize))

        def observe(stage_id: str, event: str, status: StageStatus | None) -> None:
            if stage_id != "stage.raster-normalize":
                return
            if event == "started":
                notify("RASTER_NORMALIZE", "running")
            else:
                notify(
                    "RASTER_NORMALIZE",
                    "completed" if status is StageStatus.SUCCEEDED else "blocked" if status is StageStatus.BLOCKED else "failed",
                )

        status, manifest_path = PipelineRunner(build_raster_registry(), workspace_root).run(
            spec_path=spec,
            source_path=source_path,
            run_id=run_id,
            source_revision="source.local-model-workbench",
            build_id="build.local-model-workbench",
            stage_observer=observe,
        )
    if status is not StageStatus.SUCCEEDED:
        raise StageContractError("model workbench normalization did not complete")
    manifest = strict_load_json_bytes(read_bounded_file(manifest_path))
    if not isinstance(manifest, dict):
        raise StageContractError("model workbench normalization manifest is invalid")
    outputs = _stage_outputs(manifest, 0)
    normalized_value = _find_output(outputs, role="normalized_raster")
    report_value = _find_output(outputs, role="raster_normalization_report")
    if normalized_value is None or report_value is None:
        raise StageContractError("model workbench normalization output is incomplete")
    run_dir = manifest_path.parent
    normalized = _descriptor(run_dir, normalized_value, "normalized", "image")
    report_descriptor = _descriptor(run_dir, report_value, "normalization-report", "json")
    report = strict_load_json_bytes(read_bounded_file(run_dir / str(report_descriptor["uri"])))
    if not isinstance(report, dict):
        raise StageContractError("model workbench normalization report is invalid")
    return run_dir, normalized, report


def _indexed_files(run_dir: Path, result: dict[str, object]) -> dict[str, tuple[Path, dict[str, object]]]:
    values = result.get("files")
    try:
        output_root = contained_workspace_path(run_dir, "model-output", kind="directory")
    except ValueError as exc:
        raise StageContractError("model workbench file inventory is invalid") from exc
    if not isinstance(values, list) or not values:
        raise StageContractError("model workbench file inventory is invalid")

    descriptors: dict[str, dict[str, object]] = {}
    total = 0
    for value in values:
        if not isinstance(value, dict) or set(value) != {"uri", "byte_length", "sha256"}:
            raise StageContractError("model workbench file descriptor is invalid")
        uri = value.get("uri")
        length = value.get("byte_length")
        digest = value.get("sha256")
        uri_parts = uri.split("/") if isinstance(uri, str) else ()
        if (
            not isinstance(uri, str)
            or not uri
            or "\\" in uri
            or uri.startswith("/")
            or any(part in {"", ".", ".."} or ":" in part for part in uri_parts)
            or isinstance(length, bool)
            or not isinstance(length, int)
            or not 0 <= length <= MAX_MODEL_RESULT_BYTES
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or uri in descriptors
        ):
            raise StageContractError("model workbench file descriptor is invalid")
        total += length
        if total > MAX_MODEL_RESULT_BYTES:
            raise StageContractError("model workbench output exceeded its bound")
        descriptors[uri] = value

    actual: dict[str, Path] = {}
    pending = [output_root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name)
        except (OSError, RuntimeError) as exc:
            raise StageContractError("model workbench output contains an unsafe entry") from exc
        for path in entries:
            relative = path.relative_to(output_root).as_posix()
            try:
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    kind = "directory"
                elif stat.S_ISREG(info.st_mode):
                    kind = "file"
                else:
                    raise ValueError("model output entry is not regular")
                contained_workspace_path(run_dir, f"model-output/{relative}", kind=kind)
            except (OSError, RuntimeError, ValueError) as exc:
                message = (
                    "model workbench artifact does not match its inventory"
                    if relative in descriptors or any(uri.startswith(f"{relative}/") for uri in descriptors)
                    else "model workbench output contains an unsafe entry"
                )
                raise StageContractError(message) from exc
            if kind == "directory":
                pending.append(path)
            else:
                actual[relative] = path

    described = set(descriptors)
    discovered = set(actual)
    if described - discovered:
        raise StageContractError("model workbench artifact does not match its inventory")
    if discovered - described:
        raise StageContractError("model workbench file inventory is incomplete")

    indexed: dict[str, tuple[Path, dict[str, object]]] = {}
    for uri, descriptor in descriptors.items():
        path = actual[uri]
        try:
            path = contained_workspace_path(run_dir, f"model-output/{uri}", kind="file")
            length = path.stat().st_size
        except (OSError, RuntimeError, ValueError) as exc:
            raise StageContractError("model workbench artifact does not match its inventory") from exc
        if length != descriptor["byte_length"] or sha256_file(path) != descriptor["sha256"]:
            raise StageContractError("model workbench artifact does not match its inventory")
        indexed[uri] = (path, descriptor)
    return indexed


def _artifact(
    run_dir: Path,
    indexed: dict[str, tuple[Path, dict[str, object]]],
    uri: str,
    artifact_id: str,
    kind: str,
    media_type: str,
) -> dict[str, object]:
    item = indexed.get(uri)
    if item is None:
        raise StageContractError("model workbench artifact is missing")
    path, descriptor = item
    return {
        "id": artifact_id,
        "kind": kind,
        "media_type": media_type,
        "uri": path.relative_to(run_dir).as_posix(),
        "sha256": descriptor["sha256"],
        "byte_length": descriptor["byte_length"],
    }


def _png_facts(path: Path, expected_mode: str | tuple[str, ...]) -> dict[str, object]:
    from PIL import Image

    modes = (expected_mode,) if isinstance(expected_mode, str) else expected_mode
    try:
        with Image.open(path, formats=("PNG",)) as image:
            image.load()
            if image.format != "PNG" or image.mode not in modes or image.width <= 0 or image.height <= 0:
                raise StageContractError("model workbench PNG is outside its profile")
            return {"width": image.width, "height": image.height, "mode": image.mode}
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError("model workbench PNG is invalid") from exc


def _rgba_pixels(data: bytes, expected_size: tuple[int, int], label: str) -> bytes:
    try:
        backend = _load_pillow()
        with backend.Image.open(io.BytesIO(data), formats=("PNG",)) as image:
            if (
                image.format != "PNG"
                or image.mode != "RGBA"
                or image.size != expected_size
                or getattr(image, "n_frames", 1) != 1
            ):
                raise StageContractError(f"model workbench {label} is outside its profile")
            image.load()
            return image.tobytes()
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError(f"model workbench {label} is invalid") from exc


def _canonical_source_png_bytes(source_path: Path) -> bytes:
    try:
        source_data = read_bounded_file(source_path, NORMALIZATION_ARTIFACT_LIMITS[NORMALIZATION_RASTER_URI])
        backend = _load_pillow()
        with backend.Image.open(io.BytesIO(source_data), formats=("PNG",)) as image:
            if (
                image.format != "PNG"
                or image.mode != "RGBA"
                or image.width <= 0
                or image.height <= 0
                or image.width > 2048
                or image.height > 2048
                or image.width * image.height > 4_194_304
                or getattr(image, "n_frames", 1) != 1
            ):
                raise StageContractError("model workbench normalized source is outside its profile")
            image.load()
            normalized = image.copy()

        side = max(normalized.size)
        canvas = backend.Image.new("RGBA", (side, side), (0, 0, 0, 0))
        try:
            canvas.paste(normalized, ((side - normalized.width) // 2, (side - normalized.height) // 2))
            if side == MODEL_CANVAS_SIZE:
                canonical = canvas.copy()
            else:
                resample = (
                    backend.Image.Resampling.BILINEAR
                    if side < MODEL_CANVAS_SIZE
                    else backend.Image.Resampling.BOX
                )
                channels = canvas.split()
                resized_channels = []
                try:
                    resized_channels = [
                        channel.resize((MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), resample=resample)
                        for channel in channels
                    ]
                    canonical = backend.Image.merge("RGBA", resized_channels)
                finally:
                    for channel in (*channels, *resized_channels):
                        channel.close()
        finally:
            normalized.close()
            canvas.close()

        try:
            stream = io.BytesIO()
            pnginfo = backend.PngImagePlugin.PngInfo()
            pnginfo.add(b"sRGB", b"\x00")
            canonical.save(
                stream,
                format="PNG",
                optimize=False,
                compress_level=9,
                pnginfo=pnginfo,
                icc_profile=None,
                exif=b"",
            )
            return stream.getvalue()
        finally:
            canonical.close()
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError("model workbench canonical source construction failed") from exc


def _trusted_source_artifact(
    run_dir: Path,
) -> tuple[Path, dict[str, object], bytes] | None:
    candidate = run_dir / TRUSTED_MODEL_SOURCE_NAME
    if not candidate.exists() and not candidate.is_symlink():
        return None
    try:
        path = contained_run_path(
            run_dir.parent,
            run_dir.name,
            TRUSTED_MODEL_SOURCE_NAME,
            kind="file",
        )
    except ValueError as exc:
        raise StageContractError("model workbench trusted source path is invalid") from exc
    data = read_bounded_file(path, NORMALIZATION_ARTIFACT_LIMITS[NORMALIZATION_RASTER_URI])
    pixels = _rgba_pixels(data, (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), "trusted source")
    return (
        path,
        {
            "id": "trusted-model-source",
            "kind": "trusted-model-source",
            "media_type": "image/png",
            "uri": TRUSTED_MODEL_SOURCE_NAME,
            "sha256": sha256_bytes(data),
            "byte_length": len(data),
            "width": MODEL_CANVAS_SIZE,
            "height": MODEL_CANVAS_SIZE,
            "mode": "RGBA",
        },
        pixels,
    )


def _normalization_artifact_path(run_dir: Path, normalization: dict[str, object]) -> Path:
    if (
        set(normalization) != {"id", "kind", "media_type", "uri", "sha256", "byte_length"}
        or normalization.get("id") != "normalized"
        or normalization.get("kind") != "image"
        or normalization.get("media_type") != "image/png"
        or normalization.get("uri") != NORMALIZATION_RASTER_URI
        or not isinstance(normalization.get("sha256"), str)
        or SHA256_RE.fullmatch(str(normalization["sha256"])) is None
        or isinstance(normalization.get("byte_length"), bool)
        or not isinstance(normalization.get("byte_length"), int)
    ):
        raise StageContractError("model workbench normalized source descriptor is invalid")
    try:
        path = contained_run_path(
            run_dir.parent,
            run_dir.name,
            NORMALIZATION_RASTER_URI,
            kind="file",
        )
    except ValueError as exc:
        raise StageContractError("model workbench normalized source path is invalid") from exc
    data = read_bounded_file(path, NORMALIZATION_ARTIFACT_LIMITS[NORMALIZATION_RASTER_URI])
    if len(data) != normalization["byte_length"] or sha256_bytes(data) != normalization["sha256"]:
        raise StageContractError("model workbench normalized source artifact does not match")
    return path


def _source_trust(
    run_dir: Path,
    result: dict[str, object],
    model_source_path: Path,
    normalization: dict[str, object] | None,
) -> tuple[dict[str, object] | None, dict[str, object], Path]:
    model_source_data = read_bounded_file(
        model_source_path,
        NORMALIZATION_ARTIFACT_LIMITS[NORMALIZATION_RASTER_URI],
    )
    model_source_pixels = _rgba_pixels(
        model_source_data,
        (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE),
        "model source reference",
    )
    model_source_rgba_sha256 = sha256_bytes(model_source_pixels)
    trusted = _trusted_source_artifact(run_dir)
    reasons: list[str] = []
    normalized_source_sha256: str | None = None

    if trusted is None:
        reasons.append("MODEL_TRUSTED_SOURCE_EVIDENCE_MISSING")
        trusted_descriptor = None
        trusted_rgba_sha256 = None
        exact_reference_match = False
        fidelity_source_path = model_source_path
    else:
        trusted_path, trusted_descriptor, trusted_pixels = trusted
        trusted_rgba_sha256 = sha256_bytes(trusted_pixels)
        fidelity_source_path = trusted_path
        if trusted_descriptor["sha256"] != result.get("source_sha256"):
            reasons.append("MODEL_TRUSTED_SOURCE_IDENTITY_MISMATCH")
        if normalization is not None:
            normalized_source_path = _normalization_artifact_path(run_dir, normalization)
            normalized_source_sha256 = str(normalization["sha256"])
            canonical_data = _canonical_source_png_bytes(normalized_source_path)
            canonical_pixels = _rgba_pixels(
                canonical_data,
                (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE),
                "canonical source",
            )
            if canonical_pixels != trusted_pixels:
                reasons.append("MODEL_TRUSTED_SOURCE_CANONICAL_MISMATCH")
        exact_reference_match = trusted_pixels == model_source_pixels
        if not exact_reference_match:
            reasons.append("MODEL_SOURCE_REFERENCE_RGBA_MISMATCH")

    if any(reason not in MODEL_SOURCE_REASON_CODES for reason in reasons):
        raise StageContractError("model workbench source trust reason is invalid")
    evidence = {
        "status": "pass" if not reasons else "review_required",
        "reason_codes": reasons,
        "canonicalization_algorithm": TRUSTED_MODEL_SOURCE_CANONICALIZATION,
        "evidence_origin": (
            "missing"
            if trusted is None
            else "retained_normalized_input"
            if normalization is not None
            else "imported_retained_model_input"
        ),
        "normalized_source_sha256": normalized_source_sha256,
        "trusted_source_rgba_sha256": trusted_rgba_sha256,
        "model_source_reference_rgba_sha256": model_source_rgba_sha256,
        "exact_rgba_match": exact_reference_match,
    }
    return trusted_descriptor, evidence, fidelity_source_path


def _rgb_mismatch_mask(difference: object) -> object:
    from PIL import ImageChops

    channels = difference.split()
    red_green = ImageChops.lighter(channels[0], channels[1])
    try:
        return ImageChops.lighter(red_green, channels[2])
    finally:
        red_green.close()
        for channel in channels:
            channel.close()


def _neutral_fidelity(source_path: Path, reconstruction_path: Path) -> dict[str, object]:
    from PIL import Image, ImageChops, ImageStat

    try:
        with Image.open(source_path, formats=("PNG",)) as source_image, Image.open(
            reconstruction_path,
            formats=("PNG",),
        ) as reconstruction_image:
            source_image.load()
            reconstruction_image.load()
            if source_image.size != reconstruction_image.size or source_image.size != (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE):
                raise StageContractError("model workbench neutral fidelity canvas does not match")
            source = source_image.convert("RGBA")
            reconstruction = reconstruction_image.convert("RGBA")

        try:
            with source.getchannel("A") as source_alpha, reconstruction.getchannel("A") as reconstruction_alpha:
                with source_alpha.point(
                    lambda value: 255 if value > MODEL_VISIBLE_ALPHA_THRESHOLD else 0
                ) as source_visible_mask, reconstruction_alpha.point(
                    lambda value: 255 if value > MODEL_VISIBLE_ALPHA_THRESHOLD else 0
                ) as reconstruction_visible_mask:
                    source_visible_pixels = source_visible_mask.histogram()[255]
                    reconstruction_visible_pixels = reconstruction_visible_mask.histogram()[255]
                    if source_visible_pixels <= 0:
                        raise StageContractError("model workbench source has no visible pixels")
                    with ImageChops.multiply(source_visible_mask, reconstruction_visible_mask) as covered_mask:
                        covered_pixels = covered_mask.histogram()[255]
                    coverage_ratio = covered_pixels / source_visible_pixels
                    with source.convert("RGB") as source_rgb, reconstruction.convert("RGB") as reconstruction_rgb:
                        with ImageChops.difference(source_rgb, reconstruction_rgb) as difference:
                            with _rgb_mismatch_mask(difference) as mismatch_mask:
                                channel_mae = ImageStat.Stat(difference, mask=source_visible_mask).mean
                                exact_pixels = mismatch_mask.histogram(mask=source_visible_mask)[0]
            exact_ratio = exact_pixels / source_visible_pixels
            rgb_mae = sum(channel_mae) / len(channel_mae)
            status = (
                "pass"
                if coverage_ratio == 1.0
                and exact_ratio >= MODEL_NEUTRAL_EXACT_RATIO_MINIMUM
                and rgb_mae <= MODEL_NEUTRAL_RGB_MAE_MAXIMUM
                else "review_required"
            )
            return {
                "status": status,
                "alpha_threshold": MODEL_VISIBLE_ALPHA_THRESHOLD,
                "visible_pixel_count": reconstruction_visible_pixels,
                "visible_canvas_ratio": round(
                    reconstruction_visible_pixels / (MODEL_CANVAS_SIZE * MODEL_CANVAS_SIZE),
                    6,
                ),
                "source_visible_pixel_count": source_visible_pixels,
                "source_visible_canvas_ratio": round(
                    source_visible_pixels / (MODEL_CANVAS_SIZE * MODEL_CANVAS_SIZE),
                    6,
                ),
                "reconstruction_visible_pixel_count": reconstruction_visible_pixels,
                "reconstruction_visible_canvas_ratio": round(
                    reconstruction_visible_pixels / (MODEL_CANVAS_SIZE * MODEL_CANVAS_SIZE),
                    6,
                ),
                "source_visible_covered_pixel_count": covered_pixels,
                "source_visible_omission_count": source_visible_pixels - covered_pixels,
                "source_visible_coverage_ratio": round(coverage_ratio, 6),
                "source_rgb_exact_ratio": round(exact_ratio, 6),
                "source_rgb_mae": round(rgb_mae, 6),
                "source_rgb_channel_mae": [round(value, 6) for value in channel_mae],
                "pass_thresholds": {
                    "source_visible_coverage_ratio_minimum": 1.0,
                    "source_rgb_exact_ratio_minimum": MODEL_NEUTRAL_EXACT_RATIO_MINIMUM,
                    "source_rgb_mae_maximum": MODEL_NEUTRAL_RGB_MAE_MAXIMUM,
                },
            }
        finally:
            source.close()
            reconstruction.close()
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError("model workbench neutral fidelity evidence is invalid") from exc


def _strict_json(indexed: dict[str, tuple[Path, dict[str, object]]], uri: str) -> dict[str, object]:
    item = indexed.get(uri)
    if item is None:
        raise StageContractError("model workbench JSON artifact is missing")
    value = strict_load_json_bytes(read_bounded_file(item[0]))
    if not isinstance(value, dict):
        raise StageContractError("model workbench JSON artifact is invalid")
    return value


def _psd_layer_signature(structure: object) -> tuple[tuple[object, ...], ...]:
    layers = getattr(structure, "layers", ())
    return tuple((layer.name, layer.top, layer.left, layer.bottom, layer.right) for layer in layers)


def _validate_psd_metadata(
    indexed: dict[str, tuple[Path, dict[str, object]]],
    structure: object,
) -> None:
    metadata = _strict_json(indexed, "input/input.psd.json")
    parts = metadata.get("parts")
    if set(metadata) != {"parts", "frame_size"} or metadata.get("frame_size") != [MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE] or not isinstance(parts, dict):
        raise StageContractError("model workbench PSD metadata is invalid")
    layers = getattr(structure, "layers", ())
    if set(parts) != {layer.name for layer in layers}:
        raise StageContractError("model workbench PSD metadata does not match its layers")
    for layer in layers:
        value = parts.get(layer.name)
        allowed_keys = {"xyxy", "tag", "depth_median", "part_id"}
        base_name = layer.name[:-2] if layer.name.endswith(("-l", "-r")) else layer.name
        if (
            not isinstance(value, dict)
            or not {"xyxy", "tag", "depth_median"}.issubset(value)
            or not set(value).issubset(allowed_keys)
            or value.get("tag") != layer.name
            or base_name not in MODEL_PART_NAMES
            or value.get("xyxy") != [layer.left, layer.top, layer.right, layer.bottom]
        ):
            raise StageContractError("model workbench PSD metadata does not match its layers")
        median = value.get("depth_median")
        part_id = value.get("part_id")
        if (
            isinstance(median, bool)
            or not isinstance(median, (int, float))
            or not 0 <= float(median) <= 1
            or (part_id is not None and (isinstance(part_id, bool) or not isinstance(part_id, int) or part_id < 0))
        ):
            raise StageContractError("model workbench PSD metadata is invalid")


def _manifest_artifact_descriptor(value: object) -> tuple[str, int, str]:
    if not isinstance(value, dict) or set(value) != {"role", "media_type", "uri", "sha256", "byte_length"}:
        raise StageContractError("model workbench normalization manifest descriptor is invalid")
    uri = value.get("uri")
    length = value.get("byte_length")
    digest = value.get("sha256")
    maximum = NORMALIZATION_ARTIFACT_LIMITS.get(uri) if isinstance(uri, str) else None
    if (
        not isinstance(value.get("role"), str)
        or not isinstance(value.get("media_type"), str)
        or maximum is None
        or isinstance(length, bool)
        or not isinstance(length, int)
        or not 0 <= length <= maximum
        or not isinstance(digest, str)
        or SHA256_RE.fullmatch(digest) is None
    ):
        raise StageContractError("model workbench normalization manifest descriptor is invalid")
    return uri, length, digest


def _normalization_inventory_paths(run_dir: Path, uris: list[str]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    try:
        for uri in uris:
            paths[uri] = contained_run_path(run_dir.parent, run_dir.name, uri, kind="file")
        for relative, expected_names in NORMALIZATION_DIRECTORY_INVENTORY.items():
            directory = contained_run_path(run_dir.parent, run_dir.name, relative, kind="directory")
            entries = list(directory.iterdir())
            if {entry.name for entry in entries} != expected_names:
                raise StageContractError("model workbench normalization manifest inventory is not exact")
            for entry in entries:
                child_relative = f"{relative}/{entry.name}"
                child_kind = "directory" if child_relative in NORMALIZATION_DIRECTORY_INVENTORY else "file"
                contained_run_path(run_dir.parent, run_dir.name, child_relative, kind=child_kind)
    except StageContractError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise StageContractError("model workbench normalization manifest URI is invalid") from exc
    if set(paths) != set(NORMALIZATION_ARTIFACT_LIMITS):
        raise StageContractError("model workbench normalization manifest inventory is not exact")
    return paths


def _load_normalization_evidence(run_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    try:
        run_dir = contained_run_path(run_dir.parent, run_dir.name, kind="directory")
        manifest_path = contained_run_path(run_dir.parent, run_dir.name, "run-manifest.json", kind="file")
        digest_path = contained_run_path(run_dir.parent, run_dir.name, "run-manifest.sha256", kind="file")
    except ValueError as exc:
        raise StageContractError("model workbench normalization manifest path is invalid") from exc

    manifest_bytes = read_bounded_file(manifest_path, MAX_JSON_BYTES)
    expected_digest = (sha256_bytes(manifest_bytes) + "\n").encode("ascii")
    if read_bounded_file(digest_path, 65) != expected_digest:
        raise StageContractError("model workbench normalization manifest digest does not match")
    manifest = strict_load_json_bytes(manifest_bytes)
    stages = manifest.get("stages") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "oneclick2d.run-manifest"
        or manifest.get("run_id") != run_dir.name
        or manifest.get("terminal_status") != "succeeded"
        or not isinstance(stages, list)
        or len(stages) != 1
        or not isinstance(stages[0], dict)
        or stages[0].get("id") != "stage.raster-normalize"
        or stages[0].get("status") != "succeeded"
    ):
        raise StageContractError("model workbench normalization manifest is invalid")
    outputs = _stage_outputs(manifest, 0)
    normalized_value = _find_output(outputs, role="normalized_raster")
    report_value = _find_output(outputs, role="raster_normalization_report")
    if normalized_value is None or report_value is None:
        raise StageContractError("model workbench normalization output is incomplete")

    source_value = manifest.get("source")
    result_value = manifest.get("result")
    manifest_descriptors = [source_value, *outputs, result_value]
    lexical = [_manifest_artifact_descriptor(value) for value in manifest_descriptors]
    uris = [item[0] for item in lexical]
    if (
        len(outputs) != 2
        or uris != [NORMALIZATION_SOURCE_URI, NORMALIZATION_RASTER_URI, NORMALIZATION_REPORT_URI, NORMALIZATION_REPORT_URI]
        or source_value.get("role") != "source_raster"
        or normalized_value.get("role") != "normalized_raster"
        or normalized_value.get("media_type") != "image/png"
        or report_value.get("role") != "raster_normalization_report"
        or report_value.get("media_type") != "application/vnd.oneclick2d.raster-normalization-report+json"
        or result_value != report_value
    ):
        raise StageContractError("model workbench normalization manifest inventory is not exact")

    paths = _normalization_inventory_paths(run_dir, uris)

    verified: dict[str, bytes] = {}
    for uri, length, digest in lexical:
        if uri in verified:
            continue
        try:
            data = read_bounded_file(paths[uri], NORMALIZATION_ARTIFACT_LIMITS[uri])
        except (OSError, ValueError, TypeError) as exc:
            raise StageContractError("model workbench normalization artifact does not match the manifest") from exc
        if len(data) != length or sha256_bytes(data) != digest:
            raise StageContractError("model workbench normalization artifact does not match the manifest")
        verified[uri] = data

    normalized = {
        "id": "normalized",
        "kind": "image",
        "media_type": normalized_value["media_type"],
        "uri": normalized_value["uri"],
        "sha256": normalized_value["sha256"],
        "byte_length": normalized_value["byte_length"],
    }
    report_descriptor = {
        "id": "normalization-report",
        "kind": "json",
        "media_type": report_value["media_type"],
        "uri": report_value["uri"],
        "sha256": report_value["sha256"],
        "byte_length": report_value["byte_length"],
    }
    report = strict_load_json_bytes(verified[str(report_descriptor["uri"])])
    if not isinstance(report, dict):
        raise StageContractError("model workbench normalization report is invalid")
    return normalized, report


def _identity(result: dict[str, object]) -> dict[str, object]:
    if result.get("profile_id") == LEGACY_PROFILE_ID:
        legacy_entrypoint = ENTRYPOINT_ROOT / "see_through_v3_nf4.py"
        if (
            result.get("profile_sha256") != LEGACY_PROFILE_SHA256
            or result.get("dependencies_sha256") != LEGACY_DEPENDENCIES_SHA256
            or sha256_file(legacy_entrypoint) != LEGACY_ENTRYPOINT_SHA256
        ):
            raise StageContractError("legacy model workbench profile identity does not match")
        return {
            "profile_id": LEGACY_PROFILE_ID,
            "profile_sha256": LEGACY_PROFILE_SHA256,
            "dependencies_sha256": LEGACY_DEPENDENCIES_SHA256,
            "upstream_commit": LEGACY_UPSTREAM_COMMIT,
            "entrypoint_sha256": LEGACY_ENTRYPOINT_SHA256,
            "quantization": "nf4",
            "seed": 42,
            "resolution": 1280,
            "depth_resolution": 768,
            "inference_steps": 30,
            "cpu_offload": True,
            "group_offload": False,
            "postprocess_algorithm": "not_applied",
            "license_status": "supporting_weight_metadata_incomplete_no_redistribution",
        }

    if result.get("profile_id") == LEGACY_SOURCE_PRESERVE_PROFILE_ID:
        legacy_entrypoint = ENTRYPOINT_ROOT / "see_through_v3_nf4_source_preserve.py"
        if (
            result.get("profile_sha256") != LEGACY_SOURCE_PRESERVE_PROFILE_SHA256
            or result.get("dependencies_sha256") != LEGACY_DEPENDENCIES_SHA256
            or sha256_file(legacy_entrypoint) != LEGACY_SOURCE_PRESERVE_ENTRYPOINT_SHA256
        ):
            raise StageContractError("legacy source-preserve model workbench profile identity does not match")
        return {
            "profile_id": LEGACY_SOURCE_PRESERVE_PROFILE_ID,
            "profile_sha256": LEGACY_SOURCE_PRESERVE_PROFILE_SHA256,
            "dependencies_sha256": LEGACY_DEPENDENCIES_SHA256,
            "upstream_commit": LEGACY_UPSTREAM_COMMIT,
            "entrypoint_sha256": LEGACY_SOURCE_PRESERVE_ENTRYPOINT_SHA256,
            "quantization": "nf4",
            "seed": 42,
            "resolution": 1280,
            "depth_resolution": 768,
            "inference_steps": 30,
            "cpu_offload": True,
            "group_offload": False,
            "postprocess_algorithm": "source-visible-rgb-by-depth.v1",
            "license_status": "supporting_weight_metadata_incomplete_no_redistribution",
        }

    profile, profile_bytes = _load_profile()
    code = profile.get("code")
    entrypoint = profile.get("entrypoint")
    inference = profile.get("inference")
    postprocess = profile.get("postprocess")
    runtime = profile.get("runtime")
    if not isinstance(code, dict) or not isinstance(entrypoint, dict) or not isinstance(inference, dict) or not isinstance(postprocess, dict) or not isinstance(runtime, dict):
        raise StageContractError("model workbench profile identity is invalid")
    if (
        result.get("profile_id") != PROFILE_ID
        or result.get("profile_sha256") != sha256_bytes(profile_bytes)
        or result.get("dependencies_sha256") != runtime.get("dependencies_sha256")
    ):
        raise StageContractError("model workbench profile identity does not match")
    _validated_entrypoint(profile)
    return {
        "profile_id": PROFILE_ID,
        "profile_sha256": result["profile_sha256"],
        "dependencies_sha256": result["dependencies_sha256"],
        "upstream_commit": code.get("commit"),
        "entrypoint_sha256": entrypoint.get("sha256"),
        "quantization": inference.get("quantization"),
        "seed": inference.get("seed"),
        "resolution": inference.get("resolution"),
        "depth_resolution": inference.get("depth_resolution"),
        "inference_steps": inference.get("inference_steps"),
        "cpu_offload": inference.get("cpu_offload"),
        "group_offload": inference.get("group_offload"),
        "postprocess_algorithm": postprocess.get("algorithm_id"),
        "license_status": "supporting_weight_metadata_incomplete_no_redistribution",
    }


def build_model_workbench_report(
    run_dir: Path,
    run_id: str,
    result: dict[str, object],
    *,
    normalization: dict[str, object] | None = None,
    normalization_report: dict[str, object] | None = None,
    phases: tuple[str, ...] = IMPORTED_MODEL_PHASES,
) -> dict[str, object]:
    required = {
        "format",
        "format_version",
        "scope",
        "state",
        "profile_id",
        "profile_sha256",
        "dependencies_sha256",
        "source_sha256",
        "model_used",
        "oc2d_produced",
        "gate_f_status",
        "files",
        "psd",
    }
    if (
        not ID_RE.fullmatch(run_id)
        or set(result) != required
        or result.get("format") != "oneclick2d.model-worker-result"
        or result.get("format_version") != "0.1.0"
        or result.get("scope") != "disposable-local-model-spike"
        or result.get("state") != "completed"
        or result.get("model_used") is not True
        or result.get("oc2d_produced") is not False
        or result.get("gate_f_status") != "GATE_F_NOT_EVALUATED"
        or not isinstance(result.get("source_sha256"), str)
        or SHA256_RE.fullmatch(str(result["source_sha256"])) is None
        or not isinstance(result.get("dependencies_sha256"), str)
        or SHA256_RE.fullmatch(str(result["dependencies_sha256"])) is None
    ):
        raise StageContractError("model workbench result is invalid")

    indexed = _indexed_files(run_dir, result)
    identity = _identity(result)
    info = _strict_json(indexed, "input/input/info.json")
    parts = info.get("parts")
    if (
        not isinstance(parts, dict)
        or set(parts) != set(MODEL_PART_NAMES)
        or any(not isinstance(value, dict) for value in parts.values())
    ):
        raise StageContractError("model workbench semantic inventory is invalid")
    part_names = list(MODEL_PART_NAMES)
    semantic_prefix = "input/input/"
    special = {"src_img.png", "src_head.png", "reconstruction.png"}
    semantic_names = {
        Path(uri).stem
        for uri in indexed
        if uri.startswith(semantic_prefix)
        and uri.count("/") == 2
        and uri.endswith(".png")
        and Path(uri).name not in special
        and not Path(uri).stem.endswith("_depth")
    }
    depth_names = {
        Path(uri).stem.removesuffix("_depth")
        for uri in indexed
        if uri.startswith(semantic_prefix)
        and uri.count("/") == 2
        and uri.endswith("_depth.png")
    }
    if semantic_names != set(part_names) | {"head"} or depth_names != set(part_names):
        raise StageContractError("model workbench semantic inventory is incomplete")
    expected_uris = {
        "input/input.psd",
        "input/input_depth.psd",
        "input/input.psd.json",
        "input/input/info.json",
        "input/input/stats.json",
        "input/input/reconstruction.png",
        "input/input/src_head.png",
        "input/input/src_img.png",
        *(f"input/input/{name}.png" for name in semantic_names),
        *(f"input/input/{name}_depth.png" for name in depth_names),
    }
    if set(indexed) != expected_uris:
        raise StageContractError("model workbench file inventory is outside its fixed profile")
    ordered_names = list(part_names)
    ordered_names.insert(2 if len(ordered_names) >= 2 else len(ordered_names), "head")

    source = _artifact(
        run_dir,
        indexed,
        "input/input/src_img.png",
        "model-source",
        "model-input-evidence",
        "image/png",
    )
    source.update(_png_facts(indexed["input/input/src_img.png"][0], "RGBA"))
    if (source["width"], source["height"]) != (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE):
        raise StageContractError("model workbench source canvas is outside its profile")

    reconstruction = _artifact(
        run_dir,
        indexed,
        "input/input/reconstruction.png",
        "model-reconstruction",
        "reconstruction",
        "image/png",
    )
    reconstruction.update(_png_facts(indexed["input/input/reconstruction.png"][0], "RGBA"))
    if (reconstruction["width"], reconstruction["height"]) != (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE):
        raise StageContractError("model workbench reconstruction canvas is outside its profile")
    for source_name in ("src_img.png", "src_head.png"):
        facts = _png_facts(indexed[f"input/input/{source_name}"][0], "RGBA")
        if (facts["width"], facts["height"]) != (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE):
            raise StageContractError("model workbench source canvas is outside its profile")

    layers: list[dict[str, object]] = []
    for index, name in enumerate(ordered_names):
        semantic_uri = f"input/input/{name}.png"
        semantic = _artifact(run_dir, indexed, semantic_uri, f"model-layer-{index:02d}", "semantic-rgba", "image/png")
        semantic_facts = _png_facts(indexed[semantic_uri][0], "RGBA")
        semantic.update(semantic_facts)
        if (semantic["width"], semantic["height"]) != (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE):
            raise StageContractError("model workbench semantic canvas is outside its profile")
        depth = None
        depth_uri = f"input/input/{name}_depth.png"
        if depth_uri in indexed:
            depth = _artifact(run_dir, indexed, depth_uri, f"model-depth-{index:02d}", "semantic-depth", "image/png")
            depth.update(_png_facts(indexed[depth_uri][0], "L"))
            if (depth["width"], depth["height"]) != (semantic["width"], semantic["height"]):
                raise StageContractError("model workbench depth canvas does not match its semantic layer")
        layers.append({"index": index, "name": name, "artifact": semantic, "depth_artifact": depth})

    psd_value = result.get("psd")
    if not isinstance(psd_value, dict) or set(psd_value) != {"uri", "byte_length", "sha256"}:
        raise StageContractError("model workbench PSD descriptor is invalid")
    psd_uri = str(psd_value.get("uri"))
    if psd_uri != "input/input.psd":
        raise StageContractError("model workbench PSD descriptor is outside its profile")
    psd = _artifact(run_dir, indexed, psd_uri, "output-psd", "model-psd", "image/vnd.adobe.photoshop")
    if psd["byte_length"] != psd_value.get("byte_length") or psd["sha256"] != psd_value.get("sha256"):
        raise StageContractError("model workbench PSD descriptor does not match")
    structure = validate_model_psd(indexed[psd_uri][0])
    if (
        (structure.width, structure.height) != (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE)
        or structure.byte_length != psd["byte_length"]
        or structure.sha256 != psd["sha256"]
    ):
        raise StageContractError("model workbench PSD structure does not match its profile")
    psd.update(
        {
            "width": structure.width,
            "height": structure.height,
            "layer_count": len(structure.layers),
            "structural_readback_pass": True,
            "external_editor_status": "not_evaluated",
        }
    )
    depth_psd = _artifact(
        run_dir,
        indexed,
        "input/input_depth.psd",
        "output-depth-psd",
        "model-depth-psd",
        "image/vnd.adobe.photoshop",
    )
    depth_structure = validate_model_psd(indexed["input/input_depth.psd"][0], profile="grayscale")
    if (
        (depth_structure.width, depth_structure.height) != (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE)
        or depth_structure.byte_length != depth_psd["byte_length"]
        or depth_structure.sha256 != depth_psd["sha256"]
        or _psd_layer_signature(depth_structure) != _psd_layer_signature(structure)
    ):
        raise StageContractError("model workbench depth PSD does not match the primary PSD")
    _validate_psd_metadata(indexed, structure)
    depth_psd.update(
        {
            "width": depth_structure.width,
            "height": depth_structure.height,
            "layer_count": len(depth_structure.layers),
            "structural_readback_pass": True,
            "external_editor_status": "not_evaluated",
        }
    )

    stats = _strict_json(indexed, "input/input/stats.json")
    timing_keys = ("layerdiff_time_s", "marigold_time_s", "psd_time_s")
    if set(stats) != {"quant_mode", "peak_vram_gb", *timing_keys, "total_time_s"} or stats.get("quant_mode") != "nf4" or any(
        isinstance(stats.get(key), bool) or not isinstance(stats.get(key), (int, float)) or float(stats[key]) < 0
        for key in ("peak_vram_gb", "layerdiff_time_s", "marigold_time_s", "psd_time_s", "total_time_s")
    ) or float(stats["total_time_s"]) < sum(float(stats[key]) for key in timing_keys):
        raise StageContractError("model workbench statistics are invalid")

    if normalization is not None:
        if not isinstance(normalization_report, dict):
            raise StageContractError("model workbench normalized source identity does not match")
        normalization_value: dict[str, object] | None = {
            "input": normalization_report.get("input"),
            "output": normalization_report.get("output"),
            "orientation": normalization_report.get("orientation"),
            "color_policy": normalization_report.get("color_policy"),
            "finding_codes": normalization_report.get("finding_codes"),
            "artifact": normalization,
        }
    elif normalization_report is not None:
        raise StageContractError("model workbench normalization identity is incomplete")
    else:
        normalization_value = None

    trusted_source, source_trust, fidelity_source_path = _source_trust(
        run_dir,
        result,
        indexed["input/input/src_img.png"][0],
        normalization,
    )
    neutral_fidelity = _neutral_fidelity(
        fidelity_source_path,
        indexed["input/input/reconstruction.png"][0],
    )
    neutral_fidelity["source_trust_status"] = source_trust["status"]
    neutral_fidelity["reason_codes"] = list(source_trust["reason_codes"])
    if source_trust["status"] != "pass":
        neutral_fidelity["status"] = "review_required"
    review_items = [
        "semantic_correctness",
        "hidden_region_completion",
        "external_editor_interoperability",
        "mesh_generation",
        "parameter_binding",
        "dynamic_deformation",
        "oc2d_package",
    ]
    if neutral_fidelity["status"] != "pass":
        review_items.insert(0, "neutral_visible_pixel_fidelity")
    if source_trust["status"] != "pass":
        review_items.insert(0, "trusted_source_reference")

    report: dict[str, object] = {
        "format": "oneclick2d.local-image-workbench-report",
        "format_version": "0.3.0",
        "scope": "disposable-local-image-workbench",
        "run_id": run_id,
        "workflow": "model",
        "state": "completed",
        "local_status": "LOCAL_WORKBENCH_COMPLETED",
        "model_used": source_trust["status"] == "pass",
        "oc2d_produced": False,
        "gate_f_status": "GATE_F_NOT_EVALUATED",
        "source_retention": (
            "raw_upload_and_model_derived_outputs_retained_until_manual_removal"
            if normalization_value is not None
            else "trusted_model_input_and_model_derived_outputs_retained_until_manual_removal"
            if trusted_source is not None
            else "model_derived_outputs_retained_until_manual_removal"
        ),
        "phases": [{"id": phase, "state": "completed"} for phase in phases],
        "model": {
            "identity": identity,
            "source_sha256": result["source_sha256"],
            "trusted_source": trusted_source,
            "source": source,
            "reconstruction": reconstruction,
            "semantic_intermediate_count": len(layers),
            "depth_intermediate_count": sum(layer["depth_artifact"] is not None for layer in layers),
            "layers": layers,
            "stats": stats,
        },
        "quality": {
            "status": "review_required",
            "reason_codes": list(source_trust["reason_codes"]),
            "source_trust": source_trust,
            "neutral_fidelity": neutral_fidelity,
            "review_items": review_items,
        },
        "capabilities": {
            "source_comparison": "available",
            "semantic_rgba": "available",
            "semantic_depth": "available",
            "psd_internal_readback": "available",
            "semantic_correctness": "not_evaluated",
            "hidden_region_quality": "not_evaluated",
            "external_editor_validation": "not_evaluated",
            "mesh_generation": "not_generated",
            "parameter_binding": "not_generated",
            "dynamic_preview": "not_generated",
            "oc2d_package": "not_generated",
        },
        "psd": psd,
        "depth_psd": depth_psd,
    }
    if normalization_value is not None:
        report["normalization"] = normalization_value
    return report


def load_model_workbench_report(run_dir: Path) -> dict[str, object]:
    try:
        run_dir = contained_run_path(run_dir.parent, run_dir.name, kind="directory")
        result_path = contained_run_path(
            run_dir.parent,
            run_dir.name,
            MODEL_RESULT_NAME,
            kind="file",
        )
    except ValueError as exc:
        raise StageContractError("model workbench run directory is invalid") from exc
    value = strict_load_json_bytes(read_bounded_file(result_path))
    if not isinstance(value, dict):
        raise StageContractError("model workbench result is invalid")
    persisted_path = run_dir / WORKBENCH_REPORT_NAME
    persisted: dict[str, object] | None = None
    normalization = None
    normalization_report = None
    phases = IMPORTED_MODEL_PHASES
    if persisted_path.exists() or persisted_path.is_symlink():
        try:
            persisted_path = contained_run_path(
                run_dir.parent,
                run_dir.name,
                WORKBENCH_REPORT_NAME,
                kind="file",
            )
        except ValueError as exc:
            raise StageContractError("model workbench report path is invalid") from exc
        persisted_value = strict_load_json_bytes(read_bounded_file(persisted_path))
        if not isinstance(persisted_value, dict):
            raise StageContractError("model workbench report is invalid")
        persisted = persisted_value
        if "normalization" in persisted:
            normalization, normalization_report = _load_normalization_evidence(run_dir)
            phases = MODEL_PHASES
    report = build_model_workbench_report(
        run_dir,
        run_dir.name,
        value,
        normalization=normalization,
        normalization_report=normalization_report,
        phases=phases,
    )
    if persisted is not None and persisted != report:
        raise StageContractError("persisted model workbench report does not match validated evidence")
    motion_directory = run_dir / "motion-draft"
    if motion_directory.exists() or motion_directory.is_symlink():
        try:
            contained_run_path(run_dir.parent, run_dir.name, "motion-draft", kind="directory")
        except ValueError as exc:
            raise StageContractError("model motion draft directory is invalid") from exc
        from .model_motion_draft import load_model_motion_draft_report

        motion = load_model_motion_draft_report(
            run_dir,
            expected_model_result_sha256=sha256_file(result_path),
            expected_reconstruction_sha256=str(report["model"]["reconstruction"]["sha256"]),
            expected_reconstruction_uri=str(report["model"]["reconstruction"]["uri"]),
        )
        report["motion_draft"] = motion
        report["capabilities"]["mesh_generation"] = "research_draft"
        report["capabilities"]["parameter_binding"] = "research_draft"
        report["capabilities"]["dynamic_preview"] = "research_draft"
    return report


def run_normalized_model_workbench(
    workspace_root: Path,
    run_id: str,
    source_bytes: bytes,
    media_type: str,
    model_worker: Callable[..., dict[str, object]],
    *,
    timeout_seconds: int,
    phase_callback: Callable[[str, str], None] | None = None,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    if (
        not ID_RE.fullmatch(run_id)
        or media_type not in {"image/png", "image/jpeg"}
        or not 1 <= len(source_bytes) <= MAX_SOURCE_BYTES
        or not 1 <= timeout_seconds <= MODEL_TIMEOUT_SECONDS
    ):
        raise StageContractError("uploaded model workbench input is invalid")

    def notify(phase: str, state: str) -> None:
        if phase_callback is not None:
            try:
                phase_callback(phase, state)
            except Exception:
                pass

    prepare_regular_directory(workspace_root, create=True)
    notify("UPLOAD_RECEIVED", "completed")
    run_dir, normalized, normalization_report = _normalize_upload(
        workspace_root,
        run_id,
        source_bytes,
        media_type,
        notify,
    )
    normalized_path = run_dir / str(normalized["uri"])
    trusted_source_path = run_dir / TRUSTED_MODEL_SOURCE_NAME
    output = run_dir / "model-output"
    result_path = run_dir / MODEL_RESULT_NAME
    report_path = run_dir / WORKBENCH_REPORT_NAME
    try:
        _publish_bytes(trusted_source_path, _canonical_source_png_bytes(normalized_path))
        notify("PINNED_MODEL_INFERENCE", "running")
        result = model_worker(trusted_source_path, output, timeout_seconds=timeout_seconds)
        notify("PINNED_MODEL_INFERENCE", "completed")
        notify("MODEL_ARTIFACT_VALIDATE", "running")
        report = build_model_workbench_report(
            run_dir,
            run_id,
            result,
            normalization=normalized,
            normalization_report=normalization_report,
            phases=MODEL_PHASES,
        )
        notify("MODEL_ARTIFACT_VALIDATE", "completed")
        notify("MODEL_RESULT_PUBLISH", "running")
        _publish_json(result_path, result)
        _publish_json(report_path, report)
        notify("MODEL_RESULT_PUBLISH", "completed")
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        result_path.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)
        raise
    return report_path, report, result


def run_uploaded_model_workbench(
    workspace_root: Path,
    run_id: str,
    source_bytes: bytes,
    media_type: str,
    phase_callback: Callable[[str, str], None] | None = None,
) -> tuple[Path, dict[str, object]]:
    report_path, report, _ = run_normalized_model_workbench(
        workspace_root,
        run_id,
        source_bytes,
        media_type,
        run_model_worker,
        timeout_seconds=MODEL_TIMEOUT_SECONDS,
        phase_callback=phase_callback,
    )
    return report_path, report
