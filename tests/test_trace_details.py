import hashlib
import json
import subprocess
import sys
from pathlib import Path

from test_sample_export import make_rollout
from test_candidate_validation import make_candidate


SCRIPT = Path(__file__).parents[1] / "scripts" / "inspect_trace.py"
sys.path.insert(0, str(SCRIPT.parent))


def test_detail_reader_reports_each_step_without_provider_imports(tmp_path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    rollouts.write_text(rollouts.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    output = tmp_path / "trace_details.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--candidate-dir",
            str(candidate),
            "--rollouts",
            str(rollouts),
            "--output",
            str(output),
            "--max-text-chars",
            "40",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)
    assert result.returncode == 2
    assert summary["schema"] == "scicode-trace-details-v1"
    assert summary["output"] == str(output.resolve())
    details = json.loads(output.read_text(encoding="utf-8"))
    assert details["summary"]["total_steps"] == 2
    assert details["rollouts"][0]["steps"][0]["code_extracted"] is True
    assert details["rollouts"][0]["steps"][0]["syntax_ok"] is True
    assert "missing reasoning content" in details["rollouts"][0]["steps"][1]["warnings"]
    assert details["rollouts"][0]["steps"][0]["content_truncated"] is True
    assert details["rollouts_sha256"] == hashlib.sha256(rollouts.read_bytes()).hexdigest()


def test_detail_reader_handles_mapping_wrappers(tmp_path):
    rollouts = tmp_path / "rollouts.jsonl"
    row = {
        "run_id": "run-1",
        "query_id": "p-1",
        "model": "test",
        "instance": {"problem_id": "p-1", "sub_steps": [{"step_number": "p-1.1"}]},
        "subtraces": [
            {
                "messages": [
                    {"role": "user", "content": "prompt"},
                    {
                        "role": "assistant",
                        "content": "```python\ndef f(x):\n    return x\n```",
                        "reasoning": "derive",
                        "metadata": {
                            "finish_reason": "stop",
                            "usage": {"total_tokens": 3},
                        },
                    },
                ]
            }
        ],
        "reward": {"score": 1, "target_value": 99},
    }
    rollouts.write_text(json.dumps(row) + "\n", encoding="utf-8")
    from inspect_trace import inspect_rollouts

    details = inspect_rollouts(rollouts, max_text_chars=0)
    step = details["rollouts"][0]["steps"][0]
    assert step["code_extracted"] is True
    assert step["syntax_ok"] is True
    assert details["rollouts"][0]["evaluator"] == {"score": 1}


def test_detail_reader_uses_exporter_text_rules(tmp_path):
    rollouts = tmp_path / "rollouts.jsonl"
    row = {
        "instance": {"problem_id": "p-1", "sub_steps": [{"step_number": "p-1.1"}]},
        "subtraces": [[
            {"role": "user", "content": "prompt"},
            {
                "role": "assistant",
                "content": "<think>derive</think>\n\nasync def f(x):\n    return x",
                "metadata": {"finish_reason": "stop", "usage": {"total_tokens": 3}},
            },
        ]],
    }
    rollouts.write_text(json.dumps(row) + "\n", encoding="utf-8")
    from inspect_trace import inspect_rollouts

    details = inspect_rollouts(rollouts)
    step = details["rollouts"][0]["steps"][0]
    assert details["status"] == "ok"
    assert step["reasoning_content"] == "derive"
    assert step["code_extracted"] is True
    assert step["syntax_ok"] is True


def test_detail_reader_fails_on_incomplete_subtrace_count(tmp_path):
    rollouts = tmp_path / "rollouts.jsonl"
    row = {
        "instance": {
            "problem_id": "p-1",
            "sub_steps": [{"step_number": "p-1.1"}, {"step_number": "p-1.2"}],
        },
        "subtraces": [],
    }
    rollouts.write_text(json.dumps(row) + "\n", encoding="utf-8")
    from inspect_trace import inspect_rollouts

    details = inspect_rollouts(rollouts)
    assert details["status"] == "failed"
    assert details["summary"]["failure_count"] == 1


def test_detail_reader_does_not_overwrite_input(tmp_path):
    rollouts = tmp_path / "rollouts.jsonl"
    rollouts.write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--rollouts", str(rollouts), "--output", str(rollouts)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert rollouts.read_text(encoding="utf-8") == "{}\n"


def test_detail_reader_does_not_overwrite_problem_file(tmp_path):
    candidate = make_candidate(tmp_path)
    rollouts = make_rollout(candidate)
    problem_file = candidate / "public" / "problem.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--candidate-dir",
            str(candidate),
            "--rollouts",
            str(rollouts),
            "--output",
            str(problem_file),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "output must not overwrite problem file" in result.stdout
