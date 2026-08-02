from __future__ import annotations

import copy
import importlib
import json
import re
import struct
import tempfile
import unittest
import zlib
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

from spikes.gate_f_runner.contracts import ArtifactRef, StageContractError, StageStatus
from spikes.gate_f_runner.frame_sequence import FRAME_COUNT, MANDATORY_TICKS
from spikes.gate_f_runner.raster import PNG_SIGNATURE, _verify_output_png
from spikes.gate_f_runner.runner import PipelineRunner
from spikes.gate_f_runner.simple_cutout import (
    Affine,
    FROZEN_COMPARATOR_CONFIG_SHA256,
    _build_patches,
    _feather_mask,
    _head_affine,
    _parse_frozen_config,
    _patch_affine,
    _patch_is_active,
    _validate_normalized_report,
    build_simple_cutout_registry,
)
from spikes.gate_f_runner.runtime import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
COMPARATOR_CONFIG = ROOT / "examples" / "gate-f-simple-cutout-comparator" / "config.json"
REPORT_SCHEMA = ROOT / "schemas" / "gate-f-simple-cutout-comparator" / "v0.3" / "report.schema.json"


def _json_schema_type_matches(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    raise AssertionError("unsupported test schema type")


def _validate_json_schema(value: object, schema: dict[str, Any], root: dict[str, Any]) -> None:
    reference = schema.get("$ref")
    if reference is not None:
        prefix = "#/$defs/"
        if not isinstance(reference, str) or not reference.startswith(prefix):
            raise AssertionError("unsupported test schema reference")
        _validate_json_schema(value, root["$defs"][reference[len(prefix) :]], root)
        return
    expected_type = schema.get("type")
    if expected_type is not None and (not isinstance(expected_type, str) or not _json_schema_type_matches(value, expected_type)):
        raise AssertionError("schema type mismatch")
    if "const" in schema and value != schema["const"]:
        raise AssertionError("schema const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError("schema enum mismatch")
    if isinstance(value, str) and "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise AssertionError("schema pattern mismatch")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise AssertionError("schema minimum mismatch")
        if "maximum" in schema and value > schema["maximum"]:
            raise AssertionError("schema maximum mismatch")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise AssertionError("schema array length mismatch")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise AssertionError("schema unique-items mismatch")
        prefix_items = schema.get("prefixItems", [])
        if not isinstance(prefix_items, list):
            raise AssertionError("unsupported test prefix-items schema")
        for item, item_schema in zip(value, prefix_items):
            _validate_json_schema(item, item_schema, root)
        if schema.get("items") is False and len(value) > len(prefix_items):
            raise AssertionError("schema additional-item mismatch")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in value:
                _validate_json_schema(item, item_schema, root)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not set(required) <= set(value):
            raise AssertionError("schema required-field mismatch")
        if schema.get("additionalProperties") is False and not set(value) <= set(properties):
            raise AssertionError("schema additional-property mismatch")
        for key, item_schema in properties.items():
            if key in value:
                _validate_json_schema(value[key], item_schema, root)


def assert_v0_3_report_schema(value: object) -> None:
    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    _validate_json_schema(value, schema, schema)


def v0_3_report_schema_fixture() -> dict[str, object]:
    digest = "0" * 64
    parameters = {"head.yaw": 0, "head.pitch": 0, "eye.left.open": 1, "eye.right.open": 1, "mouth.open": 0}
    return {
        "format": "oneclick2d.simple-cutout-comparator-report",
        "format_version": "0.3.0",
        "scope": "disposable-gate-f-spike",
        "adapter_id": "simple-cutout.comparator.pillow.v1",
        "adapter_version": "0.3.0",
        "contract_id": "oc2d.spike.simple-cutout-comparator.v1",
        "config_sha256": digest,
        "stage_seed_u64": "00000000000000000042",
        "randomness_used": True,
        "sequence_scope": "mandatory-neutral-endpoint-combination-seeded-trajectory",
        "sequence": {
            "profile_id": "oc2d.spike.gate-f-frame-sequence.v1",
            "algorithm_id": "sha256-waypoint-linear-fixed-point.v1",
            "config_sha256": digest,
            "seed_u64": "00000000000000000042",
            "sha256": digest,
            "parameter_scale": 1000,
            "mandatory_frame_count": 12,
            "trajectory_frame_count": 25,
            "frame_count": 37,
        },
        "input": {
            "normalized_raster_sha256": digest,
            "normalization_report_sha256": digest,
            "normalization_finding_codes": [],
            "width": 101,
            "height": 103,
            "mode": "RGBA",
            "bit_depth": 8,
            "color_space": "srgb",
            "alpha_mode": "straight",
        },
        "parameters": {"order": ["head.yaw", "head.pitch", "eye.left.open", "eye.right.open", "mouth.open"], "side_convention": "character-anatomical"},
        "patches": [
            {
                "id": "head",
                "parameter_id": None,
                "character_side": "not-applicable",
                "percent_xyxy": [20, 5, 80, 60],
                "pixel_box_ltrb": [20, 5, 81, 62],
                "pivot_source_pixel_x2": [101, 67],
            },
            {
                "id": "eye.screen-left",
                "parameter_id": "eye.right.open",
                "character_side": "right",
                "percent_xyxy": [27, 25, 47, 40],
                "pixel_box_ltrb": [27, 25, 48, 42],
                "pivot_source_pixel_x2": [75, 67],
            },
            {
                "id": "eye.screen-right",
                "parameter_id": "eye.left.open",
                "character_side": "left",
                "percent_xyxy": [53, 25, 73, 40],
                "pixel_box_ltrb": [53, 25, 74, 42],
                "pivot_source_pixel_x2": [127, 67],
            },
            {
                "id": "mouth",
                "parameter_id": "mouth.open",
                "character_side": "not-applicable",
                "percent_xyxy": [40, 42, 60, 56],
                "pixel_box_ltrb": [40, 43, 61, 58],
                "pivot_source_pixel_x2": [101, 101],
            },
        ],
        "rendering": {
            "contract_id": "oc2d.spike.pillow-rgba-renderer.v1",
            "profile_id": "pillow-12.1.0-bilinear-premultiplied-srgb-source-over.v2",
            "coordinate_origin": "top-left",
            "rectangle_quantization": "floor-min-ceil-max-half-open",
            "pivot": "resolved-box-center",
            "feather": {"source_pixels": 2, "coverage": "linear-inward-min-edge-distance"},
            "head_transform_inheritance": "locally-active-child-patches",
            "identity_patch_policy": "skip-inactive-local-controls",
            "composition_order": ["head", "eye.screen-left", "eye.screen-right", "mouth"],
            "resampling": "pillow-bilinear",
            "rgba_filter_space": "premultiplied-srgb-u8",
            "alpha_composite": "porter-duff-source-over",
            "outside_rgba": [0, 0, 0, 0],
            "base_erased": False,
            "png": {"compress_level": 9, "optimize": False, "metadata": "srgb-only"},
        },
        "frames": [
            {
                "index": index,
                "id": f"frame.{index:03d}",
                "source": "mandatory" if index < 12 else "seeded-trajectory",
                "parameters": dict(parameters),
                "artifact": {"name": f"frame.{index:03d}.png", "role": "simple_cutout_frame", "media_type": "image/png", "sha256": digest, "byte_length": 1},
            }
            for index in range(37)
        ],
        "runtime": {"pillow": "12.1.0"},
        "finding_codes": [],
        "claims": {"semantic_decomposition_performed": False, "hidden_region_completion_performed": False, "psd_produced": False, "gate_f_feasibility_proven": False},
    }


def png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def purpose_created_asymmetric_png(width: int = 101, height: int = 103) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            alpha = 128 if x < 5 else 255
            row.extend(((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256, alpha))
        rows.append(bytes(row))
    return PNG_SIGNATURE + b"".join(
        (
            png_chunk(b"IHDR", ihdr),
            png_chunk(b"sRGB", b"\x00"),
            png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9)),
            png_chunk(b"IEND", b""),
        )
    )


def normalization_config_bytes() -> bytes:
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


def make_fixture(root: Path, source: bytes, *, comparator_output_files: int = 38, comparator_output_bytes: int = 8388608) -> tuple[Path, Path]:
    source_path = root / "source.png"
    source_path.write_bytes(source)
    configs = root / "configs"
    configs.mkdir()
    normalization_config = normalization_config_bytes()
    comparator_config = COMPARATOR_CONFIG.read_bytes()
    (configs / "normalize.json").write_bytes(normalization_config)
    (configs / "comparator.json").write_bytes(comparator_config)
    limits = {
        "max_wall_time_ms": 30000,
        "max_cpu_time_ms": 30000,
        "max_peak_ram_bytes": 536870912,
        "max_scratch_bytes": 1048576,
        "max_output_bytes": 8388608,
        "max_output_files": 2,
        "max_peak_vram_bytes": 0,
        "gpu_allowed": False,
    }
    comparator_limits = dict(limits)
    comparator_limits["max_output_files"] = comparator_output_files
    comparator_limits["max_output_bytes"] = comparator_output_bytes
    spec = {
        "$schema": str(ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
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
        "expected_result_role": "simple_cutout_comparator_report",
        "stages": [
            {
                "id": "stage.raster-normalize",
                "stage_type": "oc2d.spike.raster-normalize",
                "adapter_id": "raster.normalize.pillow.v1",
                "config_uri": "configs/normalize.json",
                "config_sha256": sha256(normalization_config).hexdigest(),
                "limits": limits,
            },
            {
                "id": "stage.simple-cutout-comparator",
                "stage_type": "oc2d.spike.simple-cutout-comparator",
                "adapter_id": "simple-cutout.comparator.pillow.v1",
                "config_uri": "configs/comparator.json",
                "config_sha256": sha256(comparator_config).hexdigest(),
                "limits": comparator_limits,
            },
        ],
    }
    spec_path = root / "run-spec.json"
    spec_path.write_bytes(canonical_json_bytes(spec))
    return spec_path, source_path


class SimpleCutoutPureTests(unittest.TestCase):
    def test_v0_3_report_schema_preserves_the_v0_2_contract(self) -> None:
        fixture = v0_3_report_schema_fixture()
        assert_v0_3_report_schema(fixture)

        reordered_ids = copy.deepcopy(fixture)
        reordered_ids["parameters"]["order"][0], reordered_ids["parameters"]["order"][1] = reordered_ids["parameters"]["order"][1], reordered_ids["parameters"]["order"][0]
        malformed_parameters = copy.deepcopy(fixture)
        del malformed_parameters["frames"][0]["parameters"]["head.yaw"]
        missing_digest = copy.deepcopy(fixture)
        del missing_digest["frames"][0]["artifact"]["sha256"]
        out_of_range_stage_seed = copy.deepcopy(fixture)
        out_of_range_stage_seed["stage_seed_u64"] = "18446744073709551616"
        out_of_range_sequence_seed = copy.deepcopy(fixture)
        out_of_range_sequence_seed["sequence"]["seed_u64"] = "18446744073709551616"
        mutations = {
            "reordered-parameter-ids": reordered_ids,
            "malformed-frame-parameters": malformed_parameters,
            "missing-artifact-digest": missing_digest,
            "out-of-range-stage-seed": out_of_range_stage_seed,
            "out-of-range-sequence-seed": out_of_range_sequence_seed,
        }
        for field, value in (("mandatory_frame_count", 11), ("trajectory_frame_count", 24), ("frame_count", 36)):
            wrong_count = copy.deepcopy(fixture)
            wrong_count["sequence"][field] = value
            mutations[f"wrong-{field}"] = wrong_count
        wrong_frame_cardinality = copy.deepcopy(fixture)
        wrong_frame_cardinality["frames"].pop()
        mutations["wrong-frame-cardinality"] = wrong_frame_cardinality
        for section in ("sequence", "input", "parameters", "runtime", "claims"):
            extra = copy.deepcopy(fixture)
            extra[section]["unexpected"] = True
            mutations[f"extra-{section}-field"] = extra
        extra_frame = copy.deepcopy(fixture)
        extra_frame["frames"][0]["unexpected"] = True
        mutations["extra-frame-field"] = extra_frame
        extra_artifact = copy.deepcopy(fixture)
        extra_artifact["frames"][0]["artifact"]["unexpected"] = True
        mutations["extra-artifact-field"] = extra_artifact
        empty_patches = copy.deepcopy(fixture)
        empty_patches["patches"] = []
        mutations["empty-patches-array"] = empty_patches
        empty_patch = copy.deepcopy(fixture)
        empty_patch["patches"][0] = {}
        mutations["empty-patch-object"] = empty_patch
        extra_patch = copy.deepcopy(fixture)
        extra_patch["patches"][0]["unexpected"] = True
        mutations["extra-patch-field"] = extra_patch
        extra_rendering = copy.deepcopy(fixture)
        extra_rendering["rendering"]["unexpected"] = True
        mutations["extra-rendering-field"] = extra_rendering
        extra_feather = copy.deepcopy(fixture)
        extra_feather["rendering"]["feather"]["unexpected"] = True
        mutations["extra-feather-field"] = extra_feather
        extra_png = copy.deepcopy(fixture)
        extra_png["rendering"]["png"]["unexpected"] = True
        mutations["extra-png-field"] = extra_png
        path_patch_property = copy.deepcopy(fixture)
        path_patch_property["patches"][0]["pixel_box_ltrb"] = "../../source.png"
        mutations["path-valued-patch-property"] = path_patch_property
        path_frame_name = copy.deepcopy(fixture)
        path_frame_name["frames"][0]["artifact"]["name"] = "../../frame.png"
        mutations["path-valued-frame-filename"] = path_frame_name
        for field in fixture["rendering"]:
            missing_rendering_field = copy.deepcopy(fixture)
            del missing_rendering_field["rendering"][field]
            mutations[f"missing-rendering-{field}"] = missing_rendering_field
        for label, changed in mutations.items():
            with self.subTest(label=label):
                with self.assertRaises(AssertionError):
                    assert_v0_3_report_schema(changed)

    def test_example_is_the_only_accepted_v0_3_config(self) -> None:
        data = COMPARATOR_CONFIG.read_bytes()
        sequence_config = _parse_frozen_config(data)
        self.assertEqual(FROZEN_COMPARATOR_CONFIG_SHA256, sha256(canonical_json_bytes(json.loads(data))).hexdigest())
        self.assertEqual("00000000000000000042", sequence_config.seed_u64)
        changed = json.loads(data)
        changed["frame_sequence"]["seed_u64"] = "00000000000000000043"
        with self.assertRaisesRegex(Exception, "frozen v1 profile"):
            _parse_frozen_config(canonical_json_bytes(changed))
        for historical_version in ("0.2.0", "0.1.0"):
            changed = json.loads(data)
            changed["format_version"] = historical_version
            with self.subTest(historical_version=historical_version):
                with self.assertRaisesRegex(StageContractError, "^unsupported simple-cutout config version$"):
                    _parse_frozen_config(canonical_json_bytes(changed))

    def test_odd_canvas_boxes_use_outward_half_open_rounding(self) -> None:
        patches = _build_patches(101, 103)
        self.assertEqual(
            [(20, 5, 81, 62), (27, 25, 48, 42), (53, 25, 74, 42), (40, 43, 61, 58)],
            [patch.box for patch in patches],
        )

    @unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
    def test_feather_is_two_source_pixels_with_linear_alpha(self) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            self.skipTest("functional comparator tests require locked Pillow 12.1.0")
        from spikes.gate_f_runner.raster import _load_pillow

        mask = _feather_mask((5, 5), _load_pillow())
        try:
            self.assertEqual([64, 191, 255, 191, 64], [mask.getpixel((x, 2)) for x in range(5)])
            self.assertEqual(64, mask.getpixel((0, 0)))
        finally:
            mask.close()

    def test_transform_signs_hierarchy_and_anatomical_mapping(self) -> None:
        patches = _build_patches(100, 100)
        positive = dict(zip(("head.yaw", "head.pitch", "eye.left.open", "eye.right.open", "mouth.open"), (15, 10, 1, 1, 0), strict=True))
        negative = dict(positive)
        negative["head.yaw"] = -15
        negative["head.pitch"] = -10
        positive_head = _head_affine(positive, 100, 100, patches[0].pivot[1])
        negative_head = _head_affine(negative, 100, 100, patches[0].pivot[1])
        center_x, center_y = patches[0].pivot
        positive_point = positive_head.map_point(center_x, center_y)
        negative_point = negative_head.map_point(center_x, center_y)
        self.assertGreater(positive_point[0], center_x)
        self.assertLess(positive_point[1], center_y)
        self.assertLess(negative_point[0], center_x)
        self.assertGreater(negative_point[1], center_y)

        left_closed = dict(positive)
        left_closed.update({"head.yaw": 0, "head.pitch": 0, "eye.left.open": 0})
        screen_left = _patch_affine(patches[1], left_closed, Affine.identity())
        screen_right = _patch_affine(patches[2], left_closed, Affine.identity())
        self.assertEqual(Affine.identity(), screen_left)
        self.assertNotEqual(Affine.identity(), screen_right)

        yaw_only = dict(positive)
        yaw_only.update({"head.pitch": 0, "eye.left.open": 1, "eye.right.open": 1, "mouth.open": 0})
        self.assertTrue(_patch_is_active(patches[0], yaw_only))
        self.assertFalse(any(_patch_is_active(patch, yaw_only) for patch in patches[1:]))

    def test_normalization_report_requires_complete_locked_contract(self) -> None:
        source = purpose_created_asymmetric_png()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        raster_path = root / "normalized.png"
        raster_path.write_bytes(source)
        report_value = {
            "format": "oneclick2d.raster-normalization-report",
            "format_version": "0.1.0",
            "scope": "disposable-gate-f-spike",
            "adapter_id": "raster.normalize.pillow.v1",
            "adapter_version": "0.1.0",
            "contract_id": "oc2d.spike.raster-normalize.v1",
            "input": {"format": "PNG", "media_type": "image/png", "width": 101, "height": 103, "mode": "RGBA", "bit_depth": 8, "frame_count": 1},
            "orientation": {"value": 1, "applied": False},
            "color_policy": "png-srgb-declared",
            "output": {"width": 101, "height": 103, "mode": "RGBA", "bit_depth": 8, "color_space": "srgb", "alpha_mode": "straight", "sha256": sha256(source).hexdigest(), "byte_length": len(source)},
            "metadata_removed": ["exif", "icc", "text", "comment", "dpi", "xmp"],
            "finding_codes": [],
            "runtime": {"pillow": "12.1.0"},
            "gate_f_feasibility_proven": False,
        }
        report_path = root / "report.json"
        report_data = canonical_json_bytes(report_value)
        report_path.write_bytes(report_data)
        raster = ArtifactRef("normalized_raster", "image/png", raster_path, "normalized.png", sha256(source).hexdigest(), len(source))
        report = ArtifactRef("raster_normalization_report", "application/vnd.oneclick2d.raster-normalization-report+json", report_path, "report.json", sha256(report_data).hexdigest(), len(report_data))
        _, width, height = _validate_normalized_report(raster, report)
        self.assertEqual((101, 103), (width, height))

        for mutation in ("missing-contract", "duplicate-finding", "extra-field"):
            changed = json.loads(json.dumps(report_value))
            if mutation == "missing-contract":
                del changed["contract_id"]
            elif mutation == "duplicate-finding":
                changed["finding_codes"] = ["RASTER_UNTAGGED_ASSUMED_SRGB", "RASTER_UNTAGGED_ASSUMED_SRGB"]
            else:
                changed["unexpected"] = True
            report_path.write_bytes(canonical_json_bytes(changed))
            with self.assertRaises(StageContractError, msg=mutation):
                _validate_normalized_report(raster, report)

    def test_registry_construction_does_not_import_pillow(self) -> None:
        original_import = __import__

        def guarded_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            registry = build_simple_cutout_registry()
        self.assertIsNotNone(registry.resolve("simple-cutout.comparator.pillow.v1"))


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class SimpleCutoutAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("functional comparator tests require locked Pillow 12.1.0")

    def _run(self, *, run_id: str, output_files: int = 38, output_bytes: int = 8388608) -> tuple[StageStatus, dict[str, object], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        spec_path, source_path = make_fixture(
            root,
            purpose_created_asymmetric_png(),
            comparator_output_files=output_files,
            comparator_output_bytes=output_bytes,
        )
        status, manifest_path = PipelineRunner(build_simple_cutout_registry(), root / "workspace").run(
            spec_path=spec_path,
            source_path=source_path,
            run_id=run_id,
            source_revision="source.test",
            build_id="build.test",
        )
        return status, json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path

    def test_two_stage_comparator_is_deterministic_and_neutral_is_preserved(self) -> None:
        first_status, first, first_path = self._run(run_id="run.comparator-first")
        second_status, second, second_path = self._run(run_id="run.comparator-second")
        self.assertEqual(StageStatus.SUCCEEDED, first_status)
        self.assertEqual(StageStatus.SUCCEEDED, second_status)
        self.assertEqual(["stage.raster-normalize", "stage.simple-cutout-comparator"], [stage["id"] for stage in first["stages"]])
        first_outputs = first["stages"][1]["outputs"]
        second_outputs = second["stages"][1]["outputs"]
        self.assertEqual(FRAME_COUNT + 1, len(first_outputs))
        self.assertEqual([item["sha256"] for item in first_outputs], [item["sha256"] for item in second_outputs])

        normalized = next(item for item in first["stages"][0]["outputs"] if item["role"] == "normalized_raster")
        neutral = first_outputs[0]
        self.assertEqual(normalized["sha256"], neutral["sha256"])
        for artifact in first_outputs[:-1]:
            _verify_output_png((first_path.parent / artifact["uri"]).read_bytes(), (101, 103))

        report = json.loads((first_path.parent / first["result"]["uri"]).read_text(encoding="utf-8"))
        assert_v0_3_report_schema(report)
        self.assertEqual(normalized["sha256"], report["input"]["normalized_raster_sha256"])
        mandatory_ids = [frame_id for frame_id, _ in MANDATORY_TICKS]
        self.assertEqual(mandatory_ids, [frame["id"] for frame in report["frames"][:12]])
        self.assertEqual([f"trajectory.{index:03d}" for index in range(25)], [frame["id"] for frame in report["frames"][12:]])
        self.assertEqual("2b9c10df115be77ff3eb17807329a016d1350a3d387ea47bdaab2dd409b0ea8c", report["sequence"]["sha256"])
        self.assertEqual(FRAME_COUNT, report["sequence"]["frame_count"])
        self.assertFalse(any(report["claims"].values()))
        self.assertTrue(report["randomness_used"])

        from io import BytesIO
        from PIL import Image, ImageChops

        left_closed_bytes = (first_path.parent / first_outputs[5]["uri"]).read_bytes()
        right_closed_bytes = (first_path.parent / first_outputs[6]["uri"]).read_bytes()
        normalized_bytes = (first_path.parent / normalized["uri"]).read_bytes()
        with Image.open(BytesIO(normalized_bytes)) as base, Image.open(BytesIO(left_closed_bytes)) as left, Image.open(BytesIO(right_closed_bytes)) as right:
            left_box = ImageChops.difference(base, left).getbbox(alpha_only=False)
            right_box = ImageChops.difference(base, right).getbbox(alpha_only=False)
        self.assertIsNotNone(left_box)
        self.assertIsNotNone(right_box)
        self.assertGreater((left_box[0] + left_box[2]) / 2, 50)  # type: ignore[index]
        self.assertLess((right_box[0] + right_box[2]) / 2, 50)  # type: ignore[index]

    def test_output_file_limit_leaves_no_comparator_commit(self) -> None:
        status, manifest, manifest_path = self._run(run_id="run.comparator-limit", output_files=37)
        self.assertEqual(StageStatus.FAILED, status)
        self.assertEqual("STAGE_RESOURCE_LIMIT_EXCEEDED", manifest["terminal_reason_code"])
        self.assertFalse((manifest_path.parent / "committed" / "stage.simple-cutout-comparator").exists())
        self.assertTrue((manifest_path.parent / "committed" / "stage.raster-normalize").exists())


if __name__ == "__main__":
    unittest.main()
