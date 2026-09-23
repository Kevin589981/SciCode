"""Minimal OpenAI-compatible chat client shared by authoring and solving."""

from __future__ import annotations

import json
import os
import time
import urllib.request


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
    message = {"role": "assistant", "content": "", "reasoning_content": ""}
    usage = {}
    finish_reason = None
    response_id = None

    def assembled(reason: str) -> dict:
        return {
            "id": response_id,
            "choices": [{"index": 0, "message": message, "finish_reason": reason}],
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
                    if isinstance(value, str):
                        message[field] += value
                finish_reason = choice.get("finish_reason") or finish_reason
    except (OSError, TimeoutError, ValueError):
        if allow_partial and (message["content"] or message["reasoning_content"]):
            return assembled("stream_interrupted")
        raise
    if finish_reason:
        return assembled(finish_reason)
    if allow_partial and (message["content"] or message["reasoning_content"]):
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
) -> dict:
    """Return a complete response, or a marked partial solver response."""
    cfg = client_config()
    url = cfg["base_url"] + "/chat/completions"
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
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
            # urllib's timeout applies to each blocking read. SSE permits long
            # reasoning while a silent socket still fails within ten minutes.
            with urllib.request.urlopen(req, timeout=min(timeout, 600)) as resp:
                if "text/event-stream" in resp.headers.get("Content-Type", ""):
                    return _stream_response(resp, allow_partial=allow_partial)
                return json.loads(resp.read().decode())
        except Exception as e:  # network/5xx -> retry with backoff
            last = e
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"LLM request failed after {retries} tries: {last}")


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
