import copy
import json
from pathlib import Path

import pytest

from factory.reasoning.build_native_sft import build, normalize_channels
from factory.reasoning.distill import export, grade_reasoning, load_config, run, select_reviewed, validate_inputs
from factory.reasoning.prepare_reused_tasks import file_sha256, prepare, rows
from factory.reasoning.schema import canonical_hash
from factory.reasoning.student_view import render_student_user
from factory.reasoning.scientific_audit import POLICY as AUDIT_POLICY, run_audit
from tests.factory_fixtures import task_for, grade_for


def config():
    return {
        "model": "DeepSeek-V4-Flash-0731", "teacher_revision": "0731-test",
        "base_url": "http://example.test/v1", "temperature": 0.7,
        "max_tokens": 16384, "context_window_tokens": 262144,
        "input_margin_tokens": 4096, "timeout": 2400,
        "concurrency": 2, "service_capacity": 2000, "attempts": 1,
        "run_variant": "test-ds-v1", "retry_incomplete": True,
    }


def response(*, finish="stop", reasoning="derive carefully", answer="Result\n```python\nx=42\n```"):
    return {"choices": [{"message": {"role": "assistant", "reasoning_content": reasoning,
                                      "content": answer}, "finish_reason": finish}],
            "usage": {"completion_tokens": 100}}


def inputs(root, *, count=1, status="model_supported_answer"):
    original = root / "original"
    original.mkdir()
    tasks = []
    sft = []
    index = []
    for number in range(count):
        task = task_for()
        task["task_id"] += str(number)
        tasks.append(task)
        identity = f"old-{number}"
        sft.append({"id": identity, "messages": [
            {"role": "system", "content": "Exact old system"},
            {"role": "user", "content": render_student_user(task)},
            {"role": "assistant", "content": "<think>OLD_PRIVATE_REASONING</think>OLD_TEACHER_ANSWER"},
        ], "metadata": {"task_hash": canonical_hash(task), "audit_disposition": status,
                         "training_target": "reasoning_and_answer"}})
        index.append({"trace_id": identity, "task_name": task["task_id"],
                      "source_file": str(original / "old-traces.jsonl")})
    for name, data in (("tasks.jsonl", tasks), ("sft.jsonl", sft), ("index.jsonl", index)):
        (original / name).write_text("".join(json.dumps(r) + "\n" for r in data), encoding="utf-8")
    output = root / "inputs"
    report = prepare(sft_path=original / "sft.jsonl", index_path=original / "index.jsonl",
                     source_root=original, out_dir=output, target=count, per_repo=0,
                     require_supported=True)
    return output, report


def test_prepare_exact_prompts_without_old_answer_and_without_repo_cap(tmp_path):
    prepared, report = inputs(tmp_path, count=8)
    assert report["selected"] == 8
    assert report["max_tasks_per_repository"] == 8
    assert report["old_assistant_responses_copied"] == 0
    assert "OLD_TEACHER_ANSWER" not in (prepared / "prompts.jsonl").read_text()
    assert "OLD_PRIVATE_REASONING" not in (prepared / "tasks.jsonl").read_text()
    assert validate_inputs(prepared, config())["selected"] == 8


def test_prepare_rejects_non_supported_population(tmp_path):
    with pytest.raises(ValueError, match="not supported"):
        inputs(tmp_path, status="reasoning_candidate")


def test_prepare_refuses_overwrite(tmp_path):
    prepared, _ = inputs(tmp_path)
    with pytest.raises(FileExistsError):
        prepare(sft_path=tmp_path / "original/sft.jsonl", index_path=tmp_path / "original/index.jsonl",
                source_root=tmp_path / "original", out_dir=prepared, target=1, per_repo=0)


@pytest.mark.parametrize("updates", [
    {"api_key": "DO_NOT_SAVE"}, {"concurrency": 2001}, {"service_capacity": 2001},
    {"base_url": "http://name:secret@example.test/v1"}, {"base_url": "http://example.test"},
    {"max_tokens": 262144}, {"timeout": 0}, {"run_variant": ""},
])
def test_config_rejects_unsafe_values(tmp_path, updates):
    cfg = config()
    cfg.update(updates)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        load_config(path)


def test_input_mutation_is_detected_before_call(tmp_path):
    prepared, _ = inputs(tmp_path)
    with (prepared / "prompts.jsonl").open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="artifact changed"):
        run(prepared, tmp_path / "solver", config(), chat_fn=lambda *_a, **_kw: pytest.fail("must not call"))


