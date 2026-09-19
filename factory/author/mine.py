"""Mine self-contained scientific functions from an upstream repo checkout.

Heuristics (v1, conservative): module-level functions with docstrings,
numeric-library usage, no I/O / network / unseeded randomness, modest size.
This is the "propose broadly" stage of the ScienceIDE principle -- volume
here is cheap, verify.py is the authority on validity.

Usage:
    python -m factory.author.mine --repo /path/to/checkout --out .work/mined.jsonl \
        [--max-per-module 3] [--limit 50]
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

NUMERIC_ROOTS = {"np", "numpy", "sp", "scipy", "math", "cmath", "sympy"}
BANNED_CALLS = {"open", "input", "print", "eval", "exec", "compile",
                "globals", "locals", "getattr", "setattr", "exit", "quit"}
BANNED_ATTR_ROOTS = {"os", "sys", "subprocess", "socket", "urllib", "requests",
                     "time", "datetime", "pathlib", "shutil", "pickle",
                     "threading", "multiprocessing", "matplotlib", "plt"}


def _name_of(func) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self, source: str, rel_path: str):
        self.source = source
        self.rel_path = rel_path
        self.numeric = False
        self.banned = False

    def visit_Call(self, node):
        name = _name_of(node.func)
        if name in BANNED_CALLS:
            self.banned = True
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in BANNED_ATTR_ROOTS:
            self.banned = True
        if name == "random" or (isinstance(node.func, ast.Attribute)
                                and getattr(node.func.value, "id", "") == "random"):
            self.banned = True
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in NUMERIC_ROOTS:
            self.numeric = True
        self.generic_visit(node)

    def visit_Attribute(self, node):
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in NUMERIC_ROOTS:
            self.numeric = True
        self.generic_visit(node)


def score_function(source: str, rel_path: str) -> dict | None:
    """Return a mined-candidate dict or None if the function is unsuitable."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return None
    func = tree.body[0]
    if ast.get_docstring(func) is None:
        return None
    n_lines = func.end_lineno - func.lineno + 1
    if not (6 <= n_lines <= 80):
        return None
    args = func.args
    if args.vararg or args.kwarg or args.kwonlyargs:
        return None
    if not args.args:
        return None
    if not any(isinstance(n, ast.Return) and n.value is not None
               for n in ast.walk(func)):
        return None

    scanner = _Scanner(source, rel_path)
    scanner.visit(func)
    if scanner.banned or not scanner.numeric:
        return None

    return {
        "function": func.name,
        "file": rel_path,
        "n_lines": n_lines,
        "n_args": len(args.args),
        "docstring_first_line": (ast.get_docstring(func) or "").split("\n")[0][:200],
        "source": source,
    }


def iter_py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        parts = p.relative_to(root).parts
        if any(seg in ("test", "tests", "testing", "benchmarks", "conftest",
                       "_external", "vendor", "vendored", "third_party")
               or seg.startswith("test_") for seg in parts):
            continue
        yield p


def dotted_module(root: Path, py: Path) -> str:
    """Dotted module path relative to the IMPORT ROOT (the dir you pass as
    --repo / --repo-root, which verify puts on sys.path). For src-layout
    repos point --repo at the directory that CONTAINS the top package."""
    return ".".join(py.relative_to(root).with_suffix("").parts)


def mine_repo(root: Path, max_per_module: int = 3, limit: int | None = None):
    root = Path(root)
    out, per_module = [], {}
    for py in iter_py_files(root):
        rel = str(py.relative_to(root))
        mod_key = rel
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if per_module.get(mod_key, 0) >= max_per_module:
                break
            seg = ast.get_source_segment(text, node)
            if not seg:
                continue
            cand = score_function(seg, rel)
            if cand:
                cand["module_hint"] = dotted_module(root, py)
                out.append(cand)
                per_module[mod_key] = per_module.get(mod_key, 0) + 1
                if limit and len(out) >= limit:
                    return out
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True,
                    help="checkout of the upstream repo (or installed package dir)")
    ap.add_argument("--out", type=Path, default=Path(".work/mined.jsonl"))
    ap.add_argument("--max-per-module", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    cands = mine_repo(args.repo, args.max_per_module, args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fp:
        for c in cands:
            fp.write(json.dumps(c) + "\n")
    print(f"mined {len(cands)} candidate functions from {args.repo} -> {args.out}")


if __name__ == "__main__":
    main()
