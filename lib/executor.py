#!/usr/bin/env python3
"""
WP Cron Scheduler - Phase 2 / Step 1 executor.

Executes one already-admitted queue item.

Security boundary:
- never executes jobs.command through a shell
- validates the recorded account/path/PHP executable again
- runs PHP through CloudLinux/CageFS as the owning cPanel account
- records execution history and job-level statistics
- marks the queue item COMPLETED or FAILED
- retry policy remains outside the executor
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

LIB_DIR = "/usr/local/lib/wpcron"
DB_FILE = "/var/lib/wpcron/scheduler.db"
CAGEFS_ENTER_USER = "/usr/sbin/cagefs_enter_user"
LOG_DIR = "/var/log/wpcron/executions"

DEFAULT_TIMEOUT = 300
LEASE_MARGIN = 60


class ExecutorError(RuntimeError):
  pass


def now() -> int:
  return int(time.time())


def worker_id() -> str:
  return f"{socket.gethostname()}:{os.getpid()}"


class Executor:
  def __init__(
    self,
    db_path: str = DB_FILE,
    timeout: int = DEFAULT_TIMEOUT,
    log_dir: str = LOG_DIR,
  ):
    self.db_path = db_path
    self.timeout = timeout
    self.log_dir = Path(log_dir)

    if not os.path.isfile(CAGEFS_ENTER_USER):
      raise ExecutorError(
        f"CloudLinux execution helper not found: {CAGEFS_ENTER_USER}"
      )

    if timeout <= 0:
      raise ExecutorError("timeout must be greater than zero")

  def _repositories(self):
    # Always load the installed scheduler libraries from LIB_DIR.
    # When executor.py is run from the Git checkout, the current
    # directory is already on sys.path and could otherwise cause Python
    # to import a different repositories.py than the installed one.
    while LIB_DIR in sys.path:
      sys.path.remove(LIB_DIR)
    sys.path.insert(0, LIB_DIR)

    from database import Database
    from repositories import SchedulerRepositories

    return SchedulerRepositories(Database(self.db_path))

  @staticmethod
  def _validate_job(job):
    if job is None:
      raise ExecutorError("job does not exist")

    if not job.enabled:
      raise ExecutorError("job is disabled")

    # Resolve the actual filesystem paths at execution time. This matters
    # if the account home moved or the target was replaced since
    # reconciliation.
    home = os.path.realpath(job.home_directory)
    path = os.path.realpath(job.wp_cron_path)

    if not os.path.isdir(home):
      raise ExecutorError("account home does not exist")

    try:
      if os.path.commonpath([home, path]) != home:
        raise ExecutorError("wp-cron path is outside account home")
    except ValueError:
      raise ExecutorError("invalid home/path boundary")

    if os.path.basename(path) != "wp-cron.php":
      raise ExecutorError("target is not wp-cron.php")

    if not os.path.isfile(path):
      raise ExecutorError("wp-cron.php does not exist")

    if not job.php_executable:
      raise ExecutorError("PHP executable is not recorded")

    php = os.path.realpath(job.php_executable)

    if not os.path.isfile(php):
      raise ExecutorError("PHP executable does not exist")

    if not os.access(php, os.X_OK):
      raise ExecutorError("PHP executable is not executable")

    return path, php

  @staticmethod
  def _command(job, php, path):
    # Never execute jobs.command through a shell.
    return [
      CAGEFS_ENTER_USER,
      "--no-fork",
      job.cpanel_account,
      php,
      path,
    ]

  def _log_path(self, queue_id: int) -> Path:
    self.log_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    return self.log_dir / f"queue-{queue_id}.log"

  def execute(self, queue_id: int, *, dry_run: bool = False) -> int:
    repos = self._repositories()

    queue_item = repos.queue.get(queue_id)
    if queue_item is None:
      raise ExecutorError(f"queue item {queue_id} does not exist")

    if queue_item.status not in ("PENDING", "RETRY"):
      raise ExecutorError(
        f"queue item {queue_id} is not executable: "
        f"status={queue_item.status}"
      )

    job = repos.jobs.get(queue_item.job_id)
    path, php = self._validate_job(job)
    command = self._command(job, php, path)

    wid = worker_id()
    lease_until = now() + self.timeout + LEASE_MARGIN

    print(f"Queue ID   : {queue_id}")
    print(f"Job ID     : {job.id}")
    print(f"Account    : {job.cpanel_account}")
    print(f"PHP        : {php}")
    print(f"Script     : {path}")
    print(f"Timeout    : {self.timeout}s")
    print(f"Worker     : {wid}")

    if dry_run:
      print(f"Command    : {' '.join(command)}")
      print("Decision   : WOULD_EXECUTE")
      return 0

    if not repos.queue.claim(
      queue_id,
      worker_id=wid,
      lease_until=lease_until,
    ):
      raise ExecutorError(
        "queue item could not be claimed; it may already be running"
      )

    queue_item = repos.queue.get(queue_id)
    if queue_item is None:
      raise ExecutorError("queue item disappeared after claim")

    execution_id = repos.executions.create(
      job_id=job.id,
      queue_id=queue_id,
      account=job.cpanel_account,
      domain=job.domain,
      queued_at=queue_item.queued_at,
      worker_id=wid,
      log_path=str(self._log_path(queue_id)),
    )

    if not repos.executions.start(execution_id, worker_id=wid):
      repos.queue.fail(queue_id)
      raise ExecutorError(
        f"execution {execution_id} could not be started"
      )

    log_path = Path(
      repos.executions.get(execution_id).log_path
    )

    exit_code: Optional[int] = None
    status = "FAILED"
    failure_reason: Optional[str] = None
    started_at = now()

    try:
      with open(log_path, "ab", buffering=0) as log:
        header = (
          f"worker={wid}\n"
          f"account={job.cpanel_account}\n"
          f"job_id={job.id}\n"
          f"queue_id={queue_id}\n"
          f"command={' '.join(command)}\n"
          f"started_at={started_at}\n"
          f"\n"
        )
        log.write(header.encode())

        try:
          completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            timeout=self.timeout,
            check=False,
          )
          exit_code = completed.returncode

          if exit_code == 0:
            status = "SUCCESS"
          else:
            failure_reason = (
              f"process exited with code {exit_code}"
            )

        except subprocess.TimeoutExpired:
          failure_reason = (
            f"execution exceeded timeout of {self.timeout}s"
          )

    except Exception as exc:
      failure_reason = f"executor error: {exc}"

    finished_at = now()

    if not repos.executions.finish(
      execution_id,
      status=status,
      exit_code=exit_code,
      finished_at=finished_at,
      failure_reason=failure_reason,
    ):
      raise ExecutorError(
        f"execution {execution_id} could not be finalized"
      )

    if not repos.jobs.record_execution_result(
      job.id,
      started_at=started_at,
      finished_at=finished_at,
      status=status,
    ):
      raise ExecutorError(
        f"job {job.id} execution statistics could not be updated"
      )

    if status == "SUCCESS":
      if not repos.queue.complete(queue_id):
        raise ExecutorError(
          f"queue {queue_id} could not be completed"
        )
    else:
      # Retry policy is deliberately not decided here.
      if not repos.queue.fail(queue_id):
        raise ExecutorError(
          f"queue {queue_id} could not be marked failed"
        )

    print(f"Execution  : {execution_id}")
    print(f"Status     : {status}")
    print(f"Exit code  : {exit_code}")
    print(f"Log        : {log_path}")

    if failure_reason:
      print(f"Reason     : {failure_reason}")

    return 0 if status == "SUCCESS" else 1


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Execute one admitted WP Cron queue item"
  )
  parser.add_argument("--queue-id", type=int, required=True)
  parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
  parser.add_argument("--dry-run", action="store_true")

  args = parser.parse_args()

  if os.geteuid() != 0:
    print("[FAIL] executor must run as root", file=sys.stderr)
    return 1

  try:
    return Executor(timeout=args.timeout).execute(
      args.queue_id,
      dry_run=args.dry_run,
    )
  except Exception as exc:
    print(f"[FAIL] {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