def test_context_guard_does_not_silently_truncate(tmp_path):
    prepared, _ = inputs(tmp_path)
    cfg = config()
    cfg["context_window_tokens"] = cfg["max_tokens"] + 4100
    with pytest.raises(ValueError, match="context budget"):
        validate_inputs(prepared, cfg)


def test_resume_keeps_both_channels_and_rejects_changed_config(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    calls = []
    def chat(messages, **kw):
        calls.append(messages)
        return response()
    assert run(prepared, output, config(), chat_fn=chat)["written"] == 1
    assert run(prepared, output, config(), chat_fn=chat)["skipped"] == 1
    assert len(calls) == 1
    assert calls[0] == list(rows(prepared / "prompts.jsonl"))[0]["messages"]
    changed = config()
    changed["max_tokens"] += 1
    with pytest.raises(ValueError, match="resume configuration"):
        run(prepared, output, changed, chat_fn=chat)
    native = tmp_path / "native"
    result = export(prepared, output, native)
    assert result["sft_rows"] == 1
    row = list(rows(native / "sft.jsonl"))[0]
    assert row["messages"][-1]["content"] == "<think>\nderive carefully\n</think>\nResult\n```python\nx=42\n```"
    assert row["metadata"]["teacher_model"] == "DeepSeek-V4-Flash-0731"
    candidate = list(rows(native / "audit-candidates.jsonl"))[0]
    assert candidate["messages"][-1]["content_loss"] is True
    assert candidate["messages"][-1]["reasoning_content"] == "derive carefully"


def test_partial_trace_can_retry_but_raw_history_remains(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response(finish="length"))
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())
    assert len(list(rows(output / "traces.jsonl"))) == 2
    assert export(prepared, output, tmp_path / "native")["sft_rows"] == 1


def test_incomplete_trace_is_preserved_not_exported_as_complete_sft(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response(finish="length"))
    native = tmp_path / "native"
    assert export(prepared, output, native)["excluded_incomplete"] == 1
    assert (output / "traces.jsonl").stat().st_size
    assert list(rows(native / "excluded.jsonl"))[0]["raw_trace_preserved"]


@pytest.mark.parametrize("status", ["model_supported_answer", "reasoning_candidate", "quarantine", "error", "missing"])
def test_review_partitions_never_remove_final_answers(tmp_path, status):
    import hashlib
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())
    native = tmp_path / "native"
    export(prepared, output, native)
    row = list(rows(native / "sft.jsonl"))[0]
    raw = (native / "audit-candidates.jsonl").read_bytes().rstrip(b"\r\n")
    audit = {"trace_id": row["id"], "model": "reviewer", "policy": AUDIT_POLICY,
             "row_sha256": hashlib.sha256(raw).hexdigest()}
    audit.update({"error": "temporary"} if status == "error" else {"disposition": status})
    path = tmp_path / "audit.jsonl"
    path.write_text("" if status == "missing" else json.dumps(audit) + "\n")
    reviewed = tmp_path / "reviewed"
    report = select_reviewed(native, path, reviewed, "reviewer")
    category = "not_reviewed" if status == "missing" else "audit_error" if status == "error" else status
    reviewed_row = list(rows(reviewed / f"{category}.jsonl"))[0]
    assert reviewed_row["messages"] == row["messages"]
    assert report["final_answers_removed"] == 0


def test_audit_adapter_works_with_existing_review_engine(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())
    native = tmp_path / "native"
    export(prepared, output, native)
    replies = iter([
        {"task_status": "answerable", "task_issue": "", "requirements": [
            {"id": "R1", "requirement": "give result", "kind": "exact", "probe": "check result"}]},
        {"checks": [{"id": "R1", "status": "satisfied", "answer_evidence": "Result",
                     "probe_result": "expected 42, delivered 42", "explanation": "matches"}],
         "critical_issue": "", "summary": "supported"},
        {"status": "consistent", "conflicts": [], "rationale": "no contradictions"},
    ])
    def review(*_a, **_kw):
        assert "Exact old system" in _a[0][0]["content"] or "REQUIREMENTS AND INDEPENDENT PROBES" in _a[0][0]["content"]
        return response(reasoning="review thinking", answer=json.dumps(next(replies)))
    audit_path = tmp_path / "audit.jsonl"
    counts = run_audit(native / "audit-candidates.jsonl", audit_path, model="reviewer", workers=1, chat_fn=review)
    assert counts["model_supported_answer"] == 1
    assert select_reviewed(native, audit_path, tmp_path / "reviewed", "reviewer")["counts"]["model_supported_answer"] == 1


