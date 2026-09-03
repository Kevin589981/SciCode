# SciCode Authoring and Evaluation Instructions

You are working on an automated SciCode task-authoring and evaluation
pipeline. The deliverable of an authoring run is a scientifically justified,
executable, SciCode-compatible problem plus complete validation evidence. Do
not optimize for the number of generated problems; your job is to produce
correct tasks and faithful, auditable run records.

These instructions are operational. Follow the steps in order, create the
listed artifacts, and stop when a stop condition is reached. A prompt file is
not an access-control mechanism: the runner must materialize solver workspaces
from an allowlist and must keep private files outside them.

This is a SciCode scientific-program-synthesis standard. Do not reinterpret a
candidate as an issue, repository patch, pre-fix snapshot, or code-repair
benchmark. The evolving object is a versioned scientific problem with ordered
subproblems, solver traces, public scientific checks, and private numerical
verification.

## 0. Roles and directories

Every run has a single declared role: `author`, `science_reviewer`,
`clarity_reviewer`, `strict_solver`, `agentic_solver`, or `evaluator`. Never
switch roles inside a run without starting a new process and recording the
change.

Use this directory layout for candidate `CANDIDATE_ID`:

```text
authoring/CANDIDATE_ID/
  public/problem.jsonl          # canonical official-compatible record
  public/solver_payload/        # derived allowlist; no evaluator assertions
  public/checks/                # deterministic public scientific checks
  public/README.md              # inventory of solver-visible context
  public/prompt_snapshot/       # exact prompts used for public runs
  source_notes/                 # public papers and official documentation
  author/                       # private design and review notes
  reference/                    # private reference implementations
  oracle/                       # private HDF5 targets and test programs
  runs/                         # strict and agentic run outputs
  iterations/                   # author-solver evolution cycles
  validation/                   # reports and decisions
```

`public/problem.jsonl` is the authoring-side canonical record, not an
automatic solver mount: its official `test_cases` and `general_tests` fields
are evaluator records. The runner must materialize a fresh solver workspace
from `public/solver_payload/` and explicitly declared solver tools. The
materialized workspace must not contain `problem.jsonl` unless a field-level
redaction has removed evaluator assertions. It must never contain `author/`,
`reference/`, `oracle/`, other candidate directories, official test targets,
or previous solver runs.

## 1. Read the upstream contract before writing a task

At the beginning of every authoring run, perform these actions:

1. Record the upstream repository commit and the local Python/package versions.
2. Read `README.md`.
3. Read `eval/data/multistep_template.txt` and
   `eval/data/background_comment_template.txt`.
4. Read `eval/inspect_ai/scicode.py`, especially prompt construction,
   previous-code handling, code extraction, and per-step evaluation.
5. Read `src/scicode/parse/parse.py`, including the HDF5 layout and numerical
   value loading code.
6. Read the official SciCode paper or website sections covering problem
   selection, subproblem decomposition, numerical tests, domain-specific
   tests, and the three validation rounds.
7. Write `author/upstream_contract.md` with the commit, files read, exact
   required fields, and any local adapter differences.

If any required upstream source or schema file is unavailable, stop and record
the missing contract dependency. The official downloaded numeric target file
`eval/data/test_data.h5` is not required for authoring a new task: generate a
candidate-owned HDF5 oracle and validate it against the parser contract. Do
not claim official score comparability until the optional official evaluator
assets are available, but do not stop scientific task authoring for that
reason. Do not invent a replacement format while claiming it is official
SciCode.

## 2. Select a scientific workflow

Before drafting JSON, choose one research workflow that naturally requires
code. Prefer numerical methods, system simulations, or scientific
calculations grounded in a paper, textbook result, official method document,
or an openly reproducible scientific workflow.

Create `author/scientific_basis.md` containing:

- the public citation or official URL and the relevant section/equation;
- the scientific question and why it matters in the workflow;
- the model assumptions, constants, units, coordinate conventions, and valid
  parameter regime;
