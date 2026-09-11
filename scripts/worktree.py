#!/usr/bin/env python3
"""CLI for the concurrency-safe SciCode candidate worktree lifecycle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scicode.pipeline.worktree import WorktreeAllocation, WorktreeManager


def _manager(args: argparse.Namespace) -> WorktreeManager:
    return WorktreeManager(args.repository, args.workspace_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "status", "cleanup-copy"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--branch")
    parser.add_argument("--allocation", type=Path)
    args = parser.parse_args()
    manager = _manager(args)
    if args.action == "create":
        allocation = manager.create(
            args.candidate_id, base_ref=args.base_ref, branch=args.branch
        )
        print(json.dumps(allocation.as_dict(), ensure_ascii=False))
        return 0
    if args.action == "status":
        print(json.dumps(manager.lock_status(args.candidate_id), ensure_ascii=False))
        return 0
    if not args.allocation:
        parser.error("cleanup-copy requires --allocation")
    allocation = WorktreeAllocation(
        **json.loads(args.allocation.read_text(encoding="utf-8"))
    )
    manager.cleanup_copy(allocation)
    print(json.dumps({"status": "cleaned", "candidate_id": args.candidate_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
