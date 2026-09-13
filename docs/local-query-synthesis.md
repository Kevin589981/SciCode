# Local Query Synthesis

This branch creates the question side of a SciCode-style dataset without
calling Kimi Code, Kimi, or any other model. The generator is deterministic
and uses a hand-authored catalog of scientific components. Each recipe is
composed from a domain phenomenon, a numerical/statistical method, an
observation scenario, a diagnostic, and a design-focus variant. Kimi API calls
are reserved for the later answer and trace stage.

The active catalog contains 30 reviewable recipes across physics, chemistry,
materials, biology, astronomy, geoscience, statistics, and signal analysis.
It also contains 12 observation scenarios, 57 method modes, 8 diagnostic
families, and 20 design-focus variants. These are semantic prompt components;
numeric values are only one part of the variation.

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
metadata.jsonl   Component IDs, provenance, composition signature, seed, and record hash
errors.jsonl     Builder/schema failures, if any
manifest.json    Counts and an explicit zero-model-call declaration
```

The target counts top-level problem records. The manifest also reports the
total number of generated subproblems. Re-running with the same output
directory resumes from the next unused id and does not duplicate records.

Run the batch audit after synthesis:

```bash
python scripts/diversity_report.py \
  runs/manual-components-10k/queries.jsonl \
  --metadata-file runs/manual-components-10k/metadata.jsonl \
  --output runs/manual-components-10k/diversity-report.json
```

The audit validates every query, checks exact-record and ID uniqueness, and
reports component, domain, subdomain, method, scenario, diagnostic, design
variant, step-count, and public-test-layout distributions. Repeated
normalized signatures are reported as a warning because parameterized records
may share a semantic skeleton; exact duplicate records are a failure.

## Inspect

```bash
python -c 'import json; p="runs/local-query-10k/manifest.json"; print(json.load(open(p)))'
python -c 'import json; p="runs/local-query-10k/queries.jsonl"; print(json.loads(open(p).readline()))'
```

No `answer`, `reference`, `oracle`, or trace path is needed until the separate
answer-synthesis stage.
