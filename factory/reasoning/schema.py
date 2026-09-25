"""Canonical schemas and deterministic validators for reasoning artifacts.

The factory intentionally keeps these validators dependency-free. LLM calls may
make semantic judgments, but malformed records and missing reasoning evidence are
rejected deterministically at every JSONL boundary.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

TASK_SCHEMA = "scicode-reasoning-task-v1"
TRACE_SCHEMA = "scicode-reasoning-trace-v1"
GRADE_SCHEMA = "scicode-reasoning-grade-v1"

ARCHETYPES = (
    "derive_implement",
    "diagnose_revise",
    "compare_justify",
)

SCORE_NAMES = (
    "scientific_validity",
    "causal_coherence",
    "strategy",
    "evidence_use",
    "self_correction",
    "insight_density",
    "degeneracy",
)


class SchemaError(ValueError):
    """Raised when a reasoning-factory artifact violates its contract."""


def canonical_hash(value: object) -> str:
    """Return a stable SHA-256 over a JSON-compatible value."""
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"value is not canonical JSON: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def _mapping(value: object, path: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{path} must be an object")
    return value


def _string(value: object, path: str, min_chars: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < min_chars:
        raise SchemaError(
            f"{path} must be a string with at least {min_chars} characters"
        )
    return value


def _string_list(
    value: object,
    path: str,
    *,
    minimum: int = 1,
    distinct: bool = False,
) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise SchemaError(f"{path} must contain at least {minimum} items")
    for index, item in enumerate(value):
        _string(item, f"{path}[{index}]")
    if distinct and len({item.strip().lower() for item in value}) != len(value):
        raise SchemaError(f"{path} items must be distinct")
    return value


def _public_list(value: object, path: str, *, minimum: int = 2) -> list:
    """Accept concise labels or structured public scientific givens."""
    if not isinstance(value, list) or len(value) < minimum:
        raise SchemaError(f"{path} must contain at least {minimum} items")
    for index, item in enumerate(value):
        if isinstance(item, str):
            _string(item, f"{path}[{index}]")
        elif isinstance(item, Mapping) and item:
            canonical_hash(item)
        else:
            raise SchemaError(f"{path}[{index}] must be a nonempty string or object")
    return value


def validate_task(task: object) -> dict:
    """Validate and return a reasoning task without altering it."""
    task = _mapping(task, "task")
    if task.get("schema_version") != TASK_SCHEMA:
        raise SchemaError(f"schema_version must be {TASK_SCHEMA}")
    _string(task.get("task_id"), "task_id")
    archetype = task.get("archetype")
    if archetype not in ARCHETYPES:
        raise SchemaError(f"archetype must be one of {ARCHETYPES}")

    source = _mapping(task.get("source"), "source")
    for name in ("repo", "commit", "file", "symbol", "source"):
        _string(source.get(name), f"source.{name}")

    problem = _mapping(task.get("problem"), "problem")
    _string(problem.get("question"), "problem.question", min_chars=120)
    _string(problem.get("background"), "problem.background", min_chars=60)

    deliverable = _mapping(task.get("deliverable"), "deliverable")
    if deliverable.get("kind") not in {
        "analysis",
        "analysis_and_code",
        "analysis_and_experiment",
    }:
        raise SchemaError("deliverable.kind is unsupported")
    _string_list(deliverable.get("requirements"), "deliverable.requirements", minimum=2)

    contract = _mapping(task.get("reasoning_contract"), "reasoning_contract")
    _string_list(
        contract.get("cognitive_operations"),
        "reasoning_contract.cognitive_operations",
        minimum=3,
        distinct=True,
    )
    _string_list(
        contract.get("scientific_concepts"),
        "reasoning_contract.scientific_concepts",
        minimum=2,
        distinct=True,
    )
    _string_list(
        contract.get("evidence_expected"),
        "reasoning_contract.evidence_expected",
        minimum=2,
    )
    _string_list(contract.get("failure_modes"), "reasoning_contract.failure_modes")
    _string_list(
        contract.get("forbidden_shortcuts"),
        "reasoning_contract.forbidden_shortcuts",
    )

    payload = _mapping(task.get("archetype_payload"), "archetype_payload")
    if archetype == "derive_implement":
        _public_list(
            payload.get("assumptions"), "archetype_payload.assumptions", minimum=2
        )
        _string(payload.get("derivation_target"), "archetype_payload.derivation_target")
    elif archetype == "diagnose_revise":
        _public_list(
            payload.get("observations"), "archetype_payload.observations", minimum=2
        )
        _public_list(
            payload.get("candidate_causes"),
            "archetype_payload.candidate_causes",
            minimum=2,
        )
    else:
        _public_list(
            payload.get("alternatives"), "archetype_payload.alternatives", minimum=2
        )
        _public_list(
            payload.get("decision_criteria"),
            "archetype_payload.decision_criteria",
            minimum=2,
        )
    return dict(task)


def validate_trace(trace: object) -> dict:
    """Validate a raw trace and require at least one native thinking message."""
    trace = _mapping(trace, "trace")
    if trace.get("schema_version") != TRACE_SCHEMA:
        raise SchemaError(f"schema_version must be {TRACE_SCHEMA}")
    for name in ("trace_id", "task_id", "task_hash", "model"):
        _string(trace.get(name), name)
    max_tokens = trace.get("max_tokens")
    if (
        not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or max_tokens < 1
    ):
        raise SchemaError("max_tokens must be a positive integer")
    if "finish_reason" in trace:
        _string(trace.get("finish_reason"), "finish_reason")
    if "truncated" in trace and not isinstance(trace.get("truncated"), bool):
        raise SchemaError("truncated must be a boolean")
    messages = trace.get("messages")
    if not isinstance(messages, list) or not messages:
        raise SchemaError("messages must be a nonempty list")
    assistant_indices = []
    has_reasoning = False
    for index, message in enumerate(messages):
        message = _mapping(message, f"messages[{index}]")
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool", "tool_response"}:
            raise SchemaError(f"messages[{index}].role is unsupported")
        content = message.get("content", "")
        if not isinstance(content, str):
            raise SchemaError(f"messages[{index}].content must be a string")
        if role == "assistant":
            assistant_indices.append(index)
            reasoning = message.get("reasoning_content", "")
            if not isinstance(reasoning, str):
                raise SchemaError(
                    f"messages[{index}].reasoning_content must be a string"
                )
            has_reasoning = has_reasoning or bool(reasoning.strip())
    if not assistant_indices:
        raise SchemaError("messages must contain an assistant message")
    if not has_reasoning:
        raise SchemaError("an assistant reasoning_content value is required")
    if "outcome" in trace and trace["outcome"] is not None:
        _mapping(trace["outcome"], "outcome")
    return dict(trace)


def validate_grade(grade: object, trace: object | None = None) -> dict:
    """Validate a trace grade and, when provided, its message annotations."""
    grade = _mapping(grade, "grade")
    if grade.get("schema_version") != GRADE_SCHEMA:
        raise SchemaError(f"schema_version must be {GRADE_SCHEMA}")
    _string(grade.get("trace_id"), "trace_id")
    scores = _mapping(grade.get("scores"), "scores")
    for name in SCORE_NAMES:
        value = scores.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 4:
            raise SchemaError(f"scores.{name} must be an integer from 0 to 4")
    if not isinstance(grade.get("trainable"), bool):
        raise SchemaError("trainable must be a boolean")
    _string(grade.get("rationale"), "rationale")

    annotations = grade.get("message_annotations")
    if not isinstance(annotations, list) or not annotations:
        raise SchemaError("message_annotations must be a nonempty list")
    annotation_indices = []
    for index, annotation in enumerate(annotations):
        annotation = _mapping(annotation, f"message_annotations[{index}]")
        message_index = annotation.get("message_index")
        if not isinstance(message_index, int) or isinstance(message_index, bool):
            raise SchemaError(
                f"message_annotations[{index}].message_index must be an integer"
            )
        annotation_indices.append(message_index)
        for name in ("train_reasoning", "train_content"):
            if not isinstance(annotation.get(name), bool):
                raise SchemaError(
                    f"message_annotations[{index}].{name} must be a boolean"
                )
        if annotation.get("quality") not in {"good", "medium", "bad"}:
            raise SchemaError(f"message_annotations[{index}].quality is unsupported")
        _string(annotation.get("rationale"), f"message_annotations[{index}].rationale")
    if len(set(annotation_indices)) != len(annotation_indices):
        raise SchemaError("message_annotations message_index values must be unique")

    if trace is not None:
        trace = validate_trace(trace)
        if grade["trace_id"] != trace["trace_id"]:
            raise SchemaError("grade trace_id does not match trace")
        assistant_indices = [
            index
            for index, message in enumerate(trace["messages"])
            if message["role"] == "assistant"
        ]
        if sorted(annotation_indices) != assistant_indices:
            raise SchemaError("message_annotations must cover every assistant message")
    return dict(grade)
