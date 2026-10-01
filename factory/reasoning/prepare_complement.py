"""Reuse the cleaned Kimi prompt universe minus an already prepared population.

Old answer approval is deliberately NOT an input requirement. Only exact public
system/user messages are reused; old assistant content is never written or sent.
The exclusion snapshot is hash-bound and checked again by distill.validate_inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .prepare_reused_tasks import file_sha256, rows, write_rows
from .schema import canonical_hash, validate_task
from .student_view import REUSED_PROMPT_POLICY

SELECTION_POLICY = "cleaned-kimi-prompt-complement-v1"


def prompt_hash(messages: list[dict]) -> str:
    return canonical_hash({"policy_version": REUSED_PROMPT_POLICY, "messages": messages})


def exclusion_snapshot(inputs: Path) -> tuple[list[dict], dict[str, str]]:
    report = json.loads((inputs / "selection_report.json").read_text(encoding="utf-8"))
    if report.get("require_supported") is not True:
        raise ValueError("baseline must be the previously supported prompt population")
    names = ("tasks.jsonl", "prompts.jsonl", "source_index.jsonl")
    hashes = {name: file_sha256(inputs / name) for name in names}
    if any(hashes[name] != report.get("input_sha256", {}).get(name) for name in names):
        raise ValueError("baseline input artifact changed")
    tasks = list(rows(inputs / "tasks.jsonl"))
    prompts = {r["task_hash"]: r["messages"] for r in rows(inputs / "prompts.jsonl")}
    sources = {r["task_hash"]: r for r in rows(inputs / "source_index.jsonl")}
    if not tasks or len(tasks) != report["selected"] or len(prompts) != len(tasks) or len(sources) != len(tasks):
        raise ValueError("baseline has missing or duplicate identities")
    snapshot = []
    for task in tasks:
        validate_task(task)
        digest = canonical_hash(task)
        messages = prompts[digest]
        visible_hash = prompt_hash(messages)
        if sources[digest].get("student_view_hash") != visible_hash:
            raise ValueError("baseline source/prompt mismatch")
        snapshot.append({"task_id": task["task_id"], "task_hash": digest,
                         "old_trace_id": sources[digest]["old_trace_id"],
                         "student_view_hash": visible_hash,
                         "user_sha256": hashlib.sha256(messages[1]["content"].encode()).hexdigest()})
    if len({r["task_hash"] for r in snapshot}) != len(snapshot):
        raise ValueError("duplicate baseline task hashes")
    return snapshot, hashes


def prepare(*, candidates_path: Path, index_path: Path, source_root: Path,
            exclude_inputs: Path, out_dir: Path, prior_audit_path: Path | None = None) -> dict:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty directory: {out_dir}")
    source_root = source_root.resolve()
    exclusions, excluded_hashes = exclusion_snapshot(exclude_inputs)
    excluded_ids = {r["old_trace_id"] for r in exclusions}
    excluded_tasks = {r["task_id"] for r in exclusions}
    excluded_task_hashes = {r["task_hash"] for r in exclusions}
    excluded_prompts = {r["student_view_hash"] for r in exclusions}
    excluded_users = {r["user_sha256"] for r in exclusions}
    source_hash, index_hash = file_sha256(candidates_path), file_sha256(index_path)
    prior_audits = {}
    prior_audit_hash = file_sha256(prior_audit_path) if prior_audit_path else None
    if prior_audit_path:
        for r in rows(prior_audit_path):
            prior_audits[r["trace_id"]] = {
                "disposition": r.get("disposition", "audit_error"),
                "task_status": (r.get("plan") or {}).get("task_status"),
            }
    locations = {}
    for row in rows(index_path):
        if row["trace_id"] in locations:
            raise ValueError("duplicate original source index ID")
        locations[row["trace_id"]] = row
    selected, rejected_rows, task_cache = [], [], {}
    seen_ids, seen_tasks, seen_prompts, seen_users = set(), set(), set(), set()
    encountered_baseline = set()
    counts, kinds, archetypes, repos = Counter(), Counter(), Counter(), Counter()
    for row in rows(candidates_path):
        counts["universe_rows"] += 1
        identity = row["trace_id"]
        if identity in seen_ids:
            raise ValueError(f"duplicate candidate identity: {identity}")
        seen_ids.add(identity)
        if identity in excluded_ids:
            encountered_baseline.add(identity)
            counts["excluded_baseline_rows"] += 1
            continue
        counts["raw_complement_rows"] += 1
        try:
            index = locations.get(identity)
            if index is None:
                raise ValueError("missing_source_index")
            source_dir = Path(index["source_file"]).resolve().parent
            if not source_dir.is_relative_to(source_root):
                raise ValueError("source_path_outside_declared_root")
            task_path = source_dir / "tasks.jsonl"
            if task_path not in task_cache:
                task_cache[task_path] = {r["task_id"]: r for r in rows(task_path)}
            task = validate_task(task_cache[task_path][index["task_name"]])
            digest = canonical_hash(task)
            if digest != row.get("task_hash"):
                raise ValueError("source_task_hash_mismatch")
            messages = row.get("messages") or []
            if [m.get("role") for m in messages[:2]] != ["system", "user"]:
                raise ValueError("invalid_source_prompt_roles")
            prompt = [{"role": m["role"], "content": m.get("content")} for m in messages[:2]]
            if any(not isinstance(m["content"], str) or not m["content"].strip() for m in prompt):
                raise ValueError("invalid_source_prompt_content")
            if task["problem"]["question"] not in prompt[1]["content"]:
                raise ValueError("question_not_in_source_prompt")
            visible_hash = prompt_hash(prompt)
            user_hash = hashlib.sha256(prompt[1]["content"].encode()).hexdigest()
            if (task["task_id"] in excluded_tasks or digest in excluded_task_hashes
                    or visible_hash in excluded_prompts or user_hash in excluded_users):
                counts["excluded_alias_overlap"] += 1
                continue
            if digest in seen_tasks or visible_hash in seen_prompts or user_hash in seen_users:
                counts["duplicate_complement_task_or_prompt"] += 1
                continue
            seen_tasks.add(digest)
            seen_prompts.add(visible_hash)
            seen_users.add(user_hash)
            repo = task["source"]["repo"]
            selected.append((identity, task, str(task_path), prompt, visible_hash, user_hash))
            repos[repo] += 1
            kinds[task["deliverable"]["kind"]] += 1
            archetypes[task["archetype"]] += 1
        except (ValueError, KeyError, TypeError, OSError) as exc:
            counts["structurally_rejected"] += 1
            rejected_rows.append({"old_trace_id": identity, "error": f"{type(exc).__name__}: {exc}"[:1000]})
    if encountered_baseline != excluded_ids:
        raise ValueError("baseline is not an exact subset of the cleaned candidate universe")
    if not selected:
        raise ValueError("no valid, disjoint prompts remain")
    if source_hash != file_sha256(candidates_path) or index_hash != file_sha256(index_path):
        raise ValueError("candidate source/index changed during preparation")
    if excluded_hashes != {name: file_sha256(exclude_inputs / name) for name in excluded_hashes}:
        raise ValueError("baseline changed during preparation")
    if prior_audit_path and prior_audit_hash != file_sha256(prior_audit_path):
        raise ValueError("prior audit changed during preparation")
    selected.sort(key=lambda item: hashlib.sha256(item[0].encode()).hexdigest())
    out_dir.mkdir(parents=True, exist_ok=True)
    write_rows(out_dir / "tasks.jsonl", [item[1] for item in selected])
    write_rows(out_dir / "prompts.jsonl", [
        {"task_hash": canonical_hash(task), "messages": prompt}
        for _identity, task, _path, prompt, _vh, _uh in selected])
    write_rows(out_dir / "source_index.jsonl", [
        {"old_trace_id": identity, "task_id": task["task_id"], "task_hash": canonical_hash(task),
         "source_task_file": path, "student_view_hash": vh, "user_sha256": uh,
         "source_population": "cleaned-kimi-complement",
         "old_answer_support_required": False,
         "prior_kimi_audit": prior_audits.get(identity)}
        for identity, task, path, _prompt, vh, uh in selected])
    write_rows(out_dir / "exclusions.jsonl", exclusions)
    write_rows(out_dir / "preparation-rejected.jsonl", rejected_rows)
    report = {"selection_policy": SELECTION_POLICY, "source_format": "cleaned-native-trace",
              "require_supported": False, "old_answer_support_required": False,
              "old_assistant_responses_copied": 0, "selected": len(selected),
              "target": len(selected), "counts": dict(counts),
              "distinct_repositories": len(repos), "max_tasks_per_repository": max(repos.values()),
              "per_repository_cap": None, "by_deliverable": dict(kinds), "by_archetype": dict(archetypes),
              "source_sft": str(candidates_path.resolve()), "source_sft_sha256": source_hash,
              "source_index": str(index_path.resolve()), "source_index_sha256": index_hash,
              "excluded_inputs": str(exclude_inputs.resolve()), "excluded_selected": len(exclusions),
              "excluded_input_sha256": excluded_hashes,
              "prior_audit": str(prior_audit_path.resolve()) if prior_audit_path else None,
              "prior_audit_sha256": prior_audit_hash,
              "by_prior_answer_disposition": dict(Counter(
                  prior_audits.get(item[0], {}).get("disposition", "not_available") for item in selected)),
              "verified_overlap": {"old_trace_ids": 0, "task_ids": 0, "task_hashes": 0,
                                   "visible_prompt_hashes": 0, "visible_user_hashes": 0},
              "input_sha256": {name: file_sha256(out_dir / name) for name in
                               ("tasks.jsonl", "prompts.jsonl", "source_index.jsonl", "exclusions.jsonl")}}
    (out_dir / "selection_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidates", "index", "source-root", "exclude-inputs", "out-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--prior-audit", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(candidates_path=args.candidates, index_path=args.index,
                            source_root=args.source_root, exclude_inputs=args.exclude_inputs,
                            out_dir=args.out_dir, prior_audit_path=args.prior_audit), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
