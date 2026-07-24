"""One-command purpose-created Gate F local technical preflight."""

from __future__ import annotations

import json
import struct
import tempfile
import zlib
from hashlib import sha256
from pathlib import Path

from .acceptance import build_bundle, purpose_created_outcomes, purpose_created_psd, purpose_created_statistics, verify_bundle
from .candidate_baseline import build_gate_f_registry
from .contracts import StageContractError, StageStatus
from .runner import PipelineRunner
from .runtime import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def purpose_created_source(width: int = 101, height: int = 103) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256, 255))
        rows.append(bytes(row))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"sRGB", b"\x00") + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b"")


def _normalization_config() -> bytes:
    return canonical_json_bytes({"max_width": 8192, "max_height": 8192, "max_pixels": 40000000, "max_metadata_bytes": 1048576, "max_icc_profile_bytes": 1048576, "required_pillow_version": "12.1.0", "png_compress_level": 9, "rendering_intent": 1})


def _run_spec(source: bytes, normalize: bytes, arm_config: bytes, arm: str) -> bytes:
    limits = {"max_wall_time_ms": 30000, "max_cpu_time_ms": 30000, "max_peak_ram_bytes": 536870912, "max_scratch_bytes": 1048576, "max_output_bytes": 33554432, "max_output_files": 2, "max_peak_vram_bytes": 0, "gpu_allowed": False}
    if arm == "candidate":
        stage = {"id": "stage.arm-render", "stage_type": "oc2d.spike.candidate-baseline", "adapter_id": "candidate.baseline.pillow.v1", "config_uri": "configs/arm.json", "config_sha256": sha256(arm_config).hexdigest(), "limits": {**limits, "max_output_files": 43}}
        result_role = "candidate_baseline_report"
    elif arm == "comparator":
        stage = {"id": "stage.arm-render", "stage_type": "oc2d.spike.simple-cutout-comparator", "adapter_id": "simple-cutout.comparator.pillow.v1", "config_uri": "configs/arm.json", "config_sha256": sha256(arm_config).hexdigest(), "limits": {**limits, "max_output_files": 38}}
        result_role = "simple_cutout_comparator_report"
    else:
        raise StageContractError("unknown preflight arm")
    return canonical_json_bytes({
        "$schema": str(ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
        "format": "oneclick2d.gate-f-run-spec",
        "format_version": "0.1.0",
        "scope": "disposable-gate-f-spike",
        "execution_profile": "python-pillow-12.1.0-in-process-v1",
        "root_seed_u64": "00000000000000000042",
        "source": {"role": "source_raster", "sha256": sha256(source).hexdigest(), "media_type": "image/png", "max_bytes": 26214400},
        "expected_result_role": result_role,
        "stages": [
            {"id": "stage.raster-normalize", "stage_type": "oc2d.spike.raster-normalize", "adapter_id": "raster.normalize.pillow.v1", "config_uri": "configs/normalize.json", "config_sha256": sha256(normalize).hexdigest(), "limits": limits},
            stage,
        ],
    })


def run_local_preflight(workspace_root: Path, run_id: str) -> tuple[Path, dict[str, object]]:
    source = purpose_created_source()
    normalize = _normalization_config()
    candidate_config = (ROOT / "examples" / "gate-f-candidate-baseline" / "config.json").read_bytes()
    comparator_config = (ROOT / "examples" / "gate-f-simple-cutout-comparator" / "config.json").read_bytes()
    registry = build_gate_f_registry()
    reports: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as temporary:
        fixture = Path(temporary)
        (fixture / "configs").mkdir()
        source_path = fixture / "source.png"
        source_path.write_bytes(source)
        (fixture / "configs" / "normalize.json").write_bytes(normalize)
        for arm, config in (("candidate", candidate_config), ("comparator", comparator_config)):
            (fixture / "configs" / "arm.json").write_bytes(config)
            spec = fixture / "run-spec.json"
            spec.write_bytes(_run_spec(source, normalize, config, arm))
            status, manifest_path = PipelineRunner(registry, workspace_root).run(
                spec_path=spec,
                source_path=source_path,
                run_id=f"{run_id}.{arm}",
                source_revision="source.purpose-created",
                build_id="build.local-preflight",
            )
            if status is not StageStatus.SUCCEEDED:
                raise StageContractError(f"{arm} preflight arm did not succeed")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            reports[f"{arm}-report.json"] = (manifest_path.parent / manifest["result"]["uri"]).read_bytes()
            frame_evidence: dict[str, bytes] = {}
            report_value = json.loads(reports[f"{arm}-report.json"].decode("utf-8"))
            frame_role = "candidate_frame" if arm == "candidate" else "simple_cutout_frame"
            output_by_sha = {
                item["sha256"]: item
                for stage_record in manifest["stages"]
                for item in stage_record["outputs"]
                if item["role"] == frame_role
            }
            for frame in report_value["frames"]:
                frame_artifact = frame["artifact"]
                manifest_artifact = output_by_sha.get(frame_artifact["sha256"])
                if manifest_artifact is None:
                    raise StageContractError(f"{arm} frame evidence is missing")
                frame_evidence[f"{arm}-frame-{frame['index']:03d}.png"] = (manifest_path.parent / manifest_artifact["uri"]).read_bytes()
            reports.update(frame_evidence)
    psd_data, psd_report = purpose_created_psd()
    outcomes = purpose_created_outcomes()
    evidence = {
        **reports,
        "paired-outcomes.json": canonical_json_bytes([{"asset_id": row.asset_id, "outcome": row.outcome, "f_usable": row.f_usable, "reason": row.reason} for row in outcomes]),
        "paired-statistics.json": canonical_json_bytes(purpose_created_statistics()),
        "structural-preflight.psd": psd_data,
        "psd-readback.json": canonical_json_bytes(psd_report),
    }
    bundle = workspace_root / f"{run_id}.bundle"
    index_path = build_bundle(bundle, evidence)
    acceptance = verify_bundle(bundle)
    (workspace_root / f"{run_id}.acceptance-report.json").write_bytes(canonical_json_bytes(acceptance))
    return index_path, acceptance
