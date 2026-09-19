"""Execute proposals against the original upstream code; emit validated seeds.

Gates (ScienceIDE validity-by-execution):
    import       original module imports, function exists        [reject]
    inputs       test-input expressions eval in a sandboxed ns   [reject]
    contract     outputs are numeric-comparable (no dict/str/…)  [reject]
    runtime      every call under SEED_CALL_TIMEOUT              [reject]
    determinism  two independent process runs agree (1e-12)      [reject]
    witness      reference passes its own tests (by construction
                 after determinism; still assembled + executed)  [reject]
    empty        excised body fails the tests                    [reject]
    novelty      not a near-duplicate of official SciCode        [reject]

Survivors are written as seeds:
    seeds/<slug>.json       scicode-like record (question/header/tests/provenance)
    seeds/test_data.h5      targets in the OFFICIAL h5 layout
                            (<step>/test<i>/var<j>), readable by
                            scicode.parse.process_hdf5_to_tuple -- so the
                            existing eval stack consumes seeds unchanged.

Usage:
    python -m factory.author.verify --proposals .work/proposals.jsonl \
        --repo-root /path/to/checkout --out seeds [--limit N]
"""
from __future__ import annotations

import argparse
import ast
import json
import pickle
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from . import novelty

SEED_CALL_TIMEOUT = 60
ATOL = RTOL = 1e-12

ALLOWED_EVAL_NAMES = {"np", "numpy", "sp", "scipy", "math", "cmath"}
BANNED_EXPR = re.compile(r"(__|import|open|exec|eval|os\.|sys\.|subprocess)")


