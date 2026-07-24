"""Purpose-created adapters that test orchestration only."""

from __future__ import annotations

import hashlib
from typing import Any

from .contracts import Determinism, ProducerKind, StageContext, StageOutcome, StageStatus
from .runtime import canonical_json_bytes, strict_load_json_bytes
from .runner import AdapterRegistry


class _SyntheticAdapter:
    producer_kind = ProducerKind.DETERMINISTIC
    determinism = Determinism.BYTE_EXACT
    implementation_version = "0.1.0"
    execution_profile = "python-stdlib-in-process-v1"
    execution_provider = "python-stdlib"

    @staticmethod
    def _one_input(context: StageContext) -> Any:
        context.cancellation.checkpoint()
        if len(context.spec.input_artifacts) != 1:
            raise ValueError("synthetic stages require exactly one input")
        return strict_load_json_bytes(context.spec.input_artifacts[0].path.read_bytes())

    @staticmethod
    def _config(context: StageContext) -> dict[str, Any]:
        value = strict_load_json_bytes(context.spec.config_bytes)
        if not isinstance(value, dict):
            raise ValueError("synthetic configuration must be an object")
        return value


class SyntheticNormalizeAdapter(_SyntheticAdapter):
    adapter_id = "synthetic.normalize.v1"
    contract_id = "oc2d.spike.synthetic-normalize.v1"
    stage_type = "oc2d.spike.synthetic-normalize"

    def execute(self, context: StageContext) -> StageOutcome:
        source = self._one_input(context)
        config = self._config(context)
        if not isinstance(source, dict) or source.get("format") != "oneclick2d.synthetic-grid":
            raise ValueError("unexpected synthetic source")
        width, height, cells = source.get("width"), source.get("height"), source.get("cells")
        if not isinstance(width, int) or not isinstance(height, int) or not isinstance(cells, list) or len(cells) != width * height:
            raise ValueError("invalid synthetic grid")
        if any(cell not in {0, 1} for cell in cells):
            raise ValueError("synthetic cells must be binary")
        if config.get("invert") is True:
            cells = [1 - cell for cell in cells]
        result = {
            "format": "oneclick2d.synthetic-normalized",
            "format_version": "0.1.0",
            "width": width,
            "height": height,
            "cells": cells,
            "source_sha256": context.spec.input_artifacts[0].sha256,
        }
        artifact = context.sink.write_bytes(
            "normalized.json",
            canonical_json_bytes(result),
            role="normalized_synthetic",
            media_type="application/vnd.oneclick2d.synthetic-normalized+json",
        )
        return StageOutcome(StageStatus.SUCCEEDED, outputs=(artifact,))


class SyntheticProposalAdapter(_SyntheticAdapter):
    adapter_id = "synthetic.proposal.v1"
    contract_id = "oc2d.spike.synthetic-proposal.v1"
    stage_type = "oc2d.spike.synthetic-proposal"

    def execute(self, context: StageContext) -> StageOutcome:
        source = self._one_input(context)
        config = self._config(context)
        count = config.get("proposal_count")
        if not isinstance(source, dict) or source.get("format") != "oneclick2d.synthetic-normalized":
            raise ValueError("unexpected normalized input")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 64:
            raise ValueError("invalid proposal count")
        values: list[int] = []
        seed = int(context.spec.seed_u64).to_bytes(8, "big")
        for index in range(count):
            context.cancellation.checkpoint()
            digest = hashlib.sha256(b"oneclick2d.synthetic-proposal.v1\0" + seed + index.to_bytes(4, "big")).digest()
            values.append(int.from_bytes(digest[:4], "big") % 1000)
        result = {
            "format": "oneclick2d.synthetic-proposal",
            "format_version": "0.1.0",
            "input_sha256": context.spec.input_artifacts[0].sha256,
            "seed_u64": context.spec.seed_u64,
            "values": values,
        }
        artifact = context.sink.write_bytes(
            "proposal.json",
            canonical_json_bytes(result),
            role="synthetic_proposal",
            media_type="application/vnd.oneclick2d.synthetic-proposal+json",
        )
        return StageOutcome(StageStatus.SUCCEEDED, outputs=(artifact,))


class SyntheticVerifyAdapter(_SyntheticAdapter):
    adapter_id = "synthetic.verify.v1"
    contract_id = "oc2d.spike.synthetic-verify.v1"
    stage_type = "oc2d.spike.synthetic-verify"

    def execute(self, context: StageContext) -> StageOutcome:
        proposal = self._one_input(context)
        config = self._config(context)
        required = config.get("required_proposal_count")
        if not isinstance(proposal, dict) or proposal.get("format") != "oneclick2d.synthetic-proposal":
            raise ValueError("unexpected proposal input")
        values = proposal.get("values")
        if not isinstance(required, int) or not isinstance(values, list) or len(values) != required:
            raise ValueError("synthetic proposal verification failed")
        result = {
            "format": "oneclick2d.synthetic-spike-result",
            "format_version": "0.1.0",
            "verified_input_sha256": context.spec.input_artifacts[0].sha256,
            "proposal_count": len(values),
            "proposal_checksum": sum(values),
            "orchestration_only": True,
        }
        artifact = context.sink.write_bytes(
            "spike-result.json",
            canonical_json_bytes(result),
            role="spike_result",
            media_type="application/vnd.oneclick2d.synthetic-spike-result+json",
        )
        return StageOutcome(StageStatus.SUCCEEDED, outputs=(artifact,))


def build_synthetic_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(SyntheticNormalizeAdapter())
    registry.register(SyntheticProposalAdapter())
    registry.register(SyntheticVerifyAdapter())
    return registry
