---
name: scicode-avacore-run
description: Run one complete strict SciCode candidate through AvaCore and return auditable Kimi traces.
---

# SciCode AvaCore Run Skill

Use this skill as the only solver handoff. Kimi Code is the caller; Kimi is the
model called inside AvaCore.

## Required inputs

Require these paths and settings from the candidate manifest:

- `public/problem.jsonl`
- private `oracle/targets.h5`
- candidate id and revision
- final model and sampling configuration
- PostgreSQL connection from the environment
- AvaCore checkout and Python environment

Do not put the API key in the command line or any artifact.

## Execute

Do not make Kimi Code wait inside the authoring session and do not make it
construct an AvaCore command. Submit a detached run through the wrapper:

```bash
python scripts/submit_avacore_run.py \
  --candidate-dir "$CANDIDATE_DIR" \
  --avacore-python /root/scicode-avacore/AvaCore/.venv/bin/python \
  --runner /root/scicode-authoring/repository/eval/avacore/scicode_avacore.py \
  --base-url http://10.100.184.127:5050 \
  --model "$KIMI_MODEL" \
  --run-id "$RUN_ID" \
  --temperature "$TEMPERATURE" \
  --max-tokens 262144 \
  --timeout 7200 \
  --http-retries 5 \
  --concurrency "$CONCURRENCY"
```

The wrapper inherits `POSTGRES` and `OPENAI_API_KEY` from the environment,
writes `runs/$RUN_ID/handoff.json`, and returns the PID. Tell Kimi Code to
close the current authoring session after submission. Resume it later with
`kimi -r SESSION_ID -p "Read the handoff and trace manifest, then continue the
quality review."`.

Poll without contacting the model:

```bash
python scripts/avacore_run_status.py "$CANDIDATE_DIR/runs/$RUN_ID/handoff.json"
```

Do not pass `--max-steps` for a quality or formal run. Do not add tools,
feedback, or extra prompt context. Scope the corporate proxy only around
source retrieval; the internal Kimi endpoint may be reached directly.

## Retry and classify

Retry a transient or incomplete run up to three times. Stop early when the
first run has a complete trace for every step. Classify a provider timeout,
queue failure, connection failure, or database failure as infrastructure. Mark
`finish_reason=length` at the 262,144-token limit as a normal trace status and
return it for quality analysis.

## Return

Write `runs/$RUN_ID/manifest.json` containing candidate/revision hashes,
provider/model/settings, step counts, run status, trace completeness, token
totals, retry records, and export hash. Return only paths and structured
metadata to Kimi Code; never return private target values in a solver prompt.
