#!/usr/bin/env python3
"""Generate SciCode-shaped problem queries directly from a chat endpoint.

This producer intentionally does not start an authoring agent.  It only turns
one model response into one canonical top-level SciCode problem record and
keeps the provider response, usage, and failures in separate JSONL files.
Scientific review, reference implementations, private tests, oracle creation,
and answer/trace generation are later stages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, urlsplit, urlunsplit
from urllib.request import Request, urlopen


TOP_LEVEL_FIELDS = (
    "problem_name",
    "problem_id",
    "problem_description_main",
    "problem_io",
    "required_dependencies",
    "sub_steps",
    "general_tests",
    "problem_background_main",
)
STEP_FIELDS = (
    "step_number",
    "step_description_prompt",
    "function_header",
    "test_cases",
    "return_line",
    "step_background",
)
ID_RE = re.compile(r"^(?P<prefix>[a-z0-9][a-z0-9_-]*)-(?P<index>[0-9]+)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_endpoint(value: str) -> str:
    """Return an endpoint identity with user info and query parameters removed."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-endpoint>"
    if not parsed.scheme or not parsed.hostname:
        return "<invalid-endpoint>"
    try:
        port = parsed.port
    except ValueError:
        return "<invalid-endpoint>"
    netloc = parsed.hostname
    if port:
        netloc += f":{port}"
    cleaned = SplitResult(parsed.scheme, netloc, parsed.path, "", "")
    return urlunsplit(cleaned)


def endpoint_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    return f"{value}/chat/completions"


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from plain, fenced, or trailing-text output."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("model response has no text content")
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("model response does not contain a JSON object")


def _string_field(value: Any, field_name: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    # Accept a single string as a small transport normalization; the emitted
    # record always uses the list type used by the original SciCode dataset.
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string")
        result.append(item)
    return result


def normalize_query(value: Mapping[str, Any], query_id: str) -> dict[str, Any]:
    """Project a model object onto the original SciCode problem schema.

    This function intentionally performs no scientific, difficulty, duplicate,
    or answer-correctness checks.  Its only job is to produce the field shape
    consumed by the existing SciCode adapter.  Unknown authoring fields are
    omitted, and the controller-owned id is used for stable resume behavior.
    """
    if not isinstance(value, Mapping):
        raise ValueError("query must be a JSON object")
    missing = [
        name
        for name in TOP_LEVEL_FIELDS
        if name != "problem_id" and name not in value
    ]
    if missing:
        raise ValueError(f"missing top-level field(s): {', '.join(missing)}")

    result: dict[str, Any] = {
        "problem_name": _string_field(value["problem_name"], "problem_name", allow_empty=False),
        "problem_id": query_id,
        "problem_description_main": _string_field(
            value["problem_description_main"], "problem_description_main", allow_empty=False
        ),
        "problem_io": _string_field(value["problem_io"], "problem_io"),
        "required_dependencies": _string_field(
            value["required_dependencies"], "required_dependencies"
        ),
        "general_tests": _string_list(value["general_tests"], "general_tests"),
        "problem_background_main": _string_field(
            value["problem_background_main"], "problem_background_main"
        ),
    }

    sub_steps = value["sub_steps"]
    if not isinstance(sub_steps, list) or not sub_steps:
        raise ValueError("sub_steps must be a non-empty list")
    normalized_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(sub_steps, start=1):
        if not isinstance(raw_step, Mapping):
            raise ValueError(f"sub_steps[{index - 1}] must be an object")
        missing_step = [name for name in STEP_FIELDS if name not in raw_step]
        if missing_step:
            raise ValueError(
                f"sub_steps[{index - 1}] missing field(s): {', '.join(missing_step)}"
            )
        normalized_steps.append(
            {
                "step_number": f"{query_id}.{index}",
                "step_description_prompt": _string_field(
                    raw_step["step_description_prompt"],
                    f"sub_steps[{index - 1}].step_description_prompt",
                    allow_empty=False,
                ),
                "function_header": _string_field(
                    raw_step["function_header"],
                    f"sub_steps[{index - 1}].function_header",
                    allow_empty=False,
                ),
                "test_cases": _string_list(
                    raw_step["test_cases"], f"sub_steps[{index - 1}].test_cases"
                ),
                "return_line": _string_field(
                    raw_step["return_line"],
                    f"sub_steps[{index - 1}].return_line",
                ),
                "step_background": _string_field(
                    raw_step["step_background"],
                    f"sub_steps[{index - 1}].step_background",
                ),
            }
        )
    result["sub_steps"] = normalized_steps
    return result


def build_generation_prompt(query_id: str, seed: str) -> str:
    """Build the deliberately small model-side query request."""
    fields = ", ".join(TOP_LEVEL_FIELDS)
    step_fields = ", ".join(STEP_FIELDS)
    return f"""Invent one original scientific programming problem in the multi-step SciCode format.

Return exactly one JSON object and no surrounding explanation or markdown. The
top-level object must contain these fields: {fields}. Each object in `sub_steps`
must contain these fields: {step_fields}. Use the requested id `{query_id}` as
the problem id and number the ordered steps from 1. Keep all descriptions,
function headers, test-case snippets, and backgrounds inside the JSON strings.

Creative seed: {seed}
"""


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = True,
        raw_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.raw_text = raw_text


@dataclass(frozen=True)
class ProviderResponse:
    body: Any
    raw_text: str
    status_code: int = 200
    elapsed_seconds: float = 0.0


class OpenAICompatibleClient:
    """Small dependency-free client for a chat-completions endpoint."""

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.endpoint = endpoint_url(base_url)
        self.api_key = api_key or ""

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        extra_body: Mapping[str, Any] | None = None,
    ) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }
        if extra_body:
            payload.update(dict(extra_body))
            payload["model"] = model
            payload["messages"] = [{"role": "user", "content": prompt}]
            payload["stream"] = False
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200))
                raw_text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raw_text = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code in {408, 409, 425, 429} or exc.code >= 500
            raise ProviderError(
                f"provider returned HTTP {exc.code}: {raw_text[:500]}",
                status_code=exc.code,
                retryable=retryable,
                raw_text=raw_text,
            ) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise ProviderError(f"provider request failed: {exc}", retryable=True) from exc
        elapsed = time.monotonic() - started
        try:
            body = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"provider returned non-JSON response: {raw_text[:500]}",
                status_code=status_code,
                retryable=True,
                raw_text=raw_text,
            ) from exc
        return ProviderResponse(body, raw_text, status_code, elapsed)


def _response_content(body: Any) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, Mapping):
        return json.dumps(body, ensure_ascii=False)
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("provider response has no choices")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("provider choice is not an object")
    message = choice.get("message")
    content: Any = message.get("content") if isinstance(message, Mapping) else choice.get("text")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        content = "".join(parts)
    if not isinstance(content, str):
        raise ValueError("provider response has no assistant content")
    return content


def _response_usage(body: Any) -> Any:
    return body.get("usage") if isinstance(body, Mapping) else None


def _response_reasoning(body: Any) -> Any:
    if not isinstance(body, Mapping) or not isinstance(body.get("choices"), list):
        return None
    if not body["choices"] or not isinstance(body["choices"][0], Mapping):
        return None
    choice = body["choices"][0]
    message = choice.get("message")
    if isinstance(message, Mapping):
        return message.get("reasoning_content") or message.get("reasoning")
    return choice.get("reasoning_content") or choice.get("reasoning")


def _response_finish_reason(body: Any) -> Any:
    if not isinstance(body, Mapping) or not isinstance(body.get("choices"), list):
        return None
    if not body["choices"] or not isinstance(body["choices"][0], Mapping):
        return None
    return body["choices"][0].get("finish_reason")


@dataclass
class GeneratorConfig:
    target_queries: int
    output_dir: Path
    model: str
    base_url: str = ""
    api_key: str = ""
    concurrency: int = 1
    temperature: float = 1.0
    max_tokens: int = 32768
    timeout_seconds: float = 1800.0
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    max_attempts: int | None = None
    id_prefix: str = "direct"
    extra_body: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.target_queries < 1:
            raise ValueError("target_queries must be positive")
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("max_attempts must be positive when supplied")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.id_prefix):
            raise ValueError("id_prefix must contain lowercase path-safe characters")
        if not self.model.strip():
            raise ValueError("model must not be empty")


class QueryGenerator:
    def __init__(
        self,
        config: GeneratorConfig,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        config.validate()
        self.config = config
        self.config.output_dir = Path(config.output_dir)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or OpenAICompatibleClient(config.base_url, config.api_key)
        self.sleep = sleep
        self._write_lock = threading.Lock()
        self.paths = {
            name: self.config.output_dir / name
            for name in ("queries.jsonl", "responses.jsonl", "rejected.jsonl")
        }
        self.manifest_path = self.config.output_dir / "manifest.json"

    def _new_id(self, index: int) -> str:
        return f"{self.config.id_prefix}-{index:06d}"

    def _existing_ids(self) -> tuple[set[str], int, int, int]:
        accepted: set[str] = set()
        largest = 0
        attempted_ids: set[str] = set()
        rejected_ids: set[str] = set()
        for path in self.paths.values():
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                query_id = value.get("problem_id") or value.get("query_id")
                if not isinstance(query_id, str):
                    continue
                match = ID_RE.fullmatch(query_id)
                if not match or match.group("prefix") != self.config.id_prefix:
                    continue
                largest = max(largest, int(match.group("index")))
                if path == self.paths["queries.jsonl"]:
                    accepted.add(query_id)
                elif path == self.paths["responses.jsonl"]:
                    attempted_ids.add(query_id)
                    if value.get("accepted") is False:
                        rejected_ids.add(query_id)
                else:
                    attempted_ids.add(query_id)
                    rejected_ids.add(query_id)
        return accepted, largest, len(attempted_ids), len(rejected_ids)

    def _append(self, path: Path, value: Mapping[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()

    def _write_manifest(self, value: Mapping[str, Any]) -> None:
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.manifest_path)

    def _manifest(
        self,
        *,
        status: str,
        started_at: str,
        accepted: int,
        rejected: int,
        attempted: int,
        next_index: int,
        error: str | None = None,
    ) -> dict[str, Any]:
        config = asdict(self.config)
        config["output_dir"] = str(self.config.output_dir)
        config.pop("api_key", None)
        config["base_url"] = safe_endpoint(self.config.base_url)
        value: dict[str, Any] = {
            "schema": "scicode-direct-query-generation-v1",
            "status": status,
            "started_at": started_at,
            "updated_at": utc_now(),
            "target_queries": self.config.target_queries,
            "accepted_queries": accepted,
            "rejected_queries": rejected,
            "attempted_query_ids": attempted,
            "next_index": next_index,
            "config": config,
        }
        if error:
            value["error"] = error
        return value

    def _coerce_response(self, value: Any) -> ProviderResponse:
        if isinstance(value, ProviderResponse):
            return value
        if isinstance(value, str):
            return ProviderResponse(value, value)
        return ProviderResponse(value, json.dumps(value, ensure_ascii=False))

    def _generate_one(self, query_id: str) -> dict[str, Any]:
        seed = f"{self.config.id_prefix}:{query_id}"
        prompt = build_generation_prompt(query_id, seed)
        history: list[dict[str, Any]] = []
        started = time.monotonic()
        for attempt in range(1, self.config.max_retries + 2):
            attempt_started = time.monotonic()
            try:
                response = self._coerce_response(
                    self.client.complete(
                        prompt,
                        model=self.config.model,
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
                        timeout_seconds=self.config.timeout_seconds,
                        extra_body=self.config.extra_body,
                    )
                )
                body = response.body
                content = _response_content(body)
                usage = _response_usage(body)
                reasoning_content = _response_reasoning(body)
                finish_reason = _response_finish_reason(body)
                attempt_record: dict[str, Any] = {
                    "attempt": attempt,
                    "elapsed_seconds": response.elapsed_seconds
                    or time.monotonic() - attempt_started,
                    "status_code": response.status_code,
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "reasoning_content": reasoning_content,
                    "content": content,
                    "raw_response": body,
                }
                try:
                    parsed = normalize_query(extract_json_object(content), query_id)
                except (TypeError, ValueError) as exc:
                    attempt_record["error"] = str(exc)
                    history.append(attempt_record)
                    if attempt < self.config.max_retries + 1:
                        self._backoff(attempt)
                        continue
                    return self._result_row(
                        query_id,
                        prompt,
                        history,
                        accepted=False,
                        error=str(exc),
                        elapsed_seconds=time.monotonic() - started,
                    )
                history.append(attempt_record)
                return self._result_row(
                    query_id,
                    prompt,
                    history,
                    accepted=True,
                    query=parsed,
                    elapsed_seconds=time.monotonic() - started,
                )
            except ProviderError as exc:
                history.append(
                    {
                        "attempt": attempt,
                        "elapsed_seconds": time.monotonic() - attempt_started,
                        "status_code": exc.status_code,
                        "error": str(exc),
                        "raw_response": exc.raw_text,
                    }
                )
                if exc.retryable and attempt < self.config.max_retries + 1:
                    self._backoff(attempt)
                    continue
                return self._result_row(
                    query_id,
                    prompt,
                    history,
                    accepted=False,
                    error=str(exc),
                    elapsed_seconds=time.monotonic() - started,
                )
            except (TypeError, ValueError) as exc:
                history.append(
                    {
                        "attempt": attempt,
                        "elapsed_seconds": time.monotonic() - attempt_started,
                        "error": str(exc),
                    }
                )
                if attempt < self.config.max_retries + 1:
                    self._backoff(attempt)
                    continue
                return self._result_row(
                    query_id,
                    prompt,
                    history,
                    accepted=False,
                    error=str(exc),
                    elapsed_seconds=time.monotonic() - started,
                )
        raise AssertionError("generation loop ended without a result")

    def _backoff(self, attempt: int) -> None:
        delay = self.config.retry_backoff_seconds * (2 ** max(attempt - 1, 0))
        if delay:
            self.sleep(delay)

    def _result_row(
        self,
        query_id: str,
        prompt: str,
        history: list[dict[str, Any]],
        *,
        accepted: bool,
        elapsed_seconds: float,
        query: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        final = history[-1] if history else {}
        row: dict[str, Any] = {
            "schema": "scicode-direct-query-response-v1",
            "query_id": query_id,
            "accepted": accepted,
            "model": self.config.model,
            "endpoint": safe_endpoint(self.config.base_url),
            "prompt": prompt,
            "attempt_count": len(history),
            "attempts": history,
            "finish_reason": final.get("finish_reason"),
            "usage": final.get("usage"),
            "reasoning_content": final.get("reasoning_content"),
            "content": final.get("content"),
            "raw_response": final.get("raw_response"),
            "elapsed_seconds": elapsed_seconds,
        }
        if query is not None:
            row["query"] = query
        if error:
            row["error"] = error
        return row

    def run(self) -> dict[str, Any]:
        started_at = utc_now()
        accepted_ids, largest_index, attempted, rejected = self._existing_ids()
        accepted = len(accepted_ids)
        next_index = largest_index + 1
        max_attempts = self.config.max_attempts
        self._write_manifest(
            self._manifest(
                status="running",
                started_at=started_at,
                accepted=accepted,
                rejected=rejected,
                attempted=attempted,
                next_index=next_index,
            )
        )
        if accepted >= self.config.target_queries:
            result = self._manifest(
                status="finished",
                started_at=started_at,
                accepted=accepted,
                rejected=rejected,
                attempted=attempted,
                next_index=next_index,
            )
            self._write_manifest(result)
            return result

        executor = ThreadPoolExecutor(max_workers=self.config.concurrency)
        futures: dict[Future[dict[str, Any]], str] = {}
        try:
            while accepted < self.config.target_queries:
                while len(futures) < self.config.concurrency:
                    if max_attempts is not None and attempted + len(futures) >= max_attempts:
                        break
                    query_id = self._new_id(next_index)
                    next_index += 1
                    futures[executor.submit(self._generate_one, query_id)] = query_id
                if not futures:
                    break
                completed = next(as_completed(futures))
                query_id = futures.pop(completed)
                attempted += 1
                try:
                    row = completed.result()
                except Exception as exc:  # Keep one worker failure auditable.
                    row = self._result_row(
                        query_id,
                        build_generation_prompt(query_id, f"{self.config.id_prefix}:{query_id}"),
                        [],
                        accepted=False,
                        error=f"worker failure: {exc}",
                        elapsed_seconds=0.0,
                    )
                self._append(self.paths["responses.jsonl"], row)
                if row.get("accepted") and isinstance(row.get("query"), Mapping):
                    query = dict(row["query"])
                    self._append(self.paths["queries.jsonl"], query)
                    accepted_ids.add(query_id)
                    accepted += 1
                else:
                    self._append(self.paths["rejected.jsonl"], row)
                    rejected += 1
                self._write_manifest(
                    self._manifest(
                        status="running",
                        started_at=started_at,
                        accepted=accepted,
                        rejected=rejected,
                        attempted=attempted,
                        next_index=next_index,
                    )
                )
            status = "finished" if accepted >= self.config.target_queries else "incomplete"
            result = self._manifest(
                status=status,
                started_at=started_at,
                accepted=accepted,
                rejected=rejected,
                attempted=attempted,
                next_index=next_index,
            )
            self._write_manifest(result)
            return result
        except KeyboardInterrupt:
            result = self._manifest(
                status="stopped",
                started_at=started_at,
                accepted=accepted,
                rejected=rejected,
                attempted=attempted,
                next_index=next_index,
            )
            self._write_manifest(result)
            return result
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-queries", type=int, default=10_000)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=Path("query_generation"))
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("MODEL", ""))
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument("--id-prefix", default="direct")
    parser.add_argument(
        "--extra-body",
        help="JSON object merged into each request (for provider-specific options)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.base_url:
        print("--base-url or BASE_URL is required", file=sys.stderr)
        return 2
    if not args.model:
        print("--model or MODEL is required", file=sys.stderr)
        return 2
    try:
        extra_body = json.loads(args.extra_body) if args.extra_body else {}
        if not isinstance(extra_body, dict):
            raise ValueError("--extra-body must be a JSON object")
        config = GeneratorConfig(
            target_queries=args.target_queries,
            output_dir=args.output_dir,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            concurrency=args.concurrency,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
            max_attempts=args.max_attempts,
            id_prefix=args.id_prefix,
            extra_body=extra_body,
        )
        result = QueryGenerator(config).run()
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "finished" else 2


if __name__ == "__main__":
    raise SystemExit(main())
