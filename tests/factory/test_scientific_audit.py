import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.scientific_audit import (
    AuditError,
    POLICY,
    _validate_checks,
    audit_row,
    materialize,
    run_audit,
)


def row(*, answer="The result is correct.", content_loss=True):
    return {
        "trace_id": "trace-1",
        "messages": [
            {"role": "system", "content": "scientific"},
            {"role": "user", "content": "For a fully contained rectangle, IoF must be exactly 1."},
            {"role": "assistant", "content": answer, "reasoning_content": "PRIVATE LONG THINKING",
             "reasoning_loss": True, "content_loss": content_loss},
        ],
    }


def response(value):
    return {"choices": [{"message": {"content": json.dumps(value)}}], "usage": {}}


PLAN = {
    "task_status": "answerable", "task_issue": "",
    "requirements": [{
        "id": "R1", "requirement": "Fully contained rectangle has IoF exactly 1",
        "kind": "exact", "probe": "Use area 0.25 inside a larger container; expected IoF 1",
    }],
}


def check(status="violated"):
    return {
        "checks": [{"id": "R1", "status": status,
                    "answer_evidence": "max(box_area, 1)",
                    "probe_result": "area 0.25: expected 1; answer gives 0.25",
                    "explanation": "The denominator is floored at 1."}],
        "critical_issue": "Exact containment invariant fails" if status == "violated" else "",
        "summary": "One counterexample" if status == "violated" else "All checks passed",
    }


