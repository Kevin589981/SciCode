"""Convert completed native-reasoning rollouts to chat SFT without rewriting them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .schema import canonical_hash, validate_task, validate_trace
from .student_view import trace_student_prompt


def rows(path: Path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def build(tasks_path: Path, traces_path: Path, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output: {out_dir}")
    tasks = {task["task_id"]: validate_task(task) for task in rows(tasks_path)}
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "sft.jsonl"
    counts = Counter()
    seen = set()
    digest = hashlib.sha256()
    with output_path.open("wb") as output:
        for raw in rows(traces_path):
            trace = validate_trace(raw)
            trace_id = trace["trace_id"]
            if trace_id in seen:
                raise ValueError(f"duplicate trace: {trace_id}")
            seen.add(trace_id)
            task = tasks[trace["task_id"]]
            if trace["task_hash"] != canonical_hash(task):
                raise ValueError(f"task hash mismatch: {trace_id}")
            trace_student_prompt(task, trace)
            counts["source_traces"] += 1
            if trace.get("truncated") or trace.get("finish_reason") != "stop":
                counts["excluded_incomplete"] += 1
                continue
            if [item.get("role") for item in trace["messages"]] != [
                "system", "user", "assistant"
            ]:
                raise ValueError(f"unexpected message sequence: {trace_id}")
            assistant = trace["messages"][-1]
            reasoning = assistant.get("reasoning_content") or ""
            answer = assistant.get("content") or ""
            if "<think>" in reasoning or "</think>" in reasoning or "<think>" in answer:
                counts["excluded_malformed_boundary"] += 1
                continue
            if "</think>" in answer:
                # Some Qwen responses contain a duplicate draft and a literal
                # closing tag inside the final channel. Keep the final answer
                # after the last tag; never train on an extra thinking boundary.
                answer = answer.rsplit("</think>", 1)[1].strip()
                if not re.search(r"```\s*(?:python|py)\s*\n", answer, re.I):
                    counts["excluded_malformed_boundary"] += 1
                    continue
                counts["repaired_duplicate_final"] += 1
            if not reasoning.strip() or not answer.strip():
                counts["excluded_empty_target"] += 1
                continue
            row = {
                "id": trace_id,
                "messages": [
                    {"role": "system", "content": trace["messages"][0]["content"]},
                    {"role": "user", "content": trace["messages"][1]["content"]},
                    {"role": "assistant", "content": f"<think>\n{reasoning}\n</think>\n{answer}"},
                ],
                "metadata": {
                    "dataset_policy": "scicode-qwen-native-sft-v2",
                    "source_trace_id": trace_id,
                    "task_hash": trace["task_hash"],
                    "teacher_model": trace["model"],
                    "scientific_correctness_proven": False,
                    "termination": {
                        "finish_reason": trace["finish_reason"],
                        "truncated": trace["truncated"],
                        "usage": trace.get("usage") or {},
                    },
                },
            }
            payload = (json.dumps(row, ensure_ascii=False) + "\n").encode()
            output.write(payload)
            digest.update(payload)
            counts["sft_rows"] += 1
            counts["answer_python_fences"] += bool(
                re.search(r"```\s*(?:python|py)\s*\n", answer, re.I)
            )
    report = {
        "policy": "scicode-qwen-native-sft-v2",
        "source_tasks": len(tasks),
        "source_traces": counts["source_traces"],
        "sft_rows": counts["sft_rows"],
        "excluded_incomplete": counts["excluded_incomplete"],
        "excluded_empty_target": counts["excluded_empty_target"],
        "excluded_malformed_boundary": counts["excluded_malformed_boundary"],
        "repaired_duplicate_final": counts["repaired_duplicate_final"],
        "answer_python_fences": counts["answer_python_fences"],
        "scientific_correctness_proven": False,
        "output": str(output_path.resolve()),
        "sha256": digest.hexdigest(),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.tasks, args.traces, args.out_dir), indent=2))


if __name__ == "__main__":
    main()
