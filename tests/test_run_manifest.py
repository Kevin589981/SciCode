from pathlib import Path

from scicode.pipeline.run_manifest import (
    build_run_manifest,
    is_promotable,
    safe_endpoint,
)

from test_candidate_validation import make_candidate
from scicode.pipeline.candidate import load_candidate


def test_safe_endpoint_removes_credentials_and_query():
    assert safe_endpoint("https://user:secret@example.org:8443/v1?token=x") == "https://example.org:8443/v1"


def test_complete_candidate_run_is_promotable(tmp_path: Path):
    candidate = load_candidate(make_candidate(tmp_path))
    export = tmp_path / "rollouts.jsonl"
    export.write_text("{}\n", encoding="utf-8")
    manifest = build_run_manifest(
        run_id="run-1",
        status="finished",
        config={"base_url": "http://user:secret@example.org/v1", "api_key": "secret"},
        candidate=candidate,
        expected_problems=1,
        expected_subproblems=1,
        completed_problems=1,
        completed_subproblems=1,
        trace_export=export,
    )
    assert manifest["trace_complete"] is True
    assert is_promotable(manifest) is True
    assert "api_key" not in manifest["config"]
    assert manifest["config"]["base_url"] == "http://example.org/v1"


def test_partial_run_is_not_promotable(tmp_path: Path):
    candidate = load_candidate(make_candidate(tmp_path))
    manifest = build_run_manifest(
        run_id="run-2",
        status="failed",
        config={},
        candidate=candidate,
        expected_problems=1,
        expected_subproblems=1,
        completed_problems=0,
        completed_subproblems=0,
    )
    assert is_promotable(manifest) is False
