#!/usr/bin/env python3
"""Run SciCode's original sequential generator against an OpenAI-compatible API.

This wrapper uses :class:`eval.scripts.gencode.Gencode` for prompt construction,
previous-code propagation, code extraction, and output layout. It does not
start Kimi Code CLI. Use ``--model openai/Kimi-K3`` with the endpoint settings
below to run the teacher's Kimi deployment through SciCode's native adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Make the repository root importable when this file is executed directly.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.scripts.gencode import BACKGOUND_PROMPT_TEMPLATE, DEFAULT_PROMPT_TEMPLATE, Gencode
from scicode.parse.parse import read_from_jsonl
from scicode.trace import TraceRecorder


DEFAULT_BASE_URL = "http://117.135.59.14:5050/v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--problem",
        type=Path,
        default=REPO_ROOT / "tests/test_data/first_problem.jsonl",
        help="One-line SciCode JSONL record",
    )
    parser.add_argument("--model", default="openai/Kimi-K3")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY", os.environ.get("OPENAI_KEY", "dummy")),
    )
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "runs/kimi-strict-api")
    parser.add_argument("--prompt-dir", type=Path, default=REPO_ROOT / "runs/kimi-strict-api-prompts")
    parser.add_argument("--run-id", default="kimi-strict-api-001")
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--with-background", action="store_true")
    return parser


def _load_problem(path: Path) -> dict[str, Any]:
    records = read_from_jsonl(path)
    if len(records) != 1:
        raise ValueError(f"expected one problem record, found {len(records)}")
    return records[0]


def main() -> int:
    args = _parser().parse_args()
    problem = _load_problem(args.problem)
    total_steps = len(problem["sub_steps"])
    steps_to_run = total_steps if args.max_steps <= 0 else min(args.max_steps, total_steps)
    is_openai_compatible = args.model.startswith("openai/") or "gpt" in args.model

    # The upstream adapter reads these names from keys.cfg when present and
    # from the environment otherwise. No Kimi Code CLI process is involved.
    os.environ["OPENAI_KEY"] = args.api_key
    os.environ["OPENAI_BASE_URL"] = args.base_url

    recorder = TraceRecorder(
        args.output_dir,
        run_id=args.run_id,
        candidate_id=f"strict-{problem['problem_id']}",
        task_revision="r1",
        mode="strict",
        provider="openai-compatible" if is_openai_compatible else args.model,
        model=args.model.removeprefix("openai/"),
        sampling={"temperature": args.temperature, "stream": False},
        prompt_version="scicode-upstream-v1",
    )
    prompt_template = BACKGOUND_PROMPT_TEMPLATE if args.with_background else DEFAULT_PROMPT_TEMPLATE
    generator = Gencode(
        model=args.model,
        output_dir=args.output_dir,
        prompt_dir=args.prompt_dir,
        with_background=args.with_background,
        temperature=args.temperature,
        trace_recorder=recorder,
    )

    try:
        for index in range(steps_to_run):
            generator.generate_response_with_steps(
                problem,
                index + 1,
                total_steps,
                args.model,
                prompt_template,
            )
        recorder.record("run_end", {"status": "completed", "steps": steps_to_run})
        manifest = recorder.finalize(
            verification={"passed": True, "steps_completed": steps_to_run}
        )
        visible_count = recorder.export_visible(args.output_dir / "trace" / "public_events.jsonl")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "strict",
                    "api": "direct_openai_compatible" if is_openai_compatible else args.model,
                    "model": args.model.removeprefix("openai/"),
                    "problem_id": problem["problem_id"],
                    "steps_completed": steps_to_run,
                    "visible_event_count": visible_count,
                    "event_count": manifest["event_count"],
                    "events": str(recorder.events_path),
                    "manifest": str(recorder.manifest_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        recorder.record(
            "error",
            {"error_type": type(exc).__name__, "message": str(exc)},
            visibility="private",
        )
        recorder.finalize(verification={"passed": False, "error": type(exc).__name__})
        print(f"strict generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
