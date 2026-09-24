"""Race-safe batch orchestration from repository discovery to SFT shards."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import os
import random
import socket
import sqlite3
import threading
import time
from pathlib import Path

from ..author import llm
from .discovery import (
    DEFAULT_KEYWORDS_PATH,
    GitHubClient,
    discover_repositories,
    expand_keywords_best_effort,
    load_keywords,
    load_scicodepile_manifest,
    merge_repository_channels,
    pin_repository_heads,
    resolve_github_token,
    stratify_repositories,
    write_catalog,
)
from .queue import (
    JobLease,
    LeaseHeartbeat,
    MetricsCapacityController,
    ScheduledChat,
    WorkQueue,
)
from .repository import process_repository, safe_slug
from .rollout import current_commit

JOB_KIND = "reasoning_repository"
BATCH_SCHEMA = "scicode-reasoning-batch-v1"
RECIPE_SCHEMA = "scicode-reasoning-recipe-v1"


class BatchError(RuntimeError):
    """Batch inputs or completed shards are inconsistent."""


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BatchError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise BatchError(f"{path}:{number}: row must be an object")
            rows.append(value)
    return rows


def snapshot_key(candidate: dict) -> str:
    identity = (
        candidate.get("snapshot_hash")
        or candidate.get("head_sha")
        or candidate.get("pushed_at")
        or candidate.get("updated_at")
        or "unknown"
    )
    return f"{candidate['repo_id']}:{identity}"


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def enqueue_catalog(
    queue: WorkQueue,
    catalog_path: Path,
    *,
    max_attempts: int = 3,
    recipe: dict | None = None,
) -> dict:
    recipe = dict(recipe or {})
    counts = {"inserted": 0, "existing": 0}
    for candidate in _jsonl(Path(catalog_path)):
        key = f"{snapshot_key(candidate)}:{_canonical_hash(recipe)[:16]}"
        _job_id, inserted = queue.enqueue(
            JOB_KIND,
            key,
            {
                "repository": candidate,
                "recipe": recipe,
                "expected_rows": int(
                    recipe.get("reservation_rows_per_repo")
                    or recipe.get("tasks_per_repo", 3)
                ),
            },
            priority=int(candidate.get("stars") or 0),
            max_attempts=max_attempts,
        )
        counts["inserted" if inserted else "existing"] += 1
    return counts


def _output_dir(output_root: Path, lease: JobLease) -> Path:
    candidate = lease.payload.get("repository", lease.payload)
    repo_slug = safe_slug(candidate["full_name"])
    return Path(output_root) / "repositories" / repo_slug / lease.job_id


def process_lease(
    queue: WorkQueue,
    lease: JobLease,
    *,
    cache_root: Path,
    output_root: Path,
    chat_fn=llm.chat,
    job_lease_seconds: float = 14_400,
    capacity_controller: MetricsCapacityController | None = None,
) -> dict:
    """Process one leased repository under global repo and model budgets."""
    candidate = lease.payload.get("repository", lease.payload)
    recipe = lease.payload.get("recipe") or {}
    expected_commit = recipe.get("factory_commit")
    running_commit = current_commit()
    if (
        expected_commit
        and expected_commit != "unknown"
        and running_commit != "unknown"
        and expected_commit != running_commit
    ):
        raise BatchError(
            f"worker factory commit {running_commit} does not match recipe "
            f"{expected_commit}"
        )
    scheduled_chat = ScheduledChat(
        queue,
        chat_fn,
        worker=lease.worker,
        capacity_controller=capacity_controller,
    )
    repo_resource = f"repo:{candidate['repo_id']}"
    queue.configure_slots(repo_resource, 1)
    output_dir = _output_dir(Path(output_root), lease)
    with LeaseHeartbeat(queue, lease, job_lease_seconds):
        with queue.slot(
            "repository",
            lease.worker,
            lease_seconds=job_lease_seconds,
        ), queue.slot(
            repo_resource,
            lease.worker,
            lease_seconds=job_lease_seconds,
        ):
            report = process_repository(
                candidate,
                cache_root=cache_root,
                output_dir=output_dir,
                chat_fn=scheduled_chat,
                profile_model=recipe.get("profile_model"),
                author_model=recipe.get("author_model"),
                critic_model=recipe.get("critic_model"),
                verifier_model=recipe.get("verifier_model"),
                solver_model=recipe.get("solver_model"),
                judge_model=recipe.get("judge_model"),
                additional_judge_models=tuple(
                    recipe.get("additional_judge_models") or ()
                ),
                tasks_per_repo=int(recipe.get("tasks_per_repo", 3)),
                min_tasks_per_repo=int(recipe.get("min_tasks_per_repo", 3)),
                max_mined_candidates=int(recipe.get("max_mined_candidates", 600)),
                allow_unknown_license=bool(recipe.get("allow_unknown_license", False)),
                max_tokens=int(recipe.get("max_tokens", 16384)),
                author_max_tokens=int(
                    recipe.get("author_max_tokens") or recipe.get("max_tokens", 16384)
                ),
                solver_max_tokens=int(
                    recipe.get("solver_max_tokens") or recipe.get("max_tokens", 16384)
                ),
                context_window_tokens=int(recipe.get("context_window_tokens", 262_144)),
                critic_max_tokens=int(recipe.get("critic_max_tokens", 4096)),
                verifier_max_tokens=int(recipe.get("verifier_max_tokens", 4096)),
                judge_max_tokens=int(recipe.get("judge_max_tokens", 8192)),
                judge_max_input_chars=int(recipe.get("judge_max_input_chars", 160_000)),
                timeout=int(recipe.get("timeout", 2400)),
                pipeline_concurrency=int(recipe.get("pipeline_concurrency", 3)),
                resume_complete=bool(recipe.get("resume_complete", False)),
            )
    return {
        "status": report["status"],
        "reason": report.get("reason"),
        "output_dir": str(output_dir),
        "report": str(output_dir / "repository_report.json"),
        "sft": report.get("sft"),
        "candidate_sft": report.get("candidate_sft") or report.get("sft"),
        "sft_rows": report.get("sft_rows", 0),
        "selected_source_candidates": report.get("selected", 0),
        "artifacts": report.get("artifacts") or {},
        "commit": report.get("commit"),
    }


def worker_loop(
    queue: WorkQueue,
    *,
    worker: str,
    processor,
    watch: bool = True,
    idle_interval: float = 1.0,
    max_jobs: int | None = None,
    job_lease_seconds: float = 14_400,
    target_sft_rows: int | None = None,
) -> dict:
    counts = {"completed": 0, "retried": 0, "failed": 0}
    handled = 0
    while max_jobs is None or handled < max_jobs:
        lease = _retry_sqlite_lock(
            lambda: queue.claim(
                worker,
                kinds=(JOB_KIND,),
                lease_seconds=job_lease_seconds,
                target_rows=target_sft_rows,
            )
        )
        if lease is None:
            progress = _retry_sqlite_lock(lambda: queue.row_progress(kinds=(JOB_KIND,)))
            if target_sft_rows is not None and progress["completed"] >= target_sft_rows:
                counts["target_reached"] = progress["completed"]
                break
            states = _retry_sqlite_lock(queue.counts)
            if watch and (states.get("pending", 0) or states.get("leased", 0)):
                time.sleep(idle_interval)
                continue
            break
        handled += 1
        try:
            result = processor(lease)
            _retry_sqlite_lock(lambda: queue.complete(lease, result))
            counts["completed"] += 1
        except Exception as exc:
            delay = min(900.0, 15.0 * (2 ** max(0, lease.attempt - 1)))
            error_message = f"{type(exc).__name__}: {exc}"
            status = _retry_sqlite_lock(
                lambda: queue.fail(
                    lease,
                    error_message,
                    retryable=True,
                    retry_delay=delay,
                )
            )
            counts["retried" if status == "pending" else "failed"] += 1
    return counts


def _retry_sqlite_lock(operation, *, max_wait: float = 300.0):
    """Retry transient writer contention without losing a repository worker."""
    deadline = time.monotonic() + max_wait
    attempt = 0
    while True:
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            delay = min(5.0, 0.25 * 2 ** min(attempt, 5))
            time.sleep(
                min(
                    random.uniform(0.8, 1.2) * delay,
                    max(0.0, deadline - time.monotonic()),
                )
            )
            attempt += 1


def run_local_workers(
    queue: WorkQueue,
    *,
    workers: int,
    processor_factory,
    job_lease_seconds: float = 14_400,
    target_sft_rows: int | None = None,
) -> list[dict]:
    if workers < 1:
        raise BatchError("workers must be positive")
    host = socket.gethostname()

    def one(index):
        worker = f"{host}:{os.getpid()}:{index}:{threading.get_ident()}"
        return worker_loop(
            queue,
            worker=worker,
            processor=processor_factory(worker),
            watch=True,
            job_lease_seconds=job_lease_seconds,
            target_sft_rows=target_sft_rows,
        )

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, range(workers)))


def aggregate_sft(
    queue: WorkQueue,
    output_path: Path,
    *,
    report_path: Path | None = None,
    allow_incomplete: bool = False,
    target_sft_rows: int | None = None,
) -> dict:
    if target_sft_rows is not None and target_sft_rows < 1:
        raise BatchError("target_sft_rows must be positive")
    states = queue.counts()
    unfinished = states.get("pending", 0) + states.get("leased", 0)
    if unfinished and not allow_incomplete and target_sft_rows is None:
        raise BatchError(f"refusing to aggregate with {unfinished} unfinished jobs")
    rows_by_trace = {}
    companion_specs = {
        "tasks": "task_id",
        "preflight": "preflight_id",
        "verification": "verification_id",
        "traces": "trace_id",
        "grades": "grade_id",
    }
    companion_rows = {name: {} for name in companion_specs}
    repositories = {"complete": 0, "rejected": 0, "missing": 0}
    selected_source_candidates = 0
    for item in queue.completed_results(JOB_KIND):
        result = item["result"]
        status = result.get("status")
        repositories[status if status in repositories else "missing"] += 1
        selected_source_candidates += int(result.get("selected_source_candidates") or 0)
        sft_path = result.get("sft")
        if status != "complete" or not sft_path:
            continue
        path = Path(sft_path)
        if not path.exists():
            repositories["missing"] += 1
            continue
        for row in _jsonl(path):
            trace_id = row.get("trace_id")
            if not isinstance(trace_id, str):
                raise BatchError(f"SFT row in {path} has no trace_id")
            previous = rows_by_trace.get(trace_id)
            if previous is not None and previous != row:
                raise BatchError(f"conflicting SFT rows for trace {trace_id}")
            rows_by_trace[trace_id] = row
        for name, identity_field in companion_specs.items():
            artifact_path = (result.get("artifacts") or {}).get(name)
            if not artifact_path:
                continue
            artifact = Path(artifact_path)
            if not artifact.exists():
                raise BatchError(f"completed job artifact is missing: {artifact}")
            for row in _jsonl(artifact):
                identity = row.get(identity_field)
                if not isinstance(identity, str):
                    raise BatchError(
                        f"{name} row in {artifact} has no {identity_field}"
                    )
                previous = companion_rows[name].get(identity)
                if previous is not None and previous != row:
                    raise BatchError(f"conflicting {name} rows for {identity}")
                companion_rows[name][identity] = row
    discovered_sft_rows = len(rows_by_trace)
    if target_sft_rows is not None:
        if discovered_sft_rows < target_sft_rows and not allow_incomplete:
            raise BatchError(
                f"target requires {target_sft_rows} SFT rows, only "
                f"{discovered_sft_rows} completed"
            )
        keep = set(sorted(rows_by_trace)[:target_sft_rows])
        rows_by_trace = {
            trace_id: row for trace_id, row in rows_by_trace.items() if trace_id in keep
        }
        task_ids = {row.get("task_name") for row in rows_by_trace.values()}
        task_hashes = {row.get("task_hash") for row in rows_by_trace.values()}
        companion_rows["tasks"] = {
            key: row
            for key, row in companion_rows["tasks"].items()
            if row.get("task_id") in task_ids
        }
        for name in ("traces", "grades"):
            companion_rows[name] = {
                key: row
                for key, row in companion_rows[name].items()
                if row.get("trace_id") in keep
            }
        for name in ("preflight", "verification"):
            companion_rows[name] = {
                key: row
                for key, row in companion_rows[name].items()
                if row.get("task_hash") in task_hashes
            }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for trace_id in sorted(rows_by_trace):
            output.write(json.dumps(rows_by_trace[trace_id], ensure_ascii=False) + "\n")
    temporary.replace(output_path)
    companion_artifacts = {}
    for name, rows in companion_rows.items():
        path = output_path.with_name(f"batch.{name}.jsonl")
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            for identity in sorted(rows):
                output.write(json.dumps(rows[identity], ensure_ascii=False) + "\n")
        temporary.replace(path)
        companion_artifacts[name] = {"path": str(path), "rows": len(rows)}
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    review_modes = {
        (row.get("automatic_review") or {}).get("mode")
        for row in rows_by_trace.values()
    }
    if rows_by_trace and review_modes == {"single_model"}:
        quality_status = "automatic_single_model_reviewed"
    elif rows_by_trace and None not in review_modes:
        quality_status = "automatic_model_panel_reviewed"
    else:
        quality_status = "unreviewed_or_mixed"
    report = {
        "schema_version": BATCH_SCHEMA,
        "queue_db": str(queue.path.resolve()),
        "queue": states,
        "repositories": repositories,
        "sft_rows": len(rows_by_trace),
        "discovered_sft_rows": discovered_sft_rows,
        "target_sft_rows": target_sft_rows,
        "quality_status": quality_status,
        "production_yield": {
            "selected_source_candidates": selected_source_candidates,
            "accepted_sft_rows_before_target_trim": discovered_sft_rows,
            "accepted_fraction": (
                round(discovered_sft_rows / selected_source_candidates, 6)
                if selected_source_candidates
                else None
            ),
            "mean_accepted_rows_per_complete_repository": (
                round(discovered_sft_rows / repositories["complete"], 6)
                if repositories["complete"]
                else None
            ),
        },
        "quality_inputs": companion_artifacts,
        "sft": str(output_path),
        "sha256": digest,
        "allow_incomplete": allow_incomplete,
    }
    report_path = report_path or output_path.with_suffix(".report.json")
    report_tmp = Path(report_path).with_name(Path(report_path).name + ".tmp")
    report_tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_tmp.replace(report_path)
    return report


def _worker_options(
    parser: argparse.ArgumentParser,
    *,
    include_roots: bool = True,
) -> None:
    if include_roots:
        parser.add_argument("--cache-root", type=Path, required=True)
        parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-lease-seconds", type=float, default=14_400)
    parser.add_argument("--target-sft-rows", type=int)
    parser.add_argument("--llm-metrics-url")
    parser.add_argument("--llm-min-slots", type=int, default=1)
    parser.add_argument("--metrics-poll-seconds", type=float, default=30.0)
    parser.add_argument("--metrics-timeout", type=float, default=5.0)


def _recipe_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile-model")
    parser.add_argument("--author-model")
    parser.add_argument("--critic-model")
    parser.add_argument("--verifier-model")
    parser.add_argument("--solver-model")
    parser.add_argument("--judge-model")
    parser.add_argument("--additional-judge-model", action="append", default=[])
    parser.add_argument("--tasks-per-repo", type=int, default=3)
    parser.add_argument("--min-tasks-per-repo", type=int, default=3)
    parser.add_argument("--reservation-rows-per-repo", type=int)
    parser.add_argument("--max-mined-candidates", type=int, default=600)
    parser.add_argument("--allow-unknown-license", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--author-max-tokens", type=int)
    parser.add_argument("--solver-max-tokens", type=int)
    parser.add_argument("--context-window-tokens", type=int, default=262_144)
    parser.add_argument("--critic-max-tokens", type=int, default=4096)
    parser.add_argument("--verifier-max-tokens", type=int, default=4096)
    parser.add_argument("--judge-max-tokens", type=int, default=8192)
    parser.add_argument("--judge-max-input-chars", type=int, default=160_000)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--pipeline-concurrency", type=int, default=3)


def recipe_from_args(args) -> dict:
    return {
        "schema_version": RECIPE_SCHEMA,
        "factory_commit": current_commit(),
        "profile_model": args.profile_model,
        "author_model": args.author_model,
        "critic_model": args.critic_model,
        "verifier_model": args.verifier_model,
        "solver_model": args.solver_model,
        "judge_model": args.judge_model,
        "additional_judge_models": args.additional_judge_model,
        "tasks_per_repo": args.tasks_per_repo,
        "min_tasks_per_repo": args.min_tasks_per_repo,
        "reservation_rows_per_repo": (
            args.reservation_rows_per_repo or args.tasks_per_repo
        ),
        "max_mined_candidates": args.max_mined_candidates,
        "allow_unknown_license": args.allow_unknown_license,
        "max_tokens": args.max_tokens,
        "author_max_tokens": args.author_max_tokens or args.max_tokens,
        "solver_max_tokens": args.solver_max_tokens or args.max_tokens,
        "context_window_tokens": args.context_window_tokens,
        "critic_max_tokens": args.critic_max_tokens,
        "verifier_max_tokens": args.verifier_max_tokens,
        "judge_max_tokens": args.judge_max_tokens,
        "judge_max_input_chars": args.judge_max_input_chars,
        "timeout": args.timeout,
        "pipeline_concurrency": args.pipeline_concurrency,
    }


def _capacity_controller(args, *, maximum: int) -> MetricsCapacityController | None:
    if not args.llm_metrics_url:
        return None
    if args.llm_min_slots > maximum:
        raise BatchError("llm_min_slots cannot exceed llm_slots")
    return MetricsCapacityController(
        args.llm_metrics_url,
        minimum=args.llm_min_slots,
        maximum=maximum,
        refresh_interval=args.metrics_poll_seconds,
        timeout=args.metrics_timeout,
    )


def _processor(
    queue: WorkQueue,
    args,
    worker: str,
    capacity_controller: MetricsCapacityController | None = None,
):
    return lambda lease: process_lease(
        queue,
        lease,
        cache_root=args.cache_root.resolve(),
        output_root=args.output_root.resolve(),
        job_lease_seconds=args.job_lease_seconds,
        capacity_controller=capacity_controller,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue_parser = subparsers.add_parser("enqueue")
    enqueue_parser.add_argument("--db", type=Path, required=True)
    enqueue_parser.add_argument("--catalog", type=Path, required=True)
    enqueue_parser.add_argument("--max-attempts", type=int, default=3)
    enqueue_parser.add_argument("--llm-slots", type=int, default=3)
    enqueue_parser.add_argument("--repository-slots", type=int, default=2)
    enqueue_parser.add_argument(
        "--sqlite-journal",
        choices=("DELETE", "TRUNCATE", "WAL"),
        default="DELETE",
    )
    _recipe_options(enqueue_parser)

    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument("--db", type=Path, required=True)
    worker_parser.add_argument("--worker")
    worker_parser.add_argument("--max-jobs", type=int)
    worker_parser.add_argument("--no-watch", action="store_true")
    _worker_options(worker_parser)

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--db", type=Path, required=True)
    resume_parser.add_argument("--workers", type=int, required=True)
    _worker_options(resume_parser)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--db", type=Path, required=True)

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--db", type=Path, required=True)
    aggregate_parser.add_argument("--out", type=Path, required=True)
    aggregate_parser.add_argument("--report", type=Path)
    aggregate_parser.add_argument("--allow-incomplete", action="store_true")
    aggregate_parser.add_argument("--target-sft-rows", type=int)

    auto_parser = subparsers.add_parser("auto")
    auto_parser.add_argument("--output-root", type=Path, required=True)
    auto_parser.add_argument("--cache-root", type=Path, required=True)
    auto_parser.add_argument(
        "--repository-source",
        choices=("keywords", "scicodepile", "hybrid"),
        default="keywords",
        help="primary repository discovery source; hybrid reserves a keyword channel",
    )
    auto_parser.add_argument(
        "--scicodepile-catalog",
        "--scicodepile-manifest",
        dest="scicodepile_manifest",
        type=Path,
        help="catalog of local snapshots prepared from the clean SciCodePile dataset",
    )
    auto_parser.add_argument(
        "--keyword-channel-limit",
        type=int,
        default=0,
        help="number of repository slots reserved for keyword search in hybrid mode",
    )
    auto_parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_PATH)
    auto_parser.add_argument("--min-stars", type=int, default=10)
    auto_parser.add_argument("--max-size-kb", type=int, default=1_000_000)
    auto_parser.add_argument("--language", default="Python")
    auto_parser.add_argument(
        "--search-scope",
        choices=("name,description", "name,description,readme"),
        default="name,description",
    )
    auto_parser.add_argument("--pages-per-query", type=int, default=1)
    auto_parser.add_argument("--per-page", type=int, default=30)
    auto_parser.add_argument(
        "--query-limit",
        type=int,
        help="use only the first N keyword queries (useful for bounded smoke runs)",
    )
    auto_parser.add_argument("--repository-limit", type=int, default=20)
    auto_parser.add_argument("--request-interval", type=float, default=2.1)
    auto_parser.add_argument("--expand-keywords", action="store_true")
    auto_parser.add_argument("--max-expanded", type=int, default=40)
    auto_parser.add_argument("--expansion-model")
    auto_parser.add_argument("--workers", type=int, default=2)
    auto_parser.add_argument("--llm-slots", type=int, default=3)
    auto_parser.add_argument("--repository-slots", type=int, default=2)
    auto_parser.add_argument("--max-attempts", type=int, default=3)
    auto_parser.add_argument("--db", type=Path)
    auto_parser.add_argument(
        "--sqlite-journal",
        choices=("DELETE", "TRUNCATE", "WAL"),
        default="DELETE",
    )
    _worker_options(auto_parser, include_roots=False)
    _recipe_options(auto_parser)

    args = parser.parse_args()
    if hasattr(args, "reservation_rows_per_repo"):
        reservation = args.reservation_rows_per_repo
        if reservation is not None and (
            reservation < 1 or reservation > args.tasks_per_repo
        ):
            parser.error(
                "--reservation-rows-per-repo must be between 1 and --tasks-per-repo"
            )
    if args.command == "status":
        print(json.dumps(WorkQueue(args.db).counts(), indent=2))
        return
    if args.command == "aggregate":
        report = aggregate_sft(
            WorkQueue(args.db),
            args.out,
            report_path=args.report,
            allow_incomplete=args.allow_incomplete,
            target_sft_rows=args.target_sft_rows,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.command == "enqueue":
        queue = WorkQueue(args.db, journal_mode=args.sqlite_journal)
        queue.configure_slots("llm", args.llm_slots)
        queue.configure_slots("repository", args.repository_slots)
        result = enqueue_catalog(
            queue,
            args.catalog,
            max_attempts=args.max_attempts,
            recipe=recipe_from_args(args),
        )
        print(json.dumps({**result, "queue": queue.counts()}, indent=2))
        return
    if args.command == "worker":
        queue = WorkQueue(args.db)
        worker = args.worker or f"{socket.gethostname()}:{os.getpid()}"
        llm_slots = queue.configured_slots("llm")
        capacity_controller = _capacity_controller(args, maximum=llm_slots)
        result = worker_loop(
            queue,
            worker=worker,
            processor=_processor(queue, args, worker, capacity_controller),
            watch=not args.no_watch,
            max_jobs=args.max_jobs,
            job_lease_seconds=args.job_lease_seconds,
            target_sft_rows=args.target_sft_rows,
        )
        print(json.dumps({**result, "queue": queue.counts()}, indent=2))
        return
    if args.command == "resume":
        queue = WorkQueue(args.db)
        llm_slots = queue.configured_slots("llm")
        capacity_controller = _capacity_controller(args, maximum=llm_slots)
        worker_results = run_local_workers(
            queue,
            workers=args.workers,
            processor_factory=lambda worker: _processor(
                queue, args, worker, capacity_controller
            ),
            job_lease_seconds=args.job_lease_seconds,
            target_sft_rows=args.target_sft_rows,
        )
        report = aggregate_sft(
            queue,
            args.output_root / "accepted-sft.jsonl",
            target_sft_rows=args.target_sft_rows,
        )
        print(json.dumps({"workers": worker_results, "aggregate": report}, indent=2))
        return

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    queue = WorkQueue(
        args.db or output_root / "batch.sqlite3",
        journal_mode=args.sqlite_journal,
    )
    queue.configure_slots("llm", args.llm_slots)
    queue.configure_slots("repository", args.repository_slots)
    discovery_errors = []
    discovery_rejections = []
    if args.repository_limit < 1:
        parser.error("--repository-limit must be positive")
    if args.keyword_channel_limit < 0:
        parser.error("--keyword-channel-limit must be nonnegative")

    primary = []
    if args.repository_source in {"scicodepile", "hybrid"}:
        if args.scicodepile_manifest is None:
            parser.error(
                "--scicodepile-catalog is required for scicodepile/hybrid sources"
            )
        reserved = (
            min(args.keyword_channel_limit, args.repository_limit)
            if args.repository_source == "hybrid"
            else 0
        )
        primary_limit = args.repository_limit - reserved
        if primary_limit < 1:
            parser.error(
                "hybrid mode must leave at least one slot for the SciCodePile source"
            )
        primary = stratify_repositories(
            load_scicodepile_manifest(args.scicodepile_manifest),
            limit=primary_limit,
        )

    secondary = []
    if args.repository_source in {"keywords", "hybrid"}:
        keyword_limit = (
            args.repository_limit
            if args.repository_source == "keywords"
            else min(args.keyword_channel_limit, args.repository_limit - 1)
        )
        if keyword_limit:
            keywords = load_keywords(args.keywords)
            if args.expand_keywords:
                scheduled = ScheduledChat(queue, llm.chat, worker="discovery")
                keywords = expand_keywords_best_effort(
                    keywords,
                    errors=discovery_errors,
                    chat_fn=scheduled,
                    model=args.expansion_model,
                    max_new=args.max_expanded,
                )
            if args.query_limit is not None:
                if args.query_limit < 1:
                    parser.error("--query-limit must be positive")
                keywords = keywords[: args.query_limit]
            github = GitHubClient(
                resolve_github_token(),
                search_scope=args.search_scope,
                request_interval=args.request_interval,
            )
            secondary = discover_repositories(
                keywords,
                search_fn=github.search,
                min_stars=args.min_stars,
                max_size_kb=args.max_size_kb,
                language=args.language or None,
                pages_per_query=args.pages_per_query,
                per_page=args.per_page,
                limit=max(keyword_limit, keyword_limit * 3),
                errors=discovery_errors,
                rejections=discovery_rejections,
            )
            primary_names = {
                str(row.get("full_name") or "").casefold() for row in primary
            }
            secondary = [
                row
                for row in secondary
                if str(row.get("full_name") or "").casefold() not in primary_names
            ][:keyword_limit]
            secondary = pin_repository_heads(
                secondary,
                resolve_fn=github.head_commit,
                errors=discovery_errors,
            )
    repositories = merge_repository_channels(
        primary,
        secondary,
        limit=args.repository_limit,
    )
    catalog_path = output_root / "catalog.jsonl"
    write_catalog(catalog_path, repositories)
    write_catalog(output_root / "discovery.errors.jsonl", discovery_errors)
    write_catalog(output_root / "discovery.rejections.jsonl", discovery_rejections)
    enqueue_result = enqueue_catalog(
        queue,
        catalog_path,
        max_attempts=args.max_attempts,
        recipe=recipe_from_args(args),
    )
    capacity_controller = _capacity_controller(args, maximum=args.llm_slots)
    worker_results = run_local_workers(
        queue,
        workers=args.workers,
        processor_factory=lambda worker: _processor(
            queue, args, worker, capacity_controller
        ),
        job_lease_seconds=args.job_lease_seconds,
        target_sft_rows=args.target_sft_rows,
    )
    report = aggregate_sft(
        queue,
        output_root / "accepted-sft.jsonl",
        target_sft_rows=args.target_sft_rows,
    )
    print(
        json.dumps(
            {
                "discovered": len(repositories),
                "discovery_errors": len(discovery_errors),
                "discovery_rejections": len(discovery_rejections),
                "enqueue": enqueue_result,
                "workers": worker_results,
                "aggregate": report,
                "llm_capacity": (
                    capacity_controller.snapshot()
                    if capacity_controller is not None
                    else {"mode": "static", "limit": args.llm_slots}
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
