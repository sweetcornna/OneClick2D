"""Frozen contracts for the disposable Gate F runner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import ArtifactSink, CancellationToken


class StageStatus(str, Enum):
    SUCCEEDED = "succeeded"
    REVIEW = "review"
    FALLBACK = "fallback"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProducerKind(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_BACKED = "model_backed"


class Determinism(str, Enum):
    BYTE_EXACT = "byte-exact"
    NUMERIC_TOLERANCE = "numeric-tolerance"
    NONDETERMINISTIC_DECLARED = "nondeterministic-declared"


class SpecValidationError(ValueError):
    """The run was rejected before execution."""


class StageContractError(RuntimeError):
    """An adapter violated its declared stage contract."""


class CancellationRequested(RuntimeError):
    """Cooperative cancellation was observed."""


class ResourceLimitExceeded(RuntimeError):
    """A runner-enforced or observed resource limit was exceeded."""


@dataclass(frozen=True)
class ResourceLimits:
    max_wall_time_ms: int
    max_cpu_time_ms: int
    max_peak_ram_bytes: int
    max_scratch_bytes: int
    max_output_bytes: int
    max_output_files: int
    max_peak_vram_bytes: int
    gpu_allowed: bool


@dataclass(frozen=True)
class SourceSpec:
    role: str
    sha256: str
    media_type: str
    max_bytes: int


@dataclass(frozen=True)
class StageTemplate:
    id: str
    stage_type: str
    adapter_id: str
    config_uri: str
    config_sha256: str
    limits: ResourceLimits


@dataclass(frozen=True)
class RunSpec:
    path: Path
    exact_bytes: bytes
    exact_sha256: str
    execution_profile: str
    root_seed_u64: str
    source: SourceSpec
    expected_result_role: str
    execution_provider: str
    stages: tuple[StageTemplate, ...]


@dataclass(frozen=True)
class ArtifactRef:
    role: str
    media_type: str
    path: Path
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
    status: StageStatus
    outputs: tuple[ArtifactRef, ...] = ()
    reason_code: str | None = None
    finding_codes: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.status in {StageStatus.FAILED, StageStatus.BLOCKED, StageStatus.CANCELLED}:
            if not self.reason_code:
                raise StageContractError("terminal stage outcomes require a reason code")
        elif self.reason_code:
            raise StageContractError("nonterminal stage outcomes cannot have a reason code")
        if self.status in {StageStatus.CANCELLED, StageStatus.FAILED} and self.outputs:
            raise StageContractError("cancelled or failed stages cannot publish outputs")


@dataclass(frozen=True)
class ResolvedStageSpec:
    stage: StageTemplate
    attempt_id: str
    contract_id: str
    adapter_version: str
    producer_kind: ProducerKind
    determinism: Determinism
    seed_u64: str
    config_bytes: bytes
    input_artifacts: tuple[ArtifactRef, ...]
    digest: str


@dataclass(frozen=True)
class StageContext:
    spec: ResolvedStageSpec
    sink: "ArtifactSink"
    scratch_dir: Path
    cancellation: "CancellationToken"


class StageAdapter(Protocol):
    adapter_id: str
    contract_id: str
    stage_type: str
    implementation_version: str
    execution_profile: str
    execution_provider: str
    producer_kind: ProducerKind
    determinism: Determinism

    def execute(self, context: StageContext) -> StageOutcome:
        """Execute a stage without mutating committed inputs."""
