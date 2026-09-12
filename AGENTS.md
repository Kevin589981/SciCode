# SciCode Candidate Authoring Instructions

Follow every instruction in this file as an operational command. Treat this
file as the instruction contract for the authoring controller and its review
child agents. Treat the repository skills as the only interfaces to execution,
solver runs, validation, and delivery.

## 1. Keep the roles separate

Act as the scientific task-authoring controller. Research one allowed source,
design one candidate, create its artifacts, request one fresh public review
child, run deterministic checks, invoke the solver-run skill, inspect every
returned trace, revise when evidence identifies a candidate defect, and request
delivery only after all gates pass.

Treat the solver model as the answer-producing role. Do not solve the candidate
in place of the solver. Do not use an authoring response, a review response, or
your control transcript as a solver trace. Invoke the solver only through the
solver-run skill; do not bypass that skill from a candidate script, shell
shortcut, or review child.

Treat the review child as another authoring agent, not as the solver. Give the
child public material only. Keep private evaluator material and implementation
details out of its prompt.

Consume execution, storage, aggregation, and run settings through the provided
skills. Do not inspect, reconfigure, or replace those settings. Do not modify
shared framework code to rescue one candidate.

## 2. Start a candidate session

1. Read `README.md`, the files under `eval/data/`,
   `eval/inspect_ai/scicode.py`, `eval/scripts/gencode.py`,
   `src/scicode/parse/parse.py`, this file, and every skill you invoke.
2. Work only in the allocated candidate worktree and candidate directory.
   Do not create another checkout, edit a shared checkout, or write outside
   the candidate workspace except through an invoked skill.
3. Confirm that the allocated worktree contains `AGENTS.md`, `.agents/skills/`,
   `eval/`, `src/`, and `scripts/` before authoring.
4. Keep credentials and private execution settings in the supplied execution
   boundary. Never write them into prompts, source notes, traces, manifests,
   generated data, or commits.
5. Use the supplied worktree and candidate identifiers in every artifact.
   Do not invent a second candidate identity or overwrite another candidate.
6. Route source retrieval according to the destination. Access domestic,
   internal, and `.cn` services directly, including domestic search endpoints
   used to locate scientific material. For GitHub, arXiv, Hugging Face,
   Docker Hub, and other externally hosted scientific sources, set
   `http_proxy`, `https_proxy`, `HTTP_PROXY`, and `HTTPS_PROXY` to
   `http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128` for the
   retrieval command only.
7. Preserve `no_proxy` and `NO_PROXY` entries for localhost, private network
   ranges, cluster-local services, internal model hosts, and domestic domains.
   Remove the temporary source proxy variables after retrieval when the shell
   environment is reused for another operation. Do not route an internal
   solver request through the external-source proxy when its host is in
   `NO_PROXY`.
8. Read the injected `BASE_URL`, `MODEL` (or `KIMI_MODEL`),
   `OPENAI_API_KEY` (or `API_KEY`), and `POSTGRES` values from the execution
   environment when a solver skill or its requested connectivity check needs
   them. Use `BASE_URL` as the OpenAI-compatible solver endpoint and use the
   supplied model name for the solver; do not substitute the authoring-agent
   model configuration. Use `POSTGRES` only for the requested trace/storage
   handoff. Never place any of these values in a prompt, source note, trace,
   manifest, generated data, or commit. Keep solver and database traffic on
   the route specified by the skill, not on an ad hoc source-download route.

## 3. Research and design the scientific task

1. Search an allowed scientific source such as GitHub, arXiv, or Hugging Face.
2. Record the original repository URL, immutable source commit, license, paper
   URL or DOI, retrieved files, and a source-fragment fingerprint in
   `source_notes/provenance.json`.
3. Derive one realistic function-level scientific computation that fits the
   original SciCode question-and-answer format.
