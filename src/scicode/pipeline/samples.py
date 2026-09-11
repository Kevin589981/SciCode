"""Expand AvaCore SciCode rollouts into deterministic subproblem samples."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .candidate import CandidateManifest, load_candidate


class SampleExportError(ValueError):
    """Raised when a rollout cannot become an auditable subproblem sample."""


SKIPPED_SUBSTEPS = frozenset({("13", 5), ("62", 0), ("76", 2)})
_FENCE = re.compile(
    r"```(?:python|py)?[ \t]*\r?\n?(.*?)```", re.IGNORECASE | re.DOTALL
)
_THINK = re.compile(r"<think>\s*(.*?)\s*</think>\s*", re.IGNORECASE | re.DOTALL)
_IMPORT_LINE = re.compile(
    r"^\s*(?:import\s+.+|from\s+.+\s+import\s+.+)\s*(?:\r?\n|$)",
    re.MULTILINE,
)


def _json_hash(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize_content(value: Any) -> str:
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


def extract_code(content: str) -> str:
    """Mirror SciCode's code extraction without importing provider libraries."""
    if not content:
        return ""
    match = _FENCE.search(content)
    code = match.group(1) if match else content
    # SciCode supplies dependencies separately; preserve the same stripped
    # code representation used by the SFT generator.
    while True:
        updated = _IMPORT_LINE.sub("", code, count=1)
        if updated == code:
            break
        code = updated
    return code.strip()


def _message_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        messages = value.get("messages")
        if isinstance(messages, list):
            return [dict(item) for item in messages if isinstance(item, Mapping)]
        return []
    if isinstance(value, list):
        # Postgres exports each AvaCore subtrace as a JSON array of messages.
        if value and all(isinstance(item, Mapping) for item in value):
            return [dict(item) for item in value]
        if len(value) == 1:
            return _message_list(value[0])
    return []


def _step_usage(record: Mapping[str, Any], index: int) -> dict[str, Any]:
    trace = record.get("trace")
    metadata = trace.get("metadata") if isinstance(trace, Mapping) else None
    usage = metadata.get("usage") if isinstance(metadata, Mapping) else None
    if isinstance(usage, Mapping):
        by_step = usage.get("by_step")
        if isinstance(by_step, list) and index < len(by_step):
            item = by_step[index]
            if isinstance(item, Mapping) and isinstance(item.get("usage"), Mapping):
                return dict(item["usage"])
        if index == 0 and any(key in usage for key in ("total_tokens", "prompt_tokens")):
            return dict(usage)
    return {}


