import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.verify import (
    VerificationError,
    run_verification,
    verification_id_for,
    verify_task,
)
from tests.factory_fixtures import task_for


def verifier_response(*, quote_two="return x / (1 + abs(x))", fatal=None):
    body = {
        "scores": {
            "scientific_validity": 4,
            "source_grounding": 4,
            "answerability": 4,
            "constraint_consistency": 4,
            "shortcut_resistance": 3,
        },
        "fatal_issues": fatal or [],
        "missing_inputs": [],
        "answer_exposed": False,
        "evidence": [
            {
                "claim": "The requested symbol exists.",
                "source_quote": "def stable_demo(x):",
                "assessment": "supports",
            },
            {
                "claim": "The source implements a bounded transformation.",
                "source_quote": quote_two,
                "assessment": "supports",
            },
        ],
        "rationale": "The task is answerable and grounded in the source.",
    }
    return {
        "choices": [{"message": {"content": json.dumps(body)}}],
        "usage": {"completion_tokens": 100},
    }


class ReasoningVerificationTests(unittest.TestCase):
    def test_accepts_two_exact_source_quotes(self):
        task = task_for()
        record = verify_task(
            task,
            chat_fn=lambda *_a, **_k: verifier_response(),
            model="verifier-a",
        )
        self.assertTrue(record["accepted"])
        self.assertEqual(
            record["verification_id"], verification_id_for(task, "verifier-a")
        )
        self.assertTrue(all(item["quote_grounded"] for item in record["evidence"]))

    def test_rejects_hallucinated_source_quote_deterministically(self):
        with self.assertRaisesRegex(VerificationError, "supporting quotes"):
            verify_task(
                task_for(),
                chat_fn=lambda *_a, **_k: verifier_response(
                    quote_two="this text is absent from the source"
                ),
                model="verifier-a",
            )

    def test_resume_key_includes_verifier_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks = root / "tasks.jsonl"
            output = root / "verification.jsonl"
            tasks.write_text(json.dumps(task_for()) + "\n", encoding="utf-8")
            calls = []

            def chat(_messages, **kwargs):
                calls.append(kwargs["model"])
                return verifier_response()

            first = run_verification(tasks, output, chat_fn=chat, model="verifier-a")
            second = run_verification(tasks, output, chat_fn=chat, model="verifier-a")
            third = run_verification(tasks, output, chat_fn=chat, model="verifier-b")
            self.assertEqual(first["accepted"], 1)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(third["accepted"], 1)
            self.assertEqual(calls, ["verifier-a", "verifier-b"])


if __name__ == "__main__":
    unittest.main()
