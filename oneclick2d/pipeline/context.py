"""Stage identity, resource budgets, cancellation and typed outcomes."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final

from ..errors import CancellationRequested, ContractError, ResourceLimitError
from ..strict_json import canonical_bytes, sha256_hex

ENTITY_ID_RE: Final[re.Pattern[str]] = re.compile(r"\A[a-z][a-z0-9_.-]{2,127}\Z")
SEED_DIGITS: Final[int] = 20
MAX_SEED: Final[int] = 18446744073709551615


class StageStatus(str, Enum):
    """Typed terminal states a stage attempt may reach."""

    SUCCEEDED = "succeeded"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


def require_entity_id(value: str, *, label: str = "identifier") -> str:
    """Validate a CIR entity ID (``docs/CIR_SPEC.md`` §3)."""
    if not isinstance(value, str) or not ENTITY_ID_RE.match(value):
        raise ContractError(f"{label} is not a valid entity identifier")
    return value


def format_seed(value: int) -> str:
    """Render a u64 seed as the zero-padded decimal string the CIR requires."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_SEED:
        raise ContractError("seed is outside the u64 range")
    return str(value).zfill(SEED_DIGITS)


def derive_seed(root_seed: str, *parts: str) -> str:
    """Derive a stable per-stage seed from the root seed and stage identity."""
    if not isinstance(root_seed, str) or len(root_seed) != SEED_DIGITS or not root_seed.isdigit():
        raise ContractError("root seed must be a 20-digit decimal string")
    digest = hashlib.sha256()
    digest.update(root_seed.encode("ascii"))
    for part in parts:
        digest.update(b"\x1f")
        digest.update(part.encode("utf-8"))
    return format_seed(int.from_bytes(digest.digest()[:8], "big"))


class CancellationToken:
    """Cooperative cancellation observed at stage checkpoints."""

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def checkpoint(self) -> None:
        if self._cancelled:
            raise CancellationRequested("cooperative cancellation was requested")


@dataclass(frozen=True)
class ResourceBudget:
    """Hard per-stage bounds. Exceeding any of them fails the stage closed."""

    max_wall_seconds: float = 900.0
    max_peak_ram_bytes: int = 4 * 1024 * 1024 * 1024
    max_scratch_bytes: int = 8 * 1024 * 1024 * 1024
    max_output_bytes: int = 2 * 1024 * 1024 * 1024
    max_output_files: int = 4096
    max_canvas_pixels: int = 40 * 1024 * 1024
    network_egress_allowed: bool = False

    def validate(self) -> None:
        if self.network_egress_allowed:
            raise ContractError("phase-1 stages must not request network egress")
        for name, value in (
            ("max_wall_seconds", self.max_wall_seconds),
            ("max_peak_ram_bytes", self.max_peak_ram_bytes),
            ("max_scratch_bytes", self.max_scratch_bytes),
            ("max_output_bytes", self.max_output_bytes),
            ("max_output_files", self.max_output_files),
            ("max_canvas_pixels", self.max_canvas_pixels),
        ):
            if value <= 0:
                raise ContractError(f"{name} must be positive")


@dataclass(frozen=True)
class Attempt:
    """Run, stage and attempt identity for one execution of a stage."""

    run_id: str
    stage_id: str
    attempt_id: str
    attempt_number: int

    def __post_init__(self) -> None:
        require_entity_id(self.run_id, label="run id")
        require_entity_id(self.stage_id, label="stage id")
        require_entity_id(self.attempt_id, label="attempt id")
        if self.attempt_number < 1:
            raise ContractError("attempt number must be positive")

    @property
    def output_prefix(self) -> str:
        """Workers may only write beneath their own attempt prefix."""
        return f"{self.run_id}/{self.stage_id}/{self.attempt_id}"


@dataclass(frozen=True)
class ArtifactRef:
    """A published stage output bound to its role, digest and length."""

    role: str
    media_type: str
    uri: str
    sha256: str
    byte_length: int

    def as_manifest(self) -> dict[str, object]:
        return {
            "role": self.role,
            "media_type": self.media_type,
            "uri": self.uri,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
        }


@dataclass(frozen=True)
class StageOutcome:
    """The typed result a stage adapter returns."""

    status: StageStatus
    outputs: tuple[ArtifactRef, ...] = ()
    reason_code: str | None = None
    finding_codes: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        terminal = {StageStatus.BLOCKED, StageStatus.FAILED, StageStatus.CANCELLED}
        if self.status in terminal and not self.reason_code:
            raise ContractError("terminal stage outcomes require a reason code")
        if self.status not in terminal and self.reason_code:
            raise ContractError("non-terminal stage outcomes must not carry a reason code")
        if self.status in {StageStatus.FAILED, StageStatus.CANCELLED} and self.outputs:
            raise ContractError("failed or cancelled stages must not publish outputs")


