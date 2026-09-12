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
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)
    assert summary["schema"] == "scicode-trace-details-v1"
    assert summary["output"] == str(output.resolve())
    details = json.loads(output.read_text(encoding="utf-8"))
    assert details["summary"]["total_steps"] == 2
    assert details["rollouts"][0]["steps"][0]["code_extracted"] is True
    assert details["rollouts"][0]["steps"][0]["syntax_ok"] is True
    assert "missing reasoning content" in details["rollouts"][0]["steps"][1]["warnings"]
    assert details["rollouts"][0]["steps"][0]["content_truncated"] is True


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
