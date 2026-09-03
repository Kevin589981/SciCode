"""Provider-neutral trace recording for SciCode runs.

The upstream SciCode evaluator stores generated code and pass/fail results but
does not define a trajectory format.  This module adds a small append-only
event log without changing the upstream problem or scoring contracts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MODES = frozenset({"strict", "agentic"})
VISIBILITIES = frozenset({"public", "solver_internal", "private"})
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]*$")
_PRIVATE_KEYS = re.compile(
    r"(?:^|_)(?:answer|expected|gold|hidden|oracle|private|reference|target)(?:$|_)",
    re.IGNORECASE,
)


class TraceError(ValueError):
    """Raised when an event would violate the trace contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    """Validate and return a JSON-compatible value without lossy coercion."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TraceError("trace payload contains a non-finite float")
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise TraceError(f"trace payload is not JSON-compatible: {type(value)!r}")


def _has_private_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _PRIVATE_KEYS.search(str(key)):
                return True
            if _has_private_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_has_private_key(item) for item in value)
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TraceRecorder:
    """Append raw and normalized events for one Strict or Agentic run.

    ``root`` is the run directory.  Normalized events are written immediately
    so an interrupted provider call still leaves an auditable record.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        candidate_id: str,
        task_revision: str,
        mode: str,
        provider: str,
        model: str,
        sampling: Mapping[str, Any] | None = None,
        prompt_version: str = "unknown",
        environment_fingerprint: str | None = None,
    ) -> None:
        if mode not in MODES:
            raise TraceError(f"mode must be one of {sorted(MODES)}, got {mode!r}")
        for field_name, value in {
            "run_id": run_id,
            "candidate_id": candidate_id,
            "task_revision": task_revision,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
        }.items():
            if not value:
                raise TraceError(f"{field_name} must be non-empty")

        self.root = Path(root)
        self.raw_path = self.root / "raw" / "provider.jsonl"
        self.events_path = self.root / "trace" / "events.jsonl"
        self.manifest_path = self.root / "trace" / "manifest.json"
        self.raw_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._event_number = 0
        self._metadata = {
            "run_id": run_id,
            "candidate_id": candidate_id,
            "task_revision": task_revision,
            "mode": mode,
            "provider": provider,
            "model": model,
            "sampling": _json_value(dict(sampling or {})),
            "prompt_version": prompt_version,
            "environment_fingerprint": environment_fingerprint,
        }

    def record_raw(self, event: Mapping[str, Any]) -> None:
        """Append a provider-native event in its strict JSON representation."""

        if not isinstance(event, Mapping):
            raise TraceError("raw provider event must be a mapping")
        try:
            serialized = json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise TraceError(f"raw provider event is not strict JSON: {exc}") from exc
        with self.raw_path.open("a", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.write("\n")

    def record(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        visibility: str = "public",
        step_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Append one normalized event and return the serialized record."""

        if not _EVENT_TYPE.fullmatch(event_type):
            raise TraceError(f"invalid event type: {event_type!r}")
        if visibility not in VISIBILITIES:
            raise TraceError(
                f"visibility must be one of {sorted(VISIBILITIES)}, got {visibility!r}"
            )
        if not isinstance(payload, Mapping):
            raise TraceError("trace event payload must be a mapping")
        normalized_payload = _json_value(payload)
        if visibility == "public" and _has_private_key(normalized_payload):
            raise TraceError(
                "public trace payload contains a private/oracle-like field; "
                "record it as private or remove the field"
            )

        self._event_number += 1
        event = {
            "event_id": f"event-{self._event_number:06d}",
            "event_type": event_type,
            "timestamp": timestamp or _utc_now(),
            "step_id": step_id,
            "visibility": visibility,
            "payload": normalized_payload,
        }
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
            )
            stream.write("\n")
        return event

    def export_visible(self, destination: str | Path) -> int:
        """Copy only public events to a JSONL file and return its event count."""

        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with self.events_path.open("r", encoding="utf-8") as source, destination_path.open(
            "w", encoding="utf-8"
        ) as target:
            for line in source:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event["visibility"] != "public":
                    continue
                target.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                target.write("\n")
                count += 1
        return count

    def finalize(
        self,
        *,
        verification: Mapping[str, Any] | None = None,
        usage: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write and return the run manifest with artifact hashes."""

        manifest = {
            **self._metadata,
            "events_file": self.events_path.relative_to(self.root).as_posix(),
            "raw_file": self.raw_path.relative_to(self.root).as_posix(),
            "verification": _json_value(dict(verification or {})),
            "usage": _json_value(dict(usage or {})),
            "event_count": self._event_number,
            "artifacts": {
                "events_sha256": _sha256(self.events_path)
                if self.events_path.exists()
                else None,
                "raw_sha256": _sha256(self.raw_path) if self.raw_path.exists() else None,
            },
        }
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


__all__ = ["MODES", "VISIBILITIES", "TraceError", "TraceRecorder"]
