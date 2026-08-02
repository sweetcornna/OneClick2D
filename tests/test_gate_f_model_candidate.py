from __future__ import annotations

import copy
import contextlib
import importlib
import io
import json
import math
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spikes.gate_f_runner.__main__ import main
from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.model_candidate import (
    ACTIVATION_BLOCKERS,
    CONFIG_PATH,
    OUTPUT_DIRECTORY,
    ONTOLOGY_PATH,
    SOURCE_VISIBLE_ALPHA_THRESHOLD,
    _ontology,
    _source_region_masks,
    generate_model_candidate_preflight,
    load_model_candidate_preflight_report,
)
from spikes.gate_f_runner.model_motion_draft import (
    CANVAS_SIZE,
    DRAW_ORDER,
    _apply_subject_matte,
    _subject_matte,
    _tighten_alpha,
    generate_model_motion_draft,
)
from spikes.gate_f_runner.model_worker import MODEL_PART_NAMES
from spikes.gate_f_runner.rendering import (
    Affine,
    PREMULTIPLIED_RENDERER_PROFILE_ID,
    RenderLayer,
    render_rgba_layers,
)
from spikes.gate_f_runner.runtime import canonical_json_bytes
from spikes.gate_f_runner.runtime import sha256_bytes
from tests.test_gate_f_model_motion_draft import persist_trusted_model_source, write_sparse_motion_fixture
from tests.test_gate_f_model_worker import _valid_entrypoint_attestation_summary
from tests.test_gate_f_model_workbench import refresh_model_inventory


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = ROOT / "schemas" / "gate-f-model-candidate" / "v0.2" / "report.schema.json"
PREFLIGHT_REPORT_SCHEMA = ROOT / "schemas" / "gate-f-model-candidate" / "v0.2" / "preflight-report.schema.json"


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
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    raise AssertionError("unsupported test schema type")


def _schema_equal(left: object, right: object) -> bool:
    return canonical_json_bytes(left) == canonical_json_bytes(right)


def _validate_json_schema(value: object, schema: dict[str, object], root: dict[str, object]) -> None:
    reference = schema.get("$ref")
    if reference is not None:
        prefix = "#/$defs/"
        if not isinstance(reference, str) or not reference.startswith(prefix):
            raise AssertionError("unsupported test schema reference")
        definitions = root.get("$defs")
        if not isinstance(definitions, dict):
            raise AssertionError("schema definitions are unavailable")
        target = definitions.get(reference[len(prefix) :])
        if not isinstance(target, dict):
            raise AssertionError("schema definition is unavailable")
        _validate_json_schema(value, target, root)
    expected_type = schema.get("type")
    if expected_type is not None and (not isinstance(expected_type, str) or not _json_schema_type_matches(value, expected_type)):
        raise AssertionError("schema type mismatch")
    if "const" in schema and not _schema_equal(value, schema["const"]):
        raise AssertionError("schema const mismatch")
    enum = schema.get("enum")
    if isinstance(enum, list) and not any(_schema_equal(value, item) for item in enum):
        raise AssertionError("schema enum mismatch")
    pattern = schema.get("pattern")
    if isinstance(value, str) and isinstance(pattern, str) and re.search(pattern, value) is None:
        raise AssertionError("schema pattern mismatch")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise AssertionError("schema finite-number mismatch")
        if "minimum" in schema and value < schema["minimum"]:
            raise AssertionError("schema minimum mismatch")
        if "maximum" in schema and value > schema["maximum"]:
            raise AssertionError("schema maximum mismatch")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise AssertionError("schema array length mismatch")
        if schema.get("uniqueItems") and len({canonical_json_bytes(item) for item in value}) != len(value):
            raise AssertionError("schema unique-items mismatch")
        prefix_items = schema.get("prefixItems", [])
        if isinstance(prefix_items, list):
            for item, item_schema in zip(value, prefix_items):
                if not isinstance(item_schema, dict):
                    raise AssertionError("unsupported prefix item schema")
                _validate_json_schema(item, item_schema, root)
        item_schema = schema.get("items")
        if item_schema is False and len(value) > len(prefix_items):
            raise AssertionError("schema extra array item mismatch")
        if isinstance(item_schema, dict):
            for item in value[len(prefix_items) :]:
                _validate_json_schema(item, item_schema, root)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list) or not set(required) <= set(value):
            raise AssertionError("schema required-field mismatch")
        if schema.get("additionalProperties") is False and not set(value) <= set(properties):
            raise AssertionError("schema additional-property mismatch")
        for key, item_schema in properties.items():
            if key in value and isinstance(item_schema, dict):
                _validate_json_schema(value[key], item_schema, root)


def assert_model_candidate_report_schema(value: object) -> None:
    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise AssertionError("candidate report schema is invalid")
    _validate_json_schema(value, schema, schema)


