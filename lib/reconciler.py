#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cpanel_connector import CPanelConnector, CPanelConnectorError, CronEntry
from system_connector import SystemAccountConnector, SystemConnectorError
from cron_command_parser import CronCommandParser


DB_FILE = "/var/lib/wpcron/scheduler.db"
LOCK_FILE = "/run/wpcron-reconcile.lock"


@dataclass(frozen=True)
class CurrentJob:
    job_key: str
    account: str
    uid: int
    gid: int
    home: str
    domain: Optional[str]
    relative_script_path: str
    wp_cron_path: str
    site_root: str
    command: str
    php_executable: Optional[str]
    minute: str
    hour: str
    day: str
    month: str
    weekday: str
    schedule_expression: str
    linekey: Optional[int]
    enabled: bool


@dataclass
class Stats:
    accounts_seen: int = 0
    accounts_skipped: int = 0
    crons_seen: int = 0
    candidates: int = 0
    invalid: int = 0
    missing: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    disabled: int = 0
    linekey_changes: int = 0


class Lock:
    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.path, "w")
        try:
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fd.close()
            self.fd = None
            raise RuntimeError("Another reconciliation is already running")
        self.fd.write(str(os.getpid()))
        self.fd.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd:
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
            self.fd.close()
            self.fd = None


