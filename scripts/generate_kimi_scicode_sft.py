#!/usr/bin/env python3
"""Generate step-level SciCode SFT records with an adaptive Kimi request scheduler.

Each record is one SciCode subproblem prompt and the raw Kimi completion. Episodes
run the official sequential prompt protocol: code extracted from an earlier Kimi
answer is supplied to the next subproblem in the same episode. The generator does
not run the HDF5 evaluator and never discards a response because it is incorrect.
HTTP failures are emitted as records with an error status so the requested record
count remains auditable.

The output JSONL schema is intentionally training-friendly and trace-friendly:
messages, completion, reasoning_content, completion_with_reasoning, parsed_code,
provider_response, usage, and metadata are all retained.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import importlib.util
import json
import os
import random
import re
import resource
import time
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import httpx
from datasets import load_dataset


REPO_ROOT = Path(__file__).resolve().parents[1]
SKIPPED_SUBSTEPS = frozenset({("13", 5), ("62", 0), ("76", 2)})
METRIC_RE = re.compile(
    r"^\s*smg_worker_requests_active(?:\{[^}]*\})?\s+"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)(?:\s+\S+)?\s*$"
)


@dataclasses.dataclass(frozen=True)
class EpisodePlan:
    ordinal: int
    problem_index: int
    step_indices: tuple[int, ...]

    @property
    def episode_id(self) -> str:
        return f"episode-{self.ordinal:06d}"


@dataclasses.dataclass
class CompletionResult:
    status: str
    content: str = ""
    reasoning_content: str = ""
    usage: dict[str, Any] = dataclasses.field(default_factory=dict)
    finish_reason: Any = None
    request_id: str | None = None
    provider_model: str | None = None
    attempts: int = 0
    raw_response: dict[str, Any] = dataclasses.field(default_factory=dict)
    raw_chunks: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None


@dataclasses.dataclass
class GenerationStats:
    planned_records: int
    planned_episodes: int
    produced_records: int = 0
    error_records: int = 0
    completed_episodes: int = 0
    requests: int = 0
    retries: int = 0
    started_at: float = dataclasses.field(default_factory=time.time)
    finished_at: float | None = None
    last_remote_active: int | None = None
    metrics_history: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def snapshot(self, in_flight: int, scheduled_episodes: int) -> dict[str, Any]:
        elapsed = max(time.time() - self.started_at, 1e-6)
        return {
            "planned_records": self.planned_records,
            "planned_episodes": self.planned_episodes,
            "produced_records": self.produced_records,
            "error_records": self.error_records,
            "completed_episodes": self.completed_episodes,
            "scheduled_episodes": scheduled_episodes,
            "in_flight_episodes": in_flight,
            "requests": self.requests,
            "retries": self.retries,
            "elapsed_seconds": round(elapsed, 3),
            "records_per_second": round(self.produced_records / elapsed, 4),
            "last_remote_active": self.last_remote_active,
        }


def raise_file_descriptor_limit(target: int) -> dict[str, int] | None:
    """Raise the soft descriptor limit when the host allows it."""
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        desired = min(max(soft, target), hard)
        if desired != soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
            soft = desired
        return {"soft": int(soft), "hard": int(hard)}
    except (AttributeError, OSError, ValueError):
        return None


@lru_cache(maxsize=1)
def official_module() -> Any:
    """Load the upstream SciCode prompt implementation without changing files."""
    path = REPO_ROOT / "eval" / "inspect_ai" / "scicode.py"
    spec = importlib.util.spec_from_file_location("scicode_official_for_sft", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load SciCode official adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    old_cwd = Path.cwd()
    os.chdir(path.parent)
    try:
        spec.loader.exec_module(module)
    finally:
        os.chdir(old_cwd)
    return module


def is_skipped(problem_id: Any, index: int) -> bool:
    return (str(problem_id), index) in SKIPPED_SUBSTEPS


def active_indices(row: dict[str, Any]) -> list[int]:
    return [
        index
        for index, _ in enumerate(row["sub_steps"])
        if not is_skipped(row["problem_id"], index)
    ]


def build_episode_plan(
    rows: list[dict[str, Any]], target_records: int, seed: int
) -> list[EpisodePlan]:
    if target_records <= 0:
        raise ValueError("target_records must be positive")
    if not rows:
        raise ValueError("dataset is empty")
    rng = random.Random(seed)
    order = list(range(len(rows)))
    rng.shuffle(order)
    plans: list[EpisodePlan] = []
    remaining = target_records
    cursor = 0
    while remaining:
        row_index = order[cursor % len(order)]
        indices = active_indices(rows[row_index])
        if not indices:
            cursor += 1
            if cursor > len(order) * 2:
                raise ValueError("dataset has no active SciCode subproblems")
            continue
        take = min(len(indices), remaining)
        plans.append(
            EpisodePlan(
                ordinal=len(plans),
                problem_index=row_index,
                step_indices=tuple(indices[:take]),
            )
        )
        remaining -= take
        cursor += 1
    return plans


def normalize_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if value is None:
        return ""
    return str(value)


def extract_code(response: str) -> str:
    """Mirror SciCode's code-block extraction without printing warnings."""
    if not response:
        return ""
    match = re.search(
        r"\x60\x60\x60(?:python|py)?[ \t]*\r?\n?(.*?)\x60\x60\x60",
        response,
        flags=re.IGNORECASE | re.DOTALL,
    )
    code = match.group(1) if match else response
    code = re.sub(
        r"^\s*(?:import .*(?:\r?\n|$)|from .*\s+import\s+.*(?:\r?\n|$))",
        "",
        code,
        flags=re.MULTILINE,
    )
    return code.strip()


@lru_cache(maxsize=128)
def released_step_code(problem_id: str, index: int, header: str) -> str:
    """Load one of the three released predecessor steps used by SciCode."""
    path = REPO_ROOT / "eval" / "data" / f"{problem_id}.{index + 1}.txt"
    if not path.is_file():
        return "# Released predecessor code is unavailable."
    text = path.read_text(encoding="utf-8")
    try:
        module = official_module()
        name = module.extract_function_name(header)
        extracted = module.get_function_from_code(text, name)
        if extracted:
            return str(extracted)
    except Exception:
        pass
    return text


def prepare_prompt(
    module: Any,
    row: dict[str, Any],
    step_index: int,
    previous_codes: list[str | None],
    with_background: bool,
) -> tuple[str, str]:
    """Construct the exact upstream prompt for one step."""
    for previous_index in range(step_index):
        if previous_codes[previous_index] is not None:
            continue
        previous_step = row["sub_steps"][previous_index]
        if is_skipped(row["problem_id"], previous_index):
            previous_codes[previous_index] = released_step_code(
                str(row["problem_id"]),
                previous_index,
                str(previous_step["function_header"]),
            )
        else:
            previous_codes[previous_index] = (
                "# No parseable code was returned for the previous step."
            )
    assistant = module.ScicodePromptingAssistant(
        output_dir=REPO_ROOT / "data",
        prompt_dir=REPO_ROOT / "data",
        with_background=with_background,
    )
    assistant.previous_llm_code = previous_codes
    template = (
        module.BACKGOUND_PROMPT_TEMPLATE
        if with_background
        else module.DEFAULT_PROMPT_TEMPLATE
    )
    return assistant.generate_prompt_with_steps(
        row, step_index + 1, template
    )


def endpoint_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def text_from_delta(delta: dict[str, Any], *names: str) -> str:
    for name in names:
        value = delta.get(name)
        if value is not None:
            text = normalize_content(value)
            if text:
                return text
    return ""


def parse_usage(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


class StreamAccumulator:
    """Consume SSE lines incrementally so large responses are not duplicated."""

    def __init__(self, keep_raw_chunks: bool) -> None:
        self.keep_raw_chunks = keep_raw_chunks
        self.content_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.chunks: list[dict[str, Any]] = []
        self.usage: dict[str, Any] = {}
        self.request_id: str | None = None
        self.provider_model: str | None = None
        self.finish_reason: Any = None

    def feed(self, line: str) -> None:
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return
        payload = stripped[5:].strip()
        if payload == "[DONE]":
            return
        try:
            item = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(item, dict):
            return
        if self.keep_raw_chunks:
            self.chunks.append(item)
        self.request_id = self.request_id or item.get("id")
        self.provider_model = self.provider_model or item.get("model")
        self.usage.update(parse_usage(item.get("usage")))
        choices = item.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0] if isinstance(choices[0], dict) else {}
        if choice.get("finish_reason") is not None:
            self.finish_reason = choice.get("finish_reason")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return
        self.content_parts.append(text_from_delta(delta, "content"))
        self.reasoning_parts.append(
            text_from_delta(delta, "reasoning_content", "reasoning")
        )

    def result(self) -> CompletionResult:
        content = "".join(self.content_parts)
        reasoning = "".join(self.reasoning_parts)
        raw = {
            "id": self.request_id,
            "model": self.provider_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": reasoning,
                    },
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": self.usage,
        }
        return CompletionResult(
            status="ok",
            content=content,
            reasoning_content=reasoning,
            usage=self.usage,
            finish_reason=self.finish_reason,
            request_id=(
                str(self.request_id) if self.request_id is not None else None
            ),
            provider_model=(
                str(self.provider_model)
                if self.provider_model is not None
                else None
            ),
            raw_response=raw,
            raw_chunks=self.chunks if self.keep_raw_chunks else None,
        )


def parse_stream_payload(
    lines: Iterable[str], keep_raw_chunks: bool
) -> CompletionResult:
    accumulator = StreamAccumulator(keep_raw_chunks)
    for line in lines:
        accumulator.feed(line)
    return accumulator.result()


def parse_nonstream_payload(
    payload: dict[str, Any], keep_raw_chunks: bool
) -> CompletionResult:
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    if not isinstance(choice, dict):
        choice = {}
    message = choice.get("message")
    if not isinstance(message, dict):
        message = {}
    content = normalize_content(message.get("content"))
    reasoning = text_from_delta(message, "reasoning_content", "reasoning")
    return CompletionResult(
        status="ok",
        content=content,
        reasoning_content=reasoning,
        usage=parse_usage(payload.get("usage")),
        finish_reason=choice.get("finish_reason"),
        request_id=str(payload["id"]) if payload.get("id") is not None else None,
        provider_model=(
            str(payload["model"]) if payload.get("model") is not None else None
        ),
        raw_response=payload,
        raw_chunks=[payload] if keep_raw_chunks else None,
    )


async def request_once(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: httpx.Timeout,
    keep_raw_chunks: bool,
) -> CompletionResult:
    if payload.get("stream"):
        async with client.stream(
            "POST", url, headers=headers, json=payload, timeout=timeout
        ) as response:
            body_type = response.headers.get("content-type", "")
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise httpx.HTTPStatusError(
                    f"provider HTTP {response.status_code}: {body[:2000]}",
                    request=response.request,
                    response=response,
                )
            if "application/json" in body_type:
                raw = await response.aread()
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ValueError("provider returned non-object JSON")
                return parse_nonstream_payload(parsed, keep_raw_chunks)
            accumulator = StreamAccumulator(keep_raw_chunks)
            async for line in response.aiter_lines():
                accumulator.feed(line)
            return accumulator.result()
    response = await client.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"provider HTTP {response.status_code}: {response.text[:2000]}",
            request=response.request,
            response=response,
        )
    parsed = response.json()
    if not isinstance(parsed, dict):
        raise ValueError("provider returned non-object JSON")
    return parse_nonstream_payload(parsed, keep_raw_chunks)


def retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        return status in {408, 409, 425, 429, 500, 502, 503, 504}
    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            OSError,
        ),
    )


async def request_completion(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    args: argparse.Namespace,
    stats: GenerationStats,
) -> CompletionResult:
    last_error: BaseException | None = None
    for attempt in range(1, args.retries + 2):
        stats.requests += 1
        try:
            result = await request_once(
                client,
                url,
                headers,
                payload,
                args.request_timeout,
                args.keep_raw_chunks,
            )
            result.attempts = attempt
            return result
        except BaseException as exc:
            last_error = exc
            if attempt > args.retries or not retryable_exception(exc):
                break
            stats.retries += 1
            delay = min(args.retry_backoff * (2 ** (attempt - 1)), 60.0)
            delay += random.random() * min(1.0, delay / 4)
            await asyncio.sleep(delay)
    message = str(last_error) if last_error is not None else "unknown provider error"
    error: dict[str, Any] = {
        "type": type(last_error).__name__ if last_error is not None else "Error",
        "message": message[:4000],
    }
    if isinstance(last_error, httpx.HTTPStatusError) and last_error.response is not None:
        error["http_status"] = last_error.response.status_code
    return CompletionResult(
        status="error",
        attempts=args.retries + 1,
        error=error,
    )


async def read_remote_active(
    client: httpx.AsyncClient, metrics_url: str, timeout: float
) -> tuple[int | None, str | None]:
    try:
        response = await client.get(metrics_url, timeout=timeout)
        response.raise_for_status()
        total = 0.0
        matched = 0
        for line in response.text.splitlines():
            match = METRIC_RE.match(line)
            if match:
                total += float(match.group(1))
                matched += 1
        if not matched:
            return None, "metric smg_worker_requests_active was not found"
        return max(0, int(total)), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:500]}"


