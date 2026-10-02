# SciCode Reasoning-Trace Factory — Project State

Last updated: 2026-10-02

## Active experiment: grounded re-audit and code repair (2026-10-02)

User approved fixing overstrict scientific review and launching re-audit / repair.
Independent branch: `fix/2026-10-02-grounded-audit-and-code-repair-v2`.
Local tree: `SciCode-audit-repair-20261002`; remote tree:
`/root/ScienceIDE-workspace/SciCode-audit-repair-20261002-v2`.
New output: `data-reasoning-grounded-audit-repair-20261002-v2`.
The v1 worktree/smoke is frozen; do not stop its existing model requests.
The two preceding batches have ended; preserve ALL their artifacts unmodified.
Expected inventory: 9,438 complete candidates; preserve 2,870 previously ready
Python examples and re-audit the remaining 6,568, including all 6,366 quarantine
rows, unresolved reviews and incomplete/non-Python dataset qualifications.
Two original unfinished generations are outside this complete-candidate inventory.
Server inventory verified: selected=6,568, preserved=2,870, old dispositions
quarantine=6,366 / supported=72 / reasoning_candidate=129 / audit_error=1.

New policy binds proposed requirements and issues to literal original task/answer
quotes, separates critical/repairable/advisory/unresolved issues, and gives the
third adjudication role the FULL original prompt and FULL answer. Cosmetic order
is not a scientific veto; real legitimate-domain code/science errors still block.
Real gateway smoke revealed line-wrap and system/user role mislabelling in quotes.
Repair only uniquely located whitespace/role differences, retaining reported and
canonical quotes; never approximate words, punctuation, comparators or meaning.
Teacher DeepSeek-V4-Flash-0731 / 500 generation workers, reviewer Kimi-K3 / 500
shared review workers; both context budgets 262,144. Teacher answer output budget
196,608; reviewer requested output budget 131,072, dynamically bounded by input.
Task-only repair uses 65,536 output tokens. One writer, bounded submission, stage
caches, independent provider circuit breakers, and up to three recovery passes.
No SQLite task queue and no per-task completion-table scans in this new runner.

Re-audit every selected old answer before generating. Accept supported old answers
only with productive CoT AND complete Python. Otherwise generate at most two fresh
CoT/code revisions and re-audit/re-grade each; flawed questions can be explicitly
rewritten with provenance. Feedback is fallible, visible in the new training user
message, and NOT authoritative task requirements. This is a feedback-assisted
repair experiment, NOT an exact-prompt controlled distillation comparison.
Retain native reasoning AND final answer in raw, candidates and SFT. Never drop
final answers silently. Uncertain/unresolved, untrainable or exhausted samples stay
in separate partitions; no humans required, no silent wholesale admission.
Python syntax/model checks are NOT execution proof; model support is NOT scientific
proof. Previously ready examples are preserved, not retroactively certified v3.
No SFT training, deployment, database writes or unrelated workspaces are in scope.
Details: `docs/experiments/2026-10-02-grounded-audit-repair.md`.

## Previous experiment: DeepSeek regeneration of the remaining Kimi prompts

User authorized computing the old 9,440 minus current 4,586 prompt population
and independently launching the complement while they are away. The actual
complement is **4,854** valid, distinct prompts, spanning 2,210 repositories.
Original Kimi answer dispositions: 4,501 quarantine, 203 reasoning candidates,
149 audit errors, and one supported answer previously omitted for lacking CoT.
An unsupported old answer does not invalidate its question. Do not require old
answer approval when reusing these prompts, and do not pass old assistant text
to the new teacher. No overlap by old trace ID, task ID/hash, visible prompt hash,
or visible user hash. Inputs and an exclusion snapshot are hash-bound.

The purpose remains **scientific code generation with useful CoT**, not pure
theory QA or reasoning-only SFT. This complement has 4,724 analysis-and-code and
130 analysis-and-experiment tasks; preserve exact original prompts for this
experiment rather than silently rewriting them or relaxing review standards.
Both new reasoning and final answers (including code) must be retained in every
raw/native/review partition. Quarantine is not deletion or proof of useless CoT.

