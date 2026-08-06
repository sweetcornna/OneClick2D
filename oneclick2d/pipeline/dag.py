"""The phase-1 stage DAG and its runner (``docs/ARCHITECTURE.md`` §7).

Stages execute in a fixed order. Each attempt gets fresh identity, a derived
seed, its own output prefix and bounded scratch; retries create a new attempt
rather than overwriting an earlier one, and a result that has lost ownership can
never be published (``docs/ARCHITECTURE.md`` §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final, Sequence

from ..errors import CancellationRequested, ContractError, OneClick2DError, ResourceLimitError
from .context import (
    ArtifactSink,
    Attempt,
    CancellationToken,
    ResourceBudget,
    StageContext,
    StageOutcome,
    StageResult,
    StageStatus,
    derive_seed,
)

STAGE_ORDER: Final[tuple[str, ...]] = (
    "INGEST_SCAN_NORMALIZE",
    "VALIDATE",
    "DECOMPOSE",
    "PLAN_AND_BOUNDED_COMPLETE",
    "SYNTHESIZE_LAYERS",
    "MESH_AND_MINIMAL_RIG",
    "VERIFY_PROJECT",
    "COMPILE_PREVIEW",
    "EXPORT_OC2D",
    "EXPORT_PSD",
    "VERIFY_EXPORTS",
)


@dataclass(frozen=True)
class StageDefinition:
    """A stage's immutable identity and everything that affects its output."""

    stage_id: str
    stage_type: str
    adapter_id: str
    adapter_version: str
    producer_kind: str
    determinism: str
    config: dict[str, Any]
    budget: ResourceBudget
    execute: Callable[[StageContext, dict[str, Any]], StageOutcome]

    def __post_init__(self) -> None:
        if self.stage_type not in STAGE_ORDER:
            raise ContractError("stage type is not part of the phase-1 DAG")
        if self.producer_kind not in ("deterministic", "model_backed"):
            raise ContractError("producer kind is unsupported")
        if self.determinism not in ("byte-exact", "numeric-tolerance", "nondeterministic-declared"):
            raise ContractError("determinism declaration is unsupported")


class RunLedger:
    """Tracks attempt ownership so late or duplicate results cannot publish."""

    def __init__(self) -> None:
        self._current: dict[str, str] = {}
        self._published: dict[str, StageResult] = {}

    def open_attempt(self, stage_id: str, attempt_id: str) -> None:
        self._current[stage_id] = attempt_id

    def owns(self, stage_id: str, attempt_id: str) -> bool:
        return self._current.get(stage_id) == attempt_id

    def publish(self, result: StageResult) -> None:
        stage_id = result.attempt.stage_id
        if not self.owns(stage_id, result.attempt.attempt_id):
            # A superseded attempt finishing late must not overwrite the result
            # of the attempt that currently owns the stage.
            raise ContractError("stage result lost attempt ownership and cannot publish")
        if stage_id in self._published:
            raise ContractError("stage has already published a terminal result")
        self._published[stage_id] = result

    @property
    def results(self) -> tuple[StageResult, ...]:
        return tuple(self._published[key] for key in self._published)

    def result(self, stage_id: str) -> StageResult | None:
        return self._published.get(stage_id)


@dataclass(frozen=True)
class RunOutcome:
    """The terminal state of a whole run."""

    run_id: str
    terminal_status: StageStatus
    results: tuple[StageResult, ...]
    reason_code: str | None
    state: dict[str, Any]


def run_pipeline(
    *,
    run_id: str,
    stages: Sequence[StageDefinition],
    workspace: Path,
    root_seed: str,
    cancellation: CancellationToken | None = None,
    initial_state: dict[str, Any] | None = None,
) -> RunOutcome:
    """Execute stages in DAG order, stopping at the first terminal failure."""
    ordered = sorted(stages, key=lambda item: STAGE_ORDER.index(item.stage_type))
    if [item.stage_type for item in ordered] != [item.stage_type for item in stages]:
        raise ContractError("stages were supplied out of DAG order")

    token = cancellation or CancellationToken()
    ledger = RunLedger()
    state: dict[str, Any] = dict(initial_state or {})
    attempt_root = workspace / run_id
    attempt_root.mkdir(parents=True, exist_ok=True)
    scratch_root = attempt_root / ".scratch"

    terminal = StageStatus.SUCCEEDED
    reason_code: str | None = None

    for definition in ordered:
        attempt_number = 1
        attempt = Attempt(
            run_id=run_id,
            stage_id=definition.stage_id,
            attempt_id=f"attempt.{definition.stage_id.replace('stage.', '')}.{attempt_number:04d}",
            attempt_number=attempt_number,
        )
        ledger.open_attempt(definition.stage_id, attempt.attempt_id)
        sink = ArtifactSink(attempt_root, attempt, definition.budget)
        context = StageContext(
            attempt=attempt,
            seed=derive_seed(root_seed, definition.stage_type, definition.stage_id),
            budget=definition.budget,
            sink=sink,
            scratch_root=scratch_root,
            cancellation=token,
            config=definition.config,
        )
        try:
            outcome = definition.execute(context, state)
            outcome.validate()
        except CancellationRequested:
            outcome = StageOutcome(status=StageStatus.CANCELLED, reason_code="USER_CANCELLED")
        except ResourceLimitError:
            outcome = StageOutcome(
                status=StageStatus.FAILED, reason_code="STAGE_RESOURCE_LIMIT_EXCEEDED"
            )
        except OneClick2DError as exc:
            status = (
                StageStatus.BLOCKED
                if exc.reason_code in ("INPUT_UNSUPPORTED", "INPUT_FILE_UNSAFE")
                else StageStatus.FAILED
            )
            outcome = StageOutcome(status=status, reason_code=exc.reason_code)
        finally:
            peak_scratch = context.cleanup()

        result = StageResult(
            attempt=attempt,
            stage_type=definition.stage_type,
            adapter_id=definition.adapter_id,
            adapter_version=definition.adapter_version,
            producer_kind=definition.producer_kind,
            determinism=definition.determinism,
            seed=context.seed,
            input_digest=state.get("input_digest", "0" * 64),
            config_digest=context.config_digest,
            spec_digest=context.config_digest,
            outcome=outcome,
            duration_ms=context.elapsed_ms(),
            peak_scratch_bytes=peak_scratch,
        )
        ledger.publish(result)

        if outcome.status in (StageStatus.FAILED, StageStatus.BLOCKED, StageStatus.CANCELLED):
            terminal = outcome.status
            reason_code = outcome.reason_code
            break
        state.update(outcome.payload)
        if outcome.status is StageStatus.NEEDS_REVIEW:
            terminal = StageStatus.NEEDS_REVIEW

    return RunOutcome(
        run_id=run_id,
        terminal_status=terminal,
        results=ledger.results,
        reason_code=reason_code,
        state=state,
    )
