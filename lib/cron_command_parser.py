#!/usr/bin/env python3
"""
WP Cron Scheduler - cron command/path parser.

This module does NOT execute commands.

Its job is only to determine whether an existing cPanel cron command
directly invokes a WordPress wp-cron.php script and, if so, normalize
the discovered path.

Security boundary:
    account home
        |
        +-- resolved wp-cron.php path must remain underneath home

The parser intentionally does not try to understand every possible shell
language. Complex shell wrappers are rejected unless a clear wp-cron.php
path can be identified safely.
"""

from __future__ import annotations

import os
import posixpath
import shlex
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ParsedCronCommand:
    account: str
    account_home: str
    command: str
    php_executable: Optional[str]
    script_path: str
    site_root: str
    relative_script_path: str


class CronCommandParser:
    def parse(
        self,
        *,
        account: str,
        account_home: str,
        command: str,
    ) -> Optional[ParsedCronCommand]:
        if not account or not account_home or not command:
            return None

        account_home = self._normalize_home(account_home)

        tokens = self._tokenize(command)
        if not tokens:
            return None

        script_index = self._find_wp_cron_token(tokens)

        if script_index is None:
            return None

        script_token = tokens[script_index]

        if not self._is_absolute_path(script_token):
            # We deliberately require an absolute script path. This avoids
            # ambiguity around the cron user's PATH and working directory.
            return None

        script_path = self._canonical_path(script_token)

        if not self._under_home(script_path, account_home):
            return None

        relative = os.path.relpath(script_path, account_home)

        if relative == "." or relative.startswith("../"):
            return None

        site_root = os.path.dirname(script_path)

        php_executable = self._find_php_executable(tokens, script_index)

        return ParsedCronCommand(
            account=account,
            account_home=account_home,
            command=command,
            php_executable=php_executable,
            script_path=script_path,
            site_root=site_root,
            relative_script_path=relative,
        )

    @staticmethod
    def _tokenize(command: str) -> list[str]:
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return []

        # Reject shell control/compound operators. Redirections such as
        # >/dev/null 2>&1 are common in cron jobs and are safe to ignore
        # for path extraction, so they are intentionally allowed.
        dangerous_operators = {
            ";", "&&", "||", "|", "|&", "&", "`",
        }

        if any(token in dangerous_operators for token in tokens):
            return []

        return tokens

    @staticmethod
    def _find_wp_cron_token(tokens: list[str]) -> Optional[int]:
        for index, token in enumerate(tokens):
            if not token:
                continue

            # Require the final path component to be exactly wp-cron.php.
            basename = posixpath.basename(token)

            if basename == "wp-cron.php":
                return index

        return None

    @staticmethod
    def _find_php_executable(
        tokens: list[str],
        script_index: int,
    ) -> Optional[str]:
        # Only inspect the executable/arguments before wp-cron.php.
        before = tokens[:script_index]

        if not before:
            return None

        # Typical:
        #   /usr/bin/php /home/user/public_html/wp-cron.php
        #
        # Also accept:
        #   php /home/user/public_html/wp-cron.php
        #
        # We do not require PHP to be present because the parser is only
        # identifying the script path. Validation of the executable belongs
        # to the later execution layer.
        for token in reversed(before):
            base = posixpath.basename(token)

            if base == "php" or base.startswith("php") and base[3:].isdigit():
                return token

        return None

    @staticmethod
    def _normalize_home(home: str) -> str:
        return os.path.normpath(home)

    @staticmethod
    def _canonical_path(path: str) -> str:
        # realpath is intentionally NOT used here. We are parsing a cron
        # command, not resolving filesystem symlinks. The execution/validation
        # layer will perform filesystem-aware checks later.
        return posixpath.normpath(path)

    @staticmethod
    def _is_absolute_path(path: str) -> bool:
        return path.startswith("/")

    @staticmethod
    def _under_home(path: str, home: str) -> bool:
        try:
            return os.path.commonpath([path, home]) == home
        except ValueError:
            return False


def parse_cron_command(
    *,
    account: str,
    account_home: str,
    command: str,
) -> Optional[ParsedCronCommand]:
    return CronCommandParser().parse(
        account=account,
        account_home=account_home,
        command=command,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse a cPanel cron command for wp-cron.php"
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--command", required=True)

    args = parser.parse_args()

    result = parse_cron_command(
        account=args.account,
        account_home=args.home,
        command=args.command,
    )

    if result is None:
        print("NOT_WP_CRON")
        raise SystemExit(1)

    print(f"account={result.account}")
    print(f"account_home={result.account_home}")
    print(f"php_executable={result.php_executable}")
    print(f"script_path={result.script_path}")
    print(f"site_root={result.site_root}")
    print(f"relative_script_path={result.relative_script_path}")
