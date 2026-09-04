#!/usr/bin/env python3
"""Run the original sequential SciCode prompting chain through Kimi Code.

Strict mode intentionally gives the model no project tools or evaluator
feedback.  Each request is a fresh ``kimi -p`` call containing only the
previous generated function code and the current SciCode step.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


_TOOL_NAMES = [
    "Read",
    "Bash",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Agent",
    "AgentSwarm",
    "WebSearch",
    "FetchURL",
    "ReadMediaFile",
    "EnterPlanMode",
    "ExitPlanMode",
]


def _parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--problem",
        type=Path,
        default=repo_root / "tests/test_data/first_problem.jsonl",
        help="One-line SciCode JSONL record",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "runs/kimi-strict-77",
    )
    parser.add_argument("--run-id", default="kimi-strict-77-001")
    parser.add_argument("--kimi", default="/home/kevin/.kimi-code/bin/kimi")
    parser.add_argument("--model", default="Kimi-K3")
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--with-background", action="store_true")
    return parser


def _load_problem(path: Path) -> dict[str, Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(f"expected exactly one JSON object, found {len(lines)} lines")
    problem = json.loads(lines[0])
    if not isinstance(problem, dict) or not isinstance(problem.get("sub_steps"), list):
        raise ValueError("problem record must contain a sub_steps list")
    return problem


def _prompt(problem: dict[str, Any], completed: list[str], index: int, with_background: bool) -> str:
    steps = problem["sub_steps"]
    previous_lines: list[str] = []
    for previous_index, code in enumerate(completed):
        step = steps[previous_index]
        description = step["step_description_prompt"]
        if with_background:
            description += "\n" + step.get("step_background", "")
        previous_lines.extend([description, code, "------"])
    current = steps[index]
    current_description = current["step_description_prompt"]
    if with_background:
        current_description += "\n" + current.get("step_background", "")
    previous_text = "\n\n".join(previous_lines[:-1])
    next_text = "\n\n".join(
        [
            current_description,
            current["function_header"] + "\n\n" + current["return_line"],
        ]
    )
    template_path = Path(__file__).resolve().parents[1] / "eval/data/background_comment_template.txt"
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        problem_steps_str=previous_text,
        next_step_str=next_text,
        dependencies=problem["required_dependencies"],
    )


def _extract_code(content: str) -> str:
    if "```python" in content:
        content = content.split("```python", 1)[1].split("```", 1)[0]
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0]
    content = re.sub(r"^\s*(?:import .*|from .*\s+import\s+.*)$", "", content, flags=re.MULTILINE)
    return content.strip() + "\n"


def _write_tool_restriction(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    disabled = ", ".join(json.dumps(name) for name in _TOOL_NAMES)
    (home / "config.toml").write_text(
        "[tools]\n"
        f"disabled = [{disabled}]\n",
        encoding="utf-8",
    )


def _run_kimi(
    kimi: str,
    prompt: str,
    *,
    env: dict[str, str],
    timeout: float,
) -> tuple[list[dict[str, Any]], str, int, str]:
    try:
        process = subprocess.run(
            [kimi, "-p", prompt, "--output-format", "stream-json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return [], "", 124, f"timed out after {timeout:.1f}s\n{stderr}"
    records: list[dict[str, Any]] = []
    for line in process.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    assistant_parts: list[str] = []
    for record in records:
        if record.get("role") == "assistant" and isinstance(record.get("content"), str):
            assistant_parts.append(record["content"])
    return records, "".join(assistant_parts), process.returncode, process.stderr


def main() -> int:
    from scicode.trace import TraceRecorder

    args = _parser().parse_args()
    problem = _load_problem(args.problem)
    steps = problem["sub_steps"]
    limit = len(steps) if args.max_steps <= 0 else min(args.max_steps, len(steps))
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir = output_dir / "prompts"
    response_dir = output_dir / "responses"
    code_dir = output_dir / "code"
    for directory in (prompt_dir, response_dir, code_dir):
        directory.mkdir(parents=True, exist_ok=True)

    kimi_home = output_dir / ".kimi-code-home"
    _write_tool_restriction(kimi_home)
    env = os.environ.copy()
    env.update(
        {
            "KIMI_CODE_HOME": str(kimi_home),
            "KIMI_DISABLE_TELEMETRY": "1",
            "KIMI_CODE_NO_AUTO_UPDATE": "1",
            "KIMI_MODEL_NAME": args.model,
            "KIMI_MODEL_API_KEY": env.get("KIMI_API_KEY", "dummy"),
            "KIMI_MODEL_BASE_URL": "http://117.135.59.14:5050/v1",
            "KIMI_MODEL_PROVIDER_TYPE": "openai",
            "KIMI_MODEL_MAX_CONTEXT_SIZE": "262144",
            "KIMI_MODEL_CAPABILITIES": "thinking",
        }
    )
    recorder = TraceRecorder(
        output_dir,
        run_id=args.run_id,
        candidate_id=f"strict-{problem['problem_id']}",
        task_revision="r1",
        mode="strict",
        provider="kimi-openai-compatible",
        model=args.model,
        sampling={"temperature": 1.0, "stream_json": True},
        prompt_version="scicode-upstream-v1",
    )

    completed: list[str] = []
    for index in range(limit):
        step = steps[index]
        step_id = str(step["step_number"])
        prompt = _prompt(problem, completed, index, args.with_background)
        (prompt_dir / f"{step_id}.txt").write_text(prompt, encoding="utf-8")
        recorder.record(
            "prompt",
            {"step_id": step_id, "text": prompt},
            step_id=step_id,
        )
        records, assistant_text, returncode, stderr = _run_kimi(
            args.kimi,
            prompt + "\n\nStrict mode: do not call tools. Return only the executable Python code for this step.",
            env=env,
            timeout=args.timeout,
        )
        (response_dir / f"{step_id}.stream.jsonl").write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        for record in records:
            recorder.record_raw(record)
            if record.get("role") == "assistant" and record.get("tool_calls"):
                recorder.record(
                    "tool_call",
                    {"step_id": step_id, "count": len(record["tool_calls"])},
                    visibility="private",
                    step_id=step_id,
                )
        if any(record.get("role") in {"tool", "tool_result"} for record in records) or any(
            record.get("tool_calls") for record in records
        ):
            recorder.record(
                "strict_violation",
                {"step_id": step_id, "reason": "tool call emitted"},
                visibility="private",
                step_id=step_id,
            )
            recorder.finalize(verification={"passed": False, "failed_step": step_id, "reason": "tool call"})
            print(json.dumps({"status": "failed", "reason": "strict mode emitted a tool call", "step_id": step_id}))
            return 2
        if returncode != 0 or not assistant_text.strip():
            recorder.record(
                "error",
                {"step_id": step_id, "returncode": returncode, "stderr": stderr[-1000:]},
                visibility="private",
                step_id=step_id,
            )
            recorder.finalize(verification={"passed": False, "failed_step": step_id, "reason": "provider failure"})
            print(json.dumps({"status": "failed", "reason": "provider failure", "step_id": step_id}))
            return 2
        code = _extract_code(assistant_text)
        (code_dir / f"{step_id}.py").write_text(code, encoding="utf-8")
        recorder.record("assistant_message", {"text": assistant_text}, step_id=step_id)
        recorder.record("code_snapshot", {"chars": len(code)}, step_id=step_id)
        recorder.record("step_result", {"status": "generated", "code_chars": len(code)}, step_id=step_id)
        completed.append(code)

    recorder.record("run_end", {"status": "completed", "steps": limit})
    manifest = recorder.finalize(verification={"passed": True, "steps_completed": limit})
    visible_count = recorder.export_visible(output_dir / "trace" / "public_events.jsonl")
    print(
        json.dumps(
            {
                "status": "ok",
                "mode": "strict",
                "problem_id": problem["problem_id"],
                "steps_completed": limit,
                "visible_event_count": visible_count,
                "event_count": manifest["event_count"],
                "events": str(recorder.events_path),
                "manifest": str(recorder.manifest_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
