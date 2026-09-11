# Automated SciCode authoring handoff

The repository uses two explicitly separated roles:

1. **Kimi Code** researches scientific sources, writes a candidate in its
   isolated Git worktree, asks a child agent to review it, revises it, and
   decides whether the release gate is satisfied.
2. **Kimi** is the solver model. AvaCore is the only component that calls the
   deployed Kimi endpoint, runs the strict sequential SciCode protocol, stores
   the rollout in PostgreSQL, and exports JSONL.

## Candidate handoff

An accepted candidate must contain:

```text
candidate/
  candidate.json
  public/problem.jsonl
  public/solver_payload/
  public/checks/
  public/prompt_snapshot/
  source_notes/provenance.json
  reference/
  oracle/targets.h5
  validation/release_decision.json
```

The canonical JSONL remains in the official SciCode shape. The solver gets a
fresh redacted payload; it never receives `test_cases`, `general_tests`, the
oracle, reference code, author notes, or prior runs as separate files.

## Strict AvaCore run

Use the candidate-aware runner:

```bash
/root/scicode-avacore/AvaCore/.venv/bin/python \
  eval/avacore/scicode_avacore.py \
  --candidate-dir /root/scicode-authoring/candidates/CANDIDATE_ID \
  --base-url http://10.100.184.127:5050 \
  --model "$KIMI_MODEL" \
  --postgres "$POSTGRES" \
  --output /root/scicode-authoring/candidates/CANDIDATE_ID/runs/RUN_ID \
  --export /root/scicode-authoring/candidates/CANDIDATE_ID/runs/RUN_ID/rollouts.jsonl \
  --run-name RUN_ID \
  --temperature 0.6 \
  --max-tokens 262144 \
  --timeout 7200 \
  --score-by-subproblem
```

The runner validates the candidate before contacting Kimi, preserves the
upstream prompt and code-extraction behavior, and writes
`runs/RUN_ID/manifest.json`. Use the detached wrapper in
`.agents/skills/scicode-avacore-run/` when Kimi Code must exit its current
session while the run is pending.

## Promote or revise

Feed `manifest.json` and `rollouts.jsonl` back to Kimi Code after the run
closes. Let Kimi Code inspect ordinary content, reasoning content, code,
finish reason, token usage, and scientific behavior for every subproblem.
Treat a wrong answer as an acceptable trace-quality result when the trace is
complete and auditable; keep candidate correctness as a separate release gate.

Promote a quality run to formal evaluation only when its candidate/oracle
hashes, prompt profile, strict model settings, complete step set, private
verifier result, PostgreSQL persistence, and JSONL export match the release
manifest. Otherwise label it `qa_only` and do not add it to the final SFT
dataset.

## Expand to SFT samples

Run the delivery skill after the release decision:

```bash
python scripts/export_subproblem_samples.py \
  --candidate-dir /root/scicode-authoring/candidates/CANDIDATE_ID \
  --rollouts /root/scicode-authoring/candidates/CANDIDATE_ID/runs/RUN_ID/rollouts.jsonl \
  --registry /root/scicode-authoring/delivery/registry.jsonl \
  --output /root/scicode-authoring/delivery/dataset.jsonl \
  --summary /root/scicode-authoring/delivery/summary.json \
  --target-count 10000
```

The exporter emits one SFT-compatible record for each complete non-skipped
subproblem and preserves `messages`, `completion`, `reasoning_content`,
`completion_with_reasoning`, `parsed_code`, `context_code`,
`provider_response`, `usage`, and `metadata`. It deduplicates by candidate
revision, problem, step, and trace hash, and never puts private oracle data in
the training JSONL.
