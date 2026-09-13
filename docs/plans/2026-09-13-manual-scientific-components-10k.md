# Manual Scientific Components 10k Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a locally reproducible catalog of hand-authored scientific task components, compose it into 10,000 SciCode-compatible query records, and publish an auditable batch without calling a language model during query creation.

**Architecture:** Keep the original SciCode record shape unchanged. Model the question side as a catalog of domain components, phenomenon/method variants, measurement contracts, step-topology templates, and test-observation templates. A deterministic composer selects only compatible combinations, renders a complete multi-step problem, and writes a separate metadata record containing component IDs, provenance, and a normalized diversity signature. A batch report rejects duplicate or structurally incomplete records before the 10k artifact is declared finished.

**Tech Stack:** Python 3.10+, standard-library dataclasses/json/hashlib/random/pathlib, pytest, ruff, JSONL.

---

### Task 1: Create the independent branch and freeze the contract

Status: complete.

**Files:**
- Create: `docs/plans/2026-09-13-manual-scientific-components-10k.md`
- Modify: `docs/local-query-synthesis.md`

Record that this checkout has its own `.git`, that query generation is model-free, that the top-level unit is one SciCode problem, and that provenance is metadata rather than an extra field in the SciCode record.

### Task 2: Define the hand-authored component catalog

Status: complete. The catalog contains 30 recipes, 12 scenarios, 57 method
modes, 8 diagnostic families, and 20 design-focus variants.

**Files:**
- Create: `scripts/scientific_components.py`
- Create: `tests/test_scientific_components.py`

Add a catalog covering multiple natural-science domains and distinct computation archetypes. Each recipe must specify its compatible inputs, scientific assumptions, parameter renderer, ordered step contracts, test-observation strategy, and source/provenance note. Keep source notes descriptive and do not copy upstream implementation code.

### Task 3: Add deterministic composition and semantic signatures

Status: complete.

**Files:**
- Modify: `scripts/scientific_blueprints.py`
- Create: `scripts/diversity_report.py`
- Modify: `tests/test_scientific_blueprints.py`

Compose compatible catalog entries with a seed-derived mixed-radix index. Vary domain, phenomenon, numerical method, boundary/noise scenario, diagnostic, number of steps, and test layout. Preserve `build_blueprint()` compatibility for existing callers while exposing component metadata and a normalized signature that does not count IDs or numeric literals as diversity.

### Task 4: Harden batch writing and metadata

Status: complete.

**Files:**
- Modify: `scripts/synthesize_queries.py`
- Modify: `tests/test_synthesize_queries.py`

Write component IDs, provenance, composition signature, and quality-gate results to metadata. Reject duplicate normalized signatures when the catalog has alternatives, keep resume behavior deterministic, and include catalog/version and distribution counts in the manifest. Never create answer, oracle, or trace artifacts.

### Task 5: Verify and generate the 10k batch

Status: complete. The finished batch contains 10,000 valid records and 30,000
ordered subproblems; the diversity report has no schema, ID, exact-record, or
metadata failures.

Run the focused tests, ruff, a small batch, and the full 10,000-record batch in a new output directory. Validate every record with the exact schema, check unique IDs and complete hashes, produce a diversity report, and record the final manifest and artifact hashes. Do not overwrite the prior local 10k draft.

### Task 6: Commit and hand off

Status: in progress until the implementation commit is created.

Commit the catalog, composer, tests, documentation, and generated manifest/report. Leave the independent branch ready for a later answer/oracle stage; do not invoke Kimi Code or the Kimi API in this query-only stage.
