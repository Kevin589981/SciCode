"""SQLite-backed leases and global resource slots for batch production.

The queue is intentionally local-machine infrastructure. SQLite transactions
provide an atomic coordination point for multiple worker processes on one host,
while each repository writes to its own artifact shard. The portable default is
DELETE journaling; WAL is opt-in for a filesystem known to support its shared
memory semantics. A multi-node deployment should use the same lease protocol
over a network database rather than place this SQLite file on an arbitrary
network filesystem.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
import urllib.request
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


def parse_active_requests(metrics: str) -> int:
    """Sum the labeled smg active-request series used by the Kimi deployment."""
    total = 0.0
    for line in metrics.splitlines():
        if not line.startswith("smg_worker_requests_active{"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            value = float(fields[-1])
        except ValueError:
            continue
        if value >= 0:
            total += value
    return max(0, int(total))


class MetricsCapacityController:
    """Turn deployment-wide active requests into a local admission ceiling."""

    def __init__(
        self,
        url: str,
        *,
        minimum: int,
        maximum: int,
        refresh_interval: float = 30.0,
        timeout: float = 5.0,
        fetch_fn=None,
    ):
        if not url or minimum < 1 or maximum < minimum:
            raise QueueError("metrics URL and a valid minimum/maximum are required")
        if refresh_interval <= 0 or timeout <= 0:
            raise QueueError("metrics refresh interval and timeout must be positive")
        self.url = url
        self.minimum = minimum
        self.maximum = maximum
        self.refresh_interval = refresh_interval
        self.timeout = timeout
        self.fetch_fn = fetch_fn or self._fetch
        self._lock = threading.Lock()
        self._observed_total = 0
        self._updated_monotonic = -float("inf")
        self._last_error: str | None = None

    def _fetch(self) -> str:
        request = urllib.request.Request(
            self.url,
            headers={"User-Agent": "scicode-reasoning-factory"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8", errors="replace")

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._updated_monotonic < self.refresh_interval:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._updated_monotonic < self.refresh_interval:
                return
            try:
                self._observed_total = parse_active_requests(self.fetch_fn())
                self._last_error = None
            except Exception as exc:
                # A missing metric must reduce throughput, never disable admission.
                self._last_error = f"{type(exc).__name__}: {exc}"[:500]
                self._observed_total = self.maximum
            self._updated_monotonic = now

    def limit(self, local_active: int) -> int:
        """Reserve capacity after subtracting this queue from global activity."""
        external_active = max(0, self._observed_total - max(0, local_active))
        available = self.maximum - external_active
        return max(self.minimum, min(self.maximum, available))

    def snapshot(self, local_active: int = 0) -> dict:
        return {
            "url": self.url,
            "observed_total": self._observed_total,
            "local_active": max(0, local_active),
            "limit": self.limit(local_active),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "last_error": self._last_error,
        }


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

    def __init__(
        self,
        path: Path,
        *,
        busy_timeout: float = 30.0,
        journal_mode: str | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout = busy_timeout
        if journal_mode is not None:
            journal_mode = journal_mode.upper()
            if journal_mode not in {"DELETE", "TRUNCATE", "WAL"}:
                raise QueueError(f"unsupported SQLite journal mode: {journal_mode}")
        self.requested_journal_mode = journal_mode
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout * 1000)}")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def initialize(self) -> None:
        existed = self.path.exists() and self.path.stat().st_size > 0
        try:
            with contextlib.closing(self._connect()) as connection:
                current = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
                requested = self.requested_journal_mode
                if existed and requested and current.casefold() != requested.casefold():
                    raise QueueError(
                        f"queue already uses SQLite journal mode {current}, not "
                        f"{requested}"
                    )
                if not existed:
                    desired = requested or "DELETE"
                    actual = str(
                        connection.execute(f"PRAGMA journal_mode={desired}").fetchone()[
                            0
                        ]
                    )
                    if actual.casefold() != desired.casefold():
                        raise QueueError(
                            f"filesystem selected SQLite journal mode {actual}, not "
                            f"{desired}"
                        )
                connection.executescript(SCHEMA)
        except sqlite3.OperationalError as exc:
            raise QueueError(
                f"cannot initialize SQLite queue at {self.path}: {exc}. "
                "Use DELETE journaling for a shared/virtual filesystem or place "
                "a WAL queue on a proven local filesystem."
            ) from exc

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
        target_rows: int | None = None,
    ) -> JobLease | None:
        if not worker or lease_seconds <= 0:
            raise QueueError("worker must be nonempty and lease_seconds positive")
        if target_rows is not None and target_rows < 1:
            raise QueueError("target_rows must be positive")
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
                if target_rows is not None:
                    progress = self._row_progress(connection, kinds=kinds)
                    if progress["completed"] + progress["reserved"] >= target_rows:
                        connection.execute("COMMIT")
                        return None
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

    @staticmethod
    def _row_progress(
        connection: sqlite3.Connection,
        *,
        kinds: tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        parameters: list[object] = []
        kind_clause = ""
        if kinds:
            marks = ",".join("?" for _ in kinds)
            kind_clause = f" AND kind IN ({marks})"
            parameters.extend(kinds)
        rows = connection.execute(
            "SELECT status,payload_json,result_json FROM jobs "
            "WHERE status IN ('leased','done')" + kind_clause,
            parameters,
        ).fetchall()
        completed = reserved = 0
        for row in rows:
            if row["status"] == "done" and row["result_json"]:
                value = json.loads(row["result_json"]).get("sft_rows", 0)
                completed += max(0, int(value or 0))
            elif row["status"] == "leased":
                value = json.loads(row["payload_json"]).get("expected_rows", 0)
                reserved += max(0, int(value or 0))
        return {"completed": completed, "reserved": reserved}

    def row_progress(self, *, kinds: tuple[str, ...] | None = None) -> dict[str, int]:
        with contextlib.closing(self._connect()) as connection:
            return self._row_progress(connection, kinds=kinds)

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

    def configured_slots(self, resource: str) -> int:
        with contextlib.closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT slot_count FROM resource_limits WHERE resource=?",
                (resource,),
            ).fetchone()
        if row is None:
            raise QueueError(f"resource {resource!r} has no configured slots")
        return int(row["slot_count"])

    def acquire_slot(
        self,
        resource: str,
        holder: str,
        *,
        lease_seconds: float,
        wait_timeout: float = 86_400,
        poll_interval: float = 0.5,
        capacity_controller: MetricsCapacityController | None = None,
    ) -> SlotLease:
        deadline = time.monotonic() + wait_timeout
        while True:
            if capacity_controller is not None:
                capacity_controller.refresh()
            now = time.time()
            # A full pool is the normal state in a batch run. Inspect it with
            # a read transaction before taking the database's single writer
            # lock; the write transaction below still rechecks capacity.
            with contextlib.closing(self._connect()) as connection:
                configured = connection.execute(
                    "SELECT slot_count FROM resource_limits WHERE resource=?",
                    (resource,),
                ).fetchone()
                if configured is None:
                    raise QueueError(f"resource {resource!r} has no configured slots")
                active = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM resource_slots WHERE resource=? "
                        "AND holder IS NOT NULL AND lease_expires >= ?",
                        (resource, now),
                    ).fetchone()[0]
                )
                dynamic_limit = int(configured["slot_count"])
                if capacity_controller is not None:
                    dynamic_limit = min(dynamic_limit, capacity_controller.limit(active))
            if active >= dynamic_limit:
                if time.monotonic() >= deadline:
                    raise QueueError(f"timed out waiting for {resource!r} slot")
                time.sleep(poll_interval)
                continue
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
                    active = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM resource_slots WHERE resource=? "
                            "AND holder IS NOT NULL",
                            (resource,),
                        ).fetchone()[0]
                    )
                    dynamic_limit = int(configured["slot_count"])
                    if capacity_controller is not None:
                        dynamic_limit = min(
                            dynamic_limit,
                            capacity_controller.limit(active),
                        )
                    row = None
                    if active < dynamic_limit:
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
        capacity_controller: MetricsCapacityController | None = None,
    ):
        holder = f"{holder_prefix}:{threading.get_ident()}:{uuid.uuid4().hex[:12]}"
        lease = self.acquire_slot(
            resource,
            holder,
            lease_seconds=lease_seconds,
            wait_timeout=wait_timeout,
            capacity_controller=capacity_controller,
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
        capacity_controller: MetricsCapacityController | None = None,
    ):
        self.queue = queue
        self.chat_fn = chat_fn
        self.worker = worker
        self.resource = resource
        self.acquire_timeout = acquire_timeout
        self.capacity_controller = capacity_controller

    def __call__(self, messages, **kwargs):
        request_timeout = float(kwargs.get("timeout", 900))
        with self.queue.slot(
            self.resource,
            self.worker,
            lease_seconds=request_timeout + 300,
            wait_timeout=self.acquire_timeout,
            capacity_controller=self.capacity_controller,
        ):
            return self.chat_fn(messages, **kwargs)