def _active_steps(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    problem_id = str(row.get("problem_id", ""))
    steps = row.get("sub_steps")
    if not isinstance(steps, list):
        raise SampleExportError(f"rollout instance has no sub_steps: {problem_id}")
    return [
        dict(step)
        for index, step in enumerate(steps)
        if isinstance(step, Mapping) and (problem_id, index) not in SKIPPED_SUBSTEPS
    ]


@dataclass(frozen=True)
class ExpandedSample:
    record: dict[str, Any]
    sample_key: str


def _expand_rollout(
    rollout: Mapping[str, Any],
    manifest: CandidateManifest,
) -> list[ExpandedSample]:
    instance = rollout.get("instance")
    if not isinstance(instance, Mapping):
        # Accept a previously normalized one-sample record for migrations and
        # dry runs, while still requiring all essential SFT fields.
        if isinstance(rollout.get("messages"), list) and rollout.get("metadata"):
            candidate_id = manifest.candidate_id
            revision = manifest.revision
            problem_id = str(rollout["metadata"].get("problem_id", rollout.get("query_id", "")))
            step_number = str(rollout["metadata"].get("step_number", ""))
            trace_hash = _json_hash(rollout.get("trace", rollout))
            key = f"{candidate_id}:{revision}:{problem_id}:{step_number}:{trace_hash}"
            return [ExpandedSample(dict(rollout), key)]
        raise SampleExportError("AvaCore rollout has no instance object")
    row = dict(instance)
    problem_id = str(row.get("problem_id", rollout.get("query_id", "")))
    steps = _active_steps(row)
    raw_subtraces = rollout.get("subtraces")
    if raw_subtraces is None:
        raw_subtraces = []
    if not isinstance(raw_subtraces, list):
        raise SampleExportError(f"subtraces is not a list for {problem_id}")
    if len(raw_subtraces) != len(steps):
        raise SampleExportError(
            f"incomplete rollout for {problem_id}: {len(raw_subtraces)} traces for {len(steps)} steps"
        )
    rewards = rollout.get("reward") if isinstance(rollout.get("reward"), Mapping) else {}
    samples: list[ExpandedSample] = []
    previous_codes: list[str] = []
    for index, (step, raw_subtrace) in enumerate(zip(steps, raw_subtraces)):
        messages = _message_list(raw_subtrace)
        user_message = next((item for item in messages if item.get("role") == "user"), None)
        assistant_message = next(
            (item for item in reversed(messages) if item.get("role") == "assistant"),
            None,
        )
        if user_message is None or assistant_message is None:
            raise SampleExportError(
                f"subproblem trace lacks user/assistant messages: {problem_id}/{step.get('step_number')}"
            )
        prompt = _normalize_content(user_message.get("content"))
        content = _normalize_content(assistant_message.get("content"))
        reasoning = _normalize_content(
            assistant_message.get("reasoning_content")
            or assistant_message.get("reasoning")
        )
        if not reasoning:
            thinking = _THINK.search(content)
            if thinking:
                reasoning = thinking.group(1).strip()
                content = content[thinking.end() :]
        if not content and not reasoning:
            raise SampleExportError(
                f"subproblem trace has no model output: {problem_id}/{step.get('step_number')}"
            )
        parsed = extract_code(content)
        assistant_metadata = assistant_message.get("metadata")
        if not isinstance(assistant_metadata, Mapping):
            assistant_metadata = {}
        usage = dict(assistant_metadata.get("usage", {})) if isinstance(assistant_metadata.get("usage"), Mapping) else _step_usage(rollout, index)
        finish_reason = assistant_metadata.get("finish_reason")
        completion = content or reasoning
        completion_with_reasoning = (
            f"<think>\n{reasoning}\n</think>\n\n{content}"
            if reasoning
            else completion
        )
        trace_hash = _json_hash(raw_subtrace)
        step_number = str(step.get("step_number", f"{problem_id}.{index + 1}"))
        sample_id = (
            f"{manifest.candidate_id}/{manifest.revision}/"
            f"{problem_id}/{step_number}/{trace_hash[:16]}"
        )
        trace_status = "length" if finish_reason == "length" else "complete"
        metadata = {
            "schema_version": "scicode-sft-v2-avacore",
            "sample_unit": "subproblem_trace",
            "candidate_id": manifest.candidate_id,
            "task_revision": manifest.revision,
            "problem_id": problem_id,
            "problem_name": row.get("problem_name"),
            "step_index": index,
            "step_number": step_number,
            "mode": "strict",
            "prompt_profile": manifest.prompt_profile,
            "prompt_protocol": "SciCode official sequential prompt",
            "run_id": rollout.get("run_id"),
            "query_id": rollout.get("query_id", problem_id),
            "trial_id": rollout.get("trial_id", 0),
            "model": rollout.get("model"),
            "provider": "avacore",
            "finish_reason": finish_reason,
            "status": "length" if finish_reason == "length" else "ok",
            "trace_status": trace_status,
            "trace_hash": trace_hash,
            "code_extracted": bool(parsed),
            "content_chars": len(content),
            "reasoning_chars": len(reasoning),
            "prompt_chars": len(prompt),
            "candidate_record_sha256": manifest.canonical_record_sha256,
            "oracle_sha256": manifest.oracle_sha256,
            "source_provenance_sha256": manifest.provenance_sha256,
            "source_url": manifest.source_url,
            "source_commit": manifest.source_commit,
            "license": manifest.license,
            "paper_url": manifest.paper_url,
            "paper_doi": manifest.paper_doi,
            "source_fingerprint": manifest.source_fingerprint,
            "usage_available": bool(usage),
            "problem_reward": rewards,
        }
        record = {
            "id": sample_id,
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": content},
            ],
            "prompt": prompt,
            "completion": completion,
            "completion_with_reasoning": completion_with_reasoning,
            "reasoning_content": reasoning,
            "parsed_code": parsed,
            "context_code": "\n\n".join(previous_codes),
            "provider_response": rollout.get("provider_response")
            or assistant_metadata.get("provider_response")
            or {"assistant_message": assistant_message},
            "usage": usage,
            "trace": raw_subtrace,
            "metadata": metadata,
        }
        raw_chunks = rollout.get("provider_raw_chunks")
        if raw_chunks is not None:
            record["provider_raw_chunks"] = raw_chunks
        key = f"{manifest.candidate_id}:{manifest.revision}:{problem_id}:{step_number}:{trace_hash}"
        samples.append(ExpandedSample(record, key))
        if parsed:
            previous_codes.append(parsed)
    return samples


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SampleExportError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise SampleExportError(f"JSONL row is not an object at {path}:{line_number}")
            rows.append(value)
    return rows


