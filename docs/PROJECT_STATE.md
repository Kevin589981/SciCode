# SciCode Reasoning-Trace Factory — Project State

Last updated: 2026-09-21

This file is the durable source of truth for project goals, decisions, progress,
and constraints. Update it whenever a design decision or milestone changes.

## Objective

Learn from ScienceIDE's automated production philosophy and build a SciCode
factory that automatically creates scientifically meaningful problems, collects
long-form solver reasoning traces, and exports training-ready SFT data.

The goal is not to reproduce ScienceIDE's benchmark, repair environments, or RL
stack. ScienceIDE contributes the general pattern of broad proposal, measured
evaluation, iterative filtering, and reproducible data production.

## Primary Product

The primary product is a high-value reasoning trace, not a binary pass/fail QA
record. A useful trace may fail its final executable check if it contains sound,
non-degenerate scientific reasoning, productive exploration, or meaningful
self-correction. A passing trace may still be rejected if the task or reasoning
is trivial.

Thinking/reasoning content is first-class training data and must survive raw
capture, normalization, grading, selection, and SFT export. Do not copy any
training path that silently drops `reasoning_content`.

## Confirmed Design Principles

1. Separate task validity from trace training value.
   - Task validity removes broken, underspecified, or scientifically incoherent
     problems.
   - Trace value measures scientific reasoning quality independently of final
     pass/fail outcome.
2. Executable checks, assertions, tolerances, and rewards are auxiliary evidence.
   They are not the definition of scientific reasoning or the main SFT filter.
3. Do not export SFT using `reward == 1` as the principal selection rule.
4. Preserve thinking and support explicit loss masks for useful reasoning,
   historical failed actions, corrections, and final answers.
5. Optimize task generation for cognitive depth, not function length, branch
   count, call depth, or difficulty of satisfying three numeric tests.
6. Long reasoning is valuable only when it is coherent and scientifically
   grounded. Repetition, dead loops, unsupported claims, and confidently wrong
   premises are not made valuable merely by length.
7. DeepSeek/Kimi artifacts produced so far are smoke outputs only. They confirm
   that the plumbing runs; they are not authoritative difficulty measurements.
8. A standalone ScienceIDE-style student environment is not required for the
   SciCode SFT objective.
9. Source-code memorization/leakage is not a primary SFT-quality criterion here.
   Generalization and provenance still remain useful metadata, but they must not
   displace reasoning-quality work.

## Current State

- Canonical local repository: `D:/1/desktop/scienceIDE/SciCode`
- Canonical branch: `scienceide-pipeline`
- Reasoning-first v1 baseline commit: `0c1c306`
- Batch v2 synchronization point: current `scienceide-pipeline` branch head
- Active development worktree:
  `D:/1/desktop/scienceIDE/scicode-work/reasoning-factory-wt`
- Active development branch: `feature/reasoning-trace-factory-v1`
- Remote execution checkout: `/root/ScienceIDE-workspace/SciCode`
- Factory code: `factory/`
- Existing pipeline: vet -> mine -> propose -> verify -> calibrate -> harvest ->
  solve, with separate trace grading.
- Existing data is dominated by single-function SciPy implementation tasks with
  three generated test inputs. This is considered a smoke baseline, not the
  target task distribution.
- Raw traces and canonical SFT preserve Kimi-K3 `reasoning_content` with
  separate content/reasoning loss masks.
- SFT selection is driven by scientific trace value rather than full reward.
- Multi-step call-chain generation is experimental and does not by itself solve
  the reasoning-depth problem.

## Code Synchronization Contract

The local Git branch is the single source of truth. Changes must be committed and
pushed before the remote execution checkout is advanced to the exact same commit.
Each smoke run must record at least:

- factory Git commit;
- task-set identifier/hash;
- author, solver, and judge model identifiers;
- generation parameters and context/token budgets;
- raw trace and exported dataset paths.

Do not rely on ad-hoc tar synchronization for final experiments.

## Redesign Direction and Implementation Progress

The next factory version must add:

1. reasoning-demand task archetypes rather than only leaf-function imitation;
2. task specifications that declare required cognitive operations;
3. a task-depth preflight gate independent of executable correctness;
4. outcome-independent trace-quality grading;
5. thinking-preserving, quality-driven SFT export with explicit masks;
6. tests for schemas, selection policy, reasoning preservation, and resume/data
   provenance;
7. a Kimi-K3 16k-context smoke test after the local and remote code are synced.

Implemented on `feature/reasoning-trace-factory-v1`:

- `6dfb708`: canonical task/trace/grade schemas and stable hashing;
- `5a09a4a`: three-archetype reasoning task composition;
- `5ea89c5`: deterministic plus semantic task-depth preflight;
- `b52e9c0`: native-thinking rollout collection with auxiliary outcomes;
- `e64ec79`: outcome-independent trace-value grading;
- `09c8f48`: thinking-preserving SFT export with separate masks.

