#!/usr/bin/env python3
"""
WP Cron Scheduler - Phase 2 / Step 2 dispatcher.

The dispatcher is deliberately small.

Responsibilities:
- recover expired queue leases
- find eligible PENDING/RETRY queue items
- hand work to the validated executor
- never execute customer commands itself

The executor remains responsible for:
- claiming the queue item
- validating the job/path/PHP executable
- running through CageFS
- recording execution history
- completing or failing the queue item

This first dispatcher version is single-shot. It does not run as a daemon
and does not introduce concurrency. A future worker loop can build on this
behavior after the single-shot path is proven.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Optional

LIB_DIR = "/usr/local/lib/wpcron"
DB_FILE = "/var/lib/wpcron/scheduler.db"
EXECUTOR = f"{LIB_DIR}/executor.py"


class DispatcherError(RuntimeError):
  pass


class Dispatcher:
  def __init__(
    self,
    db_path: str = DB_FILE,
    executor_path: str = EXECUTOR,
  ):
    self.db_path = db_path
    self.executor_path = executor_path

    if not os.path.isfile(self.executor_path):
      raise DispatcherError(
        f"executor not found: {self.executor_path}"
      )

  def _repositories(self):
    # Always use the installed scheduler libraries.
    while LIB_DIR in sys.path:
      sys.path.remove(LIB_DIR)
    sys.path.insert(0, LIB_DIR)

    from database import Database
    from repositories import SchedulerRepositories

    return SchedulerRepositories(Database(self.db_path))

  def recover_expired_leases(self) -> int:
    repos = self._repositories()
    return repos.queue.release_expired_leases()

  def pending(self, limit: int) -> list:
    repos = self._repositories()
    return repos.queue.list_pending(limit=limit)

  def dispatch(
    self,
    *,
    limit: int = 1,
    queue_id: Optional[int] = None,
    timeout: int = 300,
    dry_run: bool = False,
  ) -> int:
    if limit <= 0:
      raise DispatcherError("limit must be greater than zero")

    if timeout <= 0:
      raise DispatcherError("timeout must be greater than zero")

    recovered = self.recover_expired_leases()

    if recovered:
      print(f"Expired leases recovered : {recovered}")

    repos = self._repositories()

    if queue_id is not None:
      queue_item = repos.queue.get(queue_id)

      if queue_item is None:
        raise DispatcherError(
          f"queue item {queue_id} does not exist"
        )

      if queue_item.status not in ("PENDING", "RETRY"):
        print(
          f"Queue ID {queue_id} is not eligible: "
          f"status={queue_item.status}"
        )
        return 0

      if queue_item.available_at > int(__import__("time").time()):
        print(
          f"Queue ID {queue_id} is not available yet: "
          f"available_at={queue_item.available_at}"
        )
        return 0

      items = [queue_item]
    else:
      items = repos.queue.list_pending(limit=limit)

    if not items:
      print("No pending queue items.")
      return 0

    results = []

    for item in items:
      if item.id is None:
        continue

      print()
      print(f"Dispatching queue ID : {item.id}")
      print(f"Job ID               : {item.job_id}")
      print(f"Queue status         : {item.status}")

      command = [
        sys.executable,
        self.executor_path,
        "--queue-id",
        str(item.id),
        "--timeout",
        str(timeout),
      ]

      if dry_run:
        command.append("--dry-run")

      env = os.environ.copy()
      env["PYTHONDONTWRITEBYTECODE"] = "1"

      result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        check=False,
        env=env,
      )

      results.append((item.id, result.returncode))

      if result.returncode != 0:
        print(
          f"Queue ID {item.id}: executor returned "
          f"{result.returncode}"
        )

        if queue_id is not None:
          return result.returncode

    succeeded = sum(1 for _, code in results if code == 0)
    failed = len(results) - succeeded

    print()
    print("## Dispatcher summary")
    print(f"Queue items selected : {len(results)}")
    print(f"Executor successes   : {succeeded}")
    print(f"Executor failures    : {failed}")

    return 0 if failed == 0 else 1


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Dispatch pending WP Cron queue items"
  )

  parser.add_argument(
    "--once",
    action="store_true",
    help="process one dispatcher pass",
  )
  parser.add_argument(
    "--queue-id",
    type=int,
    help="dispatch one specific queue item",
  )
  parser.add_argument(
    "--limit",
    type=int,
    default=1,
    help="maximum queue items to dispatch in this pass",
  )
  parser.add_argument(
    "--timeout",
    type=int,
    default=300,
    help="executor timeout in seconds",
  )
  parser.add_argument(
    "--dry-run",
    action="store_true",
    help="show executor decisions without executing customer code",
  )

  args = parser.parse_args()

  if os.geteuid() != 0:
    print("[FAIL] dispatcher must run as root", file=sys.stderr)
    return 1

  if not args.once and args.queue_id is None:
    parser.error(
      "use --once or --queue-id for the single-shot dispatcher"
    )

  try:
    return Dispatcher().dispatch(
      limit=args.limit,
      queue_id=args.queue_id,
      timeout=args.timeout,
      dry_run=args.dry_run,
    )
  except Exception as exc:
    print(f"[FAIL] {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
