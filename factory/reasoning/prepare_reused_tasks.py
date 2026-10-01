"""Select source-free, previously audited reasoning tasks for a new solver.

This reads old SFT rows only to identify audited task IDs and their visible
prompts. It never carries the old assistant response into the new dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .schema import canonical_hash, validate_task
from .student_view import REUSED_PROMPT_POLICY


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_rows(path: Path, data: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in data:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare(
    *, sft_path: Path, index_path: Path, source_root: Path,
    out_dir: Path, target: int, per_repo: int, require_supported: bool = False,
) -> dict:
    if target < 1 or per_repo < 0:
        raise ValueError("target must be positive; per_repo=0 means no cap")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty directory: {out_dir}")
    source_root = source_root.resolve()
    audited = {}
    source_hash = file_sha256(sft_path)
    index_hash = file_sha256(index_path)
    for row in rows(sft_path):
        messages = row.get("messages") or []
        if row.get("id") in audited:
            raise ValueError(f"duplicate source ID: {row.get('id')}")
        if require_supported:
            metadata = row.get("metadata") or {}
            if (
                metadata.get("audit_disposition") != "model_supported_answer"
                or metadata.get("training_target") != "reasoning_and_answer"
                or [m.get("role") for m in messages] != ["system", "user", "assistant"]
                or not isinstance(messages[-1].get("content"), str)
                or not messages[-1]["content"].startswith("<think>")
                or "</think>" not in messages[-1]["content"]
                or not messages[-1]["content"].split("</think>", 1)[1].strip()
            ):
                raise ValueError(f"source is not supported CoT plus answer: {row.get('id')}")
        if len(messages) < 3:
            continue
        audited[row["id"]] = {
            "system": messages[0].get("content"),
            "user": messages[1].get("content"),
            "task_hash": (row.get("metadata") or {}).get("task_hash"),
        }

    locations = {}
    for row in rows(index_path):
        trace_id = row.get("trace_id")
        if trace_id in audited:
            if trace_id in locations:
                raise ValueError(f"duplicate source index ID: {trace_id}")
            locations[trace_id] = row

    candidates = []
    rejected = Counter()
    task_cache = {}
    for trace_id, old in audited.items():
        index = locations.get(trace_id)
        if index is None:
            rejected["missing_index"] += 1
            continue
        source_dir = Path(index["source_file"]).resolve().parent
        if not source_dir.is_relative_to(source_root):
            rejected["outside_source_root"] += 1
            continue
        task_file = source_dir / "tasks.jsonl"
        if task_file not in task_cache:
            task_cache[task_file] = {
                task.get("task_id"): task for task in rows(task_file)
            } if task_file.exists() else {}
        task = task_cache[task_file].get(index["task_name"])
        if task is None:
            rejected["missing_task"] += 1
            continue
        try:
            task = validate_task(task)
        except (ValueError, KeyError, TypeError):
            rejected["invalid_task"] += 1
            continue
        if canonical_hash(task) != old["task_hash"]:
            rejected["task_hash_mismatch"] += 1
            continue
        if not isinstance(old["system"], str) or not isinstance(old["user"], str):
            rejected["invalid_audited_prompt"] += 1
            continue
        if task["problem"]["question"] not in old["user"]:
            rejected["question_not_in_audited_prompt"] += 1
            continue
        # The v1 audited prompt deliberately did not expose author-only
        # archetype payload. Keep that exact prompt for a controlled solver
        # comparison, rather than silently switching to the v3 renderer.
        repo = task["source"]["repo"]
        candidates.append((trace_id, repo, task, str(task_file), old))

    candidates.sort(key=lambda item: hashlib.sha256(item[0].encode()).hexdigest())
    selected = []
    repo_counts = Counter()
    seen_hashes = set()
    for item in candidates:
        trace_id, repo, task, task_file, _old = item
        task_hash = canonical_hash(task)
        if task_hash in seen_hashes or (per_repo and repo_counts[repo] >= per_repo):
            continue
        selected.append(item)
        seen_hashes.add(task_hash)
        repo_counts[repo] += 1
        if len(selected) == target:
            break
    if len(selected) < target:
        raise ValueError(
            f"only {len(selected)} eligible tasks after per-repo cap; "
            f"candidates={len(candidates)}, rejected={dict(rejected)}"
        )
    if source_hash != file_sha256(sft_path) or index_hash != file_sha256(index_path):
        raise ValueError("source SFT/index changed during preparation")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_rows(out_dir / "tasks.jsonl", [item[2] for item in selected])
    write_rows(out_dir / "prompts.jsonl", [
        {
            "task_hash": canonical_hash(task),
            "messages": [
                {"role": "system", "content": old["system"]},
                {"role": "user", "content": old["user"]},
            ],
        }
        for _trace_id, _repo, task, _task_file, old in selected
    ])
    write_rows(out_dir / "source_index.jsonl", [
        {
            "old_trace_id": trace_id,
            "task_id": task["task_id"],
            "task_hash": canonical_hash(task),
            "source_task_file": task_file,
            "student_view_hash": canonical_hash({
                "policy_version": REUSED_PROMPT_POLICY,
                "messages": [
                    {"role": "system", "content": old["system"]},
                    {"role": "user", "content": old["user"]},
                ],
            }),
        }
        for trace_id, _repo, task, task_file, old in selected
    ])
    report = {
        "target": target,
        "selected": len(selected),
        "distinct_repositories": len(repo_counts),
        "max_tasks_per_repository": max(repo_counts.values()),
        "audited_rows": len(audited),
        "eligible_candidates": len(candidates),
        "rejected": dict(rejected),
        "old_assistant_responses_copied": 0,
        "require_supported": require_supported,
        "per_repository_cap": per_repo or None,
        "source_sft": str(sft_path.resolve()),
        "source_sft_sha256": source_hash,
        "source_index": str(index_path.resolve()),
        "source_index_sha256": index_hash,
        "selection_policy": "sha256-old-id-sort-distinct-task-v1",
        "input_sha256": {
            name: file_sha256(out_dir / name)
            for name in ("tasks.jsonl", "prompts.jsonl", "source_index.jsonl")
        },
    }
    (out_dir / "selection_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sft", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--target", type=int, default=1000)
    parser.add_argument("--per-repo", type=int, default=5)
    parser.add_argument("--require-supported", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(
        sft_path=args.sft, index_path=args.index,
        source_root=args.source_root, out_dir=args.out_dir,
        target=args.target, per_repo=args.per_repo,
        require_supported=args.require_supported,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
