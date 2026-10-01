import copy
import json

import pytest

from factory.reasoning.distill import export, run, validate_inputs
from factory.reasoning.prepare_complement import prepare, SELECTION_POLICY
from factory.reasoning.prepare_reused_tasks import file_sha256, rows
from factory.reasoning.schema import canonical_hash
from factory.reasoning.student_view import render_student_user
from tests.factory.test_distill import inputs, config, response
from tests.factory_fixtures import task_for


def write(path, data):
    path.write_text("".join(json.dumps(row) + "\n" for row in data), encoding="utf-8")


def universe(root):
    baseline, _ = inputs(root)
    original = root / "original"
    old_sft = list(rows(original / "sft.jsonl"))
    tasks = list(rows(original / "tasks.jsonl"))
    candidates = [{"trace_id": r["id"], "task_hash": r["metadata"]["task_hash"],
                   "messages": r["messages"]} for r in old_sft]
    index = list(rows(original / "index.jsonl"))
    for number in (1, 2):
        task = task_for()
        task["task_id"] += f"-remaining-{number}"
        task["problem"]["question"] += f" Additional public case {number}."
        tasks.append(task)
        candidates.append({"trace_id": f"remaining-{number}", "task_hash": canonical_hash(task),
                           "messages": [{"role": "system", "content": "Exact old system"},
                                        {"role": "user", "content": render_student_user(task)},
                                        {"role": "assistant", "content": "UNSUPPORTED_OLD_ANSWER",
                                         "reasoning_content": "UNSUPPORTED_PRIVATE_REASONING"}]})
        index.append({"trace_id": f"remaining-{number}", "task_name": task["task_id"],
                      "source_file": str(original / "old-traces.jsonl")})
    write(original / "tasks.jsonl", tasks)
    write(original / "candidates.jsonl", candidates)
    write(original / "index.jsonl", index)
    return baseline, original


def prepare_at(root):
    baseline, original = universe(root)
    output = root / "complement"
    report = prepare(candidates_path=original / "candidates.jsonl", index_path=original / "index.jsonl",
                     source_root=original, exclude_inputs=baseline, out_dir=output)
    return output, report


def test_complement_accepts_unapproved_old_answers_but_never_copies_them(tmp_path):
    output, report = prepare_at(tmp_path)
    assert report["selected"] == 2
    assert report["counts"] == {"universe_rows": 3, "excluded_baseline_rows": 1, "raw_complement_rows": 2}
    assert report["require_supported"] is False
    assert report["old_assistant_responses_copied"] == 0
    assert report["verified_overlap"] == dict.fromkeys(
        ("old_trace_ids", "task_ids", "task_hashes", "visible_prompt_hashes", "visible_user_hashes"), 0)
    for name in ("tasks.jsonl", "prompts.jsonl", "source_index.jsonl", "exclusions.jsonl"):
        assert "UNSUPPORTED_" not in (output / name).read_text()
    assert validate_inputs(output, config())["selected"] == 2
    seen = []
    def chat(messages, **_kw):
        seen.append(messages)
        assert [m["role"] for m in messages] == ["system", "user"]
        return response()
    solver = tmp_path / "solver"
    assert run(output, solver, config(), chat_fn=chat)["written"] == 2
    assert len(seen) == 2
    assert export(output, solver, tmp_path / "native")["sft_rows"] == 2
    assert all("```python" in r["messages"][-1]["content"] for r in rows(tmp_path / "native/sft.jsonl"))


def test_complement_deduplicates_identical_visible_prompts(tmp_path):
    baseline, original = universe(tmp_path)
    tasks = list(rows(original / "tasks.jsonl"))
    candidate_rows = list(rows(original / "candidates.jsonl"))
    index_rows = list(rows(original / "index.jsonl"))
    alias = copy.deepcopy(tasks[1])
    alias["task_id"] += "-alias"
    tasks.append(alias)
    candidate_rows.append({"trace_id": "alias", "task_hash": canonical_hash(alias),
                           "messages": candidate_rows[1]["messages"]})
    index_rows.append({"trace_id": "alias", "task_name": alias["task_id"],
                       "source_file": str(original / "old-traces.jsonl")})
    write(original / "tasks.jsonl", tasks)
    write(original / "candidates.jsonl", candidate_rows)
    write(original / "index.jsonl", index_rows)
    result = prepare(candidates_path=original / "candidates.jsonl", index_path=original / "index.jsonl",
                     source_root=original, exclude_inputs=baseline, out_dir=tmp_path / "complement")
    assert result["selected"] == 2
    assert result["counts"]["duplicate_complement_task_or_prompt"] == 1


def test_baseline_must_be_a_subset_of_candidate_universe(tmp_path):
    baseline, original = universe(tmp_path)
    write(original / "candidates.jsonl", list(rows(original / "candidates.jsonl"))[1:])
    with pytest.raises(ValueError, match="exact subset"):
        prepare(candidates_path=original / "candidates.jsonl", index_path=original / "index.jsonl",
                source_root=original, exclude_inputs=baseline, out_dir=tmp_path / "complement")
    assert not (tmp_path / "complement").exists()


def test_malformed_complement_is_recorded_not_silently_admitted(tmp_path):
    baseline, original = universe(tmp_path)
    candidates = list(rows(original / "candidates.jsonl"))
    candidates[1]["task_hash"] = "wrong"
    write(original / "candidates.jsonl", candidates)
    report = prepare(candidates_path=original / "candidates.jsonl", index_path=original / "index.jsonl",
                     source_root=original, exclude_inputs=baseline, out_dir=tmp_path / "complement")
    assert report["selected"] == 1
    assert report["counts"]["structurally_rejected"] == 1
    assert list(rows(tmp_path / "complement/preparation-rejected.jsonl"))[0]["old_trace_id"] == "remaining-1"


def test_exclusion_mutation_is_detected(tmp_path):
    output, _ = prepare_at(tmp_path)
    with (output / "exclusions.jsonl").open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="artifact changed"):
        validate_inputs(output, config())


def test_validator_checks_overlap_even_if_artifact_hash_is_updated(tmp_path):
    output, _ = prepare_at(tmp_path)
    task = list(rows(output / "tasks.jsonl"))[0]
    excluded = list(rows(output / "exclusions.jsonl"))
    excluded[0]["task_hash"] = canonical_hash(task)
    write(output / "exclusions.jsonl", excluded)
    path = output / "selection_report.json"
    report = json.loads(path.read_text())
    report["input_sha256"]["exclusions.jsonl"] = file_sha256(output / "exclusions.jsonl")
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="overlaps"):
        validate_inputs(output, config())


def test_cannot_bypass_supported_check_with_false_flag(tmp_path):
    output, _ = prepare_at(tmp_path)
    path = output / "selection_report.json"
    report = json.loads(path.read_text())
    report["selection_policy"] = "pretend-supported"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="require-supported"):
        validate_inputs(output, config())


def test_complement_cannot_claim_supported_answers(tmp_path):
    output, _ = prepare_at(tmp_path)
    path = output / "selection_report.json"
    report = json.loads(path.read_text())
    assert report["selection_policy"] == SELECTION_POLICY
    report["require_supported"] = True
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="masquerade"):
        validate_inputs(output, config())
