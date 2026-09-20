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


def build_step_prompt(seed: dict, step_idx: int,
                      prev_codes: list[str]) -> str:
    """SciCode-style multi-step prompt: step question + header + the
    student's OWN previous-step code as context (never the reference)."""
    s = seed["sub_steps"][step_idx]
    parts = []
    if step_idx == 0 and seed.get("problem_description_main"):
        parts += [seed["problem_description_main"].strip(), ""]
        if seed.get("problem_background_main"):
            parts += ["Background:", seed["problem_background_main"].strip(),
                      ""]
    parts += [f"Sub-step {step_idx + 1}/{len(seed['sub_steps'])}:",
              s["step_description_prompt"].strip(), ""]
    if s.get("step_background"):
        parts += ["Background:", s["step_background"].strip(), ""]
    parts += ["Function signature and docstring:", "```python",
              s["function_header"].strip(), "```", "",
              "The following imports are already available: "
              + seed["required_dependencies"].replace("\n", ", ")]
    if prev_codes:
        parts += ["", "Code you have already written for the previous "
                      "sub-steps (available in scope):", "```python",
                  "\n\n".join(prev_codes), "```"]
    parts += ["", "Implement this sub-step. Respond with one ```python block."]
    return "\n".join(parts)


def extract_code(content: str) -> str | None:
    m = FENCE.search(content or "")
    if not m:
        return None
    code = IMPORT_LINE.sub("", m.group(1))
    return code.strip() or None


ALLCLOSE = re.compile(
    r"^assert\s+np\.allclose\((.*),\s*target\s*,\s*atol=([^,]+),\s*rtol=([^\)]+)\)\s*$",
    re.S)


def _diagnostic_test(i: int, n: int, case: str) -> list[str] | None:
    """Rewrite one `assert np.allclose(call, target, atol=, rtol=)` case into
    a per-test diagnostic block that reports magnitude/localization instead
    of dying silently at the first failure. Returns None if the case does
    not match the expected shape (caller falls back to verbatim)."""
    m = ALLCLOSE.match(case.strip())
    if not m:
        return None
    call, atol, rtol = m.group(1), m.group(2), m.group(3)
    t = i + 1
    return [
        f"target = targets[{i}]",
        f"_a = {call}",
        "_t = target",
        "try:",
        f"    _ok = np.allclose(_a, _t, atol={atol}, rtol={rtol})",
        "except Exception as _e:",
        f'    print(f"[verifier] test {t}/{n} ERROR: '
        + '{type(_e).__name__}: {_e}")',
        f"    _failed.append({i})",
        "else:",
        "    if not _ok:",
        "        try:",
        "            _d = np.abs(np.asarray(_a, dtype=float)"
        " - np.asarray(_t, dtype=float))",
        f'            print(f"[verifier] test {t}/{n} FAIL '
        + 'max_abs_diff={float(_d.max()):.6g} shape={np.shape(_a)}")',
        "        except Exception:",
        f'            print(f"[verifier] test {t}/{n} FAIL '
        + '(got {type(_a).__name__}, expected'
        + ' {type(_t).__name__})")',
        f"        _failed.append({i})",
    ]


def run_verifier(seed: dict, code_steps: list[str], h5: Path,
                 step_idx: int = 0, timeout: int = 300) -> dict:
    """Judge step `step_idx` with the CUMULATIVE student code (its own
    earlier steps + this one) -- mirrors the official SciCode cascade where
    a broken earlier step dooms later ones."""
    s = seed["sub_steps"][step_idx]
    n = len(s["test_cases"])
    code = "\n\n".join(code_steps)
    lines = [seed["required_dependencies"], "", code, "",
             "from scicode.parse.parse import process_hdf5_to_tuple",
             f"targets = process_hdf5_to_tuple({s['step_number']!r}, {n}, {str(h5)!r})",
             "_failed = []"]
    for i in range(n):
        diag = _diagnostic_test(i, n, s["test_cases"][i])
        if diag is not None:
            lines.extend(diag)
        else:  # legacy/non-standard case: verbatim, still localized by index
            lines.append(f"target = targets[{i}]")
            lines.append("try:")
            lines.extend("    " + ln for ln in s["test_cases"][i].split("\n"))
            lines.append("except Exception as _e:")
            lines.append(f'    print(f"[verifier] test {i + 1}/{n} raised: '
                         '{type(_e).__name__}: {_e}")')
            lines.append(f"    _failed.append({i})")
    lines += ["", "if _failed:",
              '    print(f"[verifier] passed {len(targets) - len(_failed)}/'
              '{len(targets)}")',
              "    raise SystemExit(1)",
              'else:',
              '    print(f"[verifier] passed {len(targets)}/{len(targets)}")']
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fp:
        fp.write("\n".join(lines))
        path = fp.name
    t0 = time.monotonic()
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=timeout)
        diag_out = (r.stdout or "").strip()
        n_passed = None
        for ln in diag_out.splitlines():
            if ln.startswith("[verifier] passed"):
                try:
                    n_passed = int(ln.split()[2].split("/")[0])
                except (IndexError, ValueError):
                    pass
        status = "pass" if r.returncode == 0 else "fail"
        err = (r.stderr or "").strip()
        return {"status": status, "exit": r.returncode,
                "wall_sec": round(time.monotonic() - t0, 2),
                "n_passed": n_passed if n_passed is not None
                            else (n if status == "pass" else 0),
                "n_total": n,
                "diagnostics": diag_out[-1200:],
                "stderr_tail": err[-800:]}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "exit": None, "wall_sec": timeout,
                "n_passed": 0, "n_total": n, "diagnostics": "",
                "stderr_tail": ""}
    finally:
        Path(path).unlink(missing_ok=True)


