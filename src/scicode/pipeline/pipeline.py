"""State machine for one Kimi Code authoring-to-delivery candidate."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .candidate import CandidateValidationError, load_candidate, sha256_file
from .checks import run_candidate_checks
from .samples import SampleExportError, export_subproblem_samples
from .trace_checks import audit_rollouts


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
    # Child reviews use the explicit handoff decision
    # ``accepted_for_avacore_run``; release records use ``accepted``.
    return decision in {
        "pass",
        "passed",
        "approved",
        "accepted",
        "accepted_for_avacore_run",
        "accepted_for_delivery",
    }


def _validate_run_artifacts(
    root: Path,
    candidate: Any,
    rollouts: Path,
    run_manifest: Path | None,
    *,
    allow_qa_export: bool,
) -> dict[str, Any] | None:
    """Bind one trace to the exact candidate revision that produced it."""
    if run_manifest is None:
        if not allow_qa_export:
            raise PipelineError("a finished strict run manifest is required for delivery")
        return None
    manifest_path = run_manifest.expanduser().resolve()
    run_value = _read_json(manifest_path)
    run_id = run_value.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip() or Path(run_id).name != run_id:
        raise PipelineError("run manifest has no safe run_id")
    run_dir = (root / "runs" / run_id).resolve()
    if manifest_path.parent != run_dir:
        raise PipelineError("run manifest is outside the candidate run directory")
    rollouts_path = rollouts.expanduser().resolve()
    if not rollouts_path.is_relative_to(run_dir):
        raise PipelineError("rollout export is outside the run directory")
    run_candidate = run_value.get("candidate")
    if not isinstance(run_candidate, Mapping):
        raise PipelineError("run manifest has no candidate identity")
    identity = {
        "candidate_id": candidate.candidate_id,
        "revision": candidate.revision,
        "canonical_record_sha256": candidate.canonical_record_sha256,
        "visible_contract_sha256": candidate.visible_contract_sha256,
        "solver_payload_sha256": candidate.solver_payload_sha256,
        "oracle_sha256": candidate.oracle_sha256,
        "provenance_sha256": candidate.provenance_sha256,
    }
    for field, expected in identity.items():
        if run_candidate.get(field) != expected:
            raise PipelineError(f"run manifest candidate {field} does not match candidate")
    recorded_root = run_candidate.get("root")
    if recorded_root and Path(str(recorded_root)).expanduser().resolve() != root:
        raise PipelineError("run manifest candidate root does not match candidate")
    if run_value.get("mode") != "strict":
        raise PipelineError("only strict runs can enter delivery")
    status = run_value.get("status")
    if not allow_qa_export and status != "finished":
        raise PipelineError("run manifest is not finished")
    if not allow_qa_export and run_value.get("trace_complete") is not True:
        raise PipelineError("run manifest trace is incomplete")
    trace_export = run_value.get("trace_export")
    if isinstance(trace_export, Mapping):
        recorded_path = trace_export.get("path")
        if recorded_path and Path(str(recorded_path)).expanduser().resolve() != rollouts_path:
            raise PipelineError("run manifest export path does not match rollouts")
        recorded_hash = trace_export.get("sha256")
        if recorded_hash:
            if not rollouts_path.is_file() or sha256_file(rollouts_path) != recorded_hash:
                raise PipelineError("rollout export hash does not match run manifest")
        elif not allow_qa_export:
            raise PipelineError("run manifest has no export hash")
    elif not allow_qa_export:
        raise PipelineError("run manifest has no trace export metadata")
    return run_value


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
    run_value: dict[str, Any] | None = None
    if rollouts is not None:
        rollouts_path = Path(rollouts).expanduser().resolve()
        run_value = _validate_run_artifacts(
            root,
            manifest,
            rollouts_path,
            Path(run_manifest) if run_manifest is not None else None,
            allow_qa_export=allow_qa_export,
        )
        trace_audit = audit_rollouts(
            manifest,
            rollouts_path,
            require_usage=not allow_qa_export,
            require_reasoning=not allow_qa_export,
        )
        result["trace_audit"] = trace_audit
        _write_json(root / "validation" / "trace_report.json", trace_audit)
        if trace_audit["status"] != "ok" and not allow_qa_export:
            raise PipelineError("exported trace completeness checks failed")
        if trace_audit["status"] != "ok":
            warnings.append("trace completeness checks failed; samples remain QA-only")
        run_is_promotable = True
        if run_value is not None:
            if run_value.get("promotable") is not True:
                warnings.append("AvaCore run is not promotable; samples remain QA-only")
                run_is_promotable = False
        release = root / "validation" / "release_decision.json"
        release_passed = _review_passed(release)
        if release_passed and run_value is not None:
            release_value = _read_json(release)
            if release_value.get("run_id") != run_value.get("run_id"):
                message = "release decision run_id does not match the run manifest"
                if not allow_qa_export:
                    raise PipelineError(message)
                warnings.append(message + "; samples remain QA-only")
                release_passed = False
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
                rollouts_path,
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
