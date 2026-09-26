import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.clean_legacy_sft import (
    CleaningError,
    REVIEW_FILE,
    REVIEW_POLICY,
    _decide,
    _parse_review,
    _precheck,
    _review_prompt,
    finalize,
    finalize_candidate,
    prepare,
)
from factory.reasoning.schema import canonical_hash
from tests.factory_fixtures import task_for


def old_row(task, *, answer="", interrupted=True):
    user = "\n".join(
        [
            task["problem"]["question"],
            task["problem"]["background"],
            *task["deliverable"]["requirements"],
        ]
    )
    return {
        "schema_version": "scicode-reasoning-sft-v1",
        "task_name": task["task_id"],
        "trace_id": "trace-1",
        "task_hash": canonical_hash(task),
        "archetype": task["archetype"],
        "messages": [
            {"role": "system", "content": "scientific", "loss": False},
            {"role": "user", "content": user, "loss": False},
            {
                "role": "assistant",
                "content": answer,
                "reasoning_content": "a useful scientific derivation",
                "reasoning_loss": True,
                "content_loss": True,
                "loss": True,
                "quality": "good",
                "quality_rationale": "old judge",
            },
        ],
        "tools": [],
        "thinking_format": "separate_reasoning_content",
        "outcome": {"status": "not_run", "kind": "auxiliary_check"},
        "termination": {
            "finish_reason": "stream_interrupted" if interrupted else "stop",
            "truncated": interrupted,
            "max_tokens": 16384,
            "usage": {},
        },
        "trace_quality": {"scores": {}, "rationale": "old judge", "judge": {}},
        "provenance": {},
        "automatic_review": {"mode": "single_model"},
    }


class LegacySftCleaningTests(unittest.TestCase):
    def test_empty_or_partial_answer_loss_is_disabled_without_erasing_reasoning(self):
        task = task_for()
        row = old_row(task)
        reasons, repairs = _precheck(row, task)
        self.assertEqual(reasons, [])
        self.assertIn("disable_empty_answer_loss", repairs)
        record = {"trace_id": "trace-1", "raw_sha256": "abc", "source_file": "source", "source_line": 1,
                  "precheck_reasons": [], "repairs": repairs}
        review = {"verdict": {"task_status": "sound", "reasoning_status": "train",
                              "answer_status": "exclude"}}
        cleaned, decision = _decide(row, record, review)
        self.assertEqual(decision["decision"], "keep")
        assistant = cleaned["messages"][-1]
        self.assertTrue(assistant["reasoning_loss"])
        self.assertFalse(assistant["content_loss"])
        self.assertTrue(assistant["loss"])
        self.assertTrue(row["messages"][-1]["content_loss"])

    def test_flawed_task_is_rejected_even_with_good_thinking(self):
        task = task_for()
        row = old_row(task, answer="answer", interrupted=False)
        record = {"trace_id": "trace-1", "raw_sha256": "abc", "source_file": "source", "source_line": 1,
                  "precheck_reasons": [], "repairs": []}
        review = {"verdict": {"task_status": "flawed", "reasoning_status": "train",
                              "answer_status": "train"}}
        cleaned, decision = _decide(row, record, review)
        self.assertIsNone(cleaned)
        self.assertIn("task_flawed", decision["reasons"])

    def test_audited_override_can_reject_a_model_false_negative(self):
        task = task_for()
        row = old_row(task, answer="answer", interrupted=False)
        record = {"trace_id": "trace-1", "raw_sha256": "abc", "source_file": "source", "source_line": 1,
                  "precheck_reasons": [], "repairs": []}
        review = {"verdict": {"task_status": "sound", "reasoning_status": "train",
                              "answer_status": "train"}}
        override = {"action": "reject", "reason_code": "violates_exact_requirement",
                    "explanation": "A concrete counterexample violates an exact task constraint."}
        cleaned, decision = _decide(row, record, review, override)
        self.assertIsNone(cleaned)
        self.assertIn("audited_override:violates_exact_requirement", decision["reasons"])

    def test_review_requires_complete_verdict(self):
        response = {"choices": [{"message": {"content": json.dumps({
            "task_status": "sound", "reasoning_status": "train", "answer_status": "exclude",
            "requirement_checks": [{"requirement": "a", "status": "met", "evidence": "b"}],
            "novel_counterexample": "new edge case checked",
            "checks": ["checked degenerate case"], "issues": [], "summary": "valid",
        })}}]}
        self.assertEqual(_parse_review(response, answer="")["answer_status"], "exclude")

    def test_review_prompt_demands_new_counterexample_and_hard_requirements(self):
        task = task_for()
        row = old_row(task, answer="a possible answer", interrupted=False)
        prompt = _review_prompt(row, {"task_aux": {"archetype_payload": {}, "reasoning_contract": {}}})
        self.assertIn("EVERY hard requirement", prompt)
        self.assertIn("ONE NEW adversarial", prompt)
        self.assertIn('"finish_reason": "stop"', prompt)

    def test_prepare_and_finalize_preserve_original_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            task = task_for()
            row = old_row(task)
            tasks_path = source / "tasks.jsonl"
            sft_path = source / "sft.jsonl"
            tasks_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
            sft_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            original = sft_path.read_bytes()
            db = source / "batch.sqlite3"
            connection = sqlite3.connect(db)
            connection.execute("create table jobs(job_id text,status text,result_json text)")
            connection.execute("insert into jobs values(?,?,?)", ("job-1", "done", json.dumps({
                "status": "complete", "sft": str(sft_path), "sft_rows": 1,
                "artifacts": {"tasks": str(tasks_path)},
            })))
            connection.commit()
            connection.close()
            output = root / "clean"
            manifest = prepare(db, source, output)
            self.assertEqual(manifest["counts"]["rows"], 1)
            self.assertEqual(sft_path.read_bytes(), original)
            candidate = finalize_candidate(output)
            self.assertEqual(candidate["counts"]["keep"], 1)
            self.assertFalse(candidate["scientific_correctness_verified"])
            candidate_row = json.loads((output / "candidate-cleaned.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(candidate_row["messages"][-1]["content_loss"])
            (output / REVIEW_FILE).touch()
            with self.assertRaisesRegex(CleaningError, "semantic review is incomplete"):
                finalize(output)
            self.assertFalse((output / "cleaned.jsonl").exists())
            index = json.loads((output / "index.jsonl").read_text(encoding="utf-8"))
            review = {
                "policy": REVIEW_POLICY, "trace_id": index["trace_id"],
                "raw_sha256": index["raw_sha256"], "model": "Kimi-K3",
                "verdict": {"task_status": "sound", "reasoning_status": "train",
                            "answer_status": "exclude"},
            }
            (output / REVIEW_FILE).write_text(json.dumps(review) + "\n", encoding="utf-8")
            report = finalize(output)
            self.assertEqual(report["counts"]["keep"], 1)
            cleaned = json.loads((output / "cleaned.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(cleaned["messages"][-1]["content_loss"])
            self.assertEqual(sft_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
