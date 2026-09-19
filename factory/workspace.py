"""Materialize an agent-facing workspace for a packaged task.

Sparse tasks stay data-only (like ScienceInfra tasks/); this module is the
single place that turns a task dir + the pristine SciCode problem into files
an agent can run, without ever shipping defect.json/fix.json/h5 into the
agent view.

Workspace layout:
    steps/<step_number>.py    cumulative sub-step sources (defect applied)
    run_step.py               tiny official-style runner used by anchors:
                              python run_step.py <step_index>
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, lib

RUNNER = '''\
"""Per-step runner materialized by scicode-factory (agent-visible)."""
import json
import sys
from pathlib import Path


def main():
    idx = int(sys.argv[1])
    manifest = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
    steps = manifest["steps"]
    deps = manifest["required_dependencies"]
    parts = [deps] if deps else []
    for j in range(idx + 1):
        parts.append(Path("steps", steps[j] + ".py").read_text(encoding="utf-8"))
    s = manifest["sub_steps"][idx]
    n = len(s["test_cases"])
    lines = ["\\n\\n".join(parts), "",
             "from scicode.parse.parse import process_hdf5_to_tuple",
             f"targets = process_hdf5_to_tuple({s['step_number']!r}, {n}, {manifest['h5']!r})"]
    for i in range(n):
        lines.append(f"target = targets[{i}]")
        lines.extend(s["test_cases"][i].split("\\n"))
    code = "\\n".join(lines)
    g = {"__name__": "__main__"}
    exec(compile(code, "<assembled>", "exec"), g)


if __name__ == "__main__":
    main()
'''


def materialize(task_dir: Path, out_dir: Path, problems: dict[str, lib.Problem],
                apply: str = "break") -> dict:
    """Write the agent workspace for a task. apply: 'break' | 'fix' (oracle)."""
    task_dir = Path(task_dir)
    prov = json.loads((task_dir / "authoring" / "provenance.json")
                      .read_text(encoding="utf-8"))
    cand = prov["candidate"]
    meta = cand["meta"]
    prob = problems[meta["problem_id"]]
    idx = lib.step_index_by_number(prob, meta["step_number"])
    out_dir = Path(out_dir)
    steps_dir = out_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    step_names = []
    for j, s in enumerate(prob.steps):
        code = prob.step_function_source(j)
        if apply == "fix" and j == idx:
            pass  # oracle: pristine
        elif apply == "break" and j == idx:
            code = lib.apply_transform(code, cand["break"])
        (steps_dir / f"{s['step_number']}.py").write_text(code, encoding="utf-8")
        step_names.append(s["step_number"])
    (out_dir / "run_step.py").write_text(RUNNER, encoding="utf-8")
    manifest = {
        "task_id": cand["id"],
        "problem_id": meta["problem_id"],
        "required_dependencies": prob.record["required_dependencies"],
        "steps": step_names,
        "sub_steps": [{"step_number": s["step_number"],
                       "test_cases": s["test_cases"]} for s in prob.steps],
        "h5": str(config.TEST_H5),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest
