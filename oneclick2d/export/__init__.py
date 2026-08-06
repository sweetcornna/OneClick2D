"""Dual-output export and independent re-verification.

``.oc2d`` and layered PSD are deterministic read-only projections of one
validated CIR revision. Neither may write back to the CIR
(``docs/ARCHITECTURE.md`` §4), and both must pass independent re-verification
bound to the same project payload digest before anything is downloadable
(FR-017, ``docs/PACKAGE_CONFORMANCE.md`` §4).
"""

from __future__ import annotations

from .oc2d import OpenedPackage, build_package, open_package
from .psd import ParsedPsd, PsdLayer, parse_layered_psd, write_layered_psd
from .release import DualOutputRelease, publish_dual_output, project_to_psd_layers

__all__ = [
    "DualOutputRelease",
    "OpenedPackage",
    "ParsedPsd",
    "PsdLayer",
    "build_package",
    "open_package",
    "parse_layered_psd",
    "project_to_psd_layers",
    "publish_dual_output",
    "write_layered_psd",
]
