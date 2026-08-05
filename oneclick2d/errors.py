"""Typed errors and bounded reason codes for the production path.

Every failure the product surfaces carries a stable reason code drawn from
``registries/reason-codes.yaml``. Errors never carry pixels, file names, paths,
URLs, precise content hashes or free text, matching the logging boundary in
``docs/PRIVACY_SECURITY.md`` and ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Final


class OneClick2DError(Exception):
    """Base class for every typed production failure."""

    reason_code: Final[str] = "STAGE_INTERNAL_ERROR"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        if reason_code is not None:
            object.__setattr__(self, "reason_code", reason_code)


class StrictJsonError(OneClick2DError):
    """Input JSON violated the strict interoperable profile."""

    reason_code = "STAGE_CONTRACT_VIOLATION"


class ContractError(OneClick2DError):
    """A declared contract, schema or ABI was violated."""

    reason_code = "STAGE_CONTRACT_VIOLATION"


class ResourceLimitError(OneClick2DError):
    """A declared resource bound was exceeded; the stage fails closed."""

    reason_code = "STAGE_RESOURCE_LIMIT_EXCEEDED"


class IntakeRejected(OneClick2DError):
    """Isolated intake rejected the upload before any decoding side effect."""

    reason_code = "INPUT_UNSUPPORTED"


class SuitabilityBlocked(OneClick2DError):
    """Deterministic suitability policy blocked the run."""

    reason_code = "INPUT_UNSUPPORTED"


class ValidationBlocked(OneClick2DError):
    """Whole-project validation produced a blocking finding."""

    reason_code = "EXPORT_VERIFICATION_FAILED"


class ExportVerificationFailed(OneClick2DError):
    """An independent reader could not re-verify a published artifact."""

    reason_code = "EXPORT_VERIFICATION_FAILED"


class CancellationRequested(OneClick2DError):
    """Cooperative cancellation was observed at a stage checkpoint."""

    reason_code = "USER_CANCELLED"
