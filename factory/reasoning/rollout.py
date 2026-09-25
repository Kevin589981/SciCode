"""Collect native-thinking solver rollouts for reasoning tasks."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .preflight import PREFLIGHT_POLICY
from .schema import TRACE_SCHEMA, canonical_hash, validate_task, validate_trace
from .student_view import render_student_user, student_view_hash
from .verify import VERIFICATION_POLICY


class RolloutError(ValueError):
    """A solver response or rollout input is unusable."""


SYSTEM = """You are a scientific reasoning specialist. Work through the scientific
assumptions, competing methods, regimes, and failure modes carefully. Produce a
self-contained final response satisfying every requested deliverable. Do not
claim evidence you did not derive from the problem."""


def solver_messages(task: dict) -> list[dict]:
    """Render the complete, source-free task that all reviewers must evaluate."""
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": render_student_user(task)},
    ]


def trace_id_for(
    task: dict,
    model: str,
    attempt: int,
    run_variant: str | None = None,
) -> str:
    """Key resume by task content, solver identity, and requested attempt."""
    task = validate_task(task)
    identity = canonical_hash(
        {
            "task_hash": canonical_hash(task),
            "model": model,
            "attempt": attempt,
            "run_variant": run_variant or "default",
        }
    )[:16]
    readable = re.sub(r"[^A-Za-z0-9_-]+", "-", task["task_id"]).strip("-")[:80]
    return f"{readable}::{identity}"


def current_commit(cwd: Path | None = None) -> str:
    """Best-effort factory commit for runtime provenance."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def collect_trace(
    task: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    attempt: int = 0,
    temperature: float = 0.7,
    max_tokens: int = 16384,
    timeout: int = 2400,
    factory_commit: str | None = None,
    task_set_hash: str = "unknown",
    run_variant: str | None = None,
    outcome_fn: Callable[[dict, dict], dict] | None = None,
) -> dict:
    """Collect one trace; an auxiliary checker can never erase the raw response."""
    task = validate_task(task)
    model = model or llm.client_config()["model"]
    messages = solver_messages(task)
    started = time.monotonic()
    response = chat_fn(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        allow_partial=True,
    )
    try:
        choice = response["choices"][0]
        assistant = llm.assistant_message(response)
    except (KeyError, IndexError, TypeError) as exc:
        raise RolloutError(
            f"solver response has no usable assistant message: {exc}"
        ) from exc
    messages = [*messages, assistant]
    outcome = {"status": "not_run", "kind": "auxiliary_check"}
    if outcome_fn is not None:
        try:
            checked = outcome_fn(task, assistant)
            if not isinstance(checked, dict):
                raise TypeError("checker must return an object")
            outcome = checked
        except Exception as exc:
            outcome = {
                "status": "error",
                "kind": "auxiliary_check",
                "detail": f"{type(exc).__name__}: {exc}"[:1000],
            }
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason.strip():
        finish_reason = "unknown"
    usage = response.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")
    token_boundary = (
        isinstance(completion_tokens, int)
        and not isinstance(completion_tokens, bool)
        and completion_tokens >= max_tokens - 1
    )
    trace = {
        "schema_version": TRACE_SCHEMA,
        "trace_id": trace_id_for(task, model, attempt, run_variant),
        "task_id": task["task_id"],
        "task_hash": canonical_hash(task),
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "finish_reason": finish_reason,
        "truncated": finish_reason in {"length", "stream_interrupted"} or token_boundary,
        "attempt": attempt,
        "messages": messages,
        "outcome": outcome,
        "usage": usage,
        "timing_sec": round(time.monotonic() - started, 3),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "provenance": {
            "factory_commit": factory_commit or current_commit(),
            "task_set_hash": task_set_hash,
            "source_commit": task["source"]["commit"],
            "student_view_hash": student_view_hash(task),
            "run_variant": run_variant or "default",
        },
    }
    try:
        return validate_trace(trace)
    except Exception as exc:
        raise RolloutError(f"raw trace violates the trace schema: {exc}") from exc


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RolloutError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise RolloutError(f"{path}:{number}: row must be an object")
            rows.append(value)
    return rows


