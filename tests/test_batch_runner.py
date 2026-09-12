from pathlib import Path
import sys


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_10k_batch import (  # noqa: E402
    BatchConfig,
    BatchRunner,
    Job,
    build_kimi_command,
    build_config,
    parse_env_file,
    parse_json_output,
    parser,
)


def test_parse_env_file_supports_exports_and_quotes(tmp_path):
    path = tmp_path / "run.env"
    path.write_text(
        "# comment\nexport API_KEY='secret value'\nMODEL=Kimi-K3\n\ninvalid\n",
        encoding="utf-8",
    )
    assert parse_env_file(path) == {"API_KEY": "secret value", "MODEL": "Kimi-K3"}


def test_parse_env_file_expands_previous_values(tmp_path):
    path = tmp_path / "run.env"
    path.write_text("HTTP_PROXY=http://proxy\nHTTPS_PROXY=$HTTP_PROXY\n", encoding="utf-8")
    assert parse_env_file(path)["HTTPS_PROXY"] == "http://proxy"


def test_parse_json_output_ignores_prefix():
    assert parse_json_output("notice\n{\"status\": \"ok\"}") == {"status": "ok"}
    assert parse_json_output("notice\n[1, 2]") == [1, 2]


def test_kimi_command_uses_prompt_mode_without_incompatible_approval_flags():
    command = build_kimi_command("/opt/kimi", "inspect the candidate")
    resumed = build_kimi_command("/opt/kimi", "continue", session_id="session-1")
    assert command == ["/opt/kimi", "--prompt", "inspect the candidate"]
    assert resumed == ["/opt/kimi", "--session", "session-1", "--prompt", "continue"]
    assert "--auto" not in command and "--yolo" not in command


def test_config_accepts_cli_overrides():
    args = parser().parse_args(
        [
            "--workspace-root",
            "/tmp/work",
            "--repository-root",
            "/tmp/repo",
            "--batch-root",
            "/tmp/batches",
            "--delivery-root",
            "/tmp/delivery",
            "--concurrency",
            "3",
            "--target-samples",
            "17",
            "--run-timeout-seconds",
            "7200",
        ]
    )
    config = build_config(args)
    assert config.concurrency == 3
    assert config.target_samples == 17
    assert config.run_timeout_seconds == 7200


def test_job_round_trip_keeps_solver_state():
    job = Job(
        candidate_id="auto-000001",
        index=1,
        worktree_path="/tmp/candidate",
        branch="author/auto-000001",
        allocation_path="/tmp/allocation.json",
        job_root="/tmp/job",
        stage="waiting_solver",
        run_id="run-1",
        solver_started_at="2026-09-12T00:00:00+00:00",
    )
    restored = Job.from_dict(job.as_dict())
    assert restored.stage == "waiting_solver"
    assert restored.run_id == "run-1"
    assert restored.solver_started_at == "2026-09-12T00:00:00+00:00"


def test_config_defaults_are_operator_side_only():
    config = BatchConfig(
        repository_root="/repo",
        workspace_root="/workspace",
        delivery_root="/delivery",
        batch_root="/batches",
    )
    config.validate()
    assert config.concurrency == 1
    assert config.target_samples == 10_000


def test_child_env_maps_runtime_provider_aliases_without_serializing_values():
    runner = object.__new__(BatchRunner)
    runner.base_env = {
        "API_KEY": "secret",
        "BASE_URL": "http://model.internal/v1",
        "MODEL": "model-name",
        "KIMI_MODEL": "wrong-dynamic-alias",
        "KIMI_MODEL_BASE_URL": "http://wrong-model-host/v1",
        "HTTP_PROXY": "http://source-proxy",
        "NO_PROXY": "localhost,.cn",
    }
    runner.config = BatchConfig(
        repository_root="/repo",
        workspace_root="/workspace",
        delivery_root="/delivery",
        batch_root="/batches",
    )
    runner.batch_id = "batch-1"
    job = Job(
        candidate_id="auto-000001",
        index=1,
        worktree_path="/workspace/candidate",
        branch="author/auto-000001",
        allocation_path="/workspace/allocation.json",
        job_root="/workspace/job",
    )
    env = runner._child_env(job)
    assert env["OPENAI_API_KEY"] == "secret"
    assert env["MODEL"] == "model-name"
    assert env["BASE_URL"] == "http://model.internal/v1"
    assert env["HTTP_PROXY"] == "http://source-proxy"
    assert env["NO_PROXY"] == "localhost,.cn"
    assert "KIMI_MODEL" not in env
    assert "KIMI_MODEL_BASE_URL" not in env
    assert not any(name.startswith("KIMI_MODEL_") for name in env)
    assert env["SCICODE_CANDIDATE_ID"] == "auto-000001"


def test_child_env_promotes_solver_model_alias_before_removing_dynamic_name():
    runner = object.__new__(BatchRunner)
    runner.base_env = {"KIMI_MODEL": "solver-alias"}
    runner.config = BatchConfig(
        repository_root="/repo",
        workspace_root="/workspace",
        delivery_root="/delivery",
        batch_root="/batches",
    )
    runner.batch_id = "batch-1"
    job = Job(
        candidate_id="auto-000001",
        index=1,
        worktree_path="/workspace/candidate",
        branch="author/auto-000001",
        allocation_path="/workspace/allocation.json",
        job_root="/workspace/job",
    )
    env = runner._child_env(job)
    assert env["MODEL"] == "solver-alias"
    assert "KIMI_MODEL" not in env
