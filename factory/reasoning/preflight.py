"""Reasoning-depth preflight for authored tasks.

Preflight asks whether a task demands meaningful scientific cognition. It does
not run or score a candidate answer and deliberately has no pass/fail outcome
input from a solver.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .author import _json_objects
from .schema import SchemaError, canonical_hash, validate_task
from .student_view import render_student_user, student_view_hash

PREFLIGHT_SCHEMA = "scicode-reasoning-preflight-v1"
PREFLIGHT_POLICY = "reasoning-depth-v3"
CRITIC_SCORE_NAMES = (
    "scientific_depth",
    "multi_step_dependency",
    "decision_requirement",
    "nontriviality",
)

OPERATION_PATTERNS = {
    "derive": re.compile(r"\b(derive|formulate|deduce)\w*\b", re.I),
    "analyze": re.compile(r"\b(analy[sz]e|assess|characteri[sz]e)\w*\b", re.I),
    "compare": re.compile(r"\b(compare|contrast|distinguish)\w*\b", re.I),
    "diagnose": re.compile(r"\b(diagnose|infer|identify)\w*\b", re.I),
    "justify": re.compile(r"\b(justify|explain|reason)\w*\b", re.I),
    "select": re.compile(r"\b(select|choose|decide)\w*\b", re.I),
    "revise": re.compile(r"\b(revise|correct|improve|repair)\w*\b", re.I),
    "validate": re.compile(r"\b(validate|verify|test)\w*\b", re.I),
    "implement": re.compile(r"\b(implement|construct|code)\w*\b", re.I),
}


class PreflightError(ValueError):
    """A task or critic response could not be evaluated."""


def preflight_id_for(task: dict, model: str) -> str:
    return canonical_hash(
        {
            "task_hash": canonical_hash(validate_task(task)),
            "critic_model": model,
            "policy": PREFLIGHT_POLICY,
        }
    )


def _categories(text: str) -> set[str]:
    return {
        name for name, pattern in OPERATION_PATTERNS.items() if pattern.search(text)
    }


def structural_depth(task: dict) -> dict:
    """Measure explicit cognitive diversity without calling a model."""
    validate_task(task)
    contract = task["reasoning_contract"]
    operation_text = "\n".join(contract["cognitive_operations"])
    operation_categories = _categories(operation_text)
    question_categories = _categories(task["problem"]["question"])
    reasoning_categories = operation_categories - {"implement"}
    question_reasoning = question_categories - {"implement"}
    reasons = []
    if len(reasoning_categories) < 3:
        reasons.append("fewer than three explicit cognitive categories")
    if len(question_reasoning) < 2:
        reasons.append("question exposes fewer than two reasoning categories")
    if (
        task["deliverable"]["kind"] != "analysis"
        and "implement" not in question_categories
    ):
        reasons.append("implementation deliverable is not visible in the question")
    score = min(4, 1 + len(reasoning_categories))
    return {
        "passed": not reasons,
        "score": score,
        "operation_categories": sorted(operation_categories),
        "question_categories": sorted(question_categories),
        "reasons": reasons,
    }


def _parse_object(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        pieces = text.split("```", 2)
        text = pieces[1] if len(pieces) > 1 else ""
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"critic response is not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightError("critic response must be a JSON object")
    return value


def _critic_prompt(task: dict) -> str:
    return f"""You are an independent critic of a scientific reasoning task.

Judge whether solving the task requires dependent scientific reasoning rather
than docstring translation, mechanical branching, source recall, or verbose but
empty explanation. Do not solve the task. Judge only the exact student-visible
prompt below. Private source or rubric cannot repair missing inputs.

STUDENT-VISIBLE PROMPT:
{render_student_user(task)}

