"""Build a Qwen chat-SFT corpus from the stopped v1 SciCode reasoning batch.

The selected corpus has one row per trace. Scientific audit errors are kept
only when explicitly requested; their provenance is never marked approved.
The input candidate and audit JSONL files are opened read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from .batch import controller_lock
from .scientific_audit import POLICY as AUDIT_POLICY

DATASET_POLICY = "scicode-v1-scientific-audit-sft-v1"
INCLUDED = {"model_supported_answer", "reasoning_candidate", "audit_error"}


class DatasetBuildError(ValueError):
    """An input or output cannot be safely used for SFT."""


def _jsonl(path: Path):
    with Path(path).open("rb") as source:
        for line_no, raw in enumerate(source, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DatasetBuildError(f"{path}:{line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise DatasetBuildError(f"{path}:{line_no}: expected object")
            yield raw, value


def _latest_audits(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for _raw, audit in _jsonl(path):
        trace_id = audit.get("trace_id")
        if audit.get("policy") != AUDIT_POLICY or not isinstance(trace_id, str):
            raise DatasetBuildError("audit policy or trace ID mismatch")
        previous = latest.get(trace_id)
        if previous is not None and previous.get("row_sha256") != audit.get("row_sha256"):
            raise DatasetBuildError(f"audit revisions refer to different source rows: {trace_id}")
        if previous is not None and "error" not in previous:
            raise DatasetBuildError(f"completed audit was reviewed a second time: {trace_id}")
        latest[trace_id] = audit
    return latest


def _training_row(row: dict, audit: dict) -> dict | None:
    disposition = audit.get("disposition") or ("audit_error" if "error" in audit else None)
    if disposition == "quarantine":
        return None
    if disposition not in INCLUDED:
        raise DatasetBuildError(f"unknown audit disposition for {row.get('trace_id')}")
    messages = row.get("messages")
    if not isinstance(messages, list) or [message.get("role") for message in messages] != [
        "system", "user", "assistant"
    ]:
        raise DatasetBuildError(f"invalid message sequence for {row.get('trace_id')}")
    if row.get("thinking_format") != "separate_reasoning_content":
        raise DatasetBuildError(f"unexpected thinking format for {row.get('trace_id')}")
    assistant = messages[2]
    reasoning = assistant.get("reasoning_content")
    answer = assistant.get("content")
    if not isinstance(reasoning, str) or not isinstance(answer, str):
        raise DatasetBuildError(f"invalid assistant content for {row.get('trace_id')}")
    train_reasoning = assistant.get("reasoning_loss") is True and bool(reasoning.strip())
    train_answer = (
        disposition != "reasoning_candidate"
        and assistant.get("content_loss") is True
        and bool(answer.strip())
        and not (row.get("termination") or {}).get("truncated")
    )
    if not (train_reasoning or train_answer):
        raise DatasetBuildError(f"no trainable target for {row.get('trace_id')}")
    if disposition == "model_supported_answer" and not train_answer:
        raise DatasetBuildError(f"approved answer is not trainable: {row.get('trace_id')}")
    content = ""
    if train_reasoning:
        content = f"<think>\n{reasoning}\n</think>\n"
    if train_answer:
        content += answer
    for message in messages[:2]:
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise DatasetBuildError(f"empty prompt for {row.get('trace_id')}")
    return {
        "id": row["trace_id"],
        "messages": [
            {"role": "system", "content": messages[0]["content"]},
            {"role": "user", "content": messages[1]["content"]},
            {"role": "assistant", "content": content},
        ],
        "metadata": {
            "dataset_policy": DATASET_POLICY,
            "audit_policy": AUDIT_POLICY,
            "audit_disposition": disposition,
            "scientific_correctness_proven": False,
            "training_target": (
                "reasoning_and_answer" if train_reasoning and train_answer
                else "reasoning_only" if train_reasoning else "answer_only"
            ),
            "archetype": row.get("archetype"),
            "task_hash": row.get("task_hash"),
            "termination": row.get("termination"),
        },
    }


def build_dataset(
    candidate_path: Path,
    audit_path: Path,
    output_dir: Path,
    *,
    expected_rows: int | None = None,
    expected_unresolved: int | None = None,
) -> dict:
    candidate_path, audit_path, output_dir = map(Path, (candidate_path, audit_path, output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "sft.jsonl"
    report_path = output_dir / "manifest.json"
    if any(path.exists() for path in (output, report_path, output.with_suffix(".jsonl.tmp"))):
        raise DatasetBuildError("refusing to overwrite existing SFT output")
    with controller_lock(output, exclusive=True):
        audits = _latest_audits(audit_path)
        seen: set[str] = set()
        counts: Counter[str] = Counter()
        digest = hashlib.sha256()
        temporary = output.with_suffix(".jsonl.tmp")
        try:
            with temporary.open("wb") as sink:
                for raw, candidate in _jsonl(candidate_path):
                    trace_id = candidate.get("trace_id")
                    if not isinstance(trace_id, str) or trace_id in seen:
                        raise DatasetBuildError(f"missing or duplicate trace ID: {trace_id}")
                    seen.add(trace_id)
                    audit = audits.get(trace_id)
                    if audit is None:
                        raise DatasetBuildError(f"missing audit: {trace_id}")
                    row_digest = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
                    if audit.get("row_sha256") != row_digest:
                        raise DatasetBuildError(f"audit does not match source row: {trace_id}")
                    disposition = audit.get("disposition") or (
                        "audit_error" if "error" in audit else None
                    )
                    counts["source_rows"] += 1
                    counts[f"audit_{disposition}"] += 1
                    train_row = _training_row(candidate, audit)
                    if train_row is None:
                        continue
                    payload = (json.dumps(train_row, ensure_ascii=False) + "\n").encode()
                    sink.write(payload)
                    digest.update(payload)
                    counts["sft_rows"] += 1
                    counts[f"target_{train_row['metadata']['training_target']}"] += 1
            if set(audits) != seen:
                raise DatasetBuildError("audit and candidate populations differ")
            if expected_rows is not None and counts["sft_rows"] != expected_rows:
                raise DatasetBuildError(
                    f"expected {expected_rows} SFT rows, got {counts['sft_rows']}"
                )
            if expected_unresolved is not None and counts["audit_audit_error"] != expected_unresolved:
                raise DatasetBuildError(
                    f"expected {expected_unresolved} audit errors, got {counts['audit_audit_error']}"
                )
            report = {
                "policy": DATASET_POLICY,
                "candidate": str(candidate_path.resolve()),
                "audit": str(audit_path.resolve()),
                "output": str(output.resolve()),
                "sha256": digest.hexdigest(),
                "counts": dict(counts),
                "scientific_correctness_proven": False,
            }
            temporary.replace(output)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(report_path, 0o644)
            return report
        finally:
            if temporary.exists():
                temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--expected-unresolved", type=int)
    args = parser.parse_args()
    print(json.dumps(build_dataset(
        args.candidate, args.audit, args.out_dir,
        expected_rows=args.expected_rows,
        expected_unresolved=args.expected_unresolved,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
