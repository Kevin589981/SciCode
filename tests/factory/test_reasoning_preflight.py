import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.preflight import (
    PreflightError,
    critic_task,
    preflight_task,
    run_preflight,
    structural_depth,
)
from factory.reasoning.schema import canonical_hash
from tests.factory_fixtures import task_for


def critic_response(scores=None):
    scores = scores or {
        "scientific_depth": 4,
        "multi_step_dependency": 4,
        "decision_requirement": 3,
        "nontriviality": 4,
    }
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "scores": scores,
                            "missing_inputs": [],
                            "answer_exposed": False,
                            "shallow_failure_mode": None,
                            "rationale": "The task requires dependent scientific decisions.",
                        }
                    )
                }
            }
        ],
        "usage": {"completion_tokens": 80},
    }


class ReasoningPreflightTests(unittest.TestCase):
    def test_structural_depth_accepts_multiple_cognitive_categories(self):
        result = structural_depth(task_for())
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["score"], 3)
        self.assertIn("derive", result["operation_categories"])

    def test_structural_depth_rejects_formally_valid_but_shallow_wording(self):
        task = task_for()
        task["reasoning_contract"]["cognitive_operations"] = [
            "consider the first item",
            "consider the second item",
            "consider the third item",
        ]
        result = structural_depth(task)
        self.assertFalse(result["passed"])
        self.assertTrue(any("cognitive categories" in x for x in result["reasons"]))

    def test_semantic_critic_controls_acceptance_without_outcome(self):
        def fake_chat(messages, **kwargs):
            self.assertNotIn("outcome", messages[0]["content"].lower())
            return critic_response()

        record = preflight_task(task_for(), chat_fn=fake_chat, model="critic")
        self.assertTrue(record["accepted"])
        self.assertNotIn("outcome", record)
        self.assertEqual(record["critic"]["model"], "critic")

    def test_low_depth_critic_rejects_even_if_structurally_valid(self):
        low = {
            "scientific_depth": 1,
            "multi_step_dependency": 1,
            "decision_requirement": 1,
            "nontriviality": 1,
        }

        def fake_chat(messages, **kwargs):
            return critic_response(low)

        record = preflight_task(task_for(), chat_fn=fake_chat, model="critic")
        self.assertFalse(record["accepted"])

    def test_critic_accepts_result_in_native_reasoning_field(self):
        value = critic_response()
        content = value["choices"][0]["message"].pop("content")
        value["choices"][0]["message"]["reasoning_content"] = content
        record = preflight_task(
            task_for(), chat_fn=lambda *_a, **_k: value, model="critic"
        )
        self.assertTrue(record["accepted"])

    def test_malformed_critic_response_raises(self):
        def fake_chat(messages, **kwargs):
            return {"choices": [{"message": {"content": "[]"}}]}

        with self.assertRaises(PreflightError):
            critic_task(task_for(), chat_fn=fake_chat, model="critic")

    def test_run_preflight_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks = root / "tasks.jsonl"
            out = root / "preflight.jsonl"
            task = task_for()
            tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
            calls = []

            def fake_chat(messages, **kwargs):
                calls.append(1)
                return critic_response()

            first = run_preflight(tasks, out, chat_fn=fake_chat, model="critic")
            second = run_preflight(tasks, out, chat_fn=fake_chat, model="critic")
            row = json.loads(out.read_text().strip())
            self.assertEqual(first["accepted"], 1)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(row["task_hash"], canonical_hash(task))

            third = run_preflight(tasks, out, chat_fn=fake_chat, model="critic-b")
            self.assertEqual(third["accepted"], 1)
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
