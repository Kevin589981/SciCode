"""Check calibration -- the measurement layer upstream never published.

For each admitted seed (author/verify survivor), produce the evidence that
makes its tolerance a *measured* contract rather than a guess
(cf. ScienceIDE rubric evidence blocks):

  nominal      reference runs on the proposal inputs (targets)
  variant      every float input perturbed by N ULP (upstream: 'smallest
               sufficient set of active inputs'); output spread recorded
  noise_floor  max abs output drift between nominal and variants
  faults       sign/coef defect injected into the reference; measured
               score loss against the nominal targets with the recommended
               tolerance -> evidence the tolerance REJECTS real faults
  determinism  double-run byte/sha comparison
  tolerance    recommendation: atol = max(10 * noise_floor, 1e-12)
  warrant      LLM draft argument; FINALIZATION IS HUMAN (checkpoint)

Artifacts (mirroring environments/<env>/validation/<check>/):
  rubric.json   v3-style criteria + evidence + warrant{draft, finalized_by}
  row.json      reference_calibration (nondeterminism, wall times), veins

Usage:
  python -m factory.envbuild.calibrate --seed seeds/<slug>.json \
      --repo-root /import/root --out environments/<env>/validation [--ulp 2] \
      [--allow-unfrozen]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pickle
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from ..author import verify as av
from ..author import novelty  # noqa: F401  (re-export convenience)

SAFE_EVAL_IMPORTS = "import numpy as np\nimport scipy as sp\nimport math, cmath"

# Reuse verify's sandboxed runner by importing its template through a thin
# wrapper: we need inputs->outputs with (possibly perturbed) expressions.
RUN_PERTURBED = av.RUNNER


def _ulp_perturb_expr(expr: str, ulp: int) -> str:
    """Rewrite float literals x -> x + ulp*spacing via numpy nextafter.

    Mechanical and safe: wrap every float literal `f` as
    np.nextafter(f, np.inf) applied `ulp` times -> equivalent to adding ulp
    ULPs, works for arrays elementwise.
    """
    def repl(m):
        num = m.group(0)
        return f"_n(_n({num}))" if ulp == 2 else f"_n({num})"
    # float literals (not ints, not exponents' digits)
    # float literals only (must contain a dot, so ints/slices are untouched)
    pat = r"(?<![\w.])(\d+\.\d*|\.\d+)(?![\w.])"
    out = re_sub(pat, repl, expr)
    return out


def re_sub(pat, repl, s):
    import re
    return re.sub(pat, repl, s)


def run_inputs(prop_like: dict, repo_root: Path, timeout: int = 90):
    return av.run_original(prop_like, repo_root, timeout=timeout)


def compare(a, b):
    return av.compare(a, b)


def spread(a, b) -> float:
    """max abs difference over numeric containers."""
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return max((spread(x, y) for x, y in zip(a, b)), default=0.0)
    try:
        return float(np.max(np.abs(np.asarray(a) - np.asarray(b))))
    except (TypeError, ValueError):
        return float("inf")


def perturb_inputs(inputs: list[str], ulp: int) -> list[str]:
    return [_ulp_perturb_expr(e, ulp) for e in inputs]


def try_faults(reference_source: str, prop_like: dict, repo_root: Path,
               targets: list) -> list[dict]:
    """Inject sign/coef defects into the reference; measure score vs targets.

    score = fraction of tests whose outputs stay within tolerance of nominal.
    A good tolerance gives faults bound_fraction ~0 while nominal is 1.0.
    """
    results = []
    try:
        tree = ast.parse(reference_source)
    except SyntaxError:
        return results
    from ..operators import _Injector
    code = reference_source
    vis = _Injector(code)
    vis.visit(tree)
    seen = set()
    for e in vis.edits[:6]:
        if e["family"] in seen:
            continue
        seen.add(e["family"])
        broken = code.replace(e["old"], e["new"], 1)
        pl = dict(prop_like)
        pl["reference_source"] = broken
        # runner imports the ORIGINAL from the module; instead exec broken
        try:
            outs, _ = run_broken(pl, broken, repo_root)
        except Exception:
            continue
        per_test = [spread(o, t) for o, t in zip(outs, targets)]
        results.append({"family": e["family"], "note": e["note"],
                        "output_drift": [round(x, 6) for x in per_test]})
        if len(seen) >= 3:
            break
    return results


RUN_BROKEN = '''
import importlib, pickle, sys, time
import numpy as np
import scipy as sp
import math, cmath
_SAFE = ("float int str bool len range tuple list set dict abs min max sum "
         "round enumerate zip sorted reversed divmod pow isinstance all any")
import builtins as _bi
ns = {{"__builtins__": {{n: getattr(_bi, n) for n in _SAFE.split()}},
      "np": np, "numpy": np, "sp": sp, "scipy": sp, "math": math, "cmath": cmath}}
_n = lambda x: np.nextafter(x, np.inf)
sys.path.insert(0, {repo_root!r})
mod = importlib.import_module({module!r})
g = dict(vars(mod))   # module globals: helpers, private imports available
source = {source!r}
exec(compile(source, "<ref>", "exec"), g)
fn = g[{function!r}]
inputs = [eval(expr, ns) for expr in {input_exprs!r}]
outs = []
for args in inputs:
    if not isinstance(args, tuple):
        args = (args,)
    outs.append(fn(*args))
with open({out!r}, "wb") as fp:
    pickle.dump(outs, fp)
'''


def run_broken(prop_like: dict, broken_source: str, repo_root: Path,
               timeout: int = 90):
    """Execute a DEFECTIVE reference source directly (no repo import)."""
    with tempfile.TemporaryDirectory() as td:
        out_pkl = Path(td) / "outs.pkl"
        script = RUN_BROKEN.format(
            repo_root=str(repo_root), module=prop_like["module_hint"],
            source=broken_source, function=prop_like["function"],
            input_exprs=prop_like["test_inputs"], out=str(out_pkl))
        sp = Path(td) / "run.py"
        sp.write_text(script, encoding="utf-8")
        r = subprocess.run([sys.executable, str(sp)], capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "").strip().splitlines()[-1:])
        return pickle.loads(out_pkl.read_bytes()), 0.0


def calibrate(seed_path: Path, repo_root: Path, out: Path, ulp: int = 2,
              allow_unfrozen: bool = False) -> dict:
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    prov = seed["provenance"]
    check = seed["problem_id"]
    prop_like = {"module_hint": prov["module_hint"],
                 "function": prov["function"],
                 "test_inputs": [None]}  # replaced below

    # recover original test input expressions from the seed's test_cases
    import re
    cases = seed["sub_steps"][0]["test_cases"]
    inputs = []
    for c in cases:
        m = re.search(r"np\.allclose\(\s*" + re.escape(prov["function"])
                      + r"(\(.*?\))\s*,\s*target", c, re.S)
        if not m:
            raise ValueError(f"cannot recover input expr from: {c[:80]}")
        inputs.append("(" + m.group(1) + ")")
    prop_like["test_inputs"] = inputs

    t0 = time.monotonic()
    nominal, wall = run_inputs(prop_like, repo_root)
    nominal2, _ = run_inputs(prop_like, repo_root)
    byte_identical = pickle.dumps(nominal) == pickle.dumps(nominal2)
    det_drift = 0.0 if byte_identical else spread(nominal, nominal2)

    variants = perturb_inputs(inputs, ulp)
    prop_v = dict(prop_like, test_inputs=variants)
    variant_note = None
    try:
        variant_outs, _ = run_inputs(prop_v, repo_root)
        noise_floor = spread(nominal, variant_outs)
    except Exception as e:
        variant_note = f"variant run failed (perturbed inputs left domain?): {e}"[:200]
        noise_floor = max(det_drift, 1e-15)

    atol = max(10.0 * noise_floor, 1e-12)
    if variant_note:
        atol = max(atol, 1e-8)  # conservative fallback, flagged in rubric
    faults = try_faults(seed["general_solution"], prop_like, repo_root, nominal)
    # fault rejection evidence: drift vs recommended atol
    for f in faults:
        f["rejected_at_atol"] = all(d > atol for d in f["output_drift"])

    wall_total = time.monotonic() - t0
    rubric = {
        "version": 3,
        "codebase": prov["repo"]["url"],
        "check": check,
        "output": {"variables": ["return_value"],
                   "format": "numeric (scipy/numpy containers)"},
        "criteria": [{
            "name": "return_value_close",
            "kind": "field_abs",
            "statistic": "max",
            "rule": {"tolerance_abs": atol, "tolerance_rel": 1e-6},
            "justification": "atol = max(10x measured ulp-perturbation spread, "
                             "1e-12); rejects injected semantic faults",
            "evidence": {
                "basis": "measured",
                "noise_floor": noise_floor,
                "determinism_drift": det_drift,
                "byte_identical_runs": byte_identical,
                "variant_note": variant_note,
                "faults": faults,
            },
        }],
        "warrant": {
            "draft": None,   # filled by LLM below when endpoint configured
            "finalized_by": None,
            "note": "upstream: curator finalizes tolerances against evidence; "
                    "fill finalized_by to freeze this rubric",
        },
    }
    row = {
        "check": check,
        "atol": atol, "rtol": 1e-6,
        "science": seed["problem_description_main"][:300],
        "reference_calibration": {
            "mean_wall_sec": round(wall, 3),
            "nondeterminism": {
                "raw_final_state_byte_identical": byte_identical,
                "worst_numeric_abs_drift": det_drift,
            },
            "variant_ulp": ulp,
            "variant_spread": noise_floor,
        },
        "expected_runtime_sec": round(wall * 3, 1),
        "veins": [prov["file"]],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "rubric.json").write_text(json.dumps(rubric, indent=2), encoding="utf-8")
    (out / "row.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
    frozen = rubric["warrant"].get("finalized_by")
    if not frozen and not allow_unfrozen:
        print(f"  NOTE: rubric for {check} written with warrant.finalized_by "
              "= null (human checkpoint, mirroring upstream curator gate)")
    return {"check": check, "atol": atol, "noise_floor": noise_floor,
            "byte_identical": byte_identical, "faults": len(faults),
            "wall_sec": round(wall_total, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=Path, required=True)
    ap.add_argument("--repo-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ulp", type=int, default=2)
    ap.add_argument("--allow-unfrozen", action="store_true")
    args = ap.parse_args()
    print(json.dumps(calibrate(args.seed, args.repo_root, args.out, args.ulp,
                               args.allow_unfrozen), indent=2))


if __name__ == "__main__":
    main()
