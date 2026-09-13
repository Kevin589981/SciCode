"""Create an isolated Git repository for one authoring batch."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MirrorError(RuntimeError):
    """Raised when a batch mirror cannot be created safely."""


@dataclass(frozen=True)
class BatchMirror:
    """The immutable source and destination identity recorded for a batch."""

    source_repository: str
    source_commit: str
    path: str
    base_ref: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source_repository": self.source_repository,
            "source_commit": self.source_commit,
            "path": self.path,
            "base_ref": self.base_ref,
        }


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise MirrorError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def prepare_batch_mirror(
    source_repository: str | Path,
    mirror_root: str | Path,
    batch_id: str,
    base_ref: str,
) -> BatchMirror:
    """Clone a clean source commit into a repository dedicated to one batch.

    ``git worktree`` stores administration data in the repository's common
    ``.git/worktrees`` directory.  A normal clone with ``--no-hardlinks`` gives
    each batch its own administration directory and object store, so another
    batch cannot contend for that registry or mutate its refs.
    """

    source = Path(source_repository).expanduser().resolve()
    root = Path(mirror_root).expanduser().resolve()
    if not source.is_dir() or not (source / ".git").exists():
        raise MirrorError(f"source is not a Git checkout: {source}")
    if not batch_id or "/" in batch_id or "\\" in batch_id:
        raise MirrorError(f"invalid batch id for mirror: {batch_id!r}")
    if not base_ref:
        raise MirrorError("base_ref must be non-empty")
    if source == root or source in root.parents:
        raise MirrorError("mirror root must not be inside the source checkout")
    if _git(source, "status", "--porcelain"):
        raise MirrorError(f"source checkout is dirty: {source}")

    source_commit = _git(source, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    destination = root / batch_id
    if destination.exists():
        raise MirrorError(f"batch mirror already exists: {destination}")
    root.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "git",
                "clone",
                "--no-hardlinks",
                "--no-tags",
                "--single-branch",
                "--branch",
                base_ref,
                str(source),
                str(destination),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            raise MirrorError(f"git clone failed: {detail}")
        # A batch mirror is intentionally not a remote checkout.  Removing the
        # local origin prevents accidental pushes or future source coupling.
        subprocess.run(
            ["git", "-C", str(destination), "remote", "remove", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        mirror_commit = _git(destination, "rev-parse", "--verify", "HEAD^{commit}")
        if mirror_commit != source_commit:
            raise MirrorError(
                f"mirror commit mismatch: source={source_commit}, mirror={mirror_commit}"
            )
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        raise
    return BatchMirror(
        source_repository=str(source),
        source_commit=source_commit,
        path=str(destination),
        base_ref=base_ref,
    )
