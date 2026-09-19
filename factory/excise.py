"""Implementation-excision candidates: erase a sub-step's function body.

Mirrors ScienceInfra factory/excise.py. The break transform replaces the body
(after the docstring, so the signature + contract stay visible) with
`raise NotImplementedError`, and the fix restores the body exactly. The agent
facing such a task must reimplement the routine so the downstream steps
reproduce the incumbent numerics.

Usage:  python -m factory.excise --out .work/cand-excise.jsonl [--problems 1,10]
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

from . import config, lib
from .reference import empty_transform


def excise_for_step(prob: lib.Problem, idx: int) -> dict | None:
    code = prob.step_function_source(idx)
    s = prob.steps[idx]
    break_t = empty_transform(code)
    if break_t is None:
        return None
    # retarget the logical file name used by empty_transform
    for edit in break_t["edits"]:
        edit["file"] = f"steps/{s['step_number']}.py"
    # excision communicates intent through the marker, not a leaked answer
    for edit in break_t["edits"]:
        edit["new"] = (
            'raise NotImplementedError(\n'
            f'    "body of {lib.extract_function_name(s["function_header"])} excised; '
            'reimplement it from step_description_prompt + function_header")'
        )
    fix_t = lib.inverse_transform(break_t)
    if not lib.roundtrip_ok(code, break_t, fix_t):
        return None
    fname = lib.extract_function_name(s["function_header"])
    return lib.make_candidate(
        source="excise", family="excise",
        problem_id=prob.problem_id, step_number=s["step_number"],
        break_t=break_t, fix_t=fix_t,
        note=f"reimplement {fname} body (sub-step {s['step_number']})",
        line=None,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=config.WORK_DIR / "cand-excise.jsonl")
    ap.add_argument("--problems", default=None)
    args = ap.parse_args()
    ids = set(args.problems.split(",")) if args.problems else None
    problems = lib.load_problems(problem_ids=ids)

    cands = []
    for pid in sorted(problems, key=lambda x: int(x)):
        prob = problems[pid]
        n0 = len(cands)
        for i in range(len(prob.steps)):
            if prob.steps[i]["step_number"] in config.ENV_SKIP_STEPS:
                continue
            c = excise_for_step(prob, i)
            if c:
                cands.append(c)
        print(f"{pid} {prob.record['problem_name']}: +{len(cands) - n0} candidates")
    lib.write_candidates(cands, args.out)
    print(f"\ntotal {len(cands)} candidates -> {args.out}")


if __name__ == "__main__":
    main()
