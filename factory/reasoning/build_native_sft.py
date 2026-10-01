"""Convert completed native-reasoning rollouts to chat SFT without rewriting them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import copy
from collections import Counter
from pathlib import Path

from .schema import canonical_hash, validate_task, validate_trace
from .student_view import trace_student_prompt
from .prepare_reused_tasks import file_sha256


def rows(path: Path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def normalize_channels(assistant: dict, *, repair_qwen: bool = False) -> tuple[str, str, str]:
    """Keep native channels verbatim; unwrap one leading tagged response only."""
    reasoning = assistant.get("reasoning_content") or ""
    answer = assistant.get("content") or ""
    if not isinstance(reasoning, str) or not isinstance(answer, str):
        raise ValueError("non-text native response")
    normalization = "native_separate_channels"
    if not reasoning and answer.startswith("<think>") and answer.count("<think>") == 1 and answer.count("</think>") == 1:
        reasoning, answer = answer[len("<think>"):].split("</think>", 1)
        normalization = "unwrap_single_leading_think_block"
    if "<think>" in reasoning or "</think>" in reasoning or "<think>" in answer:
        raise ValueError("ambiguous thinking boundary")
    if "</think>" in answer:
        if not repair_qwen:
            raise ValueError("unexpected closing thinking boundary")
        answer = answer.rsplit("</think>", 1)[1].strip()
        if not re.search(r"```\s*(?:python|py)\s*\n", answer, re.I):
            raise ValueError("Qwen boundary repair has no final Python block")
        normalization = "qwen_duplicate_final_repair"
    return reasoning, answer, normalization


def audit_candidate(task: dict, trace: dict, *, repair_qwen: bool = False) -> dict:
    """Same byte-serializable candidate for live review and final native export."""
    validate_trace(trace)
    if trace["task_hash"] != canonical_hash(task):
        raise ValueError("candidate task mismatch")
    trace_student_prompt(task, trace)
    if trace.get("truncated") or trace.get("finish_reason") != "stop":
        raise ValueError("incomplete generation")
    if [m["role"] for m in trace["messages"]] != ["system", "user", "assistant"]:
        raise ValueError("unexpected message sequence")
    reasoning, answer, _ = normalize_channels(trace["messages"][-1], repair_qwen=repair_qwen)
    if not reasoning.strip() or not answer.strip():
        raise ValueError("missing reasoning or final answer")
    candidate = copy.deepcopy(trace)
    candidate["thinking_format"] = "separate_reasoning_content"
    candidate["audit_prompt_policy"] = "system-user-v1"
    candidate["archetype"] = task["archetype"]
    candidate["termination"] = {"finish_reason": trace["finish_reason"],
                                "truncated": trace["truncated"], "usage": trace.get("usage") or {}}
    candidate["messages"][-1].update({"reasoning_content": reasoning, "content": answer,
                                    "reasoning_loss": True, "content_loss": True})
    return candidate


def build(tasks_path: Path, traces_path: Path, out_dir: Path, *,
          policy: str = "scicode-qwen-native-sft-v2",
          expected_teacher: str | None = None,
          repair_qwen: bool = True,
          source_index_path: Path | None = None,
          run_manifest: dict | None = None) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output: {out_dir}")
    tasks = {task["task_id"]: validate_task(task) for task in rows(tasks_path)}
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "sft.jsonl"
    counts = Counter()
    seen = set()
    digest = hashlib.sha256()
    source_index = {
        r["task_hash"]: r for r in rows(source_index_path)
    } if source_index_path else {}
    latest = {}
    with traces_path.open("rb") as stream:
        while True:
            offset = stream.tell()
            raw = stream.readline()
            if not raw:
                break
            if not raw.strip():
                continue
            trace = validate_trace(json.loads(raw))
            identity = trace["trace_id"]
            previous = latest.get(identity)
            prompt_hash = canonical_hash(trace["messages"][:2])
            # A stop marker without a final answer is not a completed target.
            try:
                r, a, _ = normalize_channels(trace["messages"][-1], repair_qwen=repair_qwen)
                complete = trace.get("finish_reason") == "stop" and not trace.get("truncated") and bool(r.strip() and a.strip())
            except ValueError:
                complete = False
            if previous and (
                previous["complete"] or previous["task_hash"] != trace["task_hash"]
                or previous["prompt_hash"] != prompt_hash or previous["model"] != trace["model"]
            ):
                raise ValueError(f"unsafe duplicate trace: {identity}")
            latest[identity] = {"offset": offset, "length": len(raw),
                                "complete": complete,
                                "task_hash": trace["task_hash"], "prompt_hash": prompt_hash,
                                "model": trace["model"], "raw_sha256": hashlib.sha256(raw).hexdigest()}
    with output_path.open("wb") as output, (out_dir / "audit-candidates.jsonl").open("w", encoding="utf-8") as candidates, (out_dir / "excluded.jsonl").open("w", encoding="utf-8") as excluded:
        def exclude(trace, reason):
            excluded.write(json.dumps({"trace_id": trace["trace_id"], "reason": reason,
                                       "raw_trace_preserved": str(traces_path.resolve())}) + "\n")

        def latest_traces():
            with traces_path.open("rb") as stream:
                for info in latest.values():
                    stream.seek(info["offset"])
                    raw = stream.read(info["length"])
                    if hashlib.sha256(raw).hexdigest() != info["raw_sha256"]:
                        raise ValueError("trace file changed during export")
                    yield validate_trace(json.loads(raw))

        for trace in latest_traces():
            trace_id = trace["trace_id"]
            if trace_id in seen:
                raise ValueError(f"duplicate trace: {trace_id}")
            seen.add(trace_id)
            task = tasks[trace["task_id"]]
            if expected_teacher is not None and trace["model"] != expected_teacher:
                raise ValueError(f"unexpected teacher: {trace['model']}")
            if trace["task_hash"] != canonical_hash(task):
                raise ValueError(f"task hash mismatch: {trace_id}")
            trace_student_prompt(task, trace)
            counts["source_traces"] += 1
            if trace.get("truncated") or trace.get("finish_reason") != "stop":
                counts["excluded_incomplete"] += 1
                exclude(trace, "incomplete_generation")
                continue
            if [item.get("role") for item in trace["messages"]] != [
                "system", "user", "assistant"
            ]:
                raise ValueError(f"unexpected message sequence: {trace_id}")
            assistant = trace["messages"][-1]
            try:
                reasoning, answer, normalization = normalize_channels(assistant, repair_qwen=repair_qwen)
            except ValueError:
                counts["excluded_malformed_boundary"] += 1
                exclude(trace, "ambiguous_thinking_boundary")
                continue
            if normalization == "qwen_duplicate_final_repair":
                counts["repaired_duplicate_final"] += 1
            if not reasoning.strip() or not answer.strip():
                counts["excluded_empty_target"] += 1
                exclude(trace, "missing_reasoning_or_final_answer")
                continue
            row = {
                "id": trace_id,
                "messages": [
                    {"role": "system", "content": trace["messages"][0]["content"]},
                    {"role": "user", "content": trace["messages"][1]["content"]},
                    {"role": "assistant", "content": f"<think>\n{reasoning}\n</think>\n{answer}"},
                ],
                "metadata": {
                    "dataset_policy": policy,
                    "source_trace_id": trace_id,
                    "task_hash": trace["task_hash"],
                    "teacher_model": trace["model"],
                    "training_target": "reasoning_and_answer",
                    "audit_disposition": "not_reviewed",
                    "channel_normalization": normalization,
                    "source": source_index.get(trace["task_hash"]),
                    "trace_provenance": trace.get("provenance"),
                    "run_fingerprint": (run_manifest or {}).get("fingerprint"),
                    "teacher_revision": (run_manifest or {}).get("generation", {}).get("teacher_revision"),
                    "reasoning_metrics": {
                        "reasoning_chars": len(reasoning), "answer_chars": len(answer),
                        "reported_reasoning_tokens": (trace.get("usage", {}).get("completion_tokens_details") or {}).get("reasoning_tokens"),
                        "reported_text_tokens": (trace.get("usage", {}).get("completion_tokens_details") or {}).get("text_tokens"),
                    },
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
            candidate = audit_candidate(task, trace, repair_qwen=repair_qwen)
            candidates.write(json.dumps(candidate, ensure_ascii=False) + "\n")
            digest.update(payload)
            counts["sft_rows"] += 1
            counts["answer_python_fences"] += bool(
                re.search(r"```\s*(?:python|py)\s*\n", answer, re.I)
            )
    report = {
        "policy": policy,
        "expected_teacher": expected_teacher,
        "repair_qwen": repair_qwen,
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
        "audit_candidates_sha256": file_sha256(out_dir / "audit-candidates.jsonl"),
        "raw_traces_sha256": file_sha256(traces_path),
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
