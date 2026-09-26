"""Answer-focused scientific audit for candidate reasoning SFT JSONL.

This deliberately separates requirement/probe generation (question only) from
answer checking. It does not claim to prove scientific correctness: model-only
judgments are recorded as evidence and never overwrite the input dataset.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import copy
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .author import _json_objects
from .batch import controller_lock

POLICY = "scientific-answer-audit-v2"
SELECTION_POLICY = "scientific-audit-selection-v2"


class AuditError(ValueError):
    """An audit cannot be trusted or safely resumed."""


def _jsonl(path: Path):
    with path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if raw.strip():
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise AuditError(f"{path}:{line_number}: {exc}") from exc
                if not isinstance(row, dict):
                    raise AuditError(f"{path}:{line_number}: expected object")
                yield line_number, raw, row


def _sample(row: dict) -> tuple[str, str, str, bool]:
    messages = row.get("messages")
    if not isinstance(messages, list):
        raise AuditError("missing messages")
    users = [message for message in messages if isinstance(message, dict) and message.get("role") == "user"]
    assistants = [message for message in messages if isinstance(message, dict) and message.get("role") == "assistant"]
    if len(users) != 1 or len(assistants) != 1:
        raise AuditError("expected exactly one user and one assistant message")
    prompt, answer = users[0].get("content"), assistants[0].get("content")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(answer, str):
        raise AuditError("missing user prompt or invalid assistant answer")
    trace_id = row.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise AuditError("missing trace_id")
    return trace_id, prompt, answer, bool(assistants[0].get("content_loss"))


def _object(response: dict) -> dict:
    try:
        choice = response["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AuditError("review response has no assistant message") from exc
    if choice.get("finish_reason") in {"length", "stream_interrupted"}:
        raise AuditError("review response was truncated")
    # The private thinking can contain speculative JSON. Accept only a
    # complete object in the model's final content.
    for value in _json_objects(message.get("content") or ""):
        if isinstance(value, dict):
            return value
    raise AuditError("review response has no complete final JSON object")


def _requirements_prompt(prompt: str) -> str:
    return f"""You are designing an INDEPENDENT scientific falsification plan. You have
only the student's question, not their answer. Extract each ATOMIC, hard
requirement, including exact invariants, boundary conventions, units, ranges,
algorithmic conditions, and deliverable constraints. Do not weaken an exact
requirement by adding your own assumptions. For each requirement propose a
concrete probe that would reveal a plausible wrong solution. Favor small or
degenerate values, limiting cases, and cross-checks against the definition.
If the question lacks essential givens or contradicts itself, state why.

QUESTION:\n{prompt}\n
Return ONLY JSON:
{{"task_status":"answerable|flawed|uncertain", "task_issue":"", "requirements":[
{{"id":"R1", "requirement":"atomic requirement from question", "kind":"exact|conditional|qualitative", "probe":"specific independent test or check"}}]}}
Never invent a requirement not present in the question. Include all hard
requirements; keep the list concise by splitting compound requirements.
"""


def _validate_requirements(value: dict) -> dict:
    if value.get("task_status") not in {"answerable", "flawed", "uncertain"}:
        raise AuditError("invalid task_status")
    if not isinstance(value.get("task_issue"), str):
        raise AuditError("missing task_issue")
    requirements = value.get("requirements")
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 64:
        raise AuditError("expected 1..64 atomic requirements")
    for number, item in enumerate(requirements, 1):
        if not isinstance(item, dict) or item.get("id") != f"R{number}":
            raise AuditError("requirement IDs must be sequential")
        if item.get("kind") not in {"exact", "conditional", "qualitative"}:
            raise AuditError("invalid requirement kind")
        for name in ("requirement", "probe"):
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise AuditError(f"empty requirement {name}")
    if value["task_status"] != "answerable" and not value["task_issue"].strip():
        raise AuditError("uncertain or flawed task needs an explanation")
    return value


def _answer_prompt(prompt: str, answer: str, plan: dict) -> str:
    return f"""You are checking scientific correctness, not fluency or apparent effort.
