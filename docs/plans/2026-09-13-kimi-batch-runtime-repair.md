# Kimi Batch Runtime Repair Implementation Plan

> **For Codex:** Execute this plan task by task and verify each runtime change before starting the next batch.

**Goal:** Make high-concurrency Kimi Code authoring sessions use their allocated worktree and direct model route, preserve the existing GitHub/Hugging Face login behavior, and start a fresh batch without reusing the failed batch state.

**Architecture:** Keep the existing controller and strict SciCode flow. Give each Kimi Code child its already-created isolated `KIMI_CODE_HOME` as `HOME`, set `PWD` to the allocated worktree, and do not inject proxy variables into the child environment. Leave the 1800-second per-request timeout and Kimi Code's observed ten-attempt retry policy unchanged; do not add a memory cap, Docker layer, or `max_candidates` requirement.

**Tech Stack:** Python `subprocess`, Kimi Code CLI, SciCode batch controller, Git worktrees, `gh`, Hugging Face CLI, pytest.

---

## TODO

- [x] Verify `gh` and `hf` under an isolated task `HOME`; retain the existing `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` behavior.
- [x] Probe the Kimi chat endpoint directly and through the corporate proxy; record the route and latency evidence.
- [x] Stop the failed `scicode-kimi-k3-1k-c500` controller and its descendants; preserve its state, logs, and candidate directories.
- [x] Remove only temporary probe files and confirm no batch Kimi Code zombies remain.
- [x] Add a regression test for the child environment: `HOME` must be the per-job Kimi home, `PWD` must be the worktree, and no proxy variables may be supplied by the batch launcher.
- [x] Patch `scripts/run_10k_batch.py` to set `HOME` and `PWD` for every Kimi Code child while preserving explicit XDG/CLI credential paths.
- [x] Update the batch runbook and example configuration to state that proxy variables are command-scoped for external source retrieval, not exported by the batch launcher.
- [x] Verify the patched child with a short `strace`/workspace probe: only the isolated Kimi home and allocated worktree may be watched.
- [x] Run the repository's focused tests and lint checks on the patched branch.
- [x] Stabilize latest-handoff selection when filesystem timestamps tie on Windows.
- [x] Commit the focused change, push the branch to `Kevin589981/SciCode`, and fast-forward a clean yicloud checkout to the same commit.
- [x] Remove the old batch's allocated worktrees only after archiving a manifest of their paths and preserving state/log evidence; do not delete unrelated worktrees.
- [x] Create a separate smoke root and run one worker; inspect the first Kimi requests before scaling (the smoke was stopped after the direct-route probe, with artifacts retained).
- [x] Confirm direct model routing, valid `gh`/`hf` access under the isolated child `HOME`, and correct watcher/worktree paths; defer the solver handoff check to the formal batch.
- [x] Remove the tracked historical candidate from the clean-batch baseline while retaining it in Git history, then sync that baseline to yicloud.
- [x] Raise only the host inotify-instance ceiling required for the requested 500 concurrent Kimi Code processes; do not impose a memory limit.
- [x] Create the formal 1000-sample/500-worker batch, verify its child environment, then stop it before changing the Git allocation strategy.
- [x] Add a per-batch independent Git mirror on a low-latency filesystem and verify its commit and remote isolation.
- [ ] Start the fresh 1000-sample/500-worker batch from that mirror and inspect its first complete handoff; its child environments and direct routing are already verified.
- [ ] Scale the mirror-backed batch only after its smoke passes; do not change memory limits, add Docker, or alter the strict solver contract.

## Recorded evidence

- `gh auth status`, `gh api user`, and `hf auth list` succeed when `HOME` points
  at a fresh per-task directory while `XDG_CONFIG_HOME=/root/.config` and
  `XDG_CACHE_HOME=/root/.cache` are retained. `hf auth whoami` succeeds when
  the proxy is scoped to that external command.
- The previous batch left 1462 manifest rows. Its cleanup manifest and report
  are retained under the old batch directory; 1419 directories were removed,
  43 were already absent, and no cleanup operation failed.
- A direct request to the internal solver endpoint returned HTTP 200; the same
  request through the external-source proxy returned HTTP 502/EOF. The batch
  launcher therefore strips proxy variables from model children.
- The host currently exposes `fs.inotify.max_user_instances=128`; each Kimi
  Code child consumes an inotify instance, so the 500-worker batch needs a
  larger host ceiling before launch.
- The formal batch reached 174 candidates with no failures before it was
  stopped. `git worktree add` was waiting on metadata reads under the shared
  virtiofs `.git/worktrees` registry; the new batch must use its own mirror.
- The mirror probe cloned commit `54341c1` onto XFS, had zero remotes, and was
  removed after verification. The replacement batch pinned the same commit in
  `/var/lib/scicode-git-mirrors/scicode-batches/<batch-id>`.
- The replacement batch reached 178 authoring candidates with zero controller
  errors; its first child worktree points to the XFS mirror and its model
  socket is direct. A complete solver handoff is still pending.

## Verification commands

1. `pytest -q tests/test_batch_runner.py tests/test_pipeline_dry_run.py`
2. Inspect a live child with `/proc/<pid>/cwd`, `HOME`, `PWD`, `KIMI_CODE_HOME`, and `ss`.
3. Confirm the first clean candidate has a completed Kimi Code response before allowing high concurrency.
