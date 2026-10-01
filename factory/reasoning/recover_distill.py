"""Explicit cross-code recovery into NEW outputs, never rewrite old manifests."""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path

from .build_native_sft import audit_candidate
from .distill import dump, load_config, validate_inputs, REASONING_REVIEW_POLICY
from .prepare_reused_tasks import file_sha256, rows
from .schema import canonical_hash
from .scientific_audit import POLICY as AUDIT_POLICY
from .rollout import trace_complete, trace_id_for


def prepare_recovery(source: Path, root: Path, cfg: dict, reviewer="Kimi-K3"):
    if root.exists():
        raise FileExistsError("refusing to overwrite a recovery directory")
    inputs = source / "inputs"
    old = json.loads((source / "solver/run_manifest.json").read_text(encoding="utf-8"))
    new = validate_inputs(inputs, cfg)
    if old["input_sha256"] != new["input_sha256"] or old["generation"] != new["generation"]:
        raise ValueError("recovery must preserve exact input and generation identity except code revision")
    tasks = {t["task_id"]: t for t in rows(inputs / "tasks.jsonl")}
    prompts = {r["task_hash"]: r["messages"] for r in rows(inputs / "prompts.jsonl")}
    candidates, complete_ids = {}, set()
    trace_source = source / "solver/traces.jsonl"
    for t in rows(trace_source):
        task = tasks[t["task_id"]]
        if (t["model"] != cfg["model"] or t["task_hash"] != canonical_hash(task)
            or t["messages"][:2] != prompts[t["task_hash"]]
            or t["temperature"] != cfg["temperature"] or t["max_tokens"] != cfg["max_tokens"]
            or t["trace_id"] != trace_id_for(task, cfg["model"], t["attempt"], cfg["run_variant"])):
            raise ValueError("source trace is incompatible with recovery generation")
        if trace_complete(t, require_both_channels=True):
            if t["trace_id"] in complete_ids:
                raise ValueError("duplicate completed source trace")
            complete_ids.add(t["trace_id"])
            c = audit_candidate(task, t)
            candidates[t["trace_id"]] = canonical_row_hash(c)
    # Validate all source reviews before any copying, including errors. Keep
    # only their latest records; a successful decision must never be repeated.
    reviews = {}
    for name, policy in (("scientific-audit.jsonl", AUDIT_POLICY), ("reasoning-quality.jsonl", REASONING_REVIEW_POLICY)):
        latest = {}
        for r in rows(source / name):
            if r.get("model") != reviewer or r.get("policy") != policy or r["row_sha256"] != candidates.get(r["trace_id"]):
                raise ValueError("source review is not bound to the preserved candidate")
            previous = latest.get(r["trace_id"])
            if previous and "error" not in previous:
                raise ValueError("duplicate completed source review")
            latest[r["trace_id"]] = r
        reviews[name] = latest
    root.mkdir(parents=True)
    try:
        shutil.copytree(inputs, root / "inputs")
        solver = root / "solver"
        solver.mkdir()
        for name in ("traces.jsonl", "raw-responses.jsonl", "traces.errors.jsonl"):
            if (source / "solver" / name).exists():
                shutil.copy2(source / "solver" / name, solver / name)
        report = {"policy": "explicit-preserved-trace-recovery-v1", "source": str(source.resolve()),
                  "old_fingerprint": old["fingerprint"], "new_fingerprint": new["fingerprint"],
                  "old_factory_commit": old["factory_commit"], "new_factory_commit": new["factory_commit"],
                  "selected": new["selected"], "usable_complete_traces_preserved": len(complete_ids),
                  "generations_to_retry": new["selected"] - len(complete_ids),
                  "source_artifact_sha256": {name: file_sha256(source / name) for name in (
                      "solver/traces.jsonl", "scientific-audit.jsonl", "reasoning-quality.jsonl")},
                  "reviews": {name: {"complete": sum("error" not in r for r in latest.values()),
                                     "error": sum("error" in r for r in latest.values())}
                              for name, latest in reviews.items()},
                  "source_modified": False}
        for name, latest in reviews.items():
            with (root / name).open("w", encoding="utf-8", newline="\n") as stream:
                for r in latest.values():
                    stream.write(json.dumps(r, ensure_ascii=False) + "\n")
        dump(solver / "run_manifest.json", {**new, "recovery_source": report})
        dump(root / "recovery_manifest.json", report)
        return report
    except BaseException:
        # Do not remove partial recovery data. Its presence blocks accidental
        # adoption and leaves evidence for an explicit recovery attempt.
        raise


def canonical_row_hash(row):
    import hashlib
    return hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reviewer", default="Kimi-K3")
    args = parser.parse_args()
    print(json.dumps(prepare_recovery(args.source, args.root, load_config(args.config), args.reviewer), indent=2))


if __name__ == "__main__":
    main()
