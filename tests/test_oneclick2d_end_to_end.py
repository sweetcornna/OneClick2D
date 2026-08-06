"""End-to-end product path: upload in, independently verified dual output out.

Covers the FR-005 pipeline and the FR-017 rule that both artifacts must be
re-opened and verified from one payload digest before anything is downloadable.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oneclick2d.errors import SuitabilityBlocked
from oneclick2d.export.oc2d import open_package
from oneclick2d.export.psd import READ_ME_NAME, SOURCE_REFERENCE_NAME, parse_layered_psd
from oneclick2d.generate import generate
from oneclick2d.raster.image import Image
from oneclick2d.registries import load_registries
from oneclick2d.stages.intake import DimensionEnvelope
from oneclick2d.strict_json import canonical_bytes, sha256_hex
from oneclick2d.validation import validate_project

from tests.oneclick2d_support import synthetic_subject

CANVAS = 96
# The product envelope starts at 1,024 px per side. The rasterizer is pure
# Python, so exercising the real path end to end at that size would dominate the
# fixed test command. These tests narrow the envelope instead of weakening any
# check, and ``IntakeEnvelopeTests`` still proves the shipped default.
TEST_ENVELOPE = DimensionEnvelope(min_side=64, max_side=256, max_pixels=256 * 256)


def _run(**overrides):
    arguments = {
        "upload": synthetic_subject(CANVAS).to_png(),
        "declared_media_type": "image/png",
        "account_id": "account.0001",
        "project_id": "project.e2e",
        "revision_id": "revision.0001",
        "run_id": "run.0001",
        "release_id": "release.0001",
        "created_at": "2026-08-05T00:00:00Z",
        "root_seed": 42,
        "envelope": TEST_ENVELOPE,
    }
    arguments.update(overrides)
    return generate(**arguments)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = _run()

    def test_run_produces_both_artifacts(self) -> None:
        self.assertGreater(len(self.result.release.package_bytes), 0)
        self.assertGreater(len(self.result.release.psd_bytes), 0)
        self.assertEqual(self.result.release.record["status"], "verified")

    def test_both_artifacts_bind_one_payload_digest(self) -> None:
        digest = sha256_hex(canonical_bytes(self.result.project_document))
        self.assertEqual(self.result.release.record["project_payload_sha256"], digest)
        self.assertEqual(self.result.validation_report["project_payload_sha256"], digest)
        self.assertEqual(self.result.run_manifest["project_payload_sha256"], digest)

    def test_package_reopens_and_revalidates_independently(self) -> None:
        opened = open_package(self.result.release.package_bytes)
        report = validate_project(opened.manifest, opened.artifacts, load_registries())
        self.assertTrue(report.export_ready)

    def test_psd_reopens_with_the_documented_panel_order(self) -> None:
        parsed = parse_layered_psd(self.result.release.psd_bytes)
        names = [layer.name for layer in parsed.layers]
        self.assertEqual(names[0], SOURCE_REFERENCE_NAME)
        self.assertEqual(names[-1], READ_ME_NAME)
        self.assertTrue(parsed.has_srgb_profile)

    def test_psd_composite_equals_the_cir_neutral(self) -> None:
        parsed = parse_layered_psd(self.result.release.psd_bytes)
        self.assertEqual(parsed.merged.to_png(), self.result.neutral_png)

    def test_rendering_needs_no_model_or_inference(self) -> None:
        """FR-014: the package renders from its own contents. Nothing in the
        published provenance may be model-backed while no rights record exists.
        """
        opened = open_package(self.result.release.package_bytes)
        for entry in opened.manifest["provenance"]:
            self.assertEqual(entry["producer_kind"], "deterministic")

    def test_run_manifest_records_every_dag_stage_with_a_derived_seed(self) -> None:
        stages = self.result.run_manifest["stages"]
        self.assertEqual(len(stages), 11)
        seeds = {stage["seed_u64"] for stage in stages}
        self.assertEqual(len(seeds), len(stages))
        for stage in stages:
            self.assertEqual(len(stage["seed_u64"]), 20)

    def test_the_whole_run_is_reproducible(self) -> None:
        again = _run()
        self.assertEqual(
            again.release.record["project_payload_sha256"],
            self.result.release.record["project_payload_sha256"],
        )
        self.assertEqual(again.release.package_bytes, self.result.release.package_bytes)
        self.assertEqual(again.release.psd_bytes, self.result.release.psd_bytes)

    def test_the_seed_is_recorded_in_provenance_and_changes_the_payload(self) -> None:
        """Provenance records the seed each stage actually used, so a different
        root seed yields a different payload digest even though every stage is
        deterministic and the rendered pixels are unchanged.
        """
        other = _run(root_seed=7, run_id="run.0002", release_id="release.0002")
        self.assertNotEqual(
            other.run_manifest["stages"][0]["seed_u64"],
            self.result.run_manifest["stages"][0]["seed_u64"],
        )
        self.assertNotEqual(
            other.release.record["project_payload_sha256"],
            self.result.release.record["project_payload_sha256"],
        )
        # Same inputs, same pixels: only the recorded seed differs.
        self.assertEqual(other.neutral_png, self.result.neutral_png)

    def test_a_new_revision_invalidates_the_old_payload_digest(self) -> None:
        other = _run(revision_id="revision.0002", release_id="release.0003", run_id="run.0003")
        self.assertNotEqual(
            other.release.record["project_payload_sha256"],
            self.result.release.record["project_payload_sha256"],
        )

    def test_suitability_report_travels_with_the_run(self) -> None:
        self.assertIn("decision", self.result.suitability)
        self.assertEqual(self.result.run_manifest["suitability"], self.result.suitability)

    def test_workspace_output_is_written_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _run(workspace=workspace, release_id="release.0004", run_id="run.0004")
            written = {path.suffix for path in workspace.iterdir()}
            self.assertIn(".oc2d", written)
            self.assertIn(".psd", written)


class IntakeEnvelopeTests(unittest.TestCase):
    def test_the_shipped_default_enforces_the_fr_001_floor(self) -> None:
        """The tests above narrow the envelope for speed; this proves the value
        the product actually ships with still rejects an undersized upload.
        """
        from oneclick2d.errors import IntakeRejected
        from oneclick2d.stages.intake import DEFAULT_ENVELOPE, MAX_SIDE, MIN_SIDE

        self.assertEqual((DEFAULT_ENVELOPE.min_side, DEFAULT_ENVELOPE.max_side), (MIN_SIDE, MAX_SIDE))
        with self.assertRaises(IntakeRejected):
            generate(
                upload=synthetic_subject(CANVAS).to_png(),
                declared_media_type="image/png",
                account_id="account.0001",
                project_id="project.e2e",
                revision_id="revision.0001",
                run_id="run.0100",
                release_id="release.0100",
                created_at="2026-08-05T00:00:00Z",
            )


class RejectionTests(unittest.TestCase):
    def test_a_blocking_suitability_decision_stops_the_run(self) -> None:
        tiny_subject = Image(CANVAS, CANVAS)
        for y in range(3):
            for x in range(3):
                tiny_subject.set_pixel(x, y, (10, 20, 30, 255))
        with self.assertRaises(SuitabilityBlocked):
            _run(upload=tiny_subject.to_png())

    def test_an_opaque_upload_still_completes_with_a_warning(self) -> None:
        """The background-not-separated case is a warning, not a block: the run
        must still produce a verified dual output.
        """
        result = _run(
            upload=synthetic_subject(CANVAS, cut_out=False).to_png(),
            release_id="release.0005",
            run_id="run.0005",
        )
        self.assertEqual(result.release.record["status"], "verified")
        codes = {item["code"] for item in result.suitability["observations"]}
        self.assertIn("INPUT_BACKGROUND_NOT_SEPARATED", codes)


if __name__ == "__main__":
    unittest.main()
