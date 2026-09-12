#!/usr/bin/env python3
"""Check that the operational agent standard remains imperative and complete."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_SECTIONS = (
    "## 1. Keep the roles separate",
    "## 2. Start a candidate session",
    "## 4. Preserve the original SciCode contract",
    "## 7. Hand the candidate to AvaCore",
    "## 8. Treat reasoning and usage as trace data",
    "## 10. Accept or hand off the candidate",
)
MODAL = re.compile(r"\b(?:should|could|might|would|may|can|will)\b", re.IGNORECASE)


def lint(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors = [f"missing required section: {section}" for section in REQUIRED_SECTIONS if section not in text]
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if MODAL.search(line):
            errors.append(f"modal wording at line {line_number}: {line.strip()}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, default=Path("AGENTS.md"), nargs="?")
    args = parser.parse_args()
    errors = lint(args.path)
    result = {"status": "ok" if not errors else "rejected", "path": str(args.path), "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