def run_rollouts(
    tasks_path: Path,
    output_path: Path,
    *,
    preflight_path: Path | None = None,
    verification_path: Path | None = None,
    preflight_model: str | None = None,
    verifier_model: str | None = None,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    attempts: int = 1,
    temperature: float = 0.7,
    max_tokens: int = 16384,
    timeout: int = 2400,
    concurrency: int = 1,
    factory_commit: str | None = None,
    run_variant: str | None = None,
    outcome_fn: Callable[[dict, dict], dict] | None = None,
    errors_path: Path | None = None,
) -> dict:
    """Roll out admitted tasks with bounded concurrency and exact resume keys."""
    if attempts < 1 or concurrency < 1:
        raise RolloutError("attempts and concurrency must be positive")
    tasks = [validate_task(row) for row in _jsonl(Path(tasks_path))]
    model = model or llm.client_config()["model"]
    admission_sets = []
    if preflight_path is not None:
        records = _jsonl(Path(preflight_path))
        if preflight_model is not None:
            records = [
                row
                for row in records
                if (row.get("critic") or {}).get("model") == preflight_model
            ]
        records = [
            row for row in records if row.get("policy_version") == PREFLIGHT_POLICY
        ]
        admission_sets.append(
            {row.get("task_hash") for row in records if row.get("accepted") is True}
        )
    if verification_path is not None:
        records = _jsonl(Path(verification_path))
        if verifier_model is not None:
            records = [
                row
                for row in records
                if (row.get("verifier") or {}).get("model") == verifier_model
            ]
        records = [
            row for row in records if row.get("policy_version") == VERIFICATION_POLICY
        ]
        admission_sets.append(
            {row.get("task_hash") for row in records if row.get("accepted") is True}
        )
    admitted_hashes = None
    if admission_sets:
        admitted_hashes = set.intersection(*admission_sets)
    selected = [
        task
        for task in tasks
        if admitted_hashes is None or canonical_hash(task) in admitted_hashes
    ]
    task_set_hash = canonical_hash([canonical_hash(task) for task in selected])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path = errors_path or output_path.with_suffix(".errors.jsonl")
    existing = _jsonl(output_path) if output_path.exists() else []
    done = {row.get("trace_id") for row in existing}
    jobs = []
    skipped = 0
    for task in selected:
        for attempt in range(attempts):
            trace_id = trace_id_for(task, model, attempt, run_variant)
            if trace_id in done:
                skipped += 1
            else:
                jobs.append((task, attempt, trace_id))
    counts = {
        "written": 0,
        "errors": 0,
        "skipped": skipped,
        "not_admitted": len(tasks) - len(selected),
        "task_set_hash": task_set_hash,
    }

    def one(job):
        task, attempt, _trace_id = job
        return collect_trace(
            task,
            chat_fn=chat_fn,
            model=model,
            attempt=attempt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            factory_commit=factory_commit,
            task_set_hash=task_set_hash,
            run_variant=run_variant,
            outcome_fn=outcome_fn,
        )

    with output_path.open("a", encoding="utf-8") as output, Path(errors_path).open(
        "a", encoding="utf-8"
    ) as errors, futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending = {pool.submit(one, job): job for job in jobs}
        for future in futures.as_completed(pending):
            task, attempt, trace_id = pending[future]
            try:
                trace = future.result()
            except Exception as exc:
                errors.write(
                    json.dumps(
                        {
                            "trace_id": trace_id,
                            "task_id": task["task_id"],
                            "attempt": attempt,
                            "model": model,
                            "error": f"{type(exc).__name__}: {exc}"[:1200],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                errors.flush()
                counts["errors"] += 1
                continue
            output.write(json.dumps(trace, ensure_ascii=False) + "\n")
            output.flush()
            counts["written"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--verification", type=Path)
    parser.add_argument("--preflight-model")
    parser.add_argument("--verifier-model")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--run-variant")
    args = parser.parse_args()
    result = run_rollouts(
        args.tasks,
        args.out,
        preflight_path=args.preflight,
        verification_path=args.verification,
        preflight_model=args.preflight_model,
        verifier_model=args.verifier_model,
        model=args.model,
        attempts=args.attempts,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        concurrency=args.concurrency,
        run_variant=args.run_variant,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