Branch: `exp/2026-10-02-deepseek-reuse-remaining-kimi-prompts`.
Local: `D:/1/desktop/scienceIDE/SciCode-deepseek-complement-20261002`.
Remote: `/root/ScienceIDE-workspace/SciCode-deepseek-complement-20261002`.
Run root: `data-reasoning-deepseek-complement4854-20261002-v1` within that tree.
Teacher DeepSeek-V4-Flash-0731 / 500 generation workers; reviewer Kimi-K3 / 500
shared review workers. Both have 262,144-token context budgets. Teacher output
budget 196,608; reviewer output budget 65,536, adjusted for complete input.
Launch credentials remain outside Git and manifests. Preserve the active 4,586
run, all original data, other worktrees, and the UV virtual environment.

Details and commands: `docs/experiments/2026-10-02-deepseek-complement.md`.
The output `launch_manifest.json` records the actual detached PID and commit;
`pipeline_progress.json` and `solver/progress.json` are current runtime truth.
No SFT training or database writes are authorized by this expansion request.

Known audit limitations from the preceding analysis remain unchanged here:
all-or-nothing requirements, and a consistency role that sees model-generated
plans/findings rather than original prompt/answer. Preserve all partitions for
later adjudication; never equate quarantine with proven useless reasoning.

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
- Reasoning-first v1 baseline commit: `0c1c306`
- Batch v2 synchronization point: current `scienceide-pipeline` branch head
- Active development worktree:
  `D:/1/desktop/scienceIDE/scicode-work/reasoning-factory-wt`
- Active development branch: `feature/reasoning-trace-factory-v1`
- Remote execution checkout: `/root/ScienceIDE-workspace/SciCode`
- Factory code: `factory/`
- Existing pipeline: vet -> mine -> propose -> verify -> calibrate -> harvest ->
  solve, with separate trace grading.
- Existing data is dominated by single-function SciPy implementation tasks with
  three generated test inputs. This is considered a smoke baseline, not the
  target task distribution.
- Raw traces and canonical SFT preserve Kimi-K3 `reasoning_content` with
  separate content/reasoning loss masks.
- SFT selection is driven by scientific trace value rather than full reward.
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

## Redesign Direction and Implementation Progress

The next factory version must add:

1. reasoning-demand task archetypes rather than only leaf-function imitation;
2. task specifications that declare required cognitive operations;
3. a task-depth preflight gate independent of executable correctness;
4. outcome-independent trace-quality grading;
5. thinking-preserving, quality-driven SFT export with explicit masks;
6. tests for schemas, selection policy, reasoning preservation, and resume/data
   provenance;
7. a Kimi-K3 16k-context smoke test after the local and remote code are synced.

Implemented on `feature/reasoning-trace-factory-v1`:

- `6dfb708`: canonical task/trace/grade schemas and stable hashing;
- `5a09a4a`: three-archetype reasoning task composition;
- `5ea89c5`: deterministic plus semantic task-depth preflight;
- `b52e9c0`: native-thinking rollout collection with auxiliary outcomes;
- `e64ec79`: outcome-independent trace-value grading;
- `09c8f48`: thinking-preserving SFT export with separate masks.

Reasoning-first v1 is complete and synchronized. The three-task Kimi-K3 16k
smoke produced one selected SFT row for every archetype (3 tasks, 3 admitted
preflights, 3 traces, 3 grades, 3 SFT rows). All three auxiliary executable
outcomes were `not_run`; this is acceptable because outcome is not the trace
selection criterion. The SFT artifact SHA-256 is
`a1be8bc6f6e177ef2f60ce93c6a4d5704e6c409ac447600f93edb43df99851de`.

Batch production v2 is implemented locally:

