---
name: scicode-task-authoring
description: Create and quality-gate one scientifically difficult strict SciCode candidate.
---

# SciCode Task Authoring Skill

Use this skill for every candidate revision. Follow the commands in
`AGENTS.md`; this skill exposes the short operational sequence.

## Create

1. Research an external scientific source and pin its URL, commit, license,
   paper, retrieved files, and source fingerprint in
   `source_notes/provenance.json`.
2. Write the canonical SciCode JSONL record and ordered subproblems. Preserve
   the upstream model-visible prompt fields and do not use official SciCode
   records or `test_data.h5`.
3. Write private reference, independent, and wrong implementations. Generate
   the candidate HDF5 oracle from the reference and record its hash.
4. Derive the redacted solver payload and public checks from an allowlist.
5. Render the fixed strict prompt snapshots.

## Gate

Run:

```bash
python scripts/validate_candidate.py \
  --candidate-dir authoring/$CANDIDATE_ID \
  --write-report authoring/$CANDIDATE_ID/validation/schema_report.json
```

Start a fresh Kimi Code child agent after local validation. Give it only the
public payload, public source notes, and the validation contract. Require a
structured review covering scientific semantics, units/shapes, dependency
chain, difficulty, leakage, provenance, and oracle independence. Save its
request, response, decision, and session/event id in
`validation/review_child.json`.

Reject or revise when any gate fails. Increment the revision and regenerate
all derived files after each substantive change. Do not make wording-only
changes without evidence.

## Handoff

After review passes, invoke `scicode-avacore-run`. Do not call the Kimi API from
this skill. Resume the Kimi Code session only after the skill returns the run
manifest and trace path. Analyze every subproblem, classify failures, and then
invoke this skill again for a revision or invoke `scicode-delivery`.
