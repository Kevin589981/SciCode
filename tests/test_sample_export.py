import json
from pathlib import Path

import h5py
from scicode.pipeline.samples import export_subproblem_samples

from test_candidate_validation import make_candidate


def make_rollout(candidate_dir: Path) -> Path:
    instance = json.loads(
        (candidate_dir / "public" / "problem.jsonl").read_text(encoding="utf-8")
    )
    instance["sub_steps"].append(
        {
            "step_number": "candidate_001.2",
            "step_description_prompt": "Integrate the spectrum.",
            "function_header": "def integrate(x):\n    \"\"\"Integrate.\"\"\"",
            "test_cases": ["assert integrate(x) == target"],
            "return_line": "    return y",
            "step_background": "Use the stated units.",
        }
    )
    with h5py.File(candidate_dir / "oracle" / "targets.h5", "a") as handle:
        group = handle.create_group("candidate_001.2")
        group.create_group("test1").create_dataset("value", data=1.0)
    (candidate_dir / "public" / "prompt_snapshot" / "candidate_001.2.txt").write_text(
        "Integrate the spectrum.\n", encoding="utf-8"
    )
    rollout = {
        "run_id": 17,
        "model": "Kimi-test",
        "query_id": instance["problem_id"],
        "trial_id": 0,
        "instance": instance,
        "subtraces": [
            [
                {"role": "user", "content": "prompt one"},
                {
                    "role": "assistant",
                    "content": "```python\ndef spectrum(x):\n    return x\n```",
                    "reasoning_content": "derive the spectrum",
                    "metadata": {
                        "finish_reason": "stop",
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                    },
                },
            ],
            [
                {"role": "user", "content": "prompt two"},
                {
                    "role": "assistant",
                    "content": "```python\ndef integrate(x):\n    return x\n```",
                    "metadata": {
                        "finish_reason": "length",
                        "usage": {"prompt_tokens": 30, "completion_tokens": 40},
                    },
                },
            ],
        ],
        "reward": {"score": 0.5, "metadata": {"total_correct": 1, "total_steps": 2}},
    }
    path = candidate_dir / "rollouts.jsonl"
    path.write_text(json.dumps(rollout) + "\n", encoding="utf-8")
    return path


def test_export_expands_one_rollout_to_one_record_per_step(tmp_path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    result = export_subproblem_samples(
        candidate,
        rollouts,
        tmp_path / "registry.jsonl",
        tmp_path / "dataset.jsonl",
        target_count=10,
        summary=tmp_path / "summary.json",
    )
    assert result["accepted_samples"] == 2
    records = [
        json.loads(line)
        for line in (tmp_path / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 2
    assert records[0]["reasoning_content"] == "derive the spectrum"
    assert records[0]["metadata"]["status"] == "ok"
    assert records[1]["metadata"]["status"] == "length"
    assert records[1]["metadata"]["finish_reason"] == "length"
    assert records[1]["metadata"]["sample_key"]
    assert records[1]["context_code"]


def test_export_deduplicates_same_trace(tmp_path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    registry = tmp_path / "registry.jsonl"
    output = tmp_path / "dataset.jsonl"
    first = export_subproblem_samples(candidate, rollouts, registry, output)
    second = export_subproblem_samples(candidate, rollouts, registry, output)
    assert first["new_samples"] == 2
    assert second["new_samples"] == 0
    assert second["accepted_samples"] == 2


def test_export_extracts_inline_thinking_block(tmp_path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    payload = json.loads(rollouts.read_text(encoding="utf-8"))
    payload["subtraces"][0][1]["content"] = (
        "<think>reason inline</think>\n```python\ndef spectrum(x):\n    return x\n```"
    )
    payload["subtraces"][0][1].pop("reasoning_content", None)
    rollouts.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    export_subproblem_samples(
        candidate,
        rollouts,
        tmp_path / "registry.jsonl",
        tmp_path / "dataset.jsonl",
    )
    first = json.loads((tmp_path / "dataset.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["reasoning_content"] == "reason inline"
    assert "<think>" not in first["completion"]


def test_export_writes_overflow_after_target(tmp_path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    result = export_subproblem_samples(
        candidate,
        rollouts,
        tmp_path / "registry.jsonl",
        tmp_path / "dataset.jsonl",
        target_count=1,
    )
    assert result["accepted_samples"] == 1
    assert result["overflow_samples"] == 1
    assert (tmp_path / "dataset.jsonl.overflow.jsonl").is_file()