Reasoning-first v1 is complete and synchronized. The three-task Kimi-K3 16k
smoke produced one selected SFT row for every archetype (3 tasks, 3 admitted
preflights, 3 traces, 3 grades, 3 SFT rows). All three auxiliary executable
outcomes were `not_run`; this is acceptable because outcome is not the trace
selection criterion. The SFT artifact SHA-256 is
`a1be8bc6f6e177ef2f60ce93c6a4d5704e6c409ac447600f93edb43df99851de`.

Batch production v2 is implemented locally:

- curated/optionally expanded scientific query discovery with serial,
  rate-limit-aware GitHub Search;
- high-precision `name,description` discovery by default, with README-wide
  retrieval retained as an explicit recall-oriented mode;
- metadata filtering and repository-ID deduplication before clone or LLM use;
- high-confidence documentation/list/curriculum rejection with an auditable
  rejection ledger before clone or LLM use;
- immutable default-branch commit pinning for admitted catalog entries;
- per-query/per-repository discovery error ledgers so one deleted or transiently
  unavailable candidate does not discard an otherwise valid batch;
- SQLite transactional job/resource leases, expiry recovery, heartbeats,
  retries, and event audit; portable rollback journaling is the default and WAL
  is opt-in on compatible local filesystems;
- queue-global LLM/repository slots plus a per-repository mutex;
- fixed-schema repository screening, license gate, AST mining, and
  repository-intent × code-evidence ranking;
- job identity over source snapshot and canonical production recipe;
- isolated repository/job artifact shards and deterministic atomic aggregation;
- split enqueue/worker/status/aggregate commands and a bounded `auto` command.

Offline verification currently passes 61 tests plus 6 subtests. The v2 commits
have been pushed and the `yicloud` checkout kept synchronized. A bounded live
GitHub smoke resolved exact SHAs with no API errors; after the high-precision
scope and documentation filters were applied, `CURENT/andes` was the leading
candidate and the awesome-list result was recorded in the rejection ledger.
Quality/difficulty v3 is implemented, pushed, and synchronized to `yicloud`:

- critic-aware preflight IDs prevent reuse across different critic models;
- independent task verification requires two exact quotes grounded in the
  private source and scores validity, answerability, consistency, and shortcut
  resistance;
- rollout admission is the intersection of depth and source-verification gates;
- trace IDs include the full sampling variant, so temperature/panel changes do
  not silently reuse old generations;
- multiple judge models can grade the same trace while the primary judge still
  defines candidate-SFT masks;
- named-solver panels report Wilson intervals and require multiple distinct
  model IDs/trials; zero solve rate is `unresolved`, not `hard`;
- deterministic stratified human-audit packets and calibration metrics cover
  precision, recall, agreement, critical errors, scientific depth, and trace
  value;
- release SFT fails closed without population-matched human approval,
  calibrated medium/hard difficulty, source verification, and multi-judge
  consensus;
- candidate export recomputes the current preflight/verifier intersection, so
  a historically graded trace cannot re-enter after a newer task gate fails;
- author/solver, critic, verifier, and judge token budgets are independent, so
  a 16k reasoning budget does not multiply every admission-stage request;
- batch aggregation emits the tasks, preflights, verifications, traces, and
  grades required to reproduce those gates rather than only a candidate SFT.

The live Kimi smoke in `data-reasoning-smoke-v1/` exercised these boundaries:

- all three tasks passed the critic-aware depth preflight;
- two tasks passed exact-source verification; the third verifier response was
  incomplete at the 4096-token boundary and was recorded as an error, not
  admitted;
- gated export produced three selected historical/current traces, one
  grade-rejected trace, and excluded the unverified task's historical trace;
- the five-row blind audit packet contains no model, outcome, provenance, or
  automatic-decision fields; its separate key has three automatic positives
  and two negatives, with no unverified positive;
- the one-model/no-assessment diagnostic labels all three tasks
  `uncalibrated`; it is explicitly not a measured difficulty result;
- empty human reviews produce `release_approved: false`, and the release
  command exits with `human calibration has not approved release`.

The remaining evidence is deliberately external: real human labels and a
genuinely distinct solver/evaluator/judge panel. They must not be simulated
with repeated Kimi aliases.

## Smoke Endpoint

- Base URL: `http://10.100.184.127:5050/v1`
- Model: `Kimi-K3`
- The endpoint is slow. Use a 16k output budget and a sufficiently long request
  timeout. Do not put credentials or generated traces in Git.

## Resolved Design Decision

The first smoke uses a small heterogeneous core: one `derive_implement`, one
`diagnose_revise`, and one `compare_justify` task through a common schema. This
prevents the redesign from replacing the old function-implementation monoculture
with a different single archetype.

