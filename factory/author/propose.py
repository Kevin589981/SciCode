"""LLM-assisted proposal: mined function -> SciCode-style sub-step.

The LLM sees ONLY the upstream function source + provenance and must emit:
    question       SciCode-style sub-step prompt (what to implement)
    background     scientific context (may be "")
    test_inputs    list of 3 literal python expressions, each evaluating to
                   the positional-args tuple, e.g. "(np.arange(5.0), 2)"
    dependencies   import lines needed by the test expressions

Everything the LLM claims is then *executed* against the original code by
verify.py -- proposals are cheap, execution is the gate.

Usage:
    python -m factory.author.propose --mined .work/mined.jsonl \
        --repo-meta repo_meta.json --out .work/proposals.jsonl [--limit 20]
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from . import llm

PROMPT = """You are authoring one sub-step of a scientific research coding benchmark in the style of SciCode.

Upstream repository (pinned): {repo_url} @ {commit} (license {license})
Function location: {file}
Function source (this is GROUND TRUTH from upstream -- do not modify it):

```python
{source}
```

Write a benchmark sub-step that asks a strong LLM to implement this function from scratch.

Rules:
- The question must be self-contained: describe the scientific/numerical task, inputs and outputs, and any formulas from the docstring. Do NOT mention the upstream repository or that reference code exists.
- background: 2-6 sentences of scientific context, or empty string.
- test_inputs: exactly 3 literal Python expressions. Each expression must evaluate to a tuple of positional arguments for the function. They must be small, deterministic, and exercise the function's main behavior (e.g. a typical case, an edge case, a different regime). Use only numpy (as np), scipy (as sp), math and cmath.
- dependencies: the import lines the test_inputs need.
- Do not invent behavior the source does not have.

Respond with ONLY a JSON object:
{{"question": "...", "background": "...", "test_inputs": ["(...)", "...", "..."], "dependencies": ["import numpy as np"]}}"""

GROUP_PROMPT = """You are authoring a TWO-STEP chain of a scientific research coding benchmark in the style of SciCode's sub-steps.

Upstream repository (pinned): {repo_url} @ {commit} (license {license})

Step-1 function (called by step 2) -- GROUND TRUTH from upstream:
```python
{dep_source}
```
Location: {dep_file}

Step-2 function (calls the step-1 function) -- GROUND TRUTH from upstream:
```python
{root_source}
```
Location: {root_file}

Author a two-step chain where the solver first implements `{dep_fn}`, then implements `{root_fn}` ON TOP OF ITS OWN step-1 code (exactly like the real code does).

