#!/usr/bin/env python3
"""Summarize an AvaCore SciCode JSONL trace without project dependencies."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping


FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n?(.*?)```", re.IGNORECASE | re.DOTALL)
IMPORT_LINE = re.compile(
    r"^\s*(?:import\s+.+|from\s+.+\s+import\s+.+)\s*(?:\r?\n|$)",
    re.MULTILINE,
)
PRIVATE_MARKER = re.compile(r"(?:target|oracle|private|ground[_ -]?truth|reference)", re.I)


class TraceDetailsError(ValueError):
    """Raised when a trace or problem file cannot be read safely."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise TraceDetailsError(f"JSONL file does not exist: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TraceDetailsError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise TraceDetailsError(f"JSONL row is not an object at {path}:{line_number}")
            rows.append(value)
    return rows


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                for key in ("text", "content", "value"):
                    if isinstance(item.get(key), str):
                        parts.append(item[key])
                        break
        return "".join(parts)
    if value is None:
        return ""
    return str(value)


def _messages(value: Any) -> list[dict[str, Any]]:
    """Normalize both AvaCore's list-of-messages and mapping wrappers."""
    if isinstance(value, Mapping):
        for key in ("messages", "trace"):
            if key in value:
                result = _messages(value[key])
                if result:
                    return result
        return []
    if isinstance(value, list):
        if value and all(isinstance(item, Mapping) for item in value):
            return [dict(item) for item in value]
        for item in value:
            result = _messages(item)
            if result:
                return result
    return []


def _clip(value: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(value) <= limit:
        return value, False
    return value[:limit], True


def _public_value(value: Any, depth: int = 0) -> Any:
    """Keep evaluator scalars while omitting likely private target values."""
    if depth > 4:
        return "<nested>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if PRIVATE_MARKER.search(name):
                continue
            result[name] = _public_value(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_public_value(item, depth + 1) for item in value[:100]]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _usage(rollout: Mapping[str, Any], assistant: Mapping[str, Any], index: int) -> dict[str, Any]:
    metadata = assistant.get("metadata")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("usage"), Mapping):
        return dict(metadata["usage"])
    trace = rollout.get("trace")
    trace_metadata = trace.get("metadata") if isinstance(trace, Mapping) else None
    usage = trace_metadata.get("usage") if isinstance(trace_metadata, Mapping) else None
    if isinstance(usage, Mapping):
        by_step = usage.get("by_step")
        if isinstance(by_step, list) and index < len(by_step):
            item = by_step[index]
            if isinstance(item, Mapping) and isinstance(item.get("usage"), Mapping):
                return dict(item["usage"])
        if index == 0 and any(key in usage for key in ("total_tokens", "prompt_tokens")):
            return dict(usage)
    return {}


def _extract_code(content: str) -> tuple[str, bool]:
    if not content:
        return "", False
    match = FENCE.search(content)
    fenced = match is not None
    code = match.group(1) if match else content
    if not fenced and not re.search(r"^\s*(?:def|class)\s+", code, re.MULTILINE):
        return "", False
    while True:
        updated = IMPORT_LINE.sub("", code, count=1)
        if updated == code:
            break
        code = updated
    return code.strip(), bool(code.strip())


def _syntax(code: str) -> tuple[bool | None, str | None]:
    if not code:
        return None, None
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return False, f"{exc.__class__.__name__}: {exc.msg} (line {exc.lineno})"
    return True, None


def _problem_metadata(problem_file: Path | None) -> dict[str, Any]:
    if problem_file is None:
        return {}
    rows = _read_jsonl(problem_file)
    return {str(row.get("problem_id", "")): row for row in rows}


