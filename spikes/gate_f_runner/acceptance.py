"""Checksummed local Gate F technical-preflight bundle assembly and verification."""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import io
import json
import re
import struct
import tempfile
import warnings
import zlib
from pathlib import Path

from .candidate_baseline import build_gate_f_registry
from .contracts import StageContractError, StageStatus
from .frame_sequence import build_gate_f_frame_sequence, parse_gate_f_frame_sequence_config
from .paired_experiment import PairOutcome, arm_identity_from_report, evaluate_experiment, validate_arm_parity
from .psd_reader import parse_layered_psd
from .psd_writer import PsdLayer, write_layered_psd
from .raster import _load_pillow, _verify_output_png
from .rendering import RENDERER_CONTRACT_ID, RENDERER_PROFILE_ID
from .runner import PipelineRunner
from .runtime import ID_RE, MAX_JSON_BYTES, SHA256_RE, canonical_json_bytes, contained_workspace_path, read_bounded_file, strict_load_json_bytes


MAX_BUNDLE_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_ARM_BUNDLE_OUTPUT_BYTES = 33_554_432
_ROOT = Path(__file__).resolve().parents[2]
_BUNDLE_ARTIFACT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.(?:json|png|psd)$")
_REQUIRED_ARTIFACT_NAMES = frozenset(
    {
        "candidate-report.json",
        "comparator-report.json",
        "paired-outcomes.json",
        "paired-statistics.json",
        "structural-preflight.psd",
        "psd-readback.json",
        *(f"candidate-frame-{index:03d}.png" for index in range(37)),
        *(f"comparator-frame-{index:03d}.png" for index in range(37)),
    }
)
_JSON_ARTIFACT_NAMES = frozenset(
    {
        "candidate-report.json",
        "comparator-report.json",
        "paired-outcomes.json",
        "paired-statistics.json",
        "psd-readback.json",
    }
)
_PAIR_OUTCOMES = frozenset({"candidate_win", "comparator_win", "tie", "invalid"})
_PAIR_REASONS = frozenset(
    {
        "candidate_failure",
        "comparator_infrastructure_failure",
        "missing_review_evidence",
        "purpose_created_evaluator_fixture",
        "reviewed",
    }
)


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


def _purpose_created_source(width: int = 101, height: int = 103) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256, 255))
        rows.append(bytes(row))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"sRGB", b"\x00") + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def _normalization_config() -> bytes:
    return canonical_json_bytes(
        {
            "max_width": 8192,
            "max_height": 8192,
            "max_pixels": 40000000,
            "max_metadata_bytes": 1048576,
            "max_icc_profile_bytes": 1048576,
            "required_pillow_version": "12.1.0",
            "png_compress_level": 9,
            "rendering_intent": 1,
        }
    )


