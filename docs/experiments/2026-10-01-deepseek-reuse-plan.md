# DeepSeek V4 Flash 0731 prompt-reuse experiment

Status: isolated branch and analysis only, 2026-10-01. No DeepSeek generation,
training, dataset upload or database change has been started.

Historical planning snapshot. The user subsequently authorized implementation
and limited debugging, but NOT a production batch. The implemented workflow and
current acceptance evidence supersede the setup-only status above; see
[DeepSeek acceptance](2026-10-01-deepseek-acceptance.md). Existing source datasets
and the Qwen baseline remain untouched.

## Version lineage

- Repository: existing public `Kevin589981/SciCode`.
- Parent branch: `exp/2026-10-01-qwen35-self-distill-reuse-kimi-prompts`.
- Parent snapshot tip: `bbe322b` (based on `9fd494f`, with the formerly
  uncommitted Qwen prompt-reuse workflow and provenance documentation).
- Experiment branch: `exp/2026-10-01-deepseek-v4flash-0731-reuse-kimi-prompts`.
- Local workspace: `D:/1/desktop/scienceIDE/SciCode-deepseek-distill-20261001`.
- Server workspace: `/root/ScienceIDE-workspace/SciCode-deepseek-distill-20261001`.

The experiment branches preserve original dirty workspaces and server data.
Only code and documentation are synchronized through GitHub.

## Interpretation

The Qwen workflow keeps Kimi-authored task statements and the exact system/user
messages, discards the old assistant response, and asks Qwen to produce a new
native reasoning channel plus a final answer. It is a self-generated-trace
workflow, consistent with self-distillation intent. The actual relationship
between solver and training-student checkpoints must be recorded; a model alias
alone does not prove they are the same checkpoint.

Replacing the solver with DeepSeek makes this a cross-model distillation
experiment. Reusing prompts is desirable for the first controlled comparison:
it changes the teacher response, not the question distribution. Rerunning
repository discovery and question authoring would introduce a second variable.
Prompt reuse does not imply copying Kimi reasoning or reference answers into the
DeepSeek request. Author-only fields remain outside the reused visible prompt.

## Correct population and pilot history

The intended source file is the 4,586-row supported reasoning-plus-answer export:

`/root/ScienceIDE-workspace/SciCode-v3/datasets/scicode-v1-supported-cot-answer-sft-4586-v1/sft.jsonl`

The provenance index remains:

`/root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/index.jsonl`

Original task files are on the server and must be resolved from that index.
Do not relocate or duplicate the source corpus to Windows.

The Qwen pilot explicitly selected 1,000 from 4,939 eligible rows, not from this
4,586-row export. Its membership is 927 supported answers, 45 reasoning
candidates and 28 audit errors. Its current output contains all 1,000 trace IDs:
991 `stop` completions and nine truncated `length` completions. The 336 error
sidecar rows are historical failures across runs, not current missing-task count.

The 4,586-row source contains 4,586 distinct task hashes. The existing preparer
can request a different target; the 1k limit is not architectural. For all tasks,
review the repository cap and index/task validation before writing inputs.
The preparer refuses a nonempty output directory and reports insufficient
eligible tasks instead of silently returning fewer rows.

## Implementation required before generation

1. **Dataset preparation and manifest.** Select from the 4,586-row file, validate
   `model_supported_answer` and `reasoning_and_answer`, preserve exact prompts,
   and record source SHA-256, selected old IDs, task hashes and prompt hashes.
   The current preparer trusts the chosen SFT input and does not enforce audit
   disposition itself. Write into a new DeepSeek data directory.
2. **A separate configurable launcher.** Reuse `factory.reasoning.rollout`, but
   parameterize source/output directory, endpoint, model version, secret supplied
   externally, temperature, output budget, context limit, timeout, concurrency
   and retry policy. Do not blindly inherit Qwen's 196,608 output-token limit or
   concurrency 500, nor the unrelated Kimi service's concurrency capacity.
3. **Protocol validation.** Test the actual deployed DeepSeek service's streaming
   response fields, reasoning availability, finish reasons, usage fields, token
   limits and timeout behavior. Existing code already supports OpenAI-style
   `reasoning_content` and `content`; compatibility is conditional on the actual
   service protocol. Preserve both reasoning and final answer without rewriting
   the teacher's reasoning.
4. **Teacher-aware SFT export.** Parameterize the hard-coded Qwen policy name,
   verify reasoning/final boundaries for DeepSeek, preserve teacher/version and
   source provenance, and avoid applying the Qwen-specific duplicate-final
   repair blindly. Incomplete traces remain available as raw artifacts; their
   treatment as a separate learning population must be explicit, not silently
   mixed into the complete-answer comparison set.
5. **Scientific and reasoning-quality audit.** Reuse existing audit components
   against the exact visible prompt and new response. The native exporter does
   structural validation only. Use the same rubric and inclusion policy for
   teachers, distinguish scientific answer support from reasoning quality, and
   avoid equating failed asserts with worthless reasoning. Do not carry over an
   old Kimi-answer audit verdict as approval of a new DeepSeek answer.
6. **Matched-token training manifests.** Count the student Qwen tokenizer's
   effective supervised tokens after chat templating, loss masking, truncation
   and packing. Teacher API token counts are not interchangeable. Select math
   and STEM supplemental rows on the server from
   `/mnt/data/zf_sft_delivery/math_ds_2449.jsonl` and
   `/mnt/data/zf_sft_delivery/stem_ds_2541.jsonl` only after their schema and token
   distribution have been inspected; record seed and IDs rather than sampling by
   row count alone.

Items 1-4 are localized adaptations of an existing prompt-reuse solver/export
path, not a rewrite of the automated scientific task factory. Items 5-6 are
essential experiment controls, not merely renaming the model variable.

## Resume, scale and evaluation controls

- Use a new model-specific `run_variant`, fresh output paths, and an immutable
  launch manifest. Existing trace identity includes model/variant but not all
  prompt/budget settings, so changing settings inside one variant can cause
  inappropriate resume skipping.
- The rollout uses a bounded thread pool and one completion writer, not SQLite
  task leasing. This path does not require rerunning the old discovery queue.
  It also does not include the batch pipeline's metrics-based governor; the
  intended DeepSeek service's limits need separate validation.
- A clean checkout intentionally lacks generated input data. Merely running the
  historical 1k launcher from a new folder will fail its input-file checks until
  new inputs have been prepared or an explicit input path has been configured.
- For a teacher comparison, hold the student checkpoint, exact prompts, data
  inclusion policy, training token budget, template, optimizer/training settings
  and evaluation protocol fixed. If supplemental math/STEM data are added only
  to the DeepSeek arm, the result combines teacher and data-mixture changes.
  Keep that as a separately identified experiment or include a matched mixture
  control; do not attribute its entire score difference to teacher identity.
- Measure final-answer completeness/Python extraction, correctness and
  reasoning efficiency as well as benchmark score and basic-arithmetic retention.
  Different teacher reasoning styles may explain observed effects, but the
  reported anecdote alone does not establish a universal Kimi-vs-DeepSeek cause.

## Completion boundary for this branch setup

The Qwen snapshot and this analysis are pushed to the existing repository and
checked out in separate local/server worktrees. No API requests, new training,
or remote database writes are authorized by this setup-only step. An actual
DeepSeek endpoint/model identifier and service limits are needed before a
validated synthesis run.
