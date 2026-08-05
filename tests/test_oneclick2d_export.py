"""Dual-output export: package, PSD, independent re-verification and release."""

from __future__ import annotations

import copy
import io
import unittest
import zipfile

from oneclick2d.errors import ExportVerificationFailed, ResourceLimitError
from oneclick2d.export.oc2d import (
    INDEX_NAME,
    MANIFEST_NAME,
    build_package,
    open_package,
)
from oneclick2d.export.psd import (
    GENERATED_PREFIX,
    READ_ME_NAME,
    SOURCE_REFERENCE_NAME,
    parse_layered_psd,
    write_layered_psd,
)
from oneclick2d.export.release import project_to_psd_layers, publish_dual_output
from oneclick2d.raster.image import Image
from oneclick2d.registries import load_registries
from oneclick2d.stages.decompose import decompose
from oneclick2d.stages.rig import build_rig
from oneclick2d.stages.suitability import evaluate_suitability
from oneclick2d.stages.synthesize import compose_neutral, synthesize
from oneclick2d.strict_json import canonical_bytes, sha256_hex
from oneclick2d.validation import validate_project
from oneclick2d.cir import build_project

from tests.oneclick2d_support import synthetic_subject

SEED = "00000000000000000042"
CANVAS = 96


def _fixture():
    registries = load_registries()
    image = synthetic_subject(CANVAS)
    png = image.to_png()
    suitability = evaluate_suitability(image)
    decomposition = decompose(image, suitability.subject_mask, registries)
    synthesis = synthesize(
        image,
        suitability.subject_mask,
        decomposition,
        seed=SEED,
        config_digest="0" * 64,
        source_id="sha256:" + sha256_hex(png),
    )
    rig = build_rig(synthesis, registries, CANVAS, CANVAS)
    built = build_project(
        project_id="project.export",
        revision_id="revision.0001",
        created_at="2026-08-05T00:00:00Z",
        source_png=png,
        normalized_png=png,
        synthesis=synthesis,
        decomposition=decomposition,
        rig=rig,
        registries=registries,
        canvas_width=CANVAS,
        canvas_height=CANVAS,
        root_seed=SEED,
    )
    report = validate_project(built.document, built.artifacts.payloads, registries)
    payload_digest = built.payload_sha256
    validation_report = {
        "format": "oneclick2d.validation-report",
        "format_version": "0.2.0",
        "report_id": "report.0001",
        "project_revision_id": "revision.0001",
        "project_payload_sha256": payload_digest,
        "policy_version": "0.1.0",
        "status": report.status,
        "findings": [finding.as_report() for finding in report.findings],
        "acknowledgments": [],
        "export_readiness": {"oc2d": True, "layered_psd": True},
    }
    run_manifest = {
        "format": "oneclick2d.run-manifest",
        "format_version": "0.2.0",
        "run_id": "run.0001",
        "project_revision_id": "revision.0001",
        "project_payload_sha256": payload_digest,
        "terminal_status": "succeeded",
        "stages": [],
    }
    snapshots = {
        "ontology": registries.ontology.canonical,
        "parameters": registries.parameters.canonical,
        "reason-codes": registries.reason_codes.canonical,
    }
    neutral = compose_neutral(CANVAS, CANVAS, synthesis)
    return {
        "registries": registries,
        "document": built.document,
        "payloads": built.artifacts.payloads,
        "entries": built.artifacts.entries,
        "validation": validation_report,
        "run_manifest": run_manifest,
        "snapshots": snapshots,
        "neutral": neutral,
        "payload_digest": payload_digest,
    }


class _ExportCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _fixture()
        cls.release = publish_dual_output(
            release_id="release.0001",
            account_id="account.0001",
            project_id="project.export",
            document=cls.fixture["document"],
            payloads=cls.fixture["payloads"],
            validation_report=cls.fixture["validation"],
            run_manifest=cls.fixture["run_manifest"],
            registry_snapshots=cls.fixture["snapshots"],
            neutral=cls.fixture["neutral"],
            created_at="2026-08-05T00:00:00Z",
        )

    def _publish(self, **overrides):
        arguments = {
            "release_id": "release.0009",
            "account_id": "account.0001",
            "project_id": "project.export",
            "document": self.fixture["document"],
            "payloads": self.fixture["payloads"],
            "validation_report": self.fixture["validation"],
            "run_manifest": self.fixture["run_manifest"],
            "registry_snapshots": self.fixture["snapshots"],
            "neutral": self.fixture["neutral"],
            "created_at": "2026-08-05T00:00:00Z",
        }
        arguments.update(overrides)
        return publish_dual_output(**arguments)

    def _rebuild_archive(self, mutate) -> bytes:
        source = zipfile.ZipFile(io.BytesIO(self.release.package_bytes))
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
        mutate(members)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(members):
                archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), members[name])
        return buffer.getvalue()


