"""Stage implementations for the phase-1 DAG (``docs/ARCHITECTURE.md`` §7).

``ML proposes; deterministic code constrains``: a model may estimate
suitability, masks, landmarks, depth and bounded hidden pixels, while
deterministic code owns policy, ontology, sides, topology, parameter capability,
ranges, interpolation, validation, rendering, fallback and release.
"""

from __future__ import annotations

from .intake import DimensionEnvelope, NormalizedInput, normalize_upload
from .suitability import SuitabilityReport, evaluate_suitability

__all__ = [
    "DimensionEnvelope",
    "NormalizedInput",
    "SuitabilityReport",
    "evaluate_suitability",
    "normalize_upload",
]
