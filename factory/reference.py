"""Reference calibration: witness / empty anchors + per-step wall times.

Mirrors ScienceInfra factory/reference.py:
  * witness anchor  -- gold code must score 1.0 (every step's tests pass)
  * empty anchor    -- NotImplementedError body must score 0.0
  * calibration     -- sampled double-run determinism check
Also establishes the pristine per-step baseline wall times consumed by
funnel.py (steps that fail at baseline are excluded from candidate scoring).

Usage:
    python -m factory.reference [--problems 1,10,44] [--heavy-second-run]
Outputs:
    .work/ref/results.json       one JSON line per (problem, step)
    .work/ref/summary.json       per-problem witness/empty verdicts
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from . import config, lib

def empty_transform(step_src: str) -> dict | None:
    """TRANSFORM replacing the function body (after docstring) with raise."""
    tree = ast.parse(step_src)
    if not isinstance(tree.body[0], ast.FunctionDef):
        return None
    func = tree.body[0]
    body = func.body
    if not body:
        return None
    if (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    if not body:
        return None
    old = _segment(step_src, body[0], body[-1])
    if old is None or step_src.count(old) != 1:
        return None
    return {"edits": [{"file": "step.py", "old": old,
                       "new": 'raise NotImplementedError("scicode-factory empty anchor")'}]}


def _segment(code: str, first, last) -> str | None:
    """Raw source span from `first` to `last` statement (no re-indenting: the
    span must reproduce the original bytes exactly for exact-string edits)."""
    lines = code.splitlines(keepends=True)
    start = sum(len(l) for l in lines[: first.lineno - 1]) + first.col_offset
    end = sum(len(l) for l in lines[: last.end_lineno - 1]) + last.end_col_offset
    seg = code[start:end]
    return seg or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", default=None, help="comma-separated problem ids")
    ap.add_argument("--heavy-second-run", action="store_true",
                    help="also double-run steps heavier than HEAVY_STEP_WALL_SEC")
    args = ap.parse_args()
    ids = set(args.problems.split(",")) if args.problems else None

    problems = lib.load_problems(problem_ids=ids)
    ref_dir = config.WORK_DIR / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    results_path = ref_dir / "results.json"
    summaries = {}

    with results_path.open("w", encoding="utf-8") as out, lib.ScriptPool() as pool:
        for pid in sorted(problems, key=lambda x: int(x)):
            prob = problems[pid]
            step_results = []
            # witness anchor: gold passes everything
            gold_futs = [pool.submit(prob.assemble_script(i))
                         for i in range(len(prob.steps))]
            # empty anchor: witness of catastrophic failure
            empty_futs = []
            for i in range(len(prob.steps)):
                src = prob.step_function_source(i)
                t = empty_transform(src)
                defect = {"step": i, "transform": t} if t else None
                script = prob.assemble_script(i, defect=defect) if t else None
                empty_futs.append(None if script is None
                                  else pool.submit(script))

            witness_ok, empty_ok = True, True
            for i, s in enumerate(prob.steps):
                sid = s["step_number"]
                rec = {"problem_id": pid, "step_number": sid}
                if sid in config.ENV_SKIP_STEPS:
                    rec.update(status="skipped", reason="env-float-drift")
                else:
                    r = gold_futs[i].result()
                    rec.update(status=r.status, wall_sec=round(r.wall_sec, 2),
                               exit_code=r.exit_code,
                               stderr_tail=lib.last_exception_line(r.stderr_tail))
                    if r.status != "pass":
                        witness_ok = False
                    # determinism calibration (double run) for light steps
                    if r.status == "pass" and (
                            args.heavy_second_run
                            or r.wall_sec < config.HEAVY_STEP_WALL_SEC):
                        r2 = lib.run_script(prob.assemble_script(i))
                        rec["second_run"] = r2.status
                # empty anchor
                if empty_futs[i] is not None:
                    er = empty_futs[i].result()
                    rec["empty_status"] = er.status
                    if er.status == "pass":
                        empty_ok = False
                        rec["empty_note"] = "NOT_MONOTONE: empty implementation passed"
                step_results.append(rec)
                out.write(json.dumps(rec) + "\n")

            summaries[pid] = {
                "n_steps": len(prob.steps),
                "witness_ok": witness_ok,
                "empty_ok": empty_ok,
                "failed_steps": [r["step_number"] for r in step_results
                                 if r.get("status") not in ("pass", "skipped")],
            }
            flag = "OK" if witness_ok else "WITNESS-FAIL"
            print(f"[{flag}] {pid} {prob.record['problem_name']}: "
                  f"{len(prob.steps)} steps, "
                  f"failed={summaries[pid]['failed_steps']}")

    with (ref_dir / "summary.json").open("w", encoding="utf-8") as fp:
        json.dump(summaries, fp, indent=2)
    n_bad = sum(1 for s in summaries.values() if not s["witness_ok"])
    print(f"\nreference: {len(summaries)} problems, witness failed: {n_bad}")
    print(f"wrote {results_path}")


if __name__ == "__main__":
    main()
