#!/usr/bin/env python3
"""
WP Cron Scheduler - admission brain.

The brain is deliberately narrow.

Input:
    a registered scheduler job, identified by job_id or account + path.

Decision:
    REJECTED
    QUEUED
    ALREADY_QUEUED

The brain never executes customer code.

Admission rules:
    1. job must exist
    2. job must be enabled
    3. wp-cron.php must be a regular file
    4. target must remain under the recorded account home
    5. queue must not already contain PENDING/RUNNING/RETRY for the job

If a job is currently running, another invocation is deduplicated rather
than launched concurrently. This is the first-version policy for a single
WordPress installation.

When the queue is full, the brain still enqueues the job. Capacity is a
dispatcher concern; pending work must not be discarded.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Optional


DB_FILE = "/var/lib/wpcron/scheduler.db"


@dataclass(frozen=True)
class AdmissionResult:
    decision: str
    job_id: Optional[int]
    queue_id: Optional[int]
    reason: str


class BrainError(RuntimeError):
    pass


class AdmissionBrain:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path

    def connect(self):
        conn = sqlite3.connect(
            self.db_path,
            timeout=5.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def admit_job(self, job_id: int, *, dry_run: bool = False) -> AdmissionResult:
        conn = self.connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            job = conn.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

            if job is None:
                conn.execute("ROLLBACK")
                return AdmissionResult(
                    "REJECTED",
                    None,
                    None,
                    "job does not exist",
                )

            result = self._validate_job(job)

            if result is not None:
                conn.execute("ROLLBACK")
                return AdmissionResult(
                    "REJECTED",
                    int(job["id"]),
                    None,
                    result,
                )

            existing = conn.execute(
                """
                SELECT id, status
                FROM queue
                WHERE job_id = ?
                  AND status IN ('PENDING', 'RUNNING', 'RETRY')
                ORDER BY id
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()

            if existing is not None:
                conn.execute("ROLLBACK")
                return AdmissionResult(
                    "ALREADY_QUEUED",
                    int(job["id"]),
                    int(existing["id"]),
                    f"existing queue status={existing['status']}",
                )

            now = int(time.time())

            if dry_run:
                conn.execute("ROLLBACK")
                return AdmissionResult(
                    "WOULD_QUEUE",
                    int(job["id"]),
                    None,
                    "admission checks passed",
                )

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
                    int(job["id"]),
                    now,
                    now,
                    now,
                    now,
                ),
            )

            queue_id = int(cursor.lastrowid)

            # last_queued is scheduler state. Do not change cron configuration.
            conn.execute(
                """
                UPDATE jobs
                SET last_queued = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(job["id"])),
            )

            conn.execute("COMMIT")

            return AdmissionResult(
                "QUEUED",
                int(job["id"]),
                queue_id,
                "admission checks passed",
            )

        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        finally:
            conn.close()

    def admit_path(
        self,
        account: str,
        path: str,
        *,
        dry_run: bool = False,
    ) -> AdmissionResult:
        conn = self.connect()

        try:
            row = conn.execute(
                """
                SELECT id
                FROM jobs
                WHERE cpanel_account = ?
                  AND wp_cron_path = ?
                ORDER BY id
                LIMIT 1
                """,
                (account, os.path.normpath(path)),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return AdmissionResult(
                "REJECTED",
                None,
                None,
                "registered job not found for account/path",
            )

        return self.admit_job(int(row["id"]), dry_run=dry_run)

    @staticmethod
    def _validate_job(job) -> Optional[str]:
        if not bool(job["enabled"]):
            return "job is disabled"

        home = os.path.normpath(job["home_directory"])
        path = os.path.normpath(job["wp_cron_path"])

        try:
            if os.path.commonpath([home, path]) != home:
                return "wp-cron path is outside account home"
        except ValueError:
            return "invalid account/path boundary"

        if os.path.basename(path) != "wp-cron.php":
            return "target is not wp-cron.php"

        try:
            if not os.path.isfile(path):
                return "wp-cron.php does not exist or is not a regular file"
        except OSError as exc:
            return f"unable to validate wp-cron.php: {exc}"

        return None


def print_result(result: AdmissionResult) -> None:
    print(f"Decision : {result.decision}")
    print(f"Job ID   : {result.job_id}")
    print(f"Queue ID : {result.queue_id}")
    print(f"Reason   : {result.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Admit a registered WP-Cron job into the scheduler queue"
    )

    identity = parser.add_mutually_exclusive_group(required=True)

    identity.add_argument(
        "--job-id",
        type=int,
        help="Registered scheduler job ID",
    )

    identity.add_argument(
        "--account-path",
        nargs=2,
        metavar=("ACCOUNT", "PATH"),
        help="Registered cPanel account and exact wp-cron.php path",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate/admission-check without inserting queue state",
    )

    args = parser.parse_args()

    brain = AdmissionBrain()

    if args.job_id is not None:
        result = brain.admit_job(
            args.job_id,
            dry_run=args.dry_run,
        )
    else:
        account, path = args.account_path
        result = brain.admit_path(
            account,
            path,
            dry_run=args.dry_run,
        )

    print_result(result)

    if result.decision in ("QUEUED", "ALREADY_QUEUED", "WOULD_QUEUE"):
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