def inspect_rollouts(
    rollouts: Path,
    *,
    problem_file: Path | None = None,
    max_text_chars: int = 6000,
    include_text: bool = True,
    candidate_id: str | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    rows = _read_jsonl(rollouts)
    problems = _problem_metadata(problem_file)
    details: list[dict[str, Any]] = []
    total_steps = 0
    complete_steps = 0
    all_warnings: list[dict[str, Any]] = []
    for row_number, rollout in enumerate(rows, start=1):
        instance = rollout.get("instance")
        instance = instance if isinstance(instance, Mapping) else {}
        problem_id = str(instance.get("problem_id", rollout.get("query_id", "")))
        problem = problems.get(problem_id, instance)
        steps = problem.get("sub_steps") if isinstance(problem, Mapping) else []
        steps = steps if isinstance(steps, list) else []
        raw_subtraces = rollout.get("subtraces")
        raw_subtraces = raw_subtraces if isinstance(raw_subtraces, list) else []
        step_details: list[dict[str, Any]] = []
        previous_codes: list[str] = []
        row_warnings: list[str] = []
        if len(raw_subtraces) != len(steps):
            row_warnings.append(
                f"subtrace count {len(raw_subtraces)} does not match step count {len(steps)}"
            )
        for index, raw_subtrace in enumerate(raw_subtraces):
            messages = _messages(raw_subtrace)
            users = [item for item in messages if item.get("role") == "user"]
            assistants = [item for item in messages if item.get("role") == "assistant"]
            assistant = assistants[-1] if assistants else {}
            user = users[0] if users else {}
            prompt = _text(user.get("content"))
            content = _text(assistant.get("content"))
            reasoning = _text(
                assistant.get("reasoning_content") or assistant.get("reasoning")
            )
            metadata = assistant.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            usage = _usage(rollout, assistant, index)
            finish_reason = metadata.get("finish_reason")
            code, code_found = _extract_code(content)
            syntax_ok, syntax_error = _syntax(code)
            step = steps[index] if index < len(steps) and isinstance(steps[index], Mapping) else {}
            warnings: list[str] = []
            if not users:
                warnings.append("missing user message")
            if not assistants:
                warnings.append("missing assistant message")
            if not content and not reasoning:
                warnings.append("missing ordinary and reasoning content")
            if not reasoning:
                warnings.append("missing reasoning content")
            if not usage:
                warnings.append("missing token usage")
            if finish_reason is None:
                warnings.append("missing finish_reason")
            for warning in warnings:
                all_warnings.append(
                    {"row": row_number, "step_index": index, "problem_id": problem_id, "warning": warning}
                )
            content_out, content_truncated = _clip(content, max_text_chars)
            reasoning_out, reasoning_truncated = _clip(reasoning, max_text_chars)
            prompt_out, prompt_truncated = _clip(prompt, max_text_chars)
            code_out, code_truncated = _clip(code, max_text_chars)
            item: dict[str, Any] = {
                "step_index": index,
                "step_number": str(step.get("step_number", f"{problem_id}.{index + 1}")),
                "message_count": len(messages),
                "finish_reason": finish_reason,
                "provider_model": metadata.get("model") or rollout.get("model"),
                "usage": usage,
                "code_extracted": code_found,
                "code_chars": len(code),
                "syntax_ok": syntax_ok,
                "syntax_error": syntax_error,
                "previous_code_count": len(previous_codes),
                "previous_code_chars": sum(len(value) for value in previous_codes),
                "warnings": warnings,
            }
            if include_text:
                item.update(
                    {
                        "prompt": prompt_out,
                        "prompt_chars": len(prompt),
                        "prompt_truncated": prompt_truncated,
                        "content": content_out,
                        "content_chars": len(content),
                        "content_truncated": content_truncated,
                        "reasoning_content": reasoning_out,
                        "reasoning_chars": len(reasoning),
                        "reasoning_truncated": reasoning_truncated,
                        "parsed_code": code_out,
                        "code_truncated": code_truncated,
                    }
                )
            step_details.append(item)
            total_steps += 1
            if not warnings:
                complete_steps += 1
            if code_found:
                previous_codes.append(code)
        reward = rollout.get("reward")
        details.append(
            {
                "row": row_number,
                "run_id": rollout.get("run_id"),
                "problem_id": problem_id,
                "trial_id": rollout.get("trial_id"),
                "model": rollout.get("model"),
                "error": rollout.get("error"),
                "evaluator": _public_value(reward) if isinstance(reward, Mapping) else reward,
                "step_count": len(step_details),
                "warnings": row_warnings,
                "steps": step_details,
            }
        )
    digest = hashlib.sha256(rollouts.read_bytes()).hexdigest()
    return {
        "schema": "scicode-trace-details-v1",
        "status": "ok" if not all_warnings else "warnings",
        "candidate_id": candidate_id,
        "revision": revision,
        "rollouts": details,
        "summary": {
            "rollout_rows": len(rows),
            "total_steps": total_steps,
            "complete_steps": complete_steps,
            "warning_count": len(all_warnings),
        },
        "warnings": all_warnings,
        "rollouts_sha256": digest,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollouts", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--problem-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-text-chars", type=int, default=6000)
    parser.add_argument("--no-text", action="store_true")
    args = parser.parse_args(argv)
    problem_file = args.problem_file
    candidate_id = revision = None
    if args.candidate_dir:
        root = args.candidate_dir.expanduser().resolve()
        problem_file = problem_file or root / "public" / "problem.jsonl"
        manifest_path = root / "candidate_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
            if isinstance(manifest, Mapping):
                candidate_id = str(manifest.get("candidate_id", "")) or None
                revision = str(manifest.get("revision", "")) or None
    try:
        result = inspect_rollouts(
            args.rollouts.expanduser().resolve(),
            problem_file=problem_file.expanduser().resolve() if problem_file else None,
            max_text_chars=args.max_text_chars,
            include_text=not args.no_text,
            candidate_id=candidate_id,
            revision=revision,
        )
    except (OSError, TraceDetailsError) as exc:
        print(json.dumps({"schema": "scicode-trace-details-v1", "status": "failed", "error": str(exc)}))
        return 2
    if args.output:
        _write_json(args.output.expanduser().resolve(), result)
        print(json.dumps({"schema": result["schema"], "status": result["status"], "output": str(args.output.expanduser().resolve()), "summary": result["summary"]}, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
