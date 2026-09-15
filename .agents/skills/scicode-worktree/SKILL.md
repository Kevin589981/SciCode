---
name: scicode-worktree
description: Allocate and manage an isolated fixed Git worktree for one SciCode candidate.
---

# SciCode Worktree Skill

Use this skill before creating or editing a candidate.

## Required actions

1. Read `WORKSPACE_ROOT`, `REPOSITORY_ROOT`, and `INTEGRATION_BRANCH` from the
   run configuration. Default `WORKSPACE_ROOT` to `/root/scicode-authoring` on
   yicloud.
2. Validate `CANDIDATE_ID` with the lifecycle script. Reject path separators,
   parent traversal, whitespace, and IDs already held by another process.
3. Acquire the candidate lock and keep it until commit/merge or explicit
   rejection.
4. Run:

   ```bash
"$SCICODE_PYTHON" scripts/worktree.py create \
     --repository "$REPOSITORY_ROOT" \
     --workspace-root "$WORKSPACE_ROOT" \
     --candidate-id "$CANDIDATE_ID" \
     --base-ref "$INTEGRATION_BRANCH"
   ```

5. Create `authoring/<CANDIDATE_ID>/` inside the returned worktree and record
   the worktree path, branch, base commit, lock path, and process id in
   `authoring/<CANDIDATE_ID>/candidate.json`.
6. Set `CANDIDATE_DIR` to the nested `authoring/<CANDIDATE_ID>` path. Keep all
   candidate edits, source clones, tests, and run outputs below that directory
   or its worktree. Never edit the shared checkout or another candidate
   worktree.
7. Commit candidate changes before requesting delivery. Use one branch per
   candidate; retain the branch after deleting the worktree copy.
8. Release the lock only after the delivery skill records the final decision.

## Cleanup

Do not run `scripts/worktree.py cleanup-copy` from the Kimi Code session. The
batch coordinator owns cleanup after it has observed the final decision,
persisted the delivery/merge record, and released the candidate. Keep the
worktree and lock intact while the coordinator is tracking the candidate.

## Failure handling

If the lock or worktree command fails, stop the candidate and write the exact
error to `validation/worktree_failure.json`. Do not retry by choosing a random
directory or by removing another process's lock.
