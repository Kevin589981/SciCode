import json
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


class FinishedProcess:
    def __init__(self, code=0):
        self.code = code

    def poll(self):
        return self.code


class RunningProcess:
    def poll(self):
        return None


def _write_handoff(path, run_id):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"run_id": run_id, "output_dir": str(path.parent), "export_path": str(path.parent / "rollouts.jsonl")}
        ),
        encoding="utf-8",
    )


def test_reviewing_binds_new_handoff_after_process_finishes(tmp_path):
    worktree = tmp_path / "worktree"
    candidate_dir = worktree / "authoring" / "auto-000001"
    r1 = candidate_dir / "runs" / "run-r1" / "handoff.json"
    r2 = candidate_dir / "runs" / "run-r2" / "handoff.json"
    _write_handoff(r1, "run-r1")
    _write_handoff(r2, "run-r2")
    job = Job(
        candidate_id="auto-000001",
        index=1,
        worktree_path=str(worktree),
        branch="author/auto-000001",
        allocation_path=str(tmp_path / "allocation.json"),
        job_root=str(tmp_path / "job"),
        stage="reviewing",
        round=2,
        handoff_path=str(r1),
        run_id="run-r1",
        manifest_path=str(r1.parent / "manifest.json"),
        rollouts_path=str(r1.parent / "rollouts.jsonl"),
        pid=123,
    )
    runner = object.__new__(BatchRunner)
    runner.processes = {job.candidate_id: RunningProcess()}
    runner.log_streams = {}
    runner.jobs = {job.candidate_id: job}
    runner.config = BatchConfig(
        repository_root=str(tmp_path / "repo"),
        workspace_root=str(tmp_path / "workspace"),
        delivery_root=str(tmp_path / "delivery"),
        batch_root=str(tmp_path / "batches"),
        max_rounds=6,
    )
    runner.batch_id = "batch-1"

    runner._advance(job)

    assert job.stage == "reviewing"
    assert job.handoff_path == str(r1)
    assert job.manifest_path == str(r1.parent / "manifest.json")

    runner.processes[job.candidate_id] = FinishedProcess()
    runner._advance(job)

    assert job.stage == "waiting_solver"
    assert job.handoff_path == str(r2)
    assert job.run_id is None
    assert job.manifest_path is None
    assert job.rollouts_path is None


def test_release_handoff_prefers_release_run_id(tmp_path):
    worktree = tmp_path / "worktree"
    candidate_dir = worktree / "authoring" / "auto-000001"
    r1 = candidate_dir / "runs" / "run-r1" / "handoff.json"
    r2 = candidate_dir / "runs" / "run-r2" / "handoff.json"
    _write_handoff(r1, "run-r1")
    _write_handoff(r2, "run-r2")
    decision = candidate_dir / "validation" / "release_decision.json"
    decision.parent.mkdir(parents=True, exist_ok=True)
    decision.write_text(json.dumps({"run_id": "run-r1", "decision": "accepted"}), encoding="utf-8")
    job = Job(
        candidate_id="auto-000001",
        index=1,
        worktree_path=str(worktree),
        branch="author/auto-000001",
        allocation_path=str(tmp_path / "allocation.json"),
        job_root=str(tmp_path / "job"),
    )
    runner = object.__new__(BatchRunner)
    assert runner._release_handoff(job) == r1


def test_bind_run_artifacts_rejects_manifest_from_prior_revision(tmp_path):
    worktree = tmp_path / "worktree"
    candidate_dir = worktree / "authoring" / "auto-000001"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "candidate_manifest.json").write_text(
        json.dumps({"visible_contract_sha256": "current"}), encoding="utf-8"
    )
    handoff = candidate_dir / "runs" / "run-r1" / "handoff.json"
    _write_handoff(handoff, "run-r1")
    (handoff.parent / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-r1",
                "candidate": {"visible_contract_sha256": "prior"},
            }
        ),
        encoding="utf-8",
    )
    job = Job(
        candidate_id="auto-000001",
        index=1,
        worktree_path=str(worktree),
        branch="author/auto-000001",
        allocation_path=str(tmp_path / "allocation.json"),
        job_root=str(tmp_path / "job"),
    )
    runner = object.__new__(BatchRunner)
    assert runner._bind_run_artifacts(job, handoff) is False
