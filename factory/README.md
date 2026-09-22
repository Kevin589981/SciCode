# scicode-factory — ScienceIDE-style automated data construction for SciCode-like QA

This directory ports the
[ScienceIDE/ScienceInfra](https://github.com/Gen-Verse/ScienceInfra) automated
task-authoring philosophy — **"propose broadly; establish validity by
execution"** — to the production of *new* SciCode-like scientific coding data.

The **primary deliverable is raw solver traces** (verbatim message logs +
verifier feedback + rewards). QA pairs can be reconstructed from traces, and
the traces themselves are directly usable for SFT. Extraction is downstream,
not upstream — mirroring how the official SciCode benchmark derives its QA
pairs from solver trajectories.

> Contamination contract: official SciCode problems, prompts, tests and
> oracles are **never** used as seeds or source material. They are used in
> exactly one place — as a *calibration sandbox* for the pipeline's own
> gates (below), and as the fingerprint corpus for the novelty screen.

## Two layers

### 1. Calibration layer (the verified machinery)

`reference / operators / excise / funnel / package / gate_pack /
run_anchors / build_dataset / workspace`

A faithful port of the ScienceInfra factory: defect injection (AST-based),
function-body excision, execution funnel (silent / floor_high / survivor),
content-anchored leak scan with selftest, oracle/nop anchors, and
`reward_repair = max(0, (r - floor) / (1 - floor))` scoring.

This layer was validated end-to-end on the official validation split
(120 candidates -> 68 survivors -> 68/68 gates and anchors pass); those
run artifacts live under `.work/calibration-official/` and are **calibration
evidence, not QA data**. The machinery is source-agnostic and reused by layer 2.

### 1.5 Environment construction layer (ScienceIDE environments/ workflow)

`envbuild/vet / calibrate / harvest`

Ports the upstream **environment** lifecycle that lives in
ScienceIDE/environments/ (NOT ScienceInfra, which only consumes built envs):
repo vetting (pin/archive/sha256/license + hazards + LLM-proposed module
decomposition with an expert-approval checkpoint), check calibration (the
measurement toolchain upstream never published: ulp-variant spread, fault
injection probes, tolerance recommendation, double-run determinism, warrant
checkpoint), and the writer brief. Produces environments/<env>/{source,
validation, authoring} mirroring upstream layout.

```
python -m factory.envbuild.vet --repo <url-or-path> --slug <name> [--commit <sha>] --out environments
python -m factory.envbuild.calibrate --seed seeds/<slug>.json --repo-root <import-root> \
    --out environments/<env>/validation/<check>
python -m factory.envbuild.harvest --env environments/<env>
```

Human checkpoints kept by design (upstream: "agent proposes, curator
decides"): module.json `approval` and rubric `warrant.finalized_by` stay
pending until a human fills them.

### 2. Authoring + trace layer (the data factory)

```
mine.py      AST heuristics over a pinned upstream repo checkout
             -> self-contained scientific functions
propose.py   LLM turns a mined function into a SciCode-style sub-step
             (question / background / literal test inputs)   [broad proposals]
verify.py    EXECUTES every proposal against the original code:
             import / sandboxed-inputs / numeric contract / runtime /
             determinism (two process runs, 1e-12) / novelty vs official
             SciCode  -> survivors become seeds                    [the gate]
novelty.py   token-Jaccard + function-name screen vs official benchmark
traces.py    solver LLM episodes over seeds (multi-turn with verifier
             feedback); RAW traces are the primary output
```

```
# 1. mine (repo = checkout of an upstream scientific codebase)
python -m factory.author.mine --repo /path/to/scipy --out .work/mined.jsonl

# 2. propose (needs SCICODE_LLM_* endpoint config)
python -m factory.author.propose --mined .work/mined.jsonl \
    --repo-meta repo_meta.json --out .work/proposals.jsonl

# 3. verify -> seeds/  (scicode-format records + official-layout test_data.h5)
python -m factory.author.verify --proposals .work/proposals.jsonl \
    --repo-root /path/to/scipy --out seeds

# 4. solve -> data/traces.jsonl (PRIMARY) + data/sft.jsonl (derived)
python -m factory.solve.traces --seeds seeds --out data \
    --attempts 4 --max-turns 3
```

Seeds are emitted in the official SciCode record layout with targets in the
official h5 layout (`<step>/test<i>/var<j>`), so `scicode.parse.
process_hdf5_to_tuple` and the existing eval stack consume them unchanged.

### 3. Reasoning-first trace factory

`factory.reasoning` is the SFT-oriented path. It borrows ScienceIDE's broad
proposal and measured-filtering pattern without reproducing the repair
benchmark or treating executable pass/fail as the definition of data quality.

It produces three task archetypes through a shared schema:

* `derive_implement` — derive a method from scientific assumptions, analyze
  regimes, then implement it;
* `diagnose_revise` — distinguish plausible causes from observations and revise
  a flawed scientific or numerical method;
* `compare_justify` — compare alternatives under explicit criteria, justify a
  choice, then deliver an implementation or analysis.

The vertical pipeline is:

```
mined source -> heterogeneous task authoring -> reasoning-depth preflight
             -> exact-source scientific verification -> native-thinking rollout
             -> outcome-independent Kimi grading -> automatically reviewed SFT
```

Run the complete pipeline with one source candidate per archetype:

```bash
export SCICODE_LLM_BASE_URL=http://host:port/v1
export SCICODE_LLM_API_KEY=dummy
export SCICODE_LLM_MODEL=Kimi-K3

python -m factory.reasoning.pipeline \
    --mined .work/scipy-mined.jsonl \
    --repo-meta .work/scipy-repo-meta.json \
    --out-dir data-reasoning-smoke \
    --verifier-model Kimi-K3 \
    --limit 3 --attempts 1 --max-tokens 16384 \
    --timeout 2400 --concurrency 3
```

`--max-tokens` is reserved for authoring and solver traces, where long-form
reasoning is useful. The bounded defaults for the critic, source verifier, and
judge are respectively 4096, 4096, and 8192 tokens; override them with
`--critic-max-tokens`, `--verifier-max-tokens`, and `--judge-max-tokens`.
This prevents a 16k solver budget from multiplying the cost of every gate.

The primary files are:

| file | purpose |
|---|---|
| `tasks.jsonl` | validated task contracts and private source provenance |
| `preflight.jsonl` | structural and semantic reasoning-depth admission |
| `verification.jsonl` | source-grounded validity, answerability, and shortcut gate |
| `traces.jsonl` | verbatim messages, including native `reasoning_content` |
| `grades.jsonl` | scientific trace-value scores and per-message loss policy |
| `sft.jsonl` | automatically reviewed training records with separate reasoning/content masks |
| `run_manifest.json` | code, model, parameter, input, and artifact hashes |

`sft.jsonl` selection is controlled by reasoning quality, not `reward == 1`.
Executable outcomes may be attached to traces as auxiliary evidence. The
canonical format keeps `reasoning_content` even when `--inline-thinking` is used;
trainer-specific conversion must not silently discard it. Pipeline export also
recomputes the current critic/source-verifier admission intersection, so an old
graded trace cannot re-enter candidate SFT after its task fails a newer gate.
Admission and grading resume identities include their policy versions; changing
a rubric therefore triggers fresh decisions instead of relabeling old model
outputs as if they had been produced under the new policy.

For high-volume production, one Kimi model may fill the critic, verifier, and
judge roles. A selected row records `automatic_review.mode=single_model` and is
usable directly: it has passed current-policy reasoning-depth admission,
exact-source verification, and trace-value grading. This is model review, not a
claim of independent human or multi-model agreement.

### Optional independent calibration

The automatic Kimi path does not require human review. For smaller, independently
audited releases, `factory.reasoning.difficulty` can additionally measure task
difficulty with multiple named solver models and repeated attempts. A separate
evaluator scores whether each response actually solved the task; this is kept
separate from trace training-value grading. At least two distinct solver model
IDs and four valid trials are required by the default panel. A zero solve rate
is `unresolved`, not automatically `hard`; a one-model smoke is
`uncalibrated`.

```bash
python -m factory.reasoning.difficulty \
  --tasks .work/batch.tasks.jsonl \
  --preflight .work/batch.preflight.jsonl \
  --verification .work/batch.verification.jsonl \
  --preflight-model CriticModel --verifier-model VerifierModel \
  --panel factory/reasoning/panel.example.json \
  --out-dir .work/difficulty --concurrency 2
```

`factory.reasoning.quality` creates a deterministic blind audit sample spanning
archetype, difficulty, and automatic accept/reject strata. Model identities,
outcomes, and automatic decisions are kept in a separate `.key.jsonl`; humans
add reviews to each packet row's `human_reviews`. Calibration rejoins the key
and measures task-validity rate,
scientific depth, trace value, automatic-selection precision/recall, critical
errors, and overlapping-reviewer agreement.

```bash
python -m factory.reasoning.quality audit \
  --tasks .work/batch.tasks.jsonl --traces .work/batch.traces.jsonl \
  --grades .work/batch.grades.jsonl \
  --verifications .work/batch.verification.jsonl \
  --difficulty .work/difficulty/difficulty.jsonl \
  --out .work/human-audit.jsonl --sample-size 50

# After reviewers fill human_reviews:
python -m factory.reasoning.quality calibrate \
  --packet .work/human-audit.jsonl \
  --key .work/human-audit.jsonl.key.jsonl \
  --out .work/calibration.json

python -m factory.reasoning.quality release \
  --tasks .work/batch.tasks.jsonl --traces .work/batch.traces.jsonl \
  --candidate-sft .work/candidate-sft.jsonl \
  --grades .work/batch.grades.jsonl \
  --verifications .work/batch.verification.jsonl \
  --difficulty .work/difficulty/difficulty.jsonl \
  --calibration .work/calibration.json \
  --policy factory/reasoning/release_policy.example.json \
  --out .work/release-sft.jsonl
```

This optional stricter release fails closed unless the human calibration
belongs to the exact trace population and passes its thresholds. By default,
each released row must also
have a calibrated `medium`/`hard` band, an author-independent source verifier,
and agreement from at least two solver-independent judge model IDs. Repeating
the same model under different aliases does not count as independence.

### Batch repository factory

`factory.reasoning.batch` scales the reasoning-first path across public
scientific repositories. The design follows a staged funnel instead of asking
an expensive model to browse an unbounded repository universe:

```
curated science queries -> rate-limited GitHub metadata search -> deduplicate
  -> pin exact HEAD commits -> durable repository queue -> README relevance
  -> license gate -> AST mining -> repository-intent × code-evidence ranking
  -> three-archetype reasoning pipeline -> isolated SFT shards -> aggregation
```

Production uses the cleaned file-level
`SciCodePile/SciCode-Domain-Code` dataset as its primary source. It reconstructs
read-only repository snapshots directly from the published `repo_name`,
`file_path`, `content`, and `keyword` columns; it does not turn the dataset into
a name list and re-clone changing GitHub repositories. The older curated
keyword GitHub search remains available for separate experiments, but the 1k
and 10k production launchers disable it and consume only these cleaned local
snapshots.

Prepare 3,600 primary snapshots once on a machine with sufficient disk. The
download is pinned to the published dataset revision, resumable, and the raw
83.7GB files are retained so indexing/extraction never redownloads them:

```bash
/root/scicode-factory-venv/bin/python -m factory.reasoning.scicodepile_dataset \
  --raw-dir /root/ScienceIDE-workspace/SciCode/.cache/scicodepile/raw \
  --prepared-root /root/ScienceIDE-workspace/SciCode/.cache/scicodepile/prepared \
  --catalog /root/ScienceIDE-workspace/SciCode/.cache/scicodepile/catalog.jsonl \
  --repository-limit 3600
```

Only repositories containing Python files are eligible because this SciCode
factory currently mines Python ASTs. Selection is deterministic and
round-robins the dataset's scientific keywords instead of taking the first or
largest domains. Unsafe paths are rejected, duplicate files are content-hashed,
and every prepared repository receives a dataset revision and snapshot hash.
The dataset is marked Apache-2.0, but its rows do not reliably retain each
upstream repository's license file; production therefore records the upstream
license as unknown instead of falsely relabeling source code as Apache-2.0.

A bounded one-command run is:

```bash
python -m factory.reasoning.batch auto \
  --output-root .work/reasoning-batch \
  --cache-root .work/repository-cache \
  --repository-source scicodepile \
  --scicodepile-catalog .cache/scicodepile/catalog.jsonl \
  --repository-limit 10 \
  --workers 4 --repository-slots 2 --llm-slots 3 \
  --profile-model Kimi-K3 --author-model Kimi-K3 \
  --critic-model Kimi-K3 --verifier-model Kimi-K3 \
  --solver-model Kimi-K3 --judge-model Kimi-K3
```

One repository contributes between zero and `--tasks-per-repo` accepted rows.
The default ceiling is three. Production can raise it (the prepared 10k recipe
uses 16), while `--min-tasks-per-repo 3` prevents a repository with only 3–15
good source candidates from being discarded merely because it cannot fill the
ceiling. The batch report records accepted rows per completed repository and the
accepted/selected fraction; no fixed useful-row yield is assumed in advance.

The prepared 10k launcher is inert unless explicitly armed:

```bash
# Prints the configuration and exits; does not start work.
bash factory/reasoning/run_10k_kimi.sh

# Explicit production start.
bash factory/reasoning/run_10k_kimi.sh --execute
```

It uses a 262144-token context-window contract, a 65536-token solver/author
output ceiling, 500 repository workers, and an exact 10000-row aggregation
target. `max_tokens` is an output limit, not the model context length; the
separate `--context-window-tokens` value validates the judge input/output
budget. The launcher does not start automatically when installed or tested.

LLM admission is deployment-aware. Every 30 seconds the controller reads
`http://10.100.184.127:29000/metrics`, equivalent to:

```bash
curl -s http://10.100.184.127:29000/metrics | \
  awk '/^smg_worker_requests_active{/{n += $NF} END{print n}'
```

It estimates external traffic as `deployment active - this queue's active
leases`, then clamps this queue's dynamic capacity to 500–1792. The SQLite
transaction checks the dynamic limit while acquiring each request slot, so 500
workers cannot race and each allocate the whole deployment budget. If metrics
are unavailable, capacity falls back to 500 rather than running unbounded.

The 10000-row target is also enforced transactionally. A leased repository
reserves its configured maximum contribution; workers pause when completed plus
reserved rows cover the target and resume if rejected gates release that
reservation. Aggregation trims the final at-most-one-repository overshoot
deterministically and writes exactly 10000 rows with matching companion
artifacts.

For production, discovery/enqueue, workers, status, and aggregation can be run
separately:

```bash
python -m factory.reasoning.discovery \
  --out .work/catalog.jsonl --min-stars 10 --limit 100

python -m factory.reasoning.batch enqueue \
  --db .work/batch.sqlite3 --catalog .work/catalog.jsonl \
  --llm-slots 3 --repository-slots 2 \
  --profile-model Kimi-K3 --author-model Kimi-K3 \
  --critic-model Kimi-K3 --verifier-model Kimi-K3 \
  --solver-model Kimi-K3 --judge-model Kimi-K3 \
  --additional-judge-model SecondJudgeModel

python -m factory.reasoning.batch worker \
  --db .work/batch.sqlite3 --cache-root .work/repository-cache \
  --output-root .work/reasoning-batch

python -m factory.reasoning.batch aggregate \
  --db .work/batch.sqlite3 --out .work/reasoning-batch/accepted-sft.jsonl
```

The queue uses SQLite transactions, atomic job leases, job/resource heartbeats,
bounded retries, and process-global resource slots. The portable default is
rollback (`DELETE`) journaling; `--sqlite-journal WAL` is opt-in for a proven
local filesystem. A repository snapshot plus the
canonical recipe hash defines job identity, so replaying the same enqueue is
idempotent while a model/configuration change creates a new shard. Each
repository writes only to
`repositories/<slug>/<job-id>/`; aggregation reads completed shards, sorts by
`trace_id`, rejects conflicting duplicates, and atomically replaces the global
JSONL. Slot counts are immutable within a queue so independent workers cannot
silently choose incompatible concurrency budgets.

This SQLite implementation is deliberately single-host. Multiple processes on
one machine are supported. Some virtual/shared filesystems do not implement WAL
shared-memory semantics; keep the portable default there or use `--db` to put a
WAL queue on local storage. A multi-node deployment should preserve the same
lease and idempotency protocol over a network database rather than place the
SQLite file on an arbitrary shared filesystem.

## Configuration

Environment variables:

| var | meaning | default |
|---|---|---|
| `SCICODE_DATA_DIR` | dir with official `validation.jsonl`/`test.jsonl` (novelty + calibration) | `/root/ScienceIDE-workspace/scicode-data` |
| `SCICODE_TEST_H5` | official h5 path (calibration layer) | `$SCICODE_DATA_DIR/gdrive/test_data.h5` |
| `SCICODE_LLM_BASE_URL` / `SCICODE_LLM_API_KEY` / `SCICODE_LLM_MODEL` | OpenAI-compatible endpoint for propose + solve | **required for layer 2** |
| `SCICODE_FACTORY_PYTHON` / `SCICODE_FACTORY_WORKERS` / `SCICODE_STEP_TIMEOUT` | execution environment | `sys.executable` / `8` / `600` |

## Design notes / known limitations

* **Difficulty is measured, not labeled** (ScienceIDE principle). The named
  solver panel, Wilson interval, minimum panel coverage, and explicit
  `unresolved`/`uncalibrated` states implement this rule.
* `verify.py` currently authors **single sub-step** seeds; multi-substep
  decomposition (SciCode's 338-subproblem structure) is future work.
* Novelty screen is lexical (Jaccard + names). Semantic near-duplicates of
  official problems can slip through when the wording differs — treat solver
  pass rates on official-vs-seed comparisons as the secondary alarm.
* Demo: two hand-written proposals over scipy `inconsistent` / `vq` pass all
  gates (`seeds-demo/`); the LLM proposal stage only scales once an endpoint
  is configured.
* Model judges remain fallible. At scale the selected SFT rows therefore retain
  the exact critic/verifier/judge models and policy versions. Human/multi-model
  calibration remains available as an optional stricter release path rather
  than a production requirement.
* The primary corpus is SciCodePile's cleaned, revision-pinned file data. The
  curated vocabulary plus optional model expansion is retained as channel two.
  The current dual-evidence function ranker is lexical, intentionally cheap and
  auditable. Its output is subsequently checked by the source-grounded
  scientific verifier; embedding retrieval remains a future recall/ranking
  upgrade rather than an unrecorded quality assumption.
