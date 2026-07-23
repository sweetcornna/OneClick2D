# Gate F simple-cutout comparator preflight

- **Origin:** comparator configuration and raster fixtures are purpose-created in this repository; no external source or reference.
- **Owner:** OneClick2D maintainers.
- **Allowed use:** repository, CI, documentation, and local disposable comparator tests.
- **Content:** a fixed 12-frame parameter sequence and programmatically generated asymmetric RGBA color grids; no character, artwork, face, camera data, model, PSD, or customer content.
- **Generator:** Python standard-library fixture construction plus the locked Pillow 12.1.0 disposable spike dependency.
- **Trademark/likeness:** none.
- **Takedown:** repository maintainers may remove or replace it through normal review.

The comparator copies four fixed patches from the normalized raster, applies the preregistered head/eye/mouth transforms, and renders metadata-free PNG frames. This 12-frame sequence covers neutral, endpoints, and required combinations for implementation preflight; it deliberately omits the later seeded scoring trajectory.

Successful output proves only the fixed low-complexity comparator implementation under its locked local profile. It does not prove candidate renderer parity, semantic decomposition, hidden-region completion, PSD interoperability, Gate F feasibility, or production readiness.
