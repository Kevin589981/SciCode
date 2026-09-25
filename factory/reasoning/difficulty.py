"""Named-solver difficulty measurement for verified reasoning tasks."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import math
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .author import _json_objects
from .rollout import run_rollouts
from .schema import canonical_hash, validate_task, validate_trace
from .student_view import render_student_user

PANEL_SCHEMA = "scicode-difficulty-panel-v1"
ASSESSMENT_SCHEMA = "scicode-solution-assessment-v1"
DIFFICULTY_SCHEMA = "scicode-task-difficulty-v1"
ASSESSMENT_POLICY = "solution-adequacy-v1"
SCORE_NAMES = (
    "scientific_correctness",
    "constraint_satisfaction",
    "reasoning_completeness",
    "final_answer_adequacy",
)


class DifficultyError(ValueError):
    """A panel, assessment, or summary is invalid."""


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DifficultyError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise DifficultyError(f"{path}:{number}: row must be an object")
            rows.append(row)
    return rows


def load_panel(path: Path) -> dict:
    try:
        panel = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DifficultyError(f"invalid panel JSON: {exc}") from exc
    if not isinstance(panel, dict) or panel.get("schema_version") != PANEL_SCHEMA:
        raise DifficultyError(f"panel schema_version must be {PANEL_SCHEMA}")
    solvers = panel.get("solvers")
    if not isinstance(solvers, list) or not solvers:
        raise DifficultyError("panel.solvers must be a nonempty list")
    names = set()
    models = set()
    for index, solver in enumerate(solvers):
        if not isinstance(solver, dict):
            raise DifficultyError(f"panel.solvers[{index}] must be an object")
        name = solver.get("name")
        model = solver.get("model")
        if not isinstance(name, str) or not name.strip():
            raise DifficultyError(f"panel.solvers[{index}].name must be nonempty")
        if not isinstance(model, str) or not model.strip():
            raise DifficultyError(f"panel.solvers[{index}].model must be nonempty")
        if name in names or model in models:
            raise DifficultyError("panel solver names and model IDs must be unique")
        names.add(name)
        models.add(model)
        attempts = solver.get("attempts", 2)
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
            raise DifficultyError(f"panel.solvers[{index}].attempts must be positive")
        temperature = solver.get("temperature", 0.7)
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise DifficultyError(f"panel.solvers[{index}].temperature must be numeric")
    evaluator = panel.get("evaluator_model")
    if not isinstance(evaluator, str) or not evaluator.strip():
        raise DifficultyError("panel.evaluator_model must be nonempty")
    if evaluator in models and not panel.get("allow_evaluator_solver_overlap", False):
        raise DifficultyError(
            "panel evaluator_model must differ from every solver model"
        )
    for field, default in (("min_distinct_models", 2), ("min_trials", 4)):
        value = panel.get(field, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise DifficultyError(f"panel.{field} must be positive")
    return panel


def assessment_id_for(trace: dict, evaluator_model: str) -> str:
    return canonical_hash(
        {
            "trace_id": validate_trace(trace)["trace_id"],
            "evaluator_model": evaluator_model,
            "policy": ASSESSMENT_POLICY,
        }
    )


def _assessment_prompt(task: dict, trace: dict) -> str:
    task_view = {
        "archetype": task["archetype"],
        "source": task["source"],
        "problem": task["problem"],
        "deliverable": task["deliverable"],
        "student_visible_prompt": render_student_user(task),
    }
    response = [
        message for message in trace["messages"] if message.get("role") == "assistant"
    ]
    return f"""You are evaluating whether a solver actually solved a scientific
reasoning task. This is a DIFFICULTY measurement, not an SFT-value judgment.

Use the private source only as evidence. Accept scientifically correct methods
that differ from the source. Do not count length, confidence, or the auxiliary
outcome as correctness. Name every critical scientific error explicitly.

TASK AND PRIVATE SOURCE:
{json.dumps(task_view, ensure_ascii=False, indent=2)}

