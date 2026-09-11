"""Auditable run metadata shared by the AvaCore runner and delivery layer."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .candidate import CandidateManifest, sha256_file


def safe_endpoint(value: str) -> str:
    """Keep endpoint identity while removing credentials and query material."""
    parsed = urlsplit(str(value))
    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary.write_bytes(encoded)
    os.replace(temporary, destination)
    return sha256_file(destination)


def build_run_manifest(
    *,
    run_id: str,
    status: str,
    config: Mapping[str, Any],
    candidate: CandidateManifest | None = None,
    expected_problems: int = 0,
    expected_subproblems: int = 0,
    completed_problems: int = 0,
    completed_subproblems: int = 0,
    errors: int = 0,
    trace_export: str | Path | None = None,
    usage: Mapping[str, Any] | None = None,
    retries: int = 0,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "scicode-avacore-run-v2",
        "run_id": run_id,
        "status": status,
        "mode": "strict",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            key: (safe_endpoint(value) if key in {"base_url", "endpoint"} else value)
            for key, value in config.items()
            if key not in {"api_key", "openai_api_key", "authorization", "postgres"}
        },
        "expected_problems": expected_problems,
        "expected_subproblems": expected_subproblems,
        "completed_problems": completed_problems,
        "completed_subproblems": completed_subproblems,
        "errors": errors,
        "retries": retries,
        "trace_complete": (
            errors == 0
            and completed_problems == expected_problems
            and completed_subproblems == expected_subproblems
        ),
        "usage": dict(usage or {}),
        "notes": list(notes or []),
    }
    if candidate is not None:
        payload["candidate"] = candidate.as_dict()
    if trace_export is not None:
        path = Path(trace_export)
        payload["trace_export"] = {
            "path": str(path.resolve()),
            "sha256": sha256_file(path) if path.is_file() else None,
        }
    payload["promotable"] = bool(
        payload["trace_complete"]
        and payload.get("candidate")
        and payload.get("trace_export", {}).get("sha256")
    )
    return payload


def is_promotable(manifest: Mapping[str, Any]) -> bool:
    return bool(manifest.get("promotable"))
