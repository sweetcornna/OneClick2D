export const meta = {
  name: 'audit-gate-f-continuation',
  description: 'Audit unfinished Gate F implementation before repairs',
  phases: [
    { title: 'Audit', detail: 'inspect runtime, candidate, and contract dimensions' },
    { title: 'Synthesize', detail: 'deduplicate findings and prioritize repairs' },
  ],
}
const FINDINGS = {
  type: 'object',
  properties: {
    dimension: { type: 'string' },
    summary: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          file: { type: 'string' },
          line: { type: 'integer' },
          issue: { type: 'string' },
          evidence: { type: 'string' },
          fix: { type: 'string' },
          test: { type: 'string' },
          confidence: { type: 'number' },
        },
        required: ['severity', 'file', 'line', 'issue', 'evidence', 'fix', 'test', 'confidence'],
        additionalProperties: false,
      },
    },
  },
  required: ['dimension', 'summary', 'findings'],
  additionalProperties: false,
}
const dimensions = [
  {
    key: 'runtime-v4-motion',
    prompt: `Read-only audit in /mnt/d/project/live2d. The original user asked to continue the current uncommitted Gate F work, specifically the isolated See-through V3 NF4 CPU-offload wrapper, source-preserve v4 neutral-fidelity processing, and model motion research draft. Do not edit files, delete artifacts, commit, or push. Inspect the current git diff plus relevant tracked and untracked source/tests/config. Trace actual execution across model_worker.py, model entrypoints, model_workbench.py, model_motion_draft.py, runtime/rendering/acceptance, CLI and tests. Look for reproducible correctness bugs, device/dtype mistakes, claim-boundary violations (model_used must stay false before full success; only LOCAL_* and GATE_F_NOT_EVALUATED), nondeterminism, path/hash validation gaps, compatibility regressions, or tests that assert the wrong behavior. Ignore suspicious root files named -s, xaa, converted.png. Report only actionable findings with exact current lines and evidence; if clean, findings may be empty.`,
  },
  {
    key: 'candidate-independent-verifier',
    prompt: `Read-only audit in /mnt/d/project/live2d. The original user asked to finish the current uncommitted model-candidate and verify-model-candidate preflight implementation without expanding it into Gate F scoring. Do not edit, commit, push, or touch root files -s, xaa, converted.png. Inspect model_candidate.py, __main__.py, candidate/comparator integrations, schemas/gate-f-model-candidate, schema version changes, examples, and tests. Prove whether the verifier really independently recomputes all security- and integrity-relevant claims rather than trusting the producer report. Check artifact inventory/hash/path containment, duplicate/extra artifacts, run/profile/motion binding, ontology/provenance/arm parity, floating-point handling, status/claim boundaries, config/schema alignment, and negative tests. Report only actionable, reproducible findings with exact current lines and a focused repair/test.`,
  },
  {
    key: 'contracts-cli-regression',
    prompt: `Read-only audit in /mnt/d/project/live2d. Review the full current uncommitted diff as a continuation of the Gate F disposable spike. Do not edit, commit, push, or touch root files -s, xaa, converted.png. Focus on repository contract coherence: CLAUDE.md hard boundaries, README/CONTRIBUTING/docs/index/FEASIBILITY/MODEL_MOTION_DRAFT, examples, config/report JSON schemas, validate_docs.py, CLI dispatch and existing smoke/preflight/gui/model/motion behavior. Identify schema-before-producer violations, stale version references, docs claiming more than code proves, Windows/WSL path issues, accidental large formatting-only churn, backward compatibility failures explicitly required by CLAUDE.md, and missing focused regression tests. Run lightweight read-only checks if useful. Report exact actionable findings only.`,
  },
]
phase('Audit')
const audits = (await parallel(dimensions.map(d => () => agent(d.prompt, {
  label: `audit:${d.key}`,
  phase: 'Audit',
  schema: FINDINGS,
})))).filter(Boolean)
phase('Synthesize')
const synthesis = await agent(`You are the lead reviewer for a continuation of uncommitted Gate F spike work in /mnt/d/project/live2d. Do not edit files. Re-read source as needed. Adversarially validate and deduplicate the three audit reports below. Reject speculative findings lacking a concrete execution path. Return a concise repair order: blockers first, then medium/low improvements only when required by the original scope or repository hard boundaries. Include an explicit baseline test sequence. Original scope: finish isolated See-through V3 NF4 offload wrapper, source-preserve v4, research-only motion draft, model-candidate preflight, and an independent verifier; no commit/push; do not touch suspicious root files -s, xaa, converted.png; never claim Gate F evaluated, ballot, F-USABLE, oc2d, moc3, or professional binding. Audit reports:\n${JSON.stringify(audits)}`, {
  label: 'synthesize:audit',
  phase: 'Synthesize',
  schema: {
    type: 'object',
    properties: {
      verdict: { type: 'string', enum: ['repair-required', 'ready-for-tests'] },
      repairs: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            priority: { type: 'integer' },
            severity: { type: 'string' },
            file: { type: 'string' },
            line: { type: 'integer' },
            issue: { type: 'string' },
            evidence: { type: 'string' },
            repair: { type: 'string' },
            regressionTest: { type: 'string' },
          },
          required: ['priority', 'severity', 'file', 'line', 'issue', 'evidence', 'repair', 'regressionTest'],
          additionalProperties: false,
        },
      },
      baselineCommands: { type: 'array', items: { type: 'string' } },
      cautions: { type: 'array', items: { type: 'string' } },
    },
    required: ['verdict', 'repairs', 'baselineCommands', 'cautions'],
    additionalProperties: false,
  },
})
return { audits, synthesis }