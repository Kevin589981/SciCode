"""Run the official SciCode protocol through AvaCore.

The adapter deliberately keeps SciCode's prompt construction and evaluator as
the source of truth. AvaCore supplies the model client, rollout lifecycle, and
trace representation; it does not replace SciCode's sequential protocol or
its HDF5-based scorer.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from ava_core.core import ChatMessage, ChatTrace, HttpEndpoint, OpenAIClient, Schema, Trace
from ava_core.generate.core import GenerateFunction, Sample
from ava_core.rewards.core import Reward, RewardFunction
from ava_core.rollout import RolloutEngine, RolloutError, RolloutResult
from ava_core.store.postgres import PostgresBackend
from ava_core.utils.io import write_jsonl
from datasets import load_dataset

from scicode.gen.models import extract_python_script


def _endpoint_base_url(base_url: str) -> str:
    """Convert an OpenAI-style base URL to the root expected by AvaCore."""
    normalized = base_url.rstrip("/")
    return normalized[:-3] if normalized.endswith("/v1") else normalized


@lru_cache(maxsize=1)
def _official_adapter():
    path = Path(__file__).parents[1] / "inspect_ai" / "scicode.py"
    spec = importlib.util.spec_from_file_location("scicode_official_adapter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load official SciCode adapter at {path}")
    module = importlib.util.module_from_spec(spec)
    # The upstream module resolves its prompt templates relative to
    # ``eval/inspect_ai``. Preserve that behavior regardless of the caller's cwd.
    old_cwd = Path.cwd()
    os.chdir(path.parent)
    try:
        spec.loader.exec_module(module)
    finally:
        os.chdir(old_cwd)
    return module


@dataclass(frozen=True, slots=True)
class SciCodeReference:
    row: dict[str, Any]
    h5py_file: str
    output_dir: str
    with_background: bool


def _active_steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    problem_id = row["problem_id"]
    skipped = {
        ("13", 5),
        ("62", 0),
        ("76", 2),
    }
    return [
        step
        for index, step in enumerate(row["sub_steps"])
        if (problem_id, index) not in skipped
    ]


class SciCodeGenerate(GenerateFunction[Sample]):
    """The official sequential SciCode solver represented as one AvaCore rollout."""

    instance_type = Sample

    def __init__(
        self,
        model: OpenAIClient,
        *,
        with_background: bool,
        output_dir: Path,
        max_steps: int | None = None,
    ) -> None:
        self.model = model
        self.with_background = with_background
        self.output_dir = output_dir
        self.max_steps = max_steps

    async def __call__(
        self,
        instance: Sample,
        *,
        sampling_params: dict[str, Any] = {},
        **kwargs: Any,
    ) -> Trace:
        row = dict(instance)
        problem_root = self.output_dir / str(row["problem_id"])
        prompt_root = problem_root / "prompts"
        code_root = problem_root / "generated_code"
        official = _official_adapter()
        assistant = official.ScicodePromptingAssistant(
            output_dir=code_root,
            prompt_dir=prompt_root,
            with_background=self.with_background,
        )
        prompt_template = (
            official.BACKGOUND_PROMPT_TEMPLATE
            if self.with_background
            else official.DEFAULT_PROMPT_TEMPLATE
        )
        subtraces: list[ChatTrace] = []
        steps = _active_steps(row)
        if self.max_steps is not None:
            steps = steps[: self.max_steps]
        for index, step in enumerate(steps):
            # prepare_final_prompt_with_steps uses the original 1-based position.
            original_index = row["sub_steps"].index(step)
            prompt, _ = assistant.prepare_final_prompt_with_steps(
                prob_data=row,
                num_steps=original_index + 1,
                tot_steps=len(row["sub_steps"]),
                prompt_template=prompt_template,
                save=True,
            )
            query = ChatTrace.from_messages(
                (ChatMessage(role="user", content=prompt),)
            )
            result = await self.model.step(
                query,
                sampling_params=sampling_params,
                **kwargs,
            )
            response = result.last_assistant().text_content
            assistant.register_previous_response(
                prob_data=row,
                response=response,
                previous_code=assistant.generate_prompt_with_steps(
                    row, original_index + 1, prompt_template
                )[1],
                num_steps=original_index + 1,
            )
            subtraces.append(result)
        return ChatTrace.from_messages(()).as_root_of(subtraces)


class SciCodeReward(RewardFunction[SciCodeReference]):
    """Run the unchanged SciCode HDF5 evaluator and expose both official metrics."""

    reference_type = SciCodeReference

    async def evaluate(self, trace: Trace, reference: SciCodeReference) -> Reward:
        output_dir = Path(reference.output_dir)
        model_dir = output_dir / "generated_code" / (
            "with_background" if reference.with_background else "without_background"
        )
        model_dir.mkdir(parents=True, exist_ok=True)
        subtraces = trace.subtraces or [trace]
        for step, subtrace in zip(_active_steps(reference.row), subtraces):
            code = extract_python_script(subtrace.last_assistant().text_content)
            (model_dir / f"{step['step_number']}.py").write_text(
                f"{reference.row['required_dependencies']}\n{code}\n",
                encoding="utf-8",
            )
        with tempfile.TemporaryDirectory(prefix="scicode-avacore-", dir=output_dir) as tmp:
            evaluator = _official_adapter().ScicodeEvaluator(
                h5py_file=reference.h5py_file,
                code_dir=output_dir,
                log_dir=output_dir / "logs",
                with_background=reference.with_background,
            )
            # The upstream evaluator writes its temporary assertion scripts relative
            # to the current directory. Keep that detail isolated per rollout.
            old_cwd = Path.cwd()
            old_path = os.environ.get("PATH", "")
            os.chdir(tmp)
            os.environ["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{old_path}"
            try:
                problem_correct, total_correct, total_steps = evaluator.test_code(reference.row)
            finally:
                os.chdir(old_cwd)
                os.environ["PATH"] = old_path
        return Reward(
            score=float(problem_correct),
            reason="SciCode official evaluator",
            components={
                "problem_correctness": Reward(score=float(problem_correct)),
                "subproblem_correctness": Reward(
                    score=float(total_correct / total_steps if total_steps else 0.0),
                    metadata={"total_correct": total_correct, "total_steps": total_steps},
                ),
            },
            metadata={
                "total_correct": total_correct,
                "total_steps": total_steps,
                "problem_correct": problem_correct,
            },
        )


async def load_rows(
    split: str,
    problem_ids: list[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    dataset = load_dataset("SciCode1/SciCode", split=split)
    rows = [dict(row) for row in dataset]
    if problem_ids:
        selected = set(problem_ids)
        rows = [row for row in rows if str(row["problem_id"]) in selected]
    return rows[:limit] if limit is not None else rows


async def run(args: argparse.Namespace) -> None:
    if not args.postgres:
        raise SystemExit("Set POSTGRES or pass --postgres; AvaCore PostgreSQL is the primary trace store")
    rows = await load_rows(args.split, args.problem_id, args.limit)
    if not rows:
        raise SystemExit("No SciCode rows matched the requested selection")
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    endpoint = HttpEndpoint.from_url(
        _endpoint_base_url(args.base_url),
        headers=HttpEndpoint.bearer(args.api_key),
    )
    sampling_params = {
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        **(
            {"chat_template_kwargs": {"enable_thinking": False}}
            if args.disable_thinking
            else {}
        ),
    }
    model = OpenAIClient(
        endpoint,
        args.model,
        timeout=args.timeout,
        sampling_params=sampling_params,
    )
    generate = SciCodeGenerate(
        model,
        with_background=args.with_background,
        output_dir=output_dir,
        max_steps=args.max_steps,
    )
    pairs = [
        (
            Sample({**row, "id": str(row["problem_id"])}, key=lambda sample: sample["id"]),
            SciCodeReference(
                row=row,
                h5py_file=str(Path(args.h5py_file).resolve()),
                output_dir=str(output_dir / str(row["problem_id"])),
                with_background=args.with_background,
            ),
        )
        for row in rows
    ]
    run_name = args.run_name or datetime.now(timezone.utc).strftime("scicode-%Y%m%dT%H%M%SZ")
    backend = PostgresBackend(args.postgres)
    results: list[RolloutResult] = []
    errors = 0
    async with backend:
        async with backend.run(
            kind="benchmark",
            model=args.model,
            run=run_name,
            collection="scicode",
            collection_version=1,
            size=len(pairs),
            trials=1,
            sampling_params=sampling_params,
            config={
                "split": args.split,
                "problem_ids": args.problem_id,
                "with_background": args.with_background,
                "h5py_file": str(Path(args.h5py_file).resolve()),
            },
            schema=Schema(preview="problem_id"),
            resume=args.resume,
        ) as stored_run:
            await stored_run.update(status="running")
            engine = RolloutEngine(generate, SciCodeReward(), concurrency=args.concurrency)
            async with engine:
                async for outcome in engine.run_pairs(pairs, stream_rollout=False):
                    query_id = str(outcome.instance["id"])
                    if isinstance(outcome, RolloutError):
                        errors += 1
                        await stored_run.create_errored_rollout(
                            query_id=query_id,
                            trial_id=0,
                            instance=outcome.instance,
                            error=outcome.error,
                            status=outcome.status,
                        )
                    else:
                        results.append(outcome)
                        await stored_run.create_rollout(
                            query_id=query_id,
                            trial_id=0,
                            instance=outcome.instance,
                            trace=outcome.trace,
                            reward=outcome.reward,
                            status=outcome.status,
                        )
                        if outcome.reward is not None:
                            await stored_run.score_group(query_id)
                    await stored_run.update(
                        status="running",
                        score=sum(item.reward.score for item in results if item.reward is not None)
                        / len(results)
                        if results
                        else 0.0,
                        samples=len(results),
                        successful_rollouts=len(results),
                        errors=errors,
                    )

        export_path = Path(args.export or output_dir / "rollouts.jsonl").resolve()
        export_path.parent.mkdir(parents=True, exist_ok=True)
        await write_jsonl(
            backend.export_rollouts(
                model=args.model,
                run=run_name,
                collection="scicode",
                collection_version=1,
            ),
            str(export_path),
        )

    rewards = [item.reward for item in results if item.reward is not None]
    total_steps = sum(reward.metadata["total_steps"] for reward in rewards)
    total_correct = sum(reward.metadata["total_correct"] for reward in rewards)
    print(json.dumps({
        "run": run_name,
        "samples": len(results),
        "errors": errors,
        "problem_correctness": sum(reward.score for reward in rewards) / len(rewards) if rewards else 0.0,
        "subproblem_correctness": total_correct / total_steps if total_steps else 0.0,
        "trace_file": str(export_path),
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://localhost:8000"),
        help="OpenAI-compatible root URL; a trailing /v1 is accepted",
    )
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", "dummy"))
    parser.add_argument("--model", default=os.getenv("MODEL", "Kimi-K3"))
    parser.add_argument("--split", default="test")
    parser.add_argument("--problem-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--h5py-file", required=True)
    parser.add_argument("--output", default="./avacore_runs")
    parser.add_argument("--postgres", default=os.getenv("POSTGRES"))
    parser.add_argument("--run-name")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--export", help="JSONL destination; exported from PostgreSQL after the run")
    parser.add_argument("--with-background", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="Pass the common OpenAI-compatible no-thinking switch to the provider",
    )
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--concurrency", type=int, default=1)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
