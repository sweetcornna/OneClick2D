"""Uploaded-image local workbench for the disposable deterministic baseline."""

from __future__ import annotations

import io
import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Callable

from .candidate_baseline import build_gate_f_registry
from .contracts import StageContractError, StageStatus
from .psd_reader import parse_layered_psd
from .psd_writer import PsdLayer, write_layered_psd
from .runner import PipelineRunner
from .runtime import ID_RE, canonical_json_bytes, read_bounded_file, sha256_bytes, sha256_file

ROOT = Path(__file__).resolve().parents[2]
PHASES = (
    "UPLOAD_RECEIVED",
    "RASTER_NORMALIZE",
    "DETERMINISTIC_BASELINE_37_FRAMES",
    "PSD_WRITE",
    "PSD_READBACK",
)
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_PUBLISHED_BYTES = 512 * 1024 * 1024


def _normalization_config() -> bytes:
    return canonical_json_bytes(
        {
            "max_width": 2048,
            "max_height": 2048,
            "max_pixels": 4_194_304,
            "max_metadata_bytes": 1_048_576,
            "max_icc_profile_bytes": 1_048_576,
            "required_pillow_version": "12.1.0",
            "png_compress_level": 9,
            "rendering_intent": 1,
        }
    )


def _run_spec(source: bytes, media_type: str, normalize: bytes, candidate: bytes) -> bytes:
    common_limits = {
        "max_wall_time_ms": 120_000,
        "max_cpu_time_ms": 120_000,
        "max_peak_ram_bytes": 1_073_741_824,
        "max_scratch_bytes": 1_048_576,
        "max_output_bytes": 64 * 1024 * 1024,
        "max_output_files": 2,
        "max_peak_vram_bytes": 0,
        "gpu_allowed": False,
    }
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
                "sha256": sha256(source).hexdigest(),
                "media_type": media_type,
                "max_bytes": MAX_SOURCE_BYTES,
            },
            "expected_result_role": "candidate_baseline_report",
            "stages": [
                {
                    "id": "stage.raster-normalize",
                    "stage_type": "oc2d.spike.raster-normalize",
                    "adapter_id": "raster.normalize.pillow.v1",
                    "config_uri": "configs/normalize.json",
                    "config_sha256": sha256(normalize).hexdigest(),
                    "limits": common_limits,
                },
                {
                    "id": "stage.candidate-baseline",
                    "stage_type": "oc2d.spike.candidate-baseline",
                    "adapter_id": "candidate.baseline.pillow.v1",
                    "config_uri": "configs/candidate.json",
                    "config_sha256": sha256(candidate).hexdigest(),
                    "limits": {**common_limits, "max_output_bytes": MAX_PUBLISHED_BYTES, "max_output_files": 43},
                },
            ],
        }
    )


def _descriptor(run_dir: Path, artifact: dict[str, object], artifact_id: str, kind: str) -> dict[str, object]:
    uri = artifact.get("uri")
    digest = artifact.get("sha256")
    length = artifact.get("byte_length")
    media_type = artifact.get("media_type")
    if (
        not isinstance(uri, str)
        or not isinstance(digest, str)
        or isinstance(length, bool)
        or not isinstance(length, int)
        or not isinstance(media_type, str)
    ):
        raise StageContractError("workbench artifact descriptor is invalid")
    path = run_dir / Path(uri)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != length or sha256_file(path) != digest:
        raise StageContractError("workbench artifact does not match the manifest")
    return {
        "id": artifact_id,
        "kind": kind,
        "media_type": media_type,
        "uri": uri,
        "sha256": digest,
        "byte_length": length,
    }


def _stage_outputs(manifest: dict[str, object], stage_index: int) -> list[dict[str, object]]:
    stages = manifest.get("stages")
    if not isinstance(stages, list) or len(stages) <= stage_index or not isinstance(stages[stage_index], dict):
        raise StageContractError("workbench manifest stages are invalid")
    outputs = stages[stage_index].get("outputs")
    if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
        raise StageContractError("workbench stage outputs are invalid")
    return outputs


def _find_output(
    outputs: list[dict[str, object]],
    *,
    role: str | None = None,
    artifact: dict[str, object] | None = None,
) -> dict[str, object] | None:
    matches = outputs
    if role is not None:
        matches = [item for item in matches if item.get("role") == role]
    if artifact is not None:
        name = artifact.get("name")
        digest = artifact.get("sha256")
        matches = [
            item
            for item in matches
            if item.get("sha256") == digest
            and isinstance(item.get("uri"), str)
            and Path(str(item["uri"])).name == name
        ]
    if len(matches) > 1:
        raise StageContractError("workbench stage output identity is ambiguous")
    return matches[0] if matches else None


