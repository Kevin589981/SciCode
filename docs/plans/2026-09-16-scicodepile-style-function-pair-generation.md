# SciCodePile-Style Function-Pair Generation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Adapt the existing SciCode/AvaCore fork to generate a scalable corpus of scientifically grounded, function-level question-solution pairs while preserving the strict SciCode solver contract and auditable Kimi traces.

**Architecture:** Keep the model-visible record and strict AvaCore execution unchanged. Add a source-to-function candidate layer before the current candidate validator, make the normal production unit one SciCode problem containing one subproblem, and keep provenance, private oracle, review, trace, and delivery metadata outside the solver-visible payload. Reuse the existing per-batch mirror, isolated worker, run-manifest, trace-audit, and sample-export boundaries instead of creating a second evaluator.

**Tech Stack:** Existing Python package under `src/scicode`, JSONL/HDF5 candidate artifacts, Git batch mirrors, Kimi Code authoring sessions, strict AvaCore, PostgreSQL trace storage, JSONL delivery, pytest, and optional tree-sitter/source parsers only when they are already available in the runtime.

---

## 1. What changes and what remains fixed

### Keep fixed

- Keep the official SciCode top-level fields and ordered `sub_steps` fields in
  `public/problem.jsonl`.
- Keep the exact strict solver prompt, previous-code context rules, code
  extraction, HDF5 comparison, timeout classification, and AvaCore trace
  format. Do not add solver tools, hidden files, private tests, or extra
  context.
- Keep the answer-producing role separate: Kimi Code authors and audits;
  AvaCore calls the deployed Kimi solver and owns the trace.
- Keep private targets in a candidate-local HDF5 oracle generated from the
  candidate reference implementation. Never use official SciCode problems,
  prompts, tests, or `test_data.h5`.
- Keep the delivered unit as one `(candidate revision, problem id, step,
  trace)` record. A one-step candidate normally yields one pair; a multi-step
  candidate is allowed only when the scientific dependency is real.

### Change for scale

- Add a function-level source intake layer so one scientific repository can
  yield multiple independent, non-duplicated candidate pairs.
- Make one-step candidates the default production profile. Retain multi-step
  candidates as an explicit exception, not as a requirement for every item.
- Remove mandatory deep review from the production path. Run only cheap
  deterministic gates that prevent malformed, private, or untraceable records;
  reserve deep review for an optional small calibration sample.
- Run one strict solver attempt by default. Retry only an incomplete or
  infrastructure-failed run; a complete wrong answer remains a valid trace
  observation and is reviewed separately from candidate correctness.
- Preserve rejected candidates and revision history in Git and manifests, but
  keep only accepted subproblem-trace rows in the SFT delivery file.

## 2. Candidate unit and data flow

Use the following unit for the initial implementation:

```text
scientific repository + pinned function/source fragment
    -> scientific task specification
    -> one SciCode problem + one subproblem
    -> private reference + generated HDF5 oracle
    -> deterministic checks and provenance checks
    -> strict AvaCore/Kimi run
    -> trace audit and optional review
    -> one SFT-compatible (subproblem, trace) row
```

The generator must reject arbitrary utility functions, wrappers, setup code,
toy arithmetic, generic data transformation, API migration, installation
fixes, and shallow bug fixes. The function must implement a meaningful
scientific computation or domain algorithm, expose a finite testable contract,
and be convertible into a self-contained SciCode question without giving the
solver the source repository or the reference implementation.

The solver-visible payload contains only the normal SciCode prompt material.
Source URL, commit, license, paper, function fingerprint, review status,
token counts, run identity, and trace metadata are sidecar/manifest fields and
are never inserted into the solver prompt.

## 3. Proposed components

### 3.1 Source intake and function catalog

Create a deterministic intake module and command that:

1. accepts an allowed GitHub, arXiv, or Hugging Face source;
2. records URL, immutable commit/version, license, paper/DOI, retrieval time,
   and source file hashes;
3. extracts candidate Python functions or equivalent scientific units with
   file/line provenance and a normalized source-fragment fingerprint;
4. excludes generated files, vendored code, tests, build products, and
   fragments already present in the global registry; and
5. writes a catalog that Kimi Code can consume without copying an entire
   repository into every candidate workspace.

Use a parser when available and a conservative AST fallback otherwise. Do not
make tree-sitter a mandatory runtime dependency for the first working slice.
Keep only the source files required to understand or execute the selected
function; retain license and provenance metadata after cleanup.

### 3.2 Kimi Code authoring contract

Rewrite the new branch's `AGENTS.md` and authoring skill so that commands are
imperative and role-specific. Require Kimi Code to:

- select a catalog function with scientific evidence;
- write a SciCode-shaped one-step problem and a solver payload;
- implement a reference and private test/oracle generator;
- record provenance and source-fragment fingerprints;
- run deterministic schema, static, isolation, duplicate, and oracle checks;
- skip child-agent review in the production path;
- hand off only through the AvaCore skill;
- inspect the returned trace and classify it as complete, incomplete, or
  infrastructure-failed; and
- commit accepted candidate artifacts before delivery.

The instructions must not expose internal Windows paths, batch internals,
target counts, or operator-only details to Kimi Code. They must include only
the runtime facts it needs for source retrieval and the external-source proxy,
and must keep internal model/database credentials in the execution boundary.

