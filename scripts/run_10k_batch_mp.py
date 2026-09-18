#!/usr/bin/env python3
"""Run independent one-candidate controllers in multiple OS processes.

Each worker owns its scheduler state and workspace.  Workers publish only
completed SFT rows into the shared delivery directory under a file lock.  A
crashed worker therefore cannot corrupt another worker's state or stop the
whole batch.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.mirror import prepare_batch_mirror  # noqa: E402


def _count_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield handle
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        handle.close()


def merge_local_delivery(local_root: Path, shared_root: Path, target: int) -> int:
    """Merge a worker's dataset and overflow rows without losing concurrent rows."""
    files = [local_root / "dataset.jsonl", local_root / "dataset.overflow.jsonl"]
    rows: list[dict[str, Any]] = []
    for path in files:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        return _count_rows(shared_root / "dataset.jsonl")
    output = shared_root / "dataset.jsonl"
    registry = shared_root / "registry.jsonl"
    lock = shared_root / ".delivery.lock"
    with _file_lock(lock):
        existing = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()] if output.is_file() else []
        keys = set()
        for row in existing:
            metadata = row.get("metadata") if isinstance(row, dict) else None
            keys.add(str(metadata.get("sample_key") if isinstance(metadata, dict) else row.get("id")))
        for row in rows:
            metadata = row.get("metadata") if isinstance(row, dict) else None
            key = str(metadata.get("sample_key") if isinstance(metadata, dict) else row.get("id"))
            if key not in keys and len(existing) < target:
                existing.append(row)
                keys.add(key)
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in existing), encoding="utf-8")
        os.replace(tmp, output)
        registry_rows = []
        for row in existing:
            metadata = row.get("metadata") if isinstance(row, dict) else {}
            if isinstance(metadata, dict):
                registry_rows.append({"sample_key": metadata.get("sample_key", row.get("id")), "sample_id": row.get("id"), "metadata": metadata})
        registry.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in registry_rows), encoding="utf-8")
        return len(existing)


