"""One-command reasoning-task, rollout, grading, and SFT pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .author import run_authoring
from .export import export_sft
from .grade import run_grading
from .preflight import run_preflight
from .rollout import current_commit, run_rollouts
from .schema import ARCHETYPES, canonical_hash
from .verify import run_verification

RUN_SCHEMA = "scicode-reasoning-run-v1"


def _file_info(path: Path) -> dict:
    data = path.read_bytes() if path.exists() else b""
    rows = 0
    if data:
        with path.open(encoding="utf-8") as stream:
            rows = sum(1 for line in stream if line.strip())
    return {
        "path": str(path),
        "bytes": len(data),
        "rows": rows,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def run_pipeline(
    mined_path: Path,
    repo_meta_path: Path,
    output_dir: Path,
    *,
    chat_fn: Callable = llm.chat,
    author_model: str | None = None,
    critic_model: str | None = None,
    verifier_model: str | None = None,
    solver_model: str | None = None,
    judge_model: str | None = None,
    additional_judge_models: tuple[str, ...] = (),
    limit: int = 3,
    attempts: int = 1,
    author_temperature: float = 0.3,
    solver_temperature: float = 0.7,
    max_tokens: int = 16384,
    author_max_tokens: int | None = None,
    solver_max_tokens: int | None = None,
    context_window_tokens: int = 262_144,
    critic_max_tokens: int = 4096,
    verifier_max_tokens: int = 4096,
    judge_max_tokens: int = 8192,
    judge_max_input_chars: int = 160_000,
    timeout: int = 2400,
    concurrency: int = 3,
    author_attempts: int = 2,
    factory_commit: str | None = None,
    inline_thinking: bool = False,
) -> dict:
    """Run the v1 vertical slice and write an auditable manifest."""
    author_max_tokens = author_max_tokens or max_tokens
    solver_max_tokens = solver_max_tokens or max_tokens
    output_budgets = {
        "author_max_tokens": author_max_tokens,
        "solver_max_tokens": solver_max_tokens,
        "critic_max_tokens": critic_max_tokens,
        "verifier_max_tokens": verifier_max_tokens,
        "judge_max_tokens": judge_max_tokens,
    }
    if context_window_tokens < 1 or any(
        value < 1 or value >= context_window_tokens for value in output_budgets.values()
    ):
        raise ValueError(
            "every output token budget must be positive and smaller than "
            "context_window_tokens"
        )
    estimated_judge_input_tokens = (judge_max_input_chars + 3) // 4
    if estimated_judge_input_tokens + judge_max_tokens > context_window_tokens:
        raise ValueError(
            "judge input/output budgets exceed the configured context window"
        )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "tasks": output_dir / "tasks.jsonl",
        "preflight": output_dir / "preflight.jsonl",
        "verification": output_dir / "verification.jsonl",
        "traces": output_dir / "traces.jsonl",
        "grades": output_dir / "grades.jsonl",
        "sft": output_dir / "sft.jsonl",
        "sft_report": output_dir / "sft.report.json",
        "manifest": output_dir / "run_manifest.json",
    }
    factory_commit = factory_commit or current_commit()
    fallback_model = None
    if not all((author_model, critic_model, verifier_model, solver_model, judge_model)):
        fallback_model = llm.client_config()["model"]
    author_model = author_model or fallback_model
    critic_model = critic_model or fallback_model
    verifier_model = verifier_model or fallback_model
    solver_model = solver_model or fallback_model
    judge_model = judge_model or fallback_model
    started = dt.datetime.now(dt.timezone.utc).isoformat()

    author_result = run_authoring(
        Path(mined_path),
        Path(repo_meta_path),
        paths["tasks"],
        archetypes=ARCHETYPES,
        limit=limit,
        chat_fn=chat_fn,
        model=author_model,
        temperature=author_temperature,
        max_tokens=author_max_tokens,
        timeout=timeout,
        concurrency=concurrency,
        max_attempts=author_attempts,
    )
    preflight_result = run_preflight(
        paths["tasks"],
        paths["preflight"],
        chat_fn=chat_fn,
        model=critic_model,
        max_tokens=critic_max_tokens,
        timeout=timeout,
        concurrency=concurrency,
    )
    verification_result = run_verification(
        paths["tasks"],
        paths["verification"],
        chat_fn=chat_fn,
        model=verifier_model,
        max_tokens=verifier_max_tokens,
        timeout=timeout,
        concurrency=concurrency,
    )
    rollout_result = run_rollouts(
        paths["tasks"],
        paths["traces"],
        preflight_path=paths["preflight"],
        verification_path=paths["verification"],
        preflight_model=critic_model,
        verifier_model=verifier_model,
        chat_fn=chat_fn,
        model=solver_model,
        attempts=attempts,
        temperature=solver_temperature,
        max_tokens=solver_max_tokens,
        timeout=timeout,
        concurrency=concurrency,
        factory_commit=factory_commit,
        run_variant=canonical_hash(
            {
                "solver_model": solver_model,
                "temperature": solver_temperature,
                "max_tokens": solver_max_tokens,
            }
        )[:16],
    )
    judge_models = list(dict.fromkeys((judge_model, *additional_judge_models)))
    grade_result = {}
    for active_judge in judge_models:
        grade_result[active_judge] = run_grading(
            paths["tasks"],
            paths["traces"],
            paths["grades"],
            chat_fn=chat_fn,
            model=active_judge,
            max_tokens=judge_max_tokens,
            max_input_chars=judge_max_input_chars,
            timeout=timeout,
            concurrency=concurrency,
        )
    export_result = export_sft(
        paths["tasks"],
        paths["traces"],
        paths["grades"],
        output_path=paths["sft"],
        report_path=paths["sft_report"],
        judge_model=judge_model,
        preflight_path=paths["preflight"],
        verification_path=paths["verification"],
        preflight_model=critic_model,
        verifier_model=verifier_model,
        inline_thinking=inline_thinking,
    )
    artifact_names = (
        "tasks",
        "preflight",
        "verification",
        "traces",
        "grades",
        "sft",
        "sft_report",
    )
    manifest = {
        "schema_version": RUN_SCHEMA,
        "factory_commit": factory_commit,
        "started_at": started,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "endpoint": os.environ.get("SCICODE_LLM_BASE_URL"),
        "models": {
            "author": author_model,
            "critic": critic_model,
            "verifier": verifier_model,
            "solver": solver_model,
            "judges": judge_models,
        },
        "parameters": {
            "archetypes": list(ARCHETYPES),
            "limit": limit,
            "attempts": attempts,
            "author_temperature": author_temperature,
            "solver_temperature": solver_temperature,
            "max_tokens": max_tokens,
            "author_max_tokens": author_max_tokens,
            "solver_max_tokens": solver_max_tokens,
            "context_window_tokens": context_window_tokens,
            "critic_max_tokens": critic_max_tokens,
            "verifier_max_tokens": verifier_max_tokens,
            "judge_max_tokens": judge_max_tokens,
            "judge_max_input_chars": judge_max_input_chars,
            "timeout": timeout,
            "concurrency": concurrency,
            "author_attempts": author_attempts,
            "inline_thinking": inline_thinking,
        },
        "inputs": {
            "mined": _file_info(Path(mined_path)),
            "repo_meta": _file_info(Path(repo_meta_path)),
        },
        "stages": {
            "author": author_result,
            "preflight": preflight_result,
            "verification": verification_result,
            "rollout": rollout_result,
            "grade": grade_result,
            "export": export_result,
        },
        "artifacts": {name: _file_info(paths[name]) for name in artifact_names},
    }
    manifest_tmp = paths["manifest"].with_suffix(".json.tmp")
    manifest_tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest_tmp.replace(paths["manifest"])
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mined", type=Path, required=True)
    parser.add_argument("--repo-meta", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--author-model")
    parser.add_argument("--critic-model")
    parser.add_argument("--verifier-model")
    parser.add_argument("--solver-model")
    parser.add_argument("--judge-model")
    parser.add_argument("--additional-judge-model", action="append", default=[])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--author-temperature", type=float, default=0.3)
    parser.add_argument("--solver-temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--author-max-tokens", type=int)
    parser.add_argument("--solver-max-tokens", type=int)
    parser.add_argument("--context-window-tokens", type=int, default=262_144)
    parser.add_argument("--critic-max-tokens", type=int, default=4096)
    parser.add_argument("--verifier-max-tokens", type=int, default=4096)
    parser.add_argument("--judge-max-tokens", type=int, default=8192)
    parser.add_argument("--judge-max-input-chars", type=int, default=160_000)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--author-attempts", type=int, default=2)
    parser.add_argument("--inline-thinking", action="store_true")
    args = parser.parse_args()
    manifest = run_pipeline(
        args.mined,
        args.repo_meta,
        args.out_dir,
        author_model=args.author_model,
        critic_model=args.critic_model,
        verifier_model=args.verifier_model,
        solver_model=args.solver_model,
        judge_model=args.judge_model,
        additional_judge_models=tuple(args.additional_judge_model),
        limit=args.limit,
        attempts=args.attempts,
        author_temperature=args.author_temperature,
        solver_temperature=args.solver_temperature,
        max_tokens=args.max_tokens,
        author_max_tokens=args.author_max_tokens,
        solver_max_tokens=args.solver_max_tokens,
        context_window_tokens=args.context_window_tokens,
        critic_max_tokens=args.critic_max_tokens,
        verifier_max_tokens=args.verifier_max_tokens,
        judge_max_tokens=args.judge_max_tokens,
        judge_max_input_chars=args.judge_max_input_chars,
        timeout=args.timeout,
        concurrency=args.concurrency,
        author_attempts=args.author_attempts,
        inline_thinking=args.inline_thinking,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
