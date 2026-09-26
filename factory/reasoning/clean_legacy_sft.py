"""Copy and conservatively clean a stopped v1 reasoning batch.

The source queue and repository shards are opened read-only.  This is deliberately
separate from the production exporter: the old policy and its student view must
remain auditable, while a second review can make different loss decisions.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import copy
import hashlib
import json
import sqlite3
import sys
import threading
from collections import Counter
from pathlib import Path

from ..author import llm
from .author import _json_objects
from .schema import canonical_hash, validate_task

POLICY = "legacy-scientific-sft-clean-v1"
REVIEW_POLICY = "adversarial-scientific-answer-review-v2"
REVIEW_FILE = f"semantic-reviews-{REVIEW_POLICY}.jsonl"


class CleaningError(RuntimeError):
    """An input or review is unsafe to silently accept."""


def _line(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _read_jsonl(path: Path):
    with path.open("rb") as stream:
        for number, raw in enumerate(stream, 1):
            if raw.strip():
                yield number, raw, json.loads(raw)


def _source_results(db: Path):
    uri = f"file:{db.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        for (raw,) in connection.execute(
            "SELECT result_json FROM jobs WHERE status='done' "
            "AND result_json IS NOT NULL ORDER BY job_id"
        ):
            result = json.loads(raw)
            if result.get("status") == "complete":
                yield result
    finally:
        connection.close()


def _within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _precheck(row: dict, task: dict | None) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    repairs: list[str] = []
    if row.get("schema_version") != "scicode-reasoning-sft-v1":
        reasons.append("invalid_schema_version")
    if task is None:
        reasons.append("missing_source_task")
    else:
        try:
            validate_task(task)
            if row.get("task_hash") != canonical_hash(task):
                reasons.append("task_hash_mismatch")
            if row.get("task_name") != task.get("task_id"):
                reasons.append("task_id_mismatch")
            if row.get("archetype") != task.get("archetype"):
                reasons.append("archetype_mismatch")
        except Exception:
            reasons.append("invalid_source_task")
    messages = row.get("messages")
    if not isinstance(messages, list) or [m.get("role") for m in messages if isinstance(m, dict)] != [
        "system", "user", "assistant"
    ]:
        reasons.append("invalid_message_sequence")
        return reasons, repairs
    user = messages[1].get("content")
    assistant = messages[2]
    if not isinstance(user, str) or not user.strip():
        reasons.append("empty_student_prompt")
    if task is not None and isinstance(user, str):
        required = [
            task.get("problem", {}).get("question"),
            task.get("problem", {}).get("background"),
            *task.get("deliverable", {}).get("requirements", []),
        ]
        if any(not isinstance(item, str) or item not in user for item in required):
            reasons.append("student_prompt_omits_required_text")
    reasoning = assistant.get("reasoning_content")
    answer = assistant.get("content")
    if not isinstance(reasoning, str) or not reasoning.strip():
        reasons.append("empty_reasoning")
    if not isinstance(answer, str):
        reasons.append("invalid_answer_content")
    if not isinstance(assistant.get("reasoning_loss"), bool) or not isinstance(
        assistant.get("content_loss"), bool
    ):
        reasons.append("invalid_loss_flags")
    if not isinstance(row.get("trace_id"), str) or not row["trace_id"]:
        reasons.append("invalid_trace_id")
    termination = row.get("termination") or {}
    if not isinstance(termination, dict):
        reasons.append("invalid_termination")
        termination = {}
    interrupted = termination.get("finish_reason") in {"length", "stream_interrupted"}
    if interrupted and not termination.get("truncated"):
        repairs.append("mark_truncated")
    if isinstance(answer, str) and not answer.strip() and assistant.get("content_loss"):
        repairs.append("disable_empty_answer_loss")
    elif interrupted and assistant.get("content_loss"):
        repairs.append("disable_partial_answer_loss")
    if assistant.get("loss") != bool(
        assistant.get("reasoning_loss") or assistant.get("content_loss")
    ):
        repairs.append("recompute_combined_loss")
    return reasons, repairs


def prepare(db: Path, source_root: Path, output: Path) -> dict:
    if not db.is_file() or not source_root.is_dir():
        raise CleaningError("source queue or root is missing")
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "original-copy.jsonl"
    index_path = output / "index.jsonl"
    for path in (raw_path, index_path, raw_path.with_suffix(".tmp"), index_path.with_suffix(".tmp")):
        if path.exists():
            raise CleaningError(f"refusing to overwrite existing inventory: {path}")
    counts = Counter()
    seen: set[str] = set()
    raw_sha = hashlib.sha256()
    with raw_path.with_suffix(".tmp").open("wb") as raw_out, index_path.with_suffix(
        ".tmp"
    ).open("wb") as index_out:
        for result in _source_results(db):
            counts["complete_repositories"] += 1
            sft_path = Path(result["sft"])
            tasks_path = Path((result.get("artifacts") or {}).get("tasks") or "")
            if not _within(sft_path, source_root) or not _within(tasks_path, source_root):
                raise CleaningError("a shard escapes the declared source root")
            if not sft_path.is_file() or not tasks_path.is_file():
                raise CleaningError(f"completed shard is missing: {sft_path}")
            tasks = {row["task_id"]: row for _, _, row in _read_jsonl(tasks_path)}
            shard_rows = 0
            for line_number, original, row in _read_jsonl(sft_path):
                shard_rows += 1
                trace_id = row.get("trace_id")
                reasons, repairs = _precheck(row, tasks.get(row.get("task_name")))
                if isinstance(trace_id, str):
                    if trace_id in seen:
                        reasons.append("duplicate_trace_id")
                    seen.add(trace_id)
                normalized = original.rstrip(b"\r\n") + b"\n"
                offset = raw_out.tell()
                raw_out.write(normalized)
                raw_sha.update(normalized)
                task = tasks.get(row.get("task_name")) or {}
                record = {
                    "trace_id": trace_id,
                    "task_name": row.get("task_name"),
                    "offset": offset,
                    "bytes": len(normalized),
                    "raw_sha256": hashlib.sha256(normalized).hexdigest(),
                    "source_file": str(sft_path),
                    "source_line": line_number,
                    "precheck_reasons": reasons,
                    "repairs": repairs,
                    "task_aux": {
                        "archetype_payload": task.get("archetype_payload"),
                        "reasoning_contract": task.get("reasoning_contract"),
                    },
                }
                index_out.write(_line(record))
                counts["rows"] += 1
                counts["precheck_rejected"] += bool(reasons)
                counts["repairable"] += bool(repairs)
            if shard_rows != result.get("sft_rows"):
                raise CleaningError(f"shard count differs from queue result: {sft_path}")
    raw_path.with_suffix(".tmp").replace(raw_path)
    index_path.with_suffix(".tmp").replace(index_path)
    manifest = {
        "policy": POLICY,
        "source_queue": str(db.resolve()),
        "source_root": str(source_root.resolve()),
        "original_copy": str(raw_path.resolve()),
        "original_copy_sha256": raw_sha.hexdigest(),
        "counts": dict(counts),
    }
    (output / "inventory-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _raw_row(raw_path: Path, record: dict) -> dict:
    with raw_path.open("rb") as stream:
        stream.seek(record["offset"])
        raw = stream.read(record["bytes"])
    if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["raw_sha256"]:
        raise CleaningError(f"raw-copy index mismatch for {record['trace_id']}")
    return json.loads(raw)


def _review_prompt(row: dict, record: dict) -> str:
    messages = row["messages"]
    answer = messages[2].get("content") or ""
    reasoning = messages[2].get("reasoning_content") or ""
    limit = 6000 if answer else 90000
    if len(reasoning) <= limit * 2:
        thinking_excerpt = reasoning
        excerpt_omits_middle = False
    else:
        thinking_excerpt = reasoning[:limit] + "\n[... middle omitted FROM REVIEW only ...]\n" + reasoning[-limit:]
        excerpt_omits_middle = True
    view = {
        "student_prompt": messages[1]["content"],
        "private_structured_author_fields_for_missing_input_check_only": record["task_aux"],
        "answer": answer,
        "reasoning_excerpt": thinking_excerpt,
        "reasoning_excerpt_omits_middle": excerpt_omits_middle,
        "finish_reason": row["termination"]["finish_reason"],
    }
    return f"""You are an ADVERSARIAL scientific SFT auditor. Recheck this sample independently.
