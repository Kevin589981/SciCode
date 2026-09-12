# Revision r2 analysis

## Revision status

Revision r2 is accepted for the strict AvaCore run. The first three handoffs were retained as infrastructure-failure evidence; run 15 completed all three subproblems and produced an auditable trace. The solver passed 1/3 subproblems (0/1 whole problem), which is a solver result rather than a candidate or evaluator failure.

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
- Solver payload hash: `2dbe7bc7a026c8222e9d1f28dd64b9020600f123c53f6a475fda18fe367c376d`
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

The public payload was subsequently normalized from an accidental nested directory to the documented direct `public/solver_payload/` layout. File contents and the visible problem contract were unchanged; the new solver-payload hash is recorded above.

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

### Run 15 (successful strict evidence)

- Directory: `authoring/auto-000002/runs/auto-000002-r2-nex-strict-20260912-15`
- Base URL: `https://aigw.sotatts.online/v1`
- Model: `nex-agi/Nex-N2-Pro`
- Requested max tokens: `262144`; effective Nex cap: `256000`
- Timeout: `7200`; HTTP retries: `5`; concurrency: `1`
- Database query key: `auto-000002::r2::bc57d915a8012b50::auto-000002` (the solver-visible instance id remains `auto-000002`)
- AvaCore status: `finished`, errors `0`, complete `3/3` subproblems, trace complete and promotable
- Reward: subproblem correctness `1/3 = 0.3333333333333333`; whole-problem correctness `0`
- Evaluator logs: one subproblem passed and two failed; no parser, dependency, or `NameError` failure occurred
- Every assistant message contains ordinary code content, reasoning content, `finish_reason=stop`, provider response metadata, and usage
- Token totals: prompt `2787`, completion `7867`, reasoning `7202`, total `10654`
- Trace audit: `validation/trace_report.json` status `ok`, with all three subproblem traces present and usage available

The downstream delivery converter was also run against this trace with a target of three records. It expanded the one rollout into three complete subproblem samples, accepted all three, wrote the standard fields (`messages`, `completion`, `reasoning_content`, `completion_with_reasoning`, `parsed_code`, `context_code`, `provider_response`, `usage`, and metadata), and reached the target without overflow. The structured result is saved as `validation/delivery_smoke.json`.

The model answer is intentionally retained as QA evidence even though it is not fully correct; candidate release depends on a valid problem/oracle and a complete trace, not on requiring the solver to pass every subproblem.

## Failure separation

### Candidate errors

- r1 dependency error: fixed in r2 by using an executable import declaration.
- r1 scientific flux defect: fixed in r2 by implementing sign-aware upwind behavior and tests for both velocity signs.
- r2 local schema, static, oracle, prompt, leakage, and isolation gates pass.
- r2 public-only child review found no blocking issues.

No remaining candidate error is known from the completed r2 local gates or public-only review.

### Solver errors

Run 15 has a complete trace. Its evaluator result is `1/3` subproblems correct; the two failed subproblems are ordinary solver failures. The trace is still valid for quality review because all content, reasoning, finish reasons, provider responses, and usage fields are present.

### Infrastructure errors

- Missing `POSTGRES` in runs 12 and 14.
- Run 13 used the wrong base URL and then hit an AvaCore PostgreSQL instance conflict.

## Release decision

r2 is accepted and promotable after the successful run 15 and all local gates. Failed run directories 12--14 remain as audit evidence and must not be deleted. The candidate is ready for downstream delivery only after the delivery skill re-runs its final checks and performs deduplicated subproblem export.
