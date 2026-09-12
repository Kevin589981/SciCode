# SciCode 10k Authoring and Delivery Standard

Follow every instruction in this file as an operational command. Do not
replace a required action with an informal judgment. Do not skip a gate
because a candidate looks plausible.

## 1. Keep the two model roles separate

Treat **Kimi Code** as the authoring controller. Require Kimi Code to research
scientific sources, create a candidate, create and instruct review child
agents, run local checks, request the solver run, inspect the returned trace,
revise the candidate, and invoke delivery.

Treat **Kimi** as the solver model. Call Kimi only through the AvaCore skill
and the deployed OpenAI-compatible endpoint. Do not ask Kimi Code to solve the
candidate directly, do not call the endpoint from a candidate authoring script,
and do not substitute a Kimi Code transcript for a solver trace.

Use the term `Kimi Code` only for the controller process and its child agents.
Use the term `Kimi` only for the model invoked by AvaCore. Record both roles
separately in every run manifest.

## 2. Enforce the dataset target

Count one delivered sample as one tuple:

```text
(candidate_revision, problem_id, step_number, complete_solver_trace)
```

Count every non-skipped ordered SciCode subproblem separately. If one problem
has five subproblems and one complete solver episode, count five samples. Do
not count a whole problem as one sample. Stop delivery exactly at 10,000
accepted samples and record the remaining accepted candidates as overflow.

Keep rejected candidates, failed revisions, and overflow outside the delivered
training JSONL. Do not silently delete them.

## 3. Start every authoring session

Perform these actions before researching or editing a candidate:

1. Read `README.md`, both files under `eval/data/`,
   `eval/inspect_ai/scicode.py`, `eval/scripts/gencode.py`,
   `src/scicode/parse/parse.py`, and this file.
2. Read `D:/1/desktop/rl-new/kimi命令示例.md` when running on Windows, or its
   synchronized copy on yicloud.
3. Record the repository commit, Python version, package lock hash, and
   relevant AvaCore commit in `author/upstream_contract.md`.
4. Set the fixed workspace root. Use `/root/scicode-authoring` on yicloud
   unless the run configuration explicitly names another directory.
5. Create one candidate worktree through
   `.agents/skills/scicode-worktree/scripts/worktree.py`. Do not edit the
   shared checkout while a candidate worktree is active.
6. Keep credentials in environment variables or provider configuration. Never
   write API keys into a candidate file, trace, manifest, prompt, or commit.

Use the following network policy:

- Connect to the Kimi solver through the `BASE_URL` from the selected provider
  profile using the AvaCore skill. Do not replace a configured endpoint with a
  hard-coded fallback; record the redacted endpoint identity in the run
  manifest.
- Reach GitHub, arXiv, Hugging Face, and Docker Hub through
  `http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128`.
- Reach approved domestic services directly when the yicloud network allows
  it.
- Scope proxy variables to the command that needs them. Do not bake proxy
  values into task prompts or public metadata.

## 4. Use one fixed candidate layout

Treat the Git worktree as a complete repository checkout. Create exactly this
layout below the worktree for `CANDIDATE_ID`; set `CANDIDATE_DIR` to this
nested directory when invoking validation, AvaCore, or delivery:

```text
<worktree>/authoring/CANDIDATE_ID/
  candidate.json                    # metadata-only manifest
  public/problem.jsonl               # canonical SciCode record
  public/solver_payload/             # redacted solver-visible files
  public/checks/                     # public scientific checks only
  public/prompt_snapshot/            # rendered prompt snapshots
  public/README.md                   # visible-file inventory
  source_notes/provenance.json       # URLs, commits, papers, licenses
  source_notes/                      # public source notes
  author/                            # private design and review records
  reference/                         # private reference implementations
  oracle/                            # private HDF5 targets and verifier code
  iterations/                        # every revision and solver evidence
  validation/                        # gate reports and release decision
  runs/                              # AvaCore run manifests and exports
```

