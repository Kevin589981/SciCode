# Reasoning factory v3

This branch is an isolated successor to `scienceide-pipeline`. A diagnostic v2
smoke exposed useful extra public payload fields, which v3 renders explicitly.
The production
controller in the original SciCode worktree is not hot-patched. Its queue and
artifacts remain separate from every v2 output directory.

## Student-visible task boundary

`student_view.py` is the canonical renderer. The solver sees `problem`,
`deliverable`, and all archetype-specific payload givens. The source and private
reasoning rubric stay private. Any extra keys in these public sections are
rendered rather than silently dropped. The critic and verifier inspect this exact view;
the judge sees the same view plus the recorded trace. A missing input or an
answer already exposed in the prompt blocks admission. Trace IDs, review policies, provenance, and export checks
prevent a legacy or mismatched prompt from being silently reused.

This is a semantic quality gate, not a proof of scientific validity: one Kimi
critic/verifier can still make a wrong judgment. Native `reasoning_content` is
preserved in trace and SFT JSONL, and executable pass/fail is not the SFT
selection rule.

## Queue and concurrency boundary

`job_progress` materializes completed and reserved SFT rows transactionally.
The local batch coordinator caches progress/status and serializes queue mutation
among worker threads; it waits on a condition instead of polling SQLite from
500 workers. Model and repository slots in `auto`/`resume` are process-owned
and released on exception, so each model request no longer writes SQLite.
The controller lock excludes a concurrent external worker controller from the
same queue. Separate `worker` processes retain SQLite-backed slots. Lease and
slot renew/release paths retry transient writer contention. Do not place the
SQLite queue on unsupported WAL/shared-memory filesystems.

## Operations

The full launcher defaults to `SciCode-v3/data-reasoning-10k-v3` and reads the
already-prepared SciCodePile catalog/snapshots from the original data root.
The bounded launcher is `run_v3_smoke.sh`; both require `--execute`. Override
`SCICODE_OUTPUT_ROOT` for a fresh output and `SCICODEPILE_CATALOG` for a curated
catalog. Do not change the worktree commit while a batch is running: each job
recipe pins `factory_commit` and rejects version drift.

Run `python -m pytest tests/factory -q` before deployment. Do not interpret a
successful unit test as proof that Kimi has produced an accepted SFT sample;
inspect `batch.sqlite3`, per-repository reports, and `accepted-sft.jsonl`.
