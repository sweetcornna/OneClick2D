"""OneClick2D phase-1 production package.

This package implements the authoritative product path defined by
``docs/ARCHITECTURE.md`` and ``docs/CIR_SPEC.md``: isolated intake, suitability
policy, semantic decomposition, bounded completion, layer synthesis,
deterministic mesh and minimal rig, whole-project validation, preview
compilation, dual ``.oc2d`` + layered PSD export and independent re-verification.

It depends on the Python standard library only. Nothing here imports
``spikes/``; the Gate F spike runner remains a disposable research path.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
