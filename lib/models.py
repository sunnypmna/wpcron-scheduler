#!/usr/bin/env python3
"""
WP Cron Scheduler models.

Models are deliberately plain data objects. They contain no scheduling
decisions and no database access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Job:
  id: Optional[int]
  job_key: str
  cpanel_account: str
  uid: int
  gid: int
  home_directory: str
  domain: Optional[str]
  wp_cron_path: str
  command: Optional[str]
  php_executable: Optional[str]
  relative_script_path: Optional[str]
  site_root: Optional[str]

  minute: str
  hour: str
  day: str
  month: str
  weekday: str
  schedule_expression: str

  enabled: bool

  created_at: int
  updated_at: int
  last_seen: Optional[int]

  last_triggered: Optional[int]
  last_queued: Optional[int]
  last_started: Optional[int]
  last_finished: Optional[int]
  last_status: Optional[str]

  consecutive_failures: int
  total_runs: int
  total_successes: int
  total_failures: int


@dataclass(frozen=True)
class QueueItem:
  id: Optional[int]
  job_id: int

  queued_at: int
  available_at: int

  status: str

  claimed_at: Optional[int]
  lease_until: Optional[int]
  worker_id: Optional[str]

  attempt: int

  created_at: int
  updated_at: int


@dataclass(frozen=True)
class Execution:
  id: Optional[int]
  job_id: int
  queue_id: Optional[int]

  account: str
  domain: Optional[str]

  queued_at: int
  started_at: Optional[int]
  finished_at: Optional[int]

  duration: Optional[float]
  queue_delay: Optional[float]

  status: str

  exit_code: Optional[int]
  worker_id: Optional[str]

  failure_reason: Optional[str]
  log_path: Optional[str]

  created_at: int