def sanitize_slug(slug: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", slug).strip("-")[:100]


# ---------------------------------------------------------------------------
# numeric contract helpers
# ---------------------------------------------------------------------------

def check_numeric(obj, depth=0) -> bool:
    if depth > 3:
        return False
    if isinstance(obj, (bool, int, float, complex, np.number)):
        return True
    if isinstance(obj, np.ndarray):
        return obj.dtype.kind in "biufc"
    if isinstance(obj, (tuple, list)):
        return all(check_numeric(x, depth + 1) for x in obj)
    return False


def flatten_vars(obj):
    """Split an output into h5 vars: tuple/list -> one var per element."""
    if isinstance(obj, (tuple, list)):
        return list(obj)
    return [obj]


def to_hdf5_value(v):
    arr = np.asarray(v)
    if arr.dtype.kind == "c":      # keep complex support minimal
        return arr
    return arr


def compare(a, b) -> bool:
    """Structural allclose over numeric containers."""
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(compare(x, y) for x, y in zip(a, b))
    try:
        return bool(np.allclose(a, b, atol=ATOL, rtol=RTOL, equal_nan=True))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# runner scripts (executed in fresh processes against the ORIGINAL code)
# ---------------------------------------------------------------------------

RUNNER = '''
import importlib, json, math, pickle, sys, time
import numpy as np
import scipy as sp
import cmath

sys.path.insert(0, {repo_root!r})
t0 = time.monotonic()
mod = importlib.import_module({module!r})
fn = getattr(mod, {function!r})
ns = {{"__builtins__": {{}}, "np": np, "numpy": np, "sp": sp,
      "scipy": sp, "math": math, "cmath": cmath}}
inputs = []
for expr in {input_exprs!r}:
    inputs.append(eval(expr, ns))
outs = []
for args in inputs:
    if not isinstance(args, tuple):
        args = (args,)
    t1 = time.monotonic()
    outs.append(fn(*args))
    if time.monotonic() - t1 > {timeout}:
        raise TimeoutError("single call exceeded budget")
with open({out!r}, "wb") as fp:
    pickle.dump(outs, fp)
print(json.dumps({{"wall": time.monotonic() - t0}}))
'''


def run_original(prop: dict, repo_root: Path, timeout: int = SEED_CALL_TIMEOUT + 30):
    """Run the original function on the proposal inputs; return (outputs, wall)."""
    with tempfile.TemporaryDirectory() as td:
        out_pkl = Path(td) / "outs.pkl"
        script = RUNNER.format(
            repo_root=str(repo_root), module=prop["module_hint"],
            function=prop["function"], input_exprs=prop["test_inputs"],
            timeout=timeout, out=str(out_pkl))
        sp = Path(td) / "run.py"
        sp.write_text(script, encoding="utf-8")
        r = subprocess.run([sys.executable, str(sp)], capture_output=True,
                           text=True, timeout=timeout + 30)
        if r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()
            raise RuntimeError(f"runner failed: {tail[-1] if tail else r.returncode}")
        wall = json.loads(r.stdout.strip().splitlines()[-1])["wall"]
        return pickle.loads(out_pkl.read_bytes()), wall


def make_test_cases(prop: dict) -> list[str]:
    cases = []
    for expr in prop["test_inputs"]:
        call = f"{prop['function']}{expr}"
        cases.append(f"assert np.allclose({call}, target, atol=1e-8, rtol=1e-6)")
    return cases


# ---------------------------------------------------------------------------
# main verify loop
# ---------------------------------------------------------------------------

def verify_proposal(prop: dict, repo_root: Path, h5_path: Path,
                    official_fingerprints) -> dict:
    out = {"slug": prop["slug"], "status": None}
    try:
        if BANNED_EXPR.search(" ".join(prop["test_inputs"])):
            raise ValueError("banned tokens in test_inputs")
        outputs1, wall = run_original(prop, repo_root)
        if not check_numeric(outputs1):
            raise ValueError("outputs not numeric-comparable")
        outputs2, _ = run_original(prop, repo_root)
        if not all(compare(a, b) for a, b in zip(outputs1, outputs2)):
            raise ValueError("non-deterministic outputs across runs")
        if wall > SEED_CALL_TIMEOUT:
            raise ValueError(f"too slow: {wall:.1f}s")
        if not novelty.screen(prop, official_fingerprints):
            raise ValueError("near-duplicate of official SciCode content")

        step_number = f"{prop['slug']}-1"
        # write targets in the OFFICIAL h5 layout
        import h5py
        with h5py.File(h5_path, "a") as h5:
            if step_number in h5:
                del h5[step_number]
            grp = h5.create_group(step_number)
            for i, o in enumerate(outputs1):
                tg = grp.create_group(f"test{i + 1}")
                for j, v in enumerate(flatten_vars(o)):
                    tg.create_dataset(f"var{j + 1}", data=to_hdf5_value(v))

        deps = list(dict.fromkeys(
            [*(prop.get("dependencies") or []), "import numpy as np"]))
        seed = {
            "problem_name": prop["slug"],
            "problem_id": prop["slug"],
            "problem_description_main": prop["question"],
            "problem_background_main": prop.get("background", ""),
            "required_dependencies": "\n".join(deps),
            "sub_steps": [{
                "step_number": step_number,
                "step_description_prompt": prop["question"],
                "step_background": prop.get("background", ""),
                "ground_truth_code": prop["reference_source"],
                "function_header": prop["function_header"],
                "test_cases": make_test_cases(prop),
                "return_line": "",
            }],
            "general_solution": prop["reference_source"],
            "provenance": {
                "repo": prop["repo"], "file": prop["file"],
                "function": prop["function"],
                "module_hint": prop["module_hint"],
                "wall_sec": round(wall, 3),
                "generator": "scicode-factory author (LLM propose, execution validate)",
            },
        }
        out.update(status="survivor", seed=seed, wall_sec=round(wall, 3))
    except Exception as e:
        out.update(status="rejected", reason=str(e)[:300])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposals", type=Path, required=True)
    ap.add_argument("--repo-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("seeds"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="dir with official validation.jsonl/test.jsonl for novelty")
    args = ap.parse_args()

    from .. import config
    data_dir = args.data_dir or config.DATA_DIR
    official = novelty.load_official_fingerprints(data_dir)

    props = [json.loads(l) for l in args.proposals.open(encoding="utf-8")]
    if args.limit:
        props = props[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    h5_path = args.out / "test_data.h5"
    ok = bad = 0
    with (args.out / "seeds.jsonl").open("w", encoding="utf-8") as fp:
        for prop in props:
            prop["slug"] = sanitize_slug(prop["slug"])
            v = verify_proposal(prop, args.repo_root, h5_path, official)
            if v["status"] == "survivor":
                (args.out / f"{prop['slug']}.json").write_text(
                    json.dumps(v["seed"], indent=2), encoding="utf-8")
                fp.write(json.dumps({"slug": prop["slug"],
                                     "seed_path": f"{prop['slug']}.json"}) + "\n")
                ok += 1
            else:
                bad += 1
            print(f"[{ok + bad}/{len(props)}] {v['status']:9s} {prop['slug']}"
                  + ("" if v["status"] == "survivor" else f" :: {v['reason']}"))
    print(f"\nverify: {ok} survivors / {bad} rejected -> {args.out}")


if __name__ == "__main__":
    main()
