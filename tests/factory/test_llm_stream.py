"""The deployed Kimi endpoint streams native reasoning before its answer."""

import io
import json
import os
import unittest
from unittest.mock import patch

from factory.author import llm


def event(delta=None, *, finish_reason=None, usage=None):
    return (
        "data: "
        + json.dumps(
            {
                "id": "response-1",
                "choices": (
                    [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]
                    if delta is not None or finish_reason is not None
                    else []
                ),
                "usage": usage,
            }
        )
        + "\n\n"
    ).encode()


class FakeResponse(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


class InterruptedResponse(FakeResponse):
    def __iter__(self):
        yield event({"reasoning_content": "A useful derivation."})
        raise TimeoutError("socket stalled")


class StreamingLLMTests(unittest.TestCase):
    def test_stream_keeps_reasoning_answer_usage_and_finish_reason(self):
        body = b"".join(
            [
                event({"role": "assistant", "reasoning_content": "derive "}),
                event({"reasoning_content": "then check", "content": "result"}),
                event(finish_reason="stop"),
                event(usage={"completion_tokens": 42, "reasoning_tokens": 30}),
                b"data: [DONE]\n\n",
            ]
        )
        with patch.dict(
            os.environ,
            {
                "SCICODE_LLM_BASE_URL": "http://example.test/v1",
                "SCICODE_LLM_API_KEY": "dummy",
                "SCICODE_LLM_MODEL": "Kimi-K3",
            },
        ), patch.object(
            llm.urllib.request, "urlopen", return_value=FakeResponse(body)
        ) as call:
            result = llm.chat([{"role": "user", "content": "test"}], timeout=7200)
        self.assertTrue(json.loads(call.call_args.args[0].data)["stream"])
        self.assertEqual(call.call_args.kwargs["timeout"], 600)
        self.assertEqual(
            result["choices"][0]["message"]["reasoning_content"], "derive then check"
        )
        self.assertEqual(result["choices"][0]["message"]["content"], "result")
        self.assertEqual(result["choices"][0]["finish_reason"], "stop")
        self.assertEqual(result["usage"]["reasoning_tokens"], 30)

    def test_interrupted_solver_stream_returns_marked_partial_reasoning(self):
        with patch.dict(
            os.environ,
            {
                "SCICODE_LLM_BASE_URL": "http://example.test/v1",
                "SCICODE_LLM_API_KEY": "dummy",
                "SCICODE_LLM_MODEL": "Kimi-K3",
            },
        ), patch.object(
            llm.urllib.request, "urlopen", return_value=InterruptedResponse()
        ):
            result = llm.chat([], allow_partial=True)
        self.assertEqual(result["choices"][0]["finish_reason"], "stream_interrupted")
        self.assertEqual(
            result["choices"][0]["message"]["reasoning_content"], "A useful derivation."
        )

    def test_complete_finish_reason_survives_missing_done_marker(self):
        body = event({"content": "answer"}) + event(finish_reason="stop")
        result = llm._stream_response(FakeResponse(body), allow_partial=False)
        self.assertEqual(result["choices"][0]["finish_reason"], "stop")

    def test_role_endpoint_and_sampling_fields_do_not_require_global_config(self):
        body = event({"reasoning_content": "own thought", "content": "answer"})
        body += event(finish_reason="stop")
        with patch.dict(os.environ, {}, clear=True), patch.object(
            llm.urllib.request, "urlopen", return_value=FakeResponse(body)
        ) as call:
            result = llm.chat(
                [{"role": "user", "content": "natural question"}],
                model="Qwen-base",
                base_url="http://qwen.internal/v1",
                api_key="test-key",
                temperature=0.9,
                top_p=0.85,
                seed=123,
            )
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://qwen.internal/v1/chat/completions")
        self.assertEqual(payload["temperature"], 0.9)
        self.assertEqual(payload["top_p"], 0.85)
        self.assertEqual(payload["seed"], 123)
        self.assertEqual(result["choices"][0]["message"]["reasoning_content"],
                         "own thought")


if __name__ == "__main__":
    unittest.main()
