import json
from pathlib import Path

import pytest

from scicode.pipeline.candidate import load_candidate, sha256_file
from scicode.pipeline.pipeline import PipelineError, run_candidate_pipeline

from test_candidate_validation import make_candidate
from test_sample_export import make_rollout


def _write_run_manifest(candidate: Path, rollouts: Path, *, run_id="run-1", mutate=None):
    run_dir = candidate / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_rollouts = run_dir / "rollouts.jsonl"
    run_rollouts.write_bytes(rollouts.read_bytes())
    candidate_manifest = load_candidate(candidate).as_dict()
    if mutate:
        mutate(candidate_manifest)
    manifest = {
        "schema": "scicode-avacore-run-v2",
        "run_id": run_id,
        "status": "finished",
        "mode": "strict",
        "trace_complete": True,
        "promotable": True,
        "candidate": candidate_manifest,
        "trace_export": {
            "path": str(run_rollouts),
            "sha256": sha256_file(run_rollouts),
        },
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return run_rollouts, path


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


def test_pipeline_requires_strict_run_manifest_for_final_delivery(tmp_path: Path):
    candidate = make_candidate(tmp_path)
    review = candidate / "validation" / "review_child.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(json.dumps({"decision": "passed"}), encoding="utf-8")
    rollouts = make_rollout(candidate)
    with pytest.raises(PipelineError, match="run manifest"):
        run_candidate_pipeline(candidate, tmp_path / "delivery", rollouts=rollouts)


def test_pipeline_rejects_run_manifest_identity_mismatch(tmp_path: Path):
    candidate = make_candidate(tmp_path)
    review = candidate / "validation" / "review_child.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(json.dumps({"decision": "passed"}), encoding="utf-8")
    rollouts = make_rollout(candidate)
    _, run_manifest = _write_run_manifest(
        candidate,
        rollouts,
        mutate=lambda value: value.__setitem__("visible_contract_sha256", "wrong"),
    )
    with pytest.raises(PipelineError, match="visible_contract_sha256"):
        run_candidate_pipeline(
            candidate,
            tmp_path / "delivery",
            rollouts=run_manifest.parent / "rollouts.jsonl",
            run_manifest=run_manifest,
        )
