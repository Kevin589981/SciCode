"""Concurrency-safe Git worktree lifecycle helpers.

The authoring controller allocates one fixed worktree per candidate.  Locks
are deliberately filesystem based so independent Kimi Code sessions do not
need a shared service merely to reserve a directory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorktreeError(RuntimeError):
    """Raised when a candidate worktree operation cannot be completed safely."""


_CANDIDATE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def validate_candidate_id(candidate_id: str) -> str:
    value = str(candidate_id)
    if not _CANDIDATE_ID.fullmatch(value):
        raise WorktreeError(
            "candidate id must match ^[a-z0-9][a-z0-9._-]{0,63}$"
        )
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class WorktreeAllocation:
    candidate_id: str
    worktree_path: str
    branch: str
    base_ref: str
    base_commit: str
    lock_path: str
    lock_token: str
    pid: int
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CandidateLock:
    def __init__(self, path: Path, *, candidate_id: str) -> None:
        self.path = path
        self.candidate_id = candidate_id
        self.token = uuid.uuid4().hex
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "candidate_id": self.candidate_id,
            "token": self.token,
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise WorktreeError(f"candidate lock is already held: {self.path}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise WorktreeError(f"lock ownership cannot be verified: {self.path}") from exc
        if payload.get("token") != self.token:
            raise WorktreeError(f"lock ownership changed: {self.path}")
        self.path.unlink()
        self._held = False


class WorktreeManager:
    """Allocate candidate worktrees below one configured workspace root."""

    def __init__(self, repository: str | Path, workspace_root: str | Path) -> None:
        self.repository = Path(repository).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        if not self.repository.is_dir() or not (self.repository / ".git").exists():
            raise WorktreeError(f"repository is not a Git checkout: {self.repository}")

    def _paths(self, candidate_id: str) -> tuple[Path, Path]:
        candidate_id = validate_candidate_id(candidate_id)
        lock_path = self.workspace_root / ".locks" / f"{candidate_id}.lock"
        worktree_path = self.workspace_root / "candidates" / candidate_id
        if not _inside(lock_path, self.workspace_root) or not _inside(
            worktree_path, self.workspace_root
        ):
            raise WorktreeError("resolved worktree path escapes workspace root")
        return worktree_path, lock_path

    def create(
        self,
        candidate_id: str,
        *,
        base_ref: str = "HEAD",
        branch: str | None = None,
    ) -> WorktreeAllocation:
        candidate_id = validate_candidate_id(candidate_id)
        worktree_path, lock_path = self._paths(candidate_id)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        lock = CandidateLock(lock_path, candidate_id=candidate_id)
        lock.acquire()
        try:
            if worktree_path.exists():
                raise WorktreeError(f"candidate worktree already exists: {worktree_path}")
            resolved_ref = _git(self.repository, "rev-parse", "--verify", base_ref)
            branch_name = branch or f"author/{candidate_id}"
            if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch_name):
                raise WorktreeError(f"invalid branch name: {branch_name!r}")
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            _git(
                self.repository,
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path),
                base_ref,
            )
            allocation = WorktreeAllocation(
                candidate_id=candidate_id,
                worktree_path=str(worktree_path),
                branch=branch_name,
                base_ref=base_ref,
                base_commit=resolved_ref,
                lock_path=str(lock_path),
                lock_token=lock.token,
                pid=os.getpid(),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            (worktree_path / ".scicode-worktree.json").write_text(
                json.dumps(allocation.as_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return allocation
        except Exception:
            # Remove a partially registered worktree, but never remove an
            # existing branch or another candidate's path.
            if worktree_path.exists():
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.repository),
                        "worktree",
                        "remove",
                        "--force",
                        str(worktree_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            lock.release()
            raise

    def release(self, allocation: WorktreeAllocation) -> None:
        CandidateLock(
            Path(allocation.lock_path), candidate_id=allocation.candidate_id
        )._held = False
        path = Path(allocation.lock_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise WorktreeError(f"lock cannot be read: {path}") from exc
        if payload.get("token") != allocation.lock_token:
            raise WorktreeError(f"lock ownership changed: {path}")
        path.unlink()

    def cleanup_copy(self, allocation: WorktreeAllocation) -> None:
        worktree_path = Path(allocation.worktree_path).resolve()
        if not _inside(worktree_path, self.workspace_root):
            raise WorktreeError("worktree path escapes workspace root")
        if worktree_path.exists():
            _git(self.repository, "worktree", "remove", "--force", str(worktree_path))
        self.release(allocation)

    def lock_status(self, candidate_id: str) -> dict[str, Any] | None:
        _, lock_path = self._paths(candidate_id)
        if not lock_path.is_file():
            return None
        try:
            value = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            value = {"malformed": True}
        value["path"] = str(lock_path)
        return value
