# Gate F simple-cutout comparator preflight

- **Origin:** comparator configuration and raster fixtures are purpose-created in this repository; no external source or reference.
- **Owner:** OneClick2D maintainers.
- **Rights basis:** repository-authored deterministic geometric fixtures with no third-party source.
- **Allowed use:** repository, CI, documentation, and local disposable comparator tests.
- **Content:** a fixed 12-frame mandatory prefix, a 25-frame deterministic seeded trajectory, and programmatically generated asymmetric RGBA color grids; no character, artwork, face, camera data, model, PSD, or customer content.
- **Generator identity:** `tests/test_gate_f_simple_cutout.py` and `spikes/gate_f_runner.local_preflight`; output digests are recorded in run manifests and bundle indexes.
- **Generator:** Python standard-library fixture construction plus the locked Pillow 12.1.0 disposable spike dependency.
- **Trademark/likeness:** none.
- **Takedown:** repository maintainers may remove or replace it through normal review.

The comparator copies four fixed patches from the normalized raster, applies the preregistered head/eye/mouth transforms, and renders 37 PNG frames containing only the required sRGB color declaration and no EXIF, ICC, text, comment, DPI, XMP, or other ancillary metadata. The standard-library shared sequence contains the 12 neutral/endpoint/combination frames plus a 25-frame fixed-point trajectory derived from explicit seed `00000000000000000042`; a future candidate must consume the same sequence digest.

The current v0.3 config records the shared premultiplied-alpha renderer profile used by both arms; patch geometry, the 2 source-pixel feather, parameter rules, and sequence remain unchanged.

Successful output proves only the fixed low-complexity comparator implementation under its locked local profile. It does not prove candidate renderer parity, semantic decomposition, hidden-region completion, PSD interoperability, Gate F feasibility, or production readiness.
