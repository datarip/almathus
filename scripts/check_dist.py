#!/usr/bin/env python3
"""Check the built artefacts still respect the MIT/GPL split.

Run after `uv build`:

    python3 scripts/check_dist.py

The rule being enforced is the one in NOTICE and the README's Licensing
section: the wheel and the sdist carry only the MIT part of the project.

  * no `sidecar/` in either — it is a separate GPL-3.0 program, and a package
    that contained it would be a GPL-3.0 distribution, not an MIT one;
  * no local runtime data (results, stats, models);
  * the runtime files and assets the game reads at run time are present, and
    so is the CC BY attribution for the sounds, which must travel with them.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

DIST = Path("dist")

#: Runtime files the wheel must carry.
WHEEL_REQUIRED = (
    "main.py",
    "voice.py",
    "assets/icon.svg",
    "assets/sounds/CREDITS.md",
    "assets/flags/README.md",
)

#: Substrings that must never appear in either artefact. Only names that the
#: public repository already mentions — the README documents results/, stats/
#: and models/ as the folders the game writes to.
FORBIDDEN = (
    "sidecar/piper_sidecar",
    "piper_sidecar.py",
    "results/",
    "stats/",
    "models/",
    ".onnx",
)


def main() -> int:
    problems: list[str] = []

    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    if not wheels or not sdists:
        return _fail([f"no artefacts in {DIST}/ — run `uv build` first"])

    for wheel in wheels:
        names = zipfile.ZipFile(wheel).namelist()
        for forbidden in FORBIDDEN:
            if any(forbidden in name for name in names):
                problems.append(
                    f"{wheel.name}: contains {forbidden!r} — the wheel must be MIT-only"
                )
        for required in WHEEL_REQUIRED:
            if required not in names:
                problems.append(f"{wheel.name}: missing {required}")

    for sdist in sdists:
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
        for forbidden in FORBIDDEN:
            if any(f"/{forbidden.lstrip('/')}" in name for name in names):
                problems.append(f"{sdist.name}: contains {forbidden!r}")
        if not any(name.endswith("/LICENSE") for name in names):
            problems.append(f"{sdist.name}: LICENSE missing")
        if not any(name.endswith("/NOTICE") for name in names):
            problems.append(f"{sdist.name}: NOTICE missing")

    if problems:
        return _fail(problems)
    print(f"{len(wheels)} wheel(s), {len(sdists)} sdist(s): MIT-only boundary intact")
    return 0


def _fail(problems: list[str]) -> int:
    print("\n".join(problems), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
