import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.grade import (
    GRADE_POLICY_VERSION,
    GradeError,
    judge_trace,
    run_grading,
)
from factory.reasoning.schema import validate_grade
from tests.factory_fixtures import task_for, trace_for


def judge_response(*, strong=True, annotation=True):
    scores = (
        {
            "scientific_validity": 4,
            "causal_coherence": 4,
            "strategy": 3,
            "evidence_use": 3,
            "self_correction": 1,
            "insight_density": 4,
            "degeneracy": 0,
        }
        if strong
        else {
            "scientific_validity": 2,
            "causal_coherence": 2,
            "strategy": 1,
            "evidence_use": 1,
            "self_correction": 0,
            "insight_density": 1,
            "degeneracy": 2,
        }
    )
    annotations = [{
        "message_index": 2,
        "train_reasoning": annotation,
        "train_content": annotation,
        "quality": "good" if strong else "bad",
        "rationale": "Grounded derivation." if strong else "Mechanical response.",
    }]
    return {
        "choices": [{"message": {"content": json.dumps({
            "scores": scores,
            "rationale": "Substantive scientific reasoning." if strong else "Trivial.",
            "message_annotations": annotations,
        })}}],
        "usage": {"completion_tokens": 150},
    }


class ReasoningGradeTests(unittest.TestCase):
    def test_failed_but_coherent_trace_is_trainable(self):
        task = task_for()
        trace = trace_for(task, outcome="fail")
        prompts = []

        def fake_chat(messages, **kwargs):
            prompts.append(messages[0]["content"])
            return judge_response(strong=True)

        grade = judge_trace(task, trace, chat_fn=fake_chat, model="judge")
        validate_grade(grade, trace)
        self.assertTrue(grade["trainable"])
        self.assertIn(trace["messages"][-1]["reasoning_content"], prompts[0])
        self.assertIn('"status": "fail"', prompts[0])

    def test_passing_but_trivial_trace_is_rejected(self):
        task = task_for()
        trace = trace_for(task, outcome="pass")
        grade = judge_trace(
            task,
            trace,
            chat_fn=lambda *_a, **_k: judge_response(strong=False),
            model="judge",
        )
        self.assertFalse(grade["trainable"])
        self.assertEqual(trace["outcome"]["status"], "pass")

    def test_moderately_repetitive_reasoning_only_trace_is_salvaged(self):
        response = judge_response(strong=True)
        body = json.loads(response["choices"][0]["message"]["content"])
        body["scores"]["degeneracy"] = 2
        body["message_annotations"][0]["train_content"] = False
        response["choices"][0]["message"]["content"] = json.dumps(body)
        grade = judge_trace(
            task_for(), trace_for(),
            chat_fn=lambda *_a, **_k: response, model="judge",
        )
        self.assertTrue(grade["trainable"])
        self.assertTrue(grade["message_annotations"][0]["train_reasoning"])
        self.assertFalse(grade["message_annotations"][0]["train_content"])

    def test_invalid_score_is_rejected(self):
        invalid = judge_response()
        body = json.loads(invalid["choices"][0]["message"]["content"])
        body["scores"]["scientific_validity"] = 9
        invalid["choices"][0]["message"]["content"] = json.dumps(body)
        with self.assertRaisesRegex(GradeError, "scientific_validity"):
            judge_trace(
                task_for(), trace_for(),
                chat_fn=lambda *_a, **_k: invalid, model="judge",
            )

    def test_run_grading_resumes_by_trace_and_judge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks_path = root / "tasks.jsonl"
            traces_path = root / "traces.jsonl"
            out = root / "grades.jsonl"
            task = task_for()
            trace = trace_for(task)
            tasks_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
            traces_path.write_text(json.dumps(trace) + "\n", encoding="utf-8")
            calls = []

            def fake_chat(messages, **kwargs):
                calls.append(kwargs["model"])
                return judge_response()

            first = run_grading(
                tasks_path, traces_path, out,
                chat_fn=fake_chat, model="judge-a", concurrency=1,
            )
            stale = json.loads(out.read_text(encoding="utf-8").strip())
            stale["trainable"] = False
            stale.pop("policy_version")
            out.write_text(json.dumps(stale) + "\n", encoding="utf-8")
            second = run_grading(
                tasks_path, traces_path, out,
                chat_fn=fake_chat, model="judge-a", concurrency=1,
            )
            third = run_grading(
                tasks_path, traces_path, out,
                chat_fn=fake_chat, model="judge-b", concurrency=1,
            )
            rows = [
                json.loads(line)
                for line in out.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(first["written"], 1)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(third["written"], 1)
            self.assertEqual(calls, ["judge-a", "judge-b"])
            self.assertEqual(len({row["grade_id"] for row in rows}), 2)
            self.assertTrue(rows[0]["trainable"])
            self.assertEqual(rows[0]["policy_version"], GRADE_POLICY_VERSION)


if __name__ == "__main__":
    unittest.main()
