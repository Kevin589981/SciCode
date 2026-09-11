# SciCode 10k Authoring and AvaCore Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a strict SciCode-compatible authoring pipeline in which Kimi Code generates, reviews, revises, and delivers candidates, while AvaCore runs the separate Kimi solver and produces auditable `(subproblem, trace)` samples until 10,000 accepted samples are collected.

**Architecture:** Keep the model-visible SciCode prompt and ordered subproblem contract unchanged. Add metadata-only candidate manifests, source provenance, local candidate loading, strict AvaCore execution, and a deterministic delivery index around that contract. Kimi Code remains the orchestration/review process; Kimi is invoked only by the AvaCore runner. Use one fixed worktree per candidate and a lock-protected delivery queue so parallel sessions cannot overwrite or merge each other’s artifacts.

**Tech Stack:** Python 3.10+, existing SciCode prompt/parser/evaluator, AvaCore `RolloutEngine`/`PostgresBackend`, HDF5, JSONL, Git worktrees, POSIX shell helpers, pytest.

---

### Task 1: Freeze the architecture and operator contract

**Files:**
- Create: `docs/plans/2026-09-11-scicode-10k-authoring-avacore.md`
- Create: `docs/adr/0001-strict-authoring-avacore-boundary.md`

**Step 1: Record the role boundary**

Document that Kimi Code creates and audits candidates, and that Kimi is called
only by AvaCore for strict sequential SciCode solving.

**Step 2: Record the sample unit**

Define one delivered sample as one evaluated `(candidate_revision,
problem_id, step_number, solver_trace)` record. Do not count a whole problem as
one sample.

**Step 3: Record failure and promotion rules**

Separate QA runs from formal runs. Permit a complete QA run to be promoted only
when its candidate, oracle, model settings, prompt profile, and all ordered
steps match the release manifest.

**Step 4: Verify the documents**

Run `git diff --check` and inspect the decision table for source/oracle
separation, concurrency, and trace retention.

**Step 5: Commit**

Commit the architecture documents before implementation changes.

### Task 2: Replace the agent instruction with imperative authoring rules

**Files:**
- Modify: `AGENTS.md`
- Create: `skills/README.md`

**Step 1: Define startup actions**

Command Kimi Code to read the repository contract, inspect the provided Kimi
command examples, set the approved proxy only for external hosts, and create a
fixed candidate worktree.

**Step 2: Define source and provenance actions**

Command it to research GitHub/arXiv/Hugging Face sources, pin commits and
licenses, record papers, avoid official SciCode material and duplicate source
fragments, and remove unnecessary cloned source after task completion.

**Step 3: Define the strict SciCode task contract**

Command it to preserve the exact model-visible fields and prompt context of
SciCode, create ordered scientific subproblems, generate private HDF5 oracle
data, and never turn a candidate into a generic bug-fix task.

**Step 4: Define review and handoff actions**

Command it to create a review child agent, run static/schema/oracle checks,
invoke the AvaCore handoff skill, resume only after the Kimi trace is returned,
classify failures, and repeat revision until a gate passes or the candidate is
rejected.

**Step 5: Verify imperative language**

Run a small linter that rejects top-level requirement sentences beginning with
descriptive/modal wording instead of an imperative verb, while allowing code
and explanatory examples.

### Task 3: Add concurrency-safe worktree and candidate lifecycle tooling

**Files:**
- Create: `skills/scicode-worktree/SKILL.md`
- Create: `skills/scicode-worktree/scripts/worktree.py`
- Create: `src/scicode/pipeline/worktree.py`
- Test: `tests/test_pipeline_worktree.py`

**Step 1: Write failing lifecycle tests**

Test fixed workspace allocation, candidate-id path validation, lock ownership,
branch creation arguments, and cleanup preserving the Git branch.

**Step 2: Implement the lock and path policy**

Use an atomic lock file under a configured workspace root. Reject path
traversal, reuse of an occupied candidate id, and operations outside the root.

**Step 3: Implement worktree commands**

