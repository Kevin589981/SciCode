"""Dependency-free SciCode trace text normalization and code extraction."""

from __future__ import annotations

import re
from typing import Any, Mapping


FENCE = re.compile(
    r"\x60\x60\x60(?:python|py)?[ \t]*\r?\n?(.*?)\x60\x60\x60",
    re.IGNORECASE | re.DOTALL,
)
THINK = re.compile(r"<think>\s*(.*?)\s*</think>\s*", re.IGNORECASE | re.DOTALL)
IMPORT_LINE = re.compile(
    r"^\s*(?:import\s+.+|from\s+.+\s+import\s+.+)\s*(?:\r?\n|$)",
    re.MULTILINE,
)


def normalize_content(value: Any) -> str:
    """Convert OpenAI/AvaCore text blocks to the SciCode string form."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                for key in ("text", "content", "value"):
                    if isinstance(item.get(key), str):
                        parts.append(item[key])
                        break
        return "".join(parts)
    if value is None:
        return ""
    return str(value)


def split_reasoning(content: Any, reasoning: Any = "") -> tuple[str, str]:
    """Return ordinary content and reasoning using the exporter convention."""
    ordinary = normalize_content(content)
    thought = normalize_content(reasoning)
    if not thought:
        match = THINK.search(ordinary)
        if match:
            thought = match.group(1).strip()
            ordinary = ordinary[match.end() :]
    return ordinary, thought


def extract_code(content: str) -> str:
    """Extract code exactly as the SciCode sample exporter does."""
    if not content:
        return ""
    match = FENCE.search(content)
    code = match.group(1) if match else content
    while True:
        updated = IMPORT_LINE.sub("", code, count=1)
        if updated == code:
            break
        code = updated
    return code.strip()


__all__ = [
    "FENCE",
    "IMPORT_LINE",
    "THINK",
    "extract_code",
    "normalize_content",
    "split_reasoning",
]
