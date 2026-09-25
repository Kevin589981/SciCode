"""Source-grounded scientific validity verification for authored tasks."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .author import _json_objects
from .schema import SchemaError, canonical_hash, validate_task
from .student_view import render_student_user, student_view_hash

VERIFICATION_SCHEMA = "scicode-task-verification-v1"
VERIFICATION_POLICY = "source-grounding-v2"
SCORE_NAMES = (
    "scientific_validity",
    "source_grounding",
    "answerability",
    "constraint_consistency",
    "shortcut_resistance",
)


class VerificationError(ValueError):
    """A task or verifier response is invalid."""


def verification_id_for(task: dict, model: str) -> str:
    return canonical_hash(
        {
            "task_hash": canonical_hash(validate_task(task)),
            "verifier_model": model,
            "policy": VERIFICATION_POLICY,
        }
    )


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _grounded_evidence(evidence: list[dict], source: str) -> list[dict]:
    normalized_source = _normalized(source)
    grounded = []
    seen = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise VerificationError(f"evidence[{index}] must be an object")
        claim = item.get("claim")
        quote = item.get("source_quote")
        assessment = item.get("assessment")
        if not isinstance(claim, str) or not claim.strip():
            raise VerificationError(f"evidence[{index}].claim must be nonempty")
        if not isinstance(quote, str) or len(_normalized(quote)) < 8:
            raise VerificationError(
                f"evidence[{index}].source_quote must contain at least 8 characters"
            )
        if assessment not in {"supports", "contradicts", "unclear"}:
            raise VerificationError(f"evidence[{index}].assessment is unsupported")
        normalized_quote = _normalized(quote)
        actually_grounded = normalized_quote in normalized_source
        row = {
            "claim": claim,
            "source_quote": quote,
            "assessment": assessment,
            "quote_grounded": actually_grounded,
        }
        grounded.append(row)
        if actually_grounded and assessment == "supports":
            seen.add(normalized_quote)
    if len(seen) < 2:
        raise VerificationError(
            "verifier must provide two distinct supporting quotes copied from source"
        )
    return grounded


def _verification_prompt(task: dict) -> str:
    return f"""You are an independent scientific task verifier, not a solver.

Check whether the authored problem is scientifically defensible, answerable from
its stated background plus standard scientific knowledge, internally
consistent, grounded in the private source, and resistant to a shallow shortcut.
The source is verification evidence and is not shown to the eventual solver.

For every evidence item, copy an exact nontrivial quote from `source.source`.
Do not invent line numbers or paraphrase the quote. A deterministic checker will
reject quotes that do not occur in the source.

STUDENT-VISIBLE PROMPT (check answerability and shortcuts from this alone):
{render_student_user(task)}

PRIVATE SOURCE (grounding evidence only; it cannot repair missing student inputs):
{json.dumps(task['source'], ensure_ascii=False, indent=2)}

Return ONLY one JSON object:
{{
  "scores": {{
    "scientific_validity": 0,
    "source_grounding": 0,
    "answerability": 0,
    "constraint_consistency": 0,
    "shortcut_resistance": 0
  }},
  "fatal_issues": ["empty unless the task is unusable"],
  "missing_inputs": [],
  "answer_exposed": false,
  "evidence": [
    {{
      "claim": "task claim or requirement being checked",
      "source_quote": "exact quote copied from source.source",
      "assessment": "supports" | "contradicts" | "unclear"
    }}
  ],
  "rationale": "specific scientific justification"
}}

