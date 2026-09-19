"""AST-based defect-injection operators on SciCode gold sub-step code.

Python adaptation of ScienceInfra factory/operators.py (there it is regex +
liveness tracking for Fortran; here the AST gives docstring/string safety and
exact source spans). Families (names kept from ScienceInfra where applicable):

  sign        negate a numeric literal (a -> -a)
  coef        perturb a numeric coefficient (float *0.9, int +1)
  boundary    flip an Add<->Sub on an integer literal (n - 1 -> n + 1)
  index       shift an integer subscript/slice literal by +/-1
  drop-term   remove one additive term from a BinOp(Add) chain
  const       numerically nearby replacement of a math constant (np.pi -> 2*np.pi)
  compare     flip comparison operators (< <-> <=, > <-> >=)

Every candidate is a single-site exact-string edit (k_sites=1) whose `old`
segment occurs exactly once in the step source -- the same uniqueness contract
as ScienceInfra's defect.json so downstream roundtrip checks stay meaningful.

Usage:  python -m factory.operators --out .work/cand-inject.jsonl [--problems 1,10]
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

from . import config, lib


def _seg(code: str, node) -> str | None:
    return ast.get_source_segment(code, node)


def _edit(code, node, new_text, family, note, line) -> dict | None:
    old = _seg(code, node)
    if not old or old == new_text or code.count(old) != 1:
        return None
    return {"family": family, "note": note, "old": old, "new": new_text, "line": line}


class _Injector(ast.NodeVisitor):
    """Collect single-site mutation edits for one operator family."""

    def __init__(self, code: str):
        self.code = code
        self.edits: list[dict] = []

    # -- sign / coef / index: numeric literals ------------------------------
    def visit_Constant(self, node):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return self.generic_visit(node)
        seg = _seg(self.code, node)
        v = node.value
        if isinstance(v, float):
            e = _edit(self.code, node, repr(v * 0.9), "coef",
                      f"coefficient {seg} -> {v * 0.9:g}", node.lineno)
            if e:
                self.edits.append(e)
            if v != 0:
                e = _edit(self.code, node, repr(-v), "sign",
                          f"sign flip {seg} -> {-v:g}", node.lineno)
                if e:
                    self.edits.append(e)
        else:  # int
            if v != 0:
                e = _edit(self.code, node, str(v + 1), "coef",
                          f"integer coefficient {seg} -> {v + 1}", node.lineno)
                if e:
                    self.edits.append(e)
                e = _edit(self.code, node, str(-v), "sign",
                          f"sign flip {seg} -> {-v}", node.lineno)
                if e:
                    self.edits.append(e)
        self.generic_visit(node)

    # -- boundary: Add<->Sub on integer constants ---------------------------
    def visit_BinOp(self, node):
        if isinstance(node.op, (ast.Add, ast.Sub)) \
                and isinstance(node.right, ast.Constant) \
                and isinstance(node.right.value, int) \
                and not isinstance(node.right.value, bool):
            old = _seg(self.code, node)
            sym = "+" if isinstance(node.op, ast.Sub) else "-"
            lhs, lit = _seg(self.code, node.left), _seg(self.code, node.right)
            if old and lhs and lit:
                new = f"{lhs} {sym} {lit}"
                e = _edit(self.code, node, new, "boundary",
                          f"boundary flip {old} -> {new}", node.lineno)
                if e:
                    self.edits.append(e)
        elif isinstance(node.op, ast.Add):
            # drop-term: remove the right summand of an additive chain
            old = _seg(self.code, node)
            lhs = _seg(self.code, node.left)
            rhs = _seg(self.code, node.right)
            if old and lhs and rhs:
                e = _edit(self.code, node, lhs, "drop-term",
                          f"dropped additive term {rhs}", node.lineno)
                if e:
                    self.edits.append(e)
        self.generic_visit(node)

    # -- index: integer subscript / slice literals --------------------------
    def visit_Subscript(self, node):
        sl = node.slice
        targets = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        for t in targets:
            if isinstance(t, ast.Constant) and isinstance(t.value, int) \
                    and not isinstance(t.value, bool):
                old = _seg(self.code, t)
                delta = 1 if t.value >= 0 else -1
                newv = t.value + delta
                e = _edit(self.code, t, str(newv), "index",
                          f"index shift {old} -> {newv}", t.lineno)
                if e:
                    self.edits.append(e)
        self.generic_visit(node)

    # -- const: math constants ----------------------------------------------
    def visit_Attribute(self, node):
        qual = None
        if isinstance(node.value, ast.Name):
            qual = f"{node.value.id}.{node.attr}"
        if qual in ("np.pi", "numpy.pi", "math.pi"):
            e = _edit(self.code, node, f"({qual} / 2)", "const",
                      f"{qual} -> {qual} / 2", node.lineno)
            if e:
                self.edits.append(e)
        elif qual in ("np.e", "math.e"):
            e = _edit(self.code, node, f"({qual} * 2)", "const",
                      f"{qual} -> {qual} * 2", node.lineno)
            if e:
                self.edits.append(e)
        self.generic_visit(node)

    # -- compare -------------------------------------------------------------
    def visit_Compare(self, node):
        flips = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt}
        for j, op in enumerate(node.ops):
            op_type = type(op)
            if op_type not in flips:
                continue
            newop = ast.unparse(flips[op_type]()) if hasattr(ast, "unparse") \
                else {ast.LtE: "<=", ast.Lt: "<", ast.GtE: ">=", ast.Gt: ">"}[flips[op_type]]
            oldop_txt = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}[op_type]
            old = _seg(self.code, node)
            if not old:
                continue
            # replace only the j-th operator occurrence inside the chain
            parts = []
            cur = _seg(self.code, node.left)
            parts.append(cur)
            for k, (o, comp) in enumerate(zip(node.ops, node.comparators)):
                sym = oldop_txt if k == j else ast.unparse(o)
                parts.append(sym)
                parts.append(_seg(self.code, comp))
            new = " ".join(p for p in parts if p)
            e = _edit(self.code, node, new, "compare",
                      f"compare flip {old} -> {new}", node.lineno)
            if e:
                self.edits.append(e)
        self.generic_visit(node)


def inject_for_step(prob: lib.Problem, idx: int,
                    max_per_family: int | None = None) -> list[dict]:
    """All single-site injection candidates for one sub-step."""
    max_per_family = max_per_family or config.MAX_SITES_PER_FAMILY
    code = prob.step_function_source(idx)
    s = prob.steps[idx]
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    vis = _Injector(code)
    vis.visit(tree)
    by_family: dict[str, list[dict]] = {}
    for e in vis.edits:
        by_family.setdefault(e["family"], []).append(e)

    cands = []
    for fam, edits in by_family.items():
        kept = 0
        for e in edits:
            if kept >= max_per_family:
                break
            break_t = {"edits": [{"file": f"steps/{s['step_number']}.py",
                                  "old": e["old"], "new": e["new"]}]}
            fix_t = lib.inverse_transform(break_t)
            if not lib.roundtrip_ok(code, break_t, fix_t):
                continue
            cands.append(lib.make_candidate(
                source="inject", family=fam,
                problem_id=prob.problem_id, step_number=s["step_number"],
                break_t=break_t, fix_t=fix_t, note=e["note"], line=e["line"],
            ))
            kept += 1
    return cands


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=config.WORK_DIR / "cand-inject.jsonl")
    ap.add_argument("--problems", default=None)
    args = ap.parse_args()
    ids = set(args.problems.split(",")) if args.problems else None
    problems = lib.load_problems(problem_ids=ids)

    cands: list[dict] = []
    for pid in sorted(problems, key=lambda x: int(x)):
        prob = problems[pid]
        n0 = len(cands)
        for i in range(len(prob.steps)):
            if prob.steps[i]["step_number"] in config.ENV_SKIP_STEPS:
                continue
            cands.extend(inject_for_step(prob, i))
        print(f"{pid} {prob.record['problem_name']}: +{len(cands) - n0} candidates")
    lib.write_candidates(cands, args.out)
    fam = {}
    for c in cands:
        fam[c["family"]] = fam.get(c["family"], 0) + 1
    print(f"\ntotal {len(cands)} candidates -> {args.out}")
    print("by family:", dict(sorted(fam.items())))


if __name__ == "__main__":
    main()
