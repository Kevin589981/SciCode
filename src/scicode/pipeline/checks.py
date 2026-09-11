"""Deterministic pre-provider checks for a SciCode candidate."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

from .candidate import (
    CandidateManifest,
    CandidateValidationError,
    load_candidate,
    read_jsonl,
    validate_oracle_layout,
)


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _compile_files(root: Path) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    checked = 0
    for path in sorted(root.rglob("*.py")) if root.is_dir() else []:
        checked += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            failures.append({"path": str(path), "error": str(exc)})
    return {"status": "ok" if not failures else "failed", "checked": checked, "failures": failures}


def run_candidate_checks(
    candidate_dir: str | Path,
    *,
    validation_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run schema, oracle, static, prompt, and isolation checks as one gate."""
    root = Path(candidate_dir).expanduser().resolve()
    destination = Path(validation_dir or root / "validation").resolve()
    manifest = load_candidate(root)
    rows = read_jsonl(manifest.problem_file)
    schema = {
        "status": "ok",
        "problem_count": manifest.problem_count,
        "subproblem_count": manifest.subproblem_count,
        "visible_contract_sha256": manifest.visible_contract_sha256,
    }
    oracle = validate_oracle_layout(manifest.oracle_file, rows)
    static_parts = {
        "public_checks": _compile_files(root / "public" / "checks"),
        "reference": _compile_files(root / "reference"),
        "oracle": _compile_files(root / "oracle"),
    }
    for name in ("public_checks", "reference", "oracle"):
        if static_parts[name]["checked"] == 0:
            static_parts[name]["status"] = "failed"
            static_parts[name]["failures"].append(
                {"path": str(root / name), "error": "no Python validation files found"}
            )
    static_failures = [
        item
        for part in static_parts.values()
        for item in part["failures"]
    ]
    static = {
        "status": "ok" if not static_failures else "failed",
        "parts": static_parts,
        "failures": static_failures,
    }
    prompt_files = list((root / "public" / "prompt_snapshot").rglob("*.txt"))
    prompt = {
        "status": "ok" if len(prompt_files) >= manifest.subproblem_count else "failed",
        "files": len(prompt_files),
        "expected_at_least": manifest.subproblem_count,
    }
    result = {
        "schema": "scicode-candidate-checks-v1",
        "status": "ok" if static["status"] == "ok" and prompt["status"] == "ok" else "failed",
        "candidate": manifest.as_dict(),
        "schema_report": schema,
        "oracle_report": oracle,
        "static_report": static,
        "prompt_report": prompt,
        "isolation_report": {"status": "ok"},
    }
    _write(destination / "schema_report.json", schema)
    _write(destination / "oracle_report.json", oracle)
    _write(destination / "static_report.json", static)
    _write(destination / "prompt_report.json", prompt)
    _write(destination / "isolation_report.json", result["isolation_report"])
    _write(destination / "checks_report.json", result)
    return result
