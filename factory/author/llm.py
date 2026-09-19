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
    missing = [n for n, v in (("SCICODE_LLM_BASE_URL", base),
                              ("SCICODE_LLM_API_KEY", key),
                              ("SCICODE_LLM_MODEL", model)) if not v]
    if missing:
        raise SystemExit(
            "missing solver/authoring endpoint config: "
            + ", ".join(missing)
            + "  (OpenAI-compatible endpoint)")
    return {"base_url": base.rstrip("/"), "api_key": key, "model": model}


def chat(messages: list[dict], *, model: str | None = None,
         temperature: float = 0.0, max_tokens: int = 4096,
         retries: int = 3, timeout: int = 300) -> dict:
    """Return the raw response JSON from a chat.completions call."""
    cfg = client_config()
    url = cfg["base_url"] + "/chat/completions"
    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {cfg['api_key']}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:  # network/5xx -> retry with backoff
            last = e
            time.sleep(2 ** attempt)
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
