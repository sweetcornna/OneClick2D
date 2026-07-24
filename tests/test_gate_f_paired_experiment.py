from __future__ import annotations

import unittest
from fractions import Fraction

from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.paired_experiment import (
    ArmIdentity,
    PairOutcome,
    adjudicate_ballots,
    build_blind_mapping,
    classify_pair,
    clopper_pearson_interval,
    evaluate_experiment,
    exact_binomial_upper_tail,
    reveal_outcome,
    validate_arm_parity,
)


class PairedExperimentTests(unittest.TestCase):
    def test_arm_parity_is_exact(self) -> None:
        identity = ArmIdentity("a" * 64, "b" * 64, "renderer", "profile", 100, 120, ("neutral", "yaw.max"))
        validate_arm_parity(identity, identity)
        changed = ArmIdentity("a" * 64, "c" * 64, "renderer", "profile", 100, 120, ("neutral", "yaw.max"))
        with self.assertRaises(StageContractError):
            validate_arm_parity(identity, changed)

    def test_blinding_adjudication_and_reveal_are_deterministic(self) -> None:
        mapping = build_blind_mapping("asset.fixture-01", "00000000000000000042")
        self.assertEqual(mapping, build_blind_mapping("asset.fixture-01", "00000000000000000042"))
        blind = adjudicate_ballots(("left", "right", "left"))
        self.assertEqual("left", blind)
        self.assertIn(reveal_outcome(mapping, blind), {"candidate_win", "comparator_win"})
        self.assertEqual("tie", adjudicate_ballots(("left", "right", "tie")))
        with self.assertRaises(StageContractError):
            adjudicate_ballots(("left", "right"))

    def test_pair_failure_rules(self) -> None:
        self.assertEqual("invalid", classify_pair("a", candidate_status="succeeded", comparator_status="failed", review_outcome=None, candidate_f_usable=True).outcome)
        failed = classify_pair("a", candidate_status="failed", comparator_status="succeeded", review_outcome=None, candidate_f_usable=True)
        self.assertEqual(("comparator_win", False), (failed.outcome, failed.f_usable))
        missing = classify_pair("a", candidate_status="succeeded", comparator_status="succeeded", review_outcome=None, candidate_f_usable=True)
        self.assertEqual(("tie", True), (missing.outcome, missing.f_usable))

    def test_exact_binomial_boundaries(self) -> None:
        self.assertEqual(Fraction(1, 32), exact_binomial_upper_tail(5, 0))
        self.assertEqual(Fraction(1, 16), exact_binomial_upper_tail(4, 0))
        self.assertEqual(Fraction(378, 8192), exact_binomial_upper_tail(10, 3))
        lower, upper = clopper_pearson_interval(5, 0)
        self.assertGreater(lower, 0)
        self.assertEqual(1.0, upper)

    def test_twenty_item_primary_rule(self) -> None:
        passing = [PairOutcome(f"asset.{index:02d}", "candidate_win" if index < 10 else "comparator_win" if index < 13 else "tie", index < 12, "fixture") for index in range(20)]
        result = evaluate_experiment(passing)
        self.assertEqual((10, 3, 7), (result["wins"], result["losses"], result["ties"]))
        self.assertTrue(result["superiority_pass"])
        self.assertTrue(result["f_usable_pass"])
        self.assertTrue(result["primary_pair_rule_pass"])

        four_zero = [PairOutcome(f"asset.{index:02d}", "candidate_win" if index < 4 else "tie", True, "fixture") for index in range(20)]
        self.assertFalse(evaluate_experiment(four_zero)["superiority_pass"])
        with self.assertRaises(StageContractError):
            evaluate_experiment(four_zero[:-1])
        with self.assertRaises(TypeError):
            evaluate_experiment(four_zero[:-1], expected_assets=19)  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
