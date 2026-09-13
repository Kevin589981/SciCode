# Local Query Synthesis

This branch creates the question side of a SciCode-style dataset without
calling Kimi or any other model. The generator is deterministic and uses a
catalog of parameterized scientific computation blueprints. Kimi API calls are
reserved for the later answer and trace stage.

## Record Shape

One query is one top-level problem record. It contains the same eight fields as
the original SciCode JSONL format:

`problem_name`, `problem_id`, `problem_description_main`, `problem_io`,
`required_dependencies`, `sub_steps`, `general_tests`, and
`problem_background_main`.

Each `sub_steps` item contains `step_number`, `step_description_prompt`,
`function_header`, `test_cases`, `return_line`, and `step_background`.
The test snippets use the normal `target` placeholder. No answer code, private
oracle, HDF5 file, or trace is generated here.

The schema checker only checks field names, types, identifiers, and ordered
steps. It does not decide whether a problem is scientifically correct,
difficult, novel, or solvable. Those decisions belong to the downstream answer
and evaluation workflow.

## Generate

The generator has no network or model configuration. It can run on any machine
with Python 3.10 or newer:

```bash
python scripts/synthesize_queries.py \
  --target-queries 10000 \
  --output-dir runs/local-query-10k \
  --seed 20260913 \
  --id-prefix synth
```

The command writes:

```text
queries.jsonl    SciCode-compatible problem records
metadata.jsonl   Blueprint, seed, subproblem count, and record hash
errors.jsonl     Builder/schema failures, if any
manifest.json    Counts and an explicit zero-model-call declaration
```

The target counts top-level problem records. The manifest also reports the
total number of generated subproblems. Re-running with the same output
directory resumes from the next unused id and does not duplicate records.

## Inspect

```bash
python -c 'import json; p="runs/local-query-10k/manifest.json"; print(json.load(open(p)))'
python -c 'import json; p="runs/local-query-10k/queries.jsonl"; print(json.loads(open(p).readline()))'
```

No `answer`, `reference`, `oracle`, or trace path is needed until the separate
answer-synthesis stage.