4. Reject maintenance issues, toy arithmetic, generic data transformation,
   syntax exercises, API renames, installation fixes, simple bug repairs, and
   non-scientific tasks.
5. Do not use an official SciCode problem, official SciCode prompt, official
   SciCode test case, or official SciCode oracle as a seed, example, oracle
   input, or solver context.
6. Reject semantic duplicates of official tasks, existing candidates, or
   training records. Changing only random constants does not make a new task.
7. Clone only source files needed for the scientific derivation. Remove
   unnecessary upstream source, build products, caches, and large artifacts
   after the candidate is complete while retaining provenance and the minimal
   scientific source note.

## 4. Preserve the original SciCode contract

1. Write exactly these top-level fields in `public/problem.jsonl`:

   `problem_name`, `problem_id`, `problem_description_main`, `problem_io`,
   `required_dependencies`, `sub_steps`, `general_tests`,
   `problem_background_main`.

2. Write exactly these fields for every `sub_steps` item:

   `step_number`, `step_description_prompt`, `function_header`, `test_cases`,
   `return_line`, `step_background`.

3. Keep the model-visible prompt text, ordering, dependency declaration,
   previous-code handoff, code-block requirement, and code-extraction behavior
   identical to the checked-in SciCode adapter.
4. Put authoring metadata outside solver-visible fields. Do not add tools,
   shell access, public test feedback, hidden hints, extra background, or an
   alternate solver mode. Use strict SciCode sequencing only.
5. Build an ordered scientific dependency chain. Require nontrivial scientific
   semantics, numerical or simulation behavior, unit or shape conventions,
   boundary conditions, or meaningful multi-path checks.
6. State assumptions, domain, units, shapes, edge cases, and expected return
   values precisely.
7. Keep private material outside `public/`. Do not expose private tests, target
   values, `reference/`, `oracle/`, author notes, or prior runs to the solver.
8. Derive `public/solver_payload/` from an explicit allowlist and verify that it
   cannot reveal evaluator intent.

## 5. Build the candidate and its oracle

Perform these actions in order for every revision:

1. Write the scientific basis, decomposition, provenance, public record,
   reference implementation, independently structured correct implementation,
   plausible scientifically wrong implementation, oracle generator, and
   candidate manifest.
2. Freeze the reference implementation. Generate the private HDF5 oracle with
   the candidate oracle generator whenever it is absent. Never replace it with
   an official oracle.
3. Derive the public solver payload from the allowlist. Remove private values,
   evaluator assertions, and author-only diagnostics.
4. Render fixed strict prompt snapshots and compare them with the checked-in
   upstream adapter.
5. Run `scripts/validate_candidate.py` and the candidate-check skill before
   requesting a solver run.
6. Require the reference and independent implementations to pass every
   private test. Require the plausible wrong implementation to fail for the
   intended scientific reason. Record evidence instead of weakening a failed
   scientific test.
7. Keep experiments inside this candidate worktree or an isolated disposable
   copy. Do not modify the shared SciCode public structure to fit one task.

## 6. Run the public-only review

1. Create one fresh authoring child agent after deterministic checks pass.
2. Give the child only the public payload, public source notes, prompt
   snapshots, and validation contract. Withhold private reference code,
   private tests, oracle files, target values, and private diagnoses.
3. Require evidence-based checks of scientific semantics, assumptions, units,
   shapes, boundary behavior, clarity, SciCode-level difficulty, ordered
   dependencies, source provenance, license, prompt fidelity, evaluator
   leakage, and oracle independence.
4. Save the child request, response, decision, and event/session identifier in
   `validation/review_child.json`.
5. Reject or revise immediately when a public gate fails. Never manufacture an
   approval record.

## 7. Hand the candidate to AvaCore

1. Invoke `.agents/skills/scicode-avacore-run/SKILL.md` only after local checks
   and the public review pass.
2. Give the skill the candidate directory and consume its returned run
   manifest and exported trace path. Do not call the solver directly, build a
   replacement command, add tools, or add feedback to the solver prompt.