The earlier Kimi score is NOT evidence. Find concrete counterexamples, missing
premises, false scientific claims, violated deliverables, and code/derivation
errors. Judge the STUDENT PROMPT alone for answerability: private author fields
were not shown to the student. A truncated but useful reasoning trajectory may
still train reasoning; an incorrect or unfinished final answer must not train
content. Do not reject merely because a trace is long or unfinished.

First extract EVERY hard requirement from the student prompt and ask whether the
answer meets it over its stated domain. In particular, an exact universal
requirement is violated by ONE legitimate counterexample; a caveat that admits
the violation does NOT make the requirement satisfied. If two hard requirements
are mutually inconsistent, task_status is flawed, even if the answer discusses
that inconsistency. If a solution chooses a convention that weakens an explicit
requirement, answer_status is exclude. Do not silently add new assumptions.

Then construct at least ONE NEW adversarial quantitative, logical, or edge-case
test that is not copied from the proposed answer's own examples. Checking only
the answer's examples is insufficient. Check units, limits, degeneracy, and
whether code implements the stated mathematics. Do not pretend to run code.
Quote question/answer text or provide concrete input and expected versus actual
outputs for any major flaw. Be skeptical but do not invent objections. If a
decisive claim cannot be checked, say uncertain rather than fabricating proof.
The reasoning excerpt may omit its middle FOR REVIEW EFFICIENCY; this does not
mean the original trace was interrupted. Use finish_reason for termination.

