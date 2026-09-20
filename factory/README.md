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
             -> native-thinking rollout -> outcome-independent trace grading
             -> canonical thinking-aware SFT
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
    --limit 3 --attempts 1 --max-tokens 16384 \
    --timeout 2400 --concurrency 3
```

The primary files are:

| file | purpose |
|---|---|
| `tasks.jsonl` | validated task contracts and private source provenance |
| `preflight.jsonl` | structural and semantic reasoning-depth admission |
| `traces.jsonl` | verbatim messages, including native `reasoning_content` |
| `grades.jsonl` | scientific trace-value scores and per-message loss policy |
| `sft.jsonl` | canonical training records with separate reasoning/content masks |
| `run_manifest.json` | code, model, parameter, input, and artifact hashes |

`sft.jsonl` selection is controlled by reasoning quality, not `reward == 1`.
Executable outcomes may be attached to traces as auxiliary evidence. The
canonical format keeps `reasoning_content` even when `--inline-thinking` is used;
trainer-specific conversion must not silently discard it.

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

Discovery writes a sibling error JSONL and continues past individually failed
queries or commit pins after the built-in retries are exhausted.

A bounded one-command run is:

```bash
python -m factory.reasoning.batch auto \
  --output-root .work/reasoning-batch \
  --cache-root .work/repository-cache \
  --query-limit 3 --repository-limit 10 \
  --workers 4 --repository-slots 2 --llm-slots 3 \
  --profile-model Kimi-K3 --author-model Kimi-K3 \
  --critic-model Kimi-K3 --solver-model Kimi-K3 --judge-model Kimi-K3
```

For production, discovery/enqueue, workers, status, and aggregation can be run
separately:

```bash
python -m factory.reasoning.discovery \
  --out .work/catalog.jsonl --min-stars 10 --limit 100

python -m factory.reasoning.batch enqueue \
  --db .work/batch.sqlite3 --catalog .work/catalog.jsonl \
  --llm-slots 3 --repository-slots 2 \
  --profile-model Kimi-K3 --author-model Kimi-K3 \
  --critic-model Kimi-K3 --solver-model Kimi-K3 --judge-model Kimi-K3

python -m factory.reasoning.batch worker \
  --db .work/batch.sqlite3 --cache-root .work/repository-cache \
  --output-root .work/reasoning-batch

python -m factory.reasoning.batch aggregate \
  --db .work/batch.sqlite3 --out .work/reasoning-batch/sft.jsonl
```

The queue uses SQLite WAL, atomic job leases, job/resource heartbeats, bounded
retries, and process-global resource slots. A repository snapshot plus the
canonical recipe hash defines job identity, so replaying the same enqueue is
idempotent while a model/configuration change creates a new shard. Each
repository writes only to
`repositories/<slug>/<job-id>/`; aggregation reads completed shards, sorts by
`trace_id`, rejects conflicting duplicates, and atomically replaces the global
JSONL. Slot counts are immutable within a queue so independent workers cannot
silently choose incompatible concurrency budgets.

This SQLite implementation is deliberately single-host. Multiple processes on
one machine are supported; a multi-node deployment should preserve the same
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

* **Difficulty is measured, not labeled** (ScienceIDE principle): seeds carry
  no tier; run a named-solver panel over traces and record pass rates before
  rating. `floor` in calibration tasks and per-seed solver pass rates are the
  raw material for that.
* `verify.py` currently authors **single sub-step** seeds; multi-substep
  decomposition (SciCode's 338-subproblem structure) is future work.
* Novelty screen is lexical (Jaccard + names). Semantic near-duplicates of
  official problems can slip through when the wording differs — treat solver
  pass rates on official-vs-seed comparisons as the secondary alarm.
* Demo: two hand-written proposals over scipy `inconsistent` / `vq` pass all
  gates (`seeds-demo/`); the LLM proposal stage only scales once an endpoint
  is configured.
* The reasoning-first v1 judge is model-based and should be calibrated against
  human-reviewed traces before producing a large training mixture. The initial
  three-task Kimi run is a plumbing smoke test, not evidence of difficulty.
* Batch discovery uses a curated vocabulary plus optional model expansion. The
  current dual-evidence function ranker is lexical, intentionally cheap and
  auditable; embedding retrieval and a calibrated scientific verifier remain
  quality upgrades, not prerequisites for race-safe production.
