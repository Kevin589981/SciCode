"""SQLite-backed leases and global resource slots for batch production.

The queue is intentionally local-machine infrastructure. SQLite WAL provides an
atomic coordination point for multiple worker processes on one host, while each
repository writes to its own artifact shard. A multi-node deployment should use
the same lease protocol over a network database rather than place this SQLite
file on an arbitrary network filesystem.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


class QueueError(RuntimeError):
    """The durable work queue cannot complete an atomic operation."""


@dataclass(frozen=True)
class JobLease:
    job_id: str
    kind: str
    key: str
    payload: dict
    worker: str
    attempt: int


@dataclass(frozen=True)
class SlotLease:
    resource: str
    slot: int
    holder: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    job_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_eligible REAL NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires REAL,
    result_json TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(kind, job_key)
);
CREATE INDEX IF NOT EXISTS jobs_claim_idx
    ON jobs(status, next_eligible, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS job_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event TEXT NOT NULL,
    worker TEXT,
    detail TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_slots (
    resource TEXT NOT NULL,
    slot INTEGER NOT NULL,
    holder TEXT,
    lease_expires REAL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(resource, slot)
);
CREATE TABLE IF NOT EXISTS resource_limits (
    resource TEXT PRIMARY KEY,
    slot_count INTEGER NOT NULL,
    configured_at REAL NOT NULL
);
"""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def job_id_for(kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{key}".encode()).hexdigest()[:24]
    return f"{kind}-{digest}"


class WorkQueue:
    """Small durable queue with atomic leases and process-global slot pools."""

    def __init__(self, path: Path, *, busy_timeout: float = 30.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout = busy_timeout
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout * 1000)}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def initialize(self) -> None:
        with contextlib.closing(self._connect()) as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        job_id: str,
        event: str,
        worker: str | None = None,
        detail: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO job_events(job_id,event,worker,detail,created_at) "
            "VALUES(?,?,?,?,?)",
            (job_id, event, worker, detail, time.time()),
        )

    def enqueue(
        self,
        kind: str,
        key: str,
        payload: dict,
        *,
        priority: int = 0,
        max_attempts: int = 3,
    ) -> tuple[str, bool]:
        if not kind or not key or max_attempts < 1:
            raise QueueError("kind/key must be nonempty and max_attempts positive")
        job_id = job_id_for(kind, key)
        now = time.time()
        with contextlib.closing(self._connect()) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO jobs("
                "job_id,kind,job_key,payload_json,priority,max_attempts,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    kind,
                    key,
                    _canonical(payload),
                    priority,
                    max_attempts,
                    now,
                    now,
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                self._event(connection, job_id, "enqueued")
        return job_id, inserted

    def _recover_expired(self, connection: sqlite3.Connection, now: float) -> None:
        expired = connection.execute(
            "SELECT job_id,attempts,max_attempts,lease_owner FROM jobs "
            "WHERE status='leased' AND lease_expires < ?",
            (now,),
        ).fetchall()
        for row in expired:
            status = "pending" if row["attempts"] < row["max_attempts"] else "failed"
            connection.execute(
                "UPDATE jobs SET status=?,lease_owner=NULL,lease_expires=NULL,"
                "updated_at=?,last_error=COALESCE(last_error,'lease expired') "
                "WHERE job_id=?",
                (status, now, row["job_id"]),
            )
            self._event(
                connection,
                row["job_id"],
                "lease_expired",
                row["lease_owner"],
                status,
            )

    def claim(
        self,
        worker: str,
        *,
        kinds: tuple[str, ...] | None = None,
        lease_seconds: float = 14_400,
    ) -> JobLease | None:
        if not worker or lease_seconds <= 0:
            raise QueueError("worker must be nonempty and lease_seconds positive")
        now = time.time()
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._recover_expired(connection, now)
                parameters: list[object] = [now]
                kind_clause = ""
                if kinds:
                    marks = ",".join("?" for _ in kinds)
                    kind_clause = f" AND kind IN ({marks})"
                    parameters.extend(kinds)
                row = connection.execute(
                    "SELECT * FROM jobs WHERE status='pending' "
                    "AND next_eligible <= ? AND attempts < max_attempts"
                    + kind_clause
                    + " ORDER BY priority DESC,created_at,job_id LIMIT 1",
                    parameters,
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                expires = now + lease_seconds
                updated = connection.execute(
                    "UPDATE jobs SET status='leased',attempts=attempts+1,"
                    "lease_owner=?,lease_expires=?,updated_at=? "
                    "WHERE job_id=? AND status='pending'",
                    (worker, expires, now, row["job_id"]),
                ).rowcount
                if updated != 1:
                    raise QueueError("atomic claim lost its selected job")
                self._event(connection, row["job_id"], "claimed", worker)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return JobLease(
            job_id=row["job_id"],
            kind=row["kind"],
            key=row["job_key"],
            payload=json.loads(row["payload_json"]),
            worker=worker,
            attempt=row["attempts"] + 1,
        )

    def renew(self, lease: JobLease, *, lease_seconds: float) -> bool:
        now = time.time()
        with contextlib.closing(self._connect()) as connection:
            updated = connection.execute(
                "UPDATE jobs SET lease_expires=?,updated_at=? WHERE job_id=? "
                "AND status='leased' AND lease_owner=?",
                (now + lease_seconds, now, lease.job_id, lease.worker),
            ).rowcount
        return updated == 1

    def complete(self, lease: JobLease, result: dict) -> None:
        now = time.time()
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                updated = connection.execute(
                    "UPDATE jobs SET status='done',result_json=?,"
                    "lease_owner=NULL,lease_expires=NULL,updated_at=? "
                    "WHERE job_id=? AND status='leased' AND lease_owner=?",
                    (_canonical(result), now, lease.job_id, lease.worker),
                ).rowcount
                if updated != 1:
                    raise QueueError(f"job lease is no longer owned: {lease.job_id}")
                self._event(connection, lease.job_id, "completed", lease.worker)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def fail(
        self,
        lease: JobLease,
        error: str,
        *,
        retryable: bool = True,
        retry_delay: float = 60.0,
    ) -> str:
        now = time.time()
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT attempts,max_attempts FROM jobs WHERE job_id=? "
                    "AND status='leased' AND lease_owner=?",
                    (lease.job_id, lease.worker),
                ).fetchone()
                if row is None:
                    raise QueueError(f"job lease is no longer owned: {lease.job_id}")
                will_retry = retryable and row["attempts"] < row["max_attempts"]
                status = "pending" if will_retry else "failed"
                connection.execute(
                    "UPDATE jobs SET status=?,next_eligible=?,last_error=?,"
                    "lease_owner=NULL,lease_expires=NULL,updated_at=? WHERE job_id=?",
                    (
                        status,
                        now + max(0.0, retry_delay) if will_retry else now,
                        error[:4000],
                        now,
                        lease.job_id,
                    ),
                )
                self._event(
                    connection, lease.job_id, status, lease.worker, error[:1000]
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return status

    def counts(self) -> dict[str, int]:
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def completed_results(self, kind: str | None = None) -> list[dict]:
        query = "SELECT job_id,job_key,result_json FROM jobs WHERE status='done'"
        parameters: tuple[object, ...] = ()
        if kind:
            query += " AND kind=?"
            parameters = (kind,)
        query += " ORDER BY job_id"
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "key": row["job_key"],
                "result": json.loads(row["result_json"]),
            }
            for row in rows
        ]

    def configure_slots(self, resource: str, count: int) -> None:
        if not resource or count < 1:
            raise QueueError("resource must be nonempty and count positive")
        now = time.time()
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                configured = connection.execute(
                    "SELECT slot_count FROM resource_limits WHERE resource=?",
                    (resource,),
                ).fetchone()
                if configured is not None and configured["slot_count"] != count:
                    raise QueueError(
                        f"resource {resource!r} is already configured with "
                        f"{configured['slot_count']} slots, not {count}"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO resource_limits("
                    "resource,slot_count,configured_at) VALUES(?,?,?)",
                    (resource, count, now),
                )
                for slot in range(count):
                    connection.execute(
                        "INSERT OR IGNORE INTO resource_slots("
                        "resource,slot,updated_at) VALUES(?,?,?)",
                        (resource, slot, now),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def acquire_slot(
        self,
        resource: str,
        holder: str,
        *,
        lease_seconds: float,
        wait_timeout: float = 86_400,
        poll_interval: float = 0.25,
    ) -> SlotLease:
        deadline = time.monotonic() + wait_timeout
        while True:
            now = time.time()
            with contextlib.closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    configured = connection.execute(
                        "SELECT slot_count FROM resource_limits WHERE resource=?",
                        (resource,),
                    ).fetchone()
                    if configured is None:
                        raise QueueError(
                            f"resource {resource!r} has no configured slots"
                        )
                    connection.execute(
                        "UPDATE resource_slots SET holder=NULL,lease_expires=NULL,"
                        "updated_at=? WHERE resource=? AND lease_expires < ?",
                        (now, resource, now),
                    )
                    row = connection.execute(
                        "SELECT slot FROM resource_slots WHERE resource=? "
                        "AND holder IS NULL ORDER BY slot LIMIT 1",
                        (resource,),
                    ).fetchone()
                    if row is not None:
                        updated = connection.execute(
                            "UPDATE resource_slots SET holder=?,lease_expires=?,"
                            "updated_at=? WHERE resource=? AND slot=? "
                            "AND holder IS NULL",
                            (
                                holder,
                                now + lease_seconds,
                                now,
                                resource,
                                row["slot"],
                            ),
                        ).rowcount
                        if updated == 1:
                            connection.execute("COMMIT")
                            return SlotLease(resource, row["slot"], holder)
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            if time.monotonic() >= deadline:
                raise QueueError(f"timed out waiting for {resource!r} slot")
            time.sleep(poll_interval)

    def release_slot(self, lease: SlotLease) -> bool:
        with contextlib.closing(self._connect()) as connection:
            updated = connection.execute(
                "UPDATE resource_slots SET holder=NULL,lease_expires=NULL,"
                "updated_at=? WHERE resource=? AND slot=? AND holder=?",
                (time.time(), lease.resource, lease.slot, lease.holder),
            ).rowcount
        return updated == 1

    def renew_slot(self, lease: SlotLease, *, lease_seconds: float) -> bool:
        if lease_seconds <= 0:
            raise QueueError("slot lease_seconds must be positive")
        now = time.time()
        with contextlib.closing(self._connect()) as connection:
            updated = connection.execute(
                "UPDATE resource_slots SET lease_expires=?,updated_at=? "
                "WHERE resource=? AND slot=? AND holder=?",
                (
                    now + lease_seconds,
                    now,
                    lease.resource,
                    lease.slot,
                    lease.holder,
                ),
            ).rowcount
        return updated == 1

    @contextlib.contextmanager
    def slot(
        self,
        resource: str,
        holder_prefix: str,
        *,
        lease_seconds: float,
        wait_timeout: float = 86_400,
    ):
        holder = f"{holder_prefix}:{threading.get_ident()}:{uuid.uuid4().hex[:12]}"
        lease = self.acquire_slot(
            resource,
            holder,
            lease_seconds=lease_seconds,
            wait_timeout=wait_timeout,
        )
        stop = threading.Event()
        lost = []
        interval = max(0.05, lease_seconds / 3)

        def heartbeat() -> None:
            while not stop.wait(interval):
                try:
                    if not self.renew_slot(lease, lease_seconds=lease_seconds):
                        lost.append("ownership was lost")
                        return
                except Exception as exc:
                    lost.append(f"heartbeat failed: {type(exc).__name__}: {exc}")
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        body_failed = False
        try:
            yield lease
        except BaseException:
            body_failed = True
            raise
        finally:
            stop.set()
            thread.join(timeout=interval + 1)
            self.release_slot(lease)
            if lost and not body_failed:
                raise QueueError(f"resource slot {resource!r} became unsafe: {lost[0]}")


class LeaseHeartbeat:
    """Renew a long repository job while its nested stages are running."""

    def __init__(self, queue: WorkQueue, lease: JobLease, lease_seconds: float):
        self.queue = queue
        self.lease = lease
        self.lease_seconds = lease_seconds
        self.interval = max(1.0, lease_seconds / 3)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if not self.queue.renew(self.lease, lease_seconds=self.lease_seconds):
                return

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self._stop.set()
        self._thread.join(timeout=self.interval + 1)


class ScheduledChat:
    """Wrap a chat callable with a SQLite-backed global concurrency budget."""

    def __init__(
        self,
        queue: WorkQueue,
        chat_fn,
        *,
        worker: str,
        resource: str = "llm",
        acquire_timeout: float = 86_400,
    ):
        self.queue = queue
        self.chat_fn = chat_fn
        self.worker = worker
        self.resource = resource
        self.acquire_timeout = acquire_timeout

    def __call__(self, messages, **kwargs):
        request_timeout = float(kwargs.get("timeout", 900))
        with self.queue.slot(
            self.resource,
            self.worker,
            lease_seconds=request_timeout + 300,
            wait_timeout=self.acquire_timeout,
        ):
            return self.chat_fn(messages, **kwargs)