Scores are integers 0..4. Provide at least two distinct supporting evidence
quotes. A task with a false premise, missing information, contradictory
requirements, or an answer exposed by a mechanical shortcut must name that in
fatal_issues.
"""


def verify_task(
    task: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: int = 1200,
) -> dict:
    """Verify one task and derive admission from scores plus exact source quotes."""
    task = validate_task(task)
    model = model or llm.client_config()["model"]
    response = chat_fn(
        [{"role": "user", "content": _verification_prompt(task)}],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VerificationError(
            f"verifier response has no usable message: {exc}"
        ) from exc
    objects = []
    for field in ("content", "reasoning_content"):
        objects.extend(_json_objects(message.get(field) or ""))
    value = next(
        (
            item
            for item in objects
            if isinstance(item.get("scores"), dict)
            and isinstance(item.get("evidence"), list)
        ),
        None,
    )
    if value is None:
        raise VerificationError("verifier response has no complete result object")
    scores = value["scores"]
    for name in SCORE_NAMES:
        score = scores.get(name)
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 4:
            raise VerificationError(f"scores.{name} must be an integer from 0 to 4")
    fatal_issues = value.get("fatal_issues")
    if not isinstance(fatal_issues, list) or any(
        not isinstance(item, str) for item in fatal_issues
    ):
        raise VerificationError("fatal_issues must be a string list")
    missing_inputs = value.get("missing_inputs")
    answer_exposed = value.get("answer_exposed")
    if not isinstance(missing_inputs, list) or any(
        not isinstance(item, str) for item in missing_inputs
    ):
        raise VerificationError("missing_inputs must be a string list")
    if not isinstance(answer_exposed, bool):
        raise VerificationError("answer_exposed must be a boolean")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise VerificationError("rationale must be nonempty")
    evidence = _grounded_evidence(value["evidence"], task["source"]["source"])
    clean_fatal = [item.strip() for item in fatal_issues if item.strip()]
    accepted = (
        not clean_fatal
        and not missing_inputs
        and not answer_exposed
        and scores["scientific_validity"] >= 3
        and scores["source_grounding"] >= 3
        and scores["answerability"] >= 3
        and scores["constraint_consistency"] >= 3
        and scores["shortcut_resistance"] >= 2
    )
    record = {
        "schema_version": VERIFICATION_SCHEMA,
        "verification_id": verification_id_for(task, model),
        "task_id": task["task_id"],
        "task_hash": canonical_hash(task),
        "accepted": accepted,
        "policy_version": VERIFICATION_POLICY,
        "student_view_hash": student_view_hash(task),
        "scores": {name: scores[name] for name in SCORE_NAMES},
        "fatal_issues": clean_fatal,
        "missing_inputs": missing_inputs,
        "answer_exposed": answer_exposed,
        "evidence": evidence,
        "rationale": rationale,
        "verifier": {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": response.get("usage") or {},
        },
    }
    return record


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise VerificationError(
                    f"{path}:{number}: invalid JSON: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise VerificationError(f"{path}:{number}: row must be an object")
            rows.append(row)
    return rows


def run_verification(
    tasks_path: Path,
    output_path: Path,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: int = 1200,
    concurrency: int = 1,
    errors_path: Path | None = None,
) -> dict:
    """Verify task JSONL with verifier-aware exact resume keys."""
    if concurrency < 1:
        raise VerificationError("concurrency must be positive")
    tasks = [validate_task(row) for row in _jsonl(Path(tasks_path))]
    model = model or llm.client_config()["model"]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path = errors_path or output_path.with_suffix(".errors.jsonl")
    existing = _jsonl(output_path) if output_path.exists() else []
    done = {row.get("verification_id") for row in existing}
    jobs = []
    skipped = 0
    for task in tasks:
        verification_id = verification_id_for(task, model)
        if verification_id in done:
            skipped += 1
        else:
            jobs.append((task, verification_id))
    counts = {"accepted": 0, "rejected": 0, "errors": 0, "skipped": skipped}

    def one(job):
        task, _verification_id = job
        return verify_task(
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
            task, verification_id = pending[future]
            try:
                record = future.result()
            except (VerificationError, SchemaError, KeyError, TypeError) as exc:
                errors.write(
                    json.dumps(
                        {
                            "verification_id": verification_id,
                            "task_id": task.get("task_id"),
                            "task_hash": canonical_hash(task),
                            "verifier_model": model,
                            "error": f"{type(exc).__name__}: {exc}"[:1200],
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
            counts["accepted" if record["accepted"] else "rejected"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    result = run_verification(
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