SOLVER RESPONSE:
{json.dumps(response, ensure_ascii=False, indent=2)}

Return ONLY one JSON object:
{{
  "scores": {{
    "scientific_correctness": 0,
    "constraint_satisfaction": 0,
    "reasoning_completeness": 0,
    "final_answer_adequacy": 0
  }},
  "critical_errors": ["empty only when no critical error remains"],
  "rationale": "specific evidence for solved/partial/failed status"
}}

All scores are integers 0..4. Do not output `solved`; it is derived by policy.
"""


def assess_trace(
    task: dict,
    trace: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: int = 2400,
    max_input_chars: int = 180_000,
) -> dict:
    """Judge solution adequacy separately from trace training value."""
    task = validate_task(task)
    trace = validate_trace(trace)
    if trace["task_id"] != task["task_id"] or trace["task_hash"] != canonical_hash(
        task
    ):
        raise DifficultyError("trace does not match task")
    model = model or llm.client_config()["model"]
    prompt = _assessment_prompt(task, trace)
    if len(prompt) > max_input_chars:
        raise DifficultyError(
            f"assessment input is {len(prompt)} chars > {max_input_chars}; "
            "do not silently truncate solver reasoning"
        )
    response = chat_fn(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DifficultyError(
            f"evaluator response has no usable message: {exc}"
        ) from exc
    objects = []
    for field in ("content", "reasoning_content"):
        objects.extend(_json_objects(message.get(field) or ""))
    value = next(
        (item for item in objects if isinstance(item.get("scores"), dict)), None
    )
    if value is None:
        raise DifficultyError("evaluator response has no complete result object")
    scores = value["scores"]
    for name in SCORE_NAMES:
        score = scores.get(name)
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 4:
            raise DifficultyError(f"scores.{name} must be an integer from 0 to 4")
    critical_errors = value.get("critical_errors")
    if not isinstance(critical_errors, list) or any(
        not isinstance(item, str) for item in critical_errors
    ):
        raise DifficultyError("critical_errors must be a string list")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise DifficultyError("rationale must be nonempty")
    errors = [item.strip() for item in critical_errors if item.strip()]
    solved = not errors and all(scores[name] >= 3 for name in SCORE_NAMES)
    partial = (
        not solved
        and scores["scientific_correctness"] >= 2
        and sum(scores.values()) / len(scores) >= 2.5
    )
    reasoning_chars = sum(
        len(message.get("reasoning_content") or "")
        for message in trace["messages"]
        if message.get("role") == "assistant"
    )
    return {
        "schema_version": ASSESSMENT_SCHEMA,
        "assessment_id": assessment_id_for(trace, model),
        "policy_version": ASSESSMENT_POLICY,
        "task_id": task["task_id"],
        "task_hash": canonical_hash(task),
        "trace_id": trace["trace_id"],
        "solver_model": trace["model"],
        "attempt": trace.get("attempt", 0),
        "scores": {name: scores[name] for name in SCORE_NAMES},
        "solved": solved,
        "partial": partial,
        "critical_errors": errors,
        "rationale": rationale,
        "reasoning_chars": reasoning_chars,
        "evaluator": {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": response.get("usage") or {},
        },
    }


def run_assessments(
    tasks_path: Path,
    traces_path: Path,
    output_path: Path,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 2400,
    concurrency: int = 1,
    errors_path: Path | None = None,
) -> dict:
    if concurrency < 1:
        raise DifficultyError("concurrency must be positive")
    tasks = {row["task_id"]: validate_task(row) for row in _jsonl(tasks_path)}
    traces = [validate_trace(row) for row in _jsonl(traces_path)]
    model = model or llm.client_config()["model"]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path = errors_path or output_path.with_suffix(".errors.jsonl")
    existing = _jsonl(output_path) if output_path.exists() else []
    done = {row.get("assessment_id") for row in existing}
    jobs = []
    skipped = 0
    for trace in traces:
        task = tasks.get(trace["task_id"])
        if task is None:
            raise DifficultyError(f"no task found for trace {trace['trace_id']}")
        assessment_id = assessment_id_for(trace, model)
        if assessment_id in done:
            skipped += 1
        else:
            jobs.append((task, trace, assessment_id))
    counts = {
        "written": 0,
        "solved": 0,
        "partial": 0,
        "failed": 0,
        "errors": 0,
        "skipped": skipped,
    }

    def one(job):
        task, trace, _assessment_id = job
        return assess_trace(
            task,
            trace,
            chat_fn=chat_fn,
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    with output_path.open("a", encoding="utf-8") as output, Path(errors_path).open(
        "a", encoding="utf-8"
    ) as errors, futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending = {pool.submit(one, job): job for job in jobs}
        for future in futures.as_completed(pending):
            task, trace, assessment_id = pending[future]
            try:
                row = future.result()
            except Exception as exc:
                errors.write(
                    json.dumps(
                        {
                            "assessment_id": assessment_id,
                            "task_id": task["task_id"],
                            "trace_id": trace["trace_id"],
                            "evaluator_model": model,
                            "error": f"{type(exc).__name__}: {exc}"[:1200],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                errors.flush()
                counts["errors"] += 1
                continue
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            counts["written"] += 1
            counts[
                "solved" if row["solved"] else "partial" if row["partial"] else "failed"
            ] += 1
    return counts


def _wilson(successes: int, trials: int, z: float = 1.96) -> list[float] | None:
    if trials < 1:
        return None
    rate = successes / trials
    denominator = 1 + z * z / trials
    centre = rate + z * z / (2 * trials)
    margin = z * math.sqrt((rate * (1 - rate) + z * z / (4 * trials)) / trials)
    return [
        round(max(0.0, (centre - margin) / denominator), 6),
        round(min(1.0, (centre + margin) / denominator), 6),
    ]


def summarize_difficulty(
    tasks: list[dict],
    traces: list[dict],
    assessments: list[dict],
    panel: dict,
) -> list[dict]:
    """Derive model-relative labels; insufficient panels remain uncalibrated."""
    tasks = [validate_task(task) for task in tasks]
    traces = [validate_trace(trace) for trace in traces]
    trace_by_id = {trace["trace_id"]: trace for trace in traces}
    by_task = defaultdict(list)
    for assessment in assessments:
        trace = trace_by_id.get(assessment.get("trace_id"))
        if trace is None:
            raise DifficultyError(
                f"assessment references unknown trace {assessment.get('trace_id')}"
            )
        by_task[trace["task_id"]].append(assessment)
    minimum_models = int(panel.get("min_distinct_models", 2))
    minimum_trials = int(panel.get("min_trials", 4))
    rows = []
    for task in sorted(tasks, key=lambda row: row["task_id"]):
        values = by_task.get(task["task_id"], [])
        models = sorted({str(row["solver_model"]) for row in values})
        trials = len(values)
        solved = sum(row.get("solved") is True for row in values)
        partial = sum(row.get("partial") is True for row in values)
        solved_rate = solved / trials if trials else None
        effective_rate = (solved + 0.5 * partial) / trials if trials else None
        calibrated = len(models) >= minimum_models and trials >= minimum_trials
        if not calibrated:
            band = "uncalibrated"
        elif solved_rate >= 0.8:
            band = "too_easy"
        elif solved_rate >= 0.6:
            band = "easy"
        elif solved_rate >= 0.3:
            band = "medium"
        elif solved_rate > 0:
            band = "hard"
        else:
            band = "unresolved"
        rows.append(
            {
                "schema_version": DIFFICULTY_SCHEMA,
                "task_id": task["task_id"],
                "task_hash": canonical_hash(task),
                "panel_hash": canonical_hash(panel),
                "evaluator_model": panel["evaluator_model"],
                "band": band,
                "calibrated": calibrated,
                "models_tested": models,
                "distinct_models": len(models),
                "trials": trials,
                "solved": solved,
                "partial": partial,
                "failed": trials - solved - partial,
                "solved_rate": None if solved_rate is None else round(solved_rate, 6),
                "effective_rate": (
                    None if effective_rate is None else round(effective_rate, 6)
                ),
                "solved_rate_wilson_95": _wilson(solved, trials),
                "policy": {
                    "min_distinct_models": minimum_models,
                    "min_trials": minimum_trials,
                    "zero_solve_band": "unresolved_not_hard",
                },
            }
        )
    return rows


def _atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def run_panel(
    tasks_path: Path,
    panel_path: Path,
    output_dir: Path,
    *,
    preflight_path: Path | None = None,
    verification_path: Path | None = None,
    preflight_model: str | None = None,
    verifier_model: str | None = None,
    chat_fn: Callable = llm.chat,
    max_tokens: int = 16384,
    timeout: int = 2400,
    concurrency: int = 1,
) -> dict:
    panel = load_panel(panel_path)
    panel_hash = canonical_hash(panel)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_key = panel_hash[:16]
    traces_path = output_dir / f"panel_traces.{panel_key}.jsonl"
    rollout_results = []
    for solver in panel["solvers"]:
        rollout_results.append(
            {
                "name": solver["name"],
                "model": solver["model"],
                "result": run_rollouts(
                    tasks_path,
                    traces_path,
                    preflight_path=preflight_path,
                    verification_path=verification_path,
                    preflight_model=preflight_model,
                    verifier_model=verifier_model,
                    chat_fn=chat_fn,
                    model=solver["model"],
                    attempts=int(solver.get("attempts", 2)),
                    temperature=float(solver.get("temperature", 0.7)),
                    max_tokens=int(solver.get("max_tokens", max_tokens)),
                    timeout=int(solver.get("timeout", timeout)),
                    concurrency=concurrency,
                    run_variant=canonical_hash(
                        {
                            "purpose": "difficulty",
                            "panel_hash": panel_hash,
                            "solver": solver,
                        }
                    ),
                ),
            }
        )
    assessments_path = output_dir / f"solution_assessments.{panel_key}.jsonl"
    assessment_result = run_assessments(
        tasks_path,
        traces_path,
        assessments_path,
        chat_fn=chat_fn,
        model=panel["evaluator_model"],
        max_tokens=int(panel.get("evaluator_max_tokens", 4096)),
        timeout=int(panel.get("evaluator_timeout", timeout)),
        concurrency=concurrency,
    )
    tasks = _jsonl(tasks_path)
    traces = _jsonl(traces_path)
    assessments = _jsonl(assessments_path)
    difficulty = summarize_difficulty(tasks, traces, assessments, panel)
    difficulty_path = output_dir / "difficulty.jsonl"
    _atomic_jsonl(difficulty_path, difficulty)
    report = {
        "schema_version": DIFFICULTY_SCHEMA,
        "panel": panel,
        "panel_hash": panel_hash,
        "rollouts": rollout_results,
        "assessments": assessment_result,
        "tasks": len(difficulty),
        "calibrated": sum(row["calibrated"] for row in difficulty),
        "bands": {
            band: sum(row["band"] == band for row in difficulty)
            for band in sorted({row["band"] for row in difficulty})
        },
        "artifacts": {
            "traces": str(traces_path),
            "assessments": str(assessments_path),
            "difficulty": str(difficulty_path),
        },
    }
    report_path = output_dir / "difficulty.report.json"
    temporary = report_path.with_name(report_path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--verification", type=Path)
    parser.add_argument("--preflight-model")
    parser.add_argument("--verifier-model")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    report = run_panel(
        args.tasks,
        args.panel,
        args.out_dir,
        preflight_path=args.preflight,
        verification_path=args.verification,
        preflight_model=args.preflight_model,
        verifier_model=args.verifier_model,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        concurrency=args.concurrency,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