class ReleaseRecordTests(_ExportCase):
    def test_release_record_is_schema_valid_and_verified(self) -> None:
        self.assertEqual(self.release.record["status"], "verified")
        self.assertEqual(self.release.record["project_payload_sha256"], self.fixture["payload_digest"])

    def test_both_artifacts_bind_the_same_payload_digest(self) -> None:
        self.assertTrue(self.release.record["oc2d"]["reopen_passed"])
        self.assertTrue(self.release.record["layered_psd"]["reopen_passed"])
        self.assertEqual(
            self.release.record["oc2d"]["sha256"], sha256_hex(self.release.package_bytes)
        )
        self.assertEqual(self.release.record["layered_psd"]["sha256"], sha256_hex(self.release.psd_bytes))

    def test_archive_digest_lives_outside_the_archive(self) -> None:
        """A digest of the archive stored inside it could never be satisfied."""
        opened = open_package(self.release.package_bytes)
        self.assertNotIn(self.release.record["oc2d"]["sha256"], opened.manifest_bytes.decode())

    def test_blocking_validation_cannot_publish(self) -> None:
        blocked = dict(self.fixture["validation"], status="blocked")
        with self.assertRaises(ExportVerificationFailed):
            self._publish(validation_report=blocked)

    def test_report_bound_to_a_different_payload_cannot_publish(self) -> None:
        mismatched = dict(self.fixture["validation"], project_payload_sha256="0" * 64)
        with self.assertRaises(ExportVerificationFailed):
            self._publish(validation_report=mismatched)

    def test_run_manifest_bound_to_a_different_payload_cannot_publish(self) -> None:
        mismatched = dict(self.fixture["run_manifest"], project_payload_sha256="0" * 64)
        with self.assertRaises(ExportVerificationFailed):
            self._publish(run_manifest=mismatched)

    def test_psd_disagreeing_with_the_cir_blocks_the_release(self) -> None:
        """PSD failure must stop the release, never degrade to oc2d-only."""
        wrong = self.fixture["neutral"].copy()
        wrong.set_pixel(CANVAS // 2, CANVAS // 2, (1, 2, 3, 255))
        with self.assertRaises(ExportVerificationFailed):
            self._publish(neutral=wrong)


class PackageTests(_ExportCase):
    def test_archive_is_byte_reproducible_from_one_revision(self) -> None:
        again, _ = build_package(
            manifest_bytes=canonical_bytes(self.fixture["document"]),
            artifacts=self.fixture["payloads"],
            validation_bytes=canonical_bytes(self.fixture["validation"]),
            run_manifest_bytes=canonical_bytes(self.fixture["run_manifest"]),
            registry_snapshots=self.fixture["snapshots"],
        )
        self.assertEqual(again, self.release.package_bytes)

    def test_reopened_package_matches_the_published_payload(self) -> None:
        opened = open_package(self.release.package_bytes)
        self.assertEqual(opened.manifest_bytes, canonical_bytes(self.fixture["document"]))
        self.assertEqual(set(opened.artifacts), set(self.fixture["payloads"]))
        for artifact_id, data in opened.artifacts.items():
            self.assertEqual(data, self.fixture["payloads"][artifact_id])

    def test_reopened_package_revalidates_independently(self) -> None:
        opened = open_package(self.release.package_bytes)
        report = validate_project(opened.manifest, opened.artifacts, self.fixture["registries"])
        self.assertTrue(report.export_ready)

    def test_manifest_must_be_canonical_json(self) -> None:
        def mutate(members):
            members[MANIFEST_NAME] = b" " + members[MANIFEST_NAME]

        with self.assertRaises(ExportVerificationFailed):
            open_package(self._rebuild_archive(mutate))

    def test_missing_index_is_rejected(self) -> None:
        with self.assertRaises(ExportVerificationFailed):
            open_package(self._rebuild_archive(lambda members: members.pop(INDEX_NAME)))

    def test_member_absent_from_the_index_is_rejected(self) -> None:
        def mutate(members):
            members["registries/rogue.json"] = b"{}"

        with self.assertRaises(ExportVerificationFailed):
            open_package(self._rebuild_archive(mutate))

    def test_artifact_digest_mismatch_is_rejected(self) -> None:
        def mutate(members):
            key = next(name for name in members if name.startswith("artifacts/"))
            members[key] = members[key] + b"x"

        with self.assertRaises(ExportVerificationFailed):
            open_package(self._rebuild_archive(mutate))

    def test_report_bound_to_a_stale_payload_is_rejected(self) -> None:
        def mutate(members):
            document = copy.deepcopy(self.fixture["document"])
            document["project_id"] = "project.tampered"
            members[MANIFEST_NAME] = canonical_bytes(document)

        with self.assertRaises(ExportVerificationFailed):
            open_package(self._rebuild_archive(mutate))

    def test_unsafe_member_paths_are_rejected(self) -> None:
        for name in ("../escape.json", "/absolute.json", "a\\b.json", "./dot.json"):
            with self.subTest(name=name):

                def mutate(members, name=name):
                    members[name] = b"{}"

                with self.assertRaises((ExportVerificationFailed, ResourceLimitError)):
                    open_package(self._rebuild_archive(mutate))

    def test_corrupt_archive_container_is_rejected(self) -> None:
        with self.assertRaises((ExportVerificationFailed, ResourceLimitError, Exception)):
            open_package(b"not a zip at all")

    def test_unsupported_index_version_fails_closed(self) -> None:
        def mutate(members):
            from oneclick2d.strict_json import loads_strict

            index = loads_strict(members[INDEX_NAME])
            index["format_version"] = "9.9.9"
            members[INDEX_NAME] = canonical_bytes(index)

        with self.assertRaises(ExportVerificationFailed):
            open_package(self._rebuild_archive(mutate))


class PsdTests(_ExportCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.parsed = parse_layered_psd(cls.release.psd_bytes)
        cls.names = [layer.name for layer in cls.parsed.layers]

    def test_canvas_and_colour_profile_match_the_project(self) -> None:
        self.assertEqual((self.parsed.width, self.parsed.height), (CANVAS, CANVAS))
        self.assertTrue(self.parsed.has_srgb_profile)

    def test_source_reference_is_bottom_hidden_and_locked(self) -> None:
        self.assertEqual(self.names[0], SOURCE_REFERENCE_NAME)
        self.assertFalse(self.parsed.layers[0].visible)
        self.assertTrue(self.parsed.layers[0].locked)

    def test_read_me_is_at_the_top_of_the_panel_and_hidden(self) -> None:
        self.assertEqual(self.names[-1], READ_ME_NAME)
        self.assertFalse(self.parsed.layers[-1].visible)

    def test_each_generated_fill_sits_directly_below_its_visible_layer(self) -> None:
        fills = [name for name in self.names if name.startswith(GENERATED_PREFIX)]
        self.assertGreater(len(fills), 0)
        for index, name in enumerate(self.names):
            if not name.startswith(GENERATED_PREFIX):
                continue
            owner = name[len(GENERATED_PREFIX) :]
            self.assertEqual(self.names[index + 1], owner)

    def test_reverse_panel_composite_reproduces_the_cir_neutral(self) -> None:
        self.assertEqual(self.parsed.merged.data, self.fixture["neutral"].data)

    def test_all_layers_use_normal_blend(self) -> None:
        for layer in self.parsed.layers:
            self.assertEqual(layer.blend_mode, "norm")

    def test_layer_names_and_ids_are_unique(self) -> None:
        self.assertEqual(len(self.names), len(set(self.names)))
        identifiers = [layer.layer_id for layer in self.parsed.layers]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_unicode_layer_names_survive_the_round_trip(self) -> None:
        self.assertIn(READ_ME_NAME, self.names)
        self.assertIn("—", READ_ME_NAME)

    def test_psb_version_is_refused(self) -> None:
        mutated = bytearray(self.release.psd_bytes)
        mutated[4:6] = b"\x00\x02"
        with self.assertRaises(ExportVerificationFailed):
            parse_layered_psd(bytes(mutated))

    def test_truncated_psd_is_refused(self) -> None:
        with self.assertRaises(ExportVerificationFailed):
            parse_layered_psd(self.release.psd_bytes[:200])

    def test_writer_requires_at_least_two_layers(self) -> None:
        from oneclick2d.errors import ContractError
        from oneclick2d.export.psd import PsdLayer

        canvas = Image(8, 8)
        with self.assertRaises(ContractError):
            write_layered_psd(canvas, (PsdLayer(1, "only", canvas),), canvas)

    def test_writer_rejects_duplicate_layer_names(self) -> None:
        from oneclick2d.errors import ContractError
        from oneclick2d.export.psd import PsdLayer

        canvas = Image(8, 8)
        layers = (PsdLayer(1, "same", canvas), PsdLayer(2, "same", canvas))
        with self.assertRaises(ContractError):
            write_layered_psd(canvas, layers, canvas)

    def test_projection_layer_count_matches_the_written_psd(self) -> None:
        projected = project_to_psd_layers(self.fixture["document"], self.fixture["payloads"])
        self.assertEqual(len(projected), len(self.parsed.layers))

    def test_a_third_party_reader_opens_the_psd(self) -> None:
        """Interop evidence from a reader that shares no code with the writer.

        Skipped when the optional oracle is absent so the fixed standard-library
        test command stays runnable. This is not a Photoshop or Krita claim: the
        editor matrix in ``docs/PSD_EXPORT_PROFILE.md`` §7 is still a Gate
        decision with its own licensing evidence.
        """
        try:
            from PIL import Image as PillowImage
        except ImportError:
            self.skipTest("no third-party PSD reader available")
        with io.BytesIO(self.release.psd_bytes) as handle:
            opened = PillowImage.open(handle)
            self.assertEqual(opened.format, "PSD")
            self.assertEqual(opened.size, (CANVAS, CANVAS))
            self.assertEqual(getattr(opened, "n_frames", 0), len(self.parsed.layers))


if __name__ == "__main__":
    unittest.main()
