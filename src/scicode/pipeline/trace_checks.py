"""Deterministic completeness checks for exported AvaCore rollouts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .candidate import CandidateManifest, read_jsonl


def _messages(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping) and isinstance(value.get("messages"), list):
        return [item for item in value["messages"] if isinstance(item, Mapping)]
    return []


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in value
            if isinstance(item, (str, Mapping))
        )
    return "" if value is None else str(value)


def _active_steps(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    problem_id = str(row.get("problem_id", ""))
    skipped = {("13", 5), ("62", 0), ("76", 2)}
    return [
        step
        for index, step in enumerate(row.get("sub_steps", []))
        if isinstance(step, Mapping) and (problem_id, index) not in skipped
    ]


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def audit_rollouts(
    candidate: CandidateManifest,
    rollouts: str | Path,
    *,
    require_usage: bool = False,
    require_reasoning: bool = False,
) -> dict[str, Any]:
    """Check exported trace structure without reading private oracle files."""

    rows = read_jsonl(rollouts)
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    expected = candidate.problem_count
    seen_problems: set[str] = set()
    complete_subproblems = 0
    for row_number, rollout in enumerate(rows, start=1):
        instance = rollout.get("instance")
        if not isinstance(instance, Mapping):
            failures.append({"row": row_number, "error": "missing instance"})
            continue
        problem_id = str(instance.get("problem_id", rollout.get("query_id", "")))
        seen_problems.add(problem_id)
        steps = _active_steps(instance)
        subtraces = rollout.get("subtraces")
        if not isinstance(subtraces, list) or len(subtraces) != len(steps):
            failures.append(
                {
                    "row": row_number,
                    "problem_id": problem_id,
                    "error": "subproblem trace count does not match candidate",
                    "expected": len(steps),
                    "actual": len(subtraces) if isinstance(subtraces, list) else None,
                }
            )
            continue
        if rollout.get("error"):
            failures.append(
                {"row": row_number, "problem_id": problem_id, "error": "rollout has error"}
            )
            continue
        for step, raw_trace in zip(steps, subtraces):
            messages = _messages(raw_trace)
            assistants = [item for item in messages if item.get("role") == "assistant"]
            users = [item for item in messages if item.get("role") == "user"]
            assistant = assistants[-1] if assistants else None
            if not users or assistant is None:
                failures.append(
                    {
                        "row": row_number,
                        "problem_id": problem_id,
                        "step_number": step.get("step_number"),
                        "error": "trace lacks user and assistant messages",
                    }
                )
                continue
            content = _text(assistant.get("content"))
            reasoning = _text(
                assistant.get("reasoning_content") or assistant.get("reasoning")
            )
            if not content and not reasoning:
                failures.append(
                    {
                        "row": row_number,
                        "problem_id": problem_id,
                        "step_number": step.get("step_number"),
                        "error": "assistant has no ordinary or reasoning content",
                    }
                )
                continue
            metadata = assistant.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if metadata.get("finish_reason") is None:
                warnings.append(
                    {
                        "row": row_number,
                        "problem_id": problem_id,
                        "step_number": step.get("step_number"),
                        "warning": "finish_reason is absent",
                    }
                )
            usage = metadata.get("usage")
            usage_warning = {
                "row": row_number,
                "problem_id": problem_id,
                "step_number": step.get("step_number"),
                "warning": "token usage is absent",
            }
            if not isinstance(usage, Mapping) or not usage:
                if require_usage:
                    failures.append({**usage_warning, "error": usage_warning["warning"]})
                else:
                    warnings.append(usage_warning)
            if require_reasoning and not reasoning:
                failures.append(
                    {
                        "row": row_number,
                        "problem_id": problem_id,
                        "step_number": step.get("step_number"),
                        "error": "reasoning content is absent",
                    }
                )
            complete_subproblems += 1
    if len(rows) != expected:
        failures.append(
            {
                "error": "problem rollout count does not match candidate",
                "expected": expected,
                "actual": len(rows),
            }
        )
    expected_ids = {str(row["problem_id"]) for row in read_jsonl(candidate.problem_file)}
    missing_ids = sorted(expected_ids - seen_problems)
    if missing_ids:
        failures.append(
            {"error": "candidate problems missing from export", "problem_ids": missing_ids}
        )
    return {
        "schema": "scicode-trace-audit-v1",
        "status": "ok" if not failures else "failed",
        "candidate_id": candidate.candidate_id,
        "revision": candidate.revision,
        "rollout_rows": len(rows),
        "expected_problem_rows": expected,
        "complete_subproblems": complete_subproblems,
        "failures": failures,
        "warnings": warnings,
        "rollouts_sha256": _hash(rows),
    }


__all__ = ["audit_rollouts"]
