# ADR-0001: Keep Kimi Code Authoring Separate from Strict AvaCore Solving

## Status

Accepted

## Context

The dataset target is 10,000 samples, where each sample is one ordered
SciCode subproblem paired with one solver trace. Two model-facing roles must
remain distinct:

- Kimi Code researches sources, creates candidate tasks, asks child agents to
  review them, revises candidates, and decides whether a candidate is ready.
- Kimi solves the candidate task. AvaCore is the only process that calls the
  deployed Kimi endpoint, evaluates code with the SciCode protocol, stores the
  trace in PostgreSQL, and exports JSONL.

The original SciCode prompt and visible context must remain unchanged. New
metadata may be added around a record, but no extra solver tools or hidden
test information may be exposed. Candidate work must run concurrently in
isolated Git worktrees and must be merged into a deterministic 10k delivery
registry.

## Decision

Use a strict, two-layer pipeline:

1. Kimi Code operates the imperative `AGENTS.md` workflow and its skills. Each
   candidate receives a fixed worktree/branch and produces a SciCode-shaped
   public record, private reference/oracle, provenance, and validation reports.
2. Kimi Code hands a complete candidate to the AvaCore skill. The skill runs
   the existing strict sequential SciCode adapter against the configured Kimi
   endpoint (`http://10.100.184.127:5050`), writes the PostgreSQL rollout, and
   exports the trace.
3. Kimi Code resumes with the trace and verifier summary, classifies failures,
   and either revises the candidate or calls the delivery skill.
4. The delivery skill validates and merges the candidate branch, converts each
   evaluated subproblem into exactly one deduplicated sample record, and stops
   at 10,000 accepted `(subproblem, trace)` samples.

Allow a quality run to become the formal run only when its release manifest
proves that the candidate, oracle, prompt profile, model settings, complete
step set, and trace/export are identical to the formal configuration.

## Consequences

### Positive

- The solver sees exactly the SciCode contract and no author-only material.
- Trace production, token accounting, scoring, and database persistence have a
  single implementation boundary.
- Parallel Kimi Code sessions cannot overwrite one another's worktrees or
  delivery records.
- A multi-step problem naturally produces one SFT sample per subproblem.
- A complete quality run can be reused without an unnecessary second model
  invocation.

### Negative

- The pipeline needs explicit handoff/resume state between Kimi Code and
  AvaCore.
- Candidate provenance, private oracle files, and trace metadata require more
  storage and validation than a plain JSONL dataset.
- External source research and provider failures require separate operational
  classifications instead of being hidden as task failures.

### Neutral

- Official Hugging Face SciCode loading remains available for benchmark
  reproduction, but candidate generation never uses official problems or
  official test data.
- Candidate branches remain in Git history even when their worktree copies are
  cleaned after delivery.

## Alternatives Considered

### Let Kimi Code call Kimi directly

Rejected. It would duplicate the evaluator, trace contract, retry behavior,
and token accounting, and would make AvaCore incomplete as the authoritative
trace store.

### Make Kimi Code an agentic solver with extra tools

Rejected for this pipeline. The requested dataset uses Strict SciCode only;
adding solver tools or extra context would change the benchmark contract.

### Store only whole-problem traces

Rejected. The target unit is an evaluated subproblem and its trace, so the
delivery layer must expand a multi-step problem into multiple samples.

## References

- `AGENTS.md`
- `eval/avacore/scicode_avacore.py`
- `eval/inspect_ai/scicode.py`
- `eval/data/multistep_template.txt`
