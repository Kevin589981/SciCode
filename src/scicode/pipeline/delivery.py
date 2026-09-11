"""Lock-protected candidate merge helpers used by the delivery skill."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .worktree import CandidateLock, WorktreeError


class DeliveryError(RuntimeError):
    """Raised when a candidate cannot be merged without risking other work."""


@dataclass(frozen=True)
class MergeResult:
    status: str
    repository: str
    integration_branch: str
    candidate_branch: str
    commit: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "repository": self.repository,
            "integration_branch": self.integration_branch,
            "candidate_branch": self.candidate_branch,
            "commit": self.commit,
            "error": self.error,
        }


def _run(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def merge_candidate(
    repository: str | Path,
    candidate_branch: str,
    *,
    integration_branch: str,
    lock_root: str | Path,
) -> MergeResult:
    """Merge one committed candidate while holding the global delivery lock."""
    repo = Path(repository).expanduser().resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise DeliveryError(f"not a Git repository: {repo}")
    if not candidate_branch or candidate_branch == integration_branch:
        raise DeliveryError("candidate branch must differ from integration branch")
    lock = CandidateLock(Path(lock_root).resolve() / ".locks" / "delivery.lock", candidate_id="delivery")
    try:
        lock.acquire()
    except WorktreeError as exc:
        raise DeliveryError(str(exc)) from exc
    try:
        current = _run(repo, "branch", "--show-current").stdout.strip()
        if current != integration_branch:
            raise DeliveryError(
                f"shared checkout is on {current!r}; expected {integration_branch!r}"
            )
        dirty = _run(repo, "status", "--porcelain").stdout.strip()
        if dirty:
            raise DeliveryError("shared checkout is dirty; refusing an unsafe merge")
        _run(repo, "rev-parse", "--verify", candidate_branch)
        merged = _run(repo, "merge", "--no-ff", "--no-edit", candidate_branch, check=False)
        if merged.returncode != 0:
            _run(repo, "merge", "--abort", check=False)
            return MergeResult(
                status="conflict",
                repository=str(repo),
                integration_branch=integration_branch,
                candidate_branch=candidate_branch,
                error=(merged.stderr or merged.stdout).strip()[:4000],
            )
        commit = _run(repo, "rev-parse", "HEAD").stdout.strip()
        return MergeResult(
            status="merged",
            repository=str(repo),
            integration_branch=integration_branch,
            candidate_branch=candidate_branch,
            commit=commit,
        )
    finally:
        try:
            lock.release()
        except WorktreeError:
            # Preserve the merge result/error; an operator can inspect the
            # lock file rather than allowing a second process to race.
            pass


def write_merge_result(path: str | Path, result: MergeResult) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
