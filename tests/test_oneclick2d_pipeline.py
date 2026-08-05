"""Stage framework: identity, seeds, budgets, cancellation, ownership fencing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oneclick2d.errors import (
    CancellationRequested,
    ContractError,
    IntakeRejected,
    ResourceLimitError,
)
from oneclick2d.pipeline.context import (
    ArtifactSink,
    Attempt,
    CancellationToken,
    ResourceBudget,
    StageContext,
    StageOutcome,
    StageResult,
    StageStatus,
    derive_seed,
    format_seed,
    require_entity_id,
)
from oneclick2d.pipeline.dag import (
    STAGE_ORDER,
    RunLedger,
    StageDefinition,
    run_pipeline,
)


class IdentityTests(unittest.TestCase):
    def test_entity_ids_follow_the_cir_pattern(self) -> None:
        require_entity_id("layer.face-base")
        for invalid in ("Layer", "1abc", "ab", "a" * 200, "has space"):
            with self.subTest(value=invalid), self.assertRaises(ContractError):
                require_entity_id(invalid)

    def test_seeds_are_zero_padded_twenty_digit_decimals(self) -> None:
        self.assertEqual(format_seed(42), "00000000000000000042")
        self.assertEqual(format_seed(18446744073709551615), "18446744073709551615")

    def test_seeds_outside_u64_are_rejected(self) -> None:
        for value in (-1, 18446744073709551616):
            with self.subTest(value=value), self.assertRaises(ContractError):
                format_seed(value)

    def test_derived_seeds_are_stable_and_distinct_per_stage(self) -> None:
        root = format_seed(7)
        first = derive_seed(root, "DECOMPOSE")
        self.assertEqual(first, derive_seed(root, "DECOMPOSE"))
        self.assertNotEqual(first, derive_seed(root, "SYNTHESIZE_LAYERS"))
        self.assertNotEqual(first, derive_seed(format_seed(8), "DECOMPOSE"))
        self.assertEqual(len(first), 20)

    def test_malformed_root_seed_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            derive_seed("42", "DECOMPOSE")

    def test_attempt_prefix_confines_writes(self) -> None:
        attempt = Attempt("run.one", "stage.decompose", "attempt.decompose.0001", 1)
        self.assertEqual(attempt.output_prefix, "run.one/stage.decompose/attempt.decompose.0001")

    def test_attempt_number_must_be_positive(self) -> None:
        with self.assertRaises(ContractError):
            Attempt("run.one", "stage.decompose", "attempt.decompose.0001", 0)


class BudgetTests(unittest.TestCase):
    def test_network_egress_is_refused_by_default_and_on_request(self) -> None:
        ResourceBudget().validate()
        with self.assertRaises(ContractError):
            ResourceBudget(network_egress_allowed=True).validate()

    def test_non_positive_bounds_are_rejected(self) -> None:
        with self.assertRaises(ContractError):
            ResourceBudget(max_output_files=0).validate()


class OutcomeTests(unittest.TestCase):
    def test_terminal_outcomes_require_a_reason_code(self) -> None:
        for status in (StageStatus.BLOCKED, StageStatus.FAILED, StageStatus.CANCELLED):
            with self.subTest(status=status), self.assertRaises(ContractError):
                StageOutcome(status=status).validate()

    def test_non_terminal_outcomes_must_not_carry_a_reason_code(self) -> None:
        with self.assertRaises(ContractError):
            StageOutcome(status=StageStatus.SUCCEEDED, reason_code="USER_CANCELLED").validate()

    def test_failed_stages_cannot_publish_outputs(self) -> None:
        from oneclick2d.pipeline.context import ArtifactRef

        artifact = ArtifactRef("role", "application/json", "uri", "a" * 64, 1)
        with self.assertRaises(ContractError):
            StageOutcome(
                status=StageStatus.FAILED, reason_code="STAGE_INTERNAL_ERROR", outputs=(artifact,)
            ).validate()


class SinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.attempt = Attempt("run.one", "stage.decompose", "attempt.decompose.0001", 1)
        self.addCleanup(self.temporary.cleanup)

    def test_writes_are_digested_and_prefixed(self) -> None:
        sink = ArtifactSink(self.root, self.attempt, ResourceBudget())
        artifact = sink.write("layer.png", b"payload", role="layer_texture", media_type="image/png")
        self.assertTrue(artifact.uri.startswith(self.attempt.output_prefix))
        self.assertEqual(artifact.byte_length, 7)
        self.assertEqual(len(artifact.sha256), 64)

    def test_duplicate_names_are_rejected(self) -> None:
        sink = ArtifactSink(self.root, self.attempt, ResourceBudget())
        sink.write("a.png", b"x", role="layer_texture", media_type="image/png")
        with self.assertRaises(ContractError):
            sink.write("a.png", b"y", role="layer_texture", media_type="image/png")

    def test_unsafe_names_are_rejected(self) -> None:
        sink = ArtifactSink(self.root, self.attempt, ResourceBudget())
        for name in ("../escape", "/abs", "Upper.png", "a" * 200):
            with self.subTest(name=name), self.assertRaises(ContractError):
                sink.write(name, b"x", role="layer_texture", media_type="image/png")

    def test_output_byte_budget_is_enforced(self) -> None:
        sink = ArtifactSink(self.root, self.attempt, ResourceBudget(max_output_bytes=8))
        with self.assertRaises(ResourceLimitError):
            sink.write("big.bin", b"x" * 9, role="layer_texture", media_type="image/png")

    def test_output_file_budget_is_enforced(self) -> None:
        sink = ArtifactSink(self.root, self.attempt, ResourceBudget(max_output_files=1))
        sink.write("a.png", b"x", role="layer_texture", media_type="image/png")
        with self.assertRaises(ResourceLimitError):
            sink.write("b.png", b"x", role="layer_texture", media_type="image/png")


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def _context(self, budget: ResourceBudget | None = None, number: int = 1) -> StageContext:
        # Each attempt owns a distinct output prefix, so callers that build more
        # than one context must vary the attempt number.
        attempt = Attempt("run.one", "stage.decompose", f"attempt.decompose.{number:04d}", number)
        resolved = budget or ResourceBudget()
        return StageContext(
            attempt=attempt,
            seed=format_seed(1),
            budget=resolved,
            sink=ArtifactSink(self.root / "out", attempt, resolved),
            scratch_root=self.root / "scratch",
            cancellation=CancellationToken(),
            config={"version": "0.1.0"},
        )

    def test_cancellation_is_observed_at_checkpoints(self) -> None:
        context = self._context()
        context.checkpoint()
        context.cancellation.cancel()
        with self.assertRaises(CancellationRequested):
            context.checkpoint()

    def test_wall_clock_budget_is_observed_at_checkpoints(self) -> None:
        # A budget this small is already spent by the time the first checkpoint
        # runs, which is what proves the deadline is actually consulted.
        context = self._context(ResourceBudget(max_wall_seconds=1e-9))
        with self.assertRaises(ResourceLimitError):
            context.checkpoint()

    def test_scratch_budget_is_enforced_at_checkpoints(self) -> None:
        """A declared bound that nothing checks is not a bound."""
        context = self._context(ResourceBudget(max_scratch_bytes=64), number=3)
        context.checkpoint()
        (context.scratch / "big.bin").write_bytes(b"x" * 500)
        with self.assertRaises(ResourceLimitError):
            context.checkpoint()

    def test_peak_ram_budget_is_enforced_when_the_platform_reports_it(self) -> None:
        from oneclick2d.pipeline.context import peak_ram_bytes

        if not peak_ram_bytes():
            self.skipTest("platform does not expose peak RSS")
        context = self._context(ResourceBudget(max_peak_ram_bytes=1), number=4)
        with self.assertRaises(ResourceLimitError):
            context.checkpoint()

    def test_scratch_is_created_lazily_and_removed(self) -> None:
        context = self._context()
        scratch = context.scratch
        (scratch / "temp.bin").write_bytes(b"x" * 16)
        self.assertTrue(scratch.exists())
        self.assertEqual(context.cleanup(), 16)
        self.assertFalse(scratch.exists())

    def test_config_digest_is_stable_across_attempts(self) -> None:
        self.assertEqual(
            self._context(number=1).config_digest, self._context(number=2).config_digest
        )

    def test_two_attempts_cannot_share_an_output_prefix(self) -> None:
        """Retries must not overwrite an earlier attempt's outputs."""
        attempt = Attempt("run.one", "stage.decompose", "attempt.decompose.0001", 1)
        budget = ResourceBudget()
        ArtifactSink(self.root / "shared", attempt, budget)
        with self.assertRaises(FileExistsError):
            ArtifactSink(self.root / "shared", attempt, budget)