class ScientificAuditTests(unittest.TestCase):
    def test_blinded_requirement_plan_and_counterexample_quarantine(self):
        calls = []

        def chat(messages, **_kwargs):
            calls.append(messages[0]["content"])
            return response(PLAN if len(calls) == 1 else check())

        result = audit_row(row(answer="IoF = intersection / max(box_area, 1)"), chat_fn=chat)
        self.assertEqual(result["disposition"], "quarantine")
        self.assertNotIn("max(box_area, 1)", calls[0])
        self.assertNotIn("PRIVATE LONG THINKING", calls[0] + calls[1])
        self.assertIn("max(box_area, 1)", calls[1])

    def test_unverifiable_and_nontraining_answer_are_not_approved(self):
        def chat(messages, **_kwargs):
            return response(PLAN if "INDEPENDENT PLAN:" not in messages[0]["content"]
                            else check("unverifiable"))

        self.assertEqual(audit_row(row(), chat_fn=chat)["disposition"], "reasoning_candidate")
        self.assertEqual(audit_row(row(content_loss=False), chat_fn=chat)["disposition"],
                         "reasoning_candidate")

    def test_truncated_answer_cannot_be_approved(self):
        sample = row()
        sample["termination"] = {"finish_reason": "length", "truncated": True}

        def chat(messages, **_kwargs):
            return response(PLAN if "INDEPENDENT PLAN:" not in messages[0]["content"]
                            else check("satisfied"))

        self.assertEqual(audit_row(sample, chat_fn=chat)["disposition"],
                         "reasoning_candidate")

    def test_final_json_must_not_be_recovered_from_private_thinking(self):
        def chat(_messages, **_kwargs):
            return {"choices": [{"message": {"content": "", "reasoning_content": json.dumps(PLAN)},
                                 "finish_reason": "length"}]}

        with self.assertRaisesRegex(AuditError, "truncated"):
            audit_row(row(), chat_fn=chat)

    def test_missing_check_fails_closed(self):
        with self.assertRaises(AuditError):
            _validate_checks({"checks": [], "critical_issue": "", "summary": ""}, PLAN, "answer")

    def test_resumable_hash_bound_audit_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "candidate.jsonl"
            output_path = Path(temporary) / "audit.jsonl"
            input_path.write_text(json.dumps(row()) + "\n", encoding="utf-8")
            original = input_path.read_bytes()
            calls = []

            def chat(messages, **_kwargs):
                calls.append(messages[0]["content"])
                if len(calls) == 1:
                    return response(PLAN)
                if len(calls) == 2:
                    return response(check("satisfied"))
                return response({"status": "consistent", "conflicts": [],
                                 "rationale": "No cross-requirement contradiction"})

            first = run_audit(input_path, output_path, workers=1, chat_fn=chat)
            self.assertEqual(first["model_supported_answer"], 1)
            self.assertEqual(input_path.read_bytes(), original)
            self.assertEqual(run_audit(input_path, output_path, workers=1, chat_fn=chat)["skipped"], 1)
            self.assertEqual(len(calls), 3)
            changed = row(answer="changed")
            input_path.write_text(json.dumps(changed) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(AuditError, "changed input row"):
                run_audit(input_path, output_path, workers=1, chat_fn=chat)

    def test_cross_requirement_conflict_overrides_optimistic_checks(self):
        plan = {"task_status": "answerable", "task_issue": "", "requirements": [
            {"id": "R1", "requirement": "Full containment scores exactly 1",
             "kind": "exact", "probe": "Unit box inside large crop"},
            {"id": "R2", "requirement": "Disclose the denominator floor tradeoff",
             "kind": "qualitative", "probe": "Sub-unit box inside crop"},
        ]}
        satisfied = {"checks": [
            {"id": "R1", "status": "satisfied", "answer_evidence": "formula says 1",
             "probe_result": "unit box gives 1", "explanation": "unit-area case passes"},
            {"id": "R2", "status": "satisfied", "answer_evidence": "max(area, 1)",
             "probe_result": "fully contained area 0.25 gives 0.25, not exact 1",
             "explanation": "disclosed floor distortion"},
        ], "critical_issue": "", "summary": "all satisfied"}
        calls = []

        def chat(messages, **_kwargs):
            calls.append(messages[0]["content"])
            if len(calls) == 1:
                return response(plan)
            if len(calls) == 2:
                return response(satisfied)
            return response({"status": "conflict", "conflicts": [{
                "required_id": "R1", "evidence_id": "R2",
                "input": "fully contained area 0.25",
                "required": "1", "delivered": "0.25",
                "explanation": "denominator floor breaks exact containment",
            }], "rationale": "Disclosure does not satisfy R1"})

        result = audit_row(row(answer="IoF = intersection / max(box_area, 1)"),
                           chat_fn=chat)
        self.assertEqual(result["disposition"], "quarantine")
        self.assertEqual(result["consistency"]["conflicts"][0]["required_id"], "R1")
        self.assertNotIn("PRIVATE LONG THINKING", calls[2])

    def test_selection_masks_unverified_answer_but_keeps_reasoning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "candidate.jsonl"
            audit = root / "audit.jsonl"
            source.write_text(json.dumps(row()) + "\n", encoding="utf-8")

            def chat(messages, **_kwargs):
                return response(PLAN if "INDEPENDENT PLAN:" not in messages[0]["content"]
                                else check("unverifiable"))

            run_audit(source, audit, workers=1, chat_fn=chat)
            report = materialize(source, audit, root / "selected")
            self.assertEqual(report["counts"]["reasoning_candidate"], 1)
            selected = json.loads((root / "selected" / "reasoning-candidates.jsonl")
                                  .read_text(encoding="utf-8"))
            self.assertTrue(selected["messages"][-1]["reasoning_loss"])
            self.assertFalse(selected["messages"][-1]["content_loss"])
            self.assertEqual(selected["messages"][-1]["content"], "")
            self.assertTrue(selected["messages"][-1]["reasoning_content"])
            self.assertTrue(selected["scientific_audit"]["suppressed_answer_sha256"])
            self.assertFalse(selected["scientific_audit"]["scientific_correctness_proven"])
            self.assertEqual(selected["scientific_audit"]["policy"], POLICY)


if __name__ == "__main__":
    unittest.main()
