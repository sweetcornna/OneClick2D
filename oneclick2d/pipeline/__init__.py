"""Stage framework and the phase-1 stage DAG.

Implements ``docs/ARCHITECTURE.md`` §6 and §7: every stage takes immutable input
and a versioned stage spec, carries run/stage/attempt identity and a stable seed,
declares digests of everything that affects output, enforces CPU/RAM/disk/time
and output bounds, checks cancellation, returns a typed outcome, cleans scratch
and defaults to no network egress.
"""

from __future__ import annotations

from .context import (
    Attempt,
    CancellationToken,
    ResourceBudget,
    StageContext,
    StageOutcome,
    StageResult,
    StageStatus,
)
from .dag import STAGE_ORDER, StageDefinition, run_pipeline

__all__ = [
    "Attempt",
    "CancellationToken",
    "ResourceBudget",
    "STAGE_ORDER",
    "StageContext",
    "StageDefinition",
    "StageOutcome",
    "StageResult",
    "StageStatus",
    "run_pipeline",
]