class LedgerTests(unittest.TestCase):
    def _result(self, attempt: Attempt) -> StageResult:
        return StageResult(
            attempt=attempt,
            stage_type="DECOMPOSE",
            adapter_id="adapter",
            adapter_version="0.1.0",
            producer_kind="deterministic",
            determinism="byte-exact",
            seed=format_seed(1),
            input_digest="a" * 64,
            config_digest="b" * 64,
            spec_digest="c" * 64,
            outcome=StageOutcome(status=StageStatus.SUCCEEDED),
            duration_ms=1,
            peak_scratch_bytes=0,
        )

    def test_a_superseded_attempt_cannot_publish(self) -> None:
        """A retry creates a new attempt; the old one finishing late must not
        overwrite the result of the attempt that now owns the stage.
        """
        ledger = RunLedger()
        first = Attempt("run.one", "stage.decompose", "attempt.decompose.0001", 1)
        second = Attempt("run.one", "stage.decompose", "attempt.decompose.0002", 2)
        ledger.open_attempt("stage.decompose", first.attempt_id)
        ledger.open_attempt("stage.decompose", second.attempt_id)
        with self.assertRaises(ContractError):
            ledger.publish(self._result(first))
        ledger.publish(self._result(second))

    def test_a_stage_publishes_once(self) -> None:
        ledger = RunLedger()
        attempt = Attempt("run.one", "stage.decompose", "attempt.decompose.0001", 1)
        ledger.open_attempt("stage.decompose", attempt.attempt_id)
        ledger.publish(self._result(attempt))
        with self.assertRaises(ContractError):
            ledger.publish(self._result(attempt))


class DagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def _definition(self, stage_type: str, execute) -> StageDefinition:
        slug = stage_type.lower().replace("_", "-")
        return StageDefinition(
            stage_id=f"stage.{slug}",
            stage_type=stage_type,
            adapter_id=f"adapter.{slug}",
            adapter_version="0.1.0",
            producer_kind="deterministic",
            determinism="byte-exact",
            config={"version": "0.1.0"},
            budget=ResourceBudget(),
            execute=execute,
        )

    def test_stage_order_matches_the_documented_dag(self) -> None:
        self.assertEqual(STAGE_ORDER[0], "INGEST_SCAN_NORMALIZE")
        self.assertEqual(STAGE_ORDER[-1], "VERIFY_EXPORTS")
        self.assertIn("MESH_AND_MINIMAL_RIG", STAGE_ORDER)

    def test_successful_run_threads_state_between_stages(self) -> None:
        def first(context, state):
            return StageOutcome(status=StageStatus.SUCCEEDED, payload={"value": 1})

        def second(context, state):
            self.assertEqual(state["value"], 1)
            return StageOutcome(status=StageStatus.SUCCEEDED, payload={"value": 2})

        outcome = run_pipeline(
            run_id="run.one",
            stages=[
                self._definition("INGEST_SCAN_NORMALIZE", first),
                self._definition("VALIDATE", second),
            ],
            workspace=self.workspace,
            root_seed=format_seed(1),
        )
        self.assertEqual(outcome.terminal_status, StageStatus.SUCCEEDED)
        self.assertEqual(outcome.state["value"], 2)
        self.assertEqual(len(outcome.results), 2)

    def test_a_blocked_stage_stops_the_run_with_its_reason_code(self) -> None:
        def blocking(context, state):
            raise IntakeRejected("unsupported", reason_code="INPUT_UNSUPPORTED")

        def unreached(context, state):  # pragma: no cover - must not run
            raise AssertionError("later stage ran after a blocking failure")

        outcome = run_pipeline(
            run_id="run.two",
            stages=[
                self._definition("INGEST_SCAN_NORMALIZE", blocking),
                self._definition("VALIDATE", unreached),
            ],
            workspace=self.workspace,
            root_seed=format_seed(1),
        )
        self.assertEqual(outcome.terminal_status, StageStatus.BLOCKED)
        self.assertEqual(outcome.reason_code, "INPUT_UNSUPPORTED")
        self.assertEqual(len(outcome.results), 1)

    def test_resource_exhaustion_is_reported_as_a_typed_failure(self) -> None:
        def exhausting(context, state):
            raise ResourceLimitError("too big")

        outcome = run_pipeline(
            run_id="run.three",
            stages=[self._definition("INGEST_SCAN_NORMALIZE", exhausting)],
            workspace=self.workspace,
            root_seed=format_seed(1),
        )
        self.assertEqual(outcome.terminal_status, StageStatus.FAILED)
        self.assertEqual(outcome.reason_code, "STAGE_RESOURCE_LIMIT_EXCEEDED")

    def test_cancellation_produces_a_cancelled_terminal_state(self) -> None:
        token = CancellationToken()

        def cancelling(context, state):
            token.cancel()
            context.checkpoint()
            return StageOutcome(status=StageStatus.SUCCEEDED)

        outcome = run_pipeline(
            run_id="run.four",
            stages=[self._definition("INGEST_SCAN_NORMALIZE", cancelling)],
            workspace=self.workspace,
            root_seed=format_seed(1),
            cancellation=token,
        )
        self.assertEqual(outcome.terminal_status, StageStatus.CANCELLED)
        self.assertEqual(outcome.reason_code, "USER_CANCELLED")

    def test_stages_supplied_out_of_dag_order_are_rejected(self) -> None:
        def noop(context, state):
            return StageOutcome(status=StageStatus.SUCCEEDED)

        with self.assertRaises(ContractError):
            run_pipeline(
                run_id="run.five",
                stages=[
                    self._definition("VALIDATE", noop),
                    self._definition("INGEST_SCAN_NORMALIZE", noop),
                ],
                workspace=self.workspace,
                root_seed=format_seed(1),
            )

    def test_stage_records_declare_everything_that_affects_output(self) -> None:
        def noop(context, state):
            return StageOutcome(status=StageStatus.SUCCEEDED)

        outcome = run_pipeline(
            run_id="run.six",
            stages=[self._definition("INGEST_SCAN_NORMALIZE", noop)],
            workspace=self.workspace,
            root_seed=format_seed(1),
        )
        record = outcome.results[0].as_manifest()
        for field in (
            "stage_id",
            "stage_type",
            "attempt_id",
            "adapter_id",
            "adapter_version",
            "producer_kind",
            "determinism",
            "seed_u64",
            "config_digest",
            "spec_digest",
            "status",
        ):
            self.assertIn(field, record)

    def test_unknown_stage_type_is_rejected(self) -> None:
        def noop(context, state):
            return StageOutcome(status=StageStatus.SUCCEEDED)

        with self.assertRaises(ContractError):
            self._definition("NOT_A_STAGE", noop)


if __name__ == "__main__":
    unittest.main()
