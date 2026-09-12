---
name: scicode-task-authoring
description: Create and quality-gate one scientifically difficult strict SciCode candidate.
---

# SciCode Task Authoring Skill

Use this skill for every candidate revision. Follow the commands in
`AGENTS.md`; this skill exposes the short operational sequence.

Run repository scripts with the explicit `SCICODE_PYTHON` interpreter prepared
by the execution environment. When no supplied interpreter is available, set
`UV_BIN="${UV_BIN:-/root/.local/bin/uv}"`, run `"$UV_BIN" venv --python 3.12
.venv` and `"$UV_BIN" pip install --python .venv/bin/python -e .`, then set
`SCICODE_PYTHON="$PWD/.venv/bin/python"`.

## Create

1. Research an external scientific source and pin its URL, commit, license,
   paper, retrieved files, and source fingerprint in
   `source_notes/provenance.json`.
2. Write the canonical SciCode JSONL record and ordered subproblems. Preserve
   the upstream model-visible prompt fields and do not use official SciCode
   records or `test_data.h5`.
3. Write private reference, independent, and wrong implementations. Run
   `"$SCICODE_PYTHON" authoring/$CANDIDATE_ID/oracle/generate_targets.py` to generate the
   candidate HDF5 oracle when it is absent, then record its hash. Keep the HDF5
   file private and never use the official `test_data.h5`.
4. Derive the redacted solver payload and public checks from an allowlist.
5. Render the fixed strict prompt snapshots.

## Gate

Run:

```bash
"$SCICODE_PYTHON" scripts/validate_candidate.py \
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