def assert_model_candidate_preflight_report_schema(value: object) -> None:
    schema = json.loads(PREFLIGHT_REPORT_SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise AssertionError("candidate preflight report schema is invalid")
    _validate_json_schema(value, schema, schema)


def _rewrite_candidate_and_preflight(
    directory: Path,
    candidate: dict[str, object],
    *,
    preserve_numeric_types: bool = False,
) -> None:
    candidate_data = (
        json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if preserve_numeric_types
        else canonical_json_bytes(candidate)
    )
    (directory / "candidate-report.json").write_bytes(candidate_data)
    preflight_path = directory / "preflight-report.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    descriptor = preflight["artifacts"]["candidate_report"]
    descriptor["byte_length"] = len(candidate_data)
    descriptor["sha256"] = sha256_bytes(candidate_data)
    preflight_path.write_bytes(canonical_json_bytes(preflight))


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFModelCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("model candidate requires locked Pillow 12.1.0")

    def _fixture(self, root: Path, name: str = "run.model-candidate") -> Path:
        run_dir = root / name
        run_dir.mkdir()
        write_sparse_motion_fixture(run_dir)
        generate_model_motion_draft(run_dir)
        return run_dir

    def test_named_premultiplied_profile_preserves_transparent_edge_color(self) -> None:
        from fractions import Fraction
        from PIL import Image

        backend = type("Backend", (), {"Image": Image})
        with Image.new("RGBA", (2, 1), (0, 0, 0, 0)) as layer_image, Image.new("RGBA", (3, 1), (0, 0, 0, 0)) as base:
            layer_image.putpixel((0, 0), (255, 255, 255, 255))
            layer = RenderLayer(
                layer_image,
                (0, 0, 2, 1),
                Affine(Fraction(2), Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(0)),
            )
            rendered = render_rgba_layers(
                base,
                [layer],
                backend,
                None,
                profile_id=PREMULTIPLIED_RENDERER_PROFILE_ID,
            )
        try:
            edge = rendered.getpixel((1, 0))
            self.assertEqual((255, 255, 255), edge[:3])
            self.assertGreater(edge[3], 0)
        finally:
            rendered.close()

    def test_generates_and_reloads_single_item_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory))

            report_path, report = generate_model_candidate_preflight(run_dir)
            reloaded = load_model_candidate_preflight_report(run_dir)

            self.assertEqual(run_dir / OUTPUT_DIRECTORY / "preflight-report.json", report_path)
            self.assertEqual(report, reloaded)
            assert_model_candidate_preflight_report_schema(report)
            self.assertEqual("LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED", report["local_status"])
            self.assertEqual("GATE_F_NOT_EVALUATED", report["gate_f_status"])
            self.assertTrue(report["arm_parity"])
            self.assertFalse(report["ready_for_activated_scoring"])
            self.assertFalse(report["claims"]["review_ballots_present"])
            self.assertFalse(report["claims"]["paired_outcomes_present"])
            self.assertFalse(report["claims"]["f_usable_evaluated"])

            candidate = json.loads((report_path.parent / "candidate-report.json").read_text(encoding="utf-8"))
            assert_model_candidate_report_schema(candidate)
            self.assertEqual(16, len(candidate["ontology"]))
            self.assertEqual(16, len({item["slot_id"] for item in candidate["ontology"]}))
            self.assertLessEqual(
                {item["status"] for item in candidate["ontology"]},
                {"PRESENT", "NOT_APPLICABLE", "LOW_CONFIDENCE"},
            )
            self.assertEqual("review_required", candidate["quality"]["status"])
            self.assertEqual("not_evaluated", candidate["validation"]["dynamic_frame_source_pixel_protection"])
            self.assertFalse(candidate["claims"]["activated_gate_f_scoring_ready"])
            self.assertFalse(candidate["claims"]["oc2d_produced"])
            self.assertFalse(candidate["claims"]["moc3_produced"])
            self.assertEqual(0, candidate["validation"]["visible_layer_rgb_mismatch_count"])
            self.assertEqual(0, candidate["validation"]["neutral_reconstruction_rgb_mismatch_count"])

            by_slot = {item["slot_id"]: item for item in candidate["ontology"]}
            self.assertGreater(by_slot["oc2d.eye.left"]["visible_pixel_count"], 0)
            self.assertGreater(by_slot["oc2d.eye.right"]["visible_pixel_count"], 0)
            self.assertEqual("left", by_slot["oc2d.eye.left"]["side"])
            self.assertEqual("right", by_slot["oc2d.eye.right"]["side"])
            self.assertEqual("NOT_APPLICABLE", by_slot["oc2d.hair.side"]["status"])
            self.assertEqual("PRESENT", by_slot["oc2d.torso"]["status"])
            self.assertEqual(
                sum(item["status"] == "PRESENT" for item in candidate["ontology"] if item["applicability"] == "required"),
                candidate["required_slot_facts"]["present"],
            )
            self.assertEqual(
                sum(item["applicability"] == "required" for item in candidate["ontology"]),
                candidate["required_slot_facts"]["required"],
            )
            self.assertFalse(candidate["required_slot_facts"]["single_item_gate_threshold_evaluated"])

            names = {path.name for path in report_path.parent.iterdir()}
            self.assertIn("mask.source-visible.png", names)
            self.assertIn("mask.semantic-union.png", names)
            self.assertIn("mask.deterministic-underpaint.face.png", names)
            self.assertEqual(37, len([name for name in names if name.startswith("candidate-frame-")]))
            self.assertEqual(37, len([name for name in names if name.startswith("comparator-frame-")]))
            self.assertEqual(107, len(names))
            self.assertFalse(any(name.endswith((".oc2d", ".moc3")) for name in names))

            invalid_reports: dict[str, dict[str, object]] = {}
            empty_profile = copy.deepcopy(candidate)
            empty_profile["profile"] = {}
            invalid_reports["empty-profile"] = empty_profile
            missing_mask_hash = copy.deepcopy(candidate)
            del missing_mask_hash["provenance"]["source_visible"]["sha256"]
            invalid_reports["missing-provenance-mask-hash"] = missing_mask_hash
            string_geometry = copy.deepcopy(candidate)
            string_geometry["geometry"][0] = "invalid"
            invalid_reports["string-geometry-entry"] = string_geometry
            missing_frame_hash = copy.deepcopy(candidate)
            del missing_frame_hash["frames"][0]["artifact"]["sha256"]
            invalid_reports["frame-artifact-without-sha256"] = missing_frame_hash
            duplicate_slot = copy.deepcopy(candidate)
            duplicate_slot["ontology"][1] = copy.deepcopy(duplicate_slot["ontology"][0])
            invalid_reports["duplicate-ontology-slot"] = duplicate_slot
            boolean_frame_index = copy.deepcopy(candidate)
            boolean_frame_index["frames"][0]["index"] = False
            invalid_reports["boolean-frame-index"] = boolean_frame_index
            incorrect_motion_profile = copy.deepcopy(candidate)
            incorrect_motion_profile["profile"]["motion_profile_id"] = "incorrect.motion.profile"
            invalid_reports["incorrect-motion-profile"] = incorrect_motion_profile
            ballot_field = copy.deepcopy(candidate)
            ballot_field["review_ballot"] = {}
            invalid_reports["added-ballot-field"] = ballot_field
            paired_outcome_field = copy.deepcopy(candidate)
            paired_outcome_field["paired_outcome"] = {}
            invalid_reports["added-paired-outcome-field"] = paired_outcome_field
            for label, changed in invalid_reports.items():
                with self.subTest(schema_mutation=label), self.assertRaises(AssertionError):
                    assert_model_candidate_report_schema(changed)

    def test_preflight_activation_blockers_are_closed_and_bounded(self) -> None:
        expected = [
            "GATE_0_NOT_APPROVED",
            "D_003_NOT_CLOSED",
            "D_009_NOT_CLOSED",
            "EXTERNAL_PSD_EDITOR_NOT_EVALUATED",
            "TWENTY_ITEM_PROTOCOL_NOT_RUN",
        ]
        self.assertEqual(expected, list(ACTIVATION_BLOCKERS))
        schema = json.loads(PREFLIGHT_REPORT_SCHEMA.read_text(encoding="utf-8"))
        blocker_schema = schema["properties"]["activation_blockers"]
        _validate_json_schema(expected, blocker_schema, schema)
        invalid = {
            "unknown-code": ["UNKNOWN_BLOCKER", *expected[1:]],
            "path-like-string": ["../private/preflight-report.json", *expected[1:]],
            "extra-entry": [*expected, expected[0]],
        }
        for label, blockers in invalid.items():
            with self.subTest(label=label), self.assertRaises(AssertionError):
                _validate_json_schema(blockers, blocker_schema, schema)

    def test_rejects_type_changed_canonical_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory), "run.typed-report-tamper")
            report_path, _ = generate_model_candidate_preflight(run_dir)
            output = report_path.parent
            original_candidate = (output / "candidate-report.json").read_bytes()
            original_preflight = (output / "preflight-report.json").read_bytes()

            candidate = json.loads(original_candidate)
            candidate["claims"]["activated_gate_f_scoring_ready"] = 0
            _rewrite_candidate_and_preflight(output, candidate)
            with self.assertRaisesRegex(StageContractError, "candidate report"):
                load_model_candidate_preflight_report(run_dir)

            (output / "candidate-report.json").write_bytes(original_candidate)
            (output / "preflight-report.json").write_bytes(original_preflight)
            candidate = json.loads(original_candidate)
            candidate["frames"][0]["index"] = 0.0
            _rewrite_candidate_and_preflight(output, candidate, preserve_numeric_types=True)
            with self.assertRaisesRegex(StageContractError, "candidate report"):
                load_model_candidate_preflight_report(run_dir)

            (output / "candidate-report.json").write_bytes(original_candidate)
            preflight = json.loads(original_preflight)
            preflight["arm_parity"] = 1
            (output / "preflight-report.json").write_bytes(canonical_json_bytes(preflight))
            with self.assertRaisesRegex(StageContractError, "preflight report"):
                load_model_candidate_preflight_report(run_dir)

    def test_reload_rechecks_active_profile_and_neutral_fidelity(self) -> None:
        from spikes.gate_f_runner.model_workbench import load_model_workbench_report

        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory), "run.reload-profile")
            generate_model_candidate_preflight(run_dir)
            workbench = load_model_workbench_report(run_dir)
            workbench["model"]["identity"]["profile_id"] = "see-through.v3.nf4.1280.wsl2.v2"
            with patch(
                "spikes.gate_f_runner.model_workbench.load_model_workbench_report",
                return_value=workbench,
            ):
                with self.assertRaisesRegex(StageContractError, "active model profile"):
                    load_model_candidate_preflight_report(run_dir)

        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory), "run.reload-fidelity")
            generate_model_candidate_preflight(run_dir)
            workbench = load_model_workbench_report(run_dir)
            workbench["quality"]["neutral_fidelity"]["status"] = "review_required"
            with patch(
                "spikes.gate_f_runner.model_workbench.load_model_workbench_report",
                return_value=workbench,
            ):
                with self.assertRaisesRegex(StageContractError, "fidelity-passing"):
                    load_model_candidate_preflight_report(run_dir)

    def test_source_visible_excludes_model_completion_on_transparent_source(self) -> None:
        from PIL import Image

        with Image.new("L", (3, 1)) as union, Image.new("L", (3, 1)) as source_alpha:
            union.putdata([255, 255, 0])
            source_alpha.putdata([255, 0, 255])
            protected, exposed, omission = _source_region_masks(union, source_alpha)
        try:
            self.assertEqual([255, 0, 0], list(protected.get_flattened_data()))
            self.assertEqual([0, 255, 0], list(exposed.get_flattened_data()))
            self.assertEqual([0, 0, 255], list(omission.get_flattened_data()))
        finally:
            protected.close()
            exposed.close()
            omission.close()

    def test_v5_source_alpha_threshold_accepts_generated_rgb_only_through_31(self) -> None:
        from PIL import Image

        def prepare(run_dir: Path, *, mismatch_at_32: bool) -> tuple[list[tuple[int, int]], list[tuple[int, int, int]]]:
            write_sparse_motion_fixture(run_dir)
            image_root = run_dir / "model-output" / "input" / "input"
            result = json.loads((run_dir / "model-result.json").read_text(encoding="utf-8"))
            mouth_index = DRAW_ORDER.index("mouth")
            x = 90 + (mouth_index % 6) * 176 + 10
            y = 90 + (mouth_index // 6) * 190 + 10
            points = [(x + offset, y) for offset in range(3)]
            generated_rgb = [(201, 11, 21), (202, 12, 22), (203, 13, 23)]
            with Image.open(image_root / "src_img.png", formats=("PNG",)) as stored:
                stored.load()
                source = stored.convert("RGBA")
            with Image.open(image_root / "mouth.png", formats=("PNG",)) as stored:
                stored.load()
                mouth = stored.convert("RGBA")
            try:
                source_rgb = source.getpixel(points[0])[:3]
                for point, alpha in zip(points, (0, 31, 32), strict=True):
                    source.putpixel(point, (*source_rgb, alpha))
                mouth.putpixel(points[0], (*generated_rgb[0], 255))
                mouth.putpixel(points[1], (*generated_rgb[1], 255))
                protected_rgb = generated_rgb[2] if mismatch_at_32 else source_rgb
                mouth.putpixel(points[2], (*protected_rgb, 255))
                trusted_source = persist_trusted_model_source(run_dir, source)
                (image_root / "src_img.png").write_bytes(trusted_source)
                result["source_sha256"] = sha256_bytes(trusted_source)
                result["entrypoint_attestation"] = _valid_entrypoint_attestation_summary(
                    result["source_sha256"]
                )
                mouth.save(image_root / "mouth.png", format="PNG")
            finally:
                source.close()
                mouth.close()
            refresh_model_inventory(run_dir, result, publish_result=True)
            generate_model_motion_draft(run_dir)
            return points, generated_rgb

        self.assertEqual(31, SOURCE_VISIBLE_ALPHA_THRESHOLD)
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.alpha-threshold-valid"
            run_dir.mkdir()
            points, _ = prepare(run_dir, mismatch_at_32=False)
            report_path, _ = generate_model_candidate_preflight(run_dir)
            candidate = json.loads((report_path.parent / "candidate-report.json").read_text(encoding="utf-8"))
            self.assertEqual(0, candidate["validation"]["visible_layer_rgb_mismatch_count"])
            with Image.open(report_path.parent / "mask.source-visible.png", formats=("PNG",)) as source_visible, Image.open(
                report_path.parent / "mask.source-transparent-exposed.png", formats=("PNG",)
            ) as exposed:
                self.assertEqual([0, 0, 255], [source_visible.getpixel(point) for point in points])
                self.assertEqual([255, 255, 0], [exposed.getpixel(point) for point in points])

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.alpha-threshold-mismatch"
            run_dir.mkdir()
            prepare(run_dir, mismatch_at_32=True)
            with self.assertRaisesRegex(StageContractError, "source-preservation evidence"):
                generate_model_candidate_preflight(run_dir)

    def test_anatomical_side_splits_screen_halves(self) -> None:
        from PIL import Image

        masks = {
            semantic: Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), 0)
            for semantic in MODEL_PART_NAMES
        }
        try:
            for semantic in ("eyewhite", "irides", "eyelash", "eyebrow"):
                masks[semantic].close()
                mask = Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), 0)
                mask.putpixel((10, 10), 255)
                masks[semantic] = mask
            union = Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), 0)
            union.putpixel((10, 10), 255)
            try:
                by_slot = {item["slot_id"]: item for item in _ontology(masks, union)}
            finally:
                union.close()
            self.assertEqual(1, by_slot["oc2d.eye.right"]["visible_pixel_count"])
            self.assertEqual(0, by_slot["oc2d.eye.left"]["visible_pixel_count"])
            self.assertEqual("PRESENT", by_slot["oc2d.eye.right"]["status"])
            self.assertEqual("LOW_CONFIDENCE", by_slot["oc2d.eye.left"]["status"])
        finally:
            for mask in masks.values():
                mask.close()

    def test_rejects_symlinked_run_directory_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = self._fixture(root, "run.actual-candidate")
            linked = root / "run.candidate-link"
            try:
                linked.symlink_to(actual, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with self.assertRaisesRegex(StageContractError, "run directory"):
                generate_model_candidate_preflight(linked)

    def test_requires_active_fidelity_passing_model_with_motion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            no_motion = root / "run.no-motion"
            no_motion.mkdir()
            write_sparse_motion_fixture(no_motion)
            with self.assertRaisesRegex(StageContractError, "motion draft"):
                generate_model_candidate_preflight(no_motion)

            legacy = root / "run.legacy-model"
            legacy.mkdir()
            write_sparse_motion_fixture(legacy)
            generate_model_motion_draft(legacy)
            from spikes.gate_f_runner.model_workbench import load_model_workbench_report

            workbench = load_model_workbench_report(legacy)
            workbench["model"]["identity"]["profile_id"] = "see-through.v3.nf4.1280.wsl2.v2"
            with patch(
                "spikes.gate_f_runner.model_workbench.load_model_workbench_report",
                return_value=workbench,
            ):
                with self.assertRaisesRegex(StageContractError, "active model profile"):
                    generate_model_candidate_preflight(legacy)

            failed_fidelity = root / "run.failed-fidelity"
            failed_fidelity.mkdir()
            write_sparse_motion_fixture(failed_fidelity)
            generate_model_motion_draft(failed_fidelity)
            workbench = load_model_workbench_report(failed_fidelity)
            workbench["quality"]["neutral_fidelity"]["status"] = "review_required"
            with patch(
                "spikes.gate_f_runner.model_workbench.load_model_workbench_report",
                return_value=workbench,
            ):
                with self.assertRaisesRegex(StageContractError, "fidelity-passing"):
                    generate_model_candidate_preflight(failed_fidelity)

    def test_rejects_tampering_extra_files_symlinks_and_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._fixture(root)
            report_path, _ = generate_model_candidate_preflight(run_dir)
            with self.assertRaisesRegex(StageContractError, "already exists"):
                generate_model_candidate_preflight(run_dir)

            candidate_path = report_path.parent / "candidate-report.json"
            original = candidate_path.read_bytes()
            candidate_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            with self.assertRaises((StageContractError, ValueError)):
                load_model_candidate_preflight_report(run_dir)
            candidate_path.write_bytes(original)

            extra = report_path.parent / "extra.bin"
            extra.write_bytes(b"x")
            with self.assertRaisesRegex(StageContractError, "inventory"):
                load_model_candidate_preflight_report(run_dir)
            extra.unlink()

            nonregular = report_path.parent / "extra-directory"
            nonregular.mkdir()
            with self.assertRaisesRegex(StageContractError, "inventory"):
                load_model_candidate_preflight_report(run_dir)
            nonregular.rmdir()

            frame = report_path.parent / "candidate-frame-000.png"
            frame.unlink()
            try:
                frame.symlink_to(run_dir / "motion-draft" / "frame.000.neutral.png")
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaisesRegex(StageContractError, "inventory"):
                load_model_candidate_preflight_report(run_dir)

    def test_rejects_frames_masks_reports_and_upstream_evidence_tampering(self) -> None:
        targets = (
            ("candidate-frame-001.png", "frame"),
            ("comparator-frame-001.png", "comparator frame"),
            ("mask.source-visible.png", "provenance mask"),
            ("preflight-report.json", "preflight report"),
        )
        for index, (name, expected_error) in enumerate(targets):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                run_dir = self._fixture(Path(directory), f"run.output-tamper-{index}")
                report_path, _ = generate_model_candidate_preflight(run_dir)
                target = report_path.parent / name
                data = target.read_bytes()
                target.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                if name == "preflight-report.json":
                    with self.assertRaises((StageContractError, ValueError)):
                        load_model_candidate_preflight_report(run_dir)
                else:
                    with self.assertRaisesRegex((StageContractError, ValueError), expected_error):
                        load_model_candidate_preflight_report(run_dir)

        upstream_targets = (
            "model-output/input/input/face.png",
            "motion-draft/frame.001.yaw.min.png",
            "motion-draft/layer.face.png",
        )
        for index, relative in enumerate(upstream_targets):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                run_dir = self._fixture(Path(directory), f"run.upstream-tamper-{index}")
                generate_model_candidate_preflight(run_dir)
                target = run_dir / relative
                data = target.read_bytes()
                target.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                with self.assertRaises((StageContractError, ValueError)):
                    load_model_candidate_preflight_report(run_dir)

    def test_rejects_coherent_motion_frame_and_lineage_forgeries(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory), "run.coherent-frame-forgery")
            report_path, _ = generate_model_candidate_preflight(run_dir)
            motion_path = run_dir / "motion-draft" / "motion-report.json"
            motion = json.loads(motion_path.read_text(encoding="utf-8"))
            frame_path = run_dir / motion["frames"][1]["artifact"]["uri"]
            stream = io.BytesIO()
            with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (17, 29, 43, 255)) as forged:
                pnginfo = __import__("PIL.PngImagePlugin", fromlist=["PngInfo"]).PngInfo()
                pnginfo.add(b"sRGB", b"\x00")
                forged.save(
                    stream,
                    format="PNG",
                    optimize=False,
                    compress_level=9,
                    pnginfo=pnginfo,
                    icc_profile=None,
                    exif=b"",
                )
            forged_data = stream.getvalue()
            frame_path.write_bytes(forged_data)
            motion["frames"][1]["artifact"]["byte_length"] = len(forged_data)
            motion["frames"][1]["artifact"]["sha256"] = sha256_bytes(forged_data)
            motion_path.write_bytes(canonical_json_bytes(motion))

            output = report_path.parent
            candidate = json.loads((output / "candidate-report.json").read_text(encoding="utf-8"))
            candidate_frame_path = output / "candidate-frame-001.png"
            candidate_frame_path.write_bytes(forged_data)
            candidate["frames"][1]["artifact"]["byte_length"] = len(forged_data)
            candidate["frames"][1]["artifact"]["sha256"] = sha256_bytes(forged_data)
            _rewrite_candidate_and_preflight(output, candidate)
            with self.assertRaisesRegex(StageContractError, "recomputed motion"):
                load_model_candidate_preflight_report(run_dir)

        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory), "run.coherent-lineage-forgery")
            report_path, _ = generate_model_candidate_preflight(run_dir)
            motion_path = run_dir / "motion-draft" / "motion-report.json"
            motion = json.loads(motion_path.read_text(encoding="utf-8"))
            motion["layers"][0]["source_artifact_id"] = "forged-source-artifact"
            motion_path.write_bytes(canonical_json_bytes(motion))

            output = report_path.parent
            candidate = json.loads((output / "candidate-report.json").read_text(encoding="utf-8"))
            lineage = next(
                item
                for item in candidate["provenance"]["motion_lineage"]
                if item["motion_layer_id"] == motion["layers"][0]["id"]
            )
            lineage["source_artifact_id"] = "forged-source-artifact"
            _rewrite_candidate_and_preflight(output, candidate)
            with self.assertRaisesRegex(StageContractError, "recomputed motion"):
                load_model_candidate_preflight_report(run_dir)

    def test_published_provenance_masks_cover_underpaint_and_partition_source_regions(self) -> None:
        from PIL import Image, ImageChops, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.provenance-masks"
            run_dir.mkdir()
            write_sparse_motion_fixture(run_dir)
            image_root = run_dir / "model-output" / "input" / "input"
            result = json.loads((run_dir / "model-result.json").read_text(encoding="utf-8"))
            with Image.open(image_root / "face.png", formats=("PNG",)) as stored_face:
                stored_face.load()
                face = stored_face.convert("RGBA")
            try:
                draw = ImageDraw.Draw(face)
                draw.rectangle((400, 250, 560, 540), fill=(210, 180, 165, 255))
                draw.rectangle((438, 462, 502, 522), fill=(15, 25, 35, 255))
                face.save(image_root / "face.png", format="PNG")
            finally:
                face.close()
            with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0)) as reconstruction:
                for semantic in DRAW_ORDER:
                    with Image.open(image_root / f"{semantic}.png", formats=("PNG",)) as layer:
                        layer.load()
                        reconstruction.alpha_composite(layer.convert("RGBA"))
                reconstruction.save(image_root / "reconstruction.png", format="PNG")
                reconstruction.save(image_root / "src_head.png", format="PNG")
                source = reconstruction.copy()
            try:
                red, green, blue, _ = source.getpixel((410, 300))
                source.putpixel((410, 300), (red, green, blue, 0))
                trusted_source = persist_trusted_model_source(run_dir, source)
                (image_root / "src_img.png").write_bytes(trusted_source)
                result["source_sha256"] = sha256_bytes(trusted_source)
                result["entrypoint_attestation"] = _valid_entrypoint_attestation_summary(
                    result["source_sha256"]
                )
            finally:
                source.close()
            refresh_model_inventory(run_dir, result, publish_result=True)
            _, motion = generate_model_motion_draft(run_dir)
            report_path, _ = generate_model_candidate_preflight(run_dir)
            output = report_path.parent
            candidate = json.loads((output / "candidate-report.json").read_text(encoding="utf-8"))

            semantic_union = Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), 0)
            try:
                for semantic in DRAW_ORDER:
                    with Image.open(image_root / f"{semantic}.png", formats=("PNG",)) as layer:
                        layer.load()
                        alpha = layer.getchannel("A").point(lambda value: 255 if value else 0)
                    try:
                        merged = ImageChops.lighter(semantic_union, alpha)
                        semantic_union.close()
                        semantic_union = merged
                    finally:
                        alpha.close()
                with Image.open(output / "mask.source-visible.png", formats=("PNG",)) as stored:
                    source_visible = stored.copy()
                with Image.open(output / "mask.source-transparent-exposed.png", formats=("PNG",)) as stored:
                    exposed = stored.copy()
                with Image.open(output / "mask.semantic-union.png", formats=("PNG",)) as stored:
                    published_union = stored.copy()
                try:
                    with ImageChops.multiply(source_visible, exposed) as intersection:
                        self.assertIsNone(intersection.getbbox())
                    with ImageChops.lighter(source_visible, exposed) as combined, ImageChops.difference(combined, semantic_union) as difference:
                        self.assertIsNone(difference.getbbox())
                    with ImageChops.difference(published_union, semantic_union) as difference:
                        self.assertIsNone(difference.getbbox())
                    self.assertGreater(exposed.histogram()[255], 0)
                    self.assertEqual(
                        source_visible.histogram()[255],
                        candidate["validation"]["source_visible_pixel_count"],
                    )
                    with Image.open(image_root / "src_img.png", formats=("PNG",)) as source_image:
                        source_alpha = source_image.getchannel("A").point(
                            lambda value: 255 if value > SOURCE_VISIBLE_ALPHA_THRESHOLD else 0
                        )
                    try:
                        with ImageChops.multiply(semantic_union, source_alpha) as expected_visible:
                            self.assertEqual(expected_visible.histogram()[255], source_visible.histogram()[255])
                    finally:
                        source_alpha.close()
                finally:
                    source_visible.close()
                    exposed.close()
                    published_union.close()

                face_layer = next(item for item in motion["layers"] if item["semantic"] == "face")
                box = tuple(face_layer["box_ltrb"])
                with Image.open(image_root / "face.png", formats=("PNG",)) as face_image, Image.open(
                    image_root / "reconstruction.png", formats=("PNG",)
                ) as reconstruction_image:
                    face_image.load()
                    reconstruction_image.load()
                    original_face = face_image.convert("RGBA")
                    reconstruction_rgba = reconstruction_image.convert("RGBA")
                matte = _subject_matte(reconstruction_rgba)
                reconstruction_rgba.close()
                try:
                    baseline_canvas = _apply_subject_matte(original_face, matte)
                finally:
                    matte.close()
                    original_face.close()
                try:
                    baseline_crop = baseline_canvas.crop(box)
                    baseline = _tighten_alpha(baseline_crop, feature=False)
                    baseline_crop.close()
                finally:
                    baseline_canvas.close()
                with Image.open(run_dir / face_layer["artifact"]["uri"], formats=("PNG",)) as stored:
                    stored.load()
                    actual_face = stored.convert("RGBA")
                try:
                    with ImageChops.difference(actual_face.convert("RGB"), baseline.convert("RGB")) as rgb_difference:
                        channels = rgb_difference.split()
                        try:
                            changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2]).point(
                                lambda value: 255 if value else 0
                            )
                        finally:
                            for channel in channels:
                                channel.close()
                    with Image.open(output / "mask.deterministic-underpaint.face.png", formats=("PNG",)) as stored:
                        underpaint = stored.copy()
                    try:
                        mask_crop = underpaint.crop(box)
                        try:
                            self.assertGreater(changed.histogram()[255], 0)
                            with ImageChops.subtract(changed, mask_crop) as uncovered:
                                self.assertIsNone(uncovered.getbbox())
                        finally:
                            mask_crop.close()
                    finally:
                        changed.close()
                        underpaint.close()
                finally:
                    actual_face.close()
                    baseline.close()
            finally:
                semantic_union.close()

            mask_path = output / "mask.deterministic-underpaint.face.png"
            with Image.open(mask_path, formats=("PNG",)) as stored:
                stored.load()
                mutated = stored.copy()
            try:
                mutated.putpixel((0, 0), 255 if mutated.getpixel((0, 0)) == 0 else 0)
                mutated.save(mask_path, format="PNG")
            finally:
                mutated.close()
            with self.assertRaisesRegex(StageContractError, "provenance mask"):
                load_model_candidate_preflight_report(run_dir)

    def test_atomic_failure_does_not_publish_or_leave_temporary_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory), "run.atomic-failure")
            original_replace = __import__("os").replace

            def fail_publication(source: object, destination: object) -> None:
                if Path(destination).name == OUTPUT_DIRECTORY:
                    raise OSError("purpose-created publication failure")
                original_replace(source, destination)

            with patch("spikes.gate_f_runner.model_candidate.os.replace", side_effect=fail_publication):
                with self.assertRaisesRegex(OSError, "publication failure"):
                    generate_model_candidate_preflight(run_dir)
            self.assertFalse((run_dir / OUTPUT_DIRECTORY).exists())
            self.assertFalse(any(path.name.startswith(".model-candidate-preflight-") for path in run_dir.iterdir()))

    def test_fails_closed_on_frozen_config_or_registry_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self._fixture(Path(directory), "run.model-config-tamper")
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config["postprocess"]["visible_alpha_threshold"] = 32
            with patch("spikes.gate_f_runner.model_candidate.read_bounded_file") as bounded:
                from spikes.gate_f_runner.runtime import read_bounded_file

                bounded.side_effect = lambda path, maximum=1024 * 1024: canonical_json_bytes(config) if path == CONFIG_PATH else read_bounded_file(path, maximum)
                with self.assertRaisesRegex(StageContractError, "frozen profile"):
                    generate_model_candidate_preflight(run_dir)

            with patch("spikes.gate_f_runner.model_candidate.sha256_file") as digest:
                from spikes.gate_f_runner.runtime import sha256_file

                digest.side_effect = lambda path: "0" * 64 if path == ONTOLOGY_PATH else sha256_file(path)
                with self.assertRaisesRegex(StageContractError, "fixed identities"):
                    generate_model_candidate_preflight(run_dir)

    def test_cli_generation_and_verification_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._fixture(root, "run.model-cli")
            output = io.StringIO()
            with patch("sys.argv", ["gate-f-runner", "model-candidate", "--run-id", run_dir.name, "--workspace-root", str(root)]), contextlib.redirect_stdout(output):
                self.assertEqual(0, main())
            self.assertIn("LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED", output.getvalue())
            self.assertIn("GATE_F_NOT_EVALUATED", output.getvalue())

            output = io.StringIO()
            with patch("sys.argv", ["gate-f-runner", "verify-model-candidate", "--run-id", run_dir.name, "--workspace-root", str(root)]), contextlib.redirect_stdout(output):
                self.assertEqual(0, main())
            self.assertIn("LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED", output.getvalue())

            error = io.StringIO()
            with patch("sys.argv", ["gate-f-runner", "verify-model-candidate", "--run-id", "BAD", "--workspace-root", str(root)]), contextlib.redirect_stderr(error):
                self.assertEqual(64, main())
            self.assertIn("rejected", error.getvalue())


if __name__ == "__main__":
    unittest.main()
