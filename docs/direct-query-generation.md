# Direct SciCode Query Generation

This branch contains a direct query producer. It calls an OpenAI-compatible
`/chat/completions` endpoint for each query and writes the resulting top-level
SciCode problem records. It does not start Kimi Code, create Git worktrees,
run AvaCore, execute answers, or create HDF5 oracles.

## What Counts

`--target-queries` counts accepted top-level problem records. A record may have
one or more ordered `sub_steps`; the number of subproblems is reported later
from `queries.jsonl` and is not used as the producer target.

The emitted query keeps the original SciCode fields:

- top level: `problem_name`, `problem_id`, `problem_description_main`,
  `problem_io`, `required_dependencies`, `sub_steps`, `general_tests`, and
  `problem_background_main`;
- each `sub_steps` item: `step_number`, `step_description_prompt`,
  `function_header`, `test_cases`, `return_line`, and `step_background`.

The producer only checks this transport shape. It deliberately does not judge
scientific correctness, difficulty, originality, duplicate status, or whether
the eventual answer will pass. Those checks belong to the later answer/oracle
pipeline.

## Output

The output directory contains:

```text
queries.jsonl     Canonical SciCode problem records accepted by the parser.
responses.jsonl   Every provider response, prompt, finish reason, usage, and retry.
rejected.jsonl    Structurally invalid or failed attempts with their evidence.
manifest.json     Counts, redacted endpoint identity, configuration, and status.
```

Provider usage is retained as returned, including nested reasoning-token fields
when the endpoint supplies them. API keys are never written to the manifest or
JSONL metadata.

## Run

Set only the model connection values in the shell or an external environment
file. The producer does not read `AGENTS.md` and does not require the SciCode
official dataset.

```bash
export BASE_URL=http://your-chat-host/v1
export OPENAI_API_KEY=your-key
export MODEL=your-model

python scripts/generate_queries.py \
  --target-queries 10000 \
  --concurrency 65 \
  --output-dir runs/direct-queries-$(date +%Y%m%d-%H%M%S) \
  --temperature 1.0 \
  --max-tokens 32768 \
  --timeout-seconds 1800 \
  --max-retries 3
```

`BASE_URL` may point either to the API version root (for example `/v1`) or to
the complete `/chat/completions` URL. `MODEL` and `OPENAI_API_KEY` can also be
passed as command-line options. Provider-specific request fields can be added
with `--extra-body '{"top_p": 0.95}'`.

The producer resumes safely when the same output directory is used again: IDs
already present in `queries.jsonl` are counted once and never duplicated. Use a
new output directory for an independent batch.

## Later Stages

After the query batch is selected, a separate process can add provenance,
reference implementations, private tests, HDF5 oracle values, solver answers,
and AvaCore traces. Do not put those artifacts into `queries.jsonl`; keeping
the query and answer stages separate prevents generated answers from leaking
into the initial question collection.