Provide `create`, `status`, `release`, and `cleanup-copy` operations. Create
one branch per candidate with `git worktree add -b`; never modify the shared
checkout from a candidate session.

**Step 4: Add the Kimi Code skill instructions**

Make the skill issue commands and write a lifecycle manifest. Require commits
before release and prohibit force pushes or edits to shared files.

**Step 5: Run tests**

Run `PYTHONPATH=src python -m pytest -q tests/test_pipeline_worktree.py`.

### Task 4: Add candidate manifest, schema, provenance, and isolation checks

**Files:**
- Create: `src/scicode/pipeline/candidate.py`
- Create: `scripts/validate_candidate.py`
- Create: `skills/scicode-task-authoring/SKILL.md`
- Test: `tests/test_candidate_validation.py`

**Step 1: Write failing validation tests**

Cover required official fields, unique problem/step identifiers, no official
SciCode identifiers or test-data paths, valid private oracle location,
provenance fields, public/private path separation, and complete revision
metadata.

**Step 2: Implement canonical manifest loading**

Load exactly one candidate revision from `public/problem.jsonl`, normalize
paths, calculate SHA-256 hashes, count subproblems, and emit a deterministic
manifest without changing any model-visible field.

**Step 3: Implement source and license checks**

Require repository URL, pinned commit, license, paper/source URL, and a source
fragment fingerprint. Reject duplicate fingerprints against the delivery
registry.

**Step 4: Implement isolation checks**

Verify that solver-visible payloads do not contain `reference/`, `oracle/`,
private tests, target values, prior runs, or unredacted `problem.jsonl`.

**Step 5: Add the skill procedure**

Command Kimi Code to run the validator before and after every revision and to
record failures rather than silently repairing them.

**Step 6: Run tests**

Run `PYTHONPATH=src python -m pytest -q tests/test_candidate_validation.py`.

### Task 5: Make the AvaCore adapter a strict candidate runner

**Files:**
- Modify: `eval/avacore/scicode_avacore.py`
- Create: `src/scicode/pipeline/run_manifest.py`
- Create: `skills/scicode-avacore-run/SKILL.md`
- Test: `tests/test_avacore_candidate_source.py`

**Step 1: Write failing source and metadata tests**

Test local JSONL loading, candidate schema validation before provider calls,
oracle path checks, strict-only settings, complete-step accounting, and run
manifest fields.

**Step 2: Implement candidate source selection**

Keep official Hugging Face loading as the default. Add `--problem-file` for a
local candidate JSONL and validate it before constructing the model client.

**Step 3: Implement strict run metadata**

Record candidate id/revision, problem/oracle hashes, model, endpoint identity
without secrets, sampling settings, timeout/retry policy, subproblem count,
and source mode in AvaCore run configuration and exported metadata.

**Step 4: Implement complete-step checks**

Mark a run as promotable only when every non-skipped step has a trace, extracted
code, evaluator result, and token accounting record. Preserve timeout and
length termination as explicit statuses.

**Step 5: Add the handoff skill**

Command Kimi Code to close the authoring phase, invoke the strict AvaCore
runner with `http://10.100.184.127:5050`, wait for the run, and resume the
authoring session with the exported trace summary and full JSONL path.

**Step 6: Run tests**

Run `PYTHONPATH=src python -m pytest -q tests/test_avacore_candidate_source.py`.

### Task 6: Normalize AvaCore output into 10k subproblem samples

**Files:**
- Create: `src/scicode/pipeline/samples.py`
- Create: `scripts/export_subproblem_samples.py`
- Create: `skills/scicode-delivery/SKILL.md`
- Test: `tests/test_sample_export.py`

**Step 1: Write failing sample tests**

Test one output record per non-skipped subproblem, stable sample ids, retained
trace/token/finish metadata, private-field redaction, duplicate detection,
and correct counts for multi-step problems.

**Step 2: Implement tolerant AvaCore JSONL parsing**

Accept the known AvaCore export shapes, preserve the raw trace reference, and
fail closed when a subproblem trace or candidate manifest is missing.

**Step 3: Implement deterministic sample records**

