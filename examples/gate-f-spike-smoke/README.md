# Gate F runner synthetic smoke fixture

- **Origin:** purpose-created in this repository; no external source or reference.
- **Owner:** OneClick2D maintainers.
- **Allowed use:** repository, CI, documentation and local orchestration tests.
- **Content:** a 4×4 numeric checkerboard; no character, artwork, face, camera data, model, PSD or customer content.
- **Generator:** manually authored fixture format `oneclick2d.synthetic-grid` v0.1.0.
- **Trademark/likeness:** none.
- **Takedown:** repository maintainers may remove or replace it through normal review.

SHA-256:

| File | SHA-256 |
|---|---|
| `source.synthetic.json` | `744ddd21e71e156588045c3b9a3682b2978b73fcb91a51785906b5f75f15e944` |
| `configs/normalize.json` | `9fa6b9061b40b870b0076d90dfa4761b43307df58ea2a1fed0623fccb13ede9f` |
| `configs/proposal.json` | `46bf647c68a354d9435a290840491a51387f6b249b75d2d5f1b4582ea3fd57d7` |
| `configs/verify.json` | `25468559b6990a70c59535d1b62498ce9a21eac3fc09c62c1d581e06f032ca1d` |

A passing run proves only that immutable synthetic bytes can move through the disposable local runner and produce a typed manifest. It does **not** prove image validation, semantic decomposition, hidden-region completion, mesh/rig quality, rendering, PSD interoperability or Gate F feasibility.
