"""CIR v0.2 assembly, registries and whole-project semantic validation."""

from __future__ import annotations

import copy
import unittest

from oneclick2d.cir import build_project, project_validator
from oneclick2d.errors import ContractError
from oneclick2d.registries import load_registries
from oneclick2d.stages.decompose import decompose
from oneclick2d.stages.rig import build_rig
from oneclick2d.stages.suitability import evaluate_suitability
from oneclick2d.stages.synthesize import compose_neutral, synthesize
from oneclick2d.strict_json import canonical_bytes, sha256_hex
from oneclick2d.validation import validate_project

from tests.oneclick2d_support import synthetic_subject

SEED = "00000000000000000042"
CANVAS = 128


def build_fixture(revision_id: str = "revision.0001", parent: str | None = None):
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
        project_id="project.test",
        revision_id=revision_id,
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
        parent_revision_id=parent,
    )
    return registries, built, synthesis, image


class RegistrySnapshotTests(unittest.TestCase):
    def test_snapshots_expose_stable_digests(self) -> None:
        first = load_registries()
        second = load_registries()
        self.assertEqual(first.ontology.sha256, second.ontology.sha256)
        self.assertEqual(first.parameters.sha256, second.parameters.sha256)
        self.assertEqual(first.reason_codes.sha256, second.reason_codes.sha256)

    def test_reference_shape_matches_the_cir_registry_ref(self) -> None:
        reference = load_registries().parameters.as_reference()
        self.assertEqual(set(reference), {"id", "version", "sha256"})
        self.assertEqual(len(reference["sha256"]), 64)

    def test_unknown_reason_code_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            load_registries().require_reason_code("NOT_A_REGISTERED_CODE")

    def test_mandatory_parameters_are_the_registry_candidate_mandatory_set(self) -> None:
        registries = load_registries()
        self.assertEqual(
            set(registries.mandatory_parameter_ids()),
            {
                item["id"]
                for item in registries.parameters_list
                if item["capability"] == "candidate_mandatory"
            },
        )


class ProjectAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registries, cls.built, cls.synthesis, cls.image = build_fixture()

    def test_document_is_schema_valid(self) -> None:
        self.assertEqual(project_validator().validate(self.built.document), [])

    def test_payload_digest_is_over_canonical_bytes(self) -> None:
        self.assertEqual(
            self.built.payload_sha256, sha256_hex(canonical_bytes(self.built.document))
        )

    def test_artifacts_are_content_addressed(self) -> None:
        for entry in self.built.document["artifacts"]:
            payload = self.built.artifacts.payloads[str(entry["id"])]
            self.assertEqual(entry["id"], f"sha256:{sha256_hex(payload)}")
            self.assertEqual(entry["byte_length"], len(payload))

    def test_build_is_deterministic(self) -> None:
        _, again, _, _ = build_fixture()
        self.assertEqual(again.payload_sha256, self.built.payload_sha256)

    def test_new_revision_changes_the_payload_digest(self) -> None:
        _, other, _, _ = build_fixture("revision.0002", parent="revision.0001")
        self.assertNotEqual(other.payload_sha256, self.built.payload_sha256)
        self.assertEqual(other.document["parent_revision_id"], "revision.0001")

    def test_registry_references_resolve_to_the_bound_snapshots(self) -> None:
        document = self.built.document
        self.assertEqual(
            document["parameter_registry"]["sha256"], self.registries.parameters.sha256
        )
        self.assertEqual(
            document["reason_code_registry"]["sha256"], self.registries.reason_codes.sha256
        )

    def test_confidence_is_unavailable_without_a_calibration_dataset(self) -> None:
        """Reporting a number here would present an uncalibrated guess as a
        calibrated score, so ``unavailable`` is the only honest value.
        """
        for fact in self.built.document["confidence_facts"]:
            self.assertEqual(fact["score"], "unavailable")
            self.assertEqual(fact["threshold_band"], "unavailable")

    def test_model_backed_provenance_without_rights_evidence_is_refused(self) -> None:
        class ModelProposer:
            proposer_id = "oneclick2d.semantic.pretend-model"
            proposer_version = "0.1.0"
            producer_kind = "model_backed"

            def propose(self, image, subject):  # type: ignore[no-untyped-def]
                from oneclick2d.stages.decompose import LayoutPriorProposer

                return LayoutPriorProposer().propose(image, subject)

        registries = load_registries()
        image = synthetic_subject(CANVAS)
        suitability = evaluate_suitability(image)
        decomposition = decompose(image, suitability.subject_mask, registries, ModelProposer())
        synthesis = synthesize(
            image,
            suitability.subject_mask,
            decomposition,
            seed=SEED,
            config_digest="0" * 64,
            source_id="sha256:" + "a" * 64,
        )
        rig = build_rig(synthesis, registries, CANVAS, CANVAS)
        with self.assertRaises(ContractError):
            build_project(
                project_id="project.test",
                revision_id="revision.0001",
                created_at="2026-08-05T00:00:00Z",
                source_png=image.to_png(),
                normalized_png=image.to_png(),
                synthesis=synthesis,
                decomposition=decomposition,
                rig=rig,
                registries=registries,
                canvas_width=CANVAS,
                canvas_height=CANVAS,
                root_seed=SEED,
            )

    def test_invalid_identifiers_are_rejected(self) -> None:
        with self.assertRaises(ContractError):
            build_project(
                project_id="Project Test",
                revision_id="revision.0001",
                created_at="2026-08-05T00:00:00Z",
                source_png=self.image.to_png(),
                normalized_png=self.image.to_png(),
                synthesis=self.synthesis,
                decomposition=decompose(
                    self.image, evaluate_suitability(self.image).subject_mask, self.registries
                ),
                rig=build_rig(self.synthesis, self.registries, CANVAS, CANVAS),
                registries=self.registries,
                canvas_width=CANVAS,
                canvas_height=CANVAS,
                root_seed=SEED,
            )


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registries, cls.built, cls.synthesis, cls.image = build_fixture()
        cls.report = validate_project(
            cls.built.document, cls.built.artifacts.payloads, cls.registries
        )

    def test_a_well_formed_project_is_export_ready(self) -> None:
        self.assertTrue(self.report.export_ready)
        self.assertEqual(self.report.blocking, ())

    def test_neutral_composite_preserves_every_visible_source_sample(self) -> None:
        self.assertEqual(self.report.checks["neutral_composite_deviating_samples"], 0)

    def test_render_checks_cover_neutral_extremes_combinations_and_trajectory(self) -> None:
        self.assertGreater(self.report.checks["pose_count"], 20)
        self.assertGreater(self.report.checks["neutral_visible_samples"], 0)

    def test_low_confidence_slots_are_reported_for_review_not_hidden(self) -> None:
        codes = {finding.code for finding in self.report.findings}
        self.assertIn("ONTOLOGY_SLOT_LOW_CONFIDENCE", codes)
        self.assertEqual(self.report.status, "pass_with_review")

    def test_every_finding_resolves_to_the_bound_reason_code_registry(self) -> None:
        for finding in self.report.findings:
            self.registries.require_reason_code(finding.code)

    def _mutated(self, mutate) -> str:
        document = copy.deepcopy(self.built.document)
        mutate(document)
        return validate_project(document, self.built.artifacts.payloads, self.registries).status

    def test_tampered_artifact_bytes_are_detected(self) -> None:
        payloads = dict(self.built.artifacts.payloads)
        victim = next(
            key
            for key, entry in self.built.artifacts.entries.items()
            if entry["role"] == "layer_texture"
        )
        payloads[victim] = payloads[victim][:-1] + bytes([payloads[victim][-1] ^ 0xFF])
        with self.assertRaises(ContractError):
            validate_project(self.built.document, payloads, self.registries)

    def test_missing_artifact_bytes_are_detected(self) -> None:
        payloads = dict(self.built.artifacts.payloads)
        payloads.pop(next(iter(payloads)))
        with self.assertRaises(ContractError):
            validate_project(self.built.document, payloads, self.registries)

    def test_dangling_mesh_reference_blocks(self) -> None:
        def mutate(document):
            document["meshes"][0]["layer_id"] = "layer.does-not-exist"

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_duplicate_draw_order_blocks(self) -> None:
        def mutate(document):
            document["layers"][1]["draw_order"] = document["layers"][0]["draw_order"]

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_parameter_range_beyond_the_registry_blocks(self) -> None:
        def mutate(document):
            for parameter in document["parameters"]:
                if parameter["id"] == "head.yaw":
                    parameter["maximum"] = 999.0
                    parameter["safe_maximum"] = 999.0

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_inverted_parameter_range_blocks(self) -> None:
        def mutate(document):
            for parameter in document["parameters"]:
                if parameter["id"] == "head.yaw":
                    parameter["safe_minimum"] = parameter["maximum"]

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_missing_ontology_record_blocks(self) -> None:
        def mutate(document):
            document["ontology_completion"] = document["ontology_completion"][:-1]

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_unknown_ontology_slot_blocks(self) -> None:
        def mutate(document):
            document["ontology_completion"].append(
                {
                    "slot_id": "oc2d.not-a-slot",
                    "status": "PRESENT",
                    "instance_ids": [],
                    "confidence_fact_ids": [],
                    "reason_codes": [],
                    "evidence_artifact_ids": [],
                }
            )

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_wrong_role_reference_blocks(self) -> None:
        mask_id = next(
            key
            for key, entry in self.built.artifacts.entries.items()
            if entry["role"] == "visible_mask"
        )

        def mutate(document):
            document["layers"][0]["texture_artifact_id"] = mask_id

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_unbound_mandatory_parameter_blocks(self) -> None:
        def mutate(document):
            document["bindings"] = [
                binding for binding in document["bindings"] if binding["parameter_id"] != "mouth.open"
            ]

        self.assertEqual(self._mutated(mutate), "blocked")

    def test_declared_mesh_counts_must_match_the_payload(self) -> None:
        def mutate(document):
            document["meshes"][0]["vertex_count"] = int(document["meshes"][0]["vertex_count"]) + 1

        with self.assertRaises(ContractError):
            document = copy.deepcopy(self.built.document)
            mutate(document)
            validate_project(document, self.built.artifacts.payloads, self.registries)


if __name__ == "__main__":
    unittest.main()
