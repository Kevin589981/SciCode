#!/usr/bin/env python3
"""Merge a committed candidate branch under the delivery lock."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.delivery import DeliveryError, merge_candidate, write_merge_result  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--candidate-branch", required=True)
    parser.add_argument("--integration-branch", required=True)
    parser.add_argument("--lock-root", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = merge_candidate(
            args.repository,
            args.candidate_branch,
            integration_branch=args.integration_branch,
            lock_root=args.lock_root,
        )
    except DeliveryError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.report:
        write_merge_result(args.report, result)
    print(json.dumps(result.as_dict(), ensure_ascii=False))
    return 0 if result.status == "merged" else 3


if __name__ == "__main__":
    raise SystemExit(main())
