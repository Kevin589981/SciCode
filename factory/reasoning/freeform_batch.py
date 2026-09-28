"""Resumable batch runner for free-form SciCode questions and Qwen traces."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import socket
import threading
from pathlib import Path

from ..author import llm
from .freeform import (
    AUDIT_POLICY,
    atomic_json,
    atomic_jsonl,
    audit_trace,
    collect_trace,
    compose_task,
    sample_parameters,
    training_projection,
    training_row,
    validate_task,
)
from .queue import (
    LeaseHeartbeat,
    LocalSlotManager,
    MetricsCapacityController,
    ScheduledChat,
    WorkQueue,
)
from .schema import canonical_hash

JOB_KIND = "freeform_coding_seed_v2"
RECIPE_SCHEMA = "scicode-freeform-coding-recipe-v2"
SEED_SCHEMA = "scicode-freeform-seed-v1"
SOURCE_SUFFIXES = {
    ".py", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".f", ".f90",
    ".f95", ".jl", ".m", ".r",
}
THIRD_PARTY_PARTS = {"thirdparty", "third_party", "vendor", "vendored", "_external"}


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: expected object")
                rows.append(row)
    return rows


def prepare_seeds(
    catalog_path: Path,
    output_path: Path,
    *,
    repository_limit: int | None,
    seeds_per_repository: int,
    master_seed: int,
) -> dict:
    """Sample scientific snippets from already cleaned local SciCodePile files."""
    if seeds_per_repository < 1:
        raise ValueError("seeds_per_repository must be positive")
    repositories = [
        row for row in read_jsonl(catalog_path)
        if row.get("source_kind") == "scicodepile_clean_dataset"
    ]
    rng = random.Random(master_seed)
    rng.shuffle(repositories)
    if repository_limit is not None:
        repositories = repositories[:repository_limit]
    seeds = []
    missing = 0
    for repo in repositories:
        root = Path(repo["snapshot_path"])
        metadata = root / ".scicodepile_snapshot.json"
        if not metadata.is_file():
            missing += 1
            continue
        snapshot = json.loads(metadata.read_text(encoding="utf-8"))
        if snapshot.get("snapshot_hash") != repo.get("snapshot_hash"):
            raise ValueError(f"snapshot hash mismatch: {root}")
        files = sorted(
            path for path in root.rglob("*")
            if path.is_file()
            and path.suffix.casefold() in SOURCE_SUFFIXES
            and not any(
                part.casefold() in THIRD_PARTY_PARTS
                for part in path.relative_to(root).parts
            )
            and not path.stem.casefold().startswith(("license", "relicense"))
        )
        rng.shuffle(files)
        selected = []
        for path in files:
            if len(selected) >= seeds_per_repository:
                break
            size = path.stat().st_size
            if size == 0:
                continue
            offset = rng.randrange(max(1, size - 12_000))
            with path.open("rb") as stream:
                stream.seek(offset)
                excerpt = stream.read(14_000).decode("utf-8", errors="ignore")
            if offset and "\n" in excerpt:
                excerpt = excerpt.split("\n", 1)[1]
            if "\n" in excerpt:
                excerpt = excerpt.rsplit("\n", 1)[0]
            if excerpt.strip():
                selected.append((path, offset, excerpt))
        for index, (path, offset, excerpt) in enumerate(selected):
            source = {
                "repo_id": repo["repo_id"],
                "repo": repo["full_name"],
                "snapshot_hash": repo["snapshot_hash"],
                "dataset": repo.get("source_dataset"),
                "file": path.relative_to(root).as_posix(),
                "byte_offset": offset,
                "excerpt": excerpt,
            }
            seeds.append({
                "schema_version": SEED_SCHEMA,
                "seed_id": canonical_hash({
                    "source": source,
                    "draw": index,
                    "master_seed": master_seed,
                })[:24],
                "source": source,
            })
    atomic_jsonl(output_path, seeds)
    return {
        "repositories": len(repositories),
        "missing_snapshots": missing,
        "seeds": len(seeds),
        "output": str(output_path),
    }


def make_recipe(args) -> dict:
    recipe = {
        "schema_version": RECIPE_SCHEMA,
        "author_model": args.author_model,
        "solver_model": args.solver_model,
        "audit_model": args.audit_model,
        "attempts": args.attempts,
        "master_seed": args.master_seed,
        "author_max_tokens": args.author_max_tokens,
        "solver_max_tokens": args.solver_max_tokens,
        "audit_max_tokens": args.audit_max_tokens,
        "timeout": args.timeout,
        "send_seed": args.send_seed,
    }
    if not all(recipe[key] for key in ("author_model", "solver_model", "audit_model")):
        raise ValueError("all three role models are required")
    if recipe["attempts"] < 1:
        raise ValueError("attempts must be positive")
    return recipe


def enqueue_seeds(
    queue: WorkQueue, seeds_path: Path, recipe_path: Path, recipe: dict
) -> dict:
    recipe_hash = canonical_hash(recipe)
    if recipe_path.exists():
        existing = json.loads(recipe_path.read_text(encoding="utf-8"))
        if existing != recipe:
            raise ValueError("recipe file already belongs to a different run")
    else:
        atomic_json(recipe_path, recipe)
    counts = {"inserted": 0, "existing": 0}
    for seed in read_jsonl(seeds_path):
        if seed.get("schema_version") != SEED_SCHEMA:
            raise ValueError("seed schema mismatch")
        _job_id, inserted = queue.enqueue(
            JOB_KIND,
            f"{seed['seed_id']}:{recipe_hash[:16]}",
            {"seed": seed, "recipe_hash": recipe_hash, "expected_rows": 1},
            max_attempts=3,
        )
        counts["inserted" if inserted else "existing"] += 1
    return counts


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _role_endpoint(role: str) -> dict:
    prefix = f"SCICODE_{role.upper()}_"
    values = {
        "base_url": os.environ.get(prefix + "BASE_URL"),
        "api_key": os.environ.get(prefix + "API_KEY"),
    }
    if not all(values.values()):
        raise ValueError(f"set {prefix}BASE_URL and {prefix}API_KEY")
    return values


def process_seed(
    lease,
    *,
    queue: WorkQueue,
    output_root: Path,
    recipe: dict,
    slots: LocalSlotManager,
    worker: str,
    chat_fn=llm.chat,
    lease_seconds: float = 14_400,
    kimi_capacity: MetricsCapacityController | None = None,
) -> dict:
    if lease.payload["recipe_hash"] != canonical_hash(recipe):
        raise ValueError("leased job belongs to a different recipe")
    seed = lease.payload["seed"]
    shard = output_root / "shards" / lease.job_id
    shard.mkdir(parents=True, exist_ok=True)
    kimi = ScheduledChat(
        slots, chat_fn, worker=worker, resource="kimi",
        capacity_controller=kimi_capacity,
    )
    qwen = ScheduledChat(slots, chat_fn, worker=worker, resource="qwen")
    kimi_endpoint = _role_endpoint("kimi")
    qwen_endpoint = _role_endpoint("qwen")
    task_path = shard / "task.json"
    with LeaseHeartbeat(queue, lease, lease_seconds):
        if task_path.exists():
            task = validate_task(_read_json(task_path))
            if task["seed_id"] != seed["seed_id"]:
                raise ValueError("saved task belongs to another seed")
        else:
            author_sample = sample_parameters(seed["seed_id"], 0, recipe["master_seed"])
            task = compose_task(
                seed,
                chat_fn=kimi,
                model=recipe["author_model"],
                **kimi_endpoint,
                temperature=author_sample["temperature"],
                top_p=author_sample["top_p"],
                request_seed=(
                    author_sample["request_seed"] if recipe["send_seed"] else None
                ),
                prompt_variant=author_sample["request_seed"] % 3,
                max_tokens=recipe["author_max_tokens"],
                timeout=recipe["timeout"],
            )
            atomic_json(task_path, task)
        traces = []
        audits = []
        rows = []
        for attempt in range(recipe["attempts"]):
            trace_path = shard / f"trace-{attempt}.json"
            if trace_path.exists():
                trace = _read_json(trace_path)
            else:
                trace = collect_trace(
                    task,
                    attempt=attempt,
                    sample=sample_parameters(
                        task["task_id"], attempt, recipe["master_seed"]
                    ),
                    chat_fn=qwen,
                    model=recipe["solver_model"],
                    **qwen_endpoint,
                    send_seed=recipe["send_seed"],
                    max_tokens=recipe["solver_max_tokens"],
                    timeout=recipe["timeout"],
                )
                atomic_json(trace_path, trace)
            traces.append(trace)
            audit_path = shard / f"audit-{attempt}-{AUDIT_POLICY}.json"
            if audit_path.exists():
                audit = _read_json(audit_path)
            else:
                audit = audit_trace(
                    task,
                    trace,
                    chat_fn=kimi,
                    model=recipe["audit_model"],
                    **kimi_endpoint,
                    max_tokens=recipe["audit_max_tokens"],
                    timeout=recipe["timeout"],
                )
                atomic_json(audit_path, audit)
            audits.append(audit)
            row = training_row(task, trace, audit)
            if row is not None:
                rows.append(row)
        atomic_jsonl(shard / "traces.jsonl", traces)
        atomic_jsonl(shard / "audits.jsonl", audits)
        atomic_jsonl(shard / "sft.jsonl", rows)
        result = {
            "job_id": lease.job_id,
            "seed_id": seed["seed_id"],
            "task_id": task["task_id"],
            "traces": len(traces),
            "accepted": len(rows),
            "sft_rows": len(rows),
            "shard": str(shard),
        }
        atomic_json(shard / "report.json", result)
        return result


def run_workers(
    queue: WorkQueue,
    *,
    output_root: Path,
    recipe: dict,
    workers: int,
    kimi_slots: int,
    qwen_slots: int,
    chat_fn=llm.chat,
    kimi_metrics_url: str | None = None,
    kimi_min_slots: int = 1,
    kimi_service_capacity: int = 1792,
) -> list[dict]:
    if min(workers, kimi_slots, qwen_slots) < 1:
        raise ValueError("workers and model slots must be positive")
    if kimi_min_slots < 1 or kimi_min_slots > kimi_service_capacity:
        raise ValueError("kimi_min_slots must be within service capacity")
    slots = LocalSlotManager()
    slots.configure_slots("kimi", kimi_slots)
    slots.configure_slots("qwen", qwen_slots)
    kimi_capacity = (
        MetricsCapacityController(
            kimi_metrics_url,
            minimum=kimi_min_slots,
            maximum=kimi_service_capacity,
        ) if kimi_metrics_url else None
    )
    condition = threading.Condition()
    host = socket.gethostname()

    def one(index: int) -> dict:
        worker = f"{host}:{os.getpid()}:{index}:{threading.get_ident()}"
        stats = {"done": 0, "failed": 0, "retried": 0}
        while True:
            with condition:
                lease = queue.claim(worker, kinds=(JOB_KIND,))
                if lease is None:
                    counts = queue.counts()
                    if not counts.get("pending", 0) and not counts.get("leased", 0):
                        return stats
                    condition.wait(5.0)
                    continue
            try:
                result = process_seed(
                    lease,
                    queue=queue,
                    output_root=output_root,
                    recipe=recipe,
                    slots=slots,
                    worker=worker,
                    chat_fn=chat_fn,
                    kimi_capacity=kimi_capacity,
                )
            except Exception as exc:
                with condition:
                    status = queue.fail(
                        lease,
                        f"{type(exc).__name__}: {exc}",
                        retry_delay=min(300.0, 15.0 * 2 ** (lease.attempt - 1)),
                    )
                    stats["retried" if status == "pending" else "failed"] += 1
                    condition.notify_all()
            else:
                with condition:
                    queue.complete(lease, result)
                    stats["done"] += 1
                    condition.notify_all()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, range(workers)))


def aggregate(queue: WorkQueue, output_path: Path, train_output: Path | None = None) -> dict:
    rows = []
    seen = set()
    for completed in queue.completed_results(JOB_KIND):
        shard = Path(completed["result"]["shard"])
        for row in read_jsonl(shard / "sft.jsonl"):
            if row["trace_id"] in seen:
                raise ValueError(f"duplicate trace_id: {row['trace_id']}")
            seen.add(row["trace_id"])
            rows.append(row)
    atomic_jsonl(output_path, rows)
    if train_output is not None:
        atomic_jsonl(train_output, [training_projection(row) for row in rows])
    return {
        "sft_rows": len(rows),
        "output": str(output_path),
        "train_output": str(train_output) if train_output else None,
        "jobs": len(queue.completed_results(JOB_KIND)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--catalog", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--repository-limit", type=int)
    prepare.add_argument("--seeds-per-repository", type=int, default=3)
    prepare.add_argument("--master-seed", type=int, default=1)

    author = commands.add_parser("author-smoke")
    author.add_argument("--seeds", type=Path, required=True)
    author.add_argument("--index", type=int, default=0)
    author.add_argument("--out", type=Path, required=True)
    author.add_argument("--model", default="Kimi-K3")
    author.add_argument("--master-seed", type=int, default=1)
    author.add_argument("--max-tokens", type=int, default=65535)
    author.add_argument("--timeout", type=int, default=2400)

    trace_smoke = commands.add_parser("trace-smoke")
    trace_smoke.add_argument("--task", type=Path, required=True)
    trace_smoke.add_argument("--out", type=Path, required=True)
    trace_smoke.add_argument("--solver-model", required=True)
    trace_smoke.add_argument("--audit-model", default="Kimi-K3")
    trace_smoke.add_argument("--attempts", type=int, default=2)
    trace_smoke.add_argument("--master-seed", type=int, default=1)
    trace_smoke.add_argument("--solver-max-tokens", type=int, default=32768)
    trace_smoke.add_argument("--audit-max-tokens", type=int, default=32768)
    trace_smoke.add_argument("--timeout", type=int, default=2400)
    trace_smoke.add_argument("--send-seed", action="store_true")

    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("--db", type=Path, required=True)
    enqueue.add_argument("--seeds", type=Path, required=True)
    enqueue.add_argument("--recipe", type=Path, required=True)
    enqueue.add_argument("--author-model", required=True)
    enqueue.add_argument("--solver-model", required=True)
    enqueue.add_argument("--audit-model", required=True)
    enqueue.add_argument("--attempts", type=int, default=2)
    enqueue.add_argument("--master-seed", type=int, default=1)
    enqueue.add_argument("--author-max-tokens", type=int, default=65535)
    enqueue.add_argument("--solver-max-tokens", type=int, default=131072)
    enqueue.add_argument("--audit-max-tokens", type=int, default=32768)
    enqueue.add_argument("--timeout", type=int, default=2400)
    enqueue.add_argument("--send-seed", action="store_true")

    run = commands.add_parser("run")
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--recipe", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--kimi-slots", type=int, default=8)
    run.add_argument("--qwen-slots", type=int, default=8)
    run.add_argument("--kimi-metrics-url")
    run.add_argument("--kimi-min-slots", type=int, default=1)
    run.add_argument("--kimi-service-capacity", type=int, default=1792)

    status = commands.add_parser("status")
    status.add_argument("--db", type=Path, required=True)

    combine = commands.add_parser("aggregate")
    combine.add_argument("--db", type=Path, required=True)
    combine.add_argument("--out", type=Path, required=True)
    combine.add_argument("--train-out", type=Path)

    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_seeds(
            args.catalog, args.out,
            repository_limit=args.repository_limit,
            seeds_per_repository=args.seeds_per_repository,
            master_seed=args.master_seed,
        )
    elif args.command == "author-smoke":
        seed = read_jsonl(args.seeds)[args.index]
        sample = sample_parameters(seed["seed_id"], 0, args.master_seed)
        task = compose_task(
            seed, model=args.model, **_role_endpoint("kimi"),
            temperature=sample["temperature"], top_p=sample["top_p"],
            request_seed=None, prompt_variant=sample["request_seed"] % 3,
            max_tokens=args.max_tokens, timeout=args.timeout,
        )
        atomic_json(args.out, task)
        result = {"task_id": task["task_id"], "question": task["question"],
                  "output": str(args.out)}
    elif args.command == "trace-smoke":
        task = validate_task(_read_json(args.task))
        args.out.mkdir(parents=True, exist_ok=True)
        traces, audits, rows = [], [], []
        for attempt in range(args.attempts):
            trace_path = args.out / f"trace-{attempt}.json"
            trace = _read_json(trace_path) if trace_path.exists() else collect_trace(
                task, attempt=attempt,
                sample=sample_parameters(task["task_id"], attempt, args.master_seed),
                model=args.solver_model, **_role_endpoint("qwen"),
                send_seed=args.send_seed, max_tokens=args.solver_max_tokens,
                timeout=args.timeout,
            )
            if not trace_path.exists():
                atomic_json(trace_path, trace)
            traces.append(trace)
            audit_path = args.out / f"audit-{attempt}-{AUDIT_POLICY}.json"
            audit = _read_json(audit_path) if audit_path.exists() else audit_trace(
                task, trace, model=args.audit_model, **_role_endpoint("kimi"),
                max_tokens=args.audit_max_tokens, timeout=args.timeout,
            )
            if not audit_path.exists():
                atomic_json(audit_path, audit)
            audits.append(audit)
            row = training_row(task, trace, audit)
            if row is not None:
                rows.append(row)
        atomic_jsonl(args.out / "traces.jsonl", traces)
        atomic_jsonl(args.out / "audits.jsonl", audits)
        atomic_jsonl(args.out / "native-sft.jsonl", rows)
        atomic_jsonl(args.out / "train.jsonl",
                     [training_projection(row) for row in rows])
        result = {"traces": len(traces), "accepted": len(rows),
                  "output": str(args.out)}
    elif args.command == "enqueue":
        result = enqueue_seeds(
            WorkQueue(args.db), args.seeds, args.recipe, make_recipe(args)
        )
    elif args.command == "run":
        recipe = _read_json(args.recipe)
        if recipe.get("schema_version") != RECIPE_SCHEMA:
            raise ValueError("recipe schema mismatch")
        queue = WorkQueue(args.db)
        workers = run_workers(
            queue,
            output_root=args.output_root,
            recipe=recipe,
            workers=args.workers,
            kimi_slots=args.kimi_slots,
            qwen_slots=args.qwen_slots,
            kimi_metrics_url=args.kimi_metrics_url,
            kimi_min_slots=args.kimi_min_slots,
            kimi_service_capacity=args.kimi_service_capacity,
        )
        result = {"workers": workers, "queue": queue.counts()}
    elif args.command == "status":
        queue = WorkQueue(args.db)
        result = {
            "queue": queue.counts(),
            "progress": queue.row_progress(kinds=(JOB_KIND,)),
        }
    else:
        result = aggregate(WorkQueue(args.db), args.out, args.train_out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