- the intended observable or output and how it can be independently checked;
- the proposed subproblem dependency graph;
- at least one plausible scientifically wrong implementation and its visible
  consequence.

Reject the candidate immediately if it is only a syntax exercise, a library
renaming, an API migration, formatting, path handling, or a one-line numerical
comparison. Also reject a candidate whose expected behavior depends on a
private fact, an unpublished convention, or a reference implementation that
cannot be independently justified.

## 3. Decompose one main problem into ordered subproblems

Create `author/decomposition.md` before writing the final record. For every
subproblem, state its scientific purpose, inputs, outputs, units, shapes,
dtypes, assumptions, and which earlier outputs it consumes. Then check:

1. Every subproblem contributes a distinct step in the scientific workflow.
2. The order is necessary for integration, not just a list of unrelated
   exercises.
3. A solver can implement the current function from the public context and
   previous function interfaces.
4. The final subproblem combines or exposes the intended scientific result.
5. Removing any subproblem either removes a required concept or changes the
   final result.

Do not put the diagnosis, expected algorithm, target file, exact formula-to-
branch mapping, or reference implementation route in the public text. Do put
all constants, definitions, units, shapes, sign conventions, and assumptions
needed by a scientifically competent solver in the public text.

## 4. Write the official-compatible problem record

Write one JSON object per line to `public/problem.jsonl`. The top-level fields
must be exactly the fields used by the upstream dataset:

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

Every `sub_steps` item must contain:

```text
step_number
step_description_prompt
function_header
test_cases
return_line
step_background
```

Follow these construction actions:

1. Use a stable generated ID that cannot collide with an official ID. Keep
   generated records in a separate manifest; do not force them through an
   upstream evaluator that assumes a fixed 80-problem array.
2. Write the main description as the research objective, not as a hint to the
   implementation.
3. Write `problem_io` as a complete contract: argument names, units, shapes,
   valid ranges, return values, and failure conditions.
4. Give every subproblem a unique `step_number` and an exact Python function
   or class header. Verify that `return_line` matches the documented return.
5. Put only import statements that the solver and valid alternatives are
   allowed to use in `required_dependencies`. Pin versions in the run
   environment, not in the natural-language question. If the private
   reference uses an extra library, declare it separately for the oracle
   environment; never advertise a solver dependency that the materialized
   workspace does not provide.
6. Use `problem_background_main` and `step_background` only for public
   scientific context. They are optional fields already present in SciCode;
   they are not a separate authoring mode and must never contain the solution.
   Create one candidate task and choose one fixed prompt profile before the
   first run. Do not create `with_background`/`without_background` task
   variants, paired evaluation directories, or separate scores. If a prompt
   adapter includes a background field, record that single profile and use it
   consistently for both solver modes.
7. Keep `test_cases` and `general_tests` in the official record shape, but
   treat them as evaluator-only records. They must not be mounted in a solver
   workspace or rendered into a solver prompt. A solver-visible public check
   must be a transparent contract or invariant, not an author-created
   expected answer.
8. Derive `public/solver_payload/` from the canonical record. Include only
   the problem and subproblem context, function headers, return lines,
   dependency declarations, and explicitly approved public sources. Exclude
   `test_cases`, `general_tests`, target placeholders, oracle paths, private
   thresholds, and author diagnoses. Hash both the canonical record and the
   derived payload in the run manifest.
9. Validate that the prompt builder produces the intended previous-step code
   context and current-step header for every subproblem. Store the rendered
   prompt, then inspect it for omitted scientific definitions and accidental
   evaluator content before any model call.
10. Compare `required_dependencies` with the materialized solver allowlist and
    fail the candidate on any mismatch. Record whether `general_tests` are
    executed by the selected evaluator adapter; the checked-in upstream
    evaluator does not execute them, so they cannot silently be treated as
    scored tests.
