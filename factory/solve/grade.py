"""Independent trace-quality grading + bucketed export.

An INDEPENDENT stage (not part of authoring): a judge model reads each
episode -- problem, thinking, code, verifier outcomes, reward -- and grades
QUALITY. Reward alone is a poor filter: a first-try pass on a trivial
problem is thin training material, while a failed episode may contain deep,
informative exploration (and vice versa: pure unproductive rambling is bad
regardless of reward).

Buckets on export: {pass,fail} x {good,medium,bad} ->
    <out>/pass_good.jsonl, pass_medium.jsonl, pass_bad.jsonl,
        fail_good.jsonl, fail_medium.jsonl, fail_bad.jsonl

Usage:
  python -m factory.solve.grade --traces data/traces.jsonl \
      --seeds seeds/<env> --out data/graded [--model ...] [--limit N]
Requires SCICODE_LLM_* endpoint config (the judge model may differ from the
solver; grade.py re-reads env, so just re-export before running).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from ..author import llm

JUDGE_PROMPT = """You are grading the QUALITY of one problem-solving trace from a scientific coding benchmark, for its value in training a model.

## The problem
{question}

## Trace digest (thinking excerpts, submitted code, verifier outcomes)
{digest}

## Outcome
reward {reward} (1 = all tests passed), {turns} model turns, model {model}.

Grade the trace's VALUE FOR TRAINING, NOT whether it succeeded:
- good: contains real scientific/numerical reasoning (algorithm choice, edge cases, derivation, inference from error magnitudes), OR an informative failure with reasonable hypotheses tested; the problem itself required non-trivial thought.
- medium: competent but routine implementation; thin reasoning; or a hard problem solved by shallow pattern-matching.
- bad: trivial boilerplate that needed no reasoning; OR no informative content at all (e.g. empty outputs, pure source-recall rambling with zero effective reasoning, repeated identical failures with no hypothesis change).

Remember: failure does NOT imply bad quality, and success does NOT imply good quality. Judge the reasoning.

Respond with ONLY a JSON object: {{"quality": "good"|"medium"|"bad", "rationale": "<=150 words, cite concrete evidence from the trace>"}}"""


def digest_episode(row: dict, max_think: int = 1200, max_code: int = 500,
                   max_diag: int = 300) -> str:
    parts = []
    vmap = {(v.get("step", 1), v["turn"]): v for v in row.get("verifier", [])}
    step = 1
    turn = 0
    for m in row["messages"]:
        if m["role"] == "user":
            c = (m.get("content") or "")
            if "Verifier" in c:
                turn += 1
                v = vmap.get((step, turn))
                if v:
                    parts.append(
                        f"[verifier] step {step} turn {turn}: {v['status']} "
                        f"{v.get('n_passed', '?')}/{v.get('n_total', '?')} "
                        f"{(v.get('diagnostics') or '')[:max_diag]}")
        elif m["role"] == "assistant":
            code = (m.get("content") or "").strip()
            if code:
                parts.append(f"[code]\n{code[:max_code]}")
            th = (m.get("reasoning_content") or "").strip()
            if th:
                excerpt = th if len(th) <= max_think else (
                    th[: max_think // 2] + "\n...[middle omitted]...\n"
                    + th[-max_think // 2:])
                parts.append(f"[thinking]\n{excerpt}")
    return "\n\n".join(parts)


def grade_row(row: dict, question: str, temperature: float = 0.0) -> dict:
    digest = digest_episode(row)
    prompt = JUDGE_PROMPT.format(
        question=question[:2500], digest=digest[:9000],
        reward=row["reward"], turns=row["turns"], model=row.get("model", "?"))
    resp = llm.chat([{"role": "user", "content": prompt}],
                    temperature=temperature, max_tokens=1500)
    text = (resp["choices"][0]["message"].get("content") or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    verdict = json.loads(text)
    quality = verdict.get("quality", "bad")
    if quality not in ("good", "medium", "bad"):
        quality = "bad"
    return {"seed_id": row["seed_id"], "attempt": row["attempt"],
            "reward": row["reward"], "quality": quality,
            "rationale": (verdict.get("rationale") or "")[:600],
            "judge": llm.client_config()["model"],
            "judge_usage": llm.usage_of(resp)}


def export_buckets(rows: list[dict], grades: dict[tuple, dict],
                   out: Path) -> Counter:
    out.mkdir(parents=True, exist_ok=True)
    buckets: Counter = Counter()
    handles = {}
    for row in rows:
        g = grades.get((row["seed_id"], row["attempt"]))
        if g is None:
            continue
        bucket = ("pass" if row["reward"] == 1 else "fail") + "_" + g["quality"]
        if bucket not in handles:
            handles[bucket] = (out / f"{bucket}.jsonl").open(
                "w", encoding="utf-8")
        handles[bucket].write(json.dumps(
            {**row, "quality": g["quality"], "quality_rationale": g["rationale"]}
        ) + "\n")
        buckets[bucket] += 1
    for h in handles.values():
        h.close()
    return buckets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", type=Path, required=True)
    ap.add_argument("--seeds", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--grade-limit", type=int, default=None,
                    help="grade only the first N episodes")
    args = ap.parse_args()

    rows = [json.loads(l) for l in args.traces.open(encoding="utf-8")]
    if args.limit:
        rows = rows[: args.limit]

    questions = {}
    for sf in args.seeds.glob("*.json"):
        seed = json.loads(sf.read_text(encoding="utf-8"))
        questions[seed["problem_id"]] = seed["problem_description_main"]

    args.out.mkdir(parents=True, exist_ok=True)
    grades_path = args.out / "grades.jsonl"
    grades: dict[tuple, dict] = {}
    if grades_path.exists():
        for l in grades_path.open(encoding="utf-8"):
            g = json.loads(l)
            grades[(g["seed_id"], g["attempt"])] = g
    todo = [r for r in rows if (r["seed_id"], r["attempt"]) not in grades]
    if args.grade_limit:
        todo = todo[: args.grade_limit]
    print(f"grading {len(todo)} episodes "
          f"({len(grades)} already graded) ...")
    import concurrent.futures as cf
    with grades_path.open("a", encoding="utf-8") as fg, \
            cf.ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(grade_row, r,
                            questions.get(r["seed_id"], "")): r for r in todo}
        done = 0
        for fut in cf.as_completed(futs):
            row = futs[fut]
            try:
                g = fut.result()
            except Exception as e:
                g = {"seed_id": row["seed_id"], "attempt": row["attempt"],
                     "reward": row["reward"], "quality": "bad",
                     "rationale": f"judge error: {e}"[:300],
                     "judge": "error", "judge_usage": {}}
            grades[(g["seed_id"], g["attempt"])] = g
            fg.write(json.dumps(g) + "\n")
            done += 1
            if done % 10 == 0 or done == len(todo):
                print(f"  [{done}/{len(todo)}]", flush=True)

    buckets = export_buckets(rows, grades, args.out)
    q = Counter(g["quality"] for g in grades.values())
    print(f"\nquality distribution: {dict(q)}")
    print(f"exported buckets -> {args.out}:")
    for b, n in sorted(buckets.items()):
        print(f"  {b}: {n}")


if __name__ == "__main__":
    main()
