import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from scicode.pipeline.mirror import MirrorError, prepare_batch_mirror  # noqa: E402
from run_10k_batch import BatchConfig, BatchRunner  # noqa: E402


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _source_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    _git(tmp_path, "init", "-b", "main", str(source))
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / "README.md").write_text("clean baseline\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "baseline")
    return source


def test_prepare_batch_mirror_is_independent_and_pinned(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    result = prepare_batch_mirror(source, tmp_path / "mirrors", "batch-1", "main")
    mirror = tmp_path / "mirrors" / "batch-1"

    assert result.path == str(mirror.resolve())
    assert result.source_commit == _git(source, "rev-parse", "HEAD").stdout.strip()
    assert _git(mirror, "rev-parse", "HEAD").stdout.strip() == result.source_commit
    assert _git(mirror, "remote").stdout.strip() == ""
    assert (mirror / "README.md").read_text(encoding="utf-8") == "clean baseline\n"


def test_prepare_batch_mirror_rejects_dirty_source(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    (source / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(MirrorError, match="source checkout is dirty"):
        prepare_batch_mirror(source, tmp_path / "mirrors", "batch-1", "main")


def test_prepare_batch_mirror_refuses_existing_destination(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    (tmp_path / "mirrors" / "batch-1").mkdir(parents=True)

    with pytest.raises(MirrorError, match="already exists"):
        prepare_batch_mirror(source, tmp_path / "mirrors", "batch-1", "main")


def test_dry_run_does_not_create_batch_mirror(tmp_path: Path, capsys) -> None:
    source = _source_repo(tmp_path)
    mirror_root = tmp_path / "mirrors"
    config = BatchConfig(
        repository_root=str(source),
        mirror_root=str(mirror_root),
        workspace_root=str(tmp_path / "workspace"),
        delivery_root=str(tmp_path / "delivery"),
        batch_root=str(tmp_path / "batches"),
        target_samples=1,
    )

    runner = BatchRunner(config, "batch-1", prepare_mirror=False)
    assert runner.dry_run() == 0
    capsys.readouterr()
    assert not mirror_root.exists()