Emit SFT-compatible records containing the original SciCode prompt, the Kimi
answer/code response, ordered previous-code context, trace metadata, and
candidate provenance. Do not add context to the model prompt.

**Step 4: Implement registry aggregation**

Use an atomic append/lock protocol and a registry keyed by candidate revision,
problem id, step id, and trace hash. Stop exactly at 10,000 accepted samples;
retain rejected candidates and histories separately.

**Step 5: Add delivery skill instructions**

Command the skill to validate, commit, merge candidate branches, resolve
conflicts without touching other worktrees, clean copies only after merge, and
write a summary table. Do not push automatically.

**Step 6: Run tests**

Run `PYTHONPATH=src python -m pytest -q tests/test_sample_export.py`.

### Task 7: Build the one-candidate end-to-end harness

**Files:**
- Create: `scripts/run_candidate_pipeline.py`
- Create: `scripts/pipeline_config.example.json`
- Create: `docs/10k-pipeline-runbook.md`
- Test: `tests/test_pipeline_dry_run.py`

**Step 1: Write a dry-run test**

Use a local candidate fixture and a fake AvaCore export to verify the state
machine: `created -> reviewed -> ready_for_run -> run_complete -> accepted`.

**Step 2: Implement orchestration**

Run validation, invoke the configured Kimi Code command only at the authoring
boundary, invoke the AvaCore skill command for strict solving, import the trace,
and call the delivery skill. Keep all intermediate paths fixed and explicit.

**Step 3: Implement retry policy**

Retry provider timeout runs up to the configured limit; classify a 262k output
limit as a normal trace status; never treat infrastructure failure as a task
quality result.

**Step 4: Document yicloud setup**

Document SSH alias usage, proxy scoping for GitHub/arXiv/Hugging Face/Docker
Hub, Kimi Code invocation (`kimi -p` and resume), AvaCore endpoint, PostgreSQL,
and monitoring commands without embedding credentials.

**Step 5: Run the dry-run test**

Run `PYTHONPATH=src python -m pytest -q tests/test_pipeline_dry_run.py`.

### Task 8: Execute and verify one real sample

**Files:**
- Modify only candidate/run output paths under `authoring/` or the configured
  yicloud workspace; do not commit secrets or provider output by default.

**Step 1: Prepare a clean candidate worktree**

Use the existing candidate fixture only if it passes the no-official-data and
provenance checks; otherwise ask Kimi Code to create a fresh candidate from a
permitted external source.

**Step 2: Run the complete strict handoff**

Run one complete problem through AvaCore and the deployed Kimi endpoint with
all ordered subproblems, PostgreSQL storage, JSONL export, and token capture.

**Step 3: Import and inspect samples**

Verify that each subproblem yields one sample, that prompts are unchanged,
that code extraction and HDF5 scoring agree, and that the trace includes model
usage and finish status.

**Step 4: Exercise a failure path if needed**

Use a controlled fake response or timeout fixture to verify classification and
retry handling without changing the accepted sample.

**Step 5: Fix and rerun until the flow closes**

Do not declare completion while any state transition, export, or sample count
is unverified.

### Task 9: Final verification and commit

**Files:**
- Modify: `docs/10k-pipeline-runbook.md` if observed commands differ
- Modify: `skills/*/SKILL.md` only for verified corrections

**Step 1: Run the complete local test suite**

Run `PYTHONPATH=src python -m pytest -q` and `python -m compileall -q src eval scripts`.

**Step 2: Audit the model-visible contract**

Compare candidate prompt snapshots with upstream rendering and verify that all
new fields are metadata-only.

**Step 3: Audit repository state**

Run `git diff --check`, inspect staged paths, and ensure oracle/reference/run
outputs remain ignored or explicitly classified.

**Step 4: Commit the framework**

Commit only framework code, imperative instructions, skills, tests, and docs.
Keep provider traces and private oracle artifacts outside the source commit.

**Step 5: Report the branch and sample result**

Report the branch name, commit, one-sample run id, exported trace path, sample
count, score/status, and any unresolved operational dependency.
