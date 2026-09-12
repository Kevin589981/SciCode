# SciCode Candidate Authoring Instructions

Follow every instruction in this file as an operational command. This file is
for the **Kimi Code authoring controller** and its review child agents. Do not
treat it as the implementation specification for AvaCore, the evaluator, or
the delivery service. Use the repository skills as black-box interfaces for
those components.

## 1. Keep the roles separate

Act as Kimi Code, the scientific task-authoring controller. Research sources,
design one candidate, create its artifacts, ask a fresh child agent to review
the public task, run the local checks, hand the candidate to the AvaCore skill,
inspect the returned solver trace, revise when the evidence identifies a task
defect, and request delivery only after the candidate is accepted.

Treat Kimi as the solver model. Never solve the candidate in place of Kimi and
never use your own answer or your control transcript as a solver trace. Invoke
Kimi only through `.agents/skills/scicode-avacore-run/SKILL.md`; never call the
provider endpoint from a candidate script, a shell shortcut, or a child agent.

Treat a review child as another Kimi Code authoring agent, not as the solver.
Give it public material only. Keep framework implementation concerns out of
candidate prompts and out of your scientific decisions. Do not invent a
second protocol for them; invoke the relevant skill.

## 2. Start a candidate session

Before researching or editing, perform these actions:

1. Read `README.md`, the two files under `eval/data/`,
   `eval/inspect_ai/scicode.py`, `eval/scripts/gencode.py`,
   `src/scicode/parse/parse.py`, this file, and every skill you invoke.
2. Read `D:/1/desktop/rl-new/kimi命令示例.md` on Windows or its synchronized
   copy on yicloud before using Kimi Code commands.
3. Inspect the repository branch and worktree status. Allocate one dedicated
   candidate worktree with `.agents/skills/scicode-worktree/`; do not edit the
   shared checkout while that worktree is active.
4. Keep provider credentials in environment variables or provider
   configuration. Never write credentials into a prompt, source note, trace,
   manifest, or commit.
5. Use the configured provider profile and its `BASE_URL`. Scope the corporate
   proxy to source retrieval commands only.

## 3. Research and design the scientific task

Search GitHub, arXiv, Hugging Face, or another approved scientific source.
Record the original repository URL, immutable source commit, license, paper
URL or DOI, retrieved files, and a source-fragment fingerprint in
`source_notes/provenance.json`.

Derive a realistic function-level scientific computation that fits the
SciCode question-and-answer format. Do not expose an entire repository as a
solver task. Do not create a maintenance issue, toy arithmetic exercise,
generic data transformation, syntax exercise, API rename, installation fix,
or simple bug repair.

Do not use an official SciCode problem, official SciCode prompt, official
SciCode test case, or `eval/data/test_data.h5` as a seed, example, oracle
input, or solver context. Reject semantic duplicates of official tasks,
existing candidates, or training records. Changing only random constants does
not make the scientific workflow a new task.

Clone only the source files needed for research. Remove unnecessary upstream
source, build products, caches, and large artifacts after the candidate is
complete, while retaining provenance and the minimal scientific source note.

## 4. Preserve the original SciCode contract

Write exactly the original top-level fields in `public/problem.jsonl`:

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

Write exactly these fields for every `sub_steps` item:

```text
step_number
step_description_prompt
function_header
test_cases
return_line
step_background
```

Keep the model-visible prompt content, ordering, dependency declaration,
previous-code handoff, code-block requirement, and code-extraction behavior
identical to the checked-in SciCode adapter. Put authoring metadata outside
those solver-visible fields. Do not add tools, shell access, public-test
feedback, hidden hints, extra background paragraphs, or an alternate solver
mode. Use strict SciCode sequencing only.

Construct an ordered scientific dependency chain. Require nontrivial scientific
semantics, numerical or simulation behavior, unit or shape conventions,
boundary conditions, or meaningful multi-path checks. State the assumptions,
domain, units, shapes, edge cases, and expected return values precisely.

Keep private material outside `public/`. The solver must not receive private
tests, target values, `reference/`, `oracle/`, author notes, or prior runs.
Use a redacted allowlist for `public/solver_payload/` and verify that it cannot
reveal evaluator intent.

## 5. Build the candidate and its oracle

Perform these actions in order for every revision:

1. Write the scientific basis, decomposition, provenance, public record,
   reference implementation, independently structured correct implementation,
   plausible scientifically wrong implementation, oracle generator, and
   candidate manifest.
2. Freeze the reference implementation, then run
   `python authoring/$CANDIDATE_ID/oracle/generate_targets.py` whenever the
   candidate oracle is absent. Keep the generated HDF5 file private and never
   replace it with the official oracle.
3. Derive `public/solver_payload/` from an explicit allowlist. Remove private
   values, evaluator assertions, and author-only diagnostics.
4. Render the fixed strict prompt snapshots and compare them with the
   checked-in upstream adapter.
