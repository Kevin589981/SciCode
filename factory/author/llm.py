"""Minimal OpenAI-compatible chat client shared by authoring and solving."""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
import re


class LLMRequestError(RuntimeError):
    """Transport failure with enough information for provider-level recovery."""
    def __init__(self, message, *, status_code=None, retryable=True, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after


def client_config() -> dict:
    base = os.environ.get("SCICODE_LLM_BASE_URL")
    key = os.environ.get("SCICODE_LLM_API_KEY")
    model = os.environ.get("SCICODE_LLM_MODEL")
    missing = [
        n
        for n, v in (
            ("SCICODE_LLM_BASE_URL", base),
            ("SCICODE_LLM_API_KEY", key),
            ("SCICODE_LLM_MODEL", model),
        )
        if not v
    ]
    if missing:
        raise SystemExit(
            "missing solver/authoring endpoint config: "
            + ", ".join(missing)
            + "  (OpenAI-compatible endpoint)"
        )
    return {"base_url": base.rstrip("/"), "api_key": key, "model": model}


def _stream_response(response, *, allow_partial: bool) -> dict:
    """Assemble SSE chunks, including native reasoning and final usage."""
    fragments = {"content": [], "reasoning_content": []}
    usage = {}
    finish_reason = None
    response_id = None

    def assembled(reason: str) -> dict:
        return {
            "id": response_id,
            "choices": [{"index": 0, "message": {
                "role": "assistant", **{field: "".join(parts) for field, parts in fragments.items()},
            }, "finish_reason": reason}],
            "usage": usage,
        }

    try:
        for raw_line in response:
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            response_id = chunk.get("id") or response_id
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                for field in ("content", "reasoning_content"):
                    value = delta.get(field)
                    if isinstance(value, str) and value:
                        fragments[field].append(value)
                finish_reason = choice.get("finish_reason") or finish_reason
    except (OSError, TimeoutError, ValueError):
        if allow_partial and any(fragments.values()):
            return assembled("stream_interrupted")
        raise
    if finish_reason:
        return assembled(finish_reason)
    if allow_partial and any(fragments.values()):
        return assembled("stream_interrupted")
    raise RuntimeError("stream ended without a finish_reason or usable content")


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    retries: int = 3,
    timeout: int = 900,
    allow_partial: bool = False,
    extra_body: dict | None = None,
    client: dict | None = None,
) -> dict:
    """Return a complete response, or a marked partial solver response."""
    # An explicit client keeps concurrently running teachers/reviewers isolated;
    # credentials stay in memory and are never included in returned responses.
    cfg = client if client is not None else client_config()
    url = cfg["base_url"] + "/chat/completions"
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if extra_body:
        if set(extra_body) - {"thinking", "reasoning_effort"}:
            raise ValueError("unsupported provider options; cannot override core request fields")
        payload.update(extra_body)
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg['api_key']}",
                },
            )
            # urllib's timeout applies to each blocking read. Respect the
            # caller's idle timeout: a heavily loaded reasoning service may
            # pause longer than ten minutes before its first SSE chunk.
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if "text/event-stream" in resp.headers.get("Content-Type", ""):
                    return _stream_response(resp, allow_partial=allow_partial)
                return json.loads(resp.read().decode())
        except Exception as e:  # network/5xx -> retry with backoff
            last = e
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    status = getattr(last, "code", None)
    detail = str(last)
    retry_after = None
    if isinstance(last, urllib.error.HTTPError):
        try:
            detail += "; " + last.read(4000).decode("utf-8", errors="replace")
            retry_after = float(last.headers.get("Retry-After", "0")) or None
        except (OSError, TypeError, ValueError):
            pass
    detail = detail.replace(cfg["api_key"], "[REDACTED]")
    detail = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", detail)
    raise LLMRequestError(f"LLM request failed after {retries} tries: {detail}",
        status_code=status, retryable=status is None or status in {408, 409, 425, 429} or status >= 500,
        retry_after=retry_after) from last


def assistant_message(resp: dict) -> dict:
    """Normalize a chat.completions response to a messages-entry dict."""
    choice = resp["choices"][0]
    msg = choice["message"]
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    if msg.get("reasoning_content"):
        out["reasoning_content"] = msg["reasoning_content"]
    return out


def usage_of(resp: dict) -> dict:
    return resp.get("usage") or {}
