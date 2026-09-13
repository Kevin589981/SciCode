# Local SciCode Query Synthesis Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Generate 10,000 SciCode-compatible problem queries locally, without calling Kimi or any other model during query creation; reserve model calls for the later answer stage.

**Architecture:** A deterministic catalog of scientific task blueprints expands into independent parameterized problem records. Each record is emitted directly in the original SciCode JSONL shape, with no answer, reference implementation, oracle, or trace. A lightweight schema validator checks only transport compatibility; scientific review and answer generation remain separate downstream stages.

**Tech Stack:** Python 3.10+, standard-library `json`, `hashlib`, `random`, JSONL, pytest.

---

### Task 1: Freeze the query-only contract

Status: complete.

**Files:**
- Modify: `docs/plans/2026-09-13-direct-query-generation.md`
- Create: `docs/local-query-synthesis.md`

Document that the unit is one top-level SciCode problem record, preserve the eight original top-level fields and six sub-step fields, and explicitly state that Kimi/API calls, answers, oracle values, and traces are out of scope for this stage.

### Task 2: Implement the schema validator

Status: complete.

**Files:**
- Create: `scripts/query_schema.py`
- Create: `tests/test_query_schema.py`

Implement a dependency-free validator for the original field names, string/list types, non-empty ordered substeps, stable problem ids, and absence of generated answer-only fields. Do not evaluate scientific correctness or difficulty here.

### Task 3: Implement parameterized scientific blueprints

Status: complete.

**Files:**
- Create: `scripts/scientific_blueprints.py`
- Create: `tests/test_scientific_blueprints.py`

Add a catalog of multi-step scientific computation families spanning numerical methods, simulation, physics, chemistry, biology, materials, and signal/data analysis. Each blueprint must produce a complete SciCode-shaped record with parameterized units, assumptions, function headers, test snippets using the evaluator's `target` placeholder, and no reference answer.

### Task 4: Implement the local batch writer

Status: complete.

**Files:**
- Create: `scripts/synthesize_queries.py`
- Create: `tests/test_synthesize_queries.py`

Generate stable ids and deterministic records from a seed, write `queries.jsonl` and a separate `metadata.jsonl`, support resume without duplicate ids, write an atomic manifest, and verify the exact requested count. The writer must not import or invoke an HTTP client, Kimi Code, or the official SciCode dataset.

### Task 5: Verify locally

Status: complete. The focused suite passes and a local 10,000-record run
produced 10,000 valid records, 30,000 ordered subproblems, and zero model calls.

Run the focused test suite, generate a small deterministic batch, validate every record, inspect representative domains and subproblem counts, and confirm that no answer/oracle/trace files are produced.

### Task 6: Start the 10k batch

Status: in progress.

Commit and push this branch, clone it into a fresh yicloud checkout with an independent `.git`, run the local synthesizer for 10,000 queries in a detached process, and record the output manifest and monitoring command. Keep the existing Kimi Code batch separate and untouched; Kimi is used only in the later answer synthesis stage.
