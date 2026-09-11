#!/usr/bin/env python3
"""Convert AvaCore rollout JSONL into the final subproblem-level SFT JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.samples import (  # noqa: E402
    SampleExportError,
    export_subproblem_samples,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--rollouts", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--target-count", type=int, default=10_000)
    args = parser.parse_args()
    try:
        result = export_subproblem_samples(
            args.candidate_dir,
            args.rollouts,
            args.registry,
            args.output,
            target_count=args.target_count,
            summary=args.summary,
        )
    except (SampleExportError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
