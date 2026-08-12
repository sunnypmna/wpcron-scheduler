#!/usr/bin/env python3

import argparse
import os
import pwd
import sqlite3
import subprocess
import sys
import time


DB_PATH = "/var/lib/wpcron/scheduler.db"
REQUEST_NAME = "request"


class ConsumerError(Exception):
    pass


def fail(message):
    print("REJECTED")
    print("Reason   :", message)
    return 1


def account_from_request(request_file):
    st = os.stat(request_file)

    try:
        pw = pwd.getpwuid(st.st_uid)
    except KeyError:
        raise ConsumerError(
            "request owner UID does not map to a local account"
        )

    username = pw.pw_name
    home = pw.pw_dir

    expected_dir = os.path.join(home, ".wpcron-scheduler")
    actual_dir = os.path.realpath(os.path.dirname(request_file))

    if actual_dir != os.path.realpath(expected_dir):
        raise ConsumerError(
            "request is not inside the account scheduler directory"
        )

    return username, home


def read_request(request_file):
    with open(request_file, "r", encoding="utf-8") as fh:
        content = fh.read()

    # Request format is intentionally one value only.
    # The wrapper writes the full absolute wp-cron.php path.
    path = content.strip()

    if not path:
        raise ConsumerError("request is empty")

    if "\n" in path or "\r" in path:
        raise ConsumerError("request contains multiple lines")

    if not path.startswith("/"):
        raise ConsumerError("requested path is not absolute")

    if not path.endswith("/wp-cron.php"):
        raise ConsumerError("requested path is not wp-cron.php")

    return path


def validate_job(username, path):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            """
            SELECT
                id,
                cpanel_account,
                wp_cron_path,
                enabled
            FROM jobs
            WHERE cpanel_account = ?
              AND wp_cron_path = ?
            ORDER BY id
            LIMIT 1
            """,
            (username, path),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise ConsumerError(
            "no registered job matches account and path"
        )

    if not row["enabled"]:
        raise ConsumerError(
            "matching job is disabled"
        )

    return row


def submit_to_brain(username, path, dry_run=False):
    command = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "brain.py"),
        "--account-path",
        username,
        path,
    ]

    if dry_run:
        command.append("--dry-run")

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if completed.stdout:
        print(completed.stdout.rstrip())

    if completed.returncode != 0:
        if completed.stderr:
            print(
                completed.stderr.rstrip(),
                file=sys.stderr,
            )
        raise ConsumerError(
            "brain returned exit code {}".format(
                completed.returncode
            )
        )


def process_request(request_file, dry_run=False):
    if not os.path.isfile(request_file):
        raise ConsumerError("request file does not exist")

    username, home = account_from_request(request_file)
    path = read_request(request_file)

    # Basic ownership/path relationship check.
    expected_prefix = os.path.realpath(home) + os.sep

    if not os.path.realpath(path).startswith(expected_prefix):
        raise ConsumerError(
            "requested path is outside account home"
        )

    row = validate_job(username, path)

    print("REQUEST ACCEPTED")
    print("Account  :", username)
    print("Job ID   :", row["id"])
    print("Path     :", path)

    submit_to_brain(username, path, dry_run=dry_run)

    if not dry_run:
        os.unlink(request_file)
        print("Request  : consumed")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="WP Cron Scheduler privileged request consumer"
    )

    parser.add_argument(
        "--account",
        help="Process only this cPanel account",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan /home/*/.wpcron-scheduler/request",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and invoke Brain in dry-run mode",
    )

    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[FAIL] Must run as root.", file=sys.stderr)
        return 1

    requests = []

    if args.account:
        try:
            pw = pwd.getpwnam(args.account)
        except KeyError:
            return fail("account does not exist")

        requests.append(
            os.path.join(
                pw.pw_dir,
                ".wpcron-scheduler",
                REQUEST_NAME,
            )
        )

    elif args.all:
        for entry in os.scandir("/home"):
            if not entry.is_dir():
                continue

            request = os.path.join(
                entry.path,
                ".wpcron-scheduler",
                REQUEST_NAME,
            )

            if os.path.isfile(request):
                requests.append(request)

    else:
        print(
            "Usage: request_consumer.py "
            "--account USERNAME | --all [--dry-run]"
        )
        return 2

    processed = 0
    rejected = 0

    for request_file in requests:
        try:
            process_request(
                request_file,
                dry_run=args.dry_run,
            )
            processed += 1

        except Exception as exc:
            rejected += 1
            print(
                "REJECTED",
                request_file,
            )
            print(
                "Reason   :",
                str(exc),
            )

    print()
    print("Consumer summary")
    print("----------------")
    print("Requests processed :", processed)
    print("Requests rejected  :", rejected)

    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
