# Gate F raster-normalization experiment fixture

- **Origin:** purpose-created by `tests/test_gate_f_raster_adapter.py`; no external source or reference.
- **Owner:** OneClick2D maintainers.
- **Allowed use:** repository, CI, documentation and local disposable raster-adapter tests.
- **Content:** a programmatically generated 2×2 RGBA color grid; no character, artwork, face, camera data, model, PSD or customer content.
- **Generator:** standard-library PNG chunk construction; fixed pixels and zlib level 9.
- **Trademark/likeness:** none.
- **Takedown:** repository maintainers may remove or replace it through normal review.

This fixture is generated in a temporary directory and is not committed as a binary. The Adapter output proves only defensive local decode/normalization behavior under its declared profile. It does not prove semantic decomposition, hidden-region completion, mesh/rig quality, rendering, PSD interoperability or Gate F feasibility.

## Locked spike dependency

`spikes/gate_f_runner/requirements-pillow-12.1.0-win-py314.txt` pins the CPython 3.14 Windows x86-64 Pillow 12.1.0 wheel and SHA-256. It is a disposable local spike dependency, not a production runtime decision. The standard-library synthetic smoke remains usable without Pillow.