def _write_parent_state(
    root: Path,
    config: dict[str, Any],
    batch_id: str,
    workers: list[mp.Process],
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Publish a monitorable parent snapshot without exposing model secrets."""
    snapshot = {
        "schema": "scicode-multiprocess-batch-v1",
        "batch_id": batch_id,
        "status": status,
        "target_samples": int(config["target_samples"]),
        "accepted_samples": _count_rows(Path(config["delivery_root"]) / "dataset.jsonl"),
        "workers": [
            {
                "worker_id": index,
                "pid": worker.pid,
                "alive": worker.is_alive(),
                "exitcode": worker.exitcode,
            }
            for index, worker in enumerate(workers)
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        snapshot["error"] = error
    root.mkdir(parents=True, exist_ok=True)
    state = root / "state.json"
    temporary = state.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, state)


def worker_loop(worker_id: int, config: dict[str, Any], stop: Any) -> None:
    root = Path(config["batch_root"]) / f"worker-{worker_id:04d}"
    root.mkdir(parents=True, exist_ok=True)
    loop = 0
    controller = Path(config["controller_script"])
    python = config["python"]
    while not stop.is_set():
        if _count_rows(Path(config["delivery_root"]) / "dataset.jsonl") >= config["target_samples"]:
            return
        loop += 1
        local = root / f"loop-{loop:06d}"
        local.mkdir(parents=True, exist_ok=True)
        mirror = config["mirrors"][worker_id % len(config["mirrors"])]
        batch_config = {
            "repository_root": mirror,
            "workspace_root": str(local / "workspace"),
            "batch_root": str(local / "batches"),
            "delivery_root": str(local / "delivery"),
            "integration_branch": config["integration_branch"],
            "candidate_prefix": f"mp{worker_id:04d}{loop:06d}",
            "concurrency": 1,
            "target_samples": 1,
            "max_candidates": 1,
            "poll_seconds": config["poll_seconds"],
            "max_rounds": config["max_rounds"],
            "run_timeout_seconds": config["run_timeout_seconds"],
            "kimi_bin": config["kimi_bin"],
            "kimi_home": config["kimi_home"],
            "authoring_cli": config.get("authoring_cli", "kimi"),
            "codex_bin": config.get("codex_bin", "codex"),
            "authoring_model": config.get("authoring_model", "Kimi-K3"),
            "codex_provider": config.get("codex_provider", "company-kimi"),
            "env_file": config.get("env_file"),
            "merge_accepted": False,
            "cleanup_worktrees": False,
            "drain": True,
            "mirror_root": None,
            "avacore_max_slots": config["avacore_max_slots"],
            "trace_first": bool(config.get("trace_first", False)),
        }
        config_path = local / "config.json"
        config_path.write_text(json.dumps(batch_config, indent=2) + "\n", encoding="utf-8")
        log = (local / "controller.log").open("ab")
        env = os.environ.copy()
        env.pop("HTTP_PROXY", None); env.pop("HTTPS_PROXY", None)
        env.pop("http_proxy", None); env.pop("https_proxy", None)
        env["SCICODE_REPOSITORY_ROOT"] = mirror
        try:
            subprocess.run([python, str(controller), "--config", str(config_path)], cwd=mirror, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        except OSError as exc:
            (local / "worker-error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        finally:
            log.close()
        merge_local_delivery(local / "delivery", Path(config["delivery_root"]), config["target_samples"])
        time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    workers_count = int(config["workers"])
    target_samples = int(config["target_samples"])
    mirror_shards = int(config.get("mirror_shards", min(workers_count, 32)))
    if workers_count < 1:
        raise SystemExit("workers must be positive")
    if target_samples < 1:
        raise SystemExit("target_samples must be positive")
    if mirror_shards < 1:
        raise SystemExit("mirror_shards must be positive")
    batch_id = config.get("batch_id", f"mp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}")
    root = Path(config["batch_root"]).resolve()
    mirror_root = Path(config["mirror_root"]).resolve()
    source = Path(config["repository_root"]).resolve()
    shard_count = mirror_shards
    mirrors = []
    root.mkdir(parents=True, exist_ok=True)
    mirror_root.mkdir(parents=True, exist_ok=True)
    (Path(config["delivery_root"]).resolve()).mkdir(parents=True, exist_ok=True)
    for index in range(shard_count):
        path = mirror_root / f"{batch_id}-shard-{index:03d}"
        if not path.exists():
            mirrors.append(prepare_batch_mirror(source, mirror_root, f"{batch_id}-shard-{index:03d}", config["integration_branch"]).path)
        else:
            mirrors.append(str(path))
    config.update({"batch_id": batch_id, "batch_root": str(root), "mirrors": mirrors, "controller_script": str(REPOSITORY_ROOT / "scripts" / "run_10k_batch.py"), "python": sys.executable})
    (root / "resolved-config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    # Spawn gives every worker a clean interpreter and prevents inherited locks,
    # sockets, and partially initialized provider clients from crossing workers.
    context = mp.get_context("spawn")
    stop = context.Event()
    workers = [context.Process(target=worker_loop, args=(i, config, stop), name=f"scicode-worker-{i:04d}") for i in range(workers_count)]
    for worker in workers:
        worker.start()
    _write_parent_state(root, config, batch_id, workers, "running")
    restart_limit = int(config.get("worker_restart_limit", 2))
    restart_counts = [0] * workers_count
    try:
        while not stop.is_set():
            count = _count_rows(Path(config["delivery_root"]) / "dataset.jsonl")
            if count >= target_samples:
                break
            for index, worker in enumerate(workers):
                if worker.is_alive():
                    continue
                if restart_counts[index] >= restart_limit:
                    continue
                restart_counts[index] += 1
                replacement = context.Process(target=worker_loop, args=(index, config, stop), name=f"scicode-worker-{index:04d}-r{restart_counts[index]}")
                replacement.start()
                workers[index] = replacement
            _write_parent_state(root, config, batch_id, workers, "running")
            time.sleep(float(config.get("monitor_seconds", 10)))
    except KeyboardInterrupt:
        _write_parent_state(root, config, batch_id, workers, "stopping")
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=10)
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        final_count = _count_rows(Path(config["delivery_root"]) / "dataset.jsonl")
        _write_parent_state(root, config, batch_id, workers, "finished" if final_count >= target_samples else "stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
