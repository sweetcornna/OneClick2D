"""Intake, suitability, decomposition, bounded completion and rig stages."""

from __future__ import annotations

import unittest

from oneclick2d.errors import ContractError, IntakeRejected, ResourceLimitError
from oneclick2d.raster.image import Image
from oneclick2d.registries import load_registries
from oneclick2d.stages.decompose import LayoutPriorProposer, decompose
from oneclick2d.stages.intake import (
    MAX_UPLOAD_BYTES,
    normalize_upload,
    sniff_media_type,
)
from oneclick2d.stages.rig import MAX_COLLAPSE_FRACTION, build_rig
from oneclick2d.stages.suitability import Decision, evaluate_suitability
from oneclick2d.stages.synthesize import compose_neutral, synthesize

from tests.oneclick2d_support import flat_image, synthetic_subject

SEED = "00000000000000000042"
CONFIG_DIGEST = "0" * 64


class IntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Intake enforces the FR-001 floor of 1,024 px, so these fixtures must
        # meet it even though later stage tests use smaller canvases.
        self.png = synthetic_subject(1024).to_png()

    def test_normalizes_a_supported_png(self) -> None:
        result = normalize_upload(self.png, "image/png")
        self.assertEqual(result.image.size, (1024, 1024))
        self.assertEqual(result.sniffed_media_type, "image/png")
        self.assertTrue(result.had_alpha)
        self.assertEqual(len(result.upload_sha256), 64)

    def test_reencoding_strips_container_metadata(self) -> None:
        import struct
        import zlib

        # Splice a tEXt chunk in and prove it does not survive normalization.
        body = b"Comment\x00should not survive"
        chunk = struct.pack(">I", len(body)) + b"tEXt" + body
        chunk += struct.pack(">I", zlib.crc32(b"tEXt" + body) & 0xFFFFFFFF)
        tagged = self.png[: 8 + 25] + chunk + self.png[8 + 25 :]
        result = normalize_upload(tagged, "image/png")
        self.assertNotIn(b"should not survive", result.normalized_png)

    def test_declared_type_must_match_the_container(self) -> None:
        with self.assertRaises(IntakeRejected):
            normalize_upload(self.png, "image/jpeg")

    def test_unsupported_declared_type_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            normalize_upload(self.png, "image/gif")

    def test_empty_upload_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            normalize_upload(b"", "image/png")

    def test_oversized_upload_is_rejected_before_decoding(self) -> None:
        with self.assertRaises(ResourceLimitError):
            normalize_upload(b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_UPLOAD_BYTES, "image/png")

    def test_dimensions_below_the_floor_are_rejected(self) -> None:
        small = flat_image(64, (10, 20, 30, 255)).to_png()
        with self.assertRaises(IntakeRejected):
            normalize_upload(small, "image/png")

    def test_sniffing_ignores_any_declared_name(self) -> None:
        self.assertEqual(sniff_media_type(self.png), "image/png")
        with self.assertRaises(IntakeRejected):
            sniff_media_type(b"GIF89a")


class SuitabilityTests(unittest.TestCase):
    def test_cut_out_subject_passes(self) -> None:
        report = evaluate_suitability(synthetic_subject(128))
        self.assertNotEqual(report.decision, Decision.BLOCK)
        self.assertTrue(report.background_is_transparent)
        self.assertGreater(report.subject_coverage, 0.1)

    def test_opaque_upload_is_flagged_as_background_not_separated(self) -> None:
        report = evaluate_suitability(flat_image(128, (18, 24, 32, 255)))
        self.assertIn(
            "INPUT_BACKGROUND_NOT_SEPARATED", [item.code for item in report.observations]
        )
        self.assertFalse(report.background_is_transparent)
        self.assertEqual(report.decision, Decision.PASS_WITH_WARNINGS)

    def test_subject_too_small_is_blocked(self) -> None:
        image = Image(128, 128)
        for y in range(4):
            for x in range(4):
                image.set_pixel(x, y, (10, 20, 30, 255))
        report = evaluate_suitability(image)
        self.assertEqual(report.decision, Decision.BLOCK)
        self.assertIn("INPUT_POSE_OUTSIDE_ENVELOPE", report.blocking_codes)

    def test_every_observation_declares_a_consequence_and_one_next_step(self) -> None:
        report = evaluate_suitability(synthetic_subject(128))
        for observation in report.observations:
            self.assertTrue(observation.consequence)
            self.assertTrue(observation.next_step)
            self.assertIn(observation.confidence, ("unavailable",))


class DecompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registries = load_registries()
        self.image = synthetic_subject(160)
        self.suitability = evaluate_suitability(self.image)
        self.decomposition = decompose(
            self.image, self.suitability.subject_mask, self.registries
        )

    def test_every_registry_slot_is_accounted_for(self) -> None:
        recorded = {entry["slot_id"] for entry in self.decomposition.completion}
        self.assertEqual(recorded, set(self.registries.ontology_slot_ids))

    def test_no_slot_is_silently_omitted(self) -> None:
        for entry in self.decomposition.completion:
            self.assertIn(entry["status"], ("PRESENT", "NOT_APPLICABLE", "LOW_CONFIDENCE"))

    def test_draw_order_is_unique_and_sorted(self) -> None:
        orders = [layer.draw_order for layer in self.decomposition.layers]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len(orders), len(set(orders)))

    def test_sides_follow_character_anatomy(self) -> None:
        left = self.decomposition.layer("oc2d.eye.left")
        right = self.decomposition.layer("oc2d.eye.right")
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        # The character's left appears on the viewer's right, so it sits at a
        # larger x than the character's right eye.
        self.assertGreater(left.bounds.x, right.bounds.x)  # type: ignore[union-attr]
        self.assertEqual(left.side, "left")  # type: ignore[union-attr]
        self.assertEqual(right.side, "right")  # type: ignore[union-attr]

    def test_deterministic_prior_reports_low_confidence(self) -> None:
        """Layout priors locate regions; they do not recognise anatomy. Claiming
        PRESENT would overstate the available evidence.
        """
        for layer in self.decomposition.layers:
            self.assertEqual(layer.status, "LOW_CONFIDENCE")

    def test_proposer_contradicting_the_registry_side_is_rejected(self) -> None:
        class WrongSideProposer(LayoutPriorProposer):
            def propose(self, image, subject):  # type: ignore[no-untyped-def]
                proposals = super().propose(image, subject)
                return tuple(
                    type(item)(item.slot_id, "center", item.mask, item.instance_index, item.score, item.evidence)
                    if item.slot_id == "oc2d.eye.left"
                    else item
                    for item in proposals
                )

        with self.assertRaises(ContractError):
            decompose(self.image, self.suitability.subject_mask, self.registries, WrongSideProposer())

    def test_empty_subject_is_rejected(self) -> None:
        from oneclick2d.raster.image import Mask

        with self.assertRaises(ContractError):
            decompose(self.image, Mask(160, 160), self.registries)


class SynthesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registries = load_registries()
        self.image = synthetic_subject(160)
        self.suitability = evaluate_suitability(self.image)
        self.decomposition = decompose(self.image, self.suitability.subject_mask, self.registries)
        self.synthesis = synthesize(
            self.image,
            self.suitability.subject_mask,
            self.decomposition,
            seed=SEED,
            config_digest=CONFIG_DIGEST,
            source_id="sha256:" + "a" * 64,
        )

    def test_originally_visible_samples_are_byte_identical(self) -> None:
        """The source-pixel guarantee, checked per layer at the byte level."""
        for item in self.synthesis.layers:
            for index, coverage in enumerate(item.visible_mask.data):
                if coverage == 0:
                    continue
                offset = index * 4
                self.assertEqual(
                    bytes(item.texture.data[offset : offset + 4]),
                    bytes(self.image.data[offset : offset + 4]),
                )

    def test_generated_regions_carry_full_provenance(self) -> None:
        self.assertGreater(len(self.synthesis.regions), 0)
        for region in self.synthesis.regions:
            self.assertEqual(region.seed, SEED)
            self.assertEqual(region.config_digest, CONFIG_DIGEST)
            self.assertEqual(region.producer_stage, "PLAN_AND_BOUNDED_COMPLETE")
            self.assertTrue(region.producer_id)
            self.assertGreater(region.feather_width_px, 0)

    def test_generated_coverage_only_lands_where_a_front_layer_occludes_it(self) -> None:
        """Completion fills hidden area. A generated sample that no front layer
        covers would be painted over visible original artwork.
        """
        order = {item.layer.layer_id: item.layer.draw_order for item in self.synthesis.layers}
        for region in self.synthesis.regions:
            owner_order = order[region.owner_layer_id]
            front = None
            for item in self.synthesis.layers:
                if item.layer.draw_order <= owner_order:
                    continue
                mask = item.visible_mask
                front = mask.copy() if front is None else front.union(mask)
            self.assertIsNotNone(front)
            for index, coverage in enumerate(region.mask.data):
                if coverage > 0:
                    self.assertGreater(front.data[index], 0)  # type: ignore[union-attr]

    def test_neutral_composite_reproduces_the_source_exactly(self) -> None:
        neutral = compose_neutral(160, 160, self.synthesis)
        visible = self.image.alpha_mask().binarize(31)
        for index, protected in enumerate(visible.data):
            if protected == 0:
                continue
            offset = index * 4
            self.assertEqual(
                bytes(neutral.data[offset : offset + 3]),
                bytes(self.image.data[offset : offset + 3]),
            )

    def test_negative_feather_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            synthesize(
                self.image,
                self.suitability.subject_mask,
                self.decomposition,
                seed=SEED,
                config_digest=CONFIG_DIGEST,
                source_id="sha256:" + "a" * 64,
                feather_px=-1,
            )


class RigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registries = load_registries()
        image = synthetic_subject(160)
        suitability = evaluate_suitability(image)
        decomposition = decompose(image, suitability.subject_mask, self.registries)
        self.synthesis = synthesize(
            image,
            suitability.subject_mask,
            decomposition,
            seed=SEED,
            config_digest=CONFIG_DIGEST,
            source_id="sha256:" + "a" * 64,
        )
        self.rig = build_rig(self.synthesis, self.registries, 160, 160)

    def test_every_mandatory_capability_is_bound(self) -> None:
        bound = {binding.parameter_id for binding in self.rig.bindings}
        for parameter_id in self.registries.mandatory_parameter_ids():
            self.assertIn(parameter_id, bound)

    def test_parameter_ranges_are_ordered_and_manual(self) -> None:
        for spec in self.rig.parameters:
            self.assertLessEqual(spec.minimum, spec.safe_minimum)
            self.assertLessEqual(spec.safe_minimum, spec.default)
            self.assertLessEqual(spec.default, spec.safe_maximum)
            self.assertLessEqual(spec.safe_maximum, spec.maximum)
            self.assertTrue(spec.manual_enabled)

    def test_registry_neutral_is_the_project_default(self) -> None:
        for spec in self.rig.parameters:
            entry = self.registries.parameter(spec.parameter_id)
            self.assertEqual(spec.default, float(entry["neutral"]))

    def test_bindings_have_at_least_two_increasing_samples(self) -> None:
        for binding in self.rig.bindings:
            self.assertGreaterEqual(len(binding.samples), 2)
            values = [sample.parameter_value for sample in binding.samples]
            self.assertEqual(values, sorted(set(values)))

    def test_meshes_cover_generated_reveal_area(self) -> None:
        for item in self.synthesis.layers:
            coverage = item.texture.alpha_mask().bounds_at_least(0)
            mesh = self.rig.mesh_for(item.layer.layer_id).mesh
            xs = [vertex.x for vertex in mesh.vertices]
            ys = [vertex.y for vertex in mesh.vertices]
            self.assertLessEqual(min(xs), coverage.x + 1)
            self.assertLessEqual(min(ys), coverage.y + 1)
            self.assertGreaterEqual(max(xs), coverage.right - 1)
            self.assertGreaterEqual(max(ys), coverage.bottom - 1)

    def test_closing_deltas_never_fully_collapse_a_mesh(self) -> None:
        """A full collapse zeroes triangle area and flips winding, which
        whole-project validation treats as a blocking mesh defect.
        """
        self.assertLess(MAX_COLLAPSE_FRACTION, 1.0)
        for binding in self.rig.bindings:
            if binding.parameter_id not in ("eye.left.open", "eye.right.open", "mouth.open"):
                continue
            mesh = next(
                item.mesh for item in self.rig.meshes if item.mesh_id == binding.target_mesh_id
            )
            for sample in binding.samples:
                from oneclick2d.geometry import apply_deltas, check_mesh

                check_mesh(apply_deltas(mesh, sample.deltas))


if __name__ == "__main__":
    unittest.main()
