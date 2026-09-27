import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.build_audited_sft import (
    DATASET_POLICY,
    DatasetBuildError,
    build_dataset,
)
from factory.reasoning.scientific_audit import POLICY as AUDIT_POLICY


def candidate(trace_id, *, content_loss=True):
    return {
        "trace_id": trace_id,
        "task_hash": "task-hash",
        "archetype": "derive_implement",
        "thinking_format": "separate_reasoning_content",
        "termination": {"truncated": False, "finish_reason": "stop"},
        "messages": [
            {"role": "system", "content": "Think scientifically."},
            {"role": "user", "content": "Derive the result."},
            {"role": "assistant", "content": "The result is 42.",
             "reasoning_content": "First derive 6 * 7.",
             "reasoning_loss": True, "content_loss": content_loss},
        ],
    }


class BuildAuditedSftTests(unittest.TestCase):
    def test_build_keeps_one_row_per_selected_trace_and_marks_unreviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate.jsonl"
            audit = root / "audit.jsonl"
            source_rows = [
                candidate("approved"), candidate("reasoning", content_loss=False),
                candidate("unresolved"), candidate("quarantined"),
            ]
            audit_rows = []
            with source.open("wb") as sink:
                for row in source_rows:
                    raw = (json.dumps(row) + "\n").encode()
                    sink.write(raw)
                    audit_rows.append({
                        "policy": AUDIT_POLICY,
                        "trace_id": row["trace_id"],
                        "row_sha256": hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest(),
                    })
            audit_rows[0]["disposition"] = "model_supported_answer"
            audit_rows[1]["disposition"] = "reasoning_candidate"
            audit_rows[2]["error"] = "AuditError: review response was truncated"
            audit_rows[3]["disposition"] = "quarantine"
            audit.write_text("".join(json.dumps(row) + "\n" for row in audit_rows), encoding="utf-8")
            report = build_dataset(source, audit, root / "out", expected_rows=3,
                                   expected_unresolved=1)
            self.assertEqual(report["policy"], DATASET_POLICY)
            self.assertEqual(report["counts"]["audit_quarantine"], 1)
            self.assertEqual(report["counts"]["audit_audit_error"], 1)
            rows = [json.loads(line) for line in (root / "out" / "sft.jsonl")
                    .read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["id"] for row in rows],
                             ["approved", "reasoning", "unresolved"])
            self.assertIn("<think>\nFirst derive 6 * 7.\n</think>\nThe result is 42.",
                          rows[0]["messages"][-1]["content"])
            self.assertEqual(rows[1]["metadata"]["training_target"], "reasoning_only")
            self.assertNotIn("The result is 42.", rows[1]["messages"][-1]["content"])
            self.assertEqual(rows[2]["metadata"]["audit_disposition"], "audit_error")
            self.assertFalse(rows[2]["metadata"]["scientific_correctness_proven"])
            with self.assertRaisesRegex(DatasetBuildError, "refusing to overwrite"):
                build_dataset(source, audit, root / "out")

    def test_source_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate.jsonl"
            audit = root / "audit.jsonl"
            source.write_text(json.dumps(candidate("one")) + "\n", encoding="utf-8")
            audit.write_text(json.dumps({
                "policy": AUDIT_POLICY, "trace_id": "one",
                "row_sha256": "not-the-source", "disposition": "model_supported_answer",
            }) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(DatasetBuildError, "does not match source"):
                build_dataset(source, audit, root / "out")
            self.assertFalse((root / "out" / "sft.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
