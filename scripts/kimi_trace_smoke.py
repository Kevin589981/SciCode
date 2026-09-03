#!/usr/bin/env python3
"""Call an OpenAI-compatible Kimi endpoint and export a SciCode trace.

This is a provider smoke test, not a replacement for the SciCode evaluator.
It keeps the provider-native response in ``raw/provider.jsonl`` and writes
public normalized events plus a manifest through :class:`TraceRecorder`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _endpoint(value: str) -> str:
    value = value.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    return f"{value}/chat/completions"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default="http://117.135.59.14:5050/v1/chat/completions",
        help="OpenAI-compatible chat-completions URL or API base URL",
    )
    parser.add_argument("--model", default="Kimi-K3")
    parser.add_argument(
        "--prompt",
        default="Reply with exactly KIMI_TRACE_OK",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs/kimi-smoke"),
    )
    parser.add_argument("--run-id", default="kimi-smoke-001")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--api-key",
        default=os.environ.get("KIMI_API_KEY", ""),
        help="Optional bearer token; KIMI_API_KEY is used when omitted",
    )
    return parser


def _content_from_chunk(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return delta["content"]
    message = choice.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    return ""


def main() -> int:
    from scicode.trace import TraceRecorder

    args = _parser().parse_args()
    recorder = TraceRecorder(
        args.output_dir,
        run_id=args.run_id,
        candidate_id="provider-smoke",
        task_revision="r1",
        mode="agentic",
        provider="kimi-openai-compatible",
        model=args.model,
        sampling={"temperature": 1.0, "stream": True},
        prompt_version="kimi-trace-smoke-v1",
    )
    recorder.record(
        "prompt",
        {"text": args.prompt, "endpoint": _endpoint(args.endpoint)},
        step_id="smoke",
    )

    payload = json.dumps(
        {
            "model": args.model,
            "temperature": 1.0,
            "stream": True,
            "messages": [{"role": "user", "content": args.prompt}],
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    request = Request(_endpoint(args.endpoint), data=payload, headers=headers, method="POST")

    text_parts: list[str] = []
    chunk_count = 0
    try:
        with urlopen(request, timeout=args.timeout) as response:
            while True:
                line = response.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded.startswith("data:"):
                    continue
                data = decoded[5:].strip()
                if data == "[DONE]":
                    recorder.record_raw({"done": True})
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    recorder.record_raw({"raw_data": data})
                    continue
                if not isinstance(chunk, dict):
                    recorder.record_raw({"data": chunk})
                    continue
                recorder.record_raw(chunk)
                chunk_count += 1
                delta = _content_from_chunk(chunk)
                if delta:
                    text_parts.append(delta)
                    recorder.record(
                        "assistant_delta",
                        {"text": delta},
                        step_id="smoke",
                    )
    except HTTPError as exc:
        recorder.record(
            "provider_error",
            {"status": exc.code, "reason": str(exc.reason)},
            visibility="private",
            step_id="smoke",
        )
        recorder.finalize(verification={"passed": False, "http_status": exc.code})
        print(f"provider returned HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 2
    except URLError as exc:
        recorder.record(
            "provider_error",
            {"reason": str(exc.reason)},
            visibility="private",
            step_id="smoke",
        )
        recorder.finalize(verification={"passed": False, "network_error": str(exc.reason)})
        print(f"provider request failed: {exc.reason}", file=sys.stderr)
        return 2

    answer = "".join(text_parts)
    recorder.record(
        "assistant_message",
        {"text": answer},
        step_id="smoke",
    )
    recorder.record(
        "run_end",
        {"status": "completed", "chunk_count": chunk_count},
        step_id="smoke",
    )
    manifest = recorder.finalize(
        verification={
            "passed": True,
            "http_status": 200,
            "chunk_count": chunk_count,
            "assistant_chars": len(answer),
        }
    )
    visible_path = args.output_dir / "trace" / "public_events.jsonl"
    visible_count = recorder.export_visible(visible_path)
    print(
        json.dumps(
            {
                "status": "ok",
                "model": args.model,
                "assistant_text": answer,
                "chunk_count": chunk_count,
                "visible_event_count": visible_count,
                "events": str(recorder.events_path),
                "manifest": str(recorder.manifest_path),
                "manifest_event_count": manifest["event_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
