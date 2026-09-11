# Automated authoring and evaluation

The repository has two connected layers:

1. `AGENTS.md` defines the Kimi Code authoring loop. Kimi Code creates a
   candidate, runs the schema/dependency/leakage checks, starts clean Strict or
   Agentic solver attempts, reads the private verifier report, and either
   revises the candidate or records an accepted revision.
2. `scicode_avacore.py` is the execution boundary. It runs the unchanged
   SciCode sequential prompt/evaluator protocol, stores every rollout in
   AvaCore PostgreSQL, and exports the same run as JSONL for AvaVisualizer or
   downstream analysis.

## Candidate handoff

An accepted candidate must contain at least:

```text
candidate/
  public/problem.jsonl
  oracle/targets.h5
  validation/release_decision.md
```

The public JSONL is an official SciCode-shaped record. Its evaluator fields
stay private to the scoring process; the solver workspace is materialized from
the redacted payload described in `AGENTS.md`.

Run a candidate through AvaCore with:

```bash
python eval/avacore/scicode_avacore.py \
  --problem-file candidate/public/problem.jsonl \
  --h5py-file candidate/oracle/targets.h5 \
  --output candidate/runs/avacore-final \
  --export candidate/runs/avacore-final/rollouts.jsonl \
  --postgres "$POSTGRES" \
  --base-url "$BASE_URL" \
  --model "$MODEL"
```

The runner validates the local record before making any provider request. It
then uses the same ordered subproblem prompts, previous-code handoff, Python
code extraction, HDF5 assertions, retry/timeout settings, PostgreSQL trace
store, and JSONL exporter as an official run. `--problem-id` and `--limit` may
restrict a local JSONL during debugging.

## Reusing a quality trace

The quality run may also be the formal run. Reuse it only when it is complete
and its recorded configuration matches the release candidate exactly:

- same candidate revision, problem JSONL, oracle, mode, prompt profile, model,
  sampling parameters, and evaluator settings;
- every non-skipped subproblem ran and the private verifier passed;
- the AvaCore database rollout is complete and `rollouts.jsonl` was exported;
- no private files were visible to the solver and no debugging limit was used.

Otherwise retain the trace as QA evidence and launch a separate formal run.
This distinction keeps Kimi Code's authoring evidence and the published
evaluation score auditable without requiring a wasteful second model call when
the first run already satisfies the formal contract.
