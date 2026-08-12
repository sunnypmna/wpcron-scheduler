#!/usr/bin/env python3
"""
WP Cron Scheduler repositories.

Repositories contain persistence operations only.
They do not decide when jobs should run, which jobs are fair, or how many
workers should exist. Those decisions belong to the future scheduler brain.
"""

from __future__ import annotations

import time
from typing import Iterable, Optional

from database import Database
from models import Execution, Job, QueueItem


VALID_QUEUE_STATUSES = {
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "RETRY",
    "CANCELLED",
}


def _now() -> int:
    return int(time.time())


def _row_to_job(row) -> Job:
    return Job(
        id=row["id"],
        job_key=row["job_key"],
        cpanel_account=row["cpanel_account"],
        uid=row["uid"],
        gid=row["gid"],
        home_directory=row["home_directory"],
        domain=row["domain"],
        wp_cron_path=row["wp_cron_path"],
        minute=row["minute"],
        hour=row["hour"],
        day=row["day"],
        month=row["month"],
        weekday=row["weekday"],
        schedule_expression=row["schedule_expression"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen=row["last_seen"],
        last_triggered=row["last_triggered"],
        last_queued=row["last_queued"],
        last_started=row["last_started"],
        last_finished=row["last_finished"],
        last_status=row["last_status"],
        consecutive_failures=row["consecutive_failures"],
        total_runs=row["total_runs"],
        total_successes=row["total_successes"],
        total_failures=row["total_failures"],
    )


def _row_to_queue(row) -> QueueItem:
    return QueueItem(
        id=row["id"],
        job_id=row["job_id"],
        queued_at=row["queued_at"],
        available_at=row["available_at"],
        status=row["status"],
        claimed_at=row["claimed_at"],
        lease_until=row["lease_until"],
        worker_id=row["worker_id"],
        attempt=row["attempt"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_execution(row) -> Execution:
    return Execution(
        id=row["id"],
        job_id=row["job_id"],
        queue_id=row["queue_id"],
        account=row["account"],
        domain=row["domain"],
        queued_at=row["queued_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration=row["duration"],
        queue_delay=row["queue_delay"],
        status=row["status"],
        exit_code=row["exit_code"],
        worker_id=row["worker_id"],
        failure_reason=row["failure_reason"],
        log_path=row["log_path"],
        created_at=row["created_at"],
    )


class JobRepository:
    """Persistence operations for configured WordPress cron jobs."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        *,
        job_key: str,
        cpanel_account: str,
        uid: int,
        gid: int,
        home_directory: str,
        domain: Optional[str],
        wp_cron_path: str,
        minute: str,
        hour: str,
        day: str,
        month: str,
        weekday: str,
        schedule_expression: str,
        enabled: bool = True,
        last_seen: Optional[int] = None,
    ) -> int:
        now = _now()
        if last_seen is None:
            last_seen = now

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    job_key,
                    cpanel_account,
                    uid,
                    gid,
                    home_directory,
                    domain,
                    wp_cron_path,
                    minute,
                    hour,
                    day,
                    month,
                    weekday,
                    schedule_expression,
                    enabled,
                    created_at,
                    updated_at,
                    last_seen
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_key,
                    cpanel_account,
                    uid,
                    gid,
                    home_directory,
                    domain,
                    wp_cron_path,
                    minute,
                    hour,
                    day,
                    month,
                    weekday,
                    schedule_expression,
                    int(enabled),
                    now,
                    now,
                    last_seen,
                ),
            )
            return int(cursor.lastrowid)

    def get(self, job_id: int) -> Optional[Job]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            return _row_to_job(row) if row else None
        finally:
            conn.close()

    def get_by_key(self, job_key: str) -> Optional[Job]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            return _row_to_job(row) if row else None
        finally:
            conn.close()

    def list(
        self,
        *,
        enabled: Optional[bool] = None,
        account: Optional[str] = None,
    ) -> list[Job]:
        conn = self.db.connect()
        try:
            clauses = []
            params = []

            if enabled is not None:
                clauses.append("enabled = ?")
                params.append(int(enabled))

            if account is not None:
                clauses.append("cpanel_account = ?")
                params.append(account)

            sql = "SELECT * FROM jobs"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id"

            rows = conn.execute(sql, params).fetchall()
            return [_row_to_job(row) for row in rows]
        finally:
            conn.close()

    def update(
        self,
        job_id: int,
        *,
        cpanel_account: Optional[str] = None,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        home_directory: Optional[str] = None,
        domain: Optional[str] = None,
        wp_cron_path: Optional[str] = None,
        minute: Optional[str] = None,
        hour: Optional[str] = None,
        day: Optional[str] = None,
        month: Optional[str] = None,
        weekday: Optional[str] = None,
        schedule_expression: Optional[str] = None,
        enabled: Optional[bool] = None,
        last_seen: Optional[int] = None,
        last_triggered: Optional[int] = None,
        last_queued: Optional[int] = None,
        last_started: Optional[int] = None,
        last_finished: Optional[int] = None,
        last_status: Optional[str] = None,
        consecutive_failures: Optional[int] = None,
        total_runs: Optional[int] = None,
        total_successes: Optional[int] = None,
        total_failures: Optional[int] = None,
    ) -> bool:
        values = {
            "cpanel_account": cpanel_account,
            "uid": uid,
            "gid": gid,
            "home_directory": home_directory,
            "domain": domain,
            "wp_cron_path": wp_cron_path,
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month,
            "weekday": weekday,
            "schedule_expression": schedule_expression,
            "enabled": int(enabled) if enabled is not None else None,
            "last_seen": last_seen,
            "last_triggered": last_triggered,
            "last_queued": last_queued,
            "last_started": last_started,
            "last_finished": last_finished,
            "last_status": last_status,
            "consecutive_failures": consecutive_failures,
            "total_runs": total_runs,
            "total_successes": total_successes,
            "total_failures": total_failures,
        }

        updates = []
        params = []

        for column, value in values.items():
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(_now())
        params.append(job_id)

        with self.db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            return cursor.rowcount == 1

    def enable(self, job_id: int) -> bool:
        return self.update(job_id, enabled=True)

    def disable(self, job_id: int) -> bool:
        return self.update(job_id, enabled=False)

    def touch_seen(self, job_id: int, seen_at: Optional[int] = None) -> bool:
        return self.update(
            job_id,
            last_seen=seen_at if seen_at is not None else _now(),
        )


class QueueRepository:
    """Persistence operations for execution requests."""

    def __init__(self, db: Database):
        self.db = db

    def enqueue(
        self,
        job_id: int,
        *,
        queued_at: Optional[int] = None,
        available_at: Optional[int] = None,
        deduplicate: bool = True,
    ) -> Optional[int]:
        now = _now()
        queued_at = queued_at if queued_at is not None else now
        available_at = available_at if available_at is not None else queued_at

        with self.db.transaction() as conn:
            if deduplicate:
                existing = conn.execute(
                    """
                    SELECT id
                    FROM queue
                    WHERE job_id = ?
                      AND status IN ('PENDING', 'RUNNING', 'RETRY')
                    ORDER BY id
                    LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()

                if existing:
                    return int(existing["id"])

            cursor = conn.execute(
                """
                INSERT INTO queue (
                    job_id,
                    queued_at,
                    available_at,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    job_id,
                    queued_at,
                    available_at,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def get(self, queue_id: int) -> Optional[QueueItem]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM queue WHERE id = ?",
                (queue_id,),
            ).fetchone()
            return _row_to_queue(row) if row else None
        finally:
            conn.close()

    def list_pending(
        self,
        *,
        now: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[QueueItem]:
        now = now if now is not None else _now()

        conn = self.db.connect()
        try:
            sql = """
                SELECT *
                FROM queue
                WHERE status IN ('PENDING', 'RETRY')
                  AND available_at <= ?
                ORDER BY available_at, queued_at, id
            """
            params: list = [now]

            if limit is not None:
                if limit <= 0:
                    return []
                sql += " LIMIT ?"
                params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            return [_row_to_queue(row) for row in rows]
        finally:
            conn.close()

    def claim(
        self,
        queue_id: int,
        *,
        worker_id: str,
        lease_until: int,
        claimed_at: Optional[int] = None,
    ) -> bool:
        claimed_at = claimed_at if claimed_at is not None else _now()

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE queue
                   SET status = 'RUNNING',
                       claimed_at = ?,
                       lease_until = ?,
                       worker_id = ?,
                       attempt = attempt + 1,
                       updated_at = ?
                 WHERE id = ?
                   AND status IN ('PENDING', 'RETRY')
                   AND available_at <= ?
                """,
                (
                    claimed_at,
                    lease_until,
                    worker_id,
                    claimed_at,
                    queue_id,
                    claimed_at,
                ),
            )
            return cursor.rowcount == 1

    def complete(self, queue_id: int) -> bool:
        now = _now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE queue
                   SET status = 'COMPLETED',
                       lease_until = NULL,
                       updated_at = ?
                 WHERE id = ?
                   AND status = 'RUNNING'
                """,
                (now, queue_id),
            )
            return cursor.rowcount == 1

    def fail(self, queue_id: int) -> bool:
        now = _now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE queue
                   SET status = 'FAILED',
                       lease_until = NULL,
                       updated_at = ?
                 WHERE id = ?
                   AND status = 'RUNNING'
                """,
                (now, queue_id),
            )
            return cursor.rowcount == 1

    def retry(
        self,
        queue_id: int,
        *,
        available_at: Optional[int] = None,
    ) -> bool:
        now = _now()
        available_at = available_at if available_at is not None else now

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE queue
                   SET status = 'RETRY',
                       available_at = ?,
                       claimed_at = NULL,
                       lease_until = NULL,
                       worker_id = NULL,
                       updated_at = ?
                 WHERE id = ?
                   AND status IN ('RUNNING', 'FAILED', 'RETRY')
                """,
                (available_at, now, queue_id),
            )
            return cursor.rowcount == 1

    def cancel(self, queue_id: int) -> bool:
        now = _now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE queue
                   SET status = 'CANCELLED',
                       lease_until = NULL,
                       updated_at = ?
                 WHERE id = ?
                   AND status IN ('PENDING', 'RETRY', 'RUNNING')
                """,
                (now, queue_id),
            )
            return cursor.rowcount == 1

    def release_expired_leases(
        self,
        *,
        now: Optional[int] = None,
    ) -> int:
        now = now if now is not None else _now()

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE queue
                   SET status = 'RETRY',
                       available_at = ?,
                       claimed_at = NULL,
                       lease_until = NULL,
                       worker_id = NULL,
                       updated_at = ?
                 WHERE status = 'RUNNING'
                   AND lease_until IS NOT NULL
                   AND lease_until <= ?
                """,
                (now, now, now),
            )
            return cursor.rowcount


class ExecutionRepository:
    """Persistence operations for execution history."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        *,
        job_id: int,
        queue_id: Optional[int],
        account: str,
        domain: Optional[str],
        queued_at: int,
        status: str = "QUEUED",
        worker_id: Optional[str] = None,
        log_path: Optional[str] = None,
    ) -> int:
        now = _now()

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO executions (
                    job_id,
                    queue_id,
                    account,
                    domain,
                    queued_at,
                    status,
                    worker_id,
                    log_path,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    queue_id,
                    account,
                    domain,
                    queued_at,
                    status,
                    worker_id,
                    log_path,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def get(self, execution_id: int) -> Optional[Execution]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM executions WHERE id = ?",
                (execution_id,),
            ).fetchone()
            return _row_to_execution(row) if row else None
        finally:
            conn.close()

    def start(
        self,
        execution_id: int,
        *,
        worker_id: str,
        started_at: Optional[int] = None,
    ) -> bool:
        started_at = started_at if started_at is not None else _now()

        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT queued_at FROM executions WHERE id = ?",
                (execution_id,),
            ).fetchone()

            if not row:
                return False

            queue_delay = max(0, started_at - row["queued_at"])

            cursor = conn.execute(
                """
                UPDATE executions
                   SET started_at = ?,
                       queue_delay = ?,
                       status = 'RUNNING',
                       worker_id = ?
                 WHERE id = ?
                   AND started_at IS NULL
                   AND finished_at IS NULL
                """,
                (
                    started_at,
                    float(queue_delay),
                    worker_id,
                    execution_id,
                ),
            )
            return cursor.rowcount == 1

    def finish(
        self,
        execution_id: int,
        *,
        status: str,
        exit_code: Optional[int],
        finished_at: Optional[int] = None,
        failure_reason: Optional[str] = None,
    ) -> bool:
        finished_at = finished_at if finished_at is not None else _now()

        with self.db.transaction() as conn:
            row = conn.execute(
                """
                SELECT started_at
                FROM executions
                WHERE id = ?
                """,
                (execution_id,),
            ).fetchone()

            if not row:
                return False

            duration = None
            if row["started_at"] is not None:
                duration = max(0, finished_at - row["started_at"])

            cursor = conn.execute(
                """
                UPDATE executions
                   SET finished_at = ?,
                       duration = ?,
                       status = ?,
                       exit_code = ?,
                       failure_reason = ?
                 WHERE id = ?
                   AND finished_at IS NULL
                """,
                (
                    finished_at,
                    float(duration) if duration is not None else None,
                    status,
                    exit_code,
                    failure_reason,
                    execution_id,
                ),
            )
            return cursor.rowcount == 1

    def list_for_job(
        self,
        job_id: int,
        *,
        limit: int = 50,
    ) -> list[Execution]:
        if limit <= 0:
            return []

        conn = self.db.connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM executions
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
            return [_row_to_execution(row) for row in rows]
        finally:
            conn.close()

    def list_recent(
        self,
        *,
        limit: int = 100,
    ) -> list[Execution]:
        if limit <= 0:
            return []

        conn = self.db.connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM executions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [_row_to_execution(row) for row in rows]
        finally:
            conn.close()

    def count_by_status(self) -> dict[str, int]:
        conn = self.db.connect()
        try:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM executions
                GROUP BY status
                """
            ).fetchall()
            return {row["status"]: row["count"] for row in rows}
        finally:
            conn.close()


class SchedulerRepositories:
    """Convenience container for all repositories."""

    def __init__(self, db: Database):
        self.jobs = JobRepository(db)
        self.queue = QueueRepository(db)
        self.executions = ExecutionRepository(db)
