"""Run a solver LLM over validated seeds; capture raw traces.

Episode shape (ScienceIDE-style multi-turn with verifier feedback):
    user(problem) -> assistant(code) -> verifier(pass/fail + stderr)
    -> [feedback turns until pass or MAX_TURNS] -> reward in {0,1}

Raw trace rows (data/traces.jsonl, PRIMARY deliverable):
    {seed_id, attempt, model, temperature, turns, reward,
     messages: [...verbatim...], verifier: [...per-turn results...],
     usage, timing_sec}

SFT rows (data/sft.jsonl, derived): messages with loss flags
(assistant=True, everything else=False), matching the convention in
ScienceIDE/SFT/README.md.

Usage:
    python -m factory.solve.traces --seeds seeds --out data \
        [--attempts 4] [--max-turns 3] [--temperature 0.7] [--limit 5]
Requires SCICODE_LLM_BASE_URL / SCICODE_LLM_API_KEY / SCICODE_LLM_MODEL.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..author import llm

IMPORT_LINE = re.compile(r"^\s*(import|from)\s+\S+\s+import\s+.*$", re.M)
FENCE = re.compile(r"```python\s*\n(.*?)```", re.S)


SYSTEM = ("You are an expert computational scientist. Solve the coding task. "
          "Respond with exactly one ```python fenced block containing the "
          "complete function(s). Do not include import statements or "
          "explanations outside the block.")


def build_prompt(seed: dict) -> str:
    s = seed["sub_steps"][0]
    parts = [seed["problem_description_main"].strip(), ""]
    if seed.get("problem_background_main"):
        parts += ["Background:", seed["problem_background_main"].strip(), ""]
    parts += ["Function signature and docstring:", "```python",
              s["function_header"].strip(), "```", "",
              "The following imports are already available: "
              + seed["required_dependencies"].replace("\n", ", "), "",
              "Implement the function body. Respond with one ```python block."]
    return "\n".join(parts)


def extract_code(content: str) -> str | None:
    m = FENCE.search(content or "")
    if not m:
        return None
    code = IMPORT_LINE.sub("", m.group(1))
    return code.strip() or None


def run_verifier(seed: dict, code: str, h5: Path, timeout: int = 300) -> dict:
    s = seed["sub_steps"][0]
    n = len(s["test_cases"])
    lines = [seed["required_dependencies"], "", code, "",
             "from scicode.parse.parse import process_hdf5_to_tuple",
             f"targets = process_hdf5_to_tuple({s['step_number']!r}, {n}, {str(h5)!r})"]
    for i in range(n):
        lines.append(f"target = targets[{i}]")
        lines.extend(s["test_cases"][i].split("\n"))
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fp:
        fp.write("\n".join(lines))
        path = fp.name
    t0 = time.monotonic()
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=timeout)
        status = "pass" if r.returncode == 0 else "fail"
        err = (r.stderr or "").strip()
        return {"status": status, "exit": r.returncode,
                "wall_sec": round(time.monotonic() - t0, 2),
                "stderr_tail": err[-800:]}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "exit": None, "wall_sec": timeout,
                "stderr_tail": ""}
    finally:
        Path(path).unlink(missing_ok=True)


def feedback_message(verifier: dict, turn: int) -> str:
    tail = verifier["stderr_tail"].strip().splitlines()
    last = tail[-1] if tail else "(no output)"
    return (f"Verifier turn {turn}: {verifier['status']}.\n"
            f"Last error: {last}\nFix the implementation and respond with a "
            "corrected ```python block.")


def solve_seed(seed: dict, h5: Path, attempts: int, max_turns: int,
               temperature: float) -> list[dict]:
    prompt = build_prompt(seed)
    rows = []
    for attempt in range(attempts):
        t_start = time.monotonic()
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt}]
        verifier_log, usage, reward = [], {}, 0
        turns = 0
        for turn in range(1, max_turns + 1):
            turns = turn
            resp = llm.chat(messages, temperature=temperature)
            usage = llm.usage_of(resp)
            amsg = llm.assistant_message(resp)
            messages.append(amsg)
            code = extract_code(amsg.get("content", ""))
            if code is None:
                v = {"status": "no_code", "exit": None, "wall_sec": 0.0,
                     "stderr_tail": "no python fence in response"}
            else:
                v = run_verifier(seed, code, h5)
            verifier_log.append({"turn": turn, **v})
            if v["status"] == "pass":
                reward = 1
                break
            messages.append({"role": "user",
                             "content": feedback_message(v, turn)})
        rows.append({
            "seed_id": seed["problem_id"], "attempt": attempt,
            "model": llm.client_config()["model"], "temperature": temperature,
            "turns": turns, "reward": reward,
            "messages": messages, "verifier": verifier_log,
            "usage": usage,
            "timing_sec": round(time.monotonic() - t_start, 2),
        })
    return rows


def to_sft_row(trace: dict) -> dict:
    msgs = []
    for m in trace["messages"]:
        entry = {"role": m["role"], "content": m.get("content", "")}
        if m["role"] == "assistant":
            entry["loss"] = True
            if m.get("reasoning_content"):
                entry["reasoning_content"] = m["reasoning_content"]
        else:
            entry["loss"] = False
        msgs.append(entry)
    return {"task_name": trace["seed_id"],
            "messages": msgs,
            "reward": trace["reward"],
            "source": "scicode-factory traces"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--max-turns", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sft-all", action="store_true",
                    help="include failed episodes in sft.jsonl (default: "
                         "verified reward=1 only -- failed traces stay in "
                         "traces.jsonl for analysis but are not trained on)")
    args = ap.parse_args()

    seed_files = sorted(args.seeds.glob("*.json"))
    if args.limit:
        seed_files = seed_files[: args.limit]
    h5 = args.seeds / "test_data.h5"
    args.out.mkdir(parents=True, exist_ok=True)
    traces_path = args.out / "traces.jsonl"
    sft_path = args.out / "sft.jsonl"

    n_rows = n_pass = 0
    with traces_path.open("w", encoding="utf-8") as ft, \
            sft_path.open("w", encoding="utf-8") as fs:
        for sf in seed_files:
            seed = json.loads(sf.read_text(encoding="utf-8"))
            print(f"solving {seed['problem_id']} ...")
            try:
                rows = solve_seed(seed, h5, args.attempts, args.max_turns,
                                  args.temperature)
            except Exception as e:
                print(f"  solver error: {e}")
                continue
            for r in rows:
                ft.write(json.dumps(r) + "\n")
                if r["reward"] == 1 or args.sft_all:
                    fs.write(json.dumps(to_sft_row(r)) + "\n")
                n_rows += 1
                n_pass += r["reward"] == 1
            print(f"  rewards: {[r['reward'] for r in rows]}")
    print(f"\ntraces: {n_rows} episodes, pass rate {n_pass}/{n_rows} "
          f"-> {traces_path} (+ {sft_path})")


if __name__ == "__main__":
    main()
