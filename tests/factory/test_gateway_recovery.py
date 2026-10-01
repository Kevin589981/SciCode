import concurrent.futures
import json
import threading
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from factory.author import llm
from factory.reasoning.resilient_client import ResilientClient
from factory.reasoning.review_cache import ReviewCache
from factory.reasoning.recover_distill import prepare_recovery
from factory.reasoning.scientific_audit import _validate_checks
from factory.reasoning.distill import run, export
from factory.reasoning import stream_distill
from tests.factory.test_distill import inputs, config, response
from tests.factory.test_stream_distill import audit, review_response, CLIENT


def test_transient_failure_recovers_without_becoming_failed_row():
    calls, events = [], []
    def provider(*a, **k):
        calls.append(k)
        if len(calls) < 3:
            raise llm.LLMRequestError("HTTP 502 upstream temporarily unavailable", status_code=502)
        return response()
    client = ResilientClient(provider, threshold=1, cooldown=.01, max_cooldown=.02,
                             emit=events.append, sleep=lambda _: None, start_rate=0)
    assert client([], max_tokens=100)["choices"][0]["finish_reason"] == "stop"
    assert len(calls) == 3
    assert all(c["retries"] == 1 for c in calls)
    assert client.snapshot()["transient_errors"] == 2
    assert client.snapshot()["circuit"] == "closed"
    assert [e["event"] for e in events] == ["circuit_open", "circuit_open", "provider_recovered"]


def test_half_open_allows_one_probe_not_a_thread_storm():
    entered, release = threading.Event(), threading.Event()
    lock, calls = threading.Lock(), []
    def provider(*a, **k):
        with lock:
            calls.append(1)
            ordinal = len(calls)
        if ordinal == 1:
            raise llm.LLMRequestError("502", status_code=502)
        if ordinal == 2:
            entered.set()
            assert release.wait(5)
        return response()
    client = ResilientClient(provider, threshold=1, cooldown=.01, sleep=lambda _: None, start_rate=0)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        fs = [pool.submit(client, [], max_tokens=10) for _ in range(20)]
        assert entered.wait(5)
        assert len(calls) == 2
        release.set()
        for f in fs:
            f.result(timeout=5)


def test_auth_failure_is_fatal_and_never_retried_by_other_workers():
    calls = []
    def provider(*a, **k):
        calls.append(1)
        raise llm.LLMRequestError("401 unauthorized", status_code=401, retryable=False)
    client = ResilientClient(provider)
    for _ in range(3):
        with pytest.raises(llm.LLMRequestError, match="unauthorized"):
            client([], max_tokens=1)
    assert len(calls) == 1


def test_error_retains_status_and_body_but_redacts_keys():
    error = urllib.error.HTTPError("http://provider.test/v1", 502, "Bad Gateway", {},
                                  BytesIO(b'upstream unavailable; key=sk-test-key'))
    with patch.object(llm.urllib.request, "urlopen", side_effect=error):
        with pytest.raises(llm.LLMRequestError) as caught:
            llm.chat([], retries=1, client={"base_url":"http://provider.test/v1", "model":"m", "api_key":"sk-test-key"})
    assert caught.value.status_code == 502
    assert caught.value.retryable
    assert "upstream unavailable" in str(caught.value)
    assert "sk-test-key" not in str(caught.value)


def test_review_stage_cache_reuses_success_and_preserves_rejection(tmp_path):
    calls = []
    def provider(*a, **k):
        calls.append(1)
        return response()
    cache = ReviewCache(tmp_path, provider, {"model":"Kimi-K3"})
    cache([], model="Kimi-K3", max_tokens=100)
    cache([], model="Kimi-K3", max_tokens=100)
    assert len(calls) == 1
    cache.reject_last("semantic JSON validation failed")
    cache([], model="Kimi-K3", max_tokens=100)
    assert len(calls) == 2
    assert len(list(tmp_path.rglob("*.rejected.json"))) == 1


