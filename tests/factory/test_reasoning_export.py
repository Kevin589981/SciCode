import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.export import SFT_SCHEMA, export_sft
from factory.reasoning.preflight import PREFLIGHT_POLICY
from factory.reasoning.verify import VERIFICATION_POLICY
from tests.factory_fixtures import grade_for, task_for, trace_for


class ReasoningExportTests(unittest.TestCase):
    def _write_inputs(self, root, task, traces, grades):
        tasks_path = root / "tasks.jsonl"
        traces_path = root / "traces.jsonl"
        grades_path = root / "grades.jsonl"
        tasks_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
        traces_path.write_text(
            "".join(json.dumps(row) + "\n" for row in traces), encoding="utf-8"
        )
        grades_path.write_text(
            "".join(json.dumps(row) + "\n" for row in grades), encoding="utf-8"
        )
        return tasks_path, traces_path, grades_path

    def test_failed_trace_can_be_exported_and_passing_trace_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = task_for()
            failed = trace_for(task, outcome="fail")
            passed = trace_for(task, outcome="pass")
            passed["trace_id"] += "-pass"
            paths = self._write_inputs(
                root,
                task,
                [failed, passed],
                [grade_for(failed, trainable=True), grade_for(passed, trainable=False)],
            )
            out = root / "sft.jsonl"
            report = export_sft(*paths, output_path=out)
            rows = [
                json.loads(line)
                for line in out.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["schema_version"], SFT_SCHEMA)
            self.assertEqual(rows[0]["outcome"]["status"], "fail")
            self.assertEqual(rows[0]["termination"]["finish_reason"], "unknown")
            self.assertEqual(rows[0]["termination"]["max_tokens"], 16384)
            self.assertEqual(report["selected"], 1)
            self.assertEqual(report["rejected"], 1)

    def test_reasoning_and_independent_masks_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = task_for()
            trace = trace_for(task)
            grade = grade_for(trace)
            grade["message_annotations"][0]["train_content"] = False
            paths = self._write_inputs(root, task, [trace], [grade])
            out = root / "sft.jsonl"
            export_sft(*paths, output_path=out)
            row = json.loads(out.read_text(encoding="utf-8").strip())
            original = trace["messages"][-1]["reasoning_content"]
            assistant = row["messages"][-1]
            self.assertEqual(assistant["reasoning_content"], original)
            self.assertTrue(assistant["reasoning_loss"])
            self.assertFalse(assistant["content_loss"])
            self.assertTrue(assistant["loss"])

    def test_inline_adapter_does_not_discard_native_reasoning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task = task_for()
            trace = trace_for(task)
            paths = self._write_inputs(root, task, [trace], [grade_for(trace)])
            out = root / "sft-inline.jsonl"
            export_sft(*paths, output_path=out, inline_thinking=True)
            row = json.loads(out.read_text(encoding="utf-8").strip())
            assistant = row["messages"][-1]
            self.assertIn("<think>", assistant["content"])
            self.assertIn(assistant["reasoning_content"], assistant["content"])
            self.assertEqual(row["thinking_format"], "inline_and_preserved")

    def test_historical_trace_without_current_admission_is_not_exported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            admitted_task = task_for("derive_implement")
            blocked_task = task_for("diagnose_revise")
            admitted_trace = trace_for(admitted_task)
            blocked_trace = trace_for(blocked_task)
            tasks = root / "tasks.jsonl"
            traces = root / "traces.jsonl"
            grades = root / "grades.jsonl"
            preflight = root / "preflight.jsonl"
            verification = root / "verification.jsonl"
            tasks.write_text(
                "".join(
                    json.dumps(row) + "\n" for row in (admitted_task, blocked_task)
                ),
                encoding="utf-8",
            )
            traces.write_text(
                "".join(
                    json.dumps(row) + "\n" for row in (admitted_trace, blocked_trace)
                ),
                encoding="utf-8",
            )
            grades.write_text(
                "".join(
                    json.dumps(grade_for(row, trainable=True)) + "\n"
                    for row in (admitted_trace, blocked_trace)
                ),
                encoding="utf-8",
            )
            preflight.write_text(
                "".join(
                    json.dumps(
                        {
                            "task_hash": row["task_hash"],
                            "accepted": True,
                            "policy_version": PREFLIGHT_POLICY,
                            "critic": {"model": "critic"},
                        }
                    )
                    + "\n"
                    for row in (admitted_trace, blocked_trace)
                ),
                encoding="utf-8",
            )
            verification.write_text(
                "".join(
                    json.dumps(row) + "\n"
                    for row in (
                        {
                            "task_hash": admitted_trace["task_hash"],
                            "accepted": True,
                            "policy_version": VERIFICATION_POLICY,
                            "verifier": {"model": "verifier"},
                        },
                        {
                            "task_hash": blocked_trace["task_hash"],
                            "accepted": True,
                            "policy_version": "obsolete-source-policy",
                            "verifier": {"model": "verifier"},
                        },
                    )
                ),
                encoding="utf-8",
            )
            out = root / "sft.jsonl"
            report = export_sft(
                tasks,
                traces,
                grades,
                output_path=out,
                preflight_path=preflight,
                verification_path=verification,
                preflight_model="critic",
                verifier_model="verifier",
            )
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(
                [row["trace_id"] for row in rows], [admitted_trace["trace_id"]]
            )
            self.assertEqual(report["not_admitted"], 1)


if __name__ == "__main__":
    unittest.main()
