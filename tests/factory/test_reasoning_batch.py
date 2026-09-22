import json
import tempfile
import threading
import unittest
from pathlib import Path

from factory.reasoning.batch import (
    aggregate_sft,
    enqueue_catalog,
    run_local_workers,
)
from factory.reasoning.queue import WorkQueue


def repository(index):
    return {
        "repo_id": str(index),
        "full_name": f"science/repo-{index}",
        "url": f"https://github.com/science/repo-{index}",
        "clone_url": f"https://github.com/science/repo-{index}.git",
        "pushed_at": "2026-01-01T00:00:00Z",
        "stars": 100 - index,
    }


def sft_row(index):
    return {
        "schema_version": "scicode-reasoning-sft-v1",
        "trace_id": f"trace-{index}",
        "task_name": f"task-{index}",
        "task_hash": f"hash-{index}",
        "messages": [{"role": "assistant", "reasoning_content": f"r{index}"}],
        "automatic_review": {"mode": "single_model"},
    }


class ReasoningBatchTests(unittest.TestCase):
    def test_enqueue_uses_explicit_target_reservation_estimate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            queue = WorkQueue(root / "batch.sqlite3")
            catalog = root / "catalog.jsonl"
            catalog.write_text(json.dumps(repository(1)) + "\n", encoding="utf-8")
            enqueue_catalog(
                queue,
                catalog,
                recipe={"tasks_per_repo": 16, "reservation_rows_per_repo": 5},
            )
            lease = queue.claim("worker", target_rows=1000)
            self.assertIsNotNone(lease)
            self.assertEqual(lease.payload["expected_rows"], 5)

    def test_workers_make_isolated_shards_and_aggregate_deterministically(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            queue = WorkQueue(root / "batch.sqlite3")
            queue.configure_slots("llm", 2)
            queue.configure_slots("repository", 2)
            catalog = root / "catalog.jsonl"
            catalog.write_text(
                "".join(json.dumps(repository(i)) + "\n" for i in range(12)),
                encoding="utf-8",
            )
            first = enqueue_catalog(queue, catalog, recipe={"version": 1})
            second = enqueue_catalog(queue, catalog, recipe={"version": 1})
            self.assertEqual(first, {"inserted": 12, "existing": 0})
            self.assertEqual(second, {"inserted": 0, "existing": 12})
            changed = enqueue_catalog(queue, catalog, recipe={"version": 2})
            self.assertEqual(changed, {"inserted": 12, "existing": 0})
            processed = []
            lock = threading.Lock()

            def factory(_worker):
                def process(lease):
                    index = int(lease.payload["repository"]["repo_id"])
                    shard = root / "shards" / lease.job_id
                    shard.mkdir(parents=True)
                    sft = shard / "sft.jsonl"
                    sft.write_text(json.dumps(sft_row(index)) + "\n", encoding="utf-8")
                    tasks = shard / "tasks.jsonl"
                    tasks.write_text(
                        json.dumps({"task_id": f"task-{index}"}) + "\n",
                        encoding="utf-8",
                    )
                    with lock:
                        processed.append(lease.job_id)
                    return {
                        "status": "complete",
                        "sft": str(sft),
                        "sft_rows": 1,
                        "artifacts": {"tasks": str(tasks)},
                    }

                return process

            run_local_workers(queue, workers=6, processor_factory=factory)
            self.assertEqual(len(processed), 24)
            self.assertEqual(len(set(processed)), 24)
            output = root / "sft.jsonl"
            report = aggregate_sft(queue, output)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(report["sft_rows"], 12)
            self.assertEqual(
                report["quality_status"], "automatic_single_model_reviewed"
            )
            self.assertEqual(report["quality_inputs"]["tasks"]["rows"], 12)
            self.assertEqual(
                [row["trace_id"] for row in rows],
                sorted(row["trace_id"] for row in rows),
            )

    def test_aggregate_refuses_unfinished_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            queue = WorkQueue(root / "batch.sqlite3")
            queue.enqueue("reasoning_repository", "pending", repository(1))
            with self.assertRaisesRegex(Exception, "unfinished"):
                aggregate_sft(queue, root / "sft.jsonl")

    def test_target_stops_claiming_and_aggregates_exactly_requested_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            queue = WorkQueue(root / "batch.sqlite3")
            catalog = root / "catalog.jsonl"
            catalog.write_text(
                "".join(json.dumps(repository(i)) + "\n" for i in range(8)),
                encoding="utf-8",
            )
            enqueue_catalog(queue, catalog, recipe={"tasks_per_repo": 2})

            def factory(_worker):
                def process(lease):
                    index = int(lease.payload["repository"]["repo_id"])
                    shard = root / "target-shards" / lease.job_id
                    shard.mkdir(parents=True)
                    sft = shard / "sft.jsonl"
                    sft.write_text(json.dumps(sft_row(index)) + "\n", encoding="utf-8")
                    return {
                        "status": "complete",
                        "sft": str(sft),
                        "sft_rows": 1,
                        "artifacts": {},
                    }

                return process

            run_local_workers(
                queue,
                workers=4,
                processor_factory=factory,
                target_sft_rows=3,
            )
            report = aggregate_sft(
                queue,
                root / "accepted.jsonl",
                target_sft_rows=3,
            )
            self.assertEqual(report["sft_rows"], 3)
            self.assertGreater(queue.counts().get("pending", 0), 0)


if __name__ == "__main__":
    unittest.main()