### 3.3 Candidate validation and oracle checks

Extend the existing candidate validator rather than changing the public
SciCode schema. Add checks for:

- one-step/function-pair profile and valid optional multi-step profile;
- scientific task/function provenance and source fingerprint;
- no official SciCode or official test-data markers;
- public/private path separation and solver-payload redaction;
- private reference execution and HDF5 group/test-count agreement;
- mutation or metamorphic discriminators where feasible, so a reference is
  not accepted only because it returns a hard-coded value; and
- global source-fragment/problem fingerprint deduplication.

Keep the three historical skipped official subproblems isolated to official
benchmark loading code. New candidates must not rely on those exceptions.

### 3.4 Strict AvaCore run and trace audit

Reuse `eval/avacore/scicode_avacore.py`, run manifests, and trace checks. Add
only candidate-profile metadata needed to prove that the local one-step task
used the same strict solver contract. Record ordinary content, reasoning
content when returned, extracted code, finish reason, usage/token counts,
provider response, evaluator outcome, timeout/length status, and hashes.

Do not mark a candidate's reference as wrong merely because Kimi's answer
fails its private oracle. A complete failing answer is useful trace data; only
missing/truncated/unparseable/infrastructure-failed traces require rerun or
rejection from the trace dataset.

### 3.5 Delivery and aggregation

Extend the existing delivery exporter and multiprocess coordinator so that:

- each accepted one-step candidate contributes one row;
- each accepted multi-step candidate contributes one row per active step;
- registry keys include candidate revision, problem id, step number, trace hash,
  and source fingerprint;
- accepted rows contain the previous SFT fields plus provenance and trace
  metadata, but no private oracle/reference material;
- rejected candidates, review decisions, and revision history remain in the
  audit area; and
- batch aggregation stops at the configured row target without silently
  converting an incomplete trace into a sample.

## 4. Optional review tiers

Implement two explicit tiers in configuration:

1. **`smoke`**: deterministic gates plus one trace; used only to test the
   pipeline, never for release data.
2. **`calibration`**: production gates plus optional review-child and full trace
   review for a small sample. Never make this a prerequisite for the whole
   20,000-pair throughput target.

The first implementation must run a small production-profile batch and inspect
its trace fields before enabling higher concurrency.

## 5. Files expected to change after approval

The implementation should remain scoped to the new branch and should touch
only the following areas unless a test proves another file is necessary:

- `AGENTS.md` and `.agents/skills/scicode-task-authoring/SKILL.md` for the
  function-pair authoring contract;
- `src/scicode/pipeline/source_catalog.py` (new) for source/function intake;
- `src/scicode/pipeline/candidate.py` and `checks.py` for profile, provenance,
  deduplication, and oracle gates;
- `src/scicode/pipeline/pipeline.py`, `run_manifest.py`, and `trace_checks.py`
  for profile-aware handoff and trace status;
- `src/scicode/pipeline/samples.py`, `delivery.py`, and the corresponding
  scripts for one-step aggregation;
- `scripts/run_function_pair_batch_mp.py` or a narrowly scoped extension of
  `run_10k_batch_mp.py` for catalog-driven scheduling;
- tests for catalog extraction, deduplication, one-step schema, oracle
  isolation, trace completeness, delivery, and a full dry-run;
- docs describing the operator-only batch configuration and recovery commands.

Do not modify the upstream SciCode prompt templates or add solver tools.
Do not add a second model evaluator, a second database schema, or a separate
trace format.

## 6. Verification gates

Before any real model calls:

1. run the new unit tests and the existing full test suite;
2. run a local candidate fixture through schema, oracle, static, isolation,
   catalog, deduplication, and sample-export tests;
3. compare the generated one-step prompt with the existing strict prompt and
   prove the visible projection is unchanged;
4. run one real allowed-source candidate through strict AvaCore, PostgreSQL,
   JSONL export, and trace audit;
5. verify that a wrong-but-complete solver response is retained as a trace and
   that an incomplete response is rejected or retried; and
6. inspect one delivered row for all previous SFT fields, token/reasoning
   metadata, provenance, and absence of private oracle/reference content.

Only after these checks pass should the batch scheduler be scaled. The first
scale experiment should use a small target and bounded concurrency; it must
measure candidates per minute, complete traces per minute, rejected-candidate
rate, trace-incomplete rate, source deduplication rate, and disk per candidate.

## 7. Risks and decisions requiring approval

- **Quality versus throughput:** function-level one-step candidates increase
  throughput but can become shallow. The scientific-function gate, source
  provenance, private discriminating tests, and benchmark-tier calibration are
  the controls.
- **Review cost:** full child review for every pair is likely too expensive for
  20,000 pairs. The proposed production tier samples deep review while keeping
  hard deterministic gates universal.
- **Source parsing:** requiring a new parser dependency could make yicloud
  deployment brittle. Start with an optional parser and AST fallback.
- **Candidate granularity:** default to one function/one subproblem; permit
  multi-step only when the scientific dependency is explicit and recorded.
- **No official-data contamination:** keep the current forbidden-marker and
  provenance checks, and add a registry-level fingerprint check before delivery.

Implementation must not begin until this plan is approved. After approval,
start with the catalog and one-step fixture, then implement validation and
delivery changes, and only then run one real candidate end to end.
