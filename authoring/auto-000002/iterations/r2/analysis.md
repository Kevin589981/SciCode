# Revision r2 analysis

## Revision status

Revision r2 is blocked/not accepted because AvaCore strict execution failed before producing a manifest or rollouts file. This is not a solver-quality failure and no candidate scientific defect is known from the r2 local gates or public-only child review.

## r1 rejection basis

r1 was treated as rejected. The strict run exported structurally complete traces, but all evaluator files failed because `required_dependencies` was the bare string `numpy`. The official SciCode evaluator prepended that string to evaluator files, producing a `NameError` for `numpy` rather than an executable import. r2 therefore regenerates the dependency declaration as an executable import declaration such as `import numpy as np`.

## r2 fixes applied

- Replaced the bare dependency string with an executable import declaration in the canonical public record.
- Regenerated the canonical public record, prompt snapshots, solver payload hashes, candidate manifest, oracle, and validation reports.
- Fixed the child-review scientific defect: the step 1 operator is now a sign-aware upwind flux, not a fixed left-neighbor difference.
  - For `a >= 0`, the flux uses the left periodic neighbor.
  - For `a < 0`, the flux uses the right periodic neighbor.
  - Public tests and public invariant checks cover both positive and negative velocities.
- Preserved the ordered subproblem dependency chain:
  - Step 2 calls or uses `upwind_flux_difference`.
  - Step 3 calls or uses `lax_wendroff_step`.
- Kept the original SciCode JSONL top-level fields and the exact solver-visible prompt protocol.
- Kept private oracle/reference files out of public prompts and solver-visible payload.

## r2 hashes and source contract

- Candidate id: `auto-000002`
- Revision: `r2`
- Canonical record hash: `bc57d915a8012b50ff362938b925cf1317bd04c41b4a3fd16252b2aa9cde6ce1`
- Visible contract hash: `fbe2fd6b678c7e3b5e3ce1b3dc0785c2d936a57967b541780fd086ebb66ef0be`
- Solver payload hash: `22d7067da603c17f899a4b191cd77d17763b02e902926966b09323ae1ae48971`
- Oracle hash: `cda442c576499e994a5adb75dd0e101a478328728a7fb840244a8547d8a294d7`
- Provenance hash: `3091b9609394441d3f4811e403b5c8538162c7d0d8064bdbcd004a934bd8ef35`
- Source: PyClaw advection example and classic solver documentation
- Source commit: `f522337ef75abef1153e2025a204b7ef4f7c5c9f`
- License: BSD-3-Clause
- DOI: `10.1137/110829270`

## Local validation

The r2 local gate command passed:

```text
python3 scripts/run_candidate_checks.py --candidate-dir authoring/auto-000002
```

`validation/checks_report.json` reports `status = ok` with schema, oracle, static, prompt, and isolation reports all passing. The oracle semantic report reports `status = ok`: the reference and independent implementations pass all steps, and the wrong implementation fails step 1 for the intended sign-aware upwind distinction.

## Public-only child review

A fresh public-only child review was recorded in `validation/review_child.json`.

- Event id: `review_child_auto-000002_r2_20260912T032006Z`
- Decision: `accepted_for_avacore_run`
- Reviewer note: accepted for AvaCore run; no public-only blockers found.

The review confirmed the sign-aware upwind flux, positive/negative velocity coverage, ordered dependency chain, prompt protocol, provenance, leakage posture, and oracle independence.

## AvaCore strict run attempts

Three strict AvaCore run directories were retained as audit evidence.

### Run 12

- Directory: `authoring/auto-000002/runs/auto-000002-r2-nex-strict-20260912-12`
- Handoff base URL: `http://10.100.184.127:5050`
- Model: `nex-agi/Nex-N2-Pro`
- Max tokens: `256000`
- Timeout: `7200`
- Log: `Set POSTGRES or pass --postgres; AvaCore PostgreSQL is the primary trace store`
- Classification: infrastructure blocker. The shell did not provide `POSTGRES`, so AvaCore stopped before solver execution and before exporting rollouts.

### Run 13

- Directory: `authoring/auto-000002/runs/auto-000002-r2-nex-strict-20260912-13`
- Handoff base URL: `https://aigw.sotatts.online/v1`
- Model: `nex-agi/Nex-N2-Pro`
- Max tokens: `256000`
- Timeout: `7200`
- Log: `ValueError: Instance 'auto-000002' already exists with different data`
- Classification: infrastructure/store conflict plus wrong base URL. The run failed in AvaCore PostgreSQL instance creation before solver execution and before exporting rollouts.

### Run 14

- Directory: `authoring/auto-000002/runs/auto-000002-r2-nex-strict-20260912-14`
- Handoff base URL: `http://10.100.184.127:5050`
- Model: `nex-agi/Nex-N2-Pro`
- Max tokens: `256000`
- Timeout: `7200`
- HTTP retries: `5`
- Concurrency: `1`
- Command includes `--score-by-subproblem`
- Log: `Set POSTGRES or pass --postgres; AvaCore PostgreSQL is the primary trace store`
- Classification: infrastructure blocker. The shell still did not provide `POSTGRES`, so AvaCore stopped before solver execution and before exporting rollouts.

No r2 run produced `rollouts.jsonl`, `manifest.json`, evaluator scores, finish reasons, ordinary content, reasoning content, code extraction, or token usage to audit.

## Failure separation

### Candidate errors

- r1 dependency error: fixed in r2 by using an executable import declaration.
- r1 scientific flux defect: fixed in r2 by implementing sign-aware upwind behavior and tests for both velocity signs.
- r2 local schema, static, oracle, prompt, leakage, and isolation gates pass.
- r2 public-only child review found no blocking issues.

No remaining candidate error is known from the completed r2 local gates or public-only review.

### Solver errors

No solver trace was available for r2. Solver quality, scientific reasoning, code extraction, finish reason, evaluator score, and token usage cannot be audited because AvaCore failed before solver execution.

### Infrastructure errors

- Missing `POSTGRES` in runs 12 and 14.
- Run 13 used the wrong base URL and then hit an AvaCore PostgreSQL instance conflict.

## Release decision

r2 is not accepted or promotable. The release gate is blocked by missing AvaCore trace evidence, not by a known candidate scientific defect. Failed run directories are retained as audit evidence and must not be deleted.
