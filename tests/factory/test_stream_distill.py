import json
import threading

import pytest

from factory.reasoning import stream_distill
from factory.reasoning.review_client import reviewer_chat
from tests.factory.test_distill import config, inputs, response
from tests.factory_fixtures import grade_for


CLIENT = {"base_url": "http://review.test/v1", "api_key": "review-test-secret", "model": "Kimi-K3"}


def review_response(*_a, **_k):
    grade = grade_for()
    grade["message_annotations"][0]["train_content"] = True
    grade["reasoning_evidence"] = {"useful_quote": "derive carefully", "redundant_quote": "",
                                   "efficiency": "efficient", "explanation": "useful derivation"}
    return response(answer=json.dumps(grade))


def audit(candidate, **kwargs):
    return {"policy": stream_distill.AUDIT_POLICY, "trace_id": candidate["trace_id"],
            "model": kwargs["model"], "disposition": "model_supported_answer"}


def test_live_review_begins_before_all_generations_complete(tmp_path):
    prepared, _ = inputs(tmp_path, count=2)
    entered = threading.Event()

    def teacher(messages, **kwargs):
        with gate:
            order[0] += 1
            first = order[0] == 1
        if not first:
            assert entered.wait(10), "review waited until full generation completion"
        return response()

    def live_audit(candidate, **kwargs):
        entered.set()
        return audit(candidate, **kwargs)

    gate, order = threading.Lock(), [0]
    report = stream_distill.pipeline(prepared, tmp_path / "solver", tmp_path / "batch", config(),
        CLIENT, workers=2, teacher_chat=teacher, review_chat=review_response, audit_fn=live_audit)
    assert entered.is_set()
    assert report["selection"]["counts"]["sft_ready"] == 2
    assert report["missing_complete_generations"] == 0
    state = json.loads((tmp_path / "batch/pipeline_progress.json").read_text())
    assert state["audit_completed"] == state["grade_completed"] == 2
    assert state["review_active"] == 0
    assert state["stage"] == "completed"
    assert "review-test-secret" not in (tmp_path / "batch/pipeline_manifest.json").read_text()


def test_interrupted_pipeline_resumes_without_duplicate_reviews(tmp_path, monkeypatch):
    prepared, _ = inputs(tmp_path, count=2)
    real_run = stream_distill.run
    def interrupted(*args, **kwargs):
        real_run(*args, **kwargs)
        raise RuntimeError("interrupted before export")
    monkeypatch.setattr(stream_distill, "run", interrupted)
    kwargs = dict(workers=2, teacher_chat=lambda *_a, **_k: response(),
                  review_chat=review_response, audit_fn=audit)
    with pytest.raises(RuntimeError, match="before export"):
        stream_distill.pipeline(prepared, tmp_path / "solver", tmp_path / "batch", config(), CLIENT, **kwargs)
    monkeypatch.setattr(stream_distill, "run", real_run)
    kwargs["audit_fn"] = lambda *_a, **_k: pytest.fail("completed audit must not be repeated")
    report = stream_distill.pipeline(prepared, tmp_path / "solver", tmp_path / "batch", config(), CLIENT, **kwargs)
    assert report["generation"][0]["skipped"] == 2
    assert report["selection"]["counts"]["sft_ready"] == 2
    assert len((tmp_path / "batch/scientific-audit.jsonl").read_text().splitlines()) == 2


def test_review_errors_keep_complete_targets_outside_ready_set(tmp_path):
    prepared, _ = inputs(tmp_path)
    def fail(*_a, **_k):
        raise ValueError("review temporary failure")
    report = stream_distill.pipeline(prepared, tmp_path / "solver", tmp_path / "batch", config(),
        CLIENT, workers=1, teacher_chat=lambda *_a, **_k: response(), review_chat=fail, audit_fn=audit)
    assert report["selection"]["counts"].get("sft_ready", 0) == 0
    row = json.loads((tmp_path / "batch/reviewed-v1/model_supported_answer.jsonl").read_text())
    assert "derive carefully" in row["messages"][-1]["content"]
    assert "```python" in row["messages"][-1]["content"]


def test_context_budget_adapts_output_without_truncating_input():
    seen = []
    messages = [{"role": "user", "content": "complete prompt"}]
    def underlying(value, **kwargs):
        seen.append((value, kwargs))
        return response()
    chat = reviewer_chat(CLIENT, context_window_tokens=262144, input_margin_tokens=4096,
        chat_fn=underlying, count_fn=lambda *_a: (220000, "test-estimate-not-certified"))
    result = chat(messages, model="Kimi-K3", max_tokens=65536)
    assert seen[0][0] == messages
    assert seen[0][1]["max_tokens"] == 38048
    assert seen[0][1]["client"] == CLIENT
    assert result["_context_budget"]["context_window_tokens"] == 262144


def test_over_context_input_is_not_sent_or_truncated():
    chat = reviewer_chat(CLIENT, chat_fn=lambda *_a, **_k: pytest.fail("must not send"),
                         count_fn=lambda *_a: (260000, "test-estimate"))
    with pytest.raises(ValueError, match="input was not truncated"):
        chat([{"role": "user", "content": "oversized full input"}], max_tokens=65536)


def test_context_counter_failure_uses_conservative_fallback():
    def fail(*_a):
        raise OSError("counter unavailable")
    chat = reviewer_chat(CLIENT, count_fn=fail, chat_fn=lambda *_a, **_k: response())
    result = chat([{"role": "user", "content": "你好"}], max_tokens=65536)
    assert result["_context_budget"]["estimated_input_tokens"] == 6
    assert "fallback-utf8" in result["_context_budget"]["estimate_policy"]


def test_actual_reported_context_overrun_cannot_be_approved():
    def overrun(*_a, **_k):
        value = response()
        value["usage"] = {"prompt_tokens": 260000, "completion_tokens": 3000}
        return value
    chat = reviewer_chat(CLIENT, count_fn=lambda *_a: (100000, "estimate"), chat_fn=overrun)
    with pytest.raises(ValueError, match="reported actual"):
        chat([{"role": "user", "content": "test"}], max_tokens=65536)


def test_output_cannot_equal_total_window():
    chat = reviewer_chat(CLIENT, count_fn=lambda *_a: (1, "test"))
    with pytest.raises(ValueError, match="smaller"):
        chat([], max_tokens=262144)