SAMPLE:
{json.dumps(view, ensure_ascii=False)}

Return ONLY one complete JSON object with:
{{"task_status":"sound|flawed|uncertain",
  "reasoning_status":"train|exclude|uncertain",
  "answer_status":"train|exclude|uncertain",
  "requirement_checks":[{{"requirement":"quoted hard requirement", "status":"met|violated|uncertain", "evidence":"specific check"}}],
  "novel_counterexample":"new test input and expected versus actual result, or why no counterexample survives",
  "checks":["at least one specific independently checked claim"],
  "issues":[{{"severity":"major|minor", "evidence":"exact short quote or explicit counterexample", "explanation":"why it matters"}}],
  "summary":"brief rationale"}}
If the answer is empty, answer_status must be exclude. If the student prompt
contradicts itself or omits indispensable given data, task_status must be flawed.
"""


def _parse_review(response: dict, *, answer: str) -> dict:
    message = response["choices"][0]["message"]
    objects = []
    for field in ("content", "reasoning_content"):
        objects.extend(_json_objects(message.get(field) or ""))
    value = next(
        (
            item
            for item in objects
            if item.get("task_status") in {"sound", "flawed", "uncertain"}
            and item.get("reasoning_status") in {"train", "exclude", "uncertain"}
            and item.get("answer_status") in {"train", "exclude", "uncertain"}
        ),
        None,
    )
    if value is None:
        raise CleaningError("reviewer returned no complete verdict object")
    if not isinstance(value.get("checks"), list) or not value["checks"]:
        raise CleaningError("reviewer omitted independent checks")
    if not isinstance(value.get("requirement_checks"), list) or not value["requirement_checks"]:
        raise CleaningError("reviewer omitted requirement audit")
    if not isinstance(value.get("novel_counterexample"), str) or not value["novel_counterexample"].strip():
        raise CleaningError("reviewer omitted adversarial test")
    if not isinstance(value.get("issues"), list) or not isinstance(value.get("summary"), str):
        raise CleaningError("reviewer issues or summary is invalid")
    if not answer and value["answer_status"] != "exclude":
        raise CleaningError("reviewer marked an empty answer trainable")
    return value


def _review_one(record: dict, raw_path: Path, model: str, max_tokens: int, timeout: int) -> dict:
    row = _raw_row(raw_path, record)
    answer = row["messages"][2].get("content") or ""
    prompt = _review_prompt(row, record)
    last_error = None
    for budget in (max_tokens, max_tokens * 2):
        try:
            response = llm.chat(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=0.0,
                max_tokens=budget,
                timeout=timeout,
                retries=2,
            )
            verdict = _parse_review(response, answer=answer)
            return {
                "policy": REVIEW_POLICY,
                "trace_id": record["trace_id"],
                "raw_sha256": record["raw_sha256"],
                "model": model,
                "verdict": verdict,
                "usage": response.get("usage") or {},
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:500]
    return {
        "policy": REVIEW_POLICY,
        "trace_id": record["trace_id"],
        "raw_sha256": record["raw_sha256"],
        "model": model,
        "error": last_error,
    }


def review(output: Path, *, model: str, workers: int, max_tokens: int, timeout: int,
           limit: int | None = None, trace_id: str | None = None) -> dict:
    raw_path = output / "original-copy.jsonl"
    index_path = output / "index.jsonl"
    review_path = output / REVIEW_FILE
    if not raw_path.is_file() or not index_path.is_file():
        raise CleaningError("run prepare first")
    existing = {}
    if review_path.exists():
        for _, _, item in _read_jsonl(review_path):
            if item.get("policy") != REVIEW_POLICY or item.get("model") != model:
                raise CleaningError("existing reviews use another policy or model")
            existing[item["trace_id"]] = item
    records = []
    for _, _, record in _read_jsonl(index_path):
        if trace_id is not None and record["trace_id"] != trace_id:
            continue
        if record["precheck_reasons"]:
            continue
        previous = existing.get(record["trace_id"])
        if previous is not None and previous.get("raw_sha256") != record["raw_sha256"]:
            raise CleaningError("existing review points to a different raw row")
        if previous is not None and "verdict" in previous:
            continue
        records.append(record)
        if limit is not None and len(records) >= limit:
            break
    counts = Counter()
    lock = threading.Lock()
    with review_path.open("ab") as sink, futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(_review_one, record, raw_path, model, max_tokens, timeout): record
            for record in records[: workers * 2]
        }
        remaining = iter(records[workers * 2 :])
        while pending:
            done, _ = futures.wait(pending, return_when=futures.FIRST_COMPLETED)
            for future in done:
                record = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "policy": REVIEW_POLICY,
                        "trace_id": record["trace_id"],
                        "raw_sha256": record["raw_sha256"],
                        "model": model,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                with lock:
                    sink.write(_line(result))
                    sink.flush()
                verdict = result.get("verdict") or {}
                counts["reviewed" if verdict else "error"] += 1
                counts["task_" + str(verdict.get("task_status"))] += bool(verdict)
                counts["reasoning_" + str(verdict.get("reasoning_status"))] += bool(verdict)
                counts["answer_" + str(verdict.get("answer_status"))] += bool(verdict)
                if (counts["reviewed"] + counts["error"]) % 100 == 0:
                    print(dict(counts), file=sys.stderr, flush=True)
                nxt = next(remaining, None)
                if nxt is not None:
                    pending[pool.submit(_review_one, nxt, raw_path, model, max_tokens, timeout)] = nxt
    return dict(counts)


def _decide(row: dict, record: dict, model_review: dict | None) -> tuple[dict | None, dict]:
    reasons = list(record["precheck_reasons"])
    repairs = list(record["repairs"])
    if model_review is None:
        reasons.append("missing_semantic_review")
    elif "error" in model_review:
        reasons.append("semantic_review_error")
    else:
        verdict = model_review["verdict"]
        if verdict["task_status"] != "sound":
            reasons.append("task_" + verdict["task_status"])
        if verdict["reasoning_status"] == "uncertain" or verdict["answer_status"] == "uncertain":
            reasons.append("semantic_uncertainty")
    cleaned = copy.deepcopy(row)
    if not reasons:
        verdict = model_review["verdict"]
        assistant = cleaned["messages"][2]
        termination = cleaned["termination"]
        interrupted = termination.get("finish_reason") in {"length", "stream_interrupted"}
        if interrupted:
            termination["truncated"] = True
        assistant["reasoning_loss"] = bool(assistant["reasoning_loss"]) and verdict[
            "reasoning_status"
        ] == "train"
        assistant["content_loss"] = (
            bool(assistant["content_loss"])
            and bool((assistant.get("content") or "").strip())
            and not interrupted
            and verdict["answer_status"] == "train"
        )
        assistant["loss"] = assistant["reasoning_loss"] or assistant["content_loss"]
        if not assistant["loss"]:
            reasons.append("no_trainable_target_after_review")
    decision = {
        "policy": POLICY,
        "trace_id": record["trace_id"],
        "raw_sha256": record["raw_sha256"],
        "source_file": record["source_file"],
        "source_line": record["source_line"],
        "decision": "reject" if reasons else "keep",
        "reasons": reasons,
        "repairs": repairs,
        "review": model_review,
    }
    return (None if reasons else cleaned), decision


def finalize(output: Path) -> dict:
    for name in ("original-copy.jsonl", "index.jsonl", REVIEW_FILE):
        if not (output / name).is_file():
            raise CleaningError(f"missing required file: {name}")
    targets = [output / name for name in ("cleaned.jsonl", "decisions.jsonl", "rejected.jsonl", "cleaning-report.json")]
    if any(path.exists() or path.with_suffix(".tmp").exists() for path in targets):
        raise CleaningError("refusing to overwrite existing final output")
    reviews = {}
    for _, _, item in _read_jsonl(output / REVIEW_FILE):
        reviews[item["trace_id"]] = item
    counts = Counter()
    sha = hashlib.sha256()
    with (output / "cleaned.tmp").open("wb") as cleaned_out, (output / "decisions.tmp").open(
        "wb"
    ) as decision_out, (output / "rejected.tmp").open("wb") as rejected_out:
        for _, _, record in _read_jsonl(output / "index.jsonl"):
            row = _raw_row(output / "original-copy.jsonl", record)
            model_review = reviews.get(record["trace_id"])
            if model_review is not None and model_review.get("raw_sha256") != record["raw_sha256"]:
                raise CleaningError("review hash mismatch")
            cleaned, decision = _decide(row, record, model_review)
            decision_out.write(_line(decision))
            counts[decision["decision"]] += 1
            for reason in decision["reasons"]:
                counts["reason:" + reason] += 1
            for repair in decision["repairs"]:
                counts["repair:" + repair] += 1
            if cleaned is None:
                rejected_out.write(_line({k: decision[k] for k in ("trace_id", "reasons", "source_file", "source_line")}))
            else:
                raw = _line(cleaned)
                cleaned_out.write(raw)
                sha.update(raw)
    for name in ("cleaned", "decisions", "rejected"):
        (output / f"{name}.tmp").replace(output / f"{name}.jsonl")
    report = {
        "policy": POLICY,
        "review_policy": REVIEW_POLICY,
        "original_copy": str((output / "original-copy.jsonl").resolve()),
        "cleaned": str((output / "cleaned.jsonl").resolve()),
        "cleaned_sha256": sha.hexdigest(),
        "counts": dict(counts),
    }
    (output / "cleaning-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--db", type=Path, required=True)
    prep.add_argument("--source-root", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    rev = sub.add_parser("review")
    rev.add_argument("--output", type=Path, required=True)
    rev.add_argument("--model", default="Kimi-K3")
    rev.add_argument("--workers", type=int, default=200)
    rev.add_argument("--max-tokens", type=int, default=16384)
    rev.add_argument("--timeout", type=int, default=2400)
    rev.add_argument("--limit", type=int)
    rev.add_argument("--trace-id")
    fin = sub.add_parser("finalize")
    fin.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "prepare":
        result = prepare(args.db, args.source_root, args.output)
    elif args.mode == "review":
        if args.workers < 1 or args.max_tokens < 1 or args.timeout < 1:
            raise CleaningError("workers, tokens, and timeout must be positive")
        result = review(args.output, model=args.model, workers=args.workers,
                        max_tokens=args.max_tokens, timeout=args.timeout,
                        limit=args.limit, trace_id=args.trace_id)
    else:
        result = finalize(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
