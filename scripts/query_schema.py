"""Dependency-free validation for the public SciCode problem record shape."""

from __future__ import annotations

import re
from typing import Any, Mapping


TOP_LEVEL_FIELDS = (
    "problem_name",
    "problem_id",
    "problem_description_main",
    "problem_io",
    "required_dependencies",
    "sub_steps",
    "general_tests",
    "problem_background_main",
)
STEP_FIELDS = (
    "step_number",
    "step_description_prompt",
    "function_header",
    "test_cases",
    "return_line",
    "step_background",
)
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*-[0-9]{6,}$")


class QuerySchemaError(ValueError):
    """Raised when a record cannot be consumed by the SciCode adapter."""


def _require_string(value: Any, path: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, str):
        raise QuerySchemaError(f"{path} must be a string")
    if nonempty and not value.strip():
        raise QuerySchemaError(f"{path} must not be empty")


def _require_string_list(value: Any, path: str) -> None:
    if not isinstance(value, list):
        raise QuerySchemaError(f"{path} must be a list")
    for index, item in enumerate(value):
        _require_string(item, f"{path}[{index}]")


def _require_exact_keys(value: Mapping[str, Any], expected: tuple[str, ...], path: str) -> None:
    actual = set(value)
    expected_set = set(expected)
    missing = sorted(expected_set - actual)
    extra = sorted(actual - expected_set)
    if missing:
        raise QuerySchemaError(f"{path} missing field(s): {', '.join(missing)}")
    if extra:
        raise QuerySchemaError(f"{path} has unexpected field(s): {', '.join(extra)}")


def validate_query(record: Mapping[str, Any]) -> None:
    """Validate one exact SciCode problem record.

    This is intentionally a transport check. It does not judge the science,
    difficulty, originality, numerical values, or whether an answer is right.
    """
    if not isinstance(record, Mapping):
        raise QuerySchemaError("query must be an object")
    _require_exact_keys(record, TOP_LEVEL_FIELDS, "query")

    _require_string(record["problem_name"], "problem_name", nonempty=True)
    problem_id = record["problem_id"]
    _require_string(problem_id, "problem_id", nonempty=True)
    if not ID_PATTERN.fullmatch(problem_id):
        raise QuerySchemaError("problem_id must match the stable generated-id format")
    _require_string(record["problem_description_main"], "problem_description_main", nonempty=True)
    _require_string(record["problem_io"], "problem_io")
    _require_string(record["required_dependencies"], "required_dependencies")
    _require_string(record["problem_background_main"], "problem_background_main")
    _require_string_list(record["general_tests"], "general_tests")

    steps = record["sub_steps"]
    if not isinstance(steps, list) or not steps:
        raise QuerySchemaError("sub_steps must be a non-empty list")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping):
            raise QuerySchemaError(f"sub_steps[{index - 1}] must be an object")
        _require_exact_keys(step, STEP_FIELDS, f"sub_steps[{index - 1}]")
        expected_number = f"{problem_id}.{index}"
        _require_string(step["step_number"], f"sub_steps[{index - 1}].step_number", nonempty=True)
        if step["step_number"] != expected_number:
            raise QuerySchemaError(
                f"sub_steps[{index - 1}].step_number must be {expected_number}"
            )
        _require_string(
            step["step_description_prompt"],
            f"sub_steps[{index - 1}].step_description_prompt",
            nonempty=True,
        )
        _require_string(
            step["function_header"],
            f"sub_steps[{index - 1}].function_header",
            nonempty=True,
        )
        _require_string_list(step["test_cases"], f"sub_steps[{index - 1}].test_cases")
        _require_string(step["return_line"], f"sub_steps[{index - 1}].return_line")
        _require_string(step["step_background"], f"sub_steps[{index - 1}].step_background")


__all__ = ["TOP_LEVEL_FIELDS", "STEP_FIELDS", "QuerySchemaError", "validate_query"]
