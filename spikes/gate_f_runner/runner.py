"""Single-process orchestration for the disposable Gate F spike."""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .contracts import (
    ArtifactRef,
    CancellationRequested,
    Determinism,
    ProducerKind,
    ResolvedStageSpec,
    ResourceLimitExceeded,
    ResourceLimits,
    RunSpec,
    SourceSpec,
    SpecValidationError,
    StageAdapter,
    StageContext,
    StageContractError,
    StageOutcome,
    StageStatus,
    StageTemplate,
)
from .runtime import (
    ArtifactSink,
    CancellationToken,
    ID_RE,
    SHA256_RE,
    RunWorkspace,
    canonical_json_bytes,
    derive_stage_seed,
    digest_framed,
    read_bounded_file,
    resolve_safe_file,
    sha256_bytes,
    sha256_file,
    strict_load_json_bytes,
)

_ALLOWED_ROOT = {
    "$schema", "format", "format_version", "scope", "execution_profile",
    "root_seed_u64", "source", "expected_result_role", "stages",
}
_ALLOWED_STAGE = {"id", "stage_type", "adapter_id", "config_uri", "config_sha256", "limits"}
_ALLOWED_LIMITS = {
    "max_wall_time_ms", "max_cpu_time_ms", "max_peak_ram_bytes", "max_scratch_bytes",
    "max_output_bytes", "max_output_files", "max_peak_vram_bytes", "gpu_allowed",
}
_SUPPORTED_EXECUTION_PROFILES = {
    "python-stdlib-in-process-v1",
    "python-pillow-12.1.0-in-process-v1",
}
_STAGE_TYPE_RE = re.compile(r"^oc2d\.spike\.[a-z0-9.-]+$")


