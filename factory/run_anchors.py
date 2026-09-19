"""Anchor evaluation: oracle must score 1.0, nop must score the measured floor.

ScienceInfra runs `harbor run ... -a oracle` (expect 1.0) and `-a nop`
(expect 0.0 after floor normalization) on every compiled task. This module is
the container-free analogue for scicode-factory:

    oracle   workspace materialized pristine (the reference fix applied)  ->
             every downstream step must pass           => reward_repair = 1.0
    nop      workspace materialized with the defect, delivered untouched ->
             measured score must equal the funnel floor => reward_repair = 0.0

Usage:
    python -m factory.run_anchors --tasks tasks [--tasks-limit N] [--oracle-only]
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from . import lib, workspace


def _run_downstream(prob: lib.Problem, tdir: Path, ws_dir: Path, start_idx: int):
    n_pass, details = 0, []
    for j in prob.downstream_indices(start_idx):
        sid = prob.steps[j]["step_number"]
        import subprocess, sys
        r = subprocess.run([sys.executable, str(ws_dir / "run_step.py"), str(j)],
                           capture_output=True, text=True, cwd=ws_dir, timeout=900)
        status = "pass" if r.returncode == 0 else "fail"
        details.append({"step": sid, "status": status,
                        "stderr_tail": (r.stderr or "")[-300:]})
        n_pass += status == "pass"
    return n_pass, details


def anchor_task(tdir: Path, problems: dict[str, lib.Problem],
         oracle_only: bool = False) -> dict:
    prov = json.loads((tdir / "authoring" / "provenance.json")
                      .read_text(encoding="utf-8"))
    cand = prov["candidate"]
    meta = cand["meta"]
    prob = problems[meta["problem_id"]]
    idx = lib.step_index_by_number(prob, meta["step_number"])
    n_down = len(prob.steps) - idx
    out = {"task_id": cand["id"]}

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "oracle"
        workspace.materialize(tdir, ws, problems, apply="fix")
        n_pass, details = _run_downstream(prob, tdir, ws, idx)
        out["oracle"] = {"n_pass": n_pass, "n_downstream": n_down,
                         "reward": n_pass / n_down, "details": details}

    if not oracle_only:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "nop"
            workspace.materialize(tdir, ws, problems, apply="break")
            n_pass, details = _run_downstream(prob, tdir, ws, idx)
            floor_expected = prov["funnel"]["floor"]
            out["nop"] = {"n_pass": n_pass, "n_downstream": n_down,
                          "score": n_pass / n_down,
                          "floor_expected": floor_expected,
                          "match": abs(n_pass / n_down - floor_expected) < 1e-9,
                          "details": details}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, default=Path("tasks"))
    ap.add_argument("--limit", type=int, default=None, dest="tasks_limit")
    ap.add_argument("--oracle-only", action="store_true")
    args = ap.parse_args()

    problems = lib.load_problems()
    task_dirs = sorted(p.parent for p in args.tasks.rglob("task.toml"))
    if args.tasks_limit:
        task_dirs = task_dirs[: args.tasks_limit]
    n_ok = 0
    rep_dir = Path(".work/anchors")
    rep_dir.mkdir(parents=True, exist_ok=True)
    for tdir in task_dirs:
        res = anchor_task(tdir, problems, args.oracle_only)
        oracle_ok = res["oracle"]["reward"] == 1.0
        nop_ok = args.oracle_only or (res["nop"]["match"]
                                      and res["nop"]["score"] < 1.0)
        ok = oracle_ok and nop_ok
        n_ok += ok
        print(f"[{'OK' if ok else 'FAIL'}] {res['task_id']} "
              f"oracle={res['oracle']['n_pass']}/{res['oracle']['n_downstream']}"
              + ("" if args.oracle_only
                 else f" nop={res['nop']['n_pass']}/{res['nop']['n_downstream']}"
                      f" (floor {res['nop']['floor_expected']:.3f})"))
        with (rep_dir / f"{res['task_id']}.json").open("w", encoding="utf-8") as fp:
            json.dump(res, fp, indent=2)
    print(f"\nanchors: {n_ok}/{len(task_dirs)} pass "
          f"(reports in {rep_dir})")
    if n_ok != len(task_dirs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
