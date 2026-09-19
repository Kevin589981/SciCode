# scicode-factory — ScienceIDE-style automated task construction for SciCode

This directory ports the
[ScienceIDE/ScienceInfra](https://github.com/Gen-Verse/ScienceInfra) automated
task-authoring pipeline ("propose broadly; establish validity by execution")
to the SciCode benchmark. Starting from the 15 validation problems (gold code
+ official tests + `test_data.h5` numerical targets), it manufactures
execution-validated tasks:

* **repair** — an AST-based operator injects exactly one localized semantic
  defect into a sub-step's gold function; the agent must find and fix it so
  the downstream sub-steps pass again. The reference fix is the exact inverse
  of the injection (roundtrip-verified).
* **implementation** — a sub-step's function body is excised; the agent
  reimplements it from the description/docstring so the downstream steps
  reproduce the incumbent numerics.

## Pipeline

```
python -m factory.reference            # witness(gold=1.0)/empty(=0.0) anchors + baseline wall times
python -m factory.operators            # inject candidates  -> .work/cand-inject.jsonl
python -m factory.excise               # excise candidates  -> .work/cand-excise.jsonl
python -m factory.funnel --candidates .work/cand-inject.jsonl .work/cand-excise.jsonl \
                       --out .work/funnel          # execution funnel: silent/floor_high/... -> survivor
python -m factory.package --verdicts .work/funnel/verdicts.jsonl \
                       --candidates .work/cand-inject.jsonl .work/cand-excise.jsonl \
                       --out tasks                 # sparse task dirs
python -m factory.gate_pack --tasks tasks          # static + roundtrip + leakscan gates
python -m factory.gate_pack --tasks tasks --selftest
python -m factory.run_anchors --tasks tasks        # oracle=1.0 / nop=floor anchor checks
python -m factory.build_dataset --tasks tasks --out data --parquet
```

All scripts accept `--problems 1,10,...` (or `--limit N` for funnel) to scope
a partial run.

## Configuration

Environment variables (see `factory/config.py`):

| var | meaning | default |
|---|---|---|
| `SCICODE_DATA_DIR` | dir with `validation.jsonl` + `gdrive/test_data.h5` | `/root/ScienceIDE-workspace/scicode-data` |
| `SCICODE_TEST_H5` | explicit h5 path override | `$SCICODE_DATA_DIR/gdrive/test_data.h5` |
| `SCICODE_FACTORY_PYTHON` | interpreter for assembled test scripts | `sys.executable` |
| `SCICODE_FACTORY_WORKERS` | parallel script executions | `8` |
| `SCICODE_STEP_TIMEOUT` | per-step script timeout (s) | `600` |
| `SCICODE_FLOOR_MAX` | defective baseline above this => `floor_high` reject | `0.65` |

## Schemas (borrowed from ScienceInfra factory)

* **TRANSFORM** — `{"edits": [{"file", "old", "new"}]}`; `old` must occur
  exactly once in the target blob. `break`/`fix` are exact inverses;
  roundtrip = `apply(fix, apply(break, pristine)) == pristine` byte-for-byte.
* **candidate** — `{id, source: inject|excise, family, tree, note, break,
  fix, meta{problem_id, step_number, file, line, k_sites, target_checks}}`.
* **verdict** — funnel record per candidate: `status ∈ {survivor, silent,
  floor_high, invalid, skipped}`, measured per-step pass/fail, `floor`
  (defective baseline score over downstream steps), `symptom`.
* **sparse task** — `tasks/<category>/<tier>/<id>/` with `task.toml`,
  `instruction.md`, `defect.json`, `fix.json`, `authoring/provenance.json`;
  `reward_repair = max(0, (reward - floor) / (1 - floor))` (ScienceIDE Eq. 1).

## SciCode-specific adaptations

* Judging reuses the official contract: cumulative code (deps + prior steps)
  + `scicode.parse.parse.process_hdf5_to_tuple(step, N, h5)` + the official
  `test_cases`; a step passes iff the assembled script exits 0 — the same
  script assembly as `eval/scripts/test_generated_code.py`.
* A defect at sub-step k cascades to steps k..last, so scoring and the
  instruction's acceptance criterion cover the whole downstream chain.
* Injection operators are `ast`-based (Fortran in ScienceIDE had to be
  regex+liveness; Python gives us docstring/string safety for free).
* Difficulty tiers are `unrated` on purpose: ScienceInfra measures difficulty
  with named-solver pass-rate probing. Do that before assigning tiers
  (`measured_pass_rates` belongs in `task.toml [metadata.difficulty_facts]`).

## Known limitations / next steps

* Step `70.8` is excluded (`ENV_SKIP_STEPS`): its gold code fails on this
  machine's BLAS/LAPACK float path (verified correct by high-precision
  arithmetic), candidates whose downstream chain contains it are `skipped`.
* Materialized workspaces run under a plain venv; for untrusted agent runs
  wrap `factory.workspace.materialize` + `run_anchors` in a container
  (Harbor-style), like ScienceIDE's `to_harbor.py`.
* Official-test evidence is per-step binary; partial credit inside a step
  (SciCode's up-to-3-4 asserts) is currently collapsed.
