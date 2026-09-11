import subprocess
from pathlib import Path

import pytest

from scicode.pipeline.delivery import DeliveryError, merge_candidate
from scicode.pipeline.worktree import WorktreeManager


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def repo_with_commit(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.org")
    git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


def test_merge_candidate_under_lock(tmp_path):
    repo = repo_with_commit(tmp_path)
    manager = WorktreeManager(repo, tmp_path / "workspace")
    allocation = manager.create("candidate_merge", base_ref="main")
    worktree = Path(allocation.worktree_path)
    (worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(worktree, "add", "candidate.txt")
    git(worktree, "config", "user.email", "test@example.org")
    git(worktree, "config", "user.name", "test")
    git(worktree, "commit", "-m", "candidate")
    result = merge_candidate(
        repo,
        allocation.branch,
        integration_branch="main",
        lock_root=tmp_path / "workspace",
    )
    assert result.status == "merged"
    assert (repo / "candidate.txt").is_file()
    manager.cleanup_copy(allocation)


def test_merge_refuses_dirty_shared_checkout(tmp_path):
    repo = repo_with_commit(tmp_path)
    manager = WorktreeManager(repo, tmp_path / "workspace")
    allocation = manager.create("candidate_dirty", base_ref="main")
    worktree = Path(allocation.worktree_path)
    (worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(worktree, "add", "candidate.txt")
    git(worktree, "config", "user.email", "test@example.org")
    git(worktree, "config", "user.name", "test")
    git(worktree, "commit", "-m", "candidate")
    (repo / "uncommitted.txt").write_text("do not touch\n", encoding="utf-8")
    with pytest.raises(DeliveryError, match="dirty"):
        merge_candidate(
            repo,
            allocation.branch,
            integration_branch="main",
            lock_root=tmp_path / "workspace",
        )
    manager.cleanup_copy(allocation)
