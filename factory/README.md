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
