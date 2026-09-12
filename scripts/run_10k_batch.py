#!/usr/bin/env python3
"""Run many isolated SciCode authoring candidates until the delivery target is met.

This is an operator-side controller.  Candidate agents only receive the
authoring prompt and their allocated worktree; scheduler settings and process
details stay in the controller state file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.candidate import CandidateValidationError  # noqa: E402
from scicode.pipeline.pipeline import PipelineError  # noqa: E402
from scicode.pipeline.worktree import (  # noqa: E402
    CandidateLock,
    WorktreeAllocation,
    WorktreeError,
    WorktreeManager,
)


SCHEMA = "scicode-batch-run-v1"
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,48}$")
ENV_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
TERMINAL_STAGES = {"done", "failed"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def parse_json_output(text: str) -> Any:
    """Parse JSON even when a CLI prints informational lines before it."""
    text = text.strip()
    if not text:
        return None
    for start in (text.find("["), text.find("{")):
        if start < 0:
            continue
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue
    return None


def parse_env_file(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
            continue
        try:
            parsed = shlex.split(value, posix=True)
            parsed_value = parsed[0] if parsed else ""
        except ValueError:
            parsed_value = value.strip().strip("\"'")
        scope = os.environ.copy()
        scope.update(values)
        values[name.strip()] = ENV_REFERENCE.sub(
            lambda match: scope.get(match.group(1) or match.group(2), match.group(0)),
            parsed_value,
        )
    return values


def dataset_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def build_kimi_command(
    kimi_bin: str, prompt: str, *, session_id: str | None = None
) -> list[str]:
    """Build a prompt-mode invocation accepted by Kimi Code 0.42+."""
    command = [kimi_bin]
    if session_id:
        command.extend(["--session", session_id])
    command.extend(["--prompt", prompt])
    return command


@dataclass
class BatchConfig:
    repository_root: str
    workspace_root: str
    delivery_root: str
    batch_root: str
    integration_branch: str = "codex/scicode-10k-pipeline"
    candidate_prefix: str = "auto"
    start_index: int = 1
    concurrency: int = 1
    target_samples: int = 10_000
    max_candidates: int | None = None
    poll_seconds: float = 30.0
    max_rounds: int = 6
    run_timeout_seconds: float = 7_200.0
    kimi_bin: str = "kimi"
    kimi_home: str | None = None
    env_file: str | None = None
    merge_accepted: bool = False
    cleanup_worktrees: bool = False
    drain: bool = True

    def validate(self) -> None:
        if not ID_PATTERN.fullmatch(self.candidate_prefix):
            raise ValueError("candidate_prefix must contain lowercase path-safe characters")
        if self.start_index < 1:
            raise ValueError("start_index must be positive")
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.target_samples < 1:
            raise ValueError("target_samples must be positive")
        if self.max_candidates is not None and self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if self.poll_seconds <= 0 or self.max_rounds < 1:
            raise ValueError("poll_seconds and max_rounds must be positive")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Job:
    candidate_id: str
    index: int
    worktree_path: str
    branch: str
    allocation_path: str
    job_root: str
    stage: str = "authoring"
    round: int = 0
    session_id: str | None = None
    pid: int | None = None
    process_started_at: str | None = None
    handoff_path: str | None = None
    run_id: str | None = None
    manifest_path: str | None = None
    rollouts_path: str | None = None
    solver_started_at: str | None = None
    last_exit_code: int | None = None
    accepted_samples: int = 0
    delivery: dict[str, Any] | None = None
    merge: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    @property
    def candidate_dir(self) -> Path:
        return Path(self.worktree_path) / "authoring" / self.candidate_id

    @property
    def active(self) -> bool:
        return self.stage not in TERMINAL_STAGES

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidate_dir"] = str(self.candidate_dir)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Job":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value[key] for key in allowed if key in value})


def _load_config_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("batch config must be a JSON object")
    return value


def _pick(args: argparse.Namespace, config: Mapping[str, Any], name: str, default: Any) -> Any:
    value = getattr(args, name)
    return value if value is not None else config.get(name, default)


def build_config(args: argparse.Namespace) -> BatchConfig:
    file_config = _load_config_file(args.config)
    workspace = _pick(args, file_config, "workspace_root", "/root/scicode-authoring")
    repository = _pick(
        args,
        file_config,
        "repository_root",
        str(Path(workspace) / "repository"),
    )
    batch_root = _pick(args, file_config, "batch_root", str(Path(workspace) / "batches"))
    delivery = _pick(args, file_config, "delivery_root", str(Path(workspace) / "delivery"))
    config = BatchConfig(
        repository_root=str(Path(repository).expanduser().resolve()),
        workspace_root=str(Path(workspace).expanduser().resolve()),
        delivery_root=str(Path(delivery).expanduser().resolve()),
        batch_root=str(Path(batch_root).expanduser().resolve()),
        integration_branch=_pick(args, file_config, "integration_branch", "codex/scicode-10k-pipeline"),
        candidate_prefix=_pick(args, file_config, "candidate_prefix", "auto"),
        start_index=int(_pick(args, file_config, "start_index", 1)),
        concurrency=int(_pick(args, file_config, "concurrency", 1)),
        target_samples=int(_pick(args, file_config, "target_samples", 10_000)),
        max_candidates=(
            None
            if _pick(args, file_config, "max_candidates", None) is None
            else int(_pick(args, file_config, "max_candidates", None))
        ),
        poll_seconds=float(_pick(args, file_config, "poll_seconds", 30.0)),
        max_rounds=int(_pick(args, file_config, "max_rounds", 6)),
        run_timeout_seconds=float(_pick(args, file_config, "run_timeout_seconds", 7_200.0)),
        kimi_bin=str(_pick(args, file_config, "kimi_bin", os.getenv("KIMI_BIN", "kimi"))),
        kimi_home=_pick(args, file_config, "kimi_home", os.getenv("KIMI_CODE_HOME")),
        env_file=_pick(args, file_config, "env_file", os.getenv("SCICODE_ENV_FILE")),
        merge_accepted=bool(_pick(args, file_config, "merge_accepted", False)),
        cleanup_worktrees=bool(_pick(args, file_config, "cleanup_worktrees", False)),
        drain=False if args.no_drain else bool(file_config.get("drain", True)),
    )
    config.validate()
    return config


class BatchRunner:
    def __init__(self, config: BatchConfig, batch_id: str, *, resume: bool = False) -> None:
        self.config = config
        self.batch_id = batch_id
        self.batch_dir = Path(config.batch_root) / batch_id
        self.state_path = self.batch_dir / "state.json"
        self.jobs_dir = self.batch_dir / "jobs"
        self.jobs: dict[str, Job] = {}
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.log_streams: dict[str, Any] = {}
        self.next_index = config.start_index
        self.stop_requested = False
        self.created_at = now()
        self.base_env = os.environ.copy()
        self.base_env.update(parse_env_file(Path(config.env_file) if config.env_file else None))
        self.manager = WorktreeManager(config.repository_root, config.workspace_root)
        if resume:
            self._load_state()
        else:
            self.batch_dir.mkdir(parents=True, exist_ok=True)
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            self._persist()

    @classmethod
    def from_state(cls, path: Path) -> "BatchRunner":
        state = read_json(path)
        config = BatchConfig(**state["config"])
        runner = cls(config, str(state["batch_id"]), resume=True)
        return runner

    def _load_state(self) -> None:
        state = read_json(self.state_path)
        self.created_at = str(state.get("created_at", self.created_at))
        self.next_index = int(state.get("next_index", self.config.start_index))
        self.jobs = {
            item["candidate_id"]: Job.from_dict(item)
            for item in state.get("jobs", [])
        }
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def _persist(self) -> None:
        accepted = dataset_count(Path(self.config.delivery_root) / "dataset.jsonl")
        state = {
            "schema": SCHEMA,
            "batch_id": self.batch_id,
            "created_at": self.created_at,
            "updated_at": now(),
            "config": self.config.as_dict(),
            "next_index": self.next_index,
            "accepted_samples": accepted,
            "jobs": [job.as_dict() for job in self.jobs.values()],
        }
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.state_path, state)

    def _job_log(self, job: Job, name: str) -> Path:
        path = Path(job.job_root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _candidate_id(self, index: int) -> str:
        return f"{self.config.candidate_prefix}-{index:06d}"

    def _scaffold_candidate(self, job: Job) -> None:
        root = job.candidate_dir
        for relative in (
            "public/solver_payload",
            "public/checks",
            "public/prompt_snapshot",
            "source_notes",
            "author",
            "reference",
            "oracle",
            "iterations",
            "validation",
            "runs",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)

    def _new_job(self) -> Job:
        attempts = 0
        while attempts < 1000:
            index = self.next_index
            self.next_index += 1
            attempts += 1
            candidate_id = self._candidate_id(index)
            if candidate_id in self.jobs:
                continue
            try:
                allocation = self.manager.create(
                    candidate_id,
                    base_ref=self.config.integration_branch,
                )
            except WorktreeError:
                continue
            job_root = self.jobs_dir / candidate_id
            job_root.mkdir(parents=True, exist_ok=True)
            allocation_path = job_root / "allocation.json"
            write_json_atomic(allocation_path, allocation.as_dict())
            job = Job(
                candidate_id=candidate_id,
                index=index,
                worktree_path=allocation.worktree_path,
                branch=allocation.branch,
                allocation_path=str(allocation_path),
                job_root=str(job_root),
            )
            self._scaffold_candidate(job)
            self.jobs[candidate_id] = job
            return job
        raise RuntimeError("could not allocate a fresh candidate worktree")

    def _prepare_kimi_home(self, job: Job) -> Path | None:
        base = Path(self.config.kimi_home).expanduser() if self.config.kimi_home else None
        if base is None:
            return None
        if not base.is_dir():
            raise RuntimeError(f"Kimi Code home does not exist: {base}")
        target = Path(job.job_root) / "kimi-home"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("config.toml", "device_id"):
            source = base / name
            destination = target / name
            if source.exists() and not destination.exists():
                destination.symlink_to(source)
        source_bin = base / "bin"
        destination_bin = target / "bin"
        if source_bin.exists() and not destination_bin.exists():
            destination_bin.symlink_to(source_bin, target_is_directory=True)
        for name in ("cache", "file-history", "logs", "sessions", "telemetry", "updates"):
            (target / name).mkdir(exist_ok=True)
        (target / "session_index.jsonl").touch(exist_ok=True)
        workspaces = target / "workspaces.json"
        if not workspaces.exists():
            workspaces.write_text(
                json.dumps({"version": 1, "workspaces": {}, "deleted_workspace_ids": []})
                + "\n",
                encoding="utf-8",
            )
        return target

    def _child_env(self, job: Job) -> dict[str, str]:
        env = dict(self.base_env)
        home = self._prepare_kimi_home(job)
        if home is not None:
            env["KIMI_CODE_HOME"] = str(home)
        env["SCICODE_BATCH_ID"] = self.batch_id
        env["SCICODE_CANDIDATE_ID"] = job.candidate_id
        env["SCICODE_CANDIDATE_DIR"] = str(job.candidate_dir)
        # Provider profiles commonly use these aliases.  Do not print them or
        # write their values into the batch state.
        if env.get("API_KEY"):
            env.setdefault("OPENAI_API_KEY", env["API_KEY"])
            env.setdefault("KIMI_MODEL_API_KEY", env["API_KEY"])
        if env.get("BASE_URL"):
            env.setdefault("KIMI_MODEL_BASE_URL", env["BASE_URL"])
        if env.get("MODEL"):
            env.setdefault("KIMI_MODEL", env["MODEL"])
            env.setdefault("KIMI_MODEL_NAME", env["MODEL"])
        if env.get("KIMI_MODEL"):
            env.setdefault("MODEL", env["KIMI_MODEL"])
        return env

    def _author_prompt(self, job: Job, *, resume: bool, reason: str | None = None) -> str:
        candidate_dir = job.candidate_dir
        if resume:
            opening = (
                "The framework has closed the latest solver run for this candidate. "
                "Read the newest run manifest and exported rollouts before acting."
            )
        else:
            opening = "Start the complete authoring cycle for this candidate."
        if reason:
            opening += f" Previous controller note: {reason}"
        return f"""{opening}

