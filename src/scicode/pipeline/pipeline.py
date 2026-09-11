"""State machine for one Kimi Code authoring-to-delivery candidate."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .candidate import CandidateValidationError, load_candidate
from .checks import run_candidate_checks
from .samples import SampleExportError, export_subproblem_samples


PIPELINE_STATES = (
    "validated",
    "reviewed",
    "ready_for_run",
    "run_complete",
    "accepted",
    "rejected",
)


class PipelineError(RuntimeError):
    """Raised when a state transition would violate the candidate contract."""


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise PipelineError(f"invalid or missing JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"JSON artifact must be an object: {path}")
    return value


def _review_passed(path: Path) -> bool:
    if not path.is_file():
        return False
    value = _read_json(path)
    decision = str(value.get("decision", value.get("status", ""))).lower()
    return decision in {"pass", "passed", "approved", "accepted"}


def run_candidate_pipeline(
    candidate_dir: str | Path,
    delivery_root: str | Path,
    *,
    rollouts: str | Path | None = None,
    run_manifest: str | Path | None = None,
    target_count: int = 10_000,
    require_review: bool = True,
    allow_unreviewed: bool = False,
    allow_qa_export: bool = False,
) -> dict[str, Any]:
    """Advance a candidate through validated/reviewed/run/delivery states.

    Kimi Code and the AvaCore skill remain separate processes. This function
    only validates their handoff artifacts and performs deterministic delivery.
    """
    root = Path(candidate_dir).expanduser().resolve()
    delivery = Path(delivery_root).expanduser().resolve()
    manifest = load_candidate(root)
    checks = run_candidate_checks(root)
    if checks["status"] != "ok":
        raise PipelineError("candidate deterministic checks failed")
    state = "validated"
    review_path = root / "validation" / "review_child.json"
    warnings: list[str] = []
    if _review_passed(review_path):
        state = "reviewed"
    elif require_review and not allow_unreviewed:
        raise PipelineError(f"review child approval is required: {review_path}")
    else:
        warnings.append("review child approval is absent; run is marked QA-only")
    state = "ready_for_run"
    result: dict[str, Any] = {
        "schema": "scicode-pipeline-v1",
        "candidate_id": manifest.candidate_id,
        "revision": manifest.revision,
        "state": state,
        "mode": "strict",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_manifest": manifest.as_dict(),
        "checks": checks,
        "warnings": warnings,
    }
    if rollouts is not None:
        run_is_promotable = True
        if run_manifest is not None:
            run_value = _read_json(Path(run_manifest))
            candidate_value = run_value.get("candidate")
            if isinstance(candidate_value, Mapping):
                if candidate_value.get("visible_contract_sha256") != manifest.visible_contract_sha256:
                    raise PipelineError("run manifest candidate hash does not match candidate")
            if run_value.get("mode") not in (None, "strict"):
                raise PipelineError("only strict runs can enter delivery")
            if run_value.get("promotable") is not True:
                warnings.append("AvaCore run is not promotable; samples remain QA-only")
                run_is_promotable = False
        release = root / "validation" / "release_decision.json"
        release_passed = _review_passed(release)
        if (not run_is_promotable or not release_passed) and not allow_qa_export:
            state = "run_complete"
            result.update(
                {
                    "state": state,
                    "release_decision": str(release),
                    "delivery": "deferred_until_release_gate",
                }
            )
            state_path = root / "validation" / "pipeline_manifest.json"
            _write_json(state_path, result)
            result["pipeline_manifest"] = str(state_path)
            return result
        state = "run_complete"
        registry = delivery / "registry.jsonl"
        output = delivery / "dataset.jsonl"
        summary = delivery / "summary.json"
        try:
            export_result = export_subproblem_samples(
                root,
                rollouts,
                registry,
                output,
                target_count=target_count,
                summary=summary,
            )
        except (SampleExportError, OSError) as exc:
            raise PipelineError(str(exc)) from exc
        result["export"] = export_result
        result["state"] = state
        # Delivery acceptance is intentionally separate from trace completion.
        # Kimi Code must write a release decision after reviewing the trace.
        if release_passed and run_is_promotable and export_result["new_samples"] >= 0:
            state = "accepted"
            result["state"] = state
    state_path = root / "validation" / "pipeline_manifest.json"
    _write_json(state_path, result)
    result["pipeline_manifest"] = str(state_path)
    return result