def target_inflight(
    remote_active: int | None, min_concurrency: int, max_concurrency: int
) -> int:
    if remote_active is None:
        return min_concurrency
    available = max_concurrency - remote_active
    return max(min_concurrency, min(max_concurrency, available))


def completion_text(result: CompletionResult) -> str:
    return result.content or result.reasoning_content


def make_record(
    *,
    episode: EpisodePlan,
    row: dict[str, Any],
    step_index: int,
    prompt: str,
    context_code: str,
    result: CompletionResult,
    args: argparse.Namespace,
    prompt_error: str | None = None,
) -> dict[str, Any]:
    step = row["sub_steps"][step_index]
    raw_content = result.content
    parsed = extract_code(raw_content)
    completion_with_reasoning = (
        f"<think>\n{result.reasoning_content}\n</think>\n\n{raw_content}"
        if result.reasoning_content
        else raw_content
    )
    status = result.status
    if prompt_error:
        status = "prompt_error"
    if result.status == "ok" and not raw_content and result.reasoning_content:
        status = "reasoning_only"
    metadata = {
        "schema_version": "scicode-sft-v1",
        "episode_id": episode.episode_id,
        "episode_ordinal": episode.ordinal,
        "problem_id": str(row["problem_id"]),
        "problem_name": row.get("problem_name"),
        "step_index": step_index,
        "step_number": step.get("step_number"),
        "with_background": args.with_background,
        "prompt_protocol": (
            "SciCode official sequential prompt; cumulative Kimi code context"
        ),
        "dataset": args.dataset,
        "split": args.split,
        "model": args.model,
        "endpoint": args.endpoint,
        "sampling": args.sampling,
        "attempts": result.attempts,
        "finish_reason": result.finish_reason,
        "request_id": result.request_id,
        "provider_model": result.provider_model,
        "status": status,
        "code_extracted": bool(parsed),
        "content_chars": len(raw_content),
        "reasoning_chars": len(result.reasoning_content),
        "prompt_chars": len(prompt),
    }
    if prompt_error:
        metadata["prompt_error"] = prompt_error
    if result.error:
        metadata["error"] = result.error
    record: dict[str, Any] = {
        "id": f"{episode.episode_id}/step-{step_index + 1:02d}",
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": raw_content},
        ],
        "prompt": prompt,
        "completion": raw_content or result.reasoning_content,
        "completion_with_reasoning": completion_with_reasoning,
        "reasoning_content": result.reasoning_content,
        "parsed_code": parsed,
        "context_code": context_code,
        "provider_response": result.raw_response,
        "usage": result.usage,
        "metadata": metadata,
    }
    if result.raw_chunks is not None:
        record["provider_raw_chunks"] = result.raw_chunks
    return record