def _write_jsonl_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    digest = hashlib.sha256()
    with temporary.open("wb") as stream:
        for row in rows:
            raw = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            stream.write(raw)
            digest.update(raw)
    os.replace(temporary, path)
    return digest.hexdigest()


def export_subproblem_samples(
    candidate_dir: str | Path,
    rollouts: str | Path,
    registry: str | Path,
    output: str | Path,
    *,
    target_count: int = 10_000,
    summary: str | Path | None = None,
) -> dict[str, Any]:
    """Expand, deduplicate, and atomically write the SFT-compatible dataset."""
    if target_count <= 0:
        raise SampleExportError("target_count must be positive")
    manifest = load_candidate(candidate_dir)
    rollout_rows = _read_jsonl(Path(rollouts))
    expanded: list[ExpandedSample] = []
    errors: list[dict[str, str]] = []
    for row_number, rollout in enumerate(rollout_rows, start=1):
        try:
            expanded.extend(_expand_rollout(rollout, manifest))
        except SampleExportError as exc:
            errors.append({"row": str(row_number), "error": str(exc)})
    registry_path = Path(registry)
    existing_registry = _read_jsonl(registry_path)
    seen = {
        str(row.get("sample_key"))
        for row in existing_registry
        if row.get("sample_key")
    }
    existing_fingerprints = {
        str(row.get("source_fingerprint"))
        for row in existing_registry
        if row.get("source_fingerprint")
    }
    if manifest.source_fingerprint in existing_fingerprints and not any(
        row.get("candidate_id") == manifest.candidate_id for row in existing_registry
    ):
        raise SampleExportError(
            "source fragment fingerprint already belongs to another candidate"
        )
    output_path = Path(output)
    existing_output = _read_jsonl(output_path)
    accepted: list[dict[str, Any]] = []
    accepted_keys: set[str] = set()
    for row in existing_output:
        key = row.get("metadata", {}).get("sample_key") if isinstance(row.get("metadata"), Mapping) else None
        if key is None:
            key = row.get("id")
        if key not in accepted_keys and len(accepted) < target_count:
            accepted.append(row)
            accepted_keys.add(str(key))
    overflow: list[dict[str, Any]] = []
    new_registry: list[dict[str, Any]] = list(existing_registry)
    new_samples = 0
    for item in expanded:
        if item.sample_key in seen or item.sample_key in accepted_keys:
            continue
        item.record["metadata"]["sample_key"] = item.sample_key
        if len(accepted) < target_count:
            accepted.append(item.record)
            accepted_keys.add(item.sample_key)
            new_registry.append(
                {
                    "sample_key": item.sample_key,
                    "sample_id": item.record["id"],
                    "candidate_id": manifest.candidate_id,
                    "revision": manifest.revision,
                    "problem_id": item.record["metadata"]["problem_id"],
                    "step_number": item.record["metadata"]["step_number"],
                    "trace_hash": item.record["metadata"]["trace_hash"],
                    "source_fingerprint": manifest.source_fingerprint,
                }
            )
            seen.add(item.sample_key)
            new_samples += 1
        else:
            overflow.append(item.record)
    output_hash = _write_jsonl_atomic(output_path, accepted)
    registry_hash = _write_jsonl_atomic(registry_path, new_registry)
    overflow_path = output_path.with_suffix(output_path.suffix + ".overflow.jsonl")
    if overflow:
        _write_jsonl_atomic(overflow_path, overflow)
    elif overflow_path.exists():
        overflow_path.unlink()
    result = {
        "schema": "scicode-sft-delivery-v2",
        "status": "target_reached" if len(accepted) >= target_count else "under_target",
        "candidate_id": manifest.candidate_id,
        "revision": manifest.revision,
        "rollout_rows": len(rollout_rows),
        "expanded_samples": len(expanded),
        "new_samples": new_samples,
        "accepted_samples": len(accepted),
        "target_count": target_count,
        "overflow_samples": len(overflow),
        "invalid_rollouts": errors,
        "output": str(output_path.resolve()),
        "output_sha256": output_hash,
        "registry": str(registry_path.resolve()),
        "registry_sha256": registry_hash,
        "overflow": str(overflow_path.resolve()) if overflow else None,
    }
    if summary:
        summary_path = Path(summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