Place private files outside `public/`. Materialize a clean solver directory
from an explicit allowlist. Never mount `problem.jsonl`, `reference/`,
`oracle/`, private tests, target values, author notes, prior runs, or another
candidate into that directory.

## 5. Research a scientific source

Search GitHub, arXiv, Hugging Face, or another approved scientific source.
Record the original repository URL, pinned commit, license, paper URL or DOI,
retrieved files, and a source-fragment fingerprint in
`source_notes/provenance.json`.

Use a source to derive a realistic scientific computation, not a repository
maintenance issue. Extract a coherent function-level workflow that matches
SciCode's question-and-answer format. Do not expose an entire repository as a
solver task.

Do not use an official SciCode problem, official SciCode prompt, official
SciCode test case, or `eval/data/test_data.h5` as a candidate seed, example,
oracle input, or solver context. Reject a candidate that duplicates an
official problem, an existing candidate, or a training-data record. Treat
different random constants for the same scientific workflow as the same task.

Clone only the source files needed for research. After the candidate is
complete, remove unnecessary upstream source, build products, caches, and
large artifacts from the candidate worktree. Retain provenance, the minimal
scientific source note, and task artifacts.

## 6. Preserve the SciCode solver contract

Write one canonical JSONL record using the original SciCode top-level fields:

```text
problem_name
problem_id
problem_description_main
problem_io
required_dependencies
sub_steps
general_tests
problem_background_main
```

Give every `sub_steps` item these fields:

```text
step_number
step_description_prompt
function_header
test_cases
return_line
step_background
```

Keep the solver-visible prompt content, ordering, dependency declarations,
previous-code handoff, code-block requirement, and code-extraction behavior
identical to the checked-in SciCode adapter. Add metadata only outside the
solver-visible prompt fields. Do not add a tool, shell command, public test
feedback channel, hidden hint, extra background paragraph, or alternate
solver mode.

Create ordered subproblems that form a real scientific dependency chain.
Require at least one nontrivial scientific concept, numerical or simulation
behavior, unit/shape convention, boundary condition, or multi-path check.
Reject syntax exercises, toy arithmetic, generic data manipulation, simple
bug fixes, API renames, path/install fixes, and non-scientific logic changes.

Generate a candidate-owned HDF5 oracle that follows the parser contract at
`src/scicode/parse/parse.py`. Generate targets from a private reference
implementation. Validate the reference, an independently structured correct
implementation, and a plausible scientifically wrong implementation. Require
the first two to pass all private tests and the wrong implementation to fail
for the intended scientific reason.

## 7. Build and inspect a candidate

Perform these actions in order for every revision:

1. Write the scientific basis, decomposition, provenance, source snapshot,
   reference implementation, independent implementation, wrong
   implementation, oracle generator, and candidate manifest.
2. Derive `public/solver_payload/` from an allowlist. Remove evaluator
   assertions and all private values from the payload.
3. Render one fixed prompt profile. Store every prompt snapshot and verify
   that the renderer matches the upstream SciCode adapter.
4. Run `scripts/validate_candidate.py` before any model request.
5. Ask Kimi Code to create a fresh review child agent. Give the child only the
   candidate's public payload, public sources, and validation contract. Require
   it to inspect scientific correctness, clarity, leakage, difficulty, source
   provenance, subproblem dependencies, and oracle independence. Do not give
   it private targets or reference code.
6. Record the child-agent request, response, decision, and event identifier in
   `validation/review_child.json`.
7. Reject or revise immediately when a schema, dependency, prompt, leakage,
   scientific, or clarity check fails. Record the evidence and do not hide the
   failure.

Do not let Kimi Code modify the shared SciCode public structure to make one
candidate fit. Permit experiments only inside the candidate worktree or an
isolated disposable copy. Run those experiments with `uv` and discard them
unless they are framework changes approved outside the candidate workflow.

## 8. Hand the candidate to AvaCore

After the child review and local gates pass, invoke
`.agents/skills/scicode-avacore-run/SKILL.md`. Do not call Kimi directly.
The skill must:

1. Close the current authoring phase without destroying its Kimi Code session.
2. Run `eval/avacore/scicode_avacore.py` in strict mode with the candidate's
   `public/problem.jsonl` and private HDF5 oracle.
3. Use the configured provider profile's `BASE_URL`, model and key, the final
   sampling parameters, a 7,200-second request timeout, and the configured
   retry policy. Require `POSTGRES` before submission and pass it through the
   AvaCore handoff wrapper without writing credentials to artifacts.
4. Run every ordered non-skipped subproblem. Do not use `--max-steps` in a
   quality or formal run.
5. Store the rollout in AvaCore PostgreSQL and export `rollouts.jsonl`.
6. Record ordinary content, reasoning content, raw provider response when
   available, code extraction, token usage, finish reason, retries, timeout,
   and output-length termination.
7. Return the run manifest and exported trace path to Kimi Code. Resume the
   authoring session with `kimi -r SESSION_ID -p "..."` when the configured
   handoff requires an explicit resume command.

Use strict mode only. Do not start Kimi Code CLI, a shell agent, a child tool
agent, or a public-test feedback loop as the solver. Do not alter the prompt or
add tools for a failed answer.

Treat a 7,200-second timeout as a solver-run failure first. Retry the same
candidate up to three times when the failure is transient or the first run did
not produce a complete trace. Stop retrying early when the first run already
produced a complete, auditable trace. Treat provider termination caused by the
262,144-token deployment limit as a normal `length` trace status and continue
to quality review; do not call it a task failure solely for that reason.

Classify provider/network/queue errors as infrastructure errors. Do not use
them as evidence that a scientific task is too hard or poorly designed.

## 9. Analyze the returned solver trace

Give Kimi Code the complete exported trace and the structured verifier summary
after the run closes. Permit Kimi Code to give a child review agent the same
trace and the public candidate, but do not expose private target values,
private test code, or oracle files.

Require Kimi Code to inspect each subproblem separately and record:

- prompt and previous-code continuity;
- ordinary content and reasoning content presence;
- code extraction and syntax status;
- finish reason, timeout/length status, retries, and token counts;
- scientific reasoning quality and likely misconception;
- evaluator outcome, without using evaluator output as a solver hint;
- whether the trace is complete enough to count as one sample.

Do not require the Kimi answer to be correct for the trace-quality gate. Accept
a wrong answer as a normal trace when the prompt was faithful, the model
response and reasoning are preserved, the code extraction is auditable, and
the termination status is explicit. Require candidate correctness separately:
the reference/oracle and independent implementation must pass, and the task
must contain no scientific loophole.

Classify every candidate failure using evidence from trace event IDs. Use only
one of these classifications: incomplete scientific definition, incorrect
scientific semantics, unit/shape convention, boundary or numerical stability,
broken subproblem dependency, output-format failure, infrastructure failure,
or trace incompleteness.

## 10. Revise with evidence

When a candidate fails, write the diagnosis in
`iterations/<revision>/analysis.md`, identify the exact public or private
evidence, increment the revision, and change only fields justified by that
evidence. Regenerate the payload, prompt snapshots, oracle manifest, and
validation reports. Delete the old solver workspace before the next run.

Do not paste a hidden target, reference formula, private-test intent, or
diagnosis into a solver prompt. Do not fix a solver failure by making the task
easier without recording the scientific reason. Repeat child review and
AvaCore execution after every substantive revision.

Allow unlimited revisions when evidence supports them. After repeated failed
revisions, require Kimi Code to redesign the scientific workflow or reject the
candidate; do not loop forever on wording-only changes.

## 11. Promote one quality run to formal evaluation

Use one run for both quality evidence and formal evaluation only when its
manifest proves all of these conditions:

- the candidate revision, public record hash, payload hash, oracle hash, and
  provenance hash match the release candidate;
- the strict prompt profile and all model/sampling/timeout settings match the
  release configuration;
- every ordered non-skipped subproblem ran and has a complete trace;
- the private verifier passed for the reference and independent implementation
  and rejected the wrong implementation;