- primary repository input is the revision-pinned 83.7GB
  `SciCodePile/SciCode-Domain-Code` file dataset itself, not a repository-name
  list followed by fresh GitHub clones; cleaned rows are reconstructed into
  immutable local snapshots with path validation and content hashes;
- only Python-bearing snapshots enter the current Python AST miner, selected by
  deterministic round-robin coverage over SciCodePile keywords;
- the legacy curated 40-term vocabulary plus up to 40 Kimi expansions remains
  the bounded GitHub-search channel two rather than the production main source;
- curated/optionally expanded scientific query discovery with serial,
  rate-limit-aware GitHub Search;
- high-precision `name,description` discovery by default, with README-wide
  retrieval retained as an explicit recall-oriented mode;
- metadata filtering and repository-ID deduplication before clone or LLM use;
- high-confidence documentation/list/curriculum rejection with an auditable
  rejection ledger before clone or LLM use;
- immutable default-branch commit pinning for admitted catalog entries;
- per-query/per-repository discovery error ledgers so one deleted or transiently
  unavailable candidate does not discard an otherwise valid batch;
- SQLite transactional job/resource leases, expiry recovery, heartbeats,
  retries, and event audit; portable rollback journaling is the default and WAL
  is opt-in on compatible local filesystems;
- queue-global LLM/repository slots plus a per-repository mutex;
- fixed-schema repository screening, license gate, AST mining, and
  repository-intent × code-evidence ranking;
- repository screening prefers a retained README, but falls back to bounded,
  path-labelled Python source excerpts because cleaned SciCodePile snapshots
  commonly omit repository documentation;
- job identity over source snapshot and canonical production recipe;
- isolated repository/job artifact shards and deterministic atomic aggregation;
- split enqueue/worker/status/aggregate commands and a bounded `auto` command.

Offline verification currently passes 69 factory tests. The v2 commits
have been pushed and the `yicloud` checkout kept synchronized. A bounded live
GitHub smoke resolved exact SHAs with no API errors; after the high-precision
scope and documentation filters were applied, `CURENT/andes` was the leading
candidate and the awesome-list result was recorded in the rejection ledger.
Quality/difficulty v3 is implemented, pushed, and synchronized to `yicloud`:

- critic-aware preflight IDs prevent reuse across different critic models;
- independent task verification requires two exact quotes grounded in the
  private source and scores validity, answerability, consistency, and shortcut
  resistance;
- rollout admission is the intersection of depth and source-verification gates;
- trace IDs include the full sampling variant, so temperature/panel changes do
  not silently reuse old generations;
- multiple judge models can grade the same trace while the primary judge still
  defines candidate-SFT masks;
- named-solver panels report Wilson intervals and require multiple distinct
  model IDs/trials; zero solve rate is `unresolved`, not `hard`;
- optional deterministic stratified human-audit packets and calibration metrics cover
  precision, recall, agreement, critical errors, scientific depth, and trace
  value;
- the optional independent release path fails closed without population-matched human approval,
  calibrated medium/hard difficulty, source verification, and multi-judge
  consensus;
- candidate export recomputes the current preflight/verifier intersection, so
  a historically graded trace cannot re-enter after a newer task gate fails;
- author/solver, critic, verifier, and judge token budgets are independent, so
  a 16k reasoning budget does not multiply every admission-stage request;
- batch aggregation emits the tasks, preflights, verifications, traces, and
  grades required to reproduce those gates rather than only a candidate SFT.

The live Kimi smoke in `data-reasoning-smoke-v1/` exercised these boundaries:

- all three tasks passed the critic-aware depth preflight;
- two tasks passed exact-source verification; the third verifier response was
  incomplete at the 4096-token boundary and was recorded as an error, not
  admitted;
- gated export produced three selected historical/current traces, one
  grade-rejected trace, and excluded the unverified task's historical trace;
- the five-row blind audit packet contains no model, outcome, provenance, or
  automatic-decision fields; its separate key has three automatic positives
  and two negatives, with no unverified positive;