Candidate id: {job.candidate_id}
Candidate directory: {candidate_dir}
Work only in the allocated candidate worktree. Read AGENTS.md and the relevant
repository skills, then follow their scientific authoring and review commands.
Create a genuinely difficult strict SciCode candidate from an allowed external
scientific source. Keep private oracle/reference material private and never
use official SciCode tasks or official test data.

Complete the public-only child review, local checks, and AvaCore handoff. When
the solver run is pending, finish the current authoring turn after recording
the handoff artifacts; do not call the provider directly and do not edit the
shared checkout. When the framework resumes this candidate, inspect every
subproblem trace, including reasoning and token usage, then revise or accept
the candidate according to AGENTS.md. Leave a clear release decision and all
candidate artifacts before declaring the candidate complete. Do not push.
"""

    def _session_id(self, job: Job, env: Mapping[str, str]) -> str | None:
        command = [
            self.config.kimi_bin,
            "session",
            "list",
            "--cwd",
            str(job.worktree_path),
            "--json",
            "--limit",
            "20",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=job.worktree_path,
                env=dict(env),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        value = parse_json_output(result.stdout)
        if not isinstance(value, list):
            return None
        candidates = [
            item
            for item in value
            if isinstance(item, Mapping)
            and Path(str(item.get("workDir", ""))).resolve()
            == Path(job.worktree_path).resolve()
        ]
        if not candidates:
            return None
        def updated_at(item: Mapping[str, Any]) -> float:
            try:
                return float(item.get("updatedAt", 0))
            except (TypeError, ValueError):
                return 0.0

        candidates.sort(key=updated_at)
        identifier = candidates[-1].get("id")
        return str(identifier) if identifier else None

    def _launch(self, job: Job, *, resume: bool, reason: str | None = None) -> None:
        if job.round >= self.config.max_rounds:
            self._fail(job, "maximum authoring rounds reached")
            return
        env = self._child_env(job)
        if resume:
            if not job.session_id:
                job.session_id = self._session_id(job, env)
            if not job.session_id:
                self._fail(job, "cannot find Kimi Code session for resume")
                return
        # Prompt mode is already non-interactive in the installed Kimi Code
        # executable. Its approval flags cannot be combined with --prompt.
        command = build_kimi_command(
            self.config.kimi_bin,
            self._author_prompt(job, resume=resume, reason=reason),
            session_id=job.session_id if resume else None,
        )
        log_path = self._job_log(job, f"author-round-{job.round + 1}.log")
        log_stream = log_path.open("ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=job.worktree_path,
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
        except Exception:
            log_stream.close()
            raise
        # Keep the stream open until the child exits.  The descriptor is held
        # by this controller and closed in _finish_process.
        self.processes[job.candidate_id] = process
        self.log_streams[job.candidate_id] = log_stream
        job.pid = process.pid
        job.process_started_at = now()
        job.round += 1
        job.stage = "reviewing" if resume else "authoring"
        job.error = None
        job.updated_at = now()

    def _finish_process(self, job: Job) -> bool:
        process = self.processes.get(job.candidate_id)
        if process is None:
            return not pid_alive(job.pid)
        code = process.poll()
        if code is None:
            return False
        job.last_exit_code = code
        job.pid = None
        self.processes.pop(job.candidate_id, None)
        stream = self.log_streams.pop(job.candidate_id, None)
        if stream is not None:
            stream.close()
        job.updated_at = now()
        return True

    def _latest_handoff(self, job: Job) -> Path | None:
        if not job.candidate_dir.is_dir():
            return None
        paths = list(job.candidate_dir.glob("runs/*/handoff.json"))
        if not paths:
            return None
        return max(paths, key=lambda path: path.stat().st_mtime_ns)

    def _manifest_for(self, handoff: Path) -> tuple[Path | None, Path | None, str | None]:
        try:
            value = read_json(handoff)
        except (OSError, ValueError):
            return None, None, None
        output_value = value.get("output_dir")
        if not output_value:
            return None, None, None
        output_dir = Path(str(output_value))
        manifest = output_dir / "manifest.json"
        export_value = value.get("export_path") or (output_dir / "rollouts.jsonl")
        export = Path(str(export_value))
        return (manifest if manifest.is_file() else None, export, str(value.get("run_id")))

    def _release_accepted(self, job: Job) -> bool:
        path = job.candidate_dir / "validation" / "release_decision.json"
        if not path.is_file():
            return False
        try:
            value = read_json(path)
        except (OSError, ValueError):
            return False
        return str(value.get("status", value.get("decision", ""))).lower() in {
            "accepted",
            "approved",
            "accepted_for_delivery",
        }

    def _trace_ready(self, job: Job) -> bool:
        if not job.manifest_path or not job.rollouts_path:
            return False
        try:
            manifest = read_json(Path(job.manifest_path))
        except (OSError, ValueError):
            return False
        return (
            manifest.get("status") == "finished"
            and manifest.get("trace_complete") is True
            and Path(job.rollouts_path).is_file()
            and Path(job.rollouts_path).stat().st_size > 0
        )

    def _deliver(self, job: Job) -> None:
        # The exporter performs a read/modify/write over the shared dataset and
        # registry.  Serialize that operation across otherwise independent
        # candidate workers so concurrent deliveries cannot lose rows.
        delivery_lock = CandidateLock(
            Path(self.config.workspace_root) / ".locks" / "dataset.delivery.lock",
            candidate_id="dataset-delivery",
        )
        try:
            delivery_lock.acquire()
        except WorktreeError:
            job.error = "delivery lock is busy; retrying"
            job.updated_at = now()
            return
        if not self._trace_ready(job):
            try:
                self._fail(job, "release decision exists without a complete trace")
            finally:
                delivery_lock.release()
            return
        try:
            delivery_root = Path(self.config.delivery_root)
            command = [
                sys.executable,
                str(Path(self.config.repository_root) / "scripts" / "run_candidate_pipeline.py"),
                "--candidate-dir",
                str(job.candidate_dir),
                "--delivery-root",
                str(delivery_root),
                "--rollouts",
                str(job.rollouts_path),
                "--run-manifest",
                str(job.manifest_path),
                "--target-count",
                str(self.config.target_samples),
            ]
            env = self._child_env(job)
            env["PYTHONPATH"] = os.pathsep.join(
                part
                for part in (str(Path(self.config.repository_root) / "src"), env.get("PYTHONPATH", ""))
                if part
            )
            log_path = self._job_log(job, "delivery.log")
            result = subprocess.run(
                command,
                cwd=self.config.repository_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
            value = parse_json_output(result.stdout)
            if result.returncode != 0 or not isinstance(value, Mapping):
                self._fail(job, f"delivery failed; inspect {log_path}")
                return
            job.delivery = dict(value)
            export = value.get("export") if isinstance(value.get("export"), Mapping) else value
            job.accepted_samples = int(export.get("accepted_samples", 0))
            job.stage = "done"
            job.updated_at = now()
        finally:
            try:
                delivery_lock.release()
            except WorktreeError as exc:
                job.error = f"delivery lock release failed: {exc}"
        if job.stage == "done" and self.config.merge_accepted:
            self._merge(job)
        elif job.stage == "done" and self.config.cleanup_worktrees:
            job.error = "cleanup requested without merge; worktree retained"

    def _merge(self, job: Job) -> None:
        command = [
            sys.executable,
            str(Path(self.config.repository_root) / "scripts" / "merge_candidate.py"),
            "--repository",
            self.config.repository_root,
            "--candidate-branch",
            job.branch,
            "--integration-branch",
            self.config.integration_branch,
            "--lock-root",
            self.config.workspace_root,
            "--report",
            str(Path(job.job_root) / "merge.json"),
        ]
        result = subprocess.run(
            command,
            cwd=self.config.repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        value = parse_json_output(result.stdout)
        job.merge = dict(value) if isinstance(value, Mapping) else {
            "status": "error",
            "stderr": result.stderr[-4000:],
        }
        if self.config.cleanup_worktrees and job.merge.get("status") == "merged":
            allocation = WorktreeAllocation(**read_json(Path(job.allocation_path)))
            try:
                self.manager.cleanup_copy(allocation)
            except WorktreeError as exc:
                job.error = f"cleanup failed: {exc}"

    def _fail(self, job: Job, message: str) -> None:
        job.stage = "failed"
        job.error = message
        job.updated_at = now()
        log_path = self._job_log(job, "failure.txt")
        log_path.write_text(message + "\n", encoding="utf-8")

    def _advance(self, job: Job) -> None:
        if not job.active:
            return
        process_finished = self._finish_process(job)
        handoff = self._latest_handoff(job)
        handoff_is_new = handoff is not None and str(handoff) != job.handoff_path
        if handoff is not None and str(handoff) != job.handoff_path:
            job.handoff_path = str(handoff)
            job.solver_started_at = now()
            job.updated_at = now()

        if job.stage == "authoring":
            if not process_finished:
                return
            if handoff is not None:
                job.stage = "waiting_solver"
                job.updated_at = now()
                return
            if self._release_accepted(job):
                job.stage = "delivery"
                self._deliver(job)
                return
            if job.round < self.config.max_rounds:
                self._launch(
                    job,
                    resume=True,
                    reason="The previous authoring turn ended before a solver handoff; continue from the artifacts.",
                )
            else:
                self._fail(job, "authoring process ended without a solver handoff")
            return

        if job.stage == "reviewing":
            if not process_finished:
                return
            if handoff_is_new:
                job.stage = "waiting_solver"
                job.updated_at = now()
                return
            if self._release_accepted(job):
                job.stage = "delivery"
                self._deliver(job)
                return
            if job.round < self.config.max_rounds:
                self._launch(
                    job,
                    resume=True,
                    reason="The review turn ended without a new solver handoff or release decision; continue inspecting the candidate artifacts.",
                )
            else:
                self._fail(job, "review process ended without a new solver handoff or release decision")
            return

        if job.stage == "waiting_solver":
            if not process_finished and job.pid:
                return
            if handoff is None:
                self._fail(job, "solver handoff disappeared")
                return
            manifest, export, run_id = self._manifest_for(handoff)
            if manifest is None:
                if job.solver_started_at:
                    started = datetime.fromisoformat(job.solver_started_at).timestamp()
                    if time.time() - started > self.config.run_timeout_seconds:
                        self._fail(job, "solver run exceeded controller timeout without a manifest")
                return
            job.manifest_path = str(manifest)
            job.rollouts_path = str(export) if export else None
            job.run_id = run_id
            if job.round < self.config.max_rounds:
                self._launch(job, resume=True)
            else:
                self._fail(job, "maximum authoring rounds reached after solver run")
            return

        if job.stage == "delivery":
            self._deliver(job)

    def _active_jobs(self) -> list[Job]:
        return [job for job in self.jobs.values() if job.active]

    def _fill_slots(self) -> None:
        accepted = dataset_count(Path(self.config.delivery_root) / "dataset.jsonl")
        while accepted < self.config.target_samples:
            active = len(self._active_jobs())
            if active >= self.config.concurrency:
                return
            if self.config.max_candidates is not None and len(self.jobs) >= self.config.max_candidates:
                return
            job = self._new_job()
            self._launch(job, resume=False)
            accepted = dataset_count(Path(self.config.delivery_root) / "dataset.jsonl")
            self._persist()

    def run(self) -> int:
        def stop_handler(_signum: int, _frame: Any) -> None:
            self.stop_requested = True

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)
        while not self.stop_requested:
            self._fill_slots()
            for job in list(self.jobs.values()):
                self._advance(job)
            self._persist()
            accepted = dataset_count(Path(self.config.delivery_root) / "dataset.jsonl")
            active = self._active_jobs()
            if accepted >= self.config.target_samples and (not self.config.drain or not active):
                return 0
            if not active:
                if self.config.max_candidates is not None and len(self.jobs) >= self.config.max_candidates:
                    return 3
                if accepted < self.config.target_samples:
                    # _fill_slots will allocate the next candidate on the next loop.
                    time.sleep(min(self.config.poll_seconds, 1.0))
                    continue
            time.sleep(self.config.poll_seconds)
        self._persist()
        return 130

    def dry_run(self) -> int:
        active = min(self.config.concurrency, self.config.max_candidates or self.config.concurrency)
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "mode": "dry_run",
                    "repository_root": self.config.repository_root,
                    "workspace_root": self.config.workspace_root,
                    "target_samples": self.config.target_samples,
                    "concurrency": self.config.concurrency,
                    "planned_candidates": [
                        self._candidate_id(self.config.start_index + index)
                        for index in range(active)
                    ],
                    "kimi_bin": self.config.kimi_bin,
                    "kimi_home": self.config.kimi_home,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path)
    result.add_argument("--resume-batch", type=Path)
    result.add_argument("--repository-root")
    result.add_argument("--workspace-root")
    result.add_argument("--delivery-root")
    result.add_argument("--batch-root")
    result.add_argument("--integration-branch")
    result.add_argument("--candidate-prefix")
    result.add_argument("--start-index", type=int)
    result.add_argument("--concurrency", type=int)
    result.add_argument("--target-samples", type=int)
    result.add_argument("--max-candidates", type=int)
    result.add_argument("--poll-seconds", type=float)
    result.add_argument("--max-rounds", type=int)
    result.add_argument("--run-timeout-seconds", type=float)
    result.add_argument("--kimi-bin")
    result.add_argument("--kimi-home")
    result.add_argument("--env-file")
    result.add_argument("--merge-accepted", action="store_true", default=None)
    result.add_argument("--cleanup-worktrees", action="store_true", default=None)
    result.add_argument("--no-drain", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.resume_batch:
        runner = BatchRunner.from_state(args.resume_batch / "state.json" if args.resume_batch.is_dir() else args.resume_batch)
    else:
        config = build_config(args)
        batch_id = f"scicode-batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        runner = BatchRunner(config, batch_id)
    if args.dry_run:
        return runner.dry_run()
    try:
        return runner.run()
    except (OSError, ValueError, WorktreeError, CandidateValidationError, PipelineError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "error", "error": str(exc)}, ensure_ascii=False))
        runner._persist()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
