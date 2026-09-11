#!/usr/bin/env python3
"""Validate one SciCode candidate before any provider request."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow direct execution from a source checkout that has not been installed.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.candidate import (  # noqa: E402
    CandidateValidationError,
    load_candidate,
    write_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--write-report", type=Path)
    parser.add_argument("--prompt-profile", default="background")
    args = parser.parse_args()
    try:
        manifest = load_candidate(args.candidate_dir, prompt_profile=args.prompt_profile)
    except (CandidateValidationError, OSError) as exc:
        report = {"status": "rejected", "error": str(exc)}
        if args.write_report:
            args.write_report.parent.mkdir(parents=True, exist_ok=True)
            args.write_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(report, ensure_ascii=False))
        return 2
    report = {"status": "accepted_for_run", "manifest": manifest.as_dict()}
    destination = args.write_report or Path(args.candidate_dir) / "validation" / "schema_report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Keep the author-supplied candidate.json intact. Store the computed
    # immutable handoff manifest beside it for downstream skills.
    write_manifest(Path(args.candidate_dir) / "candidate_manifest.json", manifest)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