def _arm_run_spec(source: bytes, normalize: bytes, arm_config: bytes, arm: str) -> bytes:
    limits = {
        "max_wall_time_ms": 30000,
        "max_cpu_time_ms": 30000,
        "max_peak_ram_bytes": 536870912,
        "max_scratch_bytes": 1048576,
        "max_output_bytes": _MAX_ARM_BUNDLE_OUTPUT_BYTES,
        "max_output_files": 2,
        "max_peak_vram_bytes": 0,
        "gpu_allowed": False,
    }
    if arm == "candidate":
        stage = {
            "id": "stage.arm-render",
            "stage_type": "oc2d.spike.candidate-baseline",
            "adapter_id": "candidate.baseline.pillow.v1",
            "config_uri": "configs/arm.json",
            "config_sha256": sha256(arm_config).hexdigest(),
            "limits": {**limits, "max_output_files": 43},
        }
        result_role = "candidate_baseline_report"
    elif arm == "comparator":
        stage = {
            "id": "stage.arm-render",
            "stage_type": "oc2d.spike.simple-cutout-comparator",
            "adapter_id": "simple-cutout.comparator.pillow.v1",
            "config_uri": "configs/arm.json",
            "config_sha256": sha256(arm_config).hexdigest(),
            "limits": {**limits, "max_output_files": 38},
        }
        result_role = "simple_cutout_comparator_report"
    else:
        raise StageContractError("unknown preflight arm")
    return canonical_json_bytes(
        {
            "$schema": str(_ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
            "format": "oneclick2d.gate-f-run-spec",
            "format_version": "0.1.0",
            "scope": "disposable-gate-f-spike",
            "execution_profile": "python-pillow-12.1.0-in-process-v1",
            "root_seed_u64": "00000000000000000042",
            "source": {
                "role": "source_raster",
                "sha256": sha256(source).hexdigest(),
                "media_type": "image/png",
                "max_bytes": 26214400,
            },
            "expected_result_role": result_role,
            "stages": [
                {
                    "id": "stage.raster-normalize",
                    "stage_type": "oc2d.spike.raster-normalize",
                    "adapter_id": "raster.normalize.pillow.v1",
                    "config_uri": "configs/normalize.json",
                    "config_sha256": sha256(normalize).hexdigest(),
                    "limits": limits,
                },
                stage,
            ],
        }
    )


@lru_cache(maxsize=1)
def _purpose_created_arm_evidence() -> tuple[tuple[str, bytes], ...]:
    source = _purpose_created_source()
    normalize = _normalization_config()
    arm_configs = {
        "candidate": (_ROOT / "examples" / "gate-f-candidate-baseline" / "config.json").read_bytes(),
        "comparator": (_ROOT / "examples" / "gate-f-simple-cutout-comparator" / "config.json").read_bytes(),
    }
    evidence: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as temporary:
        fixture = Path(temporary)
        (fixture / "configs").mkdir()
        source_path = fixture / "source.png"
        source_path.write_bytes(source)
        (fixture / "configs" / "normalize.json").write_bytes(normalize)
        registry = build_gate_f_registry()
        runner = PipelineRunner(registry, fixture / "workspace")
        for arm, config in arm_configs.items():
            (fixture / "configs" / "arm.json").write_bytes(config)
            spec_path = fixture / "run-spec.json"
            spec_path.write_bytes(_arm_run_spec(source, normalize, config, arm))
            status, manifest_path = runner.run(
                spec_path=spec_path,
                source_path=source_path,
                run_id=f"run.acceptance-recompute.{arm}",
                source_revision="source.purpose-created",
                build_id="build.local-preflight",
            )
            if status is not StageStatus.SUCCEEDED:
                raise StageContractError(f"{arm} evidence recomputation did not succeed")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report_data = (manifest_path.parent / manifest["result"]["uri"]).read_bytes()
            evidence[f"{arm}-report.json"] = report_data
            report = strict_load_json_bytes(report_data)
            if not isinstance(report, dict) or not isinstance(report.get("frames"), list):
                raise StageContractError(f"{arm} evidence recomputation is invalid")
            frame_role = "candidate_frame" if arm == "candidate" else "simple_cutout_frame"
            output_by_sha = {
                item["sha256"]: item
                for stage_record in manifest["stages"]
                for item in stage_record["outputs"]
                if item["role"] == frame_role
            }
            for frame in report["frames"]:
                artifact = frame["artifact"]
                manifest_artifact = output_by_sha.get(artifact["sha256"])
                if manifest_artifact is None:
                    raise StageContractError(f"{arm} evidence recomputation is incomplete")
                bundle_name = f"{arm}-frame-{frame['index']:03d}.png"
                evidence[bundle_name] = (manifest_path.parent / manifest_artifact["uri"]).read_bytes()
    return tuple(sorted(evidence.items()))


@lru_cache(maxsize=1)
def _purpose_created_bundle_evidence() -> tuple[tuple[str, bytes], ...]:
    psd_data, psd_report = purpose_created_psd()
    outcomes = purpose_created_outcomes()
    evidence = dict(_purpose_created_arm_evidence())
    evidence.update(
        {
            "paired-outcomes.json": canonical_json_bytes(
                [
                    {
                        "asset_id": row.asset_id,
                        "outcome": row.outcome,
                        "f_usable": row.f_usable,
                        "reason": row.reason,
                    }
                    for row in outcomes
                ]
            ),
            "paired-statistics.json": canonical_json_bytes(evaluate_experiment(outcomes)),
            "structural-preflight.psd": psd_data,
            "psd-readback.json": canonical_json_bytes(psd_report),
        }
    )
    if set(evidence) != _REQUIRED_ARTIFACT_NAMES:
        raise StageContractError("purpose-created bundle evidence is incomplete")
    return tuple(sorted(evidence.items()))


def build_bundle(directory: Path, evidence: dict[str, bytes]) -> Path:
    directory.mkdir(parents=True, exist_ok=False)
    entries = []
    for name, data in sorted(evidence.items()):
        if not _is_canonical_artifact_name(name) or name in {"bundle-index.json", "acceptance-report.json"}:
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


def _is_canonical_artifact_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and _BUNDLE_ARTIFACT_NAME_RE.fullmatch(name) is not None
        and not Path(name).is_absolute()
        and name not in {".", ".."}
        and ":" not in name
        and "/" not in name
        and "\\" not in name
    )


