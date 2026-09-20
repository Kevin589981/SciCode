# SciCode Reasoning-Trace Factory v1 Design

## Requirements

The factory must turn scientific source material into tasks that demand
scientific reasoning, collect the solver's native thinking, evaluate the value
of the trace independently from its executable outcome, and export an auditable
SFT-ready JSONL. Version 1 supports three task archetypes: derive-and-implement,
diagnose-and-revise, and compare-and-justify.

The raw trace is the primary artifact. `reasoning_content` must be retained
verbatim. A failed answer may be trainable and a passing answer may be rejected.
Executable checks are optional evidence; they do not control selection. The
existing single-function factory and its smoke artifacts remain available and
unchanged while the reasoning pipeline is introduced beside it.

The implementation must be deterministic except for explicit LLM calls,
restartable without duplicating completed records, covered by offline tests, and
traceable to a Git commit and task-set hash. It must run with an OpenAI-compatible
endpoint and support the slow Kimi-K3 service with a 16k response budget and long
timeouts.

## High-Level Architecture

```text
mined scientific source
        |
        v
reasoning task composer ----> deterministic schema/depth gate
        |                              |
        |                              +---- reject shallow/broken proposals
        v
reasoning task JSONL
        |
        v
solver rollout (native reasoning_content + final content)
        |
        +---- optional executable outcome metadata
        v
raw trace JSONL
        |
        v
trace-value judge (outcome-independent multidimensional rubric)
        |
        v
grade JSONL + per-message supervision decisions
        |
        v
thinking-preserving canonical SFT JSONL
```

The pipeline uses append-only JSONL boundaries. Each stage can therefore be
tested separately, resumed safely, and inspected without importing a training
framework. The canonical format preserves `reasoning_content`, `content`, and
separate reasoning/content loss decisions. Trainer-specific conversion is a
later adapter concern and must not destroy the canonical record.

## Task Schema and Archetypes

Every task uses `scicode-reasoning-task-v1` and contains a stable `task_id`, an
archetype, source provenance, the user-facing problem, a deliverable contract,
and a reasoning contract. The reasoning contract names required cognitive
operations, scientific concepts, evidence expected in a strong trace, plausible
failure modes, and shortcuts that would make the task shallow.

The three v1 archetypes share one schema:

- `derive_implement`: derive a method from assumptions, state numerical or
  scientific constraints, then implement the result.
- `diagnose_revise`: reason from a scientifically meaningful failure symptom,
  identify the failed assumption or method, and construct a revision.
- `compare_justify`: compare at least two plausible methods or models under
  explicit regimes, justify a choice, then produce the requested implementation
  or analysis.

Archetype prompts must not merely request longer code. They must make the
cognitive operations observable in the task specification. The deterministic
gate requires multiple distinct reasoning operations and scientific concepts,
archetype-specific fields, a nontrivial deliverable, and sufficient prompt
content. An optional LLM preflight critic can add a depth score, but deterministic
schema checks remain authoritative for malformed records.

## Trace Collection and Value Grading

The rollout stores the full request/response messages returned by the endpoint,
including native `reasoning_content`. It records task hash, factory commit,
model, temperature, token budget, endpoint-independent timing, usage, and an
optional outcome object. Outcome is never converted directly into trainability.

The trace-value judge scores scientific validity, causal coherence, strategy and
method choice, use of assumptions/evidence, self-correction, insight density,
and degeneracy. It also emits a decision for each assistant message:
`train_reasoning` and `train_content`. This permits a failed final result with a
strong reasoning path to remain trainable, while excluding a lucky pass or a
repetitive/confidently wrong trajectory.

The judge consumes the complete reasoning up to a configurable input limit; it
must not use the old short digest as the evidence source. A deterministic
post-validator checks score ranges, message indices, and decision completeness.
Judge failure leaves the raw trace intact and records an error instead of
silently labeling the trace bad.

## SFT Export

The canonical exporter joins traces and grades by `trace_id`. It exports only
records whose grade says `trainable=true`, regardless of pass/fail. System and
user messages are context-only. Assistant records retain their original
`reasoning_content` and final `content`, together with explicit `reasoning_loss`,
`content_loss`, and a compatibility `loss` flag.

Canonical export is deliberately trainer-neutral. An optional inline adapter may
materialize thinking as `<think>...</think>` for a trainer that cannot consume a
separate field, but the preserved canonical JSONL remains the source of truth.
No transformation may silently drop thinking. Export reports include selected
and rejected counts by archetype, outcome, and quality reason.

## Reliability, Security, and Operations

All input JSON is treated as untrusted: schemas reject unknown structural types,
LLM JSON is parsed without executing generated strings, and endpoint credentials
remain in environment variables. Output writes use temporary files for final
reports and append+flush for expensive LLM results. Resume keys include task or
trace IDs rather than merely checking that a file exists.

The local Git branch is authoritative. After tests pass, commits are pushed and
the remote checkout is advanced to the same commit without deleting generated
data. Smoke manifests record code commit, source/task hashes, models, parameters,
and artifact paths. LLM errors are retried per request; malformed author or judge
responses are recorded for selective retry.

## Decisions and Trade-offs

1. **Parallel v1 pipeline instead of rewriting legacy code.** This reduces
   regression risk and lets old smoke data remain comparable, at the cost of a
   small amount of duplicated CLI orchestration.
2. **One shared schema for three archetypes.** This prevents a new single-task
   monoculture while keeping grading/export uniform. A fully generic task DSL is
   deferred.
3. **LLM judge plus deterministic validation.** Reasoning value cannot be reduced
   to asserts, but an unconstrained judge is not reproducible. Schema and range
   validation surround the semantic judge.
4. **Canonical reasoning-aware data before trainer integration.** Different
   models encode thinking differently. Preserving evidence now is safer than
   prematurely coupling the factory to one chat template.

## Test and Smoke Strategy

Offline unit tests cover task schema validation, all three archetypes, shallow
task rejection, raw reasoning preservation, outcome-independent selection,
per-message masks, malformed judge output, resume behavior, and deterministic
hashing. A fake OpenAI-compatible client fixture exercises author/solver/judge
without network access.

After local tests, the same commit is checked out on `yicloud`. The first live
smoke selects three scientific source candidates, produces one task per
archetype, runs deterministic preflight, collects one Kimi-K3 rollout per task
with `max_tokens=16384`, grades the complete trace, and exports canonical SFT.
Success means all pipeline artifacts are structurally valid and thinking
survives byte-for-byte. It does not mean the three tasks establish dataset
quality or difficulty.

