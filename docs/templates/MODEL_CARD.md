# Model Card: `<immutable model ID>`

- **Status:** experimental / candidate / approved / retired
- **Owner:**
- **Task:**
- **Weights SHA-256:**
- **Model format/runtime:**
- **Source and license record:**
- **Code/config revision:**

## Intended use and exclusions

State the exact pipeline stage, supported input profile, outputs, and uses that were not evaluated or are prohibited. A model card cannot widen the product scope in `PROJECT_CHARTER.md`.

## Inputs and outputs

- Tensor layout, dtype/range, color/alpha/coordinate conventions.
- Preprocessing and postprocessing implementation/config version.
- Output shape/range/finite checks and semantic mapping.

## Training and data provenance

- Dataset manifest versions and allowed-use confirmation.
- Train/calibration split digests and group/duplicate controls.
- Pretraining/fine-tuning sources, augmentation versions, and seeds.
- Known data gaps, annotation uncertainty, and takedown/update process.

## Evaluation

Include immutable evaluation/acceptance dataset versions, sample counts, 95% confidence intervals, aggregate and required slice tables, calibration/risk-coverage, human protocol where used, comparison to the current approved model, and representative failure taxonomy. Link results; do not paste private samples.

## Runtime and reproducibility

| Profile | Provider/precision | p50/p95 time | Peak RAM | Peak VRAM | Determinism/tolerance |
|---|---|---:|---:|---:|---|
| gpu-reference | | | | | |
| gpu-lowmem | | | | | |
| cpu-safe | | | | | |

Record OS, GPU/driver, runtime versions, deterministic flags, known nondeterministic operators, seed derivation, and artifact/config digests.

## Confidence, validation, and fallback

Define confidence semantics and calibration version, auto/review/fallback/block thresholds by required part/slice, all post-inference validation, and the exact fallback/downshift behavior. `unavailable` confidence is not high confidence.

## Limitations and impact

List concrete failure modes by pose/style/crop/part/complexity, authored-pixel or identity risks, privacy/security considerations, and user-visible mitigations. Do not infer identity, demographics, or emotion.

## Promotion decision

- Release gates met/missed:
- Regressions/trade-offs accepted by:
- Approved artifact alias and rollback version:
- Approval date / next review or retirement trigger:
