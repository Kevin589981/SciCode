# Reasoning-Trace Factory v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a three-archetype SciCode task factory that preserves native thinking, grades trace value independently of pass/fail, and exports canonical reasoning-aware SFT JSONL.

**Architecture:** Add a side-by-side `factory.reasoning` vertical pipeline rather than changing the legacy single-function path. Use append-only JSONL between task authoring, rollout, grading, and export; surround LLM semantic decisions with deterministic schemas and validators.

**Tech Stack:** Python 3.12 standard library, existing `factory.author.llm` OpenAI-compatible client, `unittest`, JSONL, Git/SSH, Kimi-K3 16k smoke endpoint.

---

### Task 1: Establish schemas and deterministic validation

**Files:**
- Create: `factory/reasoning/__init__.py`
- Create: `factory/reasoning/schema.py`
- Create: `tests/factory/test_reasoning_schema.py`

**Steps:**
1. Write tests for valid tasks in all three archetypes and for rejection of shallow, malformed, and archetype-incomplete tasks.
2. Run `python -m unittest tests.factory.test_reasoning_schema -v` and confirm failure.
3. Implement constants, canonical JSON hashing, `validate_task`, `validate_trace`, and `validate_grade` using standard-library types.
4. Re-run the test module and confirm it passes.
5. Commit as `feat(reasoning): add canonical task and trace schemas`.

### Task 2: Add three-archetype task composition

**Files:**
- Create: `factory/reasoning/prompts.py`
- Create: `factory/reasoning/author.py`
- Create: `tests/factory/test_reasoning_author.py`

**Steps:**
1. Write fake-client tests proving that one candidate can produce each archetype, model metadata is retained, malformed JSON is reported, and completed task IDs resume without duplication.
2. Run the focused tests and confirm failure.
3. Implement author prompts, strict JSON parsing, task normalization, round-robin archetype assignment, append+flush output, and error JSONL.
4. Re-run tests and confirm pass.
5. Commit as `feat(reasoning): compose heterogeneous scientific tasks`.

### Task 3: Add deterministic and semantic task preflight

**Files:**
- Create: `factory/reasoning/preflight.py`
- Create: `tests/factory/test_reasoning_preflight.py`

**Steps:**
1. Write tests for deterministic depth evidence, shallow task rejection, valid critic responses, malformed critic responses, and resume behavior.
2. Run the focused tests and confirm failure.
3. Implement structural depth features and optional LLM critic dimensions without using executable pass/fail as the principal score.
4. Re-run tests and confirm pass.
5. Commit as `feat(reasoning): gate task depth before rollout`.

### Task 4: Collect reasoning-preserving rollouts

**Files:**
- Create: `factory/reasoning/rollout.py`
- Create: `tests/factory/test_reasoning_rollout.py`

**Steps:**
1. Write fake-client tests that assert `reasoning_content` is retained exactly, metadata/hashes are recorded, optional outcomes remain auxiliary, and resume keys include model plus task ID.
2. Run the focused tests and confirm failure.
3. Implement task prompts, solver calls, trace records, append+flush output, selective error records, and bounded concurrency.
4. Re-run tests and confirm pass.
5. Commit as `feat(reasoning): preserve native solver thinking in raw traces`.

### Task 5: Grade trace value independently of outcome

**Files:**
- Create: `factory/reasoning/grade.py`
- Create: `tests/factory/test_reasoning_grade.py`

**Steps:**
1. Write tests where a failed but coherent trace is trainable, a passing trivial trace is rejected, message annotations are complete, and invalid judge scores fail validation.
2. Run the focused tests and confirm failure.
3. Implement the multidimensional judge prompt, complete-trace rendering, deterministic grade validation, trainability policy, error records, and resume.
4. Re-run tests and confirm pass.
5. Commit as `feat(reasoning): score scientific trace value beyond pass fail`.

### Task 6: Export canonical thinking-aware SFT

**Files:**
- Create: `factory/reasoning/export.py`
- Create: `tests/factory/test_reasoning_export.py`

**Steps:**
1. Write tests for outcome-independent inclusion, byte-preserved reasoning, per-message reasoning/content masks, rejection reports, and optional inline-thinking conversion.
2. Run the focused tests and confirm failure.
3. Implement trace/grade joining, canonical JSONL export, manifest/report generation, and inline adapter.
4. Re-run tests and confirm pass.
5. Commit as `feat(reasoning): export graded thinking-aware SFT`.

### Task 7: Add one-command orchestration and provenance

**Files:**
- Create: `factory/reasoning/pipeline.py`
- Create: `tests/factory/test_reasoning_pipeline.py`
- Modify: `factory/README.md`
- Modify: `docs/PROJECT_STATE.md`

**Steps:**
1. Write a fully offline fake-client integration test for author -> preflight -> rollout -> grade -> export and verify all manifests/hashes.
2. Run the integration test and confirm failure.
3. Implement stage orchestration, 16k/timeout options, model roles, run manifest, and documented CLI examples.
4. Run all reasoning tests, then the repository's existing tests.
5. Update project state with implementation commits and remaining limitations.
6. Commit as `feat(reasoning): orchestrate trace factory end to end`.

### Task 8: Synchronize and run Kimi-K3 16k smoke

**Files:**
- Update: `docs/PROJECT_STATE.md`
- Runtime artifacts only: remote `.work/reasoning-smoke-*` and `data-reasoning-smoke-*` (never commit traces)

**Steps:**
1. Push the tested feature branch and record its commit.
2. Preserve any remote untracked factory copy, then advance `/root/ScienceIDE-workspace/SciCode` to the exact pushed commit without removing existing data directories.
3. Verify local/remote commit equality and run offline tests in `/root/scicode-factory-venv`.
4. Run one task per archetype with Kimi-K3, `max_tokens=16384`, a long timeout, and bounded concurrency.
5. Validate tasks, traces, grades, canonical SFT, reasoning preservation, and the run manifest.
6. Record smoke metrics and limitations in `docs/PROJECT_STATE.md`, commit, push, and resync the remote checkout.

