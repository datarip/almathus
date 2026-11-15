#!/usr/bin/env python3
"""Refuse to publish personal data, credentials or private paths.

Two modes, both exit non-zero on the first problem found:

    python3 scripts/check_no_pii.py --history    # every blob and message in every ref
    python3 scripts/check_no_pii.py --worktree   # the files git tracks right now

The point is not the current tree — that was cleaned by hand once and would stay
clean for a while on its own. The point is that a commit, a paste or a careless
`git add -A` cannot quietly put the child's session log, a home path or a token
back into a public repository, and that the ignore rules that keep them out
cannot be deleted without CI noticing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# This file is the one place the patterns are written down, so it matches
# itself. It is never a source of data.
SELF = "scripts/check_no_pii.py"

#: (regex, human name). Every pattern is valid both as a POSIX extended regex
#: for `git grep` and as a Python regex, so the two scans cannot drift apart.
#: No \b: BSD and GNU regex disagree about what it means.
PATTERNS: list[tuple[str, str]] = [
    (r"/(Users|home)/[A-Za-z0-9._-]+", "absolute home path"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "email address"),
    (
        r"(^|[^A-Za-z])(daughter|my son|my child|my wife|my husband)([^A-Za-z]|$)",
        "family detail",
    ),
    (r"workplenty", "local account name"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"sk-[A-Za-z0-9]{20,}", "API key"),
]

#: Addresses that are meant to be there. Dependabot's own commit messages carry
#: support@github.com, and every bot identity ends in the GitHub privacy domain.
#: A personal address at any other domain still fails.
EMAIL_OK_DOMAINS = ("@users.noreply.github.com", "@github.com")

#: Paths that must never be tracked. Kept in one list because a stray
#: `.gitignore` edit is exactly how they would come back.
IGNORED = (
    "results/results.csv",
    "stats/20260101-000000-addition.json",
    "models/vosk-model-small-fr-0.22/x",
    "prefs.json",
    ".claude/settings.local.json",
    "docs/shipping.md",
    "screenshots/boy-1-welcome.png",
    "assets/README.md",
    "AGENTS.md",
    "archived/assets/old/mario.svg",
)

#: Paths that must stay tracked: the published licence record and the two
#: screenshots the README shows. If one of these becomes ignored, the public
#: repo silently loses an attribution or an image.
NOT_IGNORED = (
    "assets/screenshots/boy-mode.png",
    "assets/screenshots/girly-mode.png",
    "assets/sounds/CREDITS.md",
    "assets/flags/README.md",
    "LICENSE",
    "NOTICE",
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    # argv is built by this file alone: no shell, no interpolation, no user input.
    return subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603


def reason_for(text: str) -> str | None:
    """Name of the first pattern that matches `text`, honouring the email exception."""
    for pattern, name in PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            if name == "email address" and match.group(0).lower().endswith(
                EMAIL_OK_DOMAINS
            ):
                continue
            return name
    return None


def check_worktree() -> list[str]:
    problems: list[str] = []
    listing = run("git", "ls-files", "-z")
    if listing.returncode != 0:
        return ["git ls-files failed — is this a repository?"]
    for name in listing.stdout.split("\0"):
        if not name or name == SELF:
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
            reason = reason_for(line)
            if reason:
                problems.append(f"{name}:{number}: {reason}")
    return problems


def check_history() -> list[str]:
    problems: list[str] = []
    revs = run("git", "rev-list", "--all").stdout.split()
    if not revs:
        return ["no commits found"]

    for pattern, name in PATTERNS:
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
            path = parts[1]
            if path == SELF:
                continue
            if name == "email address" and parts[-1].strip().lower().endswith(
                EMAIL_OK_DOMAINS
            ):
                continue
            problems.append(f"{path}: {name} in a committed blob")

    for message in run("git", "log", "--all", "--format=%B%x00").stdout.split("\0"):
        reason = reason_for(message)
        if reason:
            problems.append(f"commit message: {reason} in {message.strip()[:100]!r}")
    return problems


def check_ignore_rules() -> list[str]:
    problems: list[str] = []
    for path in IGNORED:
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

    problems: list[str] = []
    if args.history:
        problems += check_history()
    if args.worktree:
        problems += check_worktree()
    problems += check_ignore_rules()

    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} problem(s).", file=sys.stderr)
        return 1
    print("no personal data, credentials or private paths found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
