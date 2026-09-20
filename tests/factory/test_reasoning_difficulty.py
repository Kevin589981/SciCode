import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.difficulty import (
    DifficultyError,
    assess_trace,
    load_panel,
    run_panel,
    summarize_difficulty,
)
from factory.reasoning.schema import canonical_hash
from tests.factory_fixtures import task_for, trace_for


def assessment_response(*, solved=True):
    score = 4 if solved else 1
    body = {
        "scores": {
            "scientific_correctness": score,
            "constraint_satisfaction": score,
            "reasoning_completeness": score,
            "final_answer_adequacy": score,
        },
        "critical_errors": [] if solved else ["The governing relation is wrong."],
        "rationale": "The response satisfies the scientific task."
        if solved
        else "It fails.",
    }
    return {"choices": [{"message": {"content": json.dumps(body)}}]}


def panel_for():
    return {
        "schema_version": "scicode-difficulty-panel-v1",
        "solvers": [
            {"name": "small-a", "model": "model-a", "attempts": 2},
            {"name": "small-b", "model": "model-b", "attempts": 2},
        ],
        "evaluator_model": "evaluator",
        "min_distinct_models": 2,
        "min_trials": 4,
    }


def traces_for_panel(task):
    rows = []
    for model in ("model-a", "model-b"):
        for attempt in range(2):
            trace = trace_for(task)
            trace["model"] = model
            trace["attempt"] = attempt
            trace["trace_id"] = f"{task['task_id']}::{model}::{attempt}"
            rows.append(trace)
    return rows


class ReasoningDifficultyTests(unittest.TestCase):
    def test_solution_assessment_is_separate_from_training_value(self):
        row = assess_trace(
            task_for(),
            trace_for(),
            chat_fn=lambda *_a, **_k: assessment_response(solved=False),
            model="evaluator",
        )
        self.assertFalse(row["solved"])
        self.assertFalse(row["partial"])
        self.assertEqual(row["solver_model"], "fake-solver")

    def test_zero_solve_rate_is_unresolved_not_automatically_hard(self):
        task = task_for()
        traces = traces_for_panel(task)
        assessments = [
            {
                "trace_id": trace["trace_id"],
                "solver_model": trace["model"],
                "solved": False,
                "partial": False,
            }
            for trace in traces
        ]
        row = summarize_difficulty([task], traces, assessments, panel_for())[0]
        self.assertTrue(row["calibrated"])
        self.assertEqual(row["band"], "unresolved")
        self.assertEqual(row["solved_rate"], 0)

    def test_one_model_cannot_claim_calibrated_difficulty(self):
        task = task_for()
        traces = traces_for_panel(task)[:2]
        assessments = [
            {
                "trace_id": trace["trace_id"],
                "solver_model": trace["model"],
                "solved": True,
                "partial": False,
            }
            for trace in traces
        ]
        row = summarize_difficulty([task], traces, assessments, panel_for())[0]
        self.assertFalse(row["calibrated"])
        self.assertEqual(row["band"], "uncalibrated")

    def test_offline_panel_runs_multiple_named_models_and_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tasks = root / "tasks.jsonl"
            panel_path = root / "panel.json"
            output = root / "difficulty"
            task = task_for()
            tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
            panel_path.write_text(json.dumps(panel_for()), encoding="utf-8")
            calls = []

            def chat(messages, **kwargs):
                calls.append(kwargs["model"])
                if "DIFFICULTY measurement" in messages[0]["content"]:
                    return assessment_response(solved=True)
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "reasoning_content": "Derive, compare, and verify the regimes.",
                                "content": "The bounded relation is implemented correctly.",
                            }
                        }
                    ]
                }

            first = run_panel(tasks, panel_path, output, chat_fn=chat, concurrency=2)
            call_count = len(calls)
            second = run_panel(tasks, panel_path, output, chat_fn=chat, concurrency=2)
            self.assertEqual(first["bands"], {"too_easy": 1})
            self.assertEqual(first["calibrated"], 1)
            self.assertEqual(len(calls), call_count)
            self.assertEqual(second["assessments"]["skipped"], 4)
            row = json.loads((output / "difficulty.jsonl").read_text().strip())
            self.assertEqual(row["task_hash"], canonical_hash(task))

    def test_panel_rejects_duplicate_model_identity(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "panel.json"
            panel = panel_for()
            panel["solvers"][1]["model"] = panel["solvers"][0]["model"]
            path.write_text(json.dumps(panel), encoding="utf-8")
            with self.assertRaisesRegex(DifficultyError, "unique"):
                load_panel(path)


if __name__ == "__main__":
    unittest.main()