class Reconciler:
    def __init__(self, dry_run=False, verbose=False):
        self.dry_run = dry_run
        self.verbose = verbose
        self.stats = Stats()
        self.cpanel = CPanelConnector()
        self.system = SystemAccountConnector()
        self.parser = CronCommandParser()

    def db(self):
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def run(self):
        accounts = self.cpanel.list_accounts()
        self.stats.accounts_seen = len(accounts)

        current_accounts = {a.username for a in accounts}
        scanned_accounts = set()
        seen_keys = set()

        for account in accounts:
            username = account.username

            try:
                system_account = self.system.require_account(username)
            except SystemConnectorError as exc:
                self.stats.accounts_skipped += 1
                self.log(f"SKIP account={username}: {exc}")
                continue

            try:
                crons = self.cpanel.list_crons(username)
            except CPanelConnectorError as exc:
                self.stats.accounts_skipped += 1
                self.log(f"SKIP account={username}: {exc}")
                continue

            scanned_accounts.add(username)

            for cron in crons:
                self.stats.crons_seen += 1

                current = self.normalize(
                    account,
                    system_account,
                    cron,
                )

                if current is None:
                    continue

                self.stats.candidates += 1
                seen_keys.add(current.job_key)
                self.reconcile_one(current)

        self.disable_missing(
            current_accounts=current_accounts,
            scanned_accounts=scanned_accounts,
            seen_keys=seen_keys,
        )

        return self.stats

    def normalize(self, account, system_account, cron):
        parsed = self.parser.parse(
            account=system_account.username,
            account_home=system_account.home,
            command=cron.command,
        )

        if parsed is None:
            return None

        home = os.path.normpath(system_account.home)
        path = os.path.normpath(parsed.script_path)

        if not self.under_home(path, home):
            self.stats.invalid += 1
            self.log(
                f"REJECT account={account.username}: path outside home: {path}"
            )
            return None

        relative = os.path.relpath(path, home)

        if relative in (".", "") or relative.startswith("../"):
            self.stats.invalid += 1
            return None

        try:
            exists = os.path.isfile(path)
        except OSError as exc:
            self.stats.invalid += 1
            self.log(f"REJECT account={account.username}: stat failed: {exc}")
            return None

        if not exists:
            self.stats.missing += 1

        return CurrentJob(
            job_key=self.job_key(system_account.username, relative),
            account=system_account.username,
            uid=system_account.uid,
            gid=system_account.gid,
            home=home,
            domain=account.domain,
            relative_script_path=relative,
            wp_cron_path=path,
            site_root=os.path.dirname(path),
            command=cron.command,
            php_executable=parsed.php_executable,
            minute=cron.minute,
            hour=cron.hour,
            day=cron.day,
            month=cron.month,
            weekday=cron.weekday,
            schedule_expression=cron.schedule_expression,
            linekey=cron.linekey,
            enabled=exists,
        )

    def reconcile_one(self, current):
        conn = self.db()
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_key = ? LIMIT 1",
                (current.job_key,),
            ).fetchone()

            if row is None:
                self.stats.created += 1
                self.log(
                    f"NEW account={current.account} "
                    f"key={current.job_key} "
                    f"linekey={current.linekey} "
                    f"schedule={current.schedule_expression} "
                    f"path={current.wp_cron_path} "
                    f"enabled={current.enabled}"
                )

                if not self.dry_run:
                    self.insert(conn, current)
                    conn.commit()
                return

            if row["linekey"] != current.linekey:
                self.stats.linekey_changes += 1
                self.log(
                    f"LINEKEY_CHANGED account={current.account} "
                    f"path={current.relative_script_path} "
                    f"old={row['linekey']} new={current.linekey}"
                )

            changed = self.changed(row, current)

            if not changed:
                self.stats.unchanged += 1
                if not self.dry_run:
                    self.touch(conn, row["id"])
                    conn.commit()
                return

            self.stats.updated += 1
            self.log(
                f"UPDATE account={current.account} "
                f"key={current.job_key} "
                f"linekey={current.linekey} "
                f"schedule={current.schedule_expression} "
                f"path={current.wp_cron_path} "
                f"php={current.php_executable} "
                f"enabled={current.enabled}"
            )

            if not self.dry_run:
                self.update(conn, row["id"], current)
                conn.commit()

        finally:
            conn.close()

    def disable_missing(self, current_accounts, scanned_accounts, seen_keys):
        conn = self.db()
        try:
            rows = conn.execute(
                "SELECT id, job_key, cpanel_account, wp_cron_path, enabled "
                "FROM jobs"
            ).fetchall()

            for row in rows:
                if not row["enabled"]:
                    continue

                if row["cpanel_account"] not in current_accounts:
                    reason = "account absent"
                else:
                    if row["cpanel_account"] not in scanned_accounts:
                        continue
                    if row["job_key"] in seen_keys:
                        continue
                    reason = "cron absent"

                self.stats.disabled += 1
                self.log(
                    f"DISABLE ({reason}) "
                    f"account={row['cpanel_account']} "
                    f"key={row['job_key']} "
                    f"path={row['wp_cron_path']}"
                )

                if not self.dry_run:
                    conn.execute(
                        "UPDATE jobs SET enabled=0, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?",
                        (row["id"],),
                    )

            if not self.dry_run:
                conn.commit()

        finally:
            conn.close()

    @staticmethod
    def changed(row, c):
        checks = {
            "cpanel_account": c.account,
            "uid": c.uid,
            "gid": c.gid,
            "home_directory": c.home,
            "domain": c.domain,
            "relative_script_path": c.relative_script_path,
            "wp_cron_path": c.wp_cron_path,
            "site_root": c.site_root,
            "command": c.command,
            "php_executable": c.php_executable,
            "minute": c.minute,
            "hour": c.hour,
            "day": c.day,
            "month": c.month,
            "weekday": c.weekday,
            "schedule_expression": c.schedule_expression,
            "linekey": c.linekey,
            "enabled": c.enabled,
        }

        for name, value in checks.items():
            if row[name] != value:
                return True

        return False

    @staticmethod
    def insert(conn, c):
        conn.execute(
            """
            INSERT INTO jobs (
                job_key,
                cpanel_account,
                uid,
                gid,
                home_directory,
                domain,
                relative_script_path,
                wp_cron_path,
                site_root,
                command,
                php_executable,
                minute,
                hour,
                day,
                month,
                weekday,
                schedule_expression,
                linekey,
                enabled
            )
            VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                c.job_key,
                c.account,
                c.uid,
                c.gid,
                c.home,
                c.domain,
                c.relative_script_path,
                c.wp_cron_path,
                c.site_root,
                c.command,
                c.php_executable,
                c.minute,
                c.hour,
                c.day,
                c.month,
                c.weekday,
                c.schedule_expression,
                c.linekey,
                int(c.enabled),
            ),
        )

    @staticmethod
    def update(conn, job_id, c):
        conn.execute(
            """
            UPDATE jobs
            SET
                cpanel_account=?,
                uid=?,
                gid=?,
                home_directory=?,
                domain=?,
                relative_script_path=?,
                wp_cron_path=?,
                site_root=?,
                command=?,
                php_executable=?,
                minute=?,
                hour=?,
                day=?,
                month=?,
                weekday=?,
                schedule_expression=?,
                linekey=?,
                enabled=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                c.account,
                c.uid,
                c.gid,
                c.home,
                c.domain,
                c.relative_script_path,
                c.wp_cron_path,
                c.site_root,
                c.command,
                c.php_executable,
                c.minute,
                c.hour,
                c.day,
                c.month,
                c.weekday,
                c.schedule_expression,
                c.linekey,
                int(c.enabled),
                job_id,
            ),
        )

    @staticmethod
    def touch(conn, job_id):
        conn.execute(
            "UPDATE jobs SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )

    @staticmethod
    def job_key(account, relative_script_path):
        return f"wpcron:{account}:{relative_script_path}"

    @staticmethod
    def under_home(path, home):
        try:
            return os.path.commonpath([
                os.path.normpath(path),
                os.path.normpath(home),
            ]) == os.path.normpath(home)
        except ValueError:
            return False

    def log(self, message):
        if self.verbose or self.dry_run:
            print(message)


def print_stats(s):
    print("")
    print("Reconciliation summary")
    print("-----------------------")
    print(f"Accounts seen       : {s.accounts_seen}")
    print(f"Accounts skipped    : {s.accounts_skipped}")
    print(f"Cron entries seen   : {s.crons_seen}")
    print(f"WP cron candidates  : {s.candidates}")
    print(f"Invalid candidates  : {s.invalid}")
    print(f"Missing files       : {s.missing}")
    print(f"Jobs created        : {s.created}")
    print(f"Jobs updated        : {s.updated}")
    print(f"Jobs unchanged      : {s.unchanged}")
    print(f"Jobs disabled       : {s.disabled}")
    print(f"Linekey changes     : {s.linekey_changes}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    try:
        with Lock(LOCK_FILE):
            r = Reconciler(
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            print_stats(r.run())
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except (CPanelConnectorError, SystemConnectorError) as exc:
        print(f"[FAIL] Reconciliation aborted: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
