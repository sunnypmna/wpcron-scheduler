#!/usr/bin/env python3
"""
WP Cron Scheduler - local system/account discovery connector.

This connector is deliberately read-only.

It uses the operating system's account database as the authoritative source
for:
    username
    UID
    GID
    home directory
    login shell

It also provides safe filesystem checks for an account's home directory.

It does NOT:
    - create users
    - modify users
    - change permissions
    - execute commands as another user
    - modify cron
    - modify the scheduler database
"""

from __future__ import annotations

import os
import pwd
import grp
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class SystemConnectorError(RuntimeError):
    """Raised when local account discovery fails."""


@dataclass(frozen=True)
class SystemAccount:
    username: str
    uid: int
    gid: int
    home: str
    shell: str

    @property
    def home_exists(self) -> bool:
        return os.path.isdir(self.home)

    @property
    def home_owner_uid(self) -> Optional[int]:
        try:
            return os.stat(self.home).st_uid
        except (FileNotFoundError, PermissionError, OSError):
            return None

    @property
    def home_owner_gid(self) -> Optional[int]:
        try:
            return os.stat(self.home).st_gid
        except (FileNotFoundError, PermissionError, OSError):
            return None


class SystemAccountConnector:
    """Read-only interface to the local Unix account database."""

    def get_account(self, username: str) -> Optional[SystemAccount]:
        self._validate_username(username)

        try:
            entry = pwd.getpwnam(username)
        except KeyError:
            return None
        except OSError as exc:
            raise SystemConnectorError(
                f"Unable to read account {username!r}: {exc}"
            ) from exc

        return self._from_passwd_entry(entry)

    def require_account(self, username: str) -> SystemAccount:
        account = self.get_account(username)

        if account is None:
            raise SystemConnectorError(
                f"Local system account does not exist: {username}"
            )

        return account

    def list_accounts(self) -> list[SystemAccount]:
        accounts = []

        try:
            entries = pwd.getpwall()
        except OSError as exc:
            raise SystemConnectorError(
                f"Unable to enumerate local accounts: {exc}"
            ) from exc

        for entry in entries:
            accounts.append(self._from_passwd_entry(entry))

        return accounts

    def home_status(self, username: str) -> dict:
        account = self.require_account(username)

        path = Path(account.home)

        result = {
            "username": account.username,
            "uid": account.uid,
            "gid": account.gid,
            "home": account.home,
            "exists": False,
            "is_directory": False,
            "owner_uid": None,
            "owner_gid": None,
            "owner_matches_uid": False,
            "group_matches_gid": False,
        }

        try:
            stat = path.stat()
        except FileNotFoundError:
            return result
        except PermissionError as exc:
            raise SystemConnectorError(
                f"Permission denied while inspecting {account.home}: {exc}"
            ) from exc
        except OSError as exc:
            raise SystemConnectorError(
                f"Unable to inspect {account.home}: {exc}"
            ) from exc

        result["exists"] = True
        result["is_directory"] = path.is_dir()
        result["owner_uid"] = stat.st_uid
        result["owner_gid"] = stat.st_gid
        result["owner_matches_uid"] = stat.st_uid == account.uid
        result["group_matches_gid"] = stat.st_gid == account.gid

        return result

    @staticmethod
    def _from_passwd_entry(entry) -> SystemAccount:
        return SystemAccount(
            username=entry.pw_name,
            uid=int(entry.pw_uid),
            gid=int(entry.pw_gid),
            home=entry.pw_dir,
            shell=entry.pw_shell,
        )

    @staticmethod
    def _validate_username(username: str) -> None:
        if not username:
            raise ValueError("Username cannot be empty")

        if username.startswith("-"):
            raise ValueError("Invalid username")

        allowed = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789_-"
        )

        if any(char not in allowed for char in username):
            raise ValueError(f"Invalid username: {username!r}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only local system account discovery"
    )

    parser.add_argument(
        "--account",
        metavar="USERNAME",
        help="Show one local account",
    )

    parser.add_argument(
        "--home-status",
        metavar="USERNAME",
        help="Inspect one account's home directory",
    )

    parser.add_argument(
        "--accounts",
        action="store_true",
        help="List local accounts",
    )

    args = parser.parse_args()

    connector = SystemAccountConnector()

    if args.account:
        account = connector.get_account(args.account)

        if account is None:
            raise SystemExit(f"Account not found: {args.account}")

        print(f"username={account.username}")
        print(f"uid={account.uid}")
        print(f"gid={account.gid}")
        print(f"home={account.home}")
        print(f"shell={account.shell}")

    elif args.home_status:
        status = connector.home_status(args.home_status)

        for key, value in status.items():
            print(f"{key}={value}")

    elif args.accounts:
        for account in connector.list_accounts():
            print(
                f"{account.username}\t"
                f"uid={account.uid}\t"
                f"gid={account.gid}\t"
                f"home={account.home}\t"
                f"shell={account.shell}"
            )

    else:
        parser.print_help()
