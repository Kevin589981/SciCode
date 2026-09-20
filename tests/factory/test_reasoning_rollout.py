import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.rollout import collect_trace, run_rollouts
from factory.reasoning.schema import canonical_hash, validate_trace
from tests.factory_fixtures import task_for


THINKING = "先推导极限行为，再比较直接形式与稳定形式；失败的断言只作为辅助证据。"
FINAL = "The stable form follows from the derivation.\n```python\ndef stable_demo(x):\n    return x / (1 + abs(x))\n```"


def response():
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "reasoning_content": THINKING,
                "content": FINAL,
            }
        }],
        "usage": {"prompt_tokens": 120, "completion_tokens": 240},
    }


class ReasoningRolloutTests(unittest.TestCase):
    def test_collect_trace_preserves_native_reasoning_and_auxiliary_failure(self):
        task = task_for()

        def fake_chat(messages, **kwargs):
            self.assertIn("Deliverables", messages[-1]["content"])
            return response()

        trace = collect_trace(
            task,
            chat_fn=fake_chat,
            model="Kimi-K3",
            attempt=2,
            max_tokens=16384,
            factory_commit="abc123",
            task_set_hash="set456",
            outcome_fn=lambda _task, _message: {
                "status": "fail",
                "kind": "auxiliary_check",
                "detail": "format mismatch",
            },
        )
        validate_trace(trace)
        assistant = trace["messages"][-1]
        self.assertEqual(assistant["reasoning_content"], THINKING)
        self.assertEqual(assistant["content"], FINAL)
        self.assertEqual(trace["outcome"]["status"], "fail")
        self.assertEqual(trace["task_hash"], canonical_hash(task))
        self.assertEqual(trace["provenance"]["factory_commit"], "abc123")
        self.assertEqual(trace["max_tokens"], 16384)

    def test_outcome_error_does_not_destroy_raw_trace(self):
        def broken_outcome(task, message):
            raise RuntimeError("checker unavailable")

        trace = collect_trace(
            task_for(), chat_fn=lambda *_a, **_k: response(),
            model="solver", outcome_fn=broken_outcome,
        )
        self.assertEqual(trace["outcome"]["status"], "error")
        self.assertIn("checker unavailable", trace["outcome"]["detail"])
        self.assertEqual(trace["messages"][-1]["reasoning_content"], THINKING)

    def test_run_rollouts_resumes_per_task_model_and_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks_path = root / "tasks.jsonl"
            out = root / "traces.jsonl"
            task = task_for()
            tasks_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
            calls = []

            def fake_chat(messages, **kwargs):
                calls.append(kwargs["model"])
                return response()

            first = run_rollouts(
                tasks_path, out, chat_fn=fake_chat, model="solver-a",
                attempts=1, concurrency=1, factory_commit="abc",
            )
            second = run_rollouts(
                tasks_path, out, chat_fn=fake_chat, model="solver-a",
                attempts=1, concurrency=1, factory_commit="abc",
            )
            third = run_rollouts(
                tasks_path, out, chat_fn=fake_chat, model="solver-b",
                attempts=1, concurrency=1, factory_commit="abc",
            )
            rows = [
                json.loads(line)
                for line in out.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(first["written"], 1)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(third["written"], 1)
            self.assertEqual(calls, ["solver-a", "solver-b"])
            self.assertEqual(len({row["trace_id"] for row in rows}), 2)


if __name__ == "__main__":
    unittest.main()
