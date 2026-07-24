"""Checksummed local Gate F technical-preflight bundle assembly and verification."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from .contracts import StageContractError
from .frame_sequence import build_gate_f_frame_sequence, parse_gate_f_frame_sequence_config
from .paired_experiment import ArmIdentity, PairOutcome, evaluate_experiment, validate_arm_parity
from .psd_reader import parse_layered_psd
from .psd_writer import PsdLayer, write_layered_psd
from .runtime import canonical_json_bytes, read_bounded_file, strict_load_json_bytes


def purpose_created_psd() -> tuple[bytes, dict[str, object]]:
    width, height = 4, 4
    transparent = bytes((0, 0, 0, 0)) * 16
    source = bytes((40, 80, 120, 255)) * 16
    layers = (
        PsdLayer(1, "Read Me 说明", 0, 0, width, height, transparent, visible=False),
        PsdLayer(2, "Face Base", 1, 1, 2, 2, bytes((180, 120, 90, 255)) * 4),
        PsdLayer(3, "Generated Fill", 1, 1, 2, 2, bytes((160, 100, 80, 255)) * 4),
        PsdLayer(4, "Source Reference", 0, 0, width, height, source, visible=False, locked=True),
    )
    data = write_layered_psd(width, height, layers, source)
    parsed = parse_layered_psd(data)
    report = {
        "format": "oneclick2d.layered-psd-preflight-report",
        "format_version": "0.1.0",
        "profile": "psd-v1-rgb8-raw-flat-layers",
        "width": parsed.width,
        "height": parsed.height,
        "layer_count": len(parsed.layers),
        "raw_compression": True,
        "writer_reader_independent_modules": True,
        "icc_profile_present": False,
        "external_editor_status": "pending",
        "structural_preflight_pass": True,
    }
    return data, report


def purpose_created_outcomes() -> tuple[PairOutcome, ...]:
    return tuple(
        PairOutcome(
            f"asset.fixture-{index:02d}",
            "candidate_win" if index < 10 else "comparator_win" if index < 13 else "tie",
            index < 12,
            "purpose_created_evaluator_fixture",
        )
        for index in range(20)
    )


def purpose_created_statistics() -> dict[str, object]:
    return evaluate_experiment(purpose_created_outcomes())


def build_bundle(directory: Path, evidence: dict[str, bytes]) -> Path:
    directory.mkdir(parents=True, exist_ok=False)
    entries = []
    for name, data in sorted(evidence.items()):
        if not name or "/" in name or "\\" in name or name in {"bundle-index.json", "acceptance-report.json"}:
            raise StageContractError("bundle artifact name is invalid")
        path = directory / name
        path.write_bytes(data)
        entries.append({"name": name, "sha256": sha256(data).hexdigest(), "byte_length": len(data)})
    index = {
        "format": "oneclick2d.gate-f-technical-preflight-bundle",
        "format_version": "0.1.0",
        "scope": "purpose-created-local-technical-preflight",
        "entries": entries,
    }
    index_path = directory / "bundle-index.json"
    index_path.write_bytes(canonical_json_bytes(index))
    return index_path


def _load_json(directory: Path, name: str) -> object:
    try:
        return strict_load_json_bytes(read_bounded_file(directory / name))
    except (OSError, ValueError, TypeError) as exc:
        raise StageContractError("bundle JSON is invalid") from exc


def _arm_identity(report: dict[str, object]) -> ArmIdentity:
    try:
        source = report["input"]
        sequence = report["sequence"]
        rendering = report["rendering"]
        frames = report["frames"]
        if not isinstance(source, dict) or not isinstance(sequence, dict) or not isinstance(rendering, dict) or not isinstance(frames, list):
            raise TypeError
        if "canvas" in rendering:
            width, height = rendering["canvas"]
        else:
            width, height = source["width"], source["height"]
        sequence_config = parse_gate_f_frame_sequence_config({
            "format": "oneclick2d.gate-f-frame-sequence-config",
            "format_version": "0.1.0",
            "profile_id": sequence["profile_id"],
            "seed_u64": sequence["seed_u64"],
        })
        expected_sequence = build_gate_f_frame_sequence(sequence_config)
        frame_ids = tuple(str(frame["id"]) for frame in frames)
        expected_ids = tuple(frame.id for frame in expected_sequence.frames)
        if (
            sequence["sha256"] != expected_sequence.sha256
            or sequence["frame_count"] != len(expected_ids)
            or frame_ids != expected_ids
            or len(frame_ids) != len(set(frame_ids))
            or any(frame.get("index") != index for index, frame in enumerate(frames))
        ):
            raise StageContractError("arm report sequence evidence is inconsistent")
        return ArmIdentity(
            str(source["normalized_raster_sha256"]),
            str(sequence["sha256"]),
            str(rendering["contract_id"]),
            str(rendering["profile_id"]),
            int(width),
            int(height),
            frame_ids,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StageContractError("bundle arm report is invalid") from exc


def verify_bundle(directory: Path) -> dict[str, object]:
    index = _load_json(directory, "bundle-index.json")
    keys = {"format", "format_version", "scope", "entries"}
    if (
        not isinstance(index, dict)
        or set(index) != keys
        or index["format"] != "oneclick2d.gate-f-technical-preflight-bundle"
        or index["format_version"] != "0.1.0"
        or index["scope"] != "purpose-created-local-technical-preflight"
        or not isinstance(index["entries"], list)
    ):
        raise StageContractError("bundle index contract is invalid")
    names = set()
    for entry in index["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"name", "sha256", "byte_length"}:
            raise StageContractError("bundle entry is invalid")
        name = entry["name"]
        byte_length = entry["byte_length"]
        if (
            not isinstance(name, str)
            or name in names
            or "/" in name
            or "\\" in name
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or not 1 <= byte_length <= 512 * 1024 * 1024
        ):
            raise StageContractError("bundle entry name or size is invalid")
        names.add(name)
        path = directory / name
        try:
            if path.stat().st_size != byte_length:
                raise StageContractError("bundle artifact size mismatch")
            digest = sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
        except OSError as exc:
            raise StageContractError("bundle artifact is unavailable") from exc
        if digest.hexdigest() != entry["sha256"]:
            raise StageContractError("bundle artifact digest mismatch")
    required = {"candidate-report.json", "comparator-report.json", "paired-outcomes.json", "paired-statistics.json", "structural-preflight.psd", "psd-readback.json"}
    required.update(f"candidate-frame-{index:03d}.png" for index in range(37))
    required.update(f"comparator-frame-{index:03d}.png" for index in range(37))
    if names != required:
        raise StageContractError("bundle evidence inventory is not exact")
    disk_names = {item.name for item in directory.iterdir() if item.is_file()}
    if disk_names != required | {"bundle-index.json"}:
        raise StageContractError("bundle directory contains unindexed or missing files")
    candidate = _load_json(directory, "candidate-report.json")
    comparator = _load_json(directory, "comparator-report.json")
    outcomes_value = _load_json(directory, "paired-outcomes.json")
    stored_statistics = _load_json(directory, "paired-statistics.json")
    psd_report = _load_json(directory, "psd-readback.json")
    if not isinstance(candidate, dict) or not isinstance(comparator, dict) or not isinstance(outcomes_value, list) or not isinstance(stored_statistics, dict) or not isinstance(psd_report, dict):
        raise StageContractError("bundle evidence shape is invalid")
    try:
        outcomes = tuple(
            PairOutcome(str(row["asset_id"]), str(row["outcome"]), bool(row["f_usable"]), str(row["reason"]))
            for row in outcomes_value
        )
    except (KeyError, TypeError) as exc:
        raise StageContractError("paired outcome evidence is invalid") from exc
    statistics = evaluate_experiment(outcomes)
    if statistics != stored_statistics:
        raise StageContractError("paired statistics do not match raw outcomes")
    validate_arm_parity(_arm_identity(candidate), _arm_identity(comparator))
    parity = True
    parsed_psd = parse_layered_psd((directory / "structural-preflight.psd").read_bytes())
    psd_structural = (
        parsed_psd.width == psd_report.get("width")
        and parsed_psd.height == psd_report.get("height")
        and len(parsed_psd.layers) == psd_report.get("layer_count")
        and parsed_psd.layers[0].name.startswith("Read Me")
        and not parsed_psd.layers[0].visible
        and parsed_psd.layers[-1].name == "Source Reference"
        and not parsed_psd.layers[-1].visible
        and parsed_psd.layers[-1].locked
    )
    if not psd_structural:
        raise StageContractError("PSD panel semantics do not match readback evidence")
    if (
        psd_report.get("icc_profile_present") is not False
        or psd_report.get("external_editor_status") != "pending"
        or psd_report.get("structural_preflight_pass") is not True
    ):
        raise StageContractError("PSD readback report is invalid")
    local_pass = parity and statistics["primary_pair_rule_pass"] is True and psd_structural
    return {
        "format": "oneclick2d.gate-f-technical-preflight-acceptance",
        "format_version": "0.1.0",
        "local_technical_preflight_status": "LOCAL_TECHNICAL_PREFLIGHT_PASS" if local_pass else "LOCAL_TECHNICAL_PREFLIGHT_FAIL",
        "ready_for_activated_scoring": False,
        "gate_f_status": "GATE_F_NOT_EVALUATED",
        "arm_parity_pass": parity,
        "paired_evaluator_fixture_pass": statistics["primary_pair_rule_pass"],
        "psd_structural_preflight_pass": psd_structural,
        "icc_profile_present": psd_report["icc_profile_present"],
        "external_editor_status": psd_report["external_editor_status"],
        "artifact_count": len(index["entries"]),
    }
