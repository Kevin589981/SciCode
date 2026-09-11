#!/usr/bin/env python3
"""Audit the public structure of one exported AvaCore rollout JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.candidate import load_candidate  # noqa: E402
from scicode.pipeline.trace_checks import audit_rollouts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--rollouts", type=Path, required=True)
    parser.add_argument("--require-usage", action="store_true")
    parser.add_argument("--require-reasoning", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit_rollouts(
        load_candidate(args.candidate_dir),
        args.rollouts,
        require_usage=args.require_usage,
        require_reasoning=args.require_reasoning,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
