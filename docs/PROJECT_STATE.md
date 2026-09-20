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
- Last pre-redesign commit: `86a69ba`
- Remote execution checkout: `/root/ScienceIDE-workspace/SciCode`
- Factory code: `factory/`
- Existing pipeline: vet -> mine -> propose -> verify -> calibrate -> harvest ->
  solve, with separate trace grading.
- Existing data is dominated by single-function SciPy implementation tasks with
  three generated test inputs. This is considered a smoke baseline, not the
  target task distribution.
- Existing raw traces preserve Kimi-K3 `reasoning_content`.
- Existing SFT export incorrectly couples selection to full reward and marks
  every assistant response as supervised.
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

## Redesign Direction

The next factory version must add:

1. reasoning-demand task archetypes rather than only leaf-function imitation;
2. task specifications that declare required cognitive operations;
3. a task-depth preflight gate independent of executable correctness;
4. outcome-independent trace-quality grading;
5. thinking-preserving, quality-driven SFT export with explicit masks;
6. tests for schemas, selection policy, reasoning preservation, and resume/data
   provenance;
7. a Kimi-K3 16k-context smoke test after the local and remote code are synced.

## Smoke Endpoint

- Base URL: `http://10.100.184.127:5050/v1`
- Model: `Kimi-K3`
- The endpoint is slow. Use a 16k output budget and a sufficiently long request
  timeout. Do not put credentials or generated traces in Git.

## Open Design Decision

Choose the first reasoning-task family to implement and use for the initial 16k
smoke. The choice determines the task schema, author prompt, grader rubric, and
test fixtures.