def _exact_keys(value: dict[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown or missing:
        raise SpecValidationError(f"{label} has unknown or missing fields")


def _positive_int(value: Any, *, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SpecValidationError(f"invalid {label}")
    return value


def _parse_limits(value: Any) -> ResourceLimits:
    if not isinstance(value, dict):
        raise SpecValidationError("limits must be an object")
    _exact_keys(value, _ALLOWED_LIMITS, _ALLOWED_LIMITS, "limits")
    if value["gpu_allowed"] is not False or value["max_peak_vram_bytes"] != 0:
        raise SpecValidationError("the standard-library spike cannot use a GPU")
    return ResourceLimits(
        max_wall_time_ms=_positive_int(value["max_wall_time_ms"], minimum=1, maximum=3_600_000, label="wall limit"),
        max_cpu_time_ms=_positive_int(value["max_cpu_time_ms"], minimum=1, maximum=3_600_000, label="CPU limit"),
        max_peak_ram_bytes=_positive_int(value["max_peak_ram_bytes"], minimum=1_048_576, maximum=9_007_199_254_740_991, label="RAM limit"),
        max_scratch_bytes=_positive_int(value["max_scratch_bytes"], minimum=0, maximum=10_737_418_240, label="scratch limit"),
        max_output_bytes=_positive_int(value["max_output_bytes"], minimum=1, maximum=10_737_418_240, label="output limit"),
        max_output_files=_positive_int(value["max_output_files"], minimum=1, maximum=1024, label="output file limit"),
        max_peak_vram_bytes=0,
        gpu_allowed=False,
    )


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, StageAdapter] = {}

    def register(self, adapter: StageAdapter) -> None:
        if adapter.adapter_id in self._adapters or not ID_RE.fullmatch(adapter.adapter_id):
            raise ValueError("invalid or duplicate adapter id")
        self._adapters[adapter.adapter_id] = adapter

    def resolve(self, adapter_id: str) -> StageAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise SpecValidationError("run spec references an unregistered adapter") from exc


def load_run_spec(path: Path, registry: AdapterRegistry) -> tuple[RunSpec, dict[str, bytes]]:
    exact = read_bounded_file(path)
    value = strict_load_json_bytes(exact)
    if not isinstance(value, dict):
        raise SpecValidationError("run spec root must be an object")
    _exact_keys(value, _ALLOWED_ROOT, _ALLOWED_ROOT - {"$schema"}, "run spec")
    execution_profile = value.get("execution_profile")
    expected_result_role = value.get("expected_result_role")
    if (
        value.get("format") != "oneclick2d.gate-f-run-spec"
        or value.get("format_version") != "0.1.0"
        or value.get("scope") != "disposable-gate-f-spike"
        or execution_profile not in _SUPPORTED_EXECUTION_PROFILES
        or not isinstance(expected_result_role, str)
        or not ID_RE.fullmatch(expected_result_role)
    ):
        raise SpecValidationError("unsupported run specification")
    root_seed = value.get("root_seed_u64")
    derive_stage_seed(root_seed, "stage.seed-validation")
    source_value = value.get("source")
    if not isinstance(source_value, dict):
        raise SpecValidationError("source must be an object")
    source_fields = {"role", "sha256", "media_type", "max_bytes"}
    _exact_keys(source_value, source_fields, source_fields, "source")
    if not isinstance(source_value["role"], str) or not ID_RE.fullmatch(source_value["role"]):
        raise SpecValidationError("invalid source role")
    if not isinstance(source_value["sha256"], str) or not SHA256_RE.fullmatch(source_value["sha256"]):
        raise SpecValidationError("invalid source digest")
    if not isinstance(source_value["media_type"], str) or not 3 <= len(source_value["media_type"]) <= 127:
        raise SpecValidationError("invalid source media type")
    source = SourceSpec(
        role=source_value["role"],
        sha256=source_value["sha256"],
        media_type=source_value["media_type"],
        max_bytes=_positive_int(source_value["max_bytes"], minimum=1, maximum=104_857_600, label="source size"),
    )
    stages_value = value.get("stages")
    if not isinstance(stages_value, list) or not 1 <= len(stages_value) <= 32:
        raise SpecValidationError("stages must contain 1 to 32 entries")
    stages: list[StageTemplate] = []
    configs: dict[str, bytes] = {}
    providers: set[str] = set()
    ids: set[str] = set()
    for raw in stages_value:
        if not isinstance(raw, dict):
            raise SpecValidationError("stage must be an object")
        _exact_keys(raw, _ALLOWED_STAGE, _ALLOWED_STAGE, "stage")
        stage_id = raw["id"]
        if not isinstance(stage_id, str) or not ID_RE.fullmatch(stage_id) or stage_id in ids:
            raise SpecValidationError("invalid or duplicate stage id")
        if not isinstance(raw["stage_type"], str) or not _STAGE_TYPE_RE.fullmatch(raw["stage_type"]):
            raise SpecValidationError("invalid stage type")
        adapter = registry.resolve(raw["adapter_id"])
        if raw["stage_type"] != adapter.stage_type:
            raise SpecValidationError("stage type does not match adapter")
        if adapter.execution_profile != execution_profile:
            raise SpecValidationError("adapter execution profile does not match run spec")
        config_path = resolve_safe_file(path.parent, raw["config_uri"])
        config_data = read_bounded_file(config_path)
        if not isinstance(raw["config_sha256"], str) or sha256_bytes(config_data) != raw["config_sha256"]:
            raise SpecValidationError("configuration digest mismatch")
        strict_load_json_bytes(config_data)
        stages.append(
            StageTemplate(
                id=stage_id,
                stage_type=raw["stage_type"],
                adapter_id=raw["adapter_id"],
                config_uri=raw["config_uri"],
                config_sha256=raw["config_sha256"],
                limits=_parse_limits(raw["limits"]),
            )
        )
        configs[raw["config_uri"]] = config_data
        providers.add(adapter.execution_provider)
        ids.add(stage_id)
    if len(providers) != 1:
        raise SpecValidationError("a run must use one execution provider")
    return (
        RunSpec(
            path=path,
            exact_bytes=exact,
            exact_sha256=sha256_bytes(exact),
            execution_profile=execution_profile,
            root_seed_u64=root_seed,
            source=source,
            expected_result_role=expected_result_role,
            execution_provider=next(iter(providers)),
            stages=tuple(stages),
        ),
        configs,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class PipelineRunner:
    def __init__(self, registry: AdapterRegistry, workspace_root: Path) -> None:
        self._registry = registry
        self._workspace_root = workspace_root

    def run(
        self,
        *,
        spec_path: Path,
        source_path: Path,
        run_id: str,
        source_revision: str,
        build_id: str,
        stage_observer: Callable[[str, str, StageStatus | None], None] | None = None,
    ) -> tuple[StageStatus, Path]:
        spec, config_paths = load_run_spec(spec_path, self._registry)
        if not ID_RE.fullmatch(source_revision) or not ID_RE.fullmatch(build_id):
            raise SpecValidationError("invalid build or source revision")
        source_data = read_bounded_file(source_path, spec.source.max_bytes)
        if sha256_bytes(source_data) != spec.source.sha256:
            raise SpecValidationError("source size or digest mismatch")

        workspace = RunWorkspace(self._workspace_root, run_id)
        workspace.create()
        started_at = _utc_now()
        spec_copy, copied_configs, source_copy = workspace.materialize(spec.exact_bytes, config_paths, source_data)
        if sha256_file(spec_copy) != spec.exact_sha256 or sha256_file(source_copy) != spec.source.sha256:
            raise SpecValidationError("materialized input digest mismatch")
        source_ref = ArtifactRef(
            role=spec.source.role,
            media_type=spec.source.media_type,
            path=source_copy,
            uri=source_copy.relative_to(workspace.run_dir).as_posix(),
            sha256=spec.source.sha256,
            byte_length=source_copy.stat().st_size,
        )
        current_inputs = (source_ref,)
        records: list[dict[str, object]] = []
        aggregate = StageStatus.SUCCEEDED
        terminal_reason: str | None = None
        active_attempt: Path | None = None
        active_candidate: Path | None = None
        active_scratch: Path | None = None

        try:
            for index, stage in enumerate(spec.stages):
                if stage_observer is not None:
                    try:
                        stage_observer(stage.id, "started", None)
                    except Exception:
                        pass
                workspace_token = CancellationToken(workspace.cancel_sentinel)
                workspace_token.checkpoint()
                adapter = self._registry.resolve(stage.adapter_id)
                attempt_id = f"attempt.{index + 1:03d}"
                seed = derive_stage_seed(spec.root_seed_u64, stage.id)
                config_bytes = read_bounded_file(copied_configs[stage.config_uri])
                if sha256_bytes(config_bytes) != stage.config_sha256:
                    raise SpecValidationError("materialized configuration digest mismatch")
                for artifact in current_inputs:
                    if artifact.path.stat().st_size != artifact.byte_length or sha256_file(artifact.path) != artifact.sha256:
                        raise SpecValidationError("stage input artifact digest mismatch")
                resolved_identity = {
                    "source_revision": source_revision,
                    "build_id": build_id,
                    "python": sys.version,
                    "stage_id": stage.id,
                    "stage_type": stage.stage_type,
                    "adapter_id": adapter.adapter_id,
                    "contract_id": adapter.contract_id,
                    "adapter_version": adapter.implementation_version,
                    "execution_profile": adapter.execution_profile,
                    "execution_provider": adapter.execution_provider,
                    "producer_kind": adapter.producer_kind.value,
                    "determinism": adapter.determinism.value,
                    "config_sha256": stage.config_sha256,
                    "seed_u64": seed,
                    "limits": asdict(stage.limits),
                    "inputs": [
                        {
                            "role": artifact.role,
                            "media_type": artifact.media_type,
                            "sha256": artifact.sha256,
                            "byte_length": artifact.byte_length,
                        }
                        for artifact in current_inputs
                    ],
                }
                framed = (canonical_json_bytes(resolved_identity),)
                resolved = ResolvedStageSpec(
                    stage=stage,
                    attempt_id=attempt_id,
                    contract_id=adapter.contract_id,
                    adapter_version=adapter.implementation_version,
                    producer_kind=adapter.producer_kind,
                    determinism=adapter.determinism,
                    seed_u64=seed,
                    config_bytes=config_bytes,
                    input_artifacts=current_inputs,
                    digest=digest_framed("oneclick2d.gate-f.resolved-stage.v1", framed),
                )
                attempt, candidate, scratch = workspace.begin_attempt(stage.id, attempt_id)
                active_attempt, active_candidate, active_scratch = attempt, candidate, scratch
                sink = ArtifactSink(candidate, workspace.run_dir, stage.limits)
                wall_start = time.monotonic()
                cpu_start = time.process_time()
                outcome: StageOutcome
                unexpected = False
                try:
                    spec_record = {
                        "stage_id": stage.id,
                        "attempt_id": attempt_id,
                        "resolved_stage_spec_sha256": resolved.digest,
                        "seed_u64": seed,
                        "input_sha256": [item.sha256 for item in current_inputs],
                    }
                    (attempt / "stage-spec.json").write_bytes(canonical_json_bytes(spec_record))
                    execution_error: BaseException | None = None
                    try:
                        outcome = adapter.execute(StageContext(resolved, sink, scratch, workspace_token))
                    except BaseException as exc:
                        execution_error = exc
                    input_mutated = False
                    for artifact in current_inputs:
                        try:
                            intact = artifact.path.stat().st_size == artifact.byte_length and sha256_file(artifact.path) == artifact.sha256
                        except OSError:
                            intact = False
                        if not intact:
                            input_mutated = True
                            if artifact is source_ref:
                                temp = source_copy.with_suffix(".restore")
                                temp.write_bytes(source_data)
                                os.replace(temp, source_copy)
                    if input_mutated:
                        raise StageContractError("adapter mutated an immutable input")
                    if execution_error is not None:
                        raise execution_error
                    outcome.validate()
                    if outcome.outputs != sink.artifacts:
                        raise StageContractError("outcome does not match bounded sink artifacts")
                    workspace_token.checkpoint()
                except (CancellationRequested, KeyboardInterrupt):
                    outcome = StageOutcome(StageStatus.CANCELLED, reason_code="USER_CANCELLED")
                except StageContractError:
                    outcome = StageOutcome(StageStatus.FAILED, reason_code="STAGE_CONTRACT_VIOLATION")
                except ResourceLimitExceeded:
                    outcome = StageOutcome(StageStatus.FAILED, reason_code="STAGE_RESOURCE_LIMIT_EXCEEDED")
                except Exception:
                    unexpected = True
                    outcome = StageOutcome(StageStatus.FAILED, reason_code="STAGE_INTERNAL_ERROR")
                wall_ms = max(0, round((time.monotonic() - wall_start) * 1000))
                cpu_ms = max(0, round((time.process_time() - cpu_start) * 1000))
                if outcome.status not in {StageStatus.CANCELLED, StageStatus.FAILED}:
                    if wall_ms > stage.limits.max_wall_time_ms or cpu_ms > stage.limits.max_cpu_time_ms or _directory_size(scratch) > stage.limits.max_scratch_bytes:
                        outcome = StageOutcome(StageStatus.FAILED, reason_code="STAGE_RESOURCE_LIMIT_EXCEEDED")
                committed: tuple[ArtifactRef, ...] = ()
                if outcome.status in {StageStatus.SUCCEEDED, StageStatus.REVIEW, StageStatus.FALLBACK, StageStatus.BLOCKED}:
                    try:
                        committed = workspace.commit(stage.id, attempt_id, candidate, outcome.outputs)
                    except ResourceLimitExceeded:
                        outcome = StageOutcome(StageStatus.FAILED, reason_code="STAGE_RESOURCE_LIMIT_EXCEEDED")
                record = {
                    "id": stage.id,
                    "attempt_id": attempt_id,
                    "contract_id": adapter.contract_id,
                    "stage_type": stage.stage_type,
                    "adapter_id": adapter.adapter_id,
                    "implementation_version": adapter.implementation_version,
                    "producer_kind": adapter.producer_kind.value,
                    "determinism": adapter.determinism.value,
                    "resolved_stage_spec_sha256": resolved.digest,
                    "config_sha256": stage.config_sha256,
                    "seed_u64": seed,
                    "input_sha256": [item.sha256 for item in current_inputs],
                    "outputs": [item.as_manifest() for item in committed],
                    "status": outcome.status.value,
                    "reason_code": outcome.reason_code,
                    "finding_codes": list(outcome.finding_codes),
                    "duration_ms": wall_ms,
                    "cpu_time_ms": cpu_ms,
                    "limits": asdict(stage.limits),
                    "enforcement": {
                        "output": "hard",
                        "scratch": "post-stage-observed",
                        "wall_time": "cooperative-post-stage-observed",
                        "cpu_time": "post-stage-observed",
                        "ram": "unavailable",
                        "vram": "not-applicable",
                        "egress": "not-enforced-in-process",
                    },
                    "scratch_cleaned": True,
                }
                (attempt / "stage-outcome.json").write_bytes(canonical_json_bytes(record))
                records.append(record)
                workspace.clean_attempt(attempt, candidate, scratch)
                active_attempt = active_candidate = active_scratch = None
                if stage_observer is not None:
                    try:
                        stage_observer(stage.id, "completed", outcome.status)
                    except Exception:
                        pass
                if outcome.status in {StageStatus.REVIEW, StageStatus.FALLBACK}:
                    if aggregate is StageStatus.SUCCEEDED or outcome.status is StageStatus.REVIEW:
                        aggregate = outcome.status
                if outcome.status in {StageStatus.BLOCKED, StageStatus.CANCELLED, StageStatus.FAILED}:
                    aggregate = outcome.status
                    terminal_reason = outcome.reason_code
                    break
                current_inputs = committed
                if unexpected:
                    break
        except CancellationRequested:
            aggregate = StageStatus.CANCELLED
            terminal_reason = "USER_CANCELLED"
        except KeyboardInterrupt:
            aggregate = StageStatus.CANCELLED
            terminal_reason = "USER_CANCELLED"
        finally:
            if active_attempt is not None and active_candidate is not None and active_scratch is not None:
                workspace.clean_attempt(active_attempt, active_candidate, active_scratch)

        result_candidates = tuple(item for item in current_inputs if item.role == spec.expected_result_role)
        if aggregate in {StageStatus.SUCCEEDED, StageStatus.REVIEW, StageStatus.FALLBACK} and len(result_candidates) != 1:
            aggregate = StageStatus.FAILED
            terminal_reason = "STAGE_CONTRACT_VIOLATION"

        manifest: dict[str, object] = {
            "format": "oneclick2d.run-manifest",
            "format_version": "0.2.0",
            "scope": "disposable-gate-f-spike",
            "run_id": run_id,
            "run_spec_sha256": spec.exact_sha256,
            "root_seed_u64": spec.root_seed_u64,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "terminal_status": aggregate.value,
            "application": {"version": __version__, "build_id": build_id, "source_revision": source_revision},
            "environment": {
                "os": platform.system(),
                "cpu": platform.machine() or "unknown",
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "execution_provider": spec.execution_provider,
                "execution_profile": spec.execution_profile,
                "precision": "not-applicable",
            },
            "source": source_ref.as_manifest(),
            "stages": records,
        }
        if terminal_reason:
            manifest["terminal_reason_code"] = terminal_reason
        if aggregate in {StageStatus.SUCCEEDED, StageStatus.REVIEW, StageStatus.FALLBACK}:
            manifest["result"] = result_candidates[0].as_manifest()
        manifest_bytes = canonical_json_bytes(manifest)
        manifest_path = workspace.write_atomic("run-manifest.json", manifest_bytes)
        workspace.write_atomic("run-manifest.sha256", (sha256_bytes(manifest_bytes) + "\n").encode("ascii"))
        return aggregate, manifest_path
