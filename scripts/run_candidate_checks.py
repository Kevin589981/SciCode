#!/usr/bin/env python3
"""Run all deterministic candidate checks before a model request."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.candidate import CandidateValidationError  # noqa: E402
from scicode.pipeline.checks import run_candidate_checks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--validation-dir", type=Path)
    args = parser.parse_args()
    try:
        result = run_candidate_checks(args.candidate_dir, validation_dir=args.validation_dir)
    except (CandidateValidationError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