def _load_json_bytes(data: bytes) -> object:
    try:
        return strict_load_json_bytes(data)
    except (ValueError, TypeError) as exc:
        raise StageContractError("bundle JSON is invalid") from exc


def _regular_bundle_inventory(directory: Path) -> dict[str, Path]:
    try:
        contained_workspace_path(directory.parent, directory.name, kind="directory")
    except (OSError, RuntimeError, ValueError) as exc:
        raise StageContractError("bundle directory is invalid") from exc
    inventory: dict[str, Path] = {}
    try:
        for path in directory.iterdir():
            try:
                inventory[path.name] = contained_workspace_path(directory, path.name, kind="file")
            except (OSError, RuntimeError, ValueError) as exc:
                raise StageContractError("bundle artifact is invalid") from exc
    except StageContractError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise StageContractError("bundle directory is invalid") from exc
    return inventory


def _read_verified_bundle_index(
    directory: Path,
) -> tuple[dict[str, object], dict[str, Path], dict[str, tuple[str, int]], dict[str, bytes]]:
    inventory = _regular_bundle_inventory(directory)
    if set(inventory) != _REQUIRED_ARTIFACT_NAMES | {"bundle-index.json"}:
        raise StageContractError("bundle directory contains unindexed or missing files")
    try:
        index = _load_json_bytes(read_bounded_file(inventory["bundle-index.json"]))
    except (OSError, ValueError, TypeError) as exc:
        raise StageContractError("bundle JSON is invalid") from exc
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

    descriptors: dict[str, tuple[str, int]] = {}
    for entry in index["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"name", "sha256", "byte_length"}:
            raise StageContractError("bundle entry is invalid")
        name = entry["name"]
        digest = entry["sha256"]
        byte_length = entry["byte_length"]
        if (
            not _is_canonical_artifact_name(name)
            or name in descriptors
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or not 1 <= byte_length <= MAX_BUNDLE_ARTIFACT_BYTES
        ):
            raise StageContractError("bundle entry name, digest or size is invalid")
        descriptors[name] = (digest, byte_length)
    if set(descriptors) != _REQUIRED_ARTIFACT_NAMES:
        raise StageContractError("bundle evidence inventory is not exact")

    trusted_evidence = dict(_purpose_created_bundle_evidence())
    arm_names = {
        name
        for name in _REQUIRED_ARTIFACT_NAMES
        if name.startswith("candidate-") or name.startswith("comparator-")
    }
    producer_byte_budget = 2 * _MAX_ARM_BUNDLE_OUTPUT_BYTES + sum(
        len(trusted_evidence[name]) for name in _REQUIRED_ARTIFACT_NAMES - arm_names
    )
    if sum(byte_length for _, byte_length in descriptors.values()) > producer_byte_budget:
        raise StageContractError("bundle aggregate byte budget exceeded")
    return index, inventory, descriptors, trusted_evidence


def _fixture_mismatch_reason(name: str) -> str:
    if name in {"candidate-report.json", "comparator-report.json"} or name.endswith(".png"):
        return "bundle frame evidence does not match purpose-created fixture"
    if name == "paired-outcomes.json":
        return "paired outcome evidence does not match purpose-created fixture"
    if name == "paired-statistics.json":
        return "paired statistics do not match raw outcomes"
    return "PSD evidence does not match purpose-created fixture"


def _read_matching_bundle_artifact(
    name: str,
    inventory: dict[str, Path],
    descriptors: dict[str, tuple[str, int]],
    trusted_evidence: dict[str, bytes],
) -> bytes:
    expected_digest, expected_length = descriptors[name]
    maximum = MAX_JSON_BYTES if name in _JSON_ARTIFACT_NAMES else MAX_BUNDLE_ARTIFACT_BYTES
    try:
        data = read_bounded_file(inventory[name], min(maximum, expected_length))
    except (OSError, ValueError, TypeError) as exc:
        raise StageContractError("bundle artifact is unavailable") from exc
    if len(data) != expected_length:
        raise StageContractError("bundle artifact size mismatch")
    if sha256(data).hexdigest() != expected_digest:
        raise StageContractError("bundle artifact digest mismatch")
    if data != trusted_evidence[name]:
        raise StageContractError(_fixture_mismatch_reason(name))
    return data


def _json_values_match(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(_json_values_match(actual[key], value) for key, value in expected.items())  # type: ignore[index]
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_json_values_match(left, right) for left, right in zip(actual, expected, strict=True))  # type: ignore[arg-type]
    return actual == expected


def _verify_frame_png(data: bytes, expected_size: tuple[int, int], backend: object) -> None:
    try:
        _verify_output_png(data, expected_size)
        image_api = backend.Image  # type: ignore[attr-defined]
        with warnings.catch_warnings():
            warnings.simplefilter("error", image_api.DecompressionBombWarning)
            with image_api.open(io.BytesIO(data), formats=("PNG",)) as image:
                image.verify()
            with image_api.open(io.BytesIO(data), formats=("PNG",)) as image:
                image.load()
                if image.mode != "RGBA" or image.size != expected_size:
                    raise StageContractError("bundle frame PNG contract is invalid")
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError("bundle frame PNG contract is invalid") from exc


def _verify_arm_report(
    report: dict[str, object],
    arm: str,
    index_descriptors: dict[str, tuple[str, int]],
) -> tuple[int, int]:
    try:
        sequence_value = report["sequence"]
        rendering = report["rendering"]
        input_value = report["input"]
        frames = report["frames"]
        if not isinstance(sequence_value, dict) or not isinstance(rendering, dict) or not isinstance(input_value, dict) or not isinstance(frames, list):
            raise TypeError
        sequence_config = parse_gate_f_frame_sequence_config(
            {
                "format": "oneclick2d.gate-f-frame-sequence-config",
                "format_version": "0.1.0",
                "profile_id": sequence_value["profile_id"],
                "seed_u64": sequence_value["seed_u64"],
            }
        )
        expected_sequence = build_gate_f_frame_sequence(sequence_config)
        if (
            sequence_value.get("algorithm_id") != expected_sequence.algorithm_id
            or sequence_value.get("sha256") != expected_sequence.sha256
            or sequence_value.get("frame_count") != len(expected_sequence.frames)
            or len(frames) != len(expected_sequence.frames)
            or rendering.get("contract_id") != RENDERER_CONTRACT_ID
            or rendering.get("profile_id") != RENDERER_PROFILE_ID
        ):
            raise StageContractError("bundle arm frame contract is invalid")

        if arm == "candidate":
            canvas = rendering.get("canvas")
            if (
                report.get("format") != "oneclick2d.candidate-baseline-report"
                or report.get("format_version") != "0.2.0"
                or rendering.get("color_space") != "srgb"
                or rendering.get("input_alpha_mode") != "straight"
                or rendering.get("filter_space") != "premultiplied-srgb-u8"
                or not isinstance(canvas, list)
                or len(canvas) != 2
            ):
                raise StageContractError("bundle arm frame contract is invalid")
            width, height = canvas
            artifact_keys = {"name", "sha256", "byte_length"}
            artifact_name = lambda index, frame_id: f"candidate.{index:03d}.{frame_id}.png"
        elif arm == "comparator":
            if (
                report.get("format") != "oneclick2d.simple-cutout-comparator-report"
                or report.get("format_version") != "0.3.0"
                or input_value.get("mode") != "RGBA"
                or input_value.get("bit_depth") != 8
                or input_value.get("color_space") != "srgb"
                or input_value.get("alpha_mode") != "straight"
                or rendering.get("rgba_filter_space") != "premultiplied-srgb-u8"
            ):
                raise StageContractError("bundle arm frame contract is invalid")
            width, height = input_value["width"], input_value["height"]
            artifact_keys = {"name", "role", "media_type", "sha256", "byte_length"}
            artifact_name = lambda index, frame_id: f"frame.{index:03d}.{frame_id}.png"
        else:
            raise StageContractError("bundle arm frame contract is invalid")
        if (
            isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or not isinstance(height, int)
            or not 1 <= width <= 8192
            or not 1 <= height <= 8192
            or width * height > 40_000_000
        ):
            raise StageContractError("bundle arm frame canvas is invalid")

        for index, (frame, expected_frame) in enumerate(zip(frames, expected_sequence.frames, strict=True)):
            if not isinstance(frame, dict) or set(frame) != {"index", "id", "source", "parameters", "artifact"}:
                raise StageContractError("bundle arm frame contract is invalid")
            descriptor = frame["artifact"]
            if not isinstance(descriptor, dict) or set(descriptor) != artifact_keys:
                raise StageContractError("bundle arm frame descriptor is invalid")
            bundle_name = f"{arm}-frame-{index:03d}.png"
            indexed_digest, indexed_length = index_descriptors[bundle_name]
            if (
                type(frame["index"]) is not int
                or frame["index"] != index
                or frame["id"] != expected_frame.id
                or frame["source"] != expected_frame.source
                or not _json_values_match(frame["parameters"], expected_frame.parameter_document())
                or descriptor["name"] != artifact_name(index, expected_frame.id)
                or descriptor["sha256"] != indexed_digest
                or descriptor["byte_length"] != indexed_length
                or (arm == "comparator" and (descriptor["role"] != "simple_cutout_frame" or descriptor["media_type"] != "image/png"))
            ):
                raise StageContractError("bundle arm frame evidence is inconsistent")
        return width, height
    except StageContractError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise StageContractError("bundle arm frame contract is invalid") from exc


def _parse_pair_outcomes(value: list[object]) -> tuple[PairOutcome, ...]:
    rows: list[PairOutcome] = []
    asset_ids: set[str] = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {"asset_id", "outcome", "f_usable", "reason"}:
            raise StageContractError("paired outcome evidence is invalid")
        asset_id = row["asset_id"]
        outcome = row["outcome"]
        f_usable = row["f_usable"]
        reason = row["reason"]
        if (
            not isinstance(asset_id, str)
            or ID_RE.fullmatch(asset_id) is None
            or asset_id in asset_ids
            or not isinstance(outcome, str)
            or outcome not in _PAIR_OUTCOMES
            or type(f_usable) is not bool
            or not isinstance(reason, str)
            or reason not in _PAIR_REASONS
        ):
            raise StageContractError("paired outcome evidence is invalid")
        asset_ids.add(asset_id)
        rows.append(PairOutcome(asset_id, outcome, f_usable, reason))
    return tuple(rows)


def _verify_bundle(
    directory: Path,
    *,
    return_names: frozenset[str] = frozenset(),
) -> tuple[dict[str, object], dict[str, bytes]]:
    if not return_names <= _REQUIRED_ARTIFACT_NAMES or len(return_names) > 1:
        raise StageContractError("requested bundle artifact is invalid")
    index, inventory, index_descriptors, trusted_evidence = _read_verified_bundle_index(directory)
    json_evidence: dict[str, object] = {}
    for name in sorted(_JSON_ARTIFACT_NAMES):
        data = _read_matching_bundle_artifact(name, inventory, index_descriptors, trusted_evidence)
        json_evidence[name] = _load_json_bytes(data)
        del data
    candidate = json_evidence["candidate-report.json"]
    comparator = json_evidence["comparator-report.json"]
    outcomes_value = json_evidence["paired-outcomes.json"]
    stored_statistics = json_evidence["paired-statistics.json"]
    psd_report = json_evidence["psd-readback.json"]
    if (
        not isinstance(candidate, dict)
        or not isinstance(comparator, dict)
        or not isinstance(outcomes_value, list)
        or not isinstance(stored_statistics, dict)
        or not isinstance(psd_report, dict)
    ):
        raise StageContractError("bundle evidence shape is invalid")
    outcomes = _parse_pair_outcomes(outcomes_value)
    expected_outcomes = purpose_created_outcomes()
    if outcomes != expected_outcomes:
        raise StageContractError("paired outcome evidence does not match purpose-created fixture")
    statistics = evaluate_experiment(outcomes)
    if not _json_values_match(stored_statistics, statistics):
        raise StageContractError("paired statistics do not match raw outcomes")
    candidate_size = _verify_arm_report(candidate, "candidate", index_descriptors)
    comparator_size = _verify_arm_report(comparator, "comparator", index_descriptors)
    validate_arm_parity(arm_identity_from_report(candidate), arm_identity_from_report(comparator))
    parity = True
    try:
        backend = _load_pillow()
    except Exception as exc:
        raise StageContractError("bundle frame PNG verification is unavailable") from exc
    for arm, expected_size in (("candidate", candidate_size), ("comparator", comparator_size)):
        for frame_index in range(37):
            name = f"{arm}-frame-{frame_index:03d}.png"
            data = _read_matching_bundle_artifact(name, inventory, index_descriptors, trusted_evidence)
            _verify_frame_png(data, expected_size, backend)
            del data
    psd_data = _read_matching_bundle_artifact(
        "structural-preflight.psd",
        inventory,
        index_descriptors,
        trusted_evidence,
    )
    parsed_psd = parse_layered_psd(psd_data)
    del psd_data
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
    report = {
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
    returned_artifacts: dict[str, bytes] = {}
    if return_names:
        name = next(iter(return_names))
        returned_artifacts[name] = _read_matching_bundle_artifact(
            name,
            inventory,
            index_descriptors,
            trusted_evidence,
        )
    return report, returned_artifacts


def verify_bundle(directory: Path) -> dict[str, object]:
    report, _ = _verify_bundle(directory)
    return report


def verified_bundle_artifact_bytes(directory: Path, name: str, maximum: int) -> bytes:
    if (
        name not in _REQUIRED_ARTIFACT_NAMES
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum < 1
    ):
        raise StageContractError("requested bundle artifact is invalid")
    report, artifacts = _verify_bundle(directory, return_names=frozenset({name}))
    if report["local_technical_preflight_status"] != "LOCAL_TECHNICAL_PREFLIGHT_PASS":
        raise StageContractError("bundle is not accepted")
    data = artifacts[name]
    if len(data) > maximum:
        raise StageContractError("requested bundle artifact exceeds byte limit")
    return data
