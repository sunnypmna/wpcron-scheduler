#!/usr/bin/env python3
"""
WP Cron Scheduler - SQLite database foundation.

This module intentionally contains no scheduling/dispatch logic.
It provides:
  - schema creation
  - SQLite connection handling
  - transaction handling
  - integrity/health checks
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


SCHEMA_VERSION = 1


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scheduler_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key                 TEXT NOT NULL UNIQUE,

    cpanel_account          TEXT NOT NULL,
    uid                     INTEGER NOT NULL,
    gid                     INTEGER NOT NULL,
    home_directory          TEXT NOT NULL,

    domain                  TEXT,
    wp_cron_path            TEXT NOT NULL,

    minute                  TEXT NOT NULL,
    hour                    TEXT NOT NULL,
    day                     TEXT NOT NULL,
    month                   TEXT NOT NULL,
    weekday                 TEXT NOT NULL,
    schedule_expression     TEXT NOT NULL,

    enabled                 INTEGER NOT NULL DEFAULT 1
                            CHECK (enabled IN (0, 1)),

    created_at              INTEGER NOT NULL,
    updated_at              INTEGER NOT NULL,
    last_seen               INTEGER,

    last_triggered          INTEGER,
    last_queued             INTEGER,
    last_started            INTEGER,
    last_finished           INTEGER,
    last_status             TEXT,

    consecutive_failures    INTEGER NOT NULL DEFAULT 0
                            CHECK (consecutive_failures >= 0),

    total_runs              INTEGER NOT NULL DEFAULT 0
                            CHECK (total_runs >= 0),

    total_successes         INTEGER NOT NULL DEFAULT 0
                            CHECK (total_successes >= 0),

    total_failures          INTEGER NOT NULL DEFAULT 0
                            CHECK (total_failures >= 0)
);

CREATE INDEX IF NOT EXISTS idx_jobs_account_enabled
    ON jobs (cpanel_account, enabled);

CREATE INDEX IF NOT EXISTS idx_jobs_enabled
    ON jobs (enabled);

CREATE INDEX IF NOT EXISTS idx_jobs_last_seen
    ON jobs (last_seen);

CREATE TABLE IF NOT EXISTS queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,

    queued_at       INTEGER NOT NULL,
    available_at    INTEGER NOT NULL,

    status          TEXT NOT NULL
                    CHECK (status IN (
                        'PENDING',
                        'RUNNING',
                        'COMPLETED',
                        'FAILED',
                        'RETRY',
                        'CANCELLED'
                    )),

    claimed_at      INTEGER,
    lease_until     INTEGER,
    worker_id       TEXT,

    attempt         INTEGER NOT NULL DEFAULT 0
                    CHECK (attempt >= 0),

    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,

    FOREIGN KEY (job_id)
        REFERENCES jobs(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_queue_pending
    ON queue (status, available_at, queued_at);

CREATE INDEX IF NOT EXISTS idx_queue_job_status
    ON queue (job_id, status);

CREATE INDEX IF NOT EXISTS idx_queue_lease
    ON queue (status, lease_until);

CREATE INDEX IF NOT EXISTS idx_queue_account_fairness
    ON queue (status, available_at, job_id);

CREATE TABLE IF NOT EXISTS executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    queue_id        INTEGER,

    account         TEXT NOT NULL,
    domain          TEXT,

    queued_at       INTEGER NOT NULL,
    started_at      INTEGER,
    finished_at     INTEGER,

    duration        REAL,
    queue_delay     REAL,

    status          TEXT NOT NULL,

    exit_code       INTEGER,
    worker_id       TEXT,

    failure_reason  TEXT,
    log_path        TEXT,

    created_at      INTEGER NOT NULL,

    FOREIGN KEY (job_id)
        REFERENCES jobs(id)
        ON DELETE CASCADE,

    FOREIGN KEY (queue_id)
        REFERENCES queue(id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_executions_job
    ON executions (job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_executions_status
    ON executions (status, created_at);

CREATE INDEX IF NOT EXISTS idx_executions_account
    ON executions (account, created_at);

CREATE INDEX IF NOT EXISTS idx_executions_started
    ON executions (started_at);

CREATE TRIGGER IF NOT EXISTS trg_jobs_updated_at
AFTER UPDATE OF
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
    last_seen,
    last_triggered,
    last_queued,
    last_started,
    last_finished,
    last_status,
    consecutive_failures,
    total_runs,
    total_successes,
    total_failures
ON jobs
BEGIN
    UPDATE jobs
       SET updated_at = CAST(strftime('%s', 'now') AS INTEGER)
     WHERE id = NEW.id
       AND updated_at = OLD.updated_at;
END;

CREATE TRIGGER IF NOT EXISTS trg_queue_updated_at
AFTER UPDATE OF
    job_id,
    queued_at,
    available_at,
    status,
    claimed_at,
    lease_until,
    worker_id,
    attempt
ON queue
BEGIN
    UPDATE queue
       SET updated_at = CAST(strftime('%s', 'now') AS INTEGER)
     WHERE id = NEW.id
       AND updated_at = OLD.updated_at;
END;
"""


class Database:
    """Small SQLite database abstraction for the scheduler foundation."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        self._configure_connection(conn)
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        # sqlite3.executescript() implicitly manages/commits its own
        # transaction, so it must not be called inside our explicit
        # BEGIN/COMMIT transaction context.
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)

            now = int(__import__("time").time())

            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO scheduler_meta (key, value, updated_at)
                    VALUES ('schema_version', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (str(SCHEMA_VERSION), now),
                )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        finally:
            conn.close()

    def health_check(self) -> dict:
        conn = self.connect()
        try:
            integrity = conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            foreign_keys = conn.execute(
                "PRAGMA foreign_keys"
            ).fetchone()[0]

            wal_mode = conn.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]

            schema_version_row = conn.execute(
                "SELECT value FROM scheduler_meta WHERE key='schema_version'"
            ).fetchone()

            jobs = conn.execute(
                "SELECT COUNT(*) FROM jobs"
            ).fetchone()[0]

            queue = conn.execute(
                "SELECT COUNT(*) FROM queue"
            ).fetchone()[0]

            executions = conn.execute(
                "SELECT COUNT(*) FROM executions"
            ).fetchone()[0]

            return {
                "integrity": integrity,
                "foreign_keys": bool(foreign_keys),
                "journal_mode": str(wal_mode),
                "schema_version": (
                    int(schema_version_row[0])
                    if schema_version_row else None
                ),
                "jobs": jobs,
                "queue": queue,
                "executions": executions,
            }
        finally:
            conn.close()


def initialize_database(path: str) -> Database:
    db = Database(path)
    db.initialize()
    return db


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Initialize/check the WP Cron Scheduler SQLite database."
    )
    parser.add_argument(
        "--database",
        required=True,
        help="SQLite database path",
    )
    parser.add_argument(
        "action",
        choices=("init", "health"),
    )

    args = parser.parse_args()

    db = Database(args.database)

    if args.action == "init":
        db.initialize()
        print(f"Database initialized: {db.path}")
    else:
        print(json.dumps(db.health_check(), indent=2))
