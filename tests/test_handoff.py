from scicode.pipeline.handoff import HandoffError, build_avacore_command


def test_handoff_command_is_strict_and_credential_free():
    command = build_avacore_command(
        avacore_python="/opt/ava/.venv/bin/python",
        runner="/repo/eval/avacore/scicode_avacore.py",
        candidate_dir="/workspace/candidate",
        output_dir="/workspace/candidate/runs/run-1",
        export_path="/workspace/candidate/runs/run-1/rollouts.jsonl",
        run_id="run-1",
        base_url="http://10.100.184.127:5050",
        model="Kimi-K3",
        temperature=0.6,
        max_tokens=262144,
        timeout=7200,
        http_retries=5,
        concurrency=1,
    )
    assert "--candidate-dir" in command
    assert "--score-by-subproblem" in command
    assert "--max-tokens" in command
    assert "--api-key" not in command
    assert "--postgres" not in command
    assert "--with-background" not in command


def test_handoff_rejects_unsafe_run_id():
    try:
        build_avacore_command(
            avacore_python="python",
            runner="runner.py",
            candidate_dir="candidate",
            output_dir="out",
            export_path="out/rollouts.jsonl",
            run_id="../escape",
            base_url="http://localhost:1",
            model="test",
            temperature=0.6,
            max_tokens=100,
            timeout=1,
            http_retries=0,
            concurrency=1,
        )
    except HandoffError:
        return
    raise AssertionError("unsafe run id was accepted")