def _load_rgba(data: bytes) -> tuple[int, int, bytes]:
    from .raster import _load_pillow

    backend = _load_pillow()
    with backend.Image.open(io.BytesIO(data), formats=("PNG",)) as image:
        image.load()
        if image.mode != "RGBA":
            raise StageContractError("workbench PNG is not RGBA")
        return image.width, image.height, image.tobytes()


def _layer_rgba(run_dir: Path, descriptor: dict[str, object]) -> tuple[int, int, bytes]:
    uri = descriptor["artifact"]["uri"]  # type: ignore[index]
    if not isinstance(uri, str):
        raise StageContractError("candidate layer URI is invalid")
    return _load_rgba(read_bounded_file(run_dir / uri, 64 * 1024 * 1024))


def _transparent_head(source_rgba: bytes, width: int, height: int, box: tuple[int, int, int, int]) -> bytes:
    result = bytearray(source_rgba)
    left, top, right, bottom = box
    for y in range(top, bottom):
        start = (y * width + left) * 4
        end = (y * width + right) * 4
        result[start:end] = b"\0" * (end - start)
    return bytes(result)


def _write_uploaded_psd(
    run_dir: Path,
    normalized: dict[str, object],
    candidate: dict[str, object],
    frames: list[dict[str, object]],
) -> dict[str, object]:
    normalized_path = run_dir / str(normalized["uri"])
    width, height, source_rgba = _load_rgba(read_bounded_file(normalized_path, 64 * 1024 * 1024))
    layers_value = candidate.get("layers")
    if not isinstance(layers_value, list):
        raise StageContractError("candidate layer report is invalid")
    layers_by_id = {item.get("id"): item for item in layers_value if isinstance(item, dict)}
    required = {"layer.torso-base", "layer.head", "layer.eye.screen-left", "layer.eye.screen-right", "layer.mouth"}
    if set(layers_by_id) != required:
        raise StageContractError("candidate layer inventory is invalid")

    head_report = layers_by_id["layer.head"]
    head_box_value = head_report.get("box_ltrb")
    if not isinstance(head_box_value, list) or len(head_box_value) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in head_box_value):
        raise StageContractError("candidate head bounds are invalid")
    head_box = tuple(head_box_value)
    head_left, head_top, head_right, head_bottom = head_box

    torso_width, torso_height, torso_rgba = _layer_rgba(run_dir, layers_by_id["layer.torso-base"])
    if (torso_width, torso_height) != (width, height):
        raise StageContractError("candidate base canvas is invalid")
    backing = bytearray()
    for y in range(head_top, head_bottom):
        backing.extend(torso_rgba[(y * width + head_left) * 4 : (y * width + head_right) * 4])

    def projected(layer_id: str, psd_id: int, name: str) -> PsdLayer:
        layer = layers_by_id[layer_id]
        box_value = layer.get("box_ltrb")
        if not isinstance(box_value, list) or len(box_value) != 4:
            raise StageContractError("candidate layer bounds are invalid")
        left, top, right, bottom = box_value
        image_width, image_height, rgba = _layer_rgba(run_dir, layer)
        if (image_width, image_height) != (right - left, bottom - top):
            raise StageContractError("candidate layer pixels do not match bounds")
        return PsdLayer(psd_id, name, left, top, image_width, image_height, rgba)

    neutral = frames[0]
    if neutral.get("index") != 0 or neutral.get("id") != "neutral" or not isinstance(neutral.get("artifact"), dict):
        raise StageContractError("candidate neutral frame is missing")
    neutral_uri = neutral["artifact"].get("uri")
    if not isinstance(neutral_uri, str):
        raise StageContractError("candidate neutral frame URI is invalid")
    neutral_width, neutral_height, neutral_rgba = _load_rgba(read_bounded_file(run_dir / neutral_uri, 64 * 1024 * 1024))
    if (neutral_width, neutral_height) != (width, height):
        raise StageContractError("candidate neutral frame canvas is invalid")

    read_me = PsdLayer(1, "Read Me — deterministic fixed-region baseline; no model", 0, 0, 1, 1, b"\0\0\0\0", visible=False)
    psd_layers = (
        read_me,
        projected("layer.mouth", 2, "Mouth"),
        projected("layer.eye.screen-right", 3, "Eye Left"),
        projected("layer.eye.screen-left", 4, "Eye Right"),
        projected("layer.head", 5, "Face Base"),
        PsdLayer(6, "Generated Fill — Face Backing", head_left, head_top, head_right - head_left, head_bottom - head_top, bytes(backing)),
        PsdLayer(7, "Base Source Pixels", 0, 0, width, height, _transparent_head(source_rgba, width, height, head_box)),
        PsdLayer(8, "Source Reference", 0, 0, width, height, source_rgba, visible=False, locked=True),
    )
    psd_data = write_layered_psd(width, height, psd_layers, neutral_rgba)
    parsed = parse_layered_psd(psd_data)
    if (
        parsed.width != width
        or parsed.height != height
        or parsed.merged_rgba != neutral_rgba
        or len(parsed.layers) != len(psd_layers)
        or any(
            (actual.layer_id, actual.name, actual.left, actual.top, actual.width, actual.height, actual.rgba, actual.visible, actual.opacity, actual.locked)
            != (expected.layer_id, expected.name, expected.left, expected.top, expected.width, expected.height, expected.rgba, expected.visible, expected.opacity, expected.locked)
            for actual, expected in zip(parsed.layers, psd_layers, strict=True)
        )
    ):
        raise StageContractError("uploaded PSD readback does not match the projection")
    temp = run_dir / "output.psd.tmp"
    final = run_dir / "output.psd"
    temp.write_bytes(psd_data)
    os.replace(temp, final)
    return {
        "id": "output-psd",
        "kind": "psd",
        "media_type": "image/vnd.adobe.photoshop",
        "uri": final.relative_to(run_dir).as_posix(),
        "sha256": sha256_bytes(psd_data),
        "byte_length": len(psd_data),
        "profile": "psd-v1-rgb8-raw-flat-layers",
        "layer_count": len(parsed.layers),
        "structural_readback_pass": True,
        "icc_profile_present": False,
        "external_editor_status": "not_evaluated",
    }


