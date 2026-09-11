---
name: scicode-delivery
description: Validate, merge, and aggregate complete strict SciCode subproblem traces into the 10k SFT dataset.
---

# SciCode Delivery Skill

Use this skill only after the candidate release gate passes.

## Validate and lock

1. Re-run schema, static, oracle, provenance, leakage, prompt, and trace
   checks. Reject incomplete or privately mounted runs.
2. Acquire the delivery lock under the configured workspace root.
3. Read the registry before writing. Deduplicate by candidate revision,
   problem id, step number, and trace hash.

## Aggregate

Run:

```bash
python scripts/export_subproblem_samples.py \
  --candidate-dir "$CANDIDATE_DIR" \
  --rollouts "$CANDIDATE_DIR/runs/$RUN_ID/rollouts.jsonl" \
  --registry "$DELIVERY_ROOT/registry.jsonl" \
  --output "$DELIVERY_ROOT/dataset.jsonl" \
  --summary "$DELIVERY_ROOT/summary.json" \
  --target-count 10000
```

Emit one SFT-compatible record for each complete non-skipped subproblem.
Preserve `messages`, `completion`, `reasoning_content`,
`completion_with_reasoning`, `parsed_code`, `context_code`,
`provider_response`, `usage`, and `metadata`. Preserve candidate/source/run
provenance inside `metadata`. Do not expose private oracle or reference code.

Stop exactly at 10,000 accepted records. Put additional valid records in an
overflow file and retain rejected candidates separately.

## Merge and cleanup

1. Commit the candidate branch.
2. Merge it into `INTEGRATION_BRANCH` with the merge helper. Do not modify
   another active worktree. If a conflict remains, record the candidate id and
   leave its branch and worktree intact.
3. Write the summary table and release decision.
4. Remove only the worktree copy after a successful merge or rejection. Keep
   the branch and Git history. Do not push automatically.
