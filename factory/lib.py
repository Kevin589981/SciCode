"""Core library for scicode-factory: schemas, transforms, problem model, runner.

Candidate schema mirrors ScienceInfra demo/envs/<env>/factory/lib.py:

    {
      "id":     "<problem_id>-<family>-<hh>",  # deterministic from break text
      "source": "inject" | "excise",
      "family": "sign" | "coef" | ... | "excise",
      "tree":   "scicode/<problem_id>/<step_number>",
      "note":   human-readable one-liner,
      "break":  TRANSFORM,
      "fix":    TRANSFORM,
      "meta":   {"problem_id", "step_number", "file", "line", "k_sites",
                 "target_checks", ...}
    }

TRANSFORM (identical to ScienceInfra):
    {"edits": [{"file": str, "old": str, "new": str}, ...]}
        exact string replacement; every "old" must occur exactly once in the
        target code blob, otherwise application refuses (keeps transforms
        reversible and positions unique).
    {"diff": "<unified diff>"}
        accepted for schema parity; not emitted by the v1 operators.
"""
from __future__ import annotations

import ast
import concurrent.futures as cf
import hashlib
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config


# ---------------------------------------------------------------------------
# TRANSFORM machinery (roundtrip-verifiable exact-string edits)
# ---------------------------------------------------------------------------

class TransformError(ValueError):
    pass


def apply_transform(code: str, transform: dict) -> str:
    """Apply a TRANSFORM to a code blob. Pure function; refuses on ambiguity."""
    if "diff" in transform:
        raise TransformError("diff transforms not supported in v1 (emitted schema only)")
    out = code
    for edit in transform["edits"]:
        if len(edit) != 3 or "old" not in edit or "new" not in edit:
            raise TransformError(f"malformed edit: {edit!r}")
        old, new = edit["old"], edit["new"]
        if old == new:
            raise TransformError("edit with old == new (silent by construction)")
        n = out.count(old)
        if n != 1:
            raise TransformError(f"'old' occurs {n} times (must be unique): {old[:80]!r}")
        out = out.replace(old, new, 1)
    return out


def inverse_transform(transform: dict) -> dict:
    """Exact inverse of an edits-TRANSFORM (swap old/new)."""
    return {"edits": [{"file": e["file"], "old": e["new"], "new": e["old"]}
                      for e in transform["edits"]]}


def roundtrip_ok(code: str, break_t: dict, fix_t: dict) -> bool:
    """break->fix must restore the pristine blob byte-for-byte (ScienceInfra gate)."""
    try:
        return apply_transform(apply_transform(code, break_t), fix_t) == code
    except TransformError:
        return False


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _short_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:6]


def make_candidate(source: str, family: str, problem_id: str, step_number: str,
                   break_t: dict, fix_t: dict, note: str, meta_extra: dict | None = None,
                   line: int | None = None) -> dict:
    meta = {
        "problem_id": problem_id,
        "step_number": step_number,
        "file": f"steps/{step_number}.py",
        "line": line,
        "k_sites": len(break_t.get("edits", [])),
        "target_checks": [],
    }
    if meta_extra:
        meta.update(meta_extra)
    return {
        "id": f"{problem_id}-{family}-{step_number.replace('.', '_')}-{_short_hash(break_t)}",
        "source": source,
        "family": family,
        "tree": f"scicode/{problem_id}/{step_number}",
        "note": note,
        "break": break_t,
        "fix": fix_t,
        "meta": meta,
    }


