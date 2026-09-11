import json
from pathlib import Path

from scicode.pipeline.trace_checks import audit_rollouts
from scicode.pipeline.candidate import load_candidate

from test_candidate_validation import make_candidate
from test_sample_export import make_rollout


def test_trace_audit_accepts_complete_export(tmp_path: Path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    report = audit_rollouts(load_candidate(candidate), rollouts, require_usage=True)
    assert report["status"] == "ok"
    assert report["complete_subproblems"] == 2


def test_trace_audit_rejects_missing_subproblem(tmp_path: Path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    value = json.loads(rollouts.read_text(encoding="utf-8"))
    value["subtraces"] = value["subtraces"][:1]
    rollouts.write_text(json.dumps(value) + "\n", encoding="utf-8")
    report = audit_rollouts(load_candidate(candidate), rollouts)
    assert report["status"] == "failed"
    assert any("count" in item["error"] for item in report["failures"])
