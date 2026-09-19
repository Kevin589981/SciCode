"""Execution-based validity funnel (replicates ScienceInfra factory/funnel.py).

State machine per candidate:
    roundtrip   break->fix must restore pristine code byte-for-byte   [invalid]
    build       broken cumulative code must still parse               [invalid]
    silent      all downstream tests still pass with the defect       [reject]
    floor_high  defective baseline score > FLOOR_MAX (no headroom)    [reject]
    witness     fix restores pristine (roundtrip) AND pristine passes
                reference (witness anchor from reference.py)          [survivor]

SciCode-specific scoring: defect at step k cascades to steps k..last, so the
funnel runs every downstream step's official-style test script and computes

    floor = passed_downstream_steps / total_downstream_steps     (defective)

which is exactly the defective baseline that reward_repair normalizes away
(reward_repair = max(0, (r - floor) / (1 - floor)), ScienceIDE paper Eq. 1).

Usage:
    python -m factory.funnel --candidates .work/cand-inject.jsonl .work/cand-excise.jsonl \
        --out .work/funnel [--problems 1,10] [--limit 40]
Outputs:
    .work/funnel/verdicts.jsonl   one record per candidate
    .work/funnel/funnel_summary.md
Requires .work/ref/results.json from `python -m factory.reference`.
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import config, lib


def _load_reference_failures(ref_path: Path) -> dict[str, set[str]]:
    """step numbers that failed (or were env-skipped) under gold reference."""
    failed: dict[str, set[str]] = defaultdict(set)
    if not ref_path.exists():
        raise SystemExit(f"reference results missing: {ref_path}\n"
                         "run `python -m factory.reference` first")
    with ref_path.open(encoding="utf-8") as fp:
        for line in fp:
            rec = json.loads(line)
            if rec.get("status") != "pass":
                failed[rec["problem_id"]].add(rec["step_number"])
    return failed


def judge_candidate(cand: dict, problems: dict[str, lib.Problem],
                    ref_fail: dict[str, set[str]]) -> dict:
    meta = cand["meta"]
    pid, step_id = meta["problem_id"], meta["step_number"]
    prob = problems[pid]
    idx = lib.step_index_by_number(prob, step_id)
    out = {"id": cand["id"], "source": cand["source"], "family": cand["family"],
           "problem_id": pid, "step_number": step_id, "meta": meta}

    # excluded-baseline steps downstream poison the witness chain
    dirty = ref_fail.get(pid, set()) & {prob.steps[j]["step_number"]
                                        for j in prob.downstream_indices(idx)}
    if dirty:
        out.update(status="skipped", reason="reference-baseline-failure",
                   dirty_steps=sorted(dirty), floor=None)
        return out

    pristine = prob.step_function_source(idx)

    # gate 1: roundtrip
    if not lib.roundtrip_ok(pristine, cand["break"], cand["fix"]):
        out.update(status="invalid", reason="roundtrip", floor=None)
        return out

    # gate 2: build (broken code must still be syntactically valid)
    try:
        broken_step = lib.apply_transform(pristine, cand["break"])
        ast.parse(broken_step)
    except (lib.TransformError, SyntaxError):
        out.update(status="invalid", reason="syntax", floor=None)
        return out

    # run downstream chain with defect applied
    defect = {"step": idx, "transform": cand["break"]}
    per_step = []
    n_pass = 0
    for j in prob.downstream_indices(idx):
        sid = prob.steps[j]["step_number"]
        r = lib.run_script(prob.assemble_script(j, defect=defect))
        per_step.append({"step": sid, "status": r.status,
                         "exit": r.exit_code, "wall_sec": round(r.wall_sec, 2),
                         "symptom": lib.last_exception_line(r.stderr_tail)
                         if r.status != "pass" else ""})
        if r.status == "pass":
            n_pass += 1

    total = len(per_step)
    floor = n_pass / total if total else 0.0
    out["per_step"] = per_step
    out["floor"] = round(floor, 6)
    out["n_downstream"] = total
    out["n_pass"] = n_pass

    first_fail = next((p for p in per_step if p["status"] != "pass"), None)
    out["symptom"] = (f"{first_fail['step']}: {first_fail['status']}"
                      f" {first_fail['symptom']}" if first_fail else "")

    # gate 3-4: silent / floor_high
    if n_pass == total:
        out.update(status="silent")
        return out
    if floor > config.FLOOR_MAX:
        out.update(status="floor_high", floor_max=config.FLOOR_MAX)
        return out

    # gate 5: witness = roundtrip (fix restores pristine) + pristine reference pass
    # (already established by reference.py witness anchor for every non-skipped step)
    out.update(status="survivor")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=config.WORK_DIR / "funnel")
    ap.add_argument("--problems", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    ids = set(args.problems.split(",")) if args.problems else None
    problems = lib.load_problems(problem_ids=ids)
    ref_path = config.WORK_DIR / "ref" / "results.json"
    ref_fail = _load_reference_failures(ref_path)

    cands = [c for c in lib.iter_candidates(*args.candidates)
             if c["meta"]["problem_id"] in problems]
    if args.limit:
        cands = cands[: args.limit]
    print(f"funnel: {len(cands)} candidates over {len(problems)} problems, "
          f"workers={config.N_WORKERS}")

    args.out.mkdir(parents=True, exist_ok=True)
    verdicts_path = args.out / "verdicts.jsonl"
    counter: Counter = Counter()
    by_family: dict[str, Counter] = defaultdict(Counter)

    with verdicts_path.open("w", encoding="utf-8") as out:
        for k, cand in enumerate(cands, 1):
            v = judge_candidate(cand, problems, ref_fail)
            counter[v["status"]] += 1
            by_family[cand["family"]][v["status"]] += 1
            out.write(json.dumps(v) + "\n")
            if k % 25 == 0 or k == len(cands):
                print(f"  [{k}/{len(cands)}] {dict(counter)}")

    # summary
    lines = ["# Funnel summary", ""]
    lines.append(f"total candidates: {len(cands)}")
    lines.append("")
    lines.append("| family | candidates | survivor | silent | floor_high | "
                 "invalid | skipped |")
    lines.append("|---|---|---|---|---|---|---|")
    for fam, c in sorted(by_family.items()):
        n = sum(c.values())
        lines.append(f"| {fam} | {n} | {c['survivor']} | {c['silent']} | "
                     f"{c['floor_high']} | {c['invalid']} | {c['skipped']} |")
    n = sum(counter.values())
    lines.append(f"| **all** | {n} | {counter['survivor']} | {counter['silent']} | "
                 f"{counter['floor_high']} | {counter['invalid']} | "
                 f"{counter['skipped']} |")
    lines.append("")
    lines.append(f"thresholds: FLOOR_MAX={config.FLOOR_MAX}, "
                 f"STEP_TIMEOUT_SEC={config.STEP_TIMEOUT_SEC}")
    (args.out / "funnel_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
