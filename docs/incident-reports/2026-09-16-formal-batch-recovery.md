# Formal Batch Recovery Report

## Observed Batch

`scicode-batch-20260913-184824-6b2848` was created on 2026-09-13 and last
persisted state on 2026-09-14 10:05 CST. The controller is no longer running;
the state file is a frozen snapshot, not a live progress report.

At stop: 651 jobs, 32 accepted sample rows, 3 jobs marked done, 150 failed,
336 authoring, 90 waiting for solver, and 72 reviewing.

## Root Causes

1. The old controller did not retry the first 125 provider HTTP 404 failures.
2. AvaCore runs exhausted PostgreSQL connection capacity. Observed logs include
   `PoolTimeout`, `remaining connection slots are reserved`, and interrupted
   SciPy initialization. Twenty-four jobs were eventually marked as controller
   timeouts without a manifest.
3. Candidate authoring sessions were allowed to run `cleanup-copy`. At least
   three allocated worktrees disappeared while the controller still tracked
   them. The controller then attempted to use the missing `kimi1kmirror-000285`
   worktree and exited with `FileNotFoundError`.
4. The old scheduler launched candidates from one serial `_fill_slots` loop.
   One slow `git worktree add` blocked the next Kimi Code launch.
5. The controller had no per-job exception boundary around stage advancement,
   so one missing worktree terminated the whole scheduler.
6. Detached AvaCore runs were not globally throttled; each run could create a
   database pool independently.

## Recovery Export

The recovery exporter scans only complete manifests and non-empty rollouts. It
does not rewrite the original delivery directory. The 2026-09-16 snapshot
exported 633 usable subproblem-level samples from 135 candidates. It skipped
510 candidates without a complete trace, 3 missing worktrees, and rejected 3
source-fingerprint duplicates. The output is stored under the batch recovery
directory on yicloud.

## Replacement Architecture

Use `scripts/run_10k_batch_mp.py` with independent OS worker processes. Each
worker owns a one-candidate controller state and local delivery directory, then
merges completed rows into the shared dataset under a file lock. The parent
process only monitors the target and worker health. It creates independent Git
mirror shards, forbids Kimi-side worktree cleanup, and injects a global
AvaCore slot limit so PostgreSQL capacity is bounded.
