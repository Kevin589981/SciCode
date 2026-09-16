#!/usr/bin/env python3
"""Recover complete subproblem traces from a stopped multiprocess batch."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from scicode.pipeline.samples import export_subproblem_samples


def _state_paths(batch_root: Path) -> list[Path]:
    return sorted(
        batch_root.glob("workers/worker-*/loop-*/batches/*/state.json"),
        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=1_000_000)
    args = parser.parse_args()

    batch_root = args.batch_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / "available-traces.jsonl"
    registry = output_root / "available-traces.registry.jsonl"
    for path in (output, registry):
        if path.exists():
            path.unlink()

    exported: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    seen_candidates: set[str] = set()
    stage_counts: Counter[str] = Counter()
    batch_ids: set[str] = set()

    for state_path in _state_paths(batch_root):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append({"state": str(state_path), "error_type": type(exc).__name__, "error": str(exc)})
            continue
        batch_ids.add(str(state.get("batch_id", state_path.parent.name)))
        for job in state.get("jobs", []):
            candidate_id = str(job.get("candidate_id", ""))
            if not candidate_id or candidate_id in seen_candidates:
                continue
            seen_candidates.add(candidate_id)
            stage_counts[str(job.get("stage", "unknown"))] += 1
            candidate = Path(str(job.get("candidate_dir", "")))
            if not candidate.is_dir():
                skipped.append({"candidate_id": candidate_id, "reason": "worktree_missing"})
                continue
            manifests: list[tuple[int, Path, Path]] = []
            for manifest in candidate.glob("runs/*/manifest.json"):
                try:
                    value = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                rollouts = manifest.parent / "rollouts.jsonl"
                if (
                    value.get("status") == "finished"
                    and value.get("trace_complete") is True
                    and rollouts.is_file()
                    and rollouts.stat().st_size
                ):
                    manifests.append((manifest.stat().st_mtime_ns, manifest, rollouts))
            if not manifests:
                skipped.append({"candidate_id": candidate_id, "reason": "no_complete_trace"})
                continue
            _, manifest, rollouts = max(manifests, key=lambda item: item[0])
            try:
                result = export_subproblem_samples(
                    candidate,
                    rollouts,
                    registry,
                    output,
                    target_count=args.target_count,
                )
                exported.append(
                    {
                        "candidate_id": candidate_id,
                        "run": manifest.parent.name,
                        "new_samples": int(result.get("new_samples", 0)),
                        "invalid_rollouts": result.get("invalid_rollouts", []),
                    }
                )
            except Exception as exc:  # keep independent candidates recoverable
                errors.append(
                    {
                        "candidate_id": candidate_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )

    report = {
        "schema": "scicode-multiprocess-recovery-export-v1",
        "batch_ids": sorted(batch_ids),
        "candidate_count": len(seen_candidates),
        "exported_candidate_count": len(exported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "available_sample_count": sum(int(item["new_samples"]) for item in exported),
        "stage_counts": dict(stage_counts),
        "output": str(output),
        "registry": str(registry),
        "exported": exported,
        "skipped": skipped,
        "errors": errors,
    }
    (output_root / "export-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