@dataclass(frozen=True)
class StageResult:
    """A recorded stage attempt, including everything that affected its output."""

    attempt: Attempt
    stage_type: str
    adapter_id: str
    adapter_version: str
    producer_kind: str
    determinism: str
    seed: str
    input_digest: str
    config_digest: str
    spec_digest: str
    outcome: StageOutcome
    duration_ms: int
    peak_scratch_bytes: int

    def as_manifest(self) -> dict[str, object]:
        record: dict[str, object] = {
            "stage_id": self.attempt.stage_id,
            "stage_type": self.stage_type,
            "attempt_id": self.attempt.attempt_id,
            "attempt_number": self.attempt.attempt_number,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "producer_kind": self.producer_kind,
            "determinism": self.determinism,
            "seed_u64": self.seed,
            "input_digest": self.input_digest,
            "config_digest": self.config_digest,
            "spec_digest": self.spec_digest,
            "status": self.outcome.status.value,
            "duration_ms": self.duration_ms,
            "outputs": [artifact.as_manifest() for artifact in self.outcome.outputs],
        }
        if self.outcome.reason_code:
            record["reason_code"] = self.outcome.reason_code
        if self.outcome.finding_codes:
            record["finding_codes"] = list(self.outcome.finding_codes)
        return record


class ArtifactSink:
    """Bounded writer that confines a stage to its own attempt prefix."""

    def __init__(self, root: Path, attempt: Attempt, budget: ResourceBudget) -> None:
        self._root = root
        self._attempt = attempt
        self._budget = budget
        self._names: set[str] = set()
        self._total = 0
        self._directory = root / attempt.stage_id / attempt.attempt_id
        self._directory.mkdir(parents=True, exist_ok=False)

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def total_bytes(self) -> int:
        return self._total

    def write(self, name: str, data: bytes, *, role: str, media_type: str) -> ArtifactRef:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", name):
            raise ContractError("artifact name is not permitted")
        if name in self._names:
            raise ContractError("artifact name is duplicated within the attempt")
        if len(self._names) + 1 > self._budget.max_output_files:
            raise ResourceLimitError("stage output file limit exceeded")
        if self._total + len(data) > self._budget.max_output_bytes:
            raise ResourceLimitError("stage output byte limit exceeded")
        target = self._directory / name
        target.write_bytes(data)
        self._names.add(name)
        self._total += len(data)
        return ArtifactRef(
            role=role,
            media_type=media_type,
            uri=f"{self._attempt.output_prefix}/{name}",
            sha256=sha256_hex(data),
            byte_length=len(data),
        )


class StageContext:
    """Everything a stage adapter is allowed to see and touch."""

    def __init__(
        self,
        *,
        attempt: Attempt,
        seed: str,
        budget: ResourceBudget,
        sink: ArtifactSink,
        scratch_root: Path,
        cancellation: CancellationToken,
        config: dict[str, Any],
    ) -> None:
        budget.validate()
        self.attempt = attempt
        self.seed = seed
        self.budget = budget
        self.sink = sink
        self.cancellation = cancellation
        self.config = config
        self._scratch_root = scratch_root
        self._scratch: Path | None = None
        self._started = time.monotonic()

    @property
    def scratch(self) -> Path:
        if self._scratch is None:
            self._scratch_root.mkdir(parents=True, exist_ok=True)
            self._scratch = Path(
                tempfile.mkdtemp(prefix=f"{self.attempt.stage_id}-", dir=self._scratch_root)
            )
        return self._scratch

    @property
    def config_digest(self) -> str:
        return sha256_hex(canonical_bytes(self.config))

    def checkpoint(self) -> None:
        """Observe cancellation, the wall-clock bound and peak RAM together.

        Peak RSS is process-wide and only ever grows, so it cannot attribute
        usage to one stage. It is still checked here because the alternative is
        declaring a RAM budget that nothing enforces. When the platform does not
        expose the figure, the check is skipped rather than guessed at.
        """
        self.cancellation.checkpoint()
        if time.monotonic() - self._started > self.budget.max_wall_seconds:
            raise ResourceLimitError("stage wall-clock budget exceeded")
        observed = peak_ram_bytes()
        if observed and observed > self.budget.max_peak_ram_bytes:
            raise ResourceLimitError("observed peak RAM exceeded the stage budget")
        # ``scratch_bytes`` raises once the on-disk total passes the budget.
        self.scratch_bytes()

    def scratch_bytes(self) -> int:
        if self._scratch is None:
            return 0
        total = 0
        for path in self._scratch.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
                if total > self.budget.max_scratch_bytes:
                    raise ResourceLimitError("stage scratch budget exceeded")
        return total

    def cleanup(self) -> int:
        """Remove scratch and report its peak observed size."""
        if self._scratch is None:
            return 0
        try:
            total = 0
            for path in self._scratch.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
        except OSError:
            total = 0
        shutil.rmtree(self._scratch, ignore_errors=True)
        self._scratch = None
        return total

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)


def peak_ram_bytes() -> int:
    """Best-effort peak RSS for the current process, or 0 when unavailable."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError):  # pragma: no cover - platform dependent
        return 0
    # Linux reports kibibytes; macOS reports bytes.
    return usage * 1024 if os.uname().sysname == "Linux" else usage
