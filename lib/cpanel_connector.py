#!/usr/bin/env python3
"""
Read-only cPanel discovery connector for WP Cron Scheduler.

Cron discovery uses:
    cpapi2 --output=json --user=<account> Cron fetchcron

The target cPanel version was observed to:
- print "no crontab for USER" before valid JSON for accounts without a crontab;
- return actual cron records from fetchcron;
- return a count record mixed with cron records from listcron.

This connector is READ-ONLY.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_WHMAPI = "/usr/local/cpanel/bin/whmapi1"
DEFAULT_CPAPI2 = "/usr/local/cpanel/bin/cpapi2"


class CPanelConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class CPanelAccount:
    username: str
    uid: Optional[int]
    gid: Optional[int]
    home: Optional[str]
    domain: Optional[str]
    owner: Optional[str]
    suspended: Optional[bool]


@dataclass(frozen=True)
class CronEntry:
    account: str
    line: Optional[int]
    linekey: Optional[int]
    commandnumber: Optional[int]
    cron_type: Optional[str]
    minute: str
    hour: str
    day: str
    month: str
    weekday: str
    command: str

    @property
    def schedule_expression(self) -> str:
        return f"{self.minute} {self.hour} {self.day} {self.month} {self.weekday}"


class CommandRunner:
    def __init__(self, *, whmapi=DEFAULT_WHMAPI, cpapi2=DEFAULT_CPAPI2):
        self.whmapi = whmapi
        self.cpapi2 = cpapi2

    def run_json(self, command):
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=60,
        )

        if completed.returncode != 0:
            raise CPanelConnectorError(
                f"Command failed ({completed.returncode}): "
                f"{' '.join(command)}: {completed.stderr.strip()}"
            )

        return self._parse_json_output(completed.stdout, command)

    @staticmethod
    def _parse_json_output(output, command):
        text = output.strip()

        if not text:
            raise CPanelConnectorError(
                f"Command returned empty output: {' '.join(command)}"
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")

            if start < 0 or end <= start:
                raise CPanelConnectorError(
                    f"Command returned invalid JSON: {' '.join(command)}"
                )

            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise CPanelConnectorError(
                    f"Command returned invalid JSON: {' '.join(command)}"
                ) from exc

        if not isinstance(data, dict):
            raise CPanelConnectorError(
                f"Command returned non-object JSON: {' '.join(command)}"
            )

        return data


class CPanelConnector:
    """Normalized, read-only cPanel discovery interface."""

    def __init__(self, runner=None):
        self.runner = runner or CommandRunner()

    def list_accounts(self):
        result = self.runner.run_json([
            self.runner.whmapi,
            "--output=json",
            "listaccts",
        ])

        self._require_whm_success(result, "listaccts")

        accounts = result.get("data", {}).get("acct", [])
        if not isinstance(accounts, list):
            raise CPanelConnectorError(
                "Unexpected listaccts response: data.acct is not a list"
            )

        normalized = []

        for item in accounts:
            if not isinstance(item, dict):
                continue

            username = self._string(item.get("user"))
            if not username:
                continue

            normalized.append(
                CPanelAccount(
                    username=username,
                    uid=self._integer(item.get("uid")),
                    gid=self._integer(item.get("gid")),
                    home=self._string(item.get("homedir")),
                    domain=self._string(item.get("domain")),
                    owner=self._string(item.get("owner")),
                    suspended=self._boolean(item.get("suspended")),
                )
            )

        return normalized

    def list_crons(self, username):
        self._validate_username(username)

        result = self.runner.run_json([
            self.runner.cpapi2,
            "--output=json",
            "--user",
            username,
            "Cron",
            "fetchcron",
        ])

        self._require_cpapi_success(result, f"Cron::fetchcron ({username})")

        data = result.get("cpanelresult", {}).get("data", [])

        if data is None:
            return []

        if not isinstance(data, list):
            raise CPanelConnectorError(
                f"Unexpected Cron::fetchcron response for {username}"
            )

        normalized = []

        for item in data:
            if not isinstance(item, dict):
                continue

            normalized.append(
                CronEntry(
                    account=username,
                    line=self._integer(item.get("line")),
                    linekey=self._integer(item.get("linekey")),
                    commandnumber=self._integer(item.get("commandnumber")),
                    cron_type=self._string(item.get("type")),
                    minute=self._cron_field(item, "minute"),
                    hour=self._cron_field(item, "hour"),
                    day=self._cron_field(item, "day"),
                    month=self._cron_field(item, "month"),
                    weekday=self._cron_field(item, "weekday"),
                    command=self._string(item.get("command")) or "",
                )
            )

        return normalized

    def discover_all_crons(self, include_empty_accounts=False):
        discovered = {}

        for account in self.list_accounts():
            crons = self.list_crons(account.username)
            if crons or include_empty_accounts:
                discovered[account.username] = crons

        return discovered

    @staticmethod
    def _validate_username(username):
        if not username or username.startswith("-"):
            raise ValueError("Invalid cPanel username")

        allowed = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789_-"
        )

        if any(char not in allowed for char in username):
            raise ValueError(f"Invalid cPanel username: {username!r}")

    @staticmethod
    def _require_whm_success(result, operation):
        metadata = result.get("metadata", {})
        if metadata.get("result") != 1:
            raise CPanelConnectorError(
                f"{operation} failed: {metadata.get('reason', 'unknown error')}"
            )

    @staticmethod
    def _require_cpapi_success(result, operation):
        cpanelresult = result.get("cpanelresult", {})
        event = cpanelresult.get("event", {})
        if event.get("result") != 1:
            raise CPanelConnectorError(
                f"{operation} failed: {event.get('reason', 'unknown error')}"
            )

    @staticmethod
    def _string(value):
        if value is None:
            return None
        value = str(value).strip()
        return value if value else None

    @staticmethod
    def _integer(value):
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _boolean(value):
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        value = str(value).strip().lower()
        if value in {"1", "true", "yes"}:
            return True
        if value in {"0", "false", "no"}:
            return False
        return None

    @classmethod
    def _cron_field(cls, item, name):
        return cls._string(item.get(name)) or "*"


def is_wp_cron_command(command):
    if not command:
        return False

    normalized = command.replace('"', "").replace("'", "")
    tokens = normalized.split()

    return any(
        token == "wp-cron.php" or token.endswith("/wp-cron.php")
        for token in tokens
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only cPanel WP Cron discovery diagnostic"
    )
    parser.add_argument("--accounts", action="store_true")
    parser.add_argument("--crons", metavar="USERNAME")
    parser.add_argument("--wp-crons", action="store_true")
    args = parser.parse_args()

    connector = CPanelConnector()

    if args.accounts:
        for account in connector.list_accounts():
            print(
                f"{account.username}\t"
                f"uid={account.uid}\t"
                f"gid={account.gid}\t"
                f"home={account.home}\t"
                f"domain={account.domain}"
            )
    elif args.crons:
        for cron in connector.list_crons(args.crons):
            print(
                f"line={cron.line}\t"
                f"linekey={cron.linekey}\t"
                f"type={cron.cron_type}\t"
                f"{cron.schedule_expression}\t"
                f"{cron.command}"
            )
    elif args.wp_crons:
        for account, crons in connector.discover_all_crons().items():
            for cron in crons:
                if is_wp_cron_command(cron.command):
                    print(
                        f"{account}\t"
                        f"line={cron.line}\t"
                        f"linekey={cron.linekey}\t"
                        f"{cron.schedule_expression}\t"
                        f"{cron.command}"
                    )
    else:
        parser.print_help()