The requirement/probe plan below was made WITHOUT seeing the answer. Test the
answer against EVERY requirement. An exact universal statement fails if one
legitimate counterexample exists, even if the answer acknowledges the caveat.
Trace length and a prior judge's score are irrelevant. Distinguish a flaw in
the question from a flaw in the answer. Do not pretend to execute code, consult
sources, or verify a fact you cannot actually check; mark it unverifiable.

QUESTION:\n{prompt}\n
INDEPENDENT PLAN:\n{json.dumps(plan, ensure_ascii=False)}\n
STUDENT ANSWER:\n{answer}\n
For each R-id, cite a short exact phrase or formula from the answer (or say
"absent"), apply the proposed probe, and state the expected and answer-implied
result. For code, compare the IMPLEMENTED expression with the stated formula;
do not credit a correct derivation if the delivered implementation violates it.
Check arithmetic, units, boundary cases, and contradictions. A probe with no
conclusive result is unverifiable, not satisfied. Output ONLY JSON:
{{"checks":[{{"id":"R1", "status":"satisfied|violated|unverifiable",
"answer_evidence":"short exact quote/formula or absent", "probe_result":"concrete input and expected vs answer-implied result; or why unverifiable",
"explanation":"brief reasoning"}}],
"critical_issue":"specific decisive contradiction, or empty string",
"summary":"brief overall assessment"}}
"""


def _validate_checks(value: dict, plan: dict, answer: str) -> dict:
    checks = value.get("checks")
    expected = [item["id"] for item in plan["requirements"]]
    if not isinstance(checks, list) or [item.get("id") if isinstance(item, dict) else None for item in checks] != expected:
        raise AuditError("review must check every requirement exactly once in order")
    for item in checks:
        if item.get("status") not in {"satisfied", "violated", "unverifiable"}:
            raise AuditError("invalid check status")
        for name in ("answer_evidence", "probe_result", "explanation"):
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise AuditError(f"empty check {name}")
    if not isinstance(value.get("critical_issue"), str) or not isinstance(value.get("summary"), str):
        raise AuditError("missing summary or critical_issue")
    if any(item["status"] == "violated" for item in checks) and not value["critical_issue"].strip():
        raise AuditError("violated requirement needs a critical_issue")
    if not answer.strip() and any(item["status"] == "satisfied" for item in checks):
        raise AuditError("empty answer cannot satisfy a deliverable")
    return value


def _consistency_prompt(plan: dict, verdict: dict) -> str:
    return f"""You are a SEPARATE final contradiction checker. You do not see the
student answer or the earlier review's confidence score. Only use the atomic
requirements and the review's concrete findings below. Look for a case where
one finding admits a delivered result that contradicts ANY other exact or
universal requirement, even when that finding calls the mismatch a caveat,
limitation, floor, approximation, or disclosed side effect. Disclosure does
NOT satisfy an exact requirement. Compare evidence across requirement IDs,
not just within one check. Do not add an unstated domain restriction. If the
evidence is insufficient, say uncertain rather than assuming consistency.

