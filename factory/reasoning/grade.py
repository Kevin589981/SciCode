"""Outcome-independent scientific reasoning trace grading."""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .schema import (
    GRADE_SCHEMA,
    SCORE_NAMES,
    SchemaError,
    canonical_hash,
    validate_grade,
    validate_task,
    validate_trace,
)


class GradeError(ValueError):
    """A trace cannot be graded or a judge response is invalid."""


GRADE_POLICY_VERSION = "reasoning-value-v2"


def grade_id_for(trace: dict, model: str) -> str:
    return canonical_hash(
        {
            "trace_id": trace["trace_id"],
            "judge_model": model,
            "policy": GRADE_POLICY_VERSION,
        }
    )


def current_grades(grades: list[dict], judge_model: str | None = None) -> list[dict]:
    """Select one current-policy grade per trace/judge, preferring canonical IDs."""
    grouped = {}
    for grade in grades:
        model = (grade.get("judge") or {}).get("model")
        trace_id = grade.get("trace_id")
        if (
            grade.get("policy_version") != GRADE_POLICY_VERSION
            or not isinstance(model, str)
            or not isinstance(trace_id, str)
            or (judge_model is not None and model != judge_model)
        ):
            continue
        grouped.setdefault((trace_id, model), []).append(grade)
    selected = []
    for (trace_id, model), options in grouped.items():
        expected_id = grade_id_for({"trace_id": trace_id}, model)
        canonical = [row for row in options if row.get("grade_id") == expected_id]
        choices = canonical or options
        if len(choices) > 1:
            raise GradeError(
                f"multiple current grades for trace {trace_id} and judge {model}"
            )
        selected.append(choices[0])
    return selected


