# Gate F local technical-preflight bundle

- **Origin/rights:** all media and outcome rows are deterministic repository-authored fixtures with no third-party source.
- **Owner:** OneClick2D maintainers.
- **Allowed use:** repository, CI, documentation, and local disposable preflight verification.
- **Generator identity:** `spikes.gate_f_runner.local_preflight`; every generated artifact is bound by name, byte length, and SHA-256 in `bundle-index.json`.
- **Trademark/likeness:** none.
- **Takedown:** repository maintainers may remove the generated workspace bundle or change its generator through normal review.

Run:

```bash
python -m spikes.gate_f_runner preflight --run-id run.local-technical
python -m spikes.gate_f_runner verify-bundle --bundle workspaces/gate-f-spike/run.local-technical.bundle
```

The command creates one purpose-created normalized raster, runs the deterministic candidate and fixed comparator through their shared 37-frame sequence and renderer, evaluates a fabricated 20-row statistics fixture, roundtrips a purpose-created layered PSD through the independent strict reader, and writes a checksummed evidence directory.

A successful report says `LOCAL_TECHNICAL_PREFLIGHT_PASS` and always says `GATE_F_NOT_EVALUATED`. It cannot establish real artwork quality, real reviewer preference, PSD editor interoperability, ICC compliance, Gate F feasibility, or product readiness.
