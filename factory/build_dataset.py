"""Build the QA dataset from packaged, gate-passed tasks.

ScienceInfra scienceinfra/datasets/build_dataset.py equivalent: rows carry a
prompt (instruction + hint), rule-based reward pointer, and stratified split.
Hint levels (their L1/L2/L3):
    L1 = defect file + line ("## Where to look" block)
    L2 = defect file only
    L3 = no hint (control)

Row fields: task_id, problem_id, category, family, step_number, floor,
prompt (chat-style), hint_level, split, workspace (materialization spec),
reference_solution (fix transform), reward {style: rule, reward_key}.

Split: deterministic hash over task_id, stratified per (category, family);
eval share <= EVAL_SHARE (ScienceInfra caps at 25%).

Usage:
    python -m factory.build_dataset --tasks tasks --out data \
        [--levels L1,L2,L3] [--parquet]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from . import lib


def _hint(level: str, cand: dict) -> str:
    meta = cand["meta"]
    line = meta.get("line")
    fname = meta["file"]
    if level == "L1" and line:
        return ("\n\n## Where to look\n"
                f"The defect is in `{fname}` near line {line}.")
    if level in ("L1", "L2"):
        return ("\n\n## Where to look\n"
                f"The defect is in `{fname}`.")
    return ""


def build_rows(tasks_dir: Path, levels: list[str],
               eval_share: float = 0.25) -> list[dict]:
    problems = lib.load_problems()
    rows = []
    for tt in sorted(tasks_dir.rglob("task.toml")):
        tdir = tt.parent
        prov = json.loads((tdir / "authoring" / "provenance.json")
                          .read_text(encoding="utf-8"))
        cand = prov["candidate"]
        meta = cand["meta"]
        instruction = (tdir / "instruction.md").read_text(encoding="utf-8")
        funnel = prov.get("funnel", {})
        for level in levels:
            h = _hint(level, cand)
            rows.append({
                "task_id": cand["id"],
                "problem_id": meta["problem_id"],
                "category": "repair" if cand["source"] == "inject"
                            else "implementation",
                "family": cand["family"],
                "step_number": meta["step_number"],
                "floor": funnel.get("floor"),
                "hint_level": level,
                "prompt": [{"role": "user", "content": instruction + h}],
                "reference_solution": cand["fix"],
                "reward": {"style": "rule",
                           "reward_key": "reward_repair",
                           "floor": funnel.get("floor")},
                "workspace": {
                    "task_dir": str(tdir),
                    "materialize": "factory.workspace.materialize(apply='break')",
                },
                "task_name": cand["id"],  # leakage guard: identical task_name
                                          # must land in the same split
            })
    # stratified deterministic split
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[(r["category"], r["family"])].append(r)
    for key, rs in groups.items():
        by_task = sorted({r["task_id"] for r in rs})
        eval_tasks = {t for t in by_task
                      if int(hashlib.sha256(f"split:{t}".encode()).hexdigest(), 16)
                      % 1000 < int(1000 * eval_share)}
        for r in rs:
            r["split"] = "eval" if r["task_id"] in eval_tasks else "train"
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, default=Path("tasks"))
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--levels", default="L1,L2,L3")
    ap.add_argument("--parquet", action="store_true",
                    help="also write parquet (requires pyarrow/pandas)")
    args = ap.parse_args()
    levels = [s.strip() for s in args.levels.split(",")]

    rows = build_rows(args.tasks, levels)
    args.out.mkdir(parents=True, exist_ok=True)
    per_split: dict[str, list] = defaultdict(list)
    for r in rows:
        per_split[r["split"]].append(r)
    for split, rs in sorted(per_split.items()):
        path = args.out / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as fp:
            for r in sorted(rs, key=lambda x: (x["task_id"], x["hint_level"])):
                fp.write(json.dumps(r) + "\n")
        print(f"{split}: {len(rs)} rows -> {path}")
        if args.parquet:
            try:
                import pandas as pd
                pd.DataFrame(rs).to_parquet(path.with_suffix(".parquet"))
            except ImportError:
                print("  (pyarrow/pandas unavailable, parquet skipped)")
    n_tasks = len({r["task_id"] for r in rows})
    print(f"dataset: {len(rows)} rows / {n_tasks} tasks / levels {levels}")


if __name__ == "__main__":
    main()
