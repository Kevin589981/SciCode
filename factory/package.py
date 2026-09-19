"""Package funnel survivors into sparse task directories.

Mirrors ScienceInfra factory/package.py: survivors become sparse tasks
(task.toml / instruction.md / defect.json / fix.json / authoring/provenance.json),
instruction symptom text is rendered from the funnel's measured verdicts, and
at most 2 survivors are kept per (step, family) group to limit near-duplicates.

    tasks/
      repair/<tier>/<task-id>/
      implementation/<tier>/<task-id>/
      index.jsonl

Tiers default to "unrated": ScienceInfra measures difficulty with named-model
pass rates; that probing is an explicit later step (see factory/README.md), so
difficulty facts live in task.toml [metadata.difficulty_facts] instead.

Usage:
    python -m factory.package --verdicts .work/funnel/verdicts.jsonl \
        --candidates .work/cand-inject.jsonl .work/cand-excise.jsonl \
        --out tasks [--max-per-group 2]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config, lib

REQUIRED_FILES = ("task.toml", "instruction.md", "defect.json", "fix.json",
                  "authoring/provenance.json")


def _render_instruction(cand: dict, verdict: dict, prob: lib.Problem) -> str:
    meta = cand["meta"]
    pid, step_id = meta["problem_id"], meta["step_number"]
    rec = prob.record
    idx = lib.step_index_by_number(prob, step_id)
    n_down = verdict["n_downstream"]
    floor = verdict["floor"]
    reward = ("reward = mean share of downstream steps ("
              f"{step_id}..{prob.steps[-1]['step_number']}, {n_down} steps) whose "
              "official tests pass; "
              f"reward_repair = max(0, (reward - {floor:.3f}) / (1 - {floor:.3f})), "
              "so resubmitting the workspace unchanged scores exactly 0.")

    head = [f"<!-- canary: {cand['id']} -->", ""]
    head.append(f"# SciCode task `{cand['id']}`")
    head.append("")
    head.append(f"Derived from SciCode problem {pid} "
                f"({rec['problem_name']}), sub-step {step_id}.")
    head.append("")
    head.append("## Problem context")
    head.append("")
    head.append(rec["problem_description_main"].strip())
    head.append("")
    head.append("### Sub-step under repair" if cand["source"] == "inject"
                else "### Sub-step to implement")
    s = prob.steps[idx]
    head.append("")
    head.append(s["step_description_prompt"].strip())
    head.append("")
    head.append("```python")
    head.append(s["function_header"].strip())
    head.append("```")
    head.append("")

    if cand["source"] == "inject":
        head.append("## Observed symptom")
        head.append("")
        if verdict.get("symptom"):
            head.append(f"- first failing downstream check: `{verdict['symptom']}`")
        head.append(f"- defective baseline: {verdict['n_pass']}/{n_down} "
                    f"downstream steps pass (floor = {floor:.3f})")
        head.append("")
        head.append(
            "The workspace `steps/` contains the cumulative sub-step sources with "
            "exactly one localized semantic defect. Find it and repair it so all "
            "downstream checks pass again. Do not rewrite unrelated code.")
    else:
        head.append("## Goal")
        head.append("")
        head.append(
            f"The body of `{lib.extract_function_name(s['function_header'])}` "
            f"(`steps/{step_id}.py`) has been excised and replaced with "
            "`raise NotImplementedError`. Reimplement it from the description "
            "and docstring above so all downstream checks pass.")
    head.append("")
    head.append("## Acceptance")
    head.append("")
    head.append(f"- all official tests of steps {step_id}.."
                f"{prob.steps[-1]['step_number']} pass "
                f"({n_down} step scripts, official numerical targets from "
                "test_data.h5)")
    head.append("")
    head.append("## Scoring")
    head.append("")
    head.append(f"- {reward}")
    return "\n".join(head) + "\n"


def _task_toml(cand: dict, verdict: dict) -> str:
    meta = cand["meta"]
    category = "repair" if cand["source"] == "inject" else "implementation"
    lines = [
        'schema_version = "1.0-scicode"',
        "",
        "[task]",
        f'name = "scicode-factory/{cand["id"]}"',
        f'description = """{cand["note"]}"""',
        "",
        "[metadata.provenance]",
        'derived_from = "SciCode validation split (HF SciCode1/SciCode)"',
        f'problem_id = "{meta["problem_id"]}"',
        f'step_number = "{meta["step_number"]}"',
        "",
        "[metadata.taxonomy]",
        f'category = "{category}"',
        f'mode = "{"injected" if cand["source"] == "inject" else "excision"}"',
        f'family = "{cand["family"]}"',
        f'defect_file = "{meta["file"]}"',
        "",
        "[metadata.difficulty_facts]",
        "# difficulty tier is intentionally NOT hand-labeled; measure pass rates",
        "# with a named-solver panel (reference-solver probing) before rating",
        f'floor = {verdict["floor"]}',
        f'k_sites = {meta["k_sites"]}',
        f'n_downstream_steps = {verdict["n_downstream"]}',
        "",
        "[environment]",
        'runtime = "python3 (numpy scipy sympy matplotlib h5py)"',
        f'timeout_sec = {config.STEP_TIMEOUT_SEC}',
        "",
        "[verifier]",
        "style = \"rule\"  # official SciCode test scripts vs test_data.h5",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", type=Path, required=True)
    ap.add_argument("--candidates", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("tasks"))
    ap.add_argument("--max-per-group", type=int, default=2)
    args = ap.parse_args()

    cand_by_id = {c["id"]: c for c in lib.iter_candidates(*args.candidates)}
    survivors = []
    with args.verdicts.open(encoding="utf-8") as fp:
        for line in fp:
            v = json.loads(line)
            if v.get("status") == "survivor":
                survivors.append(v)
    # dedup: at most max-per-group survivors per (step, family), stable by id
    groups: dict[tuple, list] = {}
    for v in survivors:
        groups.setdefault((v["step_number"], v["family"]), []).append(v)
    kept = []
    for key in sorted(groups):
        kept.extend(sorted(groups[key], key=lambda x: x["id"])[: args.max_per_group])

    problems = lib.load_problems()
    args.out.mkdir(parents=True, exist_ok=True)
    index = []
    for v in kept:
        cand = cand_by_id.get(v["id"])
        if cand is None:
            print(f"WARN: verdict {v['id']} has no candidate record, skipped")
            continue
        prob = problems[v["problem_id"]]
        category = "repair" if cand["source"] == "inject" else "implementation"
        tdir = args.out / category / "unrated" / cand["id"]
        (tdir / "authoring").mkdir(parents=True, exist_ok=True)
        (tdir / "task.toml").write_text(_task_toml(cand, v), encoding="utf-8")
        (tdir / "instruction.md").write_text(
            _render_instruction(cand, v, prob), encoding="utf-8")
        (tdir / "defect.json").write_text(json.dumps(cand["break"], indent=2),
                                          encoding="utf-8")
        (tdir / "fix.json").write_text(json.dumps(cand["fix"], indent=2),
                                       encoding="utf-8")
        provenance = {
            "candidate": cand,
            "funnel": {k: v[k] for k in ("status", "floor", "n_downstream",
                                         "n_pass", "per_step", "symptom")
                       if k in v},
            "thresholds": {"FLOOR_MAX": config.FLOOR_MAX,
                           "STEP_TIMEOUT_SEC": config.STEP_TIMEOUT_SEC},
            "generator": "scicode-factory (ScienceIDE-style pipeline)",
        }
        (tdir / "authoring" / "provenance.json").write_text(
            json.dumps(provenance, indent=2), encoding="utf-8")
        index.append({
            "id": cand["id"], "category": category, "tier": "unrated",
            "family": cand["family"], "problem_id": v["problem_id"],
            "step_number": v["step_number"], "floor": v["floor"],
            "path": str(tdir.relative_to(args.out)),
        })

    with (args.out / "index.jsonl").open("w", encoding="utf-8") as fp:
        for row in index:
            fp.write(json.dumps(row) + "\n")
    print(f"packaged {len(index)} tasks "
          f"({len(survivors)} survivors, dedup cap {args.max_per_group}/group) "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