def write_candidates(cands: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fp:
        for c in cands:
            fp.write(json.dumps(c) + "\n")


def iter_candidates(*paths: Path):
    for p in paths:
        with Path(p).open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line:
                    yield json.loads(line)


# ---------------------------------------------------------------------------
# Problem model
# ---------------------------------------------------------------------------

# Official harness skips these "given code" steps (their code ships as
# eval/data/<n>.txt). None of 13/62/76 are in validation; kept for parity.
SPECIAL_STEP_FILES = {("13", 6): "13.6", ("62", 1): "62.1", ("76", 3): "76.3"}


def extract_function_name(function_header: str) -> str:
    m = re.search(r"\bdef\s+(\w+)\s*\(", function_header)
    if not m:
        raise ValueError(f"no def found in function_header: {function_header[:120]!r}")
    return m.group(1)


def extract_function_source(code: str, name: str) -> str:
    """AST-exact extraction of a top-level function/class body (docstring-safe)."""
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == name:
            seg = ast.get_source_segment(code, node)
            if seg is not None:
                return seg
    raise ValueError(f"function {name!r} not found at top level")


@dataclass
class Problem:
    record: dict
    data_dir: Path = field(default=config.DATA_DIR)

    @property
    def problem_id(self) -> str:
        return self.record["problem_id"]

    @property
    def steps(self) -> list[dict]:
        return self.record["sub_steps"]

    def step_function_source(self, idx: int) -> str:
        """Gold function source for step idx (handles the 3 given-file specials)."""
        s = self.steps[idx]
        fname = extract_function_name(s["function_header"])
        key = (self.problem_id, idx + 1)
        if key in SPECIAL_STEP_FILES:
            fpath = self.data_dir / "given_files" / f"{key[0]}.{key[1]}.txt"
            txt = Path(fpath).read_text(encoding="utf-8")
            return extract_function_source(txt, fname)
        return extract_function_source(s["ground_truth_code"], fname)

    def step_source(self, idx: int, defect: dict | None = None) -> str:
        """Gold source for step idx, optionally with a defect TRANSFORM applied.

        defect = {"step": j, "transform": TRANSFORM} (j 0-based).
        """
        src = self.step_function_source(idx)
        if defect is not None and defect["step"] == idx:
            src = apply_transform(src, defect["transform"])
        return src

    def cumulative_code(self, upto_idx: int, defect: dict | None = None) -> str:
        deps = self.record["required_dependencies"].strip()
        parts = [deps] if deps else []
        for j in range(upto_idx + 1):
            parts.append(self.step_source(j, defect))
        return "\n\n".join(parts)

    def assemble_script(self, idx: int, defect: dict | None = None,
                        h5_path: str | Path | None = None) -> str:
        """Replicates the official scorer's script assembly
        (eval/scripts/test_generated_code.py / eval/inspect_ai/scicode.py)."""
        s = self.steps[idx]
        step_id = s["step_number"]
        tests = s["test_cases"]
        n = len(tests)
        h5 = str(h5_path or config.TEST_H5)
        lines = []
        if config.SCICODE_SRC:
            lines += ["import sys", f"sys.path.insert(0, {config.SCICODE_SRC!r})"]
        lines += [
            self.cumulative_code(idx, defect),
            "",
            "from scicode.parse.parse import process_hdf5_to_tuple",
            f"targets = process_hdf5_to_tuple({step_id!r}, {n}, {h5!r})",
        ]
        for i in range(n):
            lines.append(f"target = targets[{i}]")
            lines.extend(tests[i].split("\n"))
        return "\n".join(lines)

    def downstream_indices(self, start_idx: int) -> range:
        return range(start_idx, len(self.steps))


def load_problems(path: Path | None = None,
                  problem_ids: set[str] | None = None) -> dict[str, Problem]:
    path = Path(path or config.VALIDATION_JSONL)
    problems = {}
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            rec = json.loads(line)
            pid = rec["problem_id"]
            if problem_ids and pid not in problem_ids:
                continue
            problems[pid] = Problem(rec)
    return problems


def step_index_by_number(prob: Problem, step_number: str) -> int:
    for i, s in enumerate(prob.steps):
        if s["step_number"] == step_number:
            return i
    raise KeyError(step_number)


# ---------------------------------------------------------------------------
# Script execution
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    status: str          # "pass" | "fail" | "timeout" | "error"
    exit_code: int | None
    wall_sec: float
    stderr_tail: str = ""


def run_script(script: str, timeout: int | None = None,
               python: str | None = None, workdir: Path | None = None) -> RunResult:
    timeout = timeout or config.STEP_TIMEOUT_SEC
    python = python or config.PYTHON
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     dir=workdir) as fp:
        fp.write(script)
        path = fp.name
    t0 = time.monotonic()
    try:
        r = subprocess.run([python, path], capture_output=True, text=True,
                           timeout=timeout, cwd=workdir)
        wall = time.monotonic() - t0
        status = "pass" if r.returncode == 0 else "fail"
        return RunResult(status, r.returncode, wall, (r.stderr or "")[-2000:])
    except subprocess.TimeoutExpired:
        return RunResult("timeout", None, timeout)
    except Exception as e:  # pragma: no cover - defensive
        return RunResult("error", None, time.monotonic() - t0, f"{type(e).__name__}: {e}")
    finally:
        Path(path).unlink(missing_ok=True)


class ScriptPool:
    """Shared thread pool over subprocess-bound script executions."""

    def __init__(self, workers: int = config.N_WORKERS):
        self._pool = cf.ThreadPoolExecutor(max_workers=workers)

    def submit(self, script: str, timeout: int | None = None) -> cf.Future:
        return self._pool.submit(run_script, script, timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._pool.shutdown()
        return False


def last_exception_line(stderr: str) -> str:
    """Compact symptom line: final non-empty stderr line (usually 'FooError: ...')."""
    for line in reversed(stderr.strip().splitlines()):
        if line.strip():
            return line.strip()[:300]
    return ""
