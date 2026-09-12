#!/usr/bin/env python3
"""Submit a detached strict AvaCore run for one validated candidate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scicode.pipeline.handoff import HandoffError, submit_avacore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--avacore-python", required=True)
    parser.add_argument("--runner", type=Path, default=REPOSITORY_ROOT / "eval/avacore/scicode_avacore.py")
    parser.add_argument("--base-url", default="http://10.100.184.127:5050")
    parser.add_argument("--model", required=True)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--max-tokens", type=int, default=262144)
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--http-retries", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--postgres",
        default=os.getenv("POSTGRES"),
        help="AvaCore PostgreSQL DSN; kept in the child environment and never written to handoff.json",
    )
    args = parser.parse_args()
    try:
        result = submit_avacore(
            args.candidate_dir,
            avacore_python=args.avacore_python,
            runner=args.runner,
            base_url=args.base_url,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            http_retries=args.http_retries,
            concurrency=args.concurrency,
            run_id=args.run_id,
            postgres=args.postgres,
        )
    except (HandoffError, OSError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