def _parse_object(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1] if len(parts) > 1 else ""
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GradeError(f"judge response is not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise GradeError("judge response must be a JSON object")
    return value


def _judge_prompt(task: dict, trace: dict) -> str:
    task_view = {
        "archetype": task["archetype"],
        "problem": task["problem"],
        "deliverable": task["deliverable"],
        "reasoning_contract": task["reasoning_contract"],
    }
    trace_view = {
        "messages": trace["messages"],
        "outcome": trace.get("outcome"),
    }
    assistant_indices = [
        index
        for index, message in enumerate(trace["messages"])
        if message["role"] == "assistant"
    ]
    return f"""You are judging the TRAINING VALUE of a scientific reasoning trace.

Judge the reasoning itself. A failed auxiliary check can accompany an excellent
scientific trace; a passing check can accompany a trivial or lucky trace. Treat
the recorded check only as weak diagnostic evidence. Do not use it as the
selection rule. Long text is not inherently valuable: penalize repetition,
unsupported claims, dead loops, and confidently wrong scientific premises.

TASK:
{json.dumps(task_view, ensure_ascii=False, indent=2)}

COMPLETE TRACE:
{json.dumps(trace_view, ensure_ascii=False, indent=2)}

Assistant message indices that must each be annotated: {assistant_indices}

Return ONLY one JSON object:
{{
  "scores": {{
    "scientific_validity": 0,
    "causal_coherence": 0,
    "strategy": 0,
    "evidence_use": 0,
    "self_correction": 0,
    "insight_density": 0,
    "degeneracy": 0
  }},
  "rationale": "specific evidence from the reasoning",
  "message_annotations": [
    {{
      "message_index": {assistant_indices[0]},
      "train_reasoning": true,
      "train_content": true,
      "quality": "good" | "medium" | "bad",
      "rationale": "why these parts should or should not receive loss"
    }}
  ]
}}

All scores are integers 0..4. Higher is better except degeneracy, where 0 means
none and 4 means severe. `self_correction=0` is acceptable for a correct one-turn
trace. Include exactly one annotation for every listed assistant message index.
"""


def _trainability(scores: dict, annotations: list[dict]) -> bool:
    """Deterministic policy independent of executable outcome."""
    return (
        scores["scientific_validity"] >= 3
        and scores["causal_coherence"] >= 3
        and scores["strategy"] >= 2
        and scores["evidence_use"] >= 2
        and scores["insight_density"] >= 2
        # Moderate repetition or a truncated final answer does not erase a
        # scientifically useful reasoning trajectory. Reject only traces the
        # judge rates as severely degenerate.
        and scores["degeneracy"] <= 2
        and any(
            item.get("train_reasoning") or item.get("train_content")
            for item in annotations
        )
    )


def judge_trace(
    task: dict,
    trace: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
    timeout: int = 2400,
    max_input_chars: int = 160_000,
) -> dict:
    """Grade the complete native-thinking trace and derive supervision policy."""
    task = validate_task(task)
    trace = validate_trace(trace)
    if trace["task_id"] != task["task_id"] or trace["task_hash"] != canonical_hash(task):
        raise GradeError("trace does not match task content")
    model = model or llm.client_config()["model"]
    prompt = _judge_prompt(task, trace)
    if len(prompt) > max_input_chars:
        raise GradeError(
            f"complete judge input is {len(prompt)} chars > {max_input_chars}; "
            "raise max_input_chars instead of silently truncating reasoning"
        )
    response = chat_fn(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    try:
        value = _parse_object(response["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise GradeError(f"judge response has no usable message: {exc}") from exc
    scores = value.get("scores")
    if not isinstance(scores, dict):
        raise GradeError("judge scores must be an object")
    for name in SCORE_NAMES:
        score = scores.get(name)
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 4:
            raise GradeError(f"scores.{name} must be an integer from 0 to 4")
    annotations = value.get("message_annotations")
    if not isinstance(annotations, list):
        raise GradeError("message_annotations must be a list")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise GradeError("judge rationale must be nonempty")
    grade = {
        "schema_version": GRADE_SCHEMA,
        "grade_id": grade_id_for(trace, model),
        "trace_id": trace["trace_id"],
        "scores": {name: scores[name] for name in SCORE_NAMES},
        "trainable": _trainability(scores, annotations),
        "policy_version": GRADE_POLICY_VERSION,
        "rationale": rationale,
        "message_annotations": annotations,
        "judge": {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": response.get("usage") or {},
        },
    }
    try:
        return validate_grade(grade, trace)
    except SchemaError as exc:
        raise GradeError(f"judge JSON violates the grade schema: {exc}") from exc


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GradeError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise GradeError(f"{path}:{number}: row must be an object")
            rows.append(value)
    return rows


def run_grading(
    tasks_path: Path,
    traces_path: Path,
    output_path: Path,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
    timeout: int = 2400,
    max_input_chars: int = 160_000,
    concurrency: int = 1,
    errors_path: Path | None = None,
) -> dict:
    """Grade trace JSONL with bounded concurrency and judge-aware resume."""
    if concurrency < 1:
        raise GradeError("concurrency must be positive")
    tasks = {task["task_id"]: validate_task(task) for task in _jsonl(Path(tasks_path))}
    traces = [validate_trace(trace) for trace in _jsonl(Path(traces_path))]
    model = model or llm.client_config()["model"]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path = errors_path or output_path.with_suffix(".errors.jsonl")
    existing = _jsonl(output_path) if output_path.exists() else []
    done = {
        row.get("grade_id")
        for row in current_grades(existing, model)
        if row.get("grade_id") == grade_id_for(row, model)
    }
    jobs = []
    skipped = 0
    for trace in traces:
        task = tasks.get(trace["task_id"])
        if task is None:
            raise GradeError(f"no task found for trace {trace['trace_id']}")
        grade_id = grade_id_for(trace, model)
        if grade_id in done:
            skipped += 1
        else:
            jobs.append((task, trace, grade_id))
    counts = {"written": 0, "trainable": 0, "rejected": 0, "errors": 0, "skipped": skipped}

    def one(job):
        task, trace, _grade_id = job
        return judge_trace(
            task,
            trace,
            chat_fn=chat_fn,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_input_chars=max_input_chars,
        )

    with output_path.open("a", encoding="utf-8") as output, Path(errors_path).open(
        "a", encoding="utf-8"
    ) as errors, futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending = {pool.submit(one, job): job for job in jobs}
        for future in futures.as_completed(pending):
            task, trace, grade_id = pending[future]
            try:
                grade = future.result()
            except Exception as exc:
                errors.write(
                    json.dumps(
                        {
                            "grade_id": grade_id,
                            "trace_id": trace["trace_id"],
                            "task_id": task["task_id"],
                            "judge_model": model,
                            "error": f"{type(exc).__name__}: {exc}"[:1200],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                errors.flush()
                counts["errors"] += 1
                continue
            output.write(json.dumps(grade, ensure_ascii=False) + "\n")
            output.flush()
            counts["written"] += 1
            counts["trainable" if grade["trainable"] else "rejected"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--max-input-chars", type=int, default=160_000)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    result = run_grading(
        args.tasks,
        args.traces,
        args.out,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        max_input_chars=args.max_input_chars,
        concurrency=args.concurrency,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