- no private file was visible to Kimi;
- AvaCore PostgreSQL persistence succeeded and `rollouts.jsonl` was exported;
- ordinary content, reasoning, usage, finish status, and code extraction are
  present for every counted sample.

Mark a debug-limited, alternate-configured, incomplete, or privately mounted
run as `qa_only`. Run the formal configuration separately. Never merge scores
from QA-only and formal runs.

## 12. Deliver subproblem samples

Invoke `.agents/skills/scicode-delivery/SKILL.md` only after the release gate
passes. Require the skill to:

1. Re-run schema, static, oracle, provenance, leakage, trace, and runtime
   checks from the candidate worktree. Run `scripts/check_trace.py` with
   `--require-usage` before accepting an exported run.
2. Acquire the delivery lock and inspect the registry before writing.
3. Convert each complete non-skipped subproblem trace into exactly one record
   using the SFT-compatible fields:

   ```text
   id
   messages
   prompt
   completion
   completion_with_reasoning
   reasoning_content
   parsed_code
   context_code
   provider_response
   usage
   metadata
   ```

4. Preserve candidate provenance, revision, run id, score metadata, finish
   reason, token counts, and trace path inside `metadata`.
5. Deduplicate by candidate revision, problem id, step number, and trace hash.
6. Atomically append accepted records to the delivery registry and stop at
   exactly 10,000 records.
7. Commit the candidate branch and merge it into the configured integration
   branch. Resolve conflicts without touching another active worktree. Record
   unresolved candidate IDs and leave their branches intact.
8. Clean the fixed worktree copy only after merge and commit. Keep the branch
   and history. Do not push unless a human explicitly requests it.
9. Write a deterministic summary table containing candidate id, revision,
   source, license, problem id, step number, trace status, token totals,
   evaluator status, acceptance decision, and rejection reason.

Do not place private oracle files, reference implementations, API keys, or
author-only diagnoses in the final SFT JSONL. Keep full private evidence in
the candidate archive and database with the access policy configured by the
operator.

## 13. Stop conditions

Reject the candidate when any of these conditions holds:

- it uses official SciCode data or duplicates an existing/training task;
- it is a simple bug fix, toy calculation, non-scientific task, or shallow API
  exercise;
- its scientific definition or units are incomplete or contradictory;
- its oracle is not independently justified;
- a plausible scientifically wrong implementation passes;
- the solver needs private material or extra tools;
- the public payload leaks targets or evaluator intent;
- the trace cannot be tied to the exact candidate revision;
- the worktree or delivery lock cannot be safely acquired;
- the candidate cannot be reproduced offline after source retrieval.

Pause only for an actual missing credential, unavailable yicloud resource, or
unresolvable merge conflict. Record the blocker and preserve all artifacts.

## 14. Required final records

Before marking a candidate accepted, require all of these files:

```text
author/upstream_contract.md
author/scientific_basis.md
author/decomposition.md
source_notes/provenance.json
candidate.json
public/problem.jsonl
public/README.md
validation/schema_report.json
validation/static_report.json
validation/oracle_report.json
validation/review_child.json
validation/leakage_report.json
validation/release_decision.json
runs/<run_id>/manifest.json
runs/<run_id>/rollouts.jsonl
validation/trace_report.json
```

Write the exact candidate revision, source commit, hashes, mode (`strict`),
Kimi model, AvaCore version, evaluator status, sample count, and acceptance
reason in `validation/release_decision.json`.

## 15. Do not violate these rules

- Do not use `inspect_ai` or the official `test_data.h5` as a prerequisite for
  generating a new candidate.
- Do not change the model-visible SciCode prompt contract for one candidate.
- Do not add public solver tools or test feedback.
- Do not let Kimi Code call the Kimi solver outside AvaCore.
- Do not let a child agent read private oracle/reference materials.
- Do not overwrite another candidate's branch, worktree, run directory, or
  delivery registry.
- Do not discard failed traces, timeout traces, or rejected candidates.
- Do not silently count a partial trace as a complete `(subproblem, trace)`
  sample.
- Do not push branches automatically.
