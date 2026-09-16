# SciCode Function-Pair Trace Authoring

Follow these commands for every candidate.

## 1. Create independent function questions

1. Research one allowed scientific source from GitHub, arXiv, or Hugging Face.
2. Pin the source URL, immutable commit/version, license, paper or DOI, selected files, and a source-fragment fingerprint.
3. Inspect the source and select every meaningful scientific function suitable for a self-contained question. One research session produces many independent candidates when the source contains them.
4. Create one SciCode top-level problem and one subproblem for each selected function. Keep problems independent unless a real scientific dependency requires multiple ordered subproblems.
5. Mark a multi-function candidate with `candidate_profile: "function_batch"` in `candidate.json`; mark a single function candidate with `candidate_profile: "function_pair"`.
6. Add only the scientific background, inputs, outputs, units, assumptions, and function contract needed to make each question solvable. Do not expose source implementation, reference code, oracle values, or private tests.
7. Reject utility functions, toy arithmetic, generic data conversion, setup code, API renames, installation fixes, and ordinary software bug fixes.
8. Do not use official SciCode questions, prompts, tests, or test data.

## 2. Preserve the solver contract

1. Write canonical SciCode fields in `public/problem.jsonl`.
2. Keep the normal strict prompt, ordered previous-code context, code extraction, and solver-visible payload unchanged.
3. Keep solver payload free of reference code, private oracle, private tests, author notes, prior runs, and target values.
4. Keep one private reference implementation and generate `oracle/targets.h5` from it and candidate-owned tests.
5. Store provenance and operational metadata outside solver-visible files.

## 3. Run lightweight deterministic checks

1. Run schema, static syntax, public/private isolation, prompt, provenance, and oracle checks.
2. Ensure each function-pair candidate has exactly one subproblem and unique problem/step identifiers.
3. Ensure the private reference produces every oracle group and test value.
4. Do not start a review child, write a long quality report, or run a second authoring model call.
5. Record failed checks and move to another candidate when a hard check fails.

## 4. Submit one strict trace run

1. Invoke `scicode-avacore-run` for the candidate.
2. Do not call the solver API directly from Kimi Code.
3. Use the configured strict AvaCore route, candidate oracle, and normal SciCode prompt.
4. Let the run complete once, including ordinary content, reasoning content, extracted code, finish reason, token usage, provider response, and evaluator outcome.
5. Treat a complete wrong solver answer as a usable learning trace.
6. Retry only infrastructure failure, missing trace, unusable truncation, or parser failure.

## 5. Deliver trace samples

1. Mark a run complete only when every active subproblem has an auditable trace.
2. Export one SFT sample for every complete active subproblem.
3. Preserve messages, ordinary completion, reasoning content, parsed code, context code, provider response, usage, finish reason, run metadata, and source provenance.
4. Keep private oracle and reference files out of exported samples.
5. Deduplicate by candidate revision, problem id, step number, trace hash, and source fingerprint.
6. Commit candidate artifacts before delivery. Do not push branches.

## 6. Runtime boundaries

1. Work only in the allocated candidate directory and branch.
2. Use the supplied project interpreter for repository commands.
3. Access internal model and database services directly.
4. Set the approved proxy only inline on commands that retrieve external content.
5. Keep credentials out of prompts, source notes, traces, manifests, and Git.
6. Stop the authoring session immediately after writing the AvaCore handoff.