def test_boundary_normalization_is_conservative():
    assert normalize_channels({"reasoning_content": " A\n", "content": " B\n"})[:2] == (" A\n", " B\n")
    assert normalize_channels({"content": "<think>derive</think>answer"})[:2] == ("derive", "answer")
    with pytest.raises(ValueError):
        normalize_channels({"reasoning_content": "derive", "content": "draft</think>answer"})
    with pytest.raises(ValueError):
        normalize_channels({"content": "<think>one</think><think>two</think>answer"})


def test_wrong_teacher_cannot_export(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())
    with pytest.raises(ValueError, match="unexpected teacher"):
        build(prepared / "tasks.jsonl", output / "traces.jsonl", tmp_path / "native", expected_teacher="wrong")


def test_generation_errors_are_retryable_not_finished_tasks(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    def fail(*_a, **_kw):
        raise RuntimeError("temporary endpoint outage")
    assert run(prepared, output, config(), chat_fn=fail)["errors"] == 1
    assert run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())["written"] == 1


@pytest.mark.parametrize("approve_content", [True, False])
def test_reasoning_review_is_bound_and_release_requires_both_channels(tmp_path, approve_content):
    import hashlib
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())
    native = tmp_path / "native"
    export(prepared, output, native)
    quality = tmp_path / "quality.jsonl"
    value = grade_for()
    value["message_annotations"][0]["train_content"] = approve_content
    seen = []
    def judge(messages, **_kw):
        seen.append(messages[0]["content"])
        return response(answer=json.dumps(value))
    assert grade_reasoning(prepared, native, quality, "reviewer", workers=1, chat_fn=judge)["reviewed"] == 1
    assert "derive carefully" in seen[0]
    assert grade_reasoning(prepared, native, quality, "reviewer", workers=1, chat_fn=judge)["skipped"] == 1
    row = list(rows(native / "sft.jsonl"))[0]
    candidate = (native / "audit-candidates.jsonl").read_bytes().rstrip(b"\r\n")
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(json.dumps({"trace_id": row["id"], "model": "reviewer",
        "policy": AUDIT_POLICY, "disposition": "model_supported_answer",
        "row_sha256": hashlib.sha256(candidate).hexdigest()}) + "\n")
    report = select_reviewed(native, audit_path, tmp_path / "reviewed", "reviewer", grades_path=quality)
    assert report["counts"].get("sft_ready", 0) == int(approve_content)
    assert list(rows(tmp_path / "reviewed/model_supported_answer.jsonl"))[0]["messages"] == row["messages"]


def test_truncated_quality_response_is_not_approved(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())
    native = tmp_path / "native"
    export(prepared, output, native)
    result = grade_reasoning(prepared, native, tmp_path / "quality.jsonl", "reviewer", workers=1,
                            chat_fn=lambda *_a, **_k: response(finish="length", answer=json.dumps(grade_for())))
    assert result["error"] == 1


def test_mutated_sft_cannot_borrow_original_answer_audit(tmp_path):
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    run(prepared, output, config(), chat_fn=lambda *_a, **_k: response())
    native = tmp_path / "native"
    export(prepared, output, native)
    with (native / "sft.jsonl").open("a") as sink:
        sink.write("\n")
    audit = tmp_path / "audit.jsonl"
    audit.write_text("")
    with pytest.raises(ValueError, match="changed after export"):
        select_reviewed(native, audit, tmp_path / "reviewed", "reviewer")


def test_concurrent_same_output_is_rejected_before_requests(tmp_path):
    from factory.reasoning.batch import controller_lock
    prepared, _ = inputs(tmp_path)
    output = tmp_path / "solver"
    with controller_lock(output / "run", exclusive=True):
        with pytest.raises(RuntimeError, match="another controller"):
            run(prepared, output, config(), chat_fn=lambda *_a, **_k: pytest.fail("must not call"))


def test_provider_thinking_options_and_raw_backup(tmp_path):
    prepared, _ = inputs(tmp_path)
    cfg = config()
    cfg["request_options"] = {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    def chat(_messages, **kwargs):
        assert kwargs["extra_body"] == cfg["request_options"]
        return response(reasoning="", answer="final without thinking")
    output = tmp_path / "solver"
    assert run(prepared, output, cfg, chat_fn=chat)["errors"] == 1
    assert list(rows(output / "raw-responses.jsonl"))[0]["response"]["choices"][0]["message"]["content"] == "final without thinking"
    with pytest.raises(ValueError, match="no valid traces"):
        export(prepared, output, tmp_path / "native")
