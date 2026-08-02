# Gate F deterministic candidate baseline preflight

- **Origin:** configuration and raster fixtures are purpose-created in this repository.
- **Owner:** OneClick2D maintainers.
- **Rights basis:** repository-authored deterministic geometric fixtures with no third-party source.
- **Allowed use:** repository, CI, documentation, and local disposable Gate F candidate-contract tests.
- **Content:** fixed normalized source-pixel regions and programmatically generated asymmetric RGBA grids; no character, artwork, face, camera data, model, PSD, or customer content.
- **Generator identity:** `tests/test_gate_f_candidate_baseline.py` and `spikes.gate_f_runner.local_preflight`; output digests are recorded in run manifests and bundle indexes.
- **Generator:** Python standard library plus the locked Pillow 12.1.0 disposable spike dependency.
- **Trademark/likeness:** none.
- **Takedown:** repository maintainers may remove or replace it through normal review.

The current v0.2 config records the shared premultiplied-alpha renderer profile used by both arms; fixed regions, suitability, geometry, parameters, and sequence remain unchanged.

This deterministic rule baseline exercises automatic suitability, required-slot proposal, fixed layer extraction, quad geometry, mandatory parameter bindings, full shared trajectory validation, and rendering through the same Pillow renderer used by the comparator. It does not claim semantic correctness on real artwork and is not a learned candidate or Gate F result.
