import json
import subprocess
from pathlib import Path

import pytest

from scicode.pipeline.worktree import WorktreeError, WorktreeManager


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.org")
    git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    return repo


def test_create_and_cleanup_preserves_branch(tmp_path):
    repo = make_repo(tmp_path)
    manager = WorktreeManager(repo, tmp_path / "workspace")
    allocation = manager.create("candidate_001", base_ref="main")
    worktree = Path(allocation.worktree_path)
    assert worktree.is_dir()
    assert git(worktree, "branch", "--show-current") == "author/candidate_001"
    marker = json.loads((worktree / ".scicode-worktree.json").read_text())
    assert marker["candidate_id"] == "candidate_001"
    manager.cleanup_copy(allocation)
    assert not worktree.exists()
    assert "author/candidate_001" in git(repo, "branch", "--list", "author/candidate_001")
    assert manager.lock_status("candidate_001") is None


def test_lock_prevents_duplicate_candidate(tmp_path):
    repo = make_repo(tmp_path)
    manager = WorktreeManager(repo, tmp_path / "workspace")
    allocation = manager.create("candidate_002")
    with pytest.raises(WorktreeError, match="already held"):
        manager.create("candidate_002")
    manager.cleanup_copy(allocation)


def test_invalid_candidate_id_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manager = WorktreeManager(repo, tmp_path / "workspace")
    with pytest.raises(WorktreeError, match="candidate id"):
        manager.create("../escape")
