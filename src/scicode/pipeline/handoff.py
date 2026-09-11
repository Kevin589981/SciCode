"""Detached AvaCore handoff used by the Kimi Code authoring controller."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .candidate import CandidateManifest, load_candidate
from .run_manifest import safe_endpoint


class HandoffError(RuntimeError):
    """Raised when a strict AvaCore handoff cannot be safely submitted."""


def _safe_run_id(value: str) -> str:
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value):
        raise HandoffError("run id contains unsafe characters")
    return value


def build_avacore_command(
    *,
    avacore_python: str | Path,
    runner: str | Path,
    candidate_dir: str | Path,
    output_dir: str | Path,
    export_path: str | Path,
    run_id: str,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    http_retries: int,
    concurrency: int,
) -> list[str]:
    """Build a credential-free strict command for the detached child process."""
    _safe_run_id(run_id)
    return [
        str(avacore_python),
        str(runner),
        "--candidate-dir",
        str(Path(candidate_dir).resolve()),
        "--base-url",
        base_url,
        "--model",
        model,
        "--output",
        str(Path(output_dir).resolve()),
        "--export",
        str(Path(export_path).resolve()),
        "--run-name",
        run_id,
        "--temperature",
        str(temperature),
        "--max-tokens",
        str(max_tokens),
        "--timeout",
        str(timeout),
        "--http-retries",
        str(http_retries),
        "--score-by-subproblem",
        "--concurrency",
        str(concurrency),
    ]


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def submit_avacore(
    candidate_dir: str | Path,
    *,
    avacore_python: str | Path,
    runner: str | Path,
    base_url: str,
    model: str,
    temperature: float = 0.6,
    max_tokens: int = 262144,
    timeout: float = 7200.0,
    http_retries: int = 5,
    concurrency: int = 1,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate and launch one detached strict AvaCore process."""
    candidate = load_candidate(candidate_dir)
    root = Path(candidate_dir).expanduser().resolve()
    run_id = _safe_run_id(run_id or f"{candidate.candidate_id}-{candidate.revision}-{uuid.uuid4().hex[:8]}")
    output_dir = root / "runs" / run_id
    export_path = output_dir / "rollouts.jsonl"
    log_path = output_dir / "avacore.log"
    command = build_avacore_command(
        avacore_python=avacore_python,
        runner=runner,
        candidate_dir=root,
        output_dir=output_dir,
        export_path=export_path,
        run_id=run_id,
        base_url=base_url,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        http_retries=http_retries,
        concurrency=concurrency,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    # The detached child runs from the AvaCore environment, which may not have
    # this checkout installed as a package.  Carry the repository source path
    # explicitly so nested candidates work from a clean clone as well.
    repository_root = Path(runner).expanduser().resolve().parents[2]
    source_path = str(repository_root / "src")
    inherited_pythonpath = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = (
        source_path
        if not inherited_pythonpath
        else os.pathsep.join((source_path, inherited_pythonpath))
    )
    # The caller supplies POSTGRES/OPENAI_API_KEY through the inherited
    # environment; neither value is copied into the handoff manifest.
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=str(Path(runner).resolve().parents[2]),
            env=child_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    handoff = {
        "schema": "scicode-avacore-handoff-v1",
        "status": "submitted",
        "run_id": run_id,
        "pid": process.pid,
        "candidate": candidate.as_dict(),
        "base_url": safe_endpoint(base_url),
        "model": model,
        "sampling": {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "http_retries": http_retries,
            "concurrency": concurrency,
        },
        "output_dir": str(output_dir),
        "export_path": str(export_path),
        "log_path": str(log_path),
        "command": [part for part in command if part not in {"--api-key", "--postgres"}],
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(output_dir / "handoff.json", handoff)
    return handoff


def handoff_status(path: str | Path) -> dict[str, Any]:
    handoff_path = Path(path)
    value = json.loads(handoff_path.read_text(encoding="utf-8"))
    manifest_path = Path(value["output_dir"]) / "manifest.json"
    if manifest_path.is_file():
        value["status"] = "finished"
        value["run_manifest"] = str(manifest_path)
        value["run"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        return value
    try:
        os.kill(int(value["pid"]), 0)
    except (OSError, ValueError):
        value["status"] = "failed_without_manifest"
    else:
        value["status"] = "running"
    return value
