# SciCode 10k batch runner

`scripts/run_10k_batch.py` is the operator-side controller for producing a
large set of independent SciCode candidates. It is deliberately outside the
authoring instructions: the authoring session receives only its allocated
candidate worktree and follows `AGENTS.md`. Batch size, concurrency, process
launch details, retries, and delivery locking stay here.

## What it does

For each slot, the controller:

1. Allocates a unique Git worktree and branch with `WorktreeManager`.
2. Creates the fixed `authoring/<candidate-id>` artifact layout.
3. Starts one authoring session in that worktree.
4. Watches `runs/*/handoff.json` without reading private solver material into
   the authoring prompt.
5. Waits for the run manifest and exported trace, then resumes the same
   authoring session for trace review and revision.
6. Invokes the deterministic candidate pipeline only after the release gate is
   recorded as accepted.
7. Serializes dataset/registry writes with a delivery lock, so concurrent
   candidates cannot overwrite one another's rows.
8. Persists resumable state after every polling cycle.

The target is measured in complete `(subproblem, trace)` rows, not in whole
problems. The exporter stops at the configured target and writes additional
valid rows to its overflow file.

## Configure a run

Copy `scripts/batch_config.example.json` and edit only operator settings. Use
`scripts/runtime.env.example` as the starting shape for the referenced
environment file. Keep secrets in that environment file; never put them in
JSON or Git.

The environment file is also the runtime boundary inherited by each authoring
session and its AvaCore handoff. Provide the solver and storage values there,
for example:

```bash
BASE_URL=https://your-internal-model-host/v1
OPENAI_API_KEY=replace-me
MODEL=your-model-name
POSTGRES=postgresql://user:password@db-host:5432/avacore
```

Keep the API key and database password out of this document. Do not export
proxy variables in the runtime environment: the batch launcher removes them
from each Kimi Code child so model traffic remains direct. When an authoring
session retrieves GitHub, arXiv, Hugging Face, or Docker Hub material, set the
proxy inline on that external retrieval command exactly as prescribed by
`AGENTS.md`, then let it expire. The solver-run skill consumes `BASE_URL`,
`OPENAI_API_KEY`, and `POSTGRES`; the authoring prompt never contains their
values. A simple connectivity check uses the same `BASE_URL` and key, but it
does not replace a complete AvaCore run.

Each child receives its allocated worktree as both its process `PWD` and its
isolated Kimi Code `HOME`. Existing `XDG_CONFIG_HOME` and `XDG_CACHE_HOME`
settings remain available so authenticated `gh` and Hugging Face commands can
continue to use the configured credentials.

Set `mirror_root` when the source checkout is shared by more than one batch:

```json
{
  "repository_root": "/root/scicode-authoring/repository-clean",
  "mirror_root": "/var/lib/scicode-git-mirrors"
}
```

At batch creation the controller clones the clean `integration_branch` into
`<mirror_root>/<batch-id>` with `--no-hardlinks`, verifies the source commit,
removes the clone's origin, and records the source and mirror identities in
`state.json`. All candidate worktrees for that batch are created from this
mirror. Resuming a batch reuses the recorded mirror and never creates a second
one. Put the mirror root on local XFS or another low-latency filesystem when
the candidate workspace is on a network-mounted path.

Important settings:

| Setting | Meaning |
| --- | --- |
| `concurrency` | Number of authoring sessions allowed at once. |
| `target_samples` | Number of accepted subproblem/trace rows. |
| `max_candidates` | Optional safety cap on allocated candidates. |
| `max_rounds` | Maximum authoring/resume process launches per candidate. |
| `run_timeout_seconds` | Maximum wait for a closed solver run manifest. |
| `merge_accepted` | Merge committed accepted branches under the delivery lock. |
| `cleanup_worktrees` | Remove only successfully merged worktree copies. |
| `drain` | Let active candidates finish after the target is reached. |
| `mirror_root` | Parent directory for one independent Git mirror per batch. |

## Start

From the repository checkout, run:

```bash
./scripts/run_10k_batch.sh \
  --config /root/scicode-authoring/batch-config.json
```

Command-line values override the JSON file. For a bounded smoke run:

```bash
./scripts/run_10k_batch.sh \
  --concurrency 2 \
  --target-samples 12 \
  --max-candidates 4 \
  --poll-seconds 10 \
  --dry-run
```

`--dry-run` prints the resolved paths and planned candidate identifiers without
allocating worktrees or starting sessions.

## Resume and monitor

Every run creates:

```text
<batch-root>/<batch-id>/state.json
<batch-root>/<batch-id>/jobs/<candidate-id>/author-round-*.log
<batch-root>/<batch-id>/jobs/<candidate-id>/delivery.log
```

Resume after a controller restart with:

```bash
./scripts/run_10k_batch.sh \
  --resume-batch /root/scicode-authoring/batches/<batch-id>
```

Inspect `state.json` for each stage (`authoring`, `waiting_solver`,
`reviewing`, `delivery`, `done`, or `failed`), the last exit code, run paths,
accepted row count, and an actionable failure message. The controller does not
kill active children when interrupted; rerun with `--resume-batch` after the
children have exited or continue polling from the same process.

When the target is reached, the default `drain=true` lets already-running
candidates finish. Pass `--no-drain` to return as soon as the target is
reached; unfinished candidates remain recorded in the state file.

## Delivery and recovery

The shared delivery directory contains `dataset.jsonl`, `registry.jsonl`,
`summary.json`, and an overflow JSONL when the target boundary splits a
candidate. Never edit these files by hand. Investigate a failed candidate in
its job log and worktree, correct the candidate through its authoring session,
and resume the batch. Keep rejected branches for audit; clean only an
explicitly approved merged worktree copy.