3. Submit the complete ordered candidate run. Do not use a step limit for a
   quality run.
4. Close the current authoring turn when the skill reports a detached run, and
   resume the same authoring session only after the run is closed.
5. Classify provider, network, queue, timeout, and other execution failures as
   infrastructure evidence. Do not call a scientific task defective without
   candidate-specific evidence.

## 8. Treat reasoning and usage as trace data

Treat one subproblem trace as complete only when the exported record preserves:

- the exact solver prompt and previous-code context;
- ordinary assistant `content`;
- assistant `reasoning_content`, or the provider's equivalent reasoning field;
- extracted Python code and extraction/syntax status;
- `finish_reason`, retry/timeout/length status, and provider identity;
- token usage, including prompt, completion, reasoning, and total tokens;
- the raw provider response or an auditable equivalent;
- the evaluator outcome without exposing evaluator targets to the solver.

Require reasoning and token usage. Do not discard either field during export.
Treat a missing reasoning field or missing usage as an incomplete trace that
requires a rerun or explicit rejection. Treat `finish_reason=length` at the
deployment limit as a valid trace when all available content, reasoning, usage,
and termination metadata are preserved.

Inspect every ordered subproblem separately. First run the dependency-free
detail reader and read its bounded report:

```bash
python3 scripts/inspect_trace.py \
  --candidate-dir "$CANDIDATE_DIR" \
  --rollouts "$CANDIDATE_DIR/runs/$RUN_ID/rollouts.jsonl" \
  --output "$CANDIDATE_DIR/validation/trace_details.json" \
  --max-text-chars 8000
```

Use that report to check prompt continuity, code extraction, syntax, scientific
reasoning, finish state, retries, token counts, and evaluator result. Do not
import `scicode.gen.models` or call a provider merely to read a trace. Keep
solver-answer correctness separate from trace completeness: a wrong answer is
a valid trace-quality observation, but it does not prove that the candidate is
scientifically acceptable.

## 9. Revise with evidence

1. When a trace identifies a candidate defect, write the diagnosis in
   `iterations/<revision>/analysis.md`, cite exact evidence, increment the
   revision, and change only justified candidate fields.
2. Regenerate the public payload, prompt snapshots, oracle, and validation
   reports after every substantive revision.
3. Remove the old solver workspace before the next run. Repeat the public child
   review and solver-run skill after every substantive revision.
4. Never put hidden targets, reference formulas, private-test intent, evaluator
   diagnoses, or solver hints into a later prompt.
5. Do not make the task easier merely to improve a model score. After repeated
   failures, redesign the scientific workflow or reject the candidate instead
   of making endless wording-only edits.

## 10. Accept or hand off the candidate

1. Accept a candidate only when its public schema, scientific definition,
   provenance, private reference/independent/wrong verification, leakage
   posture, public review, and complete strict solver trace all have recorded
   evidence.
2. Ensure every counted subproblem has ordinary content, reasoning, usage,
   finish status, and auditable code extraction.
3. Write the release decision and all required candidate artifacts.
4. Run `.agents/skills/scicode-delivery/SKILL.md` and follow its validation,
   deduplication, aggregation, and merge commands. Do not hand-edit a final
   dataset or bypass the delivery skill.
5. Stage and commit the complete candidate revision on its allocated branch
   before requesting delivery. Do not push the branch.

## 11. Final prohibitions

Do not use official SciCode tasks, prompts, test cases, or oracles. Do not
require optional official evaluation packages or official data in order to
invent a candidate. Do not add solver tools, public feedback, extra context,
or a non-strict solver mode. Do not let a child agent read private materials.
Do not overwrite another candidate's branch, worktree, run, or artifacts. Do
not discard failed, timed-out, length-terminated, or rejected evidence. Do not
silently count a partial trace as complete. Do not push automatically.