async def write_records(
    queue: asyncio.Queue[dict[str, Any] | None],
    output_path: Path,
    stats: GenerationStats,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        while True:
            record = await queue.get()
            try:
                if record is None:
                    stream.flush()
                    return
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stats.produced_records += 1
                if record.get("metadata", {}).get("status") not in {
                    "ok",
                    "reasoning_only",
                }:
                    stats.error_records += 1
                stream.flush()
            finally:
                queue.task_done()


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def run_episode(
    plan: EpisodePlan,
    rows: list[dict[str, Any]],
    client: httpx.AsyncClient,
    queue: asyncio.Queue[dict[str, Any] | None],
    args: argparse.Namespace,
    stats: GenerationStats,
    module: Any,
) -> None:
    row = rows[plan.problem_index]
    previous_codes: list[str | None] = [None] * len(row["sub_steps"])
    emitted: set[int] = set()
    try:
        for step_index in plan.step_indices:
            prompt = ""
            context_code = ""
            prompt_error: str | None = None
            try:
                prompt, context_code = prepare_prompt(
                    module,
                    row,
                    step_index,
                    previous_codes,
                    args.with_background,
                )
            except Exception as exc:
                prompt_error = f"{type(exc).__name__}: {str(exc)[:2000]}"
                result = CompletionResult(
                    status="prompt_error",
                    error={"type": type(exc).__name__, "message": str(exc)[:4000]},
                )
            else:
                payload = {
                    "model": args.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": args.temperature,
                    "max_tokens": args.max_tokens,
                    "reasoning_effort": args.reasoning_effort,
                    "stream": args.stream,
                }
                if args.stream and args.include_usage:
                    payload["stream_options"] = {"include_usage": True}
                if args.extra_payload:
                    payload.update(args.extra_payload)
                result = await request_completion(
                    client,
                    args.endpoint,
                    args.headers,
                    payload,
                    args,
                    stats,
                )
            if result.status == "ok":
                previous_codes[step_index] = extract_code(result.content) or (
                    "# No parseable code was returned for this step."
                )
            else:
                previous_codes[step_index] = "# Previous step request failed."
            await queue.put(
                make_record(
                    episode=plan,
                    row=row,
                    step_index=step_index,
                    prompt=prompt,
                    context_code=context_code,
                    result=result,
                    args=args,
                    prompt_error=prompt_error,
                )
            )
            emitted.add(step_index)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Preserve the requested row count if prompt construction or a provider
        # adapter raises unexpectedly after some steps have already been emitted.
        print(
            f"episode {plan.episode_id} failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        for step_index in plan.step_indices:
            if step_index in emitted:
                continue
            fallback = CompletionResult(
                status="episode_error",
                error={"type": type(exc).__name__, "message": str(exc)[:4000]},
            )
            await queue.put(
                make_record(
                    episode=plan,
                    row=row,
                    step_index=step_index,
                    prompt="",
                    context_code="",
                    result=fallback,
                    args=args,
                )
            )
    finally:
        stats.completed_episodes += 1


def parse_extra_payload(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--extra-payload must be a JSON object")
    return parsed


def load_rows(dataset_name: str, split: str) -> list[dict[str, Any]]:
    dataset = load_dataset(dataset_name, split=split)
    return [dict(row) for row in dataset]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="SciCode1/SciCode")
    parser.add_argument("--split", default="test")
    parser.add_argument("--target-records", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("KIMI_BASE_URL", "http://172.17.52.41:5050"),
    )
    parser.add_argument("--model", default=os.environ.get("KIMI_MODEL", "Kimi-K3"))
    parser.add_argument(
        "--api-key",
        default=os.environ.get(
            "KIMI_API_KEY", os.environ.get("OPENAI_API_KEY", "")
        ),
    )
    parser.add_argument(
        "--metrics-url",
        default="http://172.17.52.41:29000/metrics",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-concurrency", type=int, default=500)
    parser.add_argument("--max-concurrency", type=int, default=1536)
    parser.add_argument("--metric-poll-seconds", type=float, default=5.0)
    parser.add_argument("--status-interval", type=float, default=15.0)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--reasoning-effort", default="max")
    parser.add_argument("--max-tokens", type=int, default=65536)
    parser.add_argument("--context-length", type=int, default=1048576)
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-backoff", type=float, default=2.0)
    parser.add_argument("--stream", dest="stream", action="store_true", default=True)
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    parser.add_argument(
        "--include-usage",
        dest="include_usage",
        action="store_true",
        default=True,
        help="Request token usage in the final streaming chunk",
    )
    parser.add_argument(
        "--no-include-usage",
        dest="include_usage",
        action="store_false",
    )
    parser.add_argument("--keep-raw-chunks", action="store_true")
    parser.add_argument("--trust-env", action="store_true")
    parser.add_argument("--extra-payload")
    parser.add_argument(
        "--with-background",
        dest="with_background",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--without-background",
        dest="with_background",
        action="store_false",
    )
    return parser


async def run_generation(args: argparse.Namespace) -> dict[str, Any]:
    if args.min_concurrency <= 0 or args.max_concurrency < args.min_concurrency:
        raise ValueError("concurrency bounds are invalid")
    if args.max_tokens >= args.context_length:
        raise ValueError("--max-tokens must be smaller than --context-length")
    args.extra_payload = parse_extra_payload(args.extra_payload)
    args.endpoint = endpoint_url(args.base_url)
    args.sampling = {
        "temperature": args.temperature,
        "reasoning_effort": args.reasoning_effort,
        "max_tokens": args.max_tokens,
        "context_length": args.context_length,
        "stream": args.stream,
    }
    args.headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if args.api_key:
        args.headers["Authorization"] = f"Bearer {args.api_key}"
    fd_limit = raise_file_descriptor_limit(args.max_concurrency + 256)

    print("loading SciCode dataset...", flush=True)
    rows = load_rows(args.dataset, args.split)
    module = official_module()
    plans = build_episode_plan(rows, args.target_records, args.seed)
    stats = GenerationStats(
        planned_records=args.target_records,
        planned_episodes=len(plans),
    )
    output_path = args.output.resolve()
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    manifest_path = output_path.with_suffix(".manifest.json")
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=512)
    writer_task = asyncio.create_task(write_records(queue, output_path, stats))
    limits = httpx.Limits(
        max_connections=max(args.max_concurrency + 256, 2048),
        max_keepalive_connections=max(args.max_concurrency, 512),
    )
    timeout = httpx.Timeout(
        connect=30.0,
        read=args.timeout,
        write=60.0,
        pool=args.timeout,
    )
    args.request_timeout = timeout
    manifest_base = {
        "schema_version": "scicode-sft-manifest-v1",
        "status": "running",
        "output": str(output_path),
        "dataset": args.dataset,
        "split": args.split,
        "dataset_rows": len(rows),
        "active_subproblems": sum(len(active_indices(row)) for row in rows),
        "target_records": args.target_records,
        "planned_episodes": len(plans),
        "seed": args.seed,
        "model": args.model,
        "endpoint": args.endpoint,
        "sampling": args.sampling,
        "with_background": args.with_background,
        "metrics_url": args.metrics_url,
        "min_concurrency": args.min_concurrency,
        "max_concurrency": args.max_concurrency,
        "timeout": args.timeout,
        "retries": args.retries,
        "fd_limit": fd_limit,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_manifest(manifest_path, manifest_base)
    pending = deque(plans)
    in_flight: set[asyncio.Task[None]] = set()
    scheduled_episodes = 0
    last_metric_at = 0.0
    last_status_at = 0.0
    metrics_warning: str | None = None

    try:
        async with httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            trust_env=args.trust_env,
        ) as client:
            while pending or in_flight:
                now = time.time()
                if now - last_metric_at >= args.metric_poll_seconds:
                    active, warning = await read_remote_active(
                        client, args.metrics_url, min(15.0, args.timeout)
                    )
                    last_metric_at = now
                    stats.last_remote_active = active
                    metrics_warning = warning
                    stats.metrics_history.append(
                        {
                            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "active": active,
                            "warning": warning,
                            "in_flight": len(in_flight),
                        }
                    )
                    stats.metrics_history = stats.metrics_history[-200:]
                target = target_inflight(
                    stats.last_remote_active,
                    args.min_concurrency,
                    args.max_concurrency,
                )
                slots = max(0, target - len(in_flight))
                if not in_flight and slots == 0:
                    slots = min(args.min_concurrency, len(pending))
                while pending and slots > 0:
                    plan = pending.popleft()
                    task = asyncio.create_task(
                        run_episode(plan, rows, client, queue, args, stats, module)
                    )
                    in_flight.add(task)
                    scheduled_episodes += 1
                    slots -= 1
                if now - last_status_at >= args.status_interval:
                    snap = stats.snapshot(len(in_flight), scheduled_episodes)
                    if metrics_warning:
                        snap["metrics_warning"] = metrics_warning
                    print(json.dumps(snap, ensure_ascii=False), flush=True)
                    last_status_at = now
                    write_manifest(
                        manifest_path,
                        {**manifest_base, **snap, "metrics": stats.metrics_history},
                    )
                if not in_flight:
                    continue
                done, _ = await asyncio.wait(
                    in_flight,
                    timeout=max(0.5, args.metric_poll_seconds),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    in_flight.remove(task)
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        print(
                            f"episode task failed: {type(exc).__name__}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
            await queue.join()
            await queue.put(None)
            await writer_task
    except BaseException:
        for task in in_flight:
            task.cancel()
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        if not writer_task.done():
            await queue.put(None)
        await asyncio.gather(writer_task, return_exceptions=True)
        stats.finished_at = time.time()
        snap = stats.snapshot(len(in_flight), scheduled_episodes)
        write_manifest(
            manifest_path,
            {
                **manifest_base,
                **snap,
                "status": "interrupted",
                "metrics": stats.metrics_history,
                "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        )
        raise

    stats.finished_at = time.time()
    final = stats.snapshot(0, scheduled_episodes)
    final.update(
        {
            **manifest_base,
            **final,
            "status": (
                "finished"
                if stats.produced_records == args.target_records
                else "incomplete"
            ),
            "metrics": stats.metrics_history,
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    )
    write_manifest(manifest_path, final)
    print(json.dumps(final, ensure_ascii=False), flush=True)
    return final


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(run_generation(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
