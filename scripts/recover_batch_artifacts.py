#!/usr/bin/env python3
"""Export every complete trace still present in a stopped batch."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from scicode.pipeline.samples import export_subproblem_samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=1_000_000)
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding="utf-8"))
    root = args.output_root
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.state, root / "state.snapshot.json")
    output = root / "available-traces.jsonl"
    registry = root / "available-traces.registry.jsonl"
    for path in (output, registry):
        if path.exists(): path.unlink()
    exported, skipped, errors = [], [], []
    for job in state.get("jobs", []):
        candidate = Path(job["candidate_dir"])
        if not candidate.is_dir():
            skipped.append({"candidate_id": job["candidate_id"], "reason": "worktree_missing"})
            continue
        manifests = []
        for manifest in candidate.glob("runs/*/manifest.json"):
            try: value = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception: continue
            rollouts = manifest.parent / "rollouts.jsonl"
            if value.get("status") == "finished" and value.get("trace_complete") is True and rollouts.is_file() and rollouts.stat().st_size:
                manifests.append((manifest.stat().st_mtime_ns, manifest, rollouts))
        if not manifests:
            skipped.append({"candidate_id": job["candidate_id"], "reason": "no_complete_trace"})
            continue
        _, manifest, rollouts = max(manifests, key=lambda item: item[0])
        try:
            result = export_subproblem_samples(candidate, rollouts, registry, output, target_count=args.target_count)
            exported.append({"candidate_id": job["candidate_id"], "run": manifest.parent.name, "new_samples": result.get("new_samples", 0), "invalid_rollouts": result.get("invalid_rollouts", [])})
        except Exception as exc:
            errors.append({"candidate_id": job["candidate_id"], "error_type": type(exc).__name__, "error": str(exc)})
    report = {"schema": "scicode-batch-recovery-export-v1", "batch_id": state.get("batch_id"), "state_updated_at": state.get("updated_at"), "candidate_count": len(state.get("jobs", [])), "exported_candidate_count": len(exported), "skipped_count": len(skipped), "error_count": len(errors), "available_sample_count": sum(int(x.get("new_samples", 0)) for x in exported), "output": str(output), "registry": str(registry), "stage_counts": dict(Counter(j.get("stage") for j in state.get("jobs", []))), "exported": exported, "skipped": skipped, "errors": errors}
    (root / "export-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