Return ONLY this JSON object, using integer scores from 0 (absent) to 4 (strong):
{{
  "scores": {{
    "scientific_depth": 0,
    "multi_step_dependency": 0,
    "decision_requirement": 0,
    "nontriviality": 0
  }},
  "missing_inputs": [],
  "answer_exposed": false,
  "shallow_failure_mode": null,
  "rationale": "specific evidence from the task"
}}
"""


def critic_task(
    task: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: int = 1200,
) -> dict:
    """Ask a semantic critic for bounded task-depth scores."""
    validate_task(task)
    response = chat_fn(
        [{"role": "user", "content": _critic_prompt(task)}],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PreflightError(f"critic response has no usable message: {exc}") from exc
    objects = []
    for field in ("content", "reasoning_content"):
        objects.extend(_json_objects(message.get(field) or ""))
    value = next(
        (item for item in objects if isinstance(item.get("scores"), dict)), None
    )
    if value is None:
        # Preserve the older, more specific parse error for malformed content.
        value = _parse_object(message.get("content") or "")
    scores = value.get("scores")
    if not isinstance(scores, dict):
        raise PreflightError("critic scores must be an object")
    for name in CRITIC_SCORE_NAMES:
        score = scores.get(name)
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 4:
            raise PreflightError(f"critic score {name} must be an integer from 0 to 4")
    shallow = value.get("shallow_failure_mode")
    if shallow is not None and not isinstance(shallow, str):
        raise PreflightError("shallow_failure_mode must be a string or null")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise PreflightError("critic rationale must be a nonempty string")
    missing_inputs = value.get("missing_inputs")
    answer_exposed = value.get("answer_exposed")
    if not isinstance(missing_inputs, list) or any(
        not isinstance(item, str) for item in missing_inputs
    ):
        raise PreflightError("missing_inputs must be a string list")
    if not isinstance(answer_exposed, bool):
        raise PreflightError("answer_exposed must be a boolean")
    return {
        "model": model or llm.client_config()["model"],
        "scores": {name: scores[name] for name in CRITIC_SCORE_NAMES},
        "shallow_failure_mode": shallow,
        "rationale": rationale,
        "missing_inputs": missing_inputs,
        "answer_exposed": answer_exposed,
        "usage": response.get("usage") or {},
    }


def preflight_task(
    task: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: int = 1200,
) -> dict:
    """Combine deterministic structure and semantic depth into an admission record."""
    task = validate_task(task)
    model = model or llm.client_config()["model"]
    structural = structural_depth(task)
    critic = critic_task(
        task,
        chat_fn=chat_fn,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    scores = critic["scores"]
    semantic_pass = (
        sum(scores.values()) / len(scores) >= 3.0
        and scores["scientific_depth"] >= 2
        and scores["multi_step_dependency"] >= 2
        and scores["nontriviality"] >= 2
        and not critic["missing_inputs"]
        and not critic["answer_exposed"]
    )
    return {
        "schema_version": PREFLIGHT_SCHEMA,
        "preflight_id": preflight_id_for(task, model),
        "task_id": task["task_id"],
        "task_hash": canonical_hash(task),
        "accepted": structural["passed"] and semantic_pass,
        "policy_version": PREFLIGHT_POLICY,
        "student_view_hash": student_view_hash(task),
        "structural": structural,
        "critic": critic,
    }


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PreflightError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise PreflightError(f"{path}:{number}: row must be an object")
            rows.append(row)
    return rows


def run_preflight(
    tasks_path: Path,
    output_path: Path,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: int = 1200,
    concurrency: int = 1,
    errors_path: Path | None = None,
) -> dict:
    """Evaluate task JSONL with task-hash resume and append-safe writes."""
    if concurrency < 1:
        raise PreflightError("concurrency must be positive")
    tasks = _jsonl(Path(tasks_path))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path = errors_path or output_path.with_suffix(".errors.jsonl")
    model = model or llm.client_config()["model"]
    existing = _jsonl(output_path) if output_path.exists() else []
    done = {row.get("preflight_id") for row in existing}
    counts = {"accepted": 0, "rejected": 0, "errors": 0, "skipped": 0}
    jobs = []
    for task in tasks:
        task_hash = canonical_hash(task)
        preflight_id = preflight_id_for(task, model)
        if preflight_id in done:
            counts["skipped"] += 1
        else:
            jobs.append((task, task_hash, preflight_id))

    def one(job):
        task, _task_hash, _preflight_id = job
        return preflight_task(
            task,
            chat_fn=chat_fn,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    with output_path.open("a", encoding="utf-8") as output, Path(errors_path).open(
        "a", encoding="utf-8"
    ) as errors, futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending = {pool.submit(one, job): job for job in jobs}
        for future in futures.as_completed(pending):
            task, task_hash, preflight_id = pending[future]
            try:
                record = future.result()
            except (PreflightError, SchemaError, KeyError, TypeError) as exc:
                errors.write(
                    json.dumps(
                        {
                            "task_id": task.get("task_id"),
                            "task_hash": task_hash,
                            "preflight_id": preflight_id,
                            "critic_model": model,
                            "error": f"{type(exc).__name__}: {exc}"[:1000],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                errors.flush()
                counts["errors"] += 1
                continue
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            done.add(preflight_id)
            counts["accepted" if record["accepted"] else "rejected"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    result = run_preflight(
        args.tasks,
        args.out,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        concurrency=args.concurrency,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