def _publish_report(run_dir: Path, report: dict[str, object]) -> Path:
    path = run_dir / "workbench-report.json"
    temp = run_dir / "workbench-report.json.tmp"
    temp.write_bytes(canonical_json_bytes(report))
    os.replace(temp, path)
    return path


def run_uploaded_workbench(
    workspace_root: Path,
    run_id: str,
    source_bytes: bytes,
    media_type: str,
    phase_callback: Callable[[str, str], None] | None = None,
) -> tuple[Path, dict[str, object]]:
    if not ID_RE.fullmatch(run_id) or media_type not in {"image/png", "image/jpeg"} or not 1 <= len(source_bytes) <= MAX_SOURCE_BYTES:
        raise StageContractError("uploaded workbench input is invalid")

    phase_states = {phase: "pending" for phase in PHASES}

    def notify(phase: str, state: str) -> None:
        phase_states[phase] = state
        if phase_callback is not None:
            try:
                phase_callback(phase, state)
            except Exception:
                pass

    def finalized_phases() -> list[dict[str, str]]:
        terminal = False
        result: list[dict[str, str]] = []
        for phase in PHASES:
            state = phase_states[phase]
            if terminal or state in {"pending", "running"}:
                state = "unavailable"
            elif state in {"blocked", "cancelled", "failed"}:
                terminal = True
            result.append({"id": phase, "state": state})
        return result

    notify("UPLOAD_RECEIVED", "completed")
    normalize = _normalization_config()
    candidate_config = (ROOT / "examples" / "gate-f-candidate-baseline" / "config.json").read_bytes()
    with tempfile.TemporaryDirectory() as temporary:
        fixture = Path(temporary)
        (fixture / "configs").mkdir()
        source_path = fixture / "source.bin"
        source_path.write_bytes(source_bytes)
        (fixture / "configs" / "normalize.json").write_bytes(normalize)
        (fixture / "configs" / "candidate.json").write_bytes(candidate_config)
        spec = fixture / "run-spec.json"
        spec.write_bytes(_run_spec(source_bytes, media_type, normalize, candidate_config))

        def observe(stage_id: str, event: str, status: StageStatus | None) -> None:
            phase = "RASTER_NORMALIZE" if stage_id == "stage.raster-normalize" else "DETERMINISTIC_BASELINE_37_FRAMES"
            if event == "started":
                notify(phase, "running")
            else:
                state = {
                    StageStatus.SUCCEEDED: "completed",
                    StageStatus.REVIEW: "completed",
                    StageStatus.FALLBACK: "completed",
                    StageStatus.BLOCKED: "blocked",
                    StageStatus.CANCELLED: "cancelled",
                    StageStatus.FAILED: "failed",
                }.get(status, "failed")
                notify(phase, state)

        status, manifest_path = PipelineRunner(build_gate_f_registry(), workspace_root).run(
            spec_path=spec,
            source_path=source_path,
            run_id=run_id,
            source_revision="source.local-workbench",
            build_id="build.local-workbench",
            stage_observer=observe,
        )

    run_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise StageContractError("workbench manifest is invalid")
    if status is not StageStatus.SUCCEEDED:
        reason = manifest.get("terminal_reason_code")
        report = {
            "format": "oneclick2d.local-image-workbench-report",
            "format_version": "0.1.0",
            "scope": "disposable-local-image-workbench",
            "run_id": run_id,
            "workflow": "baseline",
            "state": status.value,
            "local_status": "LOCAL_WORKBENCH_NOT_COMPLETED",
            "reason_code": reason if isinstance(reason, str) else "STAGE_INTERNAL_ERROR",
            "model_used": False,
            "oc2d_produced": False,
            "gate_f_status": "GATE_F_NOT_EVALUATED",
            "phases": finalized_phases(),
        }
        return _publish_report(run_dir, report), report

    normalize_outputs = _stage_outputs(manifest, 0)
    candidate_outputs = _stage_outputs(manifest, 1)
    normalized_manifest = _find_output(normalize_outputs, role="normalized_raster")
    normalization_report_manifest = _find_output(normalize_outputs, role="raster_normalization_report")
    candidate_report_manifest = _find_output(candidate_outputs, role="candidate_baseline_report")
    if normalized_manifest is None or normalization_report_manifest is None or candidate_report_manifest is None:
        raise StageContractError("workbench output inventory is incomplete")
    normalized = _descriptor(run_dir, normalized_manifest, "normalized", "image")
    normalization_report_descriptor = _descriptor(run_dir, normalization_report_manifest, "normalization-report", "json")
    candidate_report_descriptor = _descriptor(run_dir, candidate_report_manifest, "candidate-report", "json")
    normalization_value = json.loads((run_dir / str(normalization_report_descriptor["uri"])).read_text(encoding="utf-8"))
    candidate_value = json.loads((run_dir / str(candidate_report_descriptor["uri"])).read_text(encoding="utf-8"))
    if not isinstance(normalization_value, dict) or not isinstance(candidate_value, dict):
        raise StageContractError("workbench reports are invalid")

    layer_reports = candidate_value.get("layers")
    frame_reports = candidate_value.get("frames")
    if not isinstance(layer_reports, list) or not isinstance(frame_reports, list) or len(frame_reports) != 37:
        raise StageContractError("candidate workbench report is incomplete")
    layers: list[dict[str, object]] = []
    for layer in layer_reports:
        if not isinstance(layer, dict) or not isinstance(layer.get("artifact"), dict):
            raise StageContractError("candidate workbench layer is invalid")
        artifact_value = layer["artifact"]
        output = _find_output(candidate_outputs, artifact=artifact_value)
        if output is None:
            raise StageContractError("candidate workbench layer artifact is missing")
        descriptor = _descriptor(run_dir, output, str(layer["id"]), "layer")
        layers.append({key: layer[key] for key in ("id", "slot_id", "side", "box_ltrb", "generated_fill")} | {"artifact": descriptor})
    frames: list[dict[str, object]] = []
    for frame in frame_reports:
        if not isinstance(frame, dict) or not isinstance(frame.get("artifact"), dict):
            raise StageContractError("candidate workbench frame is invalid")
        output = _find_output(candidate_outputs, artifact=frame["artifact"])
        if output is None:
            raise StageContractError("candidate workbench frame artifact is missing")
        descriptor = _descriptor(run_dir, output, f"frame-{int(frame['index']):03d}", "frame")
        frames.append({key: frame[key] for key in ("index", "id", "source", "parameters")} | {"artifact": descriptor})

    notify("PSD_WRITE", "running")
    psd = _write_uploaded_psd(run_dir, normalized, {"layers": layers}, frames)
    notify("PSD_WRITE", "completed")
    notify("PSD_READBACK", "completed")
    report = {
        "format": "oneclick2d.local-image-workbench-report",
        "format_version": "0.1.0",
        "scope": "disposable-local-image-workbench",
        "run_id": run_id,
        "workflow": "baseline",
        "state": "completed",
        "local_status": "LOCAL_WORKBENCH_COMPLETED",
        "model_used": False,
        "oc2d_produced": False,
        "gate_f_status": "GATE_F_NOT_EVALUATED",
        "source_retention": "local_workspace_until_manual_removal",
        "phases": [{"id": phase, "state": "completed"} for phase in PHASES],
        "normalization": {
            "input": normalization_value.get("input"),
            "output": normalization_value.get("output"),
            "orientation": normalization_value.get("orientation"),
            "color_policy": normalization_value.get("color_policy"),
            "finding_codes": normalization_value.get("finding_codes"),
            "artifact": normalized,
        },
        "candidate": {
            "kind": "deterministic_fixed_region_baseline",
            "suitability": candidate_value.get("suitability"),
            "ontology": candidate_value.get("ontology"),
            "geometry": candidate_value.get("geometry"),
            "validation": candidate_value.get("validation"),
            "sequence": candidate_value.get("sequence"),
            "rendering": candidate_value.get("rendering"),
            "layers": layers,
            "frames": frames,
        },
        "psd": psd,
    }
    return _publish_report(run_dir, report), report