Rules:
- Each step's question must be fully self-contained (task, inputs, outputs, formulas, and EVERY numeric convention/constant value -- never reference "constants defined in the library"). Do NOT mention the upstream repository or that reference code exists.
- steps[0].test_inputs: 3 literal expressions evaluating to argument tuples for `{dep_fn}`.
- steps[1].test_inputs: 3 literal expressions evaluating to argument tuples for `{root_fn}` (valid standalone; when the student's step-1 is also present the whole chain must run).
- Keep inputs small, deterministic, typical + edge regime. numpy (np), scipy (sp), math, cmath only.
- Do not invent behavior the sources do not have.

Respond with ONLY a JSON object:
{{"question": "overall one-sentence problem statement", "background": "...", "steps": [{{"function": "{dep_fn}", "question": "...", "background": "...", "test_inputs": ["(...)", "...", "..."]}}, {{"function": "{root_fn}", "question": "...", "background": "...", "test_inputs": ["(...)", "...", "..."]}}], "dependencies": ["import numpy as np"]}}"""


def _signature_of(source: str) -> str:
    tree = ast.parse(source)
    func = tree.body[0]
    seg = ast.get_source_segment(source, func)
    return seg.split(":", 1)[0] + ":"


def _parse_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def build_group_proposal(group: dict, repo_meta: dict,
                         temperature: float,
                         max_tokens: int = 4096, timeout: int = 900) -> dict | None:
    root, dep = group["root"], group["dep"]
    prompt = GROUP_PROMPT.format(
        repo_url=repo_meta.get("url", "?"), commit=repo_meta.get("commit", "?"),
        license=repo_meta.get("license", "?"),
        dep_source=dep["source"], dep_file=dep["file"],
        root_source=root["source"], root_file=root["file"],
        dep_fn=dep["function"], root_fn=root["function"])
    resp = llm.chat([{"role": "user", "content": prompt}],
                    temperature=temperature, max_tokens=max_tokens,
                    timeout=timeout)
    content = resp["choices"][0]["message"].get("content") or ""
    if not content.strip():
        raise ValueError("empty model response (thinking budget exhausted? "
                         "raise --max-tokens)")
    spec = _parse_json(content)
    if not (isinstance(spec.get("steps"), list) and len(spec["steps"]) == 2):
        return None
    for step in spec["steps"]:
        if not (step.get("question") and len(step.get("test_inputs", [])) >= 2
                and step.get("function")):
            return None
    slug = f"{repo_meta.get('slug', 'repo')}-{root['file'].replace('/', '-').replace('.py', '')}-{root['function']}"
    fn2src = {dep["function"]: dep["source"], root["function"]: root["source"]}
    fn2mod = {dep["function"]: dep.get("module_hint", ""),
              root["function"]: root.get("module_hint", "")}
    sub_steps = []
    for j, step in enumerate(spec["steps"]):
        src = fn2src.get(step["function"])
        if src is None:
            return None
        sub_steps.append({
            "step_number": None,  # filled by verify (slug-k)
            "function": step["function"],
            "module_hint": fn2mod.get(step["function"], ""),
            "step_description_prompt": step["question"],
            "step_background": step.get("background", ""),
            "function_header": _signature_of(src) + "\n    \"\"\"" + (
                ast.get_docstring(ast.parse(src).body[0]) or "") + "\"\"\"",
            "ground_truth_code": src,
            "test_inputs": step["test_inputs"][:4],
            "dependencies": spec.get("dependencies", []),
        })
    return {
        "slug": slug[:120],
        "repo": repo_meta,
        "file": root["file"],
        "function": root["function"],
        "module_hint": group.get("module_hint", ""),
        "chain": True,
        "question": spec["question"],
        "background": spec.get("background", ""),
        "sub_steps": sub_steps,
        "reference_source": root["source"],
        "llm": {"temperature": temperature, "usage": llm.usage_of(resp)},
    }


def build_proposal(cand: dict, repo_meta: dict, temperature: float,
                   max_tokens: int = 4096, timeout: int = 900) -> dict | None:
    prompt = PROMPT.format(
        repo_url=repo_meta.get("url", "?"), commit=repo_meta.get("commit", "?"),
        license=repo_meta.get("license", "?"), file=cand["file"],
        source=cand["source"])
    resp = llm.chat([{"role": "user", "content": prompt}],
                    temperature=temperature, max_tokens=max_tokens,
                    timeout=timeout)
    text = resp["choices"][0]["message"].get("content") or ""
    # tolerate ```json fences
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        return None
    for key in ("question", "background", "test_inputs", "dependencies"):
        if key not in spec:
            return None
    if not (isinstance(spec["test_inputs"], list) and len(spec["test_inputs"]) >= 2):
        return None
    slug = f"{repo_meta.get('slug', 'repo')}-{cand['file'].replace('/', '-').replace('.py','')}-{cand['function']}"
    return {
        "slug": slug[:120],
        "repo": repo_meta,
        "file": cand["file"],
        "function": cand["function"],
        "module_hint": cand.get("module_hint", ""),
        "function_header": _signature_of(cand["source"]) + "\n    \"\"\"" + (
            ast.get_docstring(ast.parse(cand["source"]).body[0]) or "") + "\"\"\"",
        "reference_source": cand["source"],
        "question": spec["question"],
        "background": spec["background"],
        "test_inputs": spec["test_inputs"][:4],
        "dependencies": spec["dependencies"],
        "llm": {"temperature": temperature,
                "usage": llm.usage_of(resp)},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mined", type=Path, required=True)
    ap.add_argument("--repo-meta", type=Path, required=True,
                    help="JSON: {url, commit, license, slug}")
    ap.add_argument("--out", type=Path, default=Path(".work/proposals.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=900,
                    help="per-request seconds; 16k thinking responses need "
                         "long budgets under concurrency")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    repo_meta = json.loads(args.repo_meta.read_text(encoding="utf-8"))
    cands = [json.loads(l) for l in args.mined.open(encoding="utf-8")]
    if args.limit:
        cands = cands[: args.limit]

    ok, bad = 0, 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    import concurrent.futures as cf

    def _one(cand):
        try:
            if "root" in cand and "dep" in cand:  # call-chain group
                return build_group_proposal(cand, repo_meta, args.temperature,
                                            args.max_tokens, args.timeout)
            return build_proposal(cand, repo_meta, args.temperature,
                                  args.max_tokens, args.timeout)
        except Exception as e:
            label = f"{cand.get('file', '?')}:{cand.get('function', '?')}"
            print(f"  LLM error on {label}: {e}")
            return None

    with args.out.open("w", encoding="utf-8") as fp, \
            cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(_one, c): c for c in cands}
        done = 0
        for fut in cf.as_completed(futs):
            cand = futs[fut]
            prop = fut.result()
            done += 1
            if prop:
                fp.write(json.dumps(prop) + "\n")
                ok += 1
            else:
                bad += 1
            if done % 10 == 0 or done == len(cands):
                print(f"[{done}/{len(cands)}] ok={ok} bad={bad}", flush=True)
    print(f"\nproposals: {ok} ok / {bad} rejected -> {args.out}")


if __name__ == "__main__":
    main()
