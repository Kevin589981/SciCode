import concurrent.futures as futures
import contextlib
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from factory.reasoning.queue import (
    MetricsCapacityController,
    QueueError,
    ScheduledChat,
    WorkQueue,
    parse_active_requests,
)


class ReasoningQueueTests(unittest.TestCase):
    def test_metrics_capacity_subtracts_local_activity_and_has_a_floor(self):
        metrics = """# HELP ignored
smg_worker_requests_active{worker="a"} 600
smg_worker_requests_active{worker="b"} 900
other_metric{worker="a"} 99
"""
        self.assertEqual(parse_active_requests(metrics), 1500)
        controller = MetricsCapacityController(
            "http://metrics.test",
            minimum=500,
            maximum=1792,
            refresh_interval=30,
            fetch_fn=lambda: metrics,
        )
        controller.refresh()
        self.assertEqual(controller.limit(local_active=300), 592)
        self.assertEqual(controller.limit(local_active=0), 500)

    def test_target_claims_reserve_expected_rows_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkQueue(Path(td) / "target.sqlite3")
            for index in range(4):
                queue.enqueue("repo", str(index), {"expected_rows": 3})
            first = queue.claim("one", kinds=("repo",), target_rows=5)
            second = queue.claim("two", kinds=("repo",), target_rows=5)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNone(queue.claim("three", kinds=("repo",), target_rows=5))
            self.assertEqual(
                queue.row_progress(kinds=("repo",)),
                {"completed": 0, "reserved": 6},
            )
            queue.complete(first, {"sft_rows": 2})
            queue.complete(second, {"sft_rows": 3})
            self.assertEqual(
                queue.row_progress(kinds=("repo",)),
                {"completed": 5, "reserved": 0},
            )

    def test_portable_journal_default_and_immutable_explicit_mode(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "jobs.sqlite3"
            WorkQueue(path)
            with contextlib.closing(sqlite3.connect(path)) as connection:
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.casefold(), "delete")
            with self.assertRaisesRegex(QueueError, "already uses"):
                WorkQueue(path, journal_mode="WAL")

    def test_concurrent_claims_are_unique(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkQueue(Path(td) / "jobs.sqlite3")
            for index in range(40):
                queue.enqueue("repo", f"repo-{index}", {"index": index})
            claimed = []
            claimed_lock = threading.Lock()

            def worker(number):
                local = WorkQueue(queue.path)
                while True:
                    lease = local.claim(f"worker-{number}", lease_seconds=10)
                    if lease is None:
                        return
                    with claimed_lock:
                        claimed.append(lease.job_id)
                    local.complete(lease, {"ok": True})

            with futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(worker, range(8)))
            self.assertEqual(len(claimed), 40)
            self.assertEqual(len(set(claimed)), 40)
            self.assertEqual(queue.counts(), {"done": 40})

    def test_expired_lease_is_recovered_without_duplicate_live_claim(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkQueue(Path(td) / "jobs.sqlite3")
            queue.enqueue("repo", "one", {"repo": "one"}, max_attempts=2)
            first = queue.claim("first", lease_seconds=0.02)
            self.assertIsNotNone(first)
            self.assertIsNone(queue.claim("second", lease_seconds=1))
            time.sleep(0.03)
            second = queue.claim("second", lease_seconds=1)
            self.assertIsNotNone(second)
            self.assertEqual(first.job_id, second.job_id)
            self.assertEqual(second.attempt, 2)
            queue.complete(second, {"recovered": True})

    def test_scheduled_chat_obeys_global_slot_limit(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkQueue(Path(td) / "jobs.sqlite3")
            queue.configure_slots("llm", 2)
            active = 0
            maximum = 0
            lock = threading.Lock()

            def fake_chat(messages, **kwargs):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
                return {"choices": [{"message": {"content": "ok"}}]}

            scheduled = ScheduledChat(queue, fake_chat, worker="test")
            with futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(
                    pool.map(
                        lambda _: scheduled([], timeout=1),
                        range(12),
                    )
                )
            self.assertEqual(maximum, 2)
            self.assertEqual(len(results), 12)

    def test_slot_wait_timeout_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkQueue(Path(td) / "jobs.sqlite3")
            queue.configure_slots("llm", 1)
            held = queue.acquire_slot("llm", "holder", lease_seconds=10)
            with self.assertRaisesRegex(QueueError, "timed out"):
                queue.acquire_slot(
                    "llm",
                    "blocked",
                    lease_seconds=1,
                    wait_timeout=0.02,
                    poll_interval=0.005,
                )
            queue.release_slot(held)

    def test_full_slot_pool_does_not_require_sqlite_writer_lock(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "jobs.sqlite3"
            queue = WorkQueue(path, busy_timeout=0.01)
            queue.configure_slots("llm", 1)
            held = queue.acquire_slot("llm", "holder", lease_seconds=10)
            with contextlib.closing(sqlite3.connect(path, timeout=0.01)) as writer:
                writer.execute("BEGIN IMMEDIATE")
                with self.assertRaisesRegex(QueueError, "timed out waiting"):
                    queue.acquire_slot(
                        "llm",
                        "blocked",
                        lease_seconds=1,
                        wait_timeout=0.03,
                        poll_interval=0.01,
                    )
                writer.execute("ROLLBACK")
            queue.release_slot(held)

    def test_slot_context_renews_past_initial_lease(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkQueue(Path(td) / "queue.sqlite3")
            queue.configure_slots("repository", 1)
            with queue.slot("repository", "holder", lease_seconds=0.08):
                time.sleep(0.16)
                with self.assertRaises(QueueError):
                    queue.acquire_slot(
                        "repository",
                        "contender",
                        lease_seconds=1,
                        wait_timeout=0.04,
                        poll_interval=0.005,
                    )
            acquired = queue.acquire_slot(
                "repository", "after-release", lease_seconds=1, wait_timeout=0.1
            )
            self.assertTrue(queue.release_slot(acquired))


if __name__ == "__main__":
    unittest.main()
