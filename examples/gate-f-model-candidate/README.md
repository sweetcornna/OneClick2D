# Gate F single-item model candidate preflight

This frozen configuration adapts one already validated active `source-preserve.v4` model run and its validated motion draft into a deterministic, independently reloadable candidate/comparator parity preflight.

The adapter maps all 16 ontology registry slots, records character-anatomical side, recomputes and byte-verifies every motion layer and all 37 candidate frames, normalizes the same model source for the fixed simple-cutout comparator, and requires exact arm identity parity. Its published semantic-union mask is partitioned into disjoint source-visible and source-transparent-exposed masks using the active v4 rule: source alpha greater than 31 is source-visible, while source alpha from 0 through 31 is source-transparent-exposed where a cleaned semantic layer is present. Runtime artifacts remain under the ignored local workspace and are never committed by this example.

Run only after `model` and `motion` have completed for a rights-cleared local input:

```bash
python -m spikes.gate_f_runner model-candidate --run-id run.local-model
```

Independently revalidate the published directory with:

```bash
python -m spikes.gate_f_runner verify-model-candidate --run-id run.local-model
```

Success is limited to `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` and `GATE_F_NOT_EVALUATED`. It creates no ballots, paired outcomes, `F-USABLE` classification, `.oc2d`, or `.moc3`; it does not establish semantic correctness, hidden-region truth, professional binding, external PSD-editor interoperability, production readiness, or Gate F feasibility. The candidate remains `review_required`, and supporting model weight metadata remains incomplete, prohibiting redistribution and product use.