- the one-model/no-assessment diagnostic labels all three tasks
  `uncalibrated`; it is explicitly not a measured difficulty result;
- empty human reviews produce `release_approved: false`, and the release
  command exits with `human calibration has not approved release`.

The production policy was subsequently changed to permit one Kimi model to act
as critic, source verifier, and trace-value judge. A selected `sft.jsonl` row is
now marked `automatic_review.mode=single_model` and is directly usable without
human review after all three current-policy gates pass. The human/multi-model
path remains available only as an optional stricter audit.

The prepared 10k batch path is implemented but has not been started:

- a repository may contribute 3 through a configurable ceiling of source
  candidates rather than being discarded whenever it cannot fill the ceiling;
- the production recipe uses a ceiling of 16 and records actual accepted rows
  per repository and gate yield, so useful sample count is measured rather
  than assumed;
- 500 repository workers feed a queue-global Kimi request budget;
- deployment metrics are sampled every 30 seconds and the local request limit
  is adjusted between 500 and 1792 after subtracting this queue's active leases;
- metrics failure conservatively falls back to 500 concurrent admissions;
- repository jobs reserve their maximum possible SFT contribution atomically,
  stop being claimed when completed plus reserved rows cover 10000, and final
  aggregation deterministically trims to exactly 10000 matching rows/artifacts;
- the run contract records a 262144-token context window; solver/author output
  remains separately bounded at 65536 tokens and judge input at 900000
  characters (approximately 225k tokens);
- `factory/reasoning/run_10k_kimi.sh` is inert without `--execute`, preventing
  an accidental production launch.

The prepared 1k and 10k recipes consume only cleaned SciCodePile snapshots. The
corrected 1k pilot and 10k run enqueue up to all 3,600 prepared repositories and
stop claiming work when their accepted-row target is covered. The legacy
keyword/GitHub implementation remains available in the
codebase but is disabled in production launchers; production never turns the
dataset's repository names into fresh GitHub clones. SciCodePile preparation
download/index/extraction is a separate resumable step and does not itself
start the Kimi production batch.

The first 400-repository pilot exposed a verifier bottleneck: 681 verification
attempts failed to emit a complete result under the former 4,096-token output
ceiling, leaving only two candidate SFT rows despite 298 depth-preflight
acceptances. The corrected recipe separates author and solver budgets and uses
131,072 author, 196,608 solver, 16,384 critic, 65,536 verifier, and 32,768 judge
output tokens within the declared 262,144-token run context. New solver traces
persist `finish_reason` plus a derived `truncated` flag, and exported SFT rows
retain this termination audit metadata. The legacy shared `--max-tokens` remains
as a backwards-compatible fallback only.

The pilot uses a five-row reservation estimate per leased repository. This
allows 200 concurrent repository jobs for a 1,000-row target; the deterministic
aggregator still trims accepted output to exactly 1,000 rows. Reserving the
16-row per-repository ceiling had unintentionally limited the prior pilot to
about 63 simultaneous jobs and a 400-repository catalog could not reach 1,000
rows at the measured gate yield.

Full preparation of the pinned 83.7GB dataset was started on `yicloud` on
2026-09-21. Its durable paths are `.cache/scicodepile/raw`,
`.cache/scicodepile/prepared`, `.cache/scicodepile/catalog.jsonl`, and
`.cache/scicodepile/prepare.log`. This preparation directly consumes the
cleaned file rows; it never turns their repository names into GitHub clones.

## Smoke Endpoint

- Base URL: `http://10.100.184.127:5050/v1`
- Model: `Kimi-K3`
- The endpoint is slow. Use a 16k output budget and a sufficiently long request
  timeout. Do not put credentials or generated traces in Git.

## Resolved Design Decision

The first smoke uses a small heterogeneous core: one `derive_implement`, one
`diagnose_revise`, and one `compare_justify` task through a common schema. This
prevents the redesign from replacing the old function-implementation monoculture
with a different single archetype.

