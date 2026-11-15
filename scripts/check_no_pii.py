#!/usr/bin/env python3
"""Refuse to publish personal data, credentials or private paths.

Two modes, both exit non-zero on the first problem found:

    python3 scripts/check_no_pii.py --history    # every blob and message in every ref
    python3 scripts/check_no_pii.py --worktree   # the files git tracks right now

This file carries only patterns that are the same for every checkout: home
paths, addresses, credentials. Anything specific to one machine or one person
belongs in a private denylist, never here:

    ALMATHUS_PII_PATTERNS   newline-separated regexes, extra things to refuse
    ALMATHUS_PII_IGNORE     newline-separated paths that must stay ignored

Both are unset in CI. Run with them set on a machine that holds the private
material, and both scans cover it without the repository ever naming it.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

#: (regex, human name). Every pattern is valid both as a POSIX extended regex
#: for `git grep` and as a Python regex, so the two scans cannot drift apart.
#: No \b: BSD and GNU regex disagree about what it means.
PATTERNS: list[tuple[str, str]] = [
    (r"/(Users|home)/[A-Za-z0-9._-]+", "absolute home path"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "email address"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"sk-[A-Za-z0-9]{20,}", "API key"),
]

#: Addresses that are meant to be there. Bot identities and GitHub's own mail
#: all end in these; a personal address at any other domain still fails.
EMAIL_OK_DOMAINS = ("@users.noreply.github.com", "@github.com")

#: Local runtime data. These are named in the README as the folders the game
#: writes to, so listing them here gives nothing away.
IGNORED = (
    "results/results.csv",
    "stats/20260101-000000-addition.json",
    "models/any-model/x",
    "prefs.json",
)

#: The published licence record and the art the README shows. If one of these
#: becomes ignored, the public repo silently loses an attribution or an image.
NOT_IGNORED = (
    "LICENSE",
    "NOTICE",
    "assets/sounds/CREDITS.md",
    "assets/flags/README.md",
)


def extra_patterns() -> list[tuple[str, str]]:
    raw = os.environ.get("ALMATHUS_PII_PATTERNS", "")
    return [(line, "private denylist") for line in raw.splitlines() if line.strip()]


def all_patterns() -> list[tuple[str, str]]:
    return PATTERNS + extra_patterns()


def extra_ignored() -> tuple[str, ...]:
    raw = os.environ.get("ALMATHUS_PII_IGNORE", "")
    return tuple(line.strip() for line in raw.splitlines() if line.strip())


def run(*args: str) -> subprocess.CompletedProcess[str]:
    # argv is built by this file alone: no shell, no interpolation, no user input.
    return subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603


def reason_for(text: str, patterns: list[tuple[str, str]]) -> str | None:
    """Name of the first pattern that matches `text`, honouring the email exception."""
    for pattern, name in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            if name == "email address" and match.group(0).lower().endswith(
                EMAIL_OK_DOMAINS
            ):
                continue
            return name
    return None


def check_worktree(patterns: list[tuple[str, str]]) -> list[str]:
    problems: list[str] = []
    listing = run("git", "ls-files", "-z")
    if listing.returncode != 0:
        return ["git ls-files failed — is this a repository?"]
    for name in listing.stdout.split("\0"):
        if not name:
            continue
        try:
            raw = Path(name).read_bytes()
        except OSError:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary
        for number, line in enumerate(text.splitlines(), 1):
            reason = reason_for(line, patterns)
            if reason:
                problems.append(f"{name}:{number}: {reason}")
    return problems


def check_history(patterns: list[tuple[str, str]]) -> list[str]:
    problems: list[str] = []
    revs = run("git", "rev-list", "--all").stdout.split()
    if not revs:
        return ["no commits found"]

    for pattern, name in patterns:
        result = run(
            "git", "grep", "-I", "-n", "-E", "-i", "--no-color", "-e", pattern, *revs
        )
        if result.returncode not in (0, 1):
            problems.append(
                f"git grep failed for {name}: {result.stderr.strip()[:200]}"
            )
            continue
        for line in result.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) < 3:
                continue
            # Re-decide from the line itself: a grep hit may be a whitelisted
            # address even though some other pattern in the alternation matched.
            if reason_for(parts[-1], patterns) is None:
                continue
            problems.append(f"{parts[1]}: {name} in a committed blob")

    for message in run("git", "log", "--all", "--format=%B%x00").stdout.split("\0"):
        reason = reason_for(message, patterns)
        if reason:
            problems.append(f"commit message: {reason} in {message.strip()[:100]!r}")
    return problems


def check_ignore_rules() -> list[str]:
    problems: list[str] = []
    for path in IGNORED + extra_ignored():
        if run("git", "check-ignore", "-q", path).returncode != 0:
            problems.append(f".gitignore no longer ignores {path}")
    for path in NOT_IGNORED:
        if run("git", "check-ignore", "-q", path).returncode == 0:
            problems.append(
                f".gitignore wrongly ignores {path} — it must stay published"
            )
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history", action="store_true", help="scan every blob and message"
    )
    parser.add_argument(
        "--worktree", action="store_true", help="scan the tracked files"
    )
    args = parser.parse_args(argv)
    if not (args.history or args.worktree):
        parser.error("choose --history or --worktree")

    patterns = all_patterns()
    problems: list[str] = []
    if args.history:
        problems += check_history(patterns)
    if args.worktree:
        problems += check_worktree(patterns)
    problems += check_ignore_rules()

    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} problem(s).", file=sys.stderr)
        return 1
    print("no personal data, credentials or private paths found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
