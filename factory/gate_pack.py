"""Static + behavioral gates over packaged tasks (ScienceInfra gate_pack.py).

Per task:
  static     all REQUIRED_FILES exist; task.toml parses; defect/fix are
             well-formed transforms; break applies to the pristine step code.
  roundtrip  break->fix restores pristine code byte-for-byte.
  leakscan   fix-side text (pristine fragments / reimplementation bodies) must
             NOT appear in any agent-visible material (instruction.md and the
             materialized break-workspace). defect.json / fix.json /
             authoring/ are private by contract and never materialized.

Usage:
    python -m factory.gate_pack --tasks tasks [--selftest]
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
import tomllib
from pathlib import Path

from . import config, lib, workspace
from .package import REQUIRED_FILES


def agent_visible_texts(task_dir: Path, problems: dict[str, lib.Problem]) -> dict[str, str]:
    """Everything an agent can read for this task."""
    texts = {"instruction.md": (task_dir / "instruction.md").read_text(encoding="utf-8")}
    with tempfile.TemporaryDirectory() as td:
        manifest = workspace.materialize(task_dir, td, problems, apply="break")
        for name in manifest["steps"]:
            texts[f"steps/{name}.py"] = (Path(td) / "steps" / f"{name}.py").read_text(
                encoding="utf-8")
    return texts


def gate_task(task_dir: Path, problems: dict[str, lib.Problem]) -> list[str]:
    """Return list of gate failures (empty == pass)."""
    errs = []
    # static
    for f in REQUIRED_FILES:
        if not (task_dir / f).exists():
            errs.append(f"missing {f}")
    if errs:
        return errs
    try:
        tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    except Exception as e:
        errs.append(f"task.toml unparsable: {e}")
    defect = json.loads((task_dir / "defect.json").read_text(encoding="utf-8"))
    fix = json.loads((task_dir / "fix.json").read_text(encoding="utf-8"))
    prov = json.loads((task_dir / "authoring" / "provenance.json")
                      .read_text(encoding="utf-8"))
    cand = prov["candidate"]
    meta = cand["meta"]
    prob = problems[meta["problem_id"]]
    pristine = prob.step_function_source(
        lib.step_index_by_number(prob, meta["step_number"]))

    # roundtrip
    if not lib.roundtrip_ok(pristine, defect, fix):
        errs.append("roundtrip: break->fix does not restore pristine code")
    try:
        broken = lib.apply_transform(pristine, defect)
        ast.parse(broken)
    except Exception as e:
        errs.append(f"broken code invalid: {e}")
        broken = None

    # leakscan (content-anchored, mirrors ScienceInfra gate_pack).
    # fix-side "new" = the answer (pristine text). Raw values like "2" would
    # false-positive everywhere, so probes are expanded to a >= PROBE_MIN_LEN
    # window of the surrounding pristine lines. The window always spans the
    # mutated line, which break() necessarily destroys, so pristine-context
    # text absent from the broken workspace is a real signal, not a rumor.
    visible = agent_visible_texts(task_dir, problems)
    for edit in fix.get("edits", []):
        probe = _context_probe(pristine, edit["new"])
        if probe:
            for vpath, text in visible.items():
                if probe in text:
                    errs.append(
                        f"leak: fix-side text found in agent-visible {vpath}")
    # defect-side "old" = where the bug lives; it must never appear in the
    # instruction (the steps/ workspace legitimately contains it).
    if broken is not None:
        instr = visible.get("instruction.md", "")
        for edit in defect.get("edits", []):
            probe = _context_probe(broken, edit["old"])
            if probe and probe in instr:
                errs.append("leak: defect location text found in instruction.md")
    return errs


PROBE_MIN_LEN = 20


def _context_probe(source: str, fragment: str, min_len: int = PROBE_MIN_LEN) -> str | None:
    """Whole-line window around a fragment, extended upward until >= min_len."""
    pos = source.find(fragment)
    if pos < 0:
        return None
    start = source.rfind("\n", 0, pos) + 1
    end = source.find("\n", pos + len(fragment))
    end = len(source) if end < 0 else end
    while start > 0 and (end - start) < min_len:
        prev = source.rfind("\n", 0, start - 1) + 1
        if prev >= start:
            break
        start = prev
    probe = source[start:end].strip()
    return probe or None


def _selftest(args_tasks: Path) -> None:
    """Plant a leak and verify the gate trips (mirrors gate_pack --selftest)."""
    problems = lib.load_problems()
    tasks = sorted(p for p in args_tasks.rglob("task.toml"))
    if not tasks:
        raise SystemExit("no packaged tasks to selftest against")
    real = tasks[0].parent
    with tempfile.TemporaryDirectory() as td:
        tcopy = Path(td) / "task"
        import shutil
        shutil.copytree(real, tcopy)
        fix = json.loads((tcopy / "fix.json").read_text(encoding="utf-8"))
        prov = json.loads((tcopy / "authoring" / "provenance.json")
                          .read_text(encoding="utf-8"))
        meta = prov["candidate"]["meta"]
        prob = problems[meta["problem_id"]]
        pristine = prob.step_function_source(
            lib.step_index_by_number(prob, meta["step_number"]))
        probe = _context_probe(pristine, fix["edits"][0]["new"])
        if probe is None:
            raise SystemExit("selftest cannot build context probe")

        instr = (tcopy / "instruction.md").read_text(encoding="utf-8")
        (tcopy / "instruction.md").write_text(instr + "\n<!-- PLANTED "
                                              + probe + " -->\n", encoding="utf-8")
        errs = gate_task(tcopy, problems)
        if not any(e.startswith("leak:") for e in errs):
            raise SystemExit("SELFTEST FAILED: planted leak was not detected")
        print("selftest OK: planted leak detected ->", errs[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, default=Path("tasks"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest(args.tasks)
        return

    problems = lib.load_problems()
    task_tomls = sorted(args.tasks.rglob("task.toml"))
    if not task_tomls:
        raise SystemExit(f"no tasks under {args.tasks}")
    n_bad = 0
    for tt in task_tomls:
        errs = gate_task(tt.parent, problems)
        flag = "PASS" if not errs else "FAIL"
        if errs:
            n_bad += 1
        print(f"[{flag}] {tt.parent.relative_to(args.tasks)}"
              + ("" if not errs else " :: " + "; ".join(errs)))
    print(f"\ngate_pack: {len(task_tomls) - n_bad}/{len(task_tomls)} tasks pass")
    if n_bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
