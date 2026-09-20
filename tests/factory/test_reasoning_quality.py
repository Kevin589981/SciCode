import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.quality import (
    CALIBRATION_CHECKS,
    CALIBRATION_SCHEMA,
    QualityError,
    calibrate_reviews,
    create_audit_packet,
    release_sft,
)
from factory.reasoning.schema import canonical_hash
from tests.factory_fixtures import grade_for, task_for, trace_for


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def artifacts(root):
    task = task_for()
    trace = trace_for(task)
    grade_a = grade_for(trace)
    grade_a["judge"] = {"model": "judge-a"}
    grade_b = grade_for(trace)
    grade_b["judge"] = {"model": "judge-b"}
    verification = {
        "task_hash": trace["task_hash"],
        "accepted": True,
        "verifier": {"model": "verifier-a"},
    }
    difficulty = {
        "task_hash": trace["task_hash"],
        "calibrated": True,
        "band": "medium",
    }
    paths = {
        name: root / name
        for name in (
            "tasks.jsonl",
            "traces.jsonl",
            "grades.jsonl",
            "verification.jsonl",
            "difficulty.jsonl",
            "candidate-sft.jsonl",
        )
    }
    write_jsonl(paths["tasks.jsonl"], [task])
    write_jsonl(paths["traces.jsonl"], [trace])
    write_jsonl(paths["grades.jsonl"], [grade_a, grade_b])
    write_jsonl(paths["verification.jsonl"], [verification])
    write_jsonl(paths["difficulty.jsonl"], [difficulty])
    write_jsonl(
        paths["candidate-sft.jsonl"],
        [{"trace_id": trace["trace_id"], "messages": trace["messages"]}],
    )
    return task, trace, paths


def approved_calibration(population_hash):
    return {
        "schema_version": CALIBRATION_SCHEMA,
        "release_approved": True,
        "population_hash": population_hash,
        "checks": {name: True for name in CALIBRATION_CHECKS},
    }


class ReasoningQualityTests(unittest.TestCase):
    def test_audit_calibration_measures_human_precision_recall_and_agreement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _task, _trace, paths = artifacts(root)
            packet = root / "audit.jsonl"
            report = create_audit_packet(
                paths["tasks.jsonl"],
                paths["traces.jsonl"],
                paths["grades.jsonl"],
                paths["verification.jsonl"],
                paths["difficulty.jsonl"],
                packet,
                sample_size=1,
            )
            row = json.loads(packet.read_text().strip())
            self.assertTrue(row["blinded"])
            self.assertNotIn("automated", row)
            self.assertNotIn("model", row["trace"])
            self.assertNotIn("outcome", row["trace"])
            key = json.loads(
                (root / "audit.jsonl.key.jsonl").read_text().strip()
            )
            self.assertTrue(key["automatic_trainable"])
            for reviewer in ("scientist-a", "scientist-b"):
                row["human_reviews"].append(
                    {
                        "reviewer": reviewer,
                        "task_valid": True,
                        "scientific_depth": 4,
                        "trace_training_value": 4,
                        "critical_error": False,
                        "approve_trace": True,
                        "notes": "Grounded derivation and useful trace.",
                    }
                )
            write_jsonl(packet, [row])
            calibration = calibrate_reviews(
                packet,
                root / "calibration.json",
                min_reviewed_items=1,
                min_reviewers=2,
                min_overlap_items=1,
            )
            self.assertEqual(report["sampled"], 1)
            self.assertTrue(calibration["release_approved"])
            self.assertEqual(calibration["metrics"]["selection_precision"], 1)
            self.assertEqual(calibration["metrics"]["pair_agreement"], 1)

    def test_release_requires_calibration_and_independent_consensus(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _task, trace, paths = artifacts(root)
            population_hash = canonical_hash([trace["trace_id"]])
            calibration = approved_calibration(population_hash)
            calibration_path = root / "calibration.json"
            calibration_path.write_text(json.dumps(calibration), encoding="utf-8")
            report = release_sft(
                paths["tasks.jsonl"],
                paths["traces.jsonl"],
                paths["candidate-sft.jsonl"],
                paths["grades.jsonl"],
                paths["verification.jsonl"],
                paths["difficulty.jsonl"],
                calibration_path,
                root / "release.jsonl",
            )
            self.assertEqual(report["released"], 1)
            released = json.loads((root / "release.jsonl").read_text().strip())
            self.assertEqual(
                released["quality_release"]["judge_models"],
                ["judge-a", "judge-b"],
            )

            calibration["release_approved"] = False
            calibration_path.write_text(json.dumps(calibration), encoding="utf-8")
            with self.assertRaisesRegex(QualityError, "not approved"):
                release_sft(
                    paths["tasks.jsonl"],
                    paths["traces.jsonl"],
                    paths["candidate-sft.jsonl"],
                    paths["grades.jsonl"],
                    paths["verification.jsonl"],
                    paths["difficulty.jsonl"],
                    calibration_path,
                    root / "blocked.jsonl",
                )

    def test_single_judge_and_easy_task_are_rejected_not_silently_released(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _task, trace, paths = artifacts(root)
            grades = [json.loads(paths["grades.jsonl"].read_text().splitlines()[0])]
            write_jsonl(paths["grades.jsonl"], grades)
            difficulty = json.loads(paths["difficulty.jsonl"].read_text().strip())
            difficulty["band"] = "too_easy"
            write_jsonl(paths["difficulty.jsonl"], [difficulty])
            calibration_path = root / "calibration.json"
            calibration_path.write_text(
                json.dumps(
                    approved_calibration(canonical_hash([trace["trace_id"]]))
                ),
                encoding="utf-8",
            )
            report = release_sft(
                paths["tasks.jsonl"],
                paths["traces.jsonl"],
                paths["candidate-sft.jsonl"],
                paths["grades.jsonl"],
                paths["verification.jsonl"],
                paths["difficulty.jsonl"],
                calibration_path,
                root / "release.jsonl",
            )
            self.assertEqual(report["released"], 0)
            self.assertIn(
                "insufficient_independent_judges", report["rejection_reasons"]
            )
            self.assertIn("difficulty_band:too_easy", report["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