11. Create `public/checks/` as executable, deterministic checks for Agentic
    debugging. Each check must be derivable from the public contract (for
    example shape/type/finite-output checks, covariance symmetry, a stated
    limiting law, or a conservation/monotonicity invariant). It must not load
    private targets, compare against reference outputs, reveal hidden case
    names, or encode an author diagnosis. Record the check version and hash in
    the run manifest.

Save the exact input record used for each run in
`runs/<run_id>/problem_snapshot.jsonl`. Never edit a problem after a run
without incrementing its revision and recording the reason in `validation/`.

## 5. Implement the private reference and oracle

Create the reference implementation only under `reference/`. It must be an
executable implementation of every subproblem and the integrated workflow.
Then create `oracle/targets.h5` using the same step IDs and test ordering that
the upstream parser expects.

Add a candidate-specific schema command that checks every `<step_id>/testN`
group, member ordering, scalar/array/tuple encoding, dtype, shape, and count
against the canonical record before any solver run.

Perform these actions for each subproblem:

1. Generate normal numerical input-output cases over more than one scale.
2. Add cases for relevant signs, units, shapes, input order, boundaries,
   degenerate values, and mode switches.
3. Add at least one domain-specific check derived from a public analytical
   result, conservation law, limiting behavior, published value, or
   reproducible scientific workflow.
4. Generate targets independently from the solver-visible prompt. Keep target
   values, target-generation code, and private thresholds in `oracle/`.
5. Recompute a subset of targets with an independent implementation or an
   analytical calculation. Do not accept a reference implementation merely
   because it agrees with itself.
6. Run the integrated reference workflow, not only isolated functions, and
   record finite outputs, expected exceptions, tolerances, and runtime.
7. Calibrate difficulty with at least three fresh solver attempts spanning two
   model configurations or providers when available. Require a nontrivial
   mixture of failures and successes, inspect the scientific cause of each
   failure, and reject candidates that are solved by a constant shortcut or
   fail universally because of an omitted definition. This calibration is a
   quality gate, not a benchmark score.

The private verifier must judge observable scientific behavior. It must not
inspect variable names, helper names, patch shape, call order, or preferred
source formatting.

## 6. Check that the verifier separates real solutions from shortcuts

Create three implementations in a disposable validation workspace:

1. the reference implementation;
2. a structurally different implementation written without copying the
   reference control flow;
3. a plausible but scientifically wrong implementation or hard-coded shortcut.

Run all three against the same private tests. The first two must pass and the
third must fail for the intended scientific reason. Examples of useful wrong
implementations include a wrong unit conversion, a transposed tensor, an
incorrect boundary convention, a missing normalization, a fixed-parameter
answer, or an unstable formula that works only on the first numerical case.

If the wrong implementation passes, add scientifically meaningful cases or
redesign the task. If the independent correct implementation fails, fix the
verifier or task contract. Never lower thresholds simply to obtain the desired
pass/fail pattern.

Record commands, seeds, package versions, case IDs, outputs, and decisions in
`validation/behavior_matrix.md`.

## 7. Run the three SciCode validation rounds

Use the official validation idea, but record each round as a separate action.

### 7.1 In-domain scientific review

Have two independent reviewers from the relevant scientific area inspect and,
where possible, execute the question, reference, and domain-specific tests.
Each reviewer must record:

- whether the model and assumptions are scientifically correct;
- whether the expected result follows from the cited source;
- whether the decomposition and units are correct;
- whether the numerical and domain-specific tests catch meaningful errors;
- every requested correction and the revision it affects.

Do not merge the two reviews into one untraceable verdict.

### 7.2 Out-of-domain clarity review

Give only `public/solver_payload/` and the public sources to a reviewer
outside the target field. Do not give them the canonical evaluator record,
reference, or oracle while they judge clarity. Ask them to list every missing
definition, constant, unit, shape, assumption, or ambiguous sentence. Resolve
every issue or record why it is not needed.

### 7.3 Blind model reproduction review

