"""One-command purpose-created Gate F local technical preflight."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .acceptance import build_bundle, purpose_created_outcomes, purpose_created_psd, purpose_created_statistics, verify_bundle
from .candidate_baseline import build_gate_f_registry
from .contracts import StageContractError, StageStatus
from .purpose_created import (
    arm_run_spec as _run_spec,
    normalization_config as _normalization_config,
    purpose_created_source,
)
from .runner import PipelineRunner
from .runtime import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]


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
