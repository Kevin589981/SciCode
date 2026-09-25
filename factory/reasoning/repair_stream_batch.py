"""Resume a stopped batch after switching long Kimi calls to streaming.

Run only after the old controller has stopped. A SQLite backup is written before
any queue changes; existing per-repository JSONL artifacts are never rewritten.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path

from .rollout import current_commit


def repair(
    db_path: Path,
    *,
    stopped_pid: int | None = None,
    backup_suffix: str = ".pre-stream-repair",
) -> dict:
    db_path = db_path.resolve()
    if stopped_pid is not None:
        try:
            os.kill(stopped_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError(f"old controller PID {stopped_pid} is still running")
    if (
        not backup_suffix.startswith(".")
        or "/" in backup_suffix
        or "\\" in backup_suffix
    ):
        raise ValueError("backup_suffix must be a filename suffix beginning with '.'")
    backup_path = db_path.with_name(db_path.stem + backup_suffix + ".sqlite3")
    if backup_path.exists():
        raise FileExistsError(f"backup already exists: {backup_path}")
    commit = current_commit()
    if commit == "unknown":
        raise RuntimeError("cannot identify the running factory commit")
    with contextlib.closing(
        sqlite3.connect(db_path, timeout=60)
    ) as source, contextlib.closing(sqlite3.connect(backup_path)) as backup:
        source.backup(backup)
        source.row_factory = sqlite3.Row
        source.execute("BEGIN IMMEDIATE")
        counts = Counter()
        now = time.time()
        for row in source.execute(
            "SELECT job_id,status,payload_json,result_json FROM jobs"
        ).fetchall():
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            if row["status"] == "done" and result.get("status") != "complete":
                counts["completed_rejections_preserved"] += 1
                continue
            payload = json.loads(row["payload_json"])
            recipe = payload.get("recipe") or {}
            recipe["resume_from_commit"] = recipe.get("factory_commit")
            recipe["factory_commit"] = commit
            recipe["resume_complete"] = True
            payload["recipe"] = recipe
            source.execute(
                "UPDATE jobs SET status='pending',payload_json=?,attempts=0,"
                "next_eligible=?,lease_owner=NULL,lease_expires=NULL,"
                "result_json=NULL,last_error=NULL,updated_at=? WHERE job_id=?",
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                    now,
                    row["job_id"],
                ),
            )
            counts[f"requeued_{row['status']}"] += 1
        source.execute(
            "UPDATE resource_slots SET holder=NULL,lease_expires=NULL,updated_at=? "
            "WHERE holder IS NOT NULL",
            (now,),
        )
        # This maintenance path mutates jobs outside WorkQueue. Rebuild the
        # materialized progress in the same transaction before workers resume.
        if source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_progress'"
        ).fetchone():
            source.execute("DELETE FROM job_progress")
            totals = {}
            for row in source.execute(
                "SELECT kind,status,payload_json,result_json FROM jobs "
                "WHERE status IN ('done','leased')"
            ):
                values = totals.setdefault(row['kind'], [0, 0])
                if row['status'] == 'done' and row['result_json']:
                    values[0] += max(0, int(json.loads(row['result_json']).get('sft_rows') or 0))
                elif row['status'] == 'leased':
                    values[1] += max(0, int(json.loads(row['payload_json']).get('expected_rows') or 0))
            for kind, values in totals.items():
                source.execute(
                    "INSERT INTO job_progress(kind,completed_rows,reserved_rows) VALUES(?,?,?)",
                    (kind, *values),
                )
        source.commit()
    return {"backup": str(backup_path), "factory_commit": commit, **counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--stopped-pid", type=int)
    parser.add_argument("--backup-suffix", default=".pre-stream-repair")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("Dry run. Add --execute after stopping the old controller.")
        return
    print(
        json.dumps(
            repair(
                args.db, stopped_pid=args.stopped_pid, backup_suffix=args.backup_suffix
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
