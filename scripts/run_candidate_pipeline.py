#!/usr/bin/env python3
"""Advance one candidate through validation, trace import, and delivery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.pipeline import PipelineError, run_candidate_pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--rollouts", type=Path)
    parser.add_argument("--run-manifest", type=Path)
    parser.add_argument("--target-count", type=int, default=10_000)
    parser.add_argument("--allow-unreviewed", action="store_true")
    parser.add_argument(
        "--allow-qa-export",
        action="store_true",
        help="Write QA samples before a promotable run/release decision (never use for final delivery)",
    )
    args = parser.parse_args()
    try:
        result = run_candidate_pipeline(
            args.candidate_dir,
            args.delivery_root,
            rollouts=args.rollouts,
            run_manifest=args.run_manifest,
            target_count=args.target_count,
            allow_unreviewed=args.allow_unreviewed,
            allow_qa_export=args.allow_qa_export,
        )
    except (PipelineError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