REQUIREMENTS AND INDEPENDENT PROBES:\n{json.dumps(plan, ensure_ascii=False)}\n
ANSWER CHECKS:\n{json.dumps(verdict, ensure_ascii=False)}\n
Return ONLY JSON:
{{"status":"consistent|conflict|uncertain", "conflicts":[
{{"required_id":"R1", "evidence_id":"R2", "input":"concrete case",
"required":"exact required result", "delivered":"answer-implied result",
"explanation":"why this contradicts the hard requirement"}}],
"rationale":"brief justification"}}
If any concrete contradiction exists, status MUST be conflict. If a result
cannot be established, status is uncertain. For consistent, conflicts is [].
"""


def _validate_consistency(value: dict, plan: dict) -> dict:
    if value.get("status") not in {"consistent", "conflict", "uncertain"}:
        raise AuditError("invalid consistency status")
    if not isinstance(value.get("rationale"), str) or not value["rationale"].strip():
        raise AuditError("consistency rationale is missing")
    conflicts = value.get("conflicts")
    if not isinstance(conflicts, list):
        raise AuditError("consistency conflicts must be a list")
    if bool(conflicts) != (value["status"] == "conflict"):
        raise AuditError("consistency status and conflicts disagree")
    ids = {item["id"] for item in plan["requirements"]}
    for conflict in conflicts:
        if not isinstance(conflict, dict) or conflict.get("required_id") not in ids or conflict.get("evidence_id") not in ids:
            raise AuditError("consistency conflict references unknown requirement")
        for name in ("input", "required", "delivered", "explanation"):
            if not isinstance(conflict.get(name), str) or not conflict[name].strip():
                raise AuditError(f"consistency conflict has empty {name}")
    return value


def audit_row(
    row: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str = "Kimi-K3",
    max_tokens: int = 16384,
    timeout: int = 2400,
    max_input_chars: int = 750_000,
) -> dict:
    """Run two blinded stages with the same reviewer model."""
    trace_id, prompt, answer, content_loss = _sample(row)
    termination = row.get("termination") or {}
    if isinstance(termination, dict) and (
        termination.get("truncated") is True
        or termination.get("finish_reason") in {"length", "stream_interrupted"}
    ):
        content_loss = False
    first_prompt = _requirements_prompt(prompt)
    if len(first_prompt) > max_input_chars:
        raise AuditError("requirement input exceeds max_input_chars")
    first_response = chat_fn(
        [{"role": "user", "content": first_prompt}], model=model,
        temperature=0.0, max_tokens=max_tokens, timeout=timeout,
    )
    plan = _validate_requirements(_object(first_response))
    second_prompt = _answer_prompt(prompt, answer, plan)
    if len(second_prompt) > max_input_chars:
        raise AuditError("answer input exceeds max_input_chars; never silently truncate")
    second_response = chat_fn(
        [{"role": "user", "content": second_prompt}], model=model,
        temperature=0.0, max_tokens=max_tokens, timeout=timeout,
    )
    verdict = _validate_checks(_object(second_response), plan, answer)
    statuses = {item["status"] for item in verdict["checks"]}
    consistency = None
    third_response = None
    if plan["task_status"] == "answerable" and statuses == {"satisfied"} and answer.strip() and content_loss:
        third_prompt = _consistency_prompt(plan, verdict)
        if len(third_prompt) > max_input_chars:
            raise AuditError("consistency input exceeds max_input_chars")
        third_response = chat_fn(
            [{"role": "user", "content": third_prompt}], model=model,
            temperature=0.0, max_tokens=max_tokens, timeout=timeout,
        )
        consistency = _validate_consistency(_object(third_response), plan)
    if plan["task_status"] != "answerable" or "violated" in statuses or (
        consistency is not None and consistency["status"] == "conflict"
    ):
        disposition = "quarantine"
    elif "unverifiable" in statuses or not answer.strip() or not content_loss or (
        consistency is not None and consistency["status"] == "uncertain"
    ):
        disposition = "reasoning_candidate"
    else:
        disposition = "model_supported_answer"
    return {
        "policy": POLICY, "trace_id": trace_id, "model": model,
        "disposition": disposition, "plan": plan, "verdict": verdict,
        "consistency": consistency,
        "usage": {"plan": first_response.get("usage") or {},
                  "answer": second_response.get("usage") or {},
                  "consistency": (third_response or {}).get("usage") or {}},
    }


def run_audit(
    input_path: Path, output_path: Path, *, model: str = "Kimi-K3",
    workers: int = 4, max_tokens: int = 16384, timeout: int = 2400,
    max_input_chars: int = 750_000, limit: int | None = None,
    trace_id: str | None = None, chat_fn: Callable = llm.chat,
) -> dict:
    """Append hash-bound reviews; reruns skip completed rows and retry errors."""
    if workers < 1 or max_tokens < 1 or timeout < 1 or max_input_chars < 1:
        raise AuditError("workers, token/input budgets, and timeout must be positive")
    input_path, output_path = Path(input_path), Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise AuditError("audit output cannot overwrite input")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with controller_lock(output_path, exclusive=True):
        previous = {}
        if output_path.exists():
            for _, _, item in _jsonl(output_path):
                if item.get("policy") != POLICY or item.get("model") != model:
                    raise AuditError("existing audit uses another policy or model")
                previous[item.get("trace_id")] = item
        jobs = []
        seen = set()
        skipped = 0
        with input_path.open("rb") as source:
            line_number = 0
            while True:
                offset = source.tell()
                raw = source.readline()
                if not raw:
                    break
                line_number += 1
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise AuditError(f"{input_path}:{line_number}: {exc}") from exc
                identity, _, _, _ = _sample(row)
                if trace_id is not None and identity != trace_id:
                    continue
                if identity in seen:
                    raise AuditError(f"duplicate trace_id: {identity}")
                seen.add(identity)
                digest = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
                old = previous.get(identity)
                if old is not None and old.get("row_sha256") != digest:
                    raise AuditError(f"changed input row for {identity}")
                if old is not None and "error" not in old:
                    skipped += 1
                    continue
                jobs.append((offset, len(raw), identity, digest))
                if limit is not None and len(jobs) >= limit:
                    break
        if trace_id is not None and not seen:
            raise AuditError(f"trace_id not found: {trace_id}")
        counts = Counter(skipped=skipped)

        def one(job):
            offset, length, identity, digest = job
            try:
                with input_path.open("rb") as source:
                    source.seek(offset)
                    raw = source.read(length)
                if hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest() != digest:
                    raise AuditError("input changed during audit")
                row = json.loads(raw)
                result = audit_row(row, chat_fn=chat_fn, model=model,
                                   max_tokens=max_tokens, timeout=timeout,
                                   max_input_chars=max_input_chars)
            except Exception as exc:
                result = {"policy": POLICY, "trace_id": identity,
                          "model": model, "error": f"{type(exc).__name__}: {exc}"[:1000]}
            result["row_sha256"] = digest
            return result

        # Bounded pending set prevents thousands of long answers occupying RAM.
        with output_path.open("ab") as sink, futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(one, job) for job in jobs[:workers * 2]}
            iterator = iter(jobs[workers * 2:])
            while pending:
                done, pending = futures.wait(pending, return_when=futures.FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    sink.write((json.dumps(result, ensure_ascii=False) + "\n").encode())
                    sink.flush()
                    counts[result.get("disposition", "error")] += 1
                    if sum(counts.values()) % 100 == 0:
                        print(dict(counts), file=sys.stderr, flush=True)
                    next_job = next(iterator, None)
                    if next_job is not None:
                        pending.add(pool.submit(one, next_job))
        return dict(counts)


def materialize(input_path: Path, audit_path: Path, output_dir: Path) -> dict:
    """Create distinct answer-supported and reasoning-only *candidate* corpora.

    This is intentionally not a scientific truth certificate. Quarantined or
    incomplete rows remain in the untouched input and decision log.
    """
    input_path, audit_path, output_dir = map(Path, (input_path, audit_path, output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = {name: output_dir / name for name in (
        "model-supported-answer.jsonl", "reasoning-candidates.jsonl",
        "scientific-audit-decisions.jsonl", "scientific-audit-report.json",
    )}
    if any(path.exists() or Path(str(path) + ".tmp").exists() for path in targets.values()):
        raise AuditError("refusing to overwrite an existing selection")
    with controller_lock(targets["scientific-audit-decisions.jsonl"], exclusive=True):
        audits = {}
        for _, _, item in _jsonl(audit_path):
            if item.get("policy") != POLICY:
                raise AuditError("audit policy mismatch")
            identity = item.get("trace_id")
            old = audits.get(identity)
            if old is not None and "error" not in old and "error" not in item:
                raise AuditError(f"duplicate completed audit: {identity}")
            audits[identity] = item
        counts = Counter()
        seen = set()
        temporary = {name: Path(str(path) + ".tmp") for name, path in targets.items()
                     if name.endswith(".jsonl")}
        try:
            with temporary["model-supported-answer.jsonl"].open("wb") as answers, \
                 temporary["reasoning-candidates.jsonl"].open("wb") as reasoning, \
                 temporary["scientific-audit-decisions.jsonl"].open("wb") as decisions:
                for _, raw, row in _jsonl(input_path):
                    identity, _, _, content_loss = _sample(row)
                    if identity in seen:
                        raise AuditError(f"duplicate input trace_id: {identity}")
                    seen.add(identity)
                    digest = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
                    audit = audits.get(identity)
                    if audit is None or "error" in audit:
                        raise AuditError(f"scientific audit incomplete: {identity}")
                    if audit.get("row_sha256") != digest:
                        raise AuditError(f"audit row hash mismatch: {identity}")
                    disposition = audit.get("disposition")
                    if disposition not in {"model_supported_answer", "reasoning_candidate", "quarantine"}:
                        raise AuditError(f"unknown disposition: {identity}")
                    assistant = row["messages"][-1]
                    if disposition == "model_supported_answer":
                        if not content_loss:
                            raise AuditError(f"answer approval contradicts loss flag: {identity}")
                        answer_row = copy.deepcopy(row)
                        answer_assistant = answer_row["messages"][-1]
                        answer_assistant["reasoning_loss"] = False
                        answer_assistant["loss"] = True
                        answer_row["scientific_audit"] = {
                            "policy": POLICY, "model": audit["model"],
                            "selection_policy": SELECTION_POLICY,
                            "row_sha256": digest, "disposition": disposition,
                            "supervision_target": "answer",
                            "scientific_correctness_proven": False,
                        }
                        answers.write((json.dumps(answer_row, ensure_ascii=False) + "\n").encode())
                        counts["answer_output_rows"] += 1
                    if disposition != "quarantine" and assistant.get("reasoning_loss") is True:
                        reasoning_row = copy.deepcopy(row)
                        reasoning_assistant = reasoning_row["messages"][-1]
                        suppressed_answer_sha = hashlib.sha256(
                            reasoning_assistant["content"].encode("utf-8")
                        ).hexdigest()
                        reasoning_assistant["content"] = ""
                        reasoning_assistant["content_loss"] = False
                        reasoning_assistant["loss"] = True
                        reasoning_row["scientific_audit"] = {
                            "policy": POLICY, "model": audit["model"],
                            "selection_policy": SELECTION_POLICY,
                            "row_sha256": digest, "disposition": disposition,
                            "supervision_target": "reasoning_candidate",
                            "scientific_correctness_proven": False,
                            "suppressed_answer_sha256": suppressed_answer_sha,
                        }
                        reasoning.write((json.dumps(reasoning_row, ensure_ascii=False) + "\n").encode())
                        counts["reasoning_output_rows"] += 1
                    elif disposition == "reasoning_candidate":
                        disposition = "quarantine"
                    decisions.write((json.dumps({
                        "trace_id": identity, "row_sha256": digest,
                        "disposition": disposition,
                        "critical_issue": (audit.get("verdict") or {}).get("critical_issue", ""),
                    }, ensure_ascii=False) + "\n").encode())
                    counts[disposition] += 1
            if set(audits) != seen:
                raise AuditError("audit contains rows absent from the input")
            report = {"policy": POLICY, "selection_policy": SELECTION_POLICY,
                      "scientific_correctness_proven": False,
                      "input": str(input_path.resolve()), "audit": str(audit_path.resolve()),
                      "counts": dict(counts)}
            temporary["scientific-audit-report.json"] = Path(str(targets["scientific-audit-report.json"]) + ".tmp")
            temporary["scientific-audit-report.json"].write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for name, path in targets.items():
                temporary[name].replace(path)
            return report
        finally:
            for path in temporary.values():
                if path.exists():
                    path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review")
    review.add_argument("--input", type=Path, required=True)
    review.add_argument("--out", type=Path, required=True)
    review.add_argument("--model", default="Kimi-K3")
    review.add_argument("--workers", type=int, default=4)
    review.add_argument("--max-tokens", type=int, default=16384)
    review.add_argument("--timeout", type=int, default=2400)
    review.add_argument("--max-input-chars", type=int, default=750000)
    review.add_argument("--limit", type=int)
    review.add_argument("--trace-id")
    select = sub.add_parser("select")
    select.add_argument("--input", type=Path, required=True)
    select.add_argument("--audit", type=Path, required=True)
    select.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "review":
        result = run_audit(args.input, args.out, model=args.model,
                           workers=args.workers, max_tokens=args.max_tokens,
                           timeout=args.timeout,
                           max_input_chars=args.max_input_chars,
                           limit=args.limit, trace_id=args.trace_id)
    else:
        result = materialize(args.input, args.audit, args.out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
