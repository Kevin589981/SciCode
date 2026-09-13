# Direct SciCode Query Generation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Generate a configurable set of SciCode-shaped problem queries directly through an OpenAI-compatible chat endpoint, without starting Kimi Code or an authoring worktree for each query.

**Architecture:** A stateless concurrent producer assigns a stable query id before each request, sends a minimal schema-oriented generation prompt, and writes the raw provider response separately from structurally accepted query records. The producer performs only parsing and shape checks needed to create valid SciCode records; scientific review, reference answers, private tests, HDF5 oracles, and traces remain later stages.

**Tech Stack:** Python 3.10+, standard-library `urllib`/`json`, `ThreadPoolExecutor`, JSONL, pytest.

---

### Task 1: Define the direct-generation contract

**Files:**
- Create: `docs/plans/2026-09-13-direct-query-generation.md`
- Create: `docs/direct-query-generation.md`

Document that one generated query is one top-level SciCode problem record. Record the original eight top-level fields and six sub-step fields, explain that answers/oracles/traces are intentionally deferred, and document the environment variables and output files without exposing credentials.

### Task 2: Add the query parser and structural validator

**Files:**
- Create: `scripts/generate_queries.py`
- Create: `tests/test_generate_queries.py`

Implement extraction of a JSON object from a plain or fenced model response, validation against the original SciCode field names and types, contiguous step numbering, and rejection records. Do not add scientific-quality rules to the generation prompt or validator beyond the structural contract.

### Task 3: Add the OpenAI-compatible producer

**Files:**
- Modify: `scripts/generate_queries.py`
- Modify: `tests/test_generate_queries.py`

Implement configurable endpoint, model, temperature, token limit, timeout, retries, concurrency, target count, output directory, and resume behavior. Assign ids before requests, preserve raw responses and usage metadata, use atomic per-record writes under a lock, and keep secrets out of manifests.

### Task 4: Verify locally with a mock provider

**Files:**
- Modify: `tests/test_generate_queries.py`

Cover valid plain JSON, fenced JSON, malformed responses, retryable HTTP errors, request timeout handling, resume without duplicate ids, usage/finish metadata preservation, and concurrent target accounting using an in-process mock HTTP server or injected transport.

### Task 5: Run a small real smoke

Run the producer for a small count against the configured direct endpoint, inspect `queries.jsonl`, `responses.jsonl`, `rejected.jsonl`, and `manifest.json`, and verify that no Kimi Code process, worktree, answer, oracle, or trace is created.

### Task 6: Start the 10k query batch

Clone this branch into a fresh yicloud checkout with a new `.git`, configure the deployed chat endpoint and model through environment variables, start the producer detached with the requested target of 10,000 queries, and record the batch directory and monitoring command. Do not mix this output with the older Kimi Code candidate batch.

