import json

import pytest

from scicode.trace import TraceError, TraceRecorder


def make_recorder(tmp_path, mode="strict"):
    return TraceRecorder(
        tmp_path / "run-001",
        run_id="run-001",
        candidate_id="generated-001",
        task_revision="r1",
        mode=mode,
        provider="test-provider",
        model="test-model",
        sampling={"temperature": 0.2},
        prompt_version="p1",
        environment_fingerprint="env-1",
    )


def test_trace_records_order_and_manifest_hashes(tmp_path):
    recorder = make_recorder(tmp_path)
    recorder.record_raw({"provider_event": "start"})
    recorder.record("prompt", {"text": "implement step 1"}, step_id="1.1")
    recorder.record(
        "assistant_message",
        {"text": "```python\ndef f(x):\n    return x\n```"},
        step_id="1.1",
    )
    manifest = recorder.finalize(verification={"passed": True})

    events = [
        json.loads(line)
        for line in recorder.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event_id"] for event in events] == [
        "event-000001",
        "event-000002",
    ]
    assert manifest["event_count"] == 2
    assert manifest["artifacts"]["events_sha256"]
    assert manifest["artifacts"]["raw_sha256"]


def test_public_event_rejects_private_field(tmp_path):
    recorder = make_recorder(tmp_path)
    with pytest.raises(TraceError, match="private/oracle-like"):
        recorder.record("step_result", {"target_value": 1.0})


def test_visible_export_excludes_private_events(tmp_path):
    recorder = make_recorder(tmp_path, mode="agentic")
    recorder.record("tool_result", {"text": "shape is valid"})
    recorder.record(
        "evaluator_result",
        {"private_status": "pass"},
        visibility="private",
    )
    destination = tmp_path / "visible.jsonl"
    assert recorder.export_visible(destination) == 1
    exported = [
        json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()
    ]
    assert len(exported) == 1
    assert exported[0]["event_type"] == "tool_result"


def test_mode_and_event_type_are_validated(tmp_path):
    with pytest.raises(TraceError, match="mode"):
        make_recorder(tmp_path, mode="interactive")
    recorder = make_recorder(tmp_path)
    with pytest.raises(TraceError, match="event type"):
        recorder.record("Prompt Event", {})


def test_raw_and_normalized_events_require_strict_json(tmp_path):
    recorder = make_recorder(tmp_path)
    with pytest.raises(TraceError, match="strict JSON"):
        recorder.record_raw({"value": float("nan")})
    with pytest.raises(TraceError, match="non-finite"):
        recorder.record("prompt", {"value": float("inf")})