def test_missing_critical_summary_is_repaired_without_weakening_violation():
    verdict = {"checks":[{"id":"R1","status":"violated","answer_evidence":"x=2",
                         "probe_result":"Expected 3, implementation returns 2", "explanation":"wrong arithmetic"}],
               "critical_issue":"", "summary":"wrong"}
    value = _validate_checks(verdict, {"requirements":[{"id":"R1"}]}, "x=2")
    assert value["checks"][0]["status"] == "violated"
    assert "Expected 3" in value["critical_issue"]
    assert value["critical_issue_repair"]


def test_stop_without_final_answer_is_regenerated_and_exported_once(tmp_path):
    prepared, _ = inputs(tmp_path)
    solver = tmp_path / "solver"
    run(prepared, solver, config(), chat_fn=lambda *_a, **_k: response(answer=""))
    report = run(prepared, solver, config(), chat_fn=lambda *_a, **_k: response())
    assert report["skipped"] == 0
    assert report["written"] == 1
    assert export(prepared, solver, tmp_path / "native")["sft_rows"] == 1
    assert len((solver / "traces.jsonl").read_text().splitlines()) == 2


def test_recovery_preserves_original_and_skips_completed_reviews(tmp_path):
    prepared, _ = inputs(tmp_path)
    original = tmp_path / "original-run"
    original.mkdir()
    import shutil
    shutil.copytree(prepared, original / "inputs")
    report = stream_distill.pipeline(original / "inputs", original / "solver", original, config(), CLIENT,
        workers=1, teacher_chat=lambda *_a, **_k: response(), review_chat=review_response, audit_fn=audit)
    before = (original / "solver/traces.jsonl").read_bytes()
    recovered = tmp_path / "recovery"
    plan = prepare_recovery(original, recovered, config())
    assert plan["usable_complete_traces_preserved"] == 1
    assert (original / "solver/traces.jsonl").read_bytes() == before
    report = stream_distill.pipeline(recovered / "inputs", recovered / "solver", recovered, config(), CLIENT,
        workers=1, teacher_chat=lambda *_a, **_k: pytest.fail("teacher already complete"),
        review_chat=lambda *_a, **_k: pytest.fail("review already complete"), audit_fn=audit)
    assert report["selection"]["counts"]["sft_ready"] == 1


def test_validation_retry_gets_feedback_and_does_not_rewrite_quotes(tmp_path):
    prepared, _ = inputs(tmp_path)
    seen = []
    def reviewer(messages, **kwargs):
        seen.append(messages[0]["content"])
        if len(seen) == 1:
            return response(answer='{"not":"a grade"}')
        return review_response()
    report = stream_distill.pipeline(prepared, tmp_path / "solver", tmp_path / "batch", config(), CLIENT,
        workers=1, teacher_chat=lambda *_a, **_k: response(), review_chat=reviewer, audit_fn=audit)
    assert len(seen) == 2
    assert "PREVIOUS RESPONSE FAILED MACHINE VALIDATION" in seen[1]
    assert report["selection"]["counts"]["sft_ready"] == 1
    assert report["grade_errors"] == 0


def test_resume_after_snapshot_creates_new_export_without_overwriting(tmp_path):
    prepared, _ = inputs(tmp_path)
    batch = tmp_path / "batch"
    kwargs = dict(workers=1, teacher_chat=lambda *_a, **_k: response(),
                  review_chat=review_response, audit_fn=audit)
    first = stream_distill.pipeline(prepared, tmp_path / "solver", batch, config(), CLIENT, **kwargs)
    original = (batch / "reviewed-v1/sft.jsonl").read_bytes()
    second = stream_distill.pipeline(prepared, tmp_path / "solver", batch, config(), CLIENT, **kwargs)
    assert second["export_version"] == 2
    assert (batch / "reviewed-v1/sft.jsonl").read_bytes() == original
    assert (batch / "reviewed-v2/sft.jsonl").read_bytes() == original
    assert second["generation"][0]["skipped"] == 1


def test_review_cache_cannot_include_client_secrets(tmp_path):
    cache = ReviewCache(tmp_path, lambda *_a, **_k: response(), {})
    with pytest.raises(ValueError, match="non-secret"):
        cache([], client={"api_key":"secret"})
