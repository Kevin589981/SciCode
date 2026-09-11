"""Run the strict SciCode protocol through AvaCore.

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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from ava_core.core import ChatMessage, ChatTrace, HttpEndpoint, OpenAIClient, Schema, Trace
from ava_core.core.retry import HTTPRetry
from ava_core.generate.core import GenerateFunction, Sample
from ava_core.rewards.core import Reward, RewardFunction
from ava_core.rollout import RolloutEngine, RolloutError, RolloutResult
from ava_core.runner import RECOVERABLE_ERRORS
from ava_core.store.postgres import PostgresBackend
from ava_core.utils.io import write_jsonl
from datasets import load_dataset

from scicode.gen.models import extract_python_script
from scicode.pipeline.candidate import (
    CandidateManifest,
    load_candidate,
    read_jsonl,
    validate_rows,
)
from scicode.pipeline.run_manifest import build_run_manifest, write_json_atomic

_OFFICIAL_CWD = Path(__file__).parents[1] / "inspect_ai"
_OFFICIAL_LOCK = asyncio.Lock()


def _endpoint_base_url(base_url: str) -> str:
    """Convert an OpenAI-style base URL to the root expected by AvaCore."""
    normalized = base_url.rstrip("/")
    return normalized[:-3] if normalized.endswith("/v1") else normalized


def _normalize_usage(raw: Any) -> dict[str, int]:
    """Normalize provider usage fields without losing reasoning-token counts."""
    if not isinstance(raw, Mapping):
        return {}

    usage: dict[str, int] = {}
    aliases = {
        "prompt_tokens": ("prompt_tokens", "input_tokens"),
        "completion_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[target] = int(value)
                break

    reasoning = raw.get("reasoning_tokens")
    if not isinstance(reasoning, (int, float)) or isinstance(reasoning, bool):
        details = raw.get("completion_tokens_details")
        if isinstance(details, Mapping):
            reasoning = details.get("reasoning_tokens")
    if isinstance(reasoning, (int, float)) and not isinstance(reasoning, bool):
        usage["reasoning_tokens"] = int(reasoning)

    prompt_details = raw.get("prompt_tokens_details")
    if isinstance(prompt_details, Mapping):
        cached = prompt_details.get("cached_tokens")
        if isinstance(cached, (int, float)) and not isinstance(cached, bool):
            usage["cached_tokens"] = int(cached)

    if "total_tokens" not in usage:
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if prompt is not None and completion is not None:
            usage["total_tokens"] = prompt + completion
    return usage


def _aggregate_usage(steps: list[dict[str, Any]], traces: list[ChatTrace]) -> dict[str, Any]:
    """Return run-level and per-subproblem token accounting for AvaCore."""
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
    }
    by_step: list[dict[str, Any]] = []
    steps_with_usage = 0
    for step, trace in zip(steps, traces):
        raw = trace.last_assistant().metadata.get("usage")
        usage = _normalize_usage(raw)
        if usage:
            steps_with_usage += 1
            for key in totals:
                totals[key] += usage.get(key, 0)
        by_step.append({"step_number": step["step_number"], "usage": usage})
    return {
        "available": steps_with_usage > 0,
        "steps": len(traces),
        "steps_with_usage": steps_with_usage,
        **totals,
        "by_step": by_step,
    }


class TraceOpenAIClient(OpenAIClient):
    """OpenAI client that retains provider usage in each assistant message.

    AvaCore's stock client currently keeps only ``finish_reason`` for chat
    completions. SciCode needs the provider usage for auditable trace export,
    so this adapter preserves the response's usage and identity fields.
    """

    async def step(self, trace: Trace, *, sampling_params: dict[str, Any] = {}, **kwargs: Any) -> Trace:
        payload = {
            "model": self.name,
            "messages": [message.to_dict() for message in trace.messages],
            **({"tools": [tool.to_dict() for tool in trace.tools]} if trace.tools else {}),
            **self.sampling_params,
            **sampling_params,
            **kwargs,
        }
        response = await self.request("/v1/chat/completions", payload, self.headers())
        choice = response["choices"][0]
        assistant = ChatMessage.from_dict(choice["message"])
        metadata = {
            "finish_reason": choice.get("finish_reason"),
            **{
                key: response[key]
                for key in ("id", "model", "system_fingerprint")
                if key in response
            },
        }
        if response.get("usage") is not None:
            metadata["usage"] = response["usage"]
        # Keep the provider-native response in the trace without retaining
        # request headers or credentials. The subproblem exporter uses this
        # field to preserve the previous SFT record contract.
        metadata["provider_response"] = response
        assistant = replace(assistant, metadata=metadata)
        return trace.extend(messages=(assistant,), query=trace, is_generated=True)


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
            async with _OFFICIAL_LOCK:
                old_cwd = Path.cwd()
                os.chdir(_OFFICIAL_CWD)
                try:
                    prompt, _ = assistant.prepare_final_prompt_with_steps(
                        prob_data=row,
                        num_steps=original_index + 1,
                        tot_steps=len(row["sub_steps"]),
                        prompt_template=prompt_template,
                        save=True,
                    )
                finally:
                    os.chdir(old_cwd)
            query = ChatTrace.from_messages(
                (ChatMessage(role="user", content=prompt),)
            )
            result = await self.model.step(
                query,
                sampling_params=sampling_params,
                **kwargs,
            )
            response = result.last_assistant().text_content
            async with _OFFICIAL_LOCK:
                old_cwd = Path.cwd()
                os.chdir(_OFFICIAL_CWD)
                try:
                    assistant.register_previous_response(
                        prob_data=row,
                        response=response,
                        previous_code=assistant.generate_prompt_with_steps(
                            row, original_index + 1, prompt_template
                        )[1],
                        num_steps=original_index + 1,
                    )
                finally:
                    os.chdir(old_cwd)
            subtraces.append(result)
        root = ChatTrace.from_messages(()).as_root_of(subtraces)
        return replace(root, metadata={"usage": _aggregate_usage(steps, subtraces)})


class SciCodeReward(RewardFunction[SciCodeReference]):
    """Run the unchanged SciCode HDF5 evaluator and expose both official metrics."""

    reference_type = SciCodeReference

    def __init__(self, *, score_by_subproblem: bool = False) -> None:
        self.score_by_subproblem = score_by_subproblem

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
        subproblem_score = float(total_correct / total_steps if total_steps else 0.0)
        primary_score = subproblem_score if self.score_by_subproblem else float(problem_correct)
        return Reward(
            score=primary_score,
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
                "problem_correctness": float(problem_correct),
                "subproblem_correctness": subproblem_score,
                "score_metric": "subproblem_correctness" if self.score_by_subproblem else "problem_correctness",
                "usage": trace.metadata.get("usage", {}),
            },
        )


async def load_rows(
    split: str,
    problem_ids: list[str] | None,
    limit: int | None,
    problem_file: str | None = None,
) -> list[dict[str, Any]]:
    if problem_file:
        source = Path(problem_file).expanduser().resolve()
        if not source.is_file():
            raise SystemExit(f"Problem JSONL does not exist: {source}")
        rows = validate_rows(read_jsonl(source), source)
    else:
        dataset = load_dataset("SciCode1/SciCode", split=split)
        rows = [dict(row) for row in dataset]
    if problem_ids:
        selected = set(problem_ids)
        rows = [row for row in rows if str(row["problem_id"]) in selected]
    return rows[:limit] if limit is not None else rows


def _aggregate_run_score(results: list[RolloutResult], *, score_by_subproblem: bool) -> float:
    rewards = [item.reward for item in results if item.reward is not None]
    if not rewards:
        return 0.0
    if score_by_subproblem:
        total_correct = sum(reward.metadata.get("total_correct", 0) for reward in rewards)
        total_steps = sum(reward.metadata.get("total_steps", 0) for reward in rewards)
        return total_correct / total_steps if total_steps else 0.0
    return sum(reward.score for reward in rewards) / len(rewards)


async def run(args: argparse.Namespace) -> None:
    candidate: CandidateManifest | None = None
    if args.candidate_dir:
        candidate = load_candidate(args.candidate_dir)
        candidate_problem = Path(candidate.problem_file).resolve()
        if args.problem_file and Path(args.problem_file).resolve() != candidate_problem:
            raise SystemExit("--problem-file disagrees with --candidate-dir/public/problem.jsonl")
        args.problem_file = str(candidate_problem)
        if not args.h5py_file:
            args.h5py_file = candidate.oracle_file
        elif Path(args.h5py_file).resolve() != Path(candidate.oracle_file).resolve():
            raise SystemExit("--h5py-file disagrees with --candidate-dir/oracle/targets.h5")
    if args.validate_only:
        if candidate is None:
            raise SystemExit("--validate-only requires --candidate-dir")
        print(json.dumps({"status": "candidate_valid", "candidate": candidate.as_dict()}, ensure_ascii=False))
        return
    if not args.h5py_file:
        raise SystemExit("Pass --h5py-file or --candidate-dir")
    if not args.postgres:
        raise SystemExit("Set POSTGRES or pass --postgres; AvaCore PostgreSQL is the primary trace store")
    rows = await load_rows(
        args.split,
        args.problem_id,
        args.limit,
        problem_file=args.problem_file,
    )
    if not rows:
        raise SystemExit("No SciCode rows matched the requested selection")
    with_background = (
        candidate.prompt_profile == "background"
        if candidate is not None
        else (
            args.prompt_profile == "background"
            if args.prompt_profile
            else args.with_background
        )
    )
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
            {"reasoning_effort": args.reasoning_effort}
            if args.reasoning_effort
            else {}
        ),
        **(
            {"chat_template_kwargs": {"enable_thinking": False}}
            if args.disable_thinking
            else {}
        ),
    }
    model = TraceOpenAIClient(
        endpoint,
        args.model,
        timeout=args.timeout,
        retry=HTTPRetry(max_retries=args.http_retries),
        sampling_params=sampling_params,
    )
    generate = SciCodeGenerate(
        model,
        with_background=with_background,
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
                with_background=with_background,
            ),
        )
        for row in rows
    ]
    run_name = args.run_name or datetime.now(timezone.utc).strftime("scicode-%Y%m%dT%H%M%SZ")
    expected_subproblems = sum(len(_active_steps(row)) for row in rows)
    run_config = {
        "split": args.split,
        "problem_file": str(Path(args.problem_file).resolve()) if args.problem_file else None,
        "problem_ids": args.problem_id,
        "with_background": with_background,
        "prompt_profile": "background" if with_background else "without_background",
        "score_metric": "subproblem_correctness" if args.score_by_subproblem else "problem_correctness",
        "h5py_file": str(Path(args.h5py_file).resolve()),
        "base_url": args.base_url,
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "timeout": args.timeout,
        "http_retries": args.http_retries,
        "concurrency": args.concurrency,
        "max_steps": args.max_steps,
    }
    if candidate is not None:
        run_config["candidate_id"] = candidate.candidate_id
        run_config["task_revision"] = candidate.revision
        run_config["candidate_hashes"] = {
            "canonical_record_sha256": candidate.canonical_record_sha256,
            "visible_contract_sha256": candidate.visible_contract_sha256,
            "solver_payload_sha256": candidate.solver_payload_sha256,
            "oracle_sha256": candidate.oracle_sha256,
            "provenance_sha256": candidate.provenance_sha256,
        }
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
            config=run_config,
            schema=Schema(preview="problem_id"),
            resume=args.resume,
        ) as stored_run:
            await stored_run.update(status="running")
            engine = RolloutEngine(
                generate,
                SciCodeReward(score_by_subproblem=args.score_by_subproblem),
                concurrency=args.concurrency,
                recoverable=RECOVERABLE_ERRORS,
            )
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
                        score=_aggregate_run_score(
                            results,
                            score_by_subproblem=args.score_by_subproblem,
                        ),
                        samples=len(results),
                        successful_rollouts=len(results),
                        errors=errors,
                    )
            await stored_run.update(
                status=("finished" if errors == 0 and len(results) == len(pairs) else "failed"),
                score=_aggregate_run_score(
                    results,
                    score_by_subproblem=args.score_by_subproblem,
                ),
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
    problem_correctness = (
        sum(reward.metadata.get("problem_correctness", reward.score) for reward in rewards)
        / len(rewards)
        if rewards
        else 0.0
    )
    run_usage = {}
    if rewards:
        usage_values = [reward.metadata.get("usage", {}) for reward in rewards]
        run_usage = {
            key: sum(
                value.get(key, 0)
                for value in usage_values
                if isinstance(value, Mapping)
            )
            for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "total_tokens")
        }
    completed_subproblems = sum(
        len(item.trace.subtraces or [])
        for item in results
        if getattr(item, "trace", None) is not None
    )
    manifest = build_run_manifest(
        run_id=run_name,
        status=("finished" if errors == 0 and len(results) == len(pairs) else "failed"),
        config=run_config,
        candidate=candidate,
        expected_problems=len(pairs),
        expected_subproblems=expected_subproblems,
        completed_problems=len(results),
        completed_subproblems=completed_subproblems,
        errors=errors,
        trace_export=export_path,
        usage=run_usage,
        retries=args.http_retries,
        notes=(
            ["debug_max_steps"]
            if args.max_steps is not None
            else []
        ),
    )
    manifest_path = Path(args.run_manifest or output_dir / "manifest.json").resolve()
    write_json_atomic(manifest_path, manifest)
    print(json.dumps({
        "run": run_name,
        "samples": len(results),
        "errors": errors,
        "problem_correctness": problem_correctness,
        "subproblem_correctness": total_correct / total_steps if total_steps else 0.0,
        "score_metric": "subproblem_correctness" if args.score_by_subproblem else "problem_correctness",
        "score": _aggregate_run_score(
            results,
            score_by_subproblem=args.score_by_subproblem,
        ),
        "trace_file": str(export_path),
        "manifest_file": str(manifest_path),
        "candidate_id": candidate.candidate_id if candidate else None,
        "task_revision": candidate.revision if candidate else None,
        "trace_complete": manifest["trace_complete"],
        "promotable": manifest["promotable"],
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://10.100.184.127:5050"),
        help="OpenAI-compatible root URL; a trailing /v1 is accepted",
    )
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", "dummy"))
    parser.add_argument("--model", default=os.getenv("MODEL", "Kimi-K3"))
    parser.add_argument("--split", default="test")
    parser.add_argument(
        "--candidate-dir",
        help="Candidate root containing public/problem.jsonl and oracle/targets.h5",
    )
    parser.add_argument(
        "--problem-file",
        help=(
            "Local SciCode-compatible JSONL source, such as a candidate's "
            "public/problem.jsonl; when set, do not load Hugging Face data"
        ),
    )
    parser.add_argument("--problem-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--h5py-file")
    parser.add_argument("--output", default="./avacore_runs")
    parser.add_argument("--postgres", default=os.getenv("POSTGRES"))
    parser.add_argument("--run-name")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--export", help="JSONL destination; exported from PostgreSQL after the run")
    parser.add_argument("--run-manifest", help="Run manifest destination (defaults to <output>/manifest.json)")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate --candidate-dir and print its manifest without contacting a provider",
    )
    parser.add_argument("--with-background", action="store_true")
    parser.add_argument(
        "--prompt-profile",
        choices=("background", "without_background"),
        help="Fixed SciCode prompt profile; candidate metadata takes precedence",
    )
    parser.add_argument(
        "--score-by-subproblem",
        action="store_true",
        help="Use the weighted subproblem pass rate as the AvaCore run score",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=262144)
    parser.add_argument(
        "--reasoning-effort",
        default=os.getenv("REASONING_EFFORT"),
        help="Provider reasoning effort, for example low, medium, high, xhigh, or max",
    )
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="Pass the common OpenAI-compatible no-thinking switch to the provider",
    )
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument(
        "--http-retries",
        type=int,
        default=5,
        help="Retries for transient provider timeouts and connection errors",
    )
    parser.add_argument("--concurrency", type=int, default=1)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
