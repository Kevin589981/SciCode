import json
from pathlib import Path

from scicode.pipeline.pipeline import PipelineError, run_candidate_pipeline

from test_candidate_validation import make_candidate
from test_sample_export import make_rollout


def test_pipeline_requires_review_by_default(tmp_path: Path):
    candidate = make_candidate(tmp_path)
    try:
        run_candidate_pipeline(candidate, tmp_path / "delivery")
    except PipelineError as exc:
        assert "review child" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unreviewed candidate unexpectedly passed")


def test_pipeline_dry_run_and_delivery_states(tmp_path: Path):
    candidate = make_candidate(tmp_path)
    review = candidate / "validation" / "review_child.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(json.dumps({"decision": "passed", "session_id": "child-1"}), encoding="utf-8")
    ready = run_candidate_pipeline(candidate, tmp_path / "delivery")
    assert ready["state"] == "ready_for_run"
    rollouts = make_rollout(candidate)
    delivered = run_candidate_pipeline(
        candidate,
        tmp_path / "delivery",
        rollouts=rollouts,
        target_count=10,
        allow_qa_export=True,
    )
    assert delivered["state"] == "run_complete"
    assert delivered["export"]["accepted_samples"] == 2
    assert (candidate / "validation" / "pipeline_manifest.json").is_file()


def test_pipeline_accepts_avacore_child_review_decision(tmp_path: Path):
    candidate = make_candidate(tmp_path)
    review = candidate / "validation" / "review_child.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(
        json.dumps({"decision": "accepted_for_avacore_run"}), encoding="utf-8"
    )
    result = run_candidate_pipeline(candidate, tmp_path / "delivery")
    assert result["state"] == "ready_for_run"
