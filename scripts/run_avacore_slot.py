#!/usr/bin/env python3
"""Run one AvaCore process while respecting a cross-process slot limit."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_slot(root: Path, limit: int, wait_seconds: float = 5.0) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    while True:
        for index in range(limit):
            path = root / f"slot-{index:04d}.lock"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    owner = int(path.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    owner = 0
                if owner and not _pid_alive(owner):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(f"{os.getpid()}\n")
            return path
        time.sleep(wait_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots-root", type=Path, required=True)
    parser.add_argument("--max-slots", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if args.max_slots < 1 or not command:
        parser.error("--max-slots must be positive and a command is required")
    lock = acquire_slot(args.slots_root, args.max_slots)
    try:
        return subprocess.run(command, check=False).returncode
    finally:
        try:
            if lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock.unlink()
        except (FileNotFoundError, OSError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