Give a fresh model run only the solver-visible payload. It must reproduce the
prompt sequence and generate code without author files or private feedback.
Inspect the generated code and failures for ambiguity, accidental hints,
false-positive tests, and unsupported assumptions. The model run is evidence
for revision, not proof of scientific correctness.

After any change to the problem, repeat the affected validation rounds and
increment the task revision.

## 8. Preserve two solver modes without mixing their results

Both modes use the same public problem record, reference semantics, and private
scientific verifier. They differ only in interaction and feedback.

### Strict mode

Execute the original SciCode interaction as a single sequential generation
chain. Strict is not an agentic debugging mode:

1. process subproblems in order;
2. construct the official prompt from the fields the upstream template
   actually renders: prior subproblem descriptions (and their optional
   scientific context in the one fixed prompt profile), extracted code from
   earlier responses, the current step description, current
   function header/return line, and dependencies. Do not assume that
   `problem_description_main` is rendered: the checked-in upstream template
   labels a problem-description section but does not interpolate that field.
   Either preserve that upstream behavior or record a versioned adapter change
   that renders it, then use the same choice for every comparison. Never create
   a second task or score by toggling background inclusion;
3. request only the current subproblem's executable Python code;
4. do not grant shell, Python, file-editing, test, or public-check tools to the
   model during generation;
5. do not send test results, hidden targets, public-check results, or tool
   output between steps;
6. save each prompt, response, extracted code, and final test result.

Strict results are the only results that may be compared directly with an
official SciCode score.

### Agentic mode

Execute the same problem in a fresh solver workspace materialized from the
derived solver payload with an explicit tool allowlist. The solver may write
code, run declared public checks, inspect syntax/import/shape errors, and
revise code without waiting for a new author prompt. Allow the model to run a
bounded autonomous loop of edit -> tool/check -> inspect -> revise, including
delegating a subproblem to a child agent under the isolation rule in Section
10. The canonical record's evaluator assertions and the private oracle remain
outside the workspace.

Feedback returned during the run may contain only:

- syntax or import failure;
- timeout or resource failure;
- shape/type/finite-output failure;
- a transparent public-invariant result.

Never return target values, reference code, hidden-test names, private
thresholds, coverage information, or a diagnosis of the intended solution.
Record Agentic results separately from Strict results. Do not combine their
success rates into one metric or describe Agentic performance as official
SciCode performance.

## 9. Export complete, redacted traces

For every Strict or Agentic run, preserve both:

```text
runs/<run_id>/raw/provider.jsonl       # provider-native strict JSON events
runs/<run_id>/trace/events.jsonl       # normalized ordered events
runs/<run_id>/trace/manifest.json      # run metadata and file hashes
```

At minimum, the manifest must record:

```json
{
  "run_id": "...",
  "candidate_id": "...",
  "task_revision": "...",
  "mode": "strict|agentic",
  "provider": "...",
  "model": "...",
  "sampling": {},
  "prompt_version": "...",
  "prompt_profile": "...",
  "canonical_record_sha256": "...",
  "solver_payload_sha256": "...",
  "public_checks_sha256": "...",
  "environment_fingerprint": "...",
  "events_file": "...",
  "verification": {},
  "usage": {}
}
```

Normalize events such as `prompt`, `assistant_message`, `tool_call`,
`tool_result`, `code_snapshot`, `public_check`, `step_result`, `error`, and
`run_end`. Every event must retain order, event ID, timestamp, subproblem ID
when applicable, visibility, and provider/model metadata.

Before exporting a trace outside the authoring run, remove or reject any
private event. Do not reconstruct a raw provider trace from a rendered
transcript. Keep request IDs, retries, cancellations, token usage, timeouts,
and model errors when the provider reports them. Never include credentials.

## 10. Run the SciCode author-solver evolution loop

Do not accept a candidate after an author-only design pass. For every
candidate revision, execute the following actions in order:

1. Start a fresh `task_author` process. Give it the scientific seed, the
   upstream contract, and the current candidate revision. Require it to write
   or update the scientific basis, ordered subproblem decomposition, canonical
   record, solver payload, and public checks. Do not let it edit private oracle
   results to make a solver failure disappear.
2. Run schema, dependency, prompt-rendering, and solver-payload leakage checks.
   Stop the revision immediately when any check fails. Record the failure in
   `iterations/<revision>/author_decision.md`.
3. Materialize a new solver workspace from the redacted solver payload and
   public checks. Start a clean `solver_agent` process with no author notes,
   reference implementation, private oracle, prior solver workspace, or
   prior private feedback.
4. Run the selected Strict or Agentic SciCode interaction over all ordered
   subproblems. In Agentic mode, permit the solver to delegate a subproblem to
   a child agent only when the child receives the same public allowlist. Record
   the parent/child relationship, every delegation prompt and response, and
   every code handoff in the trace. Treat an unrecorded delegation or a child
   with private access as a failed run.
   Use a local prompt renderer and candidate verifier when `inspect_ai` is not
   installed. Treat `inspect_ai` as an optional official-evaluation adapter,
   not as a prerequisite for authoring or for the author-solver evolution
   loop. If no external provider is configured, launch an isolated child
   solver through the available agent runner or run the local deterministic
   solver harness; record that provider substitution explicitly.
5. Save the complete run under
   `iterations/<revision>/<run_id>/`: rendered prompts, model responses,
   extracted functions, previous-step code context, tool calls/results,
   public-check results, step results, final integrated result, retries,
   timeouts, and resource usage. Never summarize away a failed attempt before
   the author has inspected it.
6. Run the private scientific verifier only after the solver run is closed.
   Give the author the trace and a structured verifier report. Do not send
   private target values, oracle files, private thresholds, or hidden-case
   diagnoses back into a solver workspace.
7. Make the author classify each failure using evidence from event IDs and
   code snapshots: missing scientific definition, incorrect model equation,
   unit/shape convention error, wrong boundary handling, broken dependency
   between steps, numerical instability, tool-use failure, or an accidental
   shortcut. Record the exact subproblem, observed behavior, and scientific
   reason in `iterations/<revision>/analysis.md`.
8. Change only fields justified by that analysis. Examples are adding a
    missing public definition, correcting a unit or shape, splitting or
    merging a dependent subproblem, adding a public invariant, moving a private
    case to a broader scale, or removing a shortcut-friendly case. Do not fix a
    solver failure by leaking the reference formula, target value, branch
    mapping, or private test intent into the prompt.
   For example, if a solver assumes a fixed sampling interval, add a public
   nonuniform-interval contract and cases that exercise it; if it drops
   correlated measurement noise, make the covariance convention explicit and
   add a public symmetry/invariant check plus private correlated cases. Do not
   paste the intended matrix formula into the next prompt.
9. Increment the task revision, regenerate the solver payload and public
   checks, discard all solver workspaces, and return to action 2. Do not reuse
   hidden feedback as prompt text in the next revision.
10. After each revision, start a separate hostile `leakage_reviewer` process
    with only the solver payload and public sources. Require it to list any
    direct solution clue, insufficient definition, public-oracle exposure,
    or trivial shortcut. A finding sends the candidate back to action 8; a
    clean report does not replace scientific verification.

Declare an iteration **passed** only when all of these conditions hold:

- the canonical record, solver payload, public checks, dependency allowlist,
  and rendered prompt snapshots agree;
- every ordered subproblem and the integrated workflow run deterministically
  in a clean environment;
- the reference implementation and a structurally different scientific
  implementation pass the private verifier, while the documented wrong
  implementation fails for the intended scientific reason;
- at least one clean solver run reaches a scientifically correct integrated
  result without private information, and its trace contains no isolation or
  redaction violation;