def feedback_message(verifier: dict, turn: int) -> str:
    parts = [f"Verifier turn {turn}: {verifier['status']}. "
             f"{verifier.get('n_passed', '?')}/{verifier.get('n_total', '?')}"
             " tests passed."]
    diag = (verifier.get("diagnostics") or "").strip()
    if diag:
        parts.append(diag)
    tail = (verifier.get("stderr_tail") or "").strip().splitlines()
    if tail and not diag:
        parts.append(f"Last error: {tail[-1]}")
    parts.append("Fix the implementation and respond with a corrected "
                 "```python block.")
    return "\n".join(parts)


def solve_seed(seed: dict, h5: Path, attempts: int, max_turns: int,
               temperature: float, max_tokens: int = 4096) -> list[dict]:
    """Multi-step episode (SciCode-style chain): walk sub_steps in order,
    the student's own code accumulating; a step that never passes breaks the
    chain (later steps are not attempted, scoring 0 -- the official cascade).
    reward = mean over ALL steps of per-step scores (1 only if everything
    passed)."""
    steps = seed["sub_steps"]
    rows = []
    for attempt in range(attempts):
        t_start = time.monotonic()
        messages = [{"role": "system", "content": SYSTEM}]
        verifier_log, usage = [], {}
        student_codes: list[str] = []
        step_scores: list[float] = []
        total_turns = 0
        chain_broken = False
        for k, _s in enumerate(steps):
            messages.append({"role": "user",
                             "content": build_step_prompt(seed, k,
                                                          student_codes)})
            passed_k = False
            for turn in range(1, max_turns + 1):
                total_turns += 1
                resp = llm.chat(messages, temperature=temperature,
                                max_tokens=max_tokens)
                usage = llm.usage_of(resp)
                amsg = llm.assistant_message(resp)
                messages.append(amsg)
                code = extract_code(amsg.get("content", ""))
                if code is None:
                    v = {"status": "no_code", "exit": None, "wall_sec": 0.0,
                         "n_passed": 0, "n_total": len(steps[k]["test_cases"]),
                         "diagnostics": "", "stderr_tail": "no python fence"}
                else:
                    v = run_verifier(seed, student_codes + [code], h5,
                                     step_idx=k)
                verifier_log.append({"step": k + 1, "turn": turn, **v})
                if v["status"] == "pass":
                    student_codes.append(code)
                    step_scores.append(1.0)
                    passed_k = True
                    break
                score = ((v.get("n_passed") or 0)
                         / max(v.get("n_total") or 1, 1))
                messages.append({"role": "user",
                                 "content": feedback_message(
                                     {**v, "n_passed": v.get("n_passed"),
                                      "n_total": v.get("n_total")},
                                     turn)})
                if turn == max_turns:
                    step_scores.append(score)  # best partial on final try
            if not passed_k:
                chain_broken = True
                step_scores.extend([0.0] * (len(steps) - k - 1))
                break
        reward = (sum(step_scores) / len(steps)) if steps else 0.0
        if not chain_broken and step_scores and all(s == 1.0 for s in step_scores):
            reward = 1.0
        rows.append({
            "seed_id": seed["problem_id"], "attempt": attempt,
            "model": llm.client_config()["model"], "temperature": temperature,
            "turns": total_turns, "reward": reward,
            "n_steps": len(steps),
            "step_scores": [round(s, 4) for s in step_scores],
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
    ap.add_argument("--max-tokens", type=int, default=4096,
                    help="per-response token budget; thinking models need "
                         "8k-16k (the model may spend it all on reasoning)")
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

    # resume: seeds already present in an existing traces file are skipped,
    # the file is appended to -- endpoint outages must not waste finished work
    mode = "a" if traces_path.exists() else "w"
    if mode == "a":
        done_ids = set()
        with traces_path.open(encoding="utf-8") as fp:
            for ln in fp:
                try:
                    done_ids.add(json.loads(ln)["seed_id"])
                except json.JSONDecodeError:
                    pass
        before = len(seed_files)
        seed_files = [sf for sf in seed_files
                      if json.loads(sf.read_text(encoding="utf-8"))["problem_id"]
                      not in done_ids]
        print(f"resume: {len(done_ids)} seeds already traced, "
              f"{len(seed_files)}/{before} to go")

    n_rows = n_pass = 0
    with traces_path.open(mode, encoding="utf-8") as ft, \
            sft_path.open(mode, encoding="utf-8") as fs:
        for sf in seed_files:
            seed = json.loads(sf.read_text(encoding="utf-8"))
            print(f"solving {seed['problem_id']} ...")
            try:
                rows = solve_seed(seed, h5, args.attempts, args.max_turns,
                                  args.temperature, max_tokens=args.max_tokens)
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
