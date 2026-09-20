# Race-safe batch reasoning factory

Date: 2026-09-21

## Goal

Scale the SciCode reasoning-trace factory from one curated repository to a
repeatable public-repository production funnel without letting discovery,
workers, retries, or aggregation race with one another. This work is about the
current SciCode factory; SciCodePile is used only as public design evidence and
is not treated as a local code dependency.

## Evidence adopted

ScienceIDE organizes construction around pinned scientific modules, fixed
scientific checks, reusable factories, and the rule “propose broadly; establish
validity by execution.” It separates whether a task is valid from whether it is
difficult for a model. Sources:

- https://arxiv.org/abs/2609.19134
- https://github.com/aitofound/ScienceIDE

SciCodePile uses a retrieve-then-filter funnel: curated scientific search
vocabulary, metadata filters, model-based README summaries, function-level
extraction, repository/code dual evidence, grounded generation, and different
models for high-volume screening versus late high-precision work. The expensive
stages see only progressively smaller candidate sets. Source:

- https://arxiv.org/abs/2607.19104
- https://huggingface.co/datasets/SciCodePile/SciCode-Domain-Code

GitHub Search has its own restrictive rate budget, so repository discovery is
serial, paginated, and backpressured; parallelism begins only after candidates
enter the durable queue. Sources:

- https://docs.github.com/en/rest/search/search
- https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api

## Pipeline

1. Load a versioned computational-science vocabulary; optionally expand it once
   with an LLM without deleting seed terms.
2. Search GitHub serially, filter cheap metadata, merge duplicate repository IDs,
   and rank candidates by independent keyword matches and stars. Persist
   exhausted per-query failures without throwing away successful queries.
   Reject strong documentation/list/curriculum signals here and retain a
   rejection ledger, before spending clone or model capacity.
3. Resolve the final candidates' mutable default branches to exact commit SHAs.
4. Enqueue one job per `(repository snapshot, canonical recipe)` in SQLite.
5. Atomically lease jobs to workers. Heartbeats keep long jobs alive; expired
   leases become retryable; permanent exhaustion is recorded, not hidden.
6. Under repository and model slot budgets, clone/fetch the pinned commit,
   screen the README into a fixed profile, check licensing, and mine functions.
7. Rank functions by repository-intent evidence multiplied by implementation
   evidence, while enforcing module diversity.
8. Run all three reasoning archetypes through task preflight, native-thinking
   rollout, outcome-independent trace grading, and thinking-preserving export.
9. Write every repository/recipe to a unique shard. Aggregate only completed
   shards, detect conflicting trace IDs, sort deterministically, and replace the
   final file atomically.

## Race and overload invariants

- `BEGIN IMMEDIATE` plus a conditional update gives exactly one active owner for
  a job lease on a single host.
- Jobs and resource slots both expire, so worker death cannot permanently wedge
  the queue.
- LLM concurrency is a queue-global slot pool, not a per-process semaphore.
  Starting more workers therefore does not multiply endpoint concurrency.
- Repository work has both a global capacity and a repository-specific mutex,
  protecting the shared clone cache.
- Slot cardinality is immutable after initialization; mismatched worker flags
  fail instead of silently oversubscribing.
- A source SHA and a recipe hash determine job identity. Same input/config is
  idempotent; changed models, prompts/config, or source snapshot create new work.
- Shards never share append-only JSONL files. Temporary-file replacement makes
  per-stage outputs and the final aggregate crash-safe.
- SQLite WAL is a single-host implementation. Multi-node operation requires a
  real network database implementing the same leases and uniqueness rules.

## Quality boundary

This architecture controls concurrency and provenance; it does not by itself
prove scientific difficulty. README screening and lexical dual-evidence ranking
reduce obviously irrelevant inputs. Reasoning-depth preflight and trace grading
then assess the generated task and trajectory. Before producing a training-scale
mixture, calibrate these judges on a human-reviewed sample, add stronger semantic
retrieval/verifier models if needed, and measure difficulty with a named solver
panel. Pass/fail remains auxiliary evidence rather than the SFT inclusion rule.