5. Run `scripts/validate_candidate.py` and the candidate check skill before
   requesting any model run.

Require the reference and the independent implementation to pass every
private test. Require the plausible wrong implementation to fail for the
intended scientific reason. Record the evidence; do not replace a failed
scientific check with a weaker test merely to make the candidate pass.

Do not modify the shared SciCode public structure to fit one candidate. Make
experiments only inside this candidate worktree or an isolated disposable
copy, run them with `uv`, and discard them unless a framework owner explicitly
approves a framework change.

## 6. Run the public-only review

After local checks pass, create one fresh Kimi Code child agent. Provide only
the candidate's public payload, public source notes, prompt snapshots, and the
validation contract. Do not provide private reference code, private tests,
oracle files, target values, or prior private diagnoses.

Require the child to inspect, with evidence:

- scientific semantics, assumptions, units, shapes, and boundary behavior;
- clarity and difficulty at the level expected of SciCode;
- ordered subproblem dependencies;
- source provenance and license;
- prompt-contract fidelity;
- leakage of targets or evaluator intent;
- independence of the oracle design as far as public evidence permits.

Save the child request, response, decision, and event/session identifier in
`validation/review_child.json`. Reject or revise immediately when a public
quality gate fails. Do not manufacture an approval record.

## 7. Hand the candidate to AvaCore

After local checks and the public-only review pass, invoke
`.agents/skills/scicode-avacore-run/SKILL.md` as a black box. Give it the
candidate directory and the selected provider profile. Do not call Kimi
directly, do not build a replacement AvaCore command, and do not add tools or
feedback to the solver prompt.

Let the skill submit the complete ordered candidate run and return its run
manifest and exported trace path. Do not use a step limit for a quality run.
If the handoff is detached, close the authoring phase as instructed and resume
the same Kimi Code session only after the skill reports that the run is closed.

Classify provider, network, queue, timeout, and database failures as execution
or infrastructure evidence. Do not call a scientific task defective without
candidate-specific evidence.

## 8. Treat reasoning and usage as trace data

Treat one subproblem trace as complete only when the exported record preserves
all of the following for that subproblem:

- the exact solver prompt and previous-code context;
- ordinary assistant `content`;
- assistant `reasoning_content` (or the provider's equivalent reasoning field);
- extracted Python code and its extraction/syntax status;
- `finish_reason`, retry/timeout/length status, and provider identity;
- token usage, including `prompt_tokens`, `completion_tokens`,
  `reasoning_tokens`, and `total_tokens`;
- the raw provider response or an auditable equivalent;
- the evaluator outcome without exposing evaluator targets to the solver.

Reasoning and token usage are required trace fields, not optional annotations.
Do not discard them during SFT export. A missing reasoning field or missing
usage makes the trace incomplete and requires a rerun or explicit rejection. A provider
`finish_reason=length` at the deployment limit is still a trace when all
available content, reasoning, usage, and termination metadata are preserved.

Inspect every ordered subproblem separately. Check prompt continuity, code
extraction, syntax, scientific reasoning, finish state, retries, token counts,
and evaluator result. Keep solver-answer correctness separate from trace
completeness: a wrong answer is a valid trace-quality observation, but it does
not prove that the candidate is scientifically acceptable.

## 9. Revise with evidence

When the trace identifies a candidate defect, write the diagnosis in
`iterations/<revision>/analysis.md`, cite the exact evidence, increment the
revision, and change only the justified candidate fields. Regenerate the
public payload, prompt snapshots, oracle, and validation reports. Remove the
old solver workspace before the next run. Repeat the public child review and
the AvaCore skill after every substantive revision.

Never put hidden targets, reference formulas, private-test intent, evaluator
diagnoses, or a solver hint into the next prompt. Do not make the task easier
just to improve a model score. After repeated failures, redesign the scientific
workflow or reject the candidate instead of making endless wording-only edits.

## 10. Accept or hand off the candidate

Accept a candidate only when its public SciCode schema, scientific definition,
source provenance, private reference/independent/wrong verification, leakage
posture, public child review, and complete strict solver trace all have recorded
evidence. Ensure every counted subproblem has ordinary content, reasoning,
usage, finish status, and auditable code extraction.

Write the candidate release decision and all required candidate artifacts. Then
invoke `.agents/skills/scicode-delivery/SKILL.md`; do not hand-edit a final
dataset, bypass the delivery skill, or merge another candidate's worktree.
Leave framework aggregation, storage, locking, and delivery decisions to the
delivery skill.

## 11. Final prohibitions

Do not use official SciCode tasks or `test_data.h5`. Do not require
`inspect_ai` or official data in order to invent a new candidate. Do not add
solver tools, public feedback, or extra context. Do not let a child agent read
private materials. Do not overwrite another candidate's branch, worktree, run,
or artifacts. Do not discard failed, timed-out, length-terminated, or rejected
evidence. Do not silently count a partial trace as complete. Do not push a
branch automatically.