- three fresh solver attempts show that the task is neither trivial nor
  impossible: not all attempts pass on the first try, at least one attempt
  exhibits a genuine scientific misconception, and at least one attempt
  eventually passes within the multi-step interaction allowed by its mode
  (Strict receives no test feedback; Agentic may receive only public-check
  feedback);
- the hostile leakage review and all required SciCode validation reviews are
  recorded for this revision.

Reject the revision and return to the relevant action when every solver passes
immediately, every solver fails for the same missing public definition, a
child-agent delegation leaks private material, a wrong shortcut passes, or the
author cannot explain a mutation from observed solver evidence. Do not label a
candidate accepted merely because its JSON parses or its trace was exported.

## 11. Model-provider and candidate-generation rules

The runner must select a provider through a stable adapter. Do not hard-code
Kimi-specific request or response fields into task logic. Record provider,
model, sampling parameters, retry policy, request IDs when available, usage,
timeouts, cancellations, and failure reasons. Kimi is an initial provider, not
an assumption built into the task format.

Use over-generation followed by independent filtering:

```text
scientific seed or capability hypothesis
  -> candidate problem and reference/oracle package
  -> schema and static checks
  -> reference and alternate-solution validation
  -> Strict and/or Agentic solver rollouts
  -> strategy and failure annotation
  -> targeted mutation or rejection
  -> accepted task revision and trace export
```

An evolution record must state what evidence caused the mutation. Examples
include a repeated unit error, an overfit to a fixed parameter, a missing
boundary case, a failure to preserve a tensor convention, or a successful
shortcut that bypasses the intended scientific concept. A mutation that only
changes wording or random constants is not evidence-based unless it tests a
documented generalization.

## 12. Contamination and leakage checks

Before acceptance, perform these concrete checks:

1. Search every solver-visible file for target values, oracle filenames,
   reference-code fragments, hidden-test labels, private thresholds, and
   author-only diagnosis.
2. Compare generated text and constants against official test records and
   parent candidates; flag near-duplicate scientific workflows and copied
   answer code.
3. Confirm that official test problems were not used as generation prompts,
   candidate parents, oracle inputs, or solver examples.
4. Confirm that public prompts do not include target values even when the
   internal `test_cases` format uses a target placeholder.
5. Materialize a fresh solver directory from the allowlist and verify that it
   contains only the derived payload and approved tools, with no path to
   `problem.jsonl`, `author/`, `reference/`, `oracle/`, or previous runs.
6. Verify that each rendered prompt contains the scientific definitions needed
   for the current step even if the upstream template omits the main
   description. Record any adapter augmentation and its revision.
7. Re-run the reference and verifier from a clean environment with network
   disabled and the recorded random seed.
8. Verify that the candidate has exactly one prompt profile and no paired
   background/no-background task directories or mode-specific scores.

If any check finds a leak or contamination, remove the candidate revision
from the accepted pool, record the finding, and return to the affected step.

## 13. Acceptance record and stop conditions

Create `validation/release_decision.md` only after all of these records exist:

```text
upstream_contract.md
scientific_basis.md
decomposition.md
behavior_matrix.md
two independent in-domain reviews
one out-of-domain clarity review
one blind model reproduction review
strict run manifest and result
agentic run manifest and result, if executed
leakage and contamination report
repeatability report
```

The release decision must state the candidate revision, source commit, test
data hash, environment fingerprint, accepted modes, known limitations, and
the exact reason for acceptance.

Stop and reject the candidate instead of improvising when:

- the public contract is scientifically incomplete or contradictory;
- the reference behavior cannot be independently justified;
- the task is only a shallow coding exercise;
- the verifier accepts a plausible wrong shortcut;
- the solver needs private information to make progress;
- the generated task is a near duplicate of an official or parent task;
- a fresh offline run is not deterministic enough to reproduce the decision;
- the public and private materials cannot be cleanly separated.

Do not add private analysis to the solver-visible task. Keep author-only
decisions and review evidence outside the public problem record.
