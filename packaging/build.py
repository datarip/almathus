#!/usr/bin/env python3
"""Freeze and package Almathus for one operating system.

    uv run python packaging/build.py --version 0.1.0 --out dist
    uv run python packaging/build.py --only sidecar --accept-model /path/to.onnx
    uv run python packaging/build.py --dry-run

Two programs are frozen, never one: the MIT game and the GPL-3.0 voice sidecar.
They sit side by side in the same folder, which is what keeps the licence story
true and what `voice.py` already expects (it looks for `piper-sidecar` beside
`sys.executable`).

The acceptance test is the point of this file. PyInstaller will happily produce
a sidecar that starts, answers `ping` and then synthesises nothing at all,
because the espeak-ng data inside the `piper` package did not make it into the
bundle. Silence is the failure mode, so silence is what is checked:
`--accept-model` runs a real synthesis and fails on all-zero PCM.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYINSTALLER_DIR = ROOT / "packaging" / "pyinstaller"
SIDECAR_NAME = "piper-sidecar.exe" if os.name == "nt" else "piper-sidecar"
#: Files piper needs to phonemize. Without them the bundle is silently mute.
ESPEAK_DATA_MIN = 300


def run(*args: str) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    # argv is fixed here, never built from user input.
    return subprocess.run(args, check=True, text=True)  # noqa: S603


def freeze(spec: str, out: Path) -> None:
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        str(PYINSTALLER_DIR / spec),
        "--noconfirm",
        "--clean",
        "--distpath",
        str(out),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
    )


def espeak_files(bundle: Path) -> int:
    """Count the phonemizer's data files anywhere under the bundle."""
    return sum(
        1
        for data in bundle.rglob("espeak-ng-data")
        if data.is_dir()
        for path in data.rglob("*")
        if path.is_file()
    )


def ask(proc: subprocess.Popen[bytes], payload: dict[str, object]) -> dict[str, Any]:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    proc.stdin.flush()
    header = b""
    while not header.endswith(b"\n"):
        chunk = proc.stdout.read(1)
        if not chunk:
            raise SystemExit("sidecar closed stdout before answering")
        header += chunk
    answer = json.loads(header)
    if "error" in answer:
        raise SystemExit(f"sidecar error: {answer['error']}")
    return answer


def accept_sidecar(bundle: Path, model: Path | None) -> None:
    """Prove the frozen sidecar can speak, or say exactly what it lacks."""
    exe = bundle / SIDECAR_NAME
    if not exe.exists():
        raise SystemExit(f"missing {exe}")

    data = espeak_files(bundle)
    if data < ESPEAK_DATA_MIN:
        raise SystemExit(
            f"piper/espeak-ng-data is incomplete: {data} files, expected >{ESPEAK_DATA_MIN}. "
            "The bundle would start and then say nothing."
        )

    if model is None:
        print(f"sidecar: {data} espeak-ng data files, no model given — ping only")
        # Fixed argv: the binary we just built.
        proc = subprocess.Popen([str(exe), "--help"], stdout=subprocess.DEVNULL)  # noqa: S603
        proc.wait(timeout=60)
        return

    proc = subprocess.Popen(  # noqa: S603  (fixed argv: the binary we just built)
        [str(exe), "--model", str(model)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        first = proc.stdout.readline()
        if b"ready" not in first:
            raise SystemExit(f"sidecar did not announce itself: {first[:200]}")
        ask(proc, {"cmd": "ping"})
        header = ask(proc, {"text": "sept fois quatre"})
        assert proc.stdout is not None
        pcm = proc.stdout.read(int(header["n"]))
        if not pcm or not any(pcm):
            raise SystemExit("synthesis produced no audio: espeak-ng data is missing")
        print(
            f"sidecar: {len(pcm)} bytes of non-silent PCM at {header['sample_rate']} Hz"
        )
        # quit exits without answering, so it is written, not asked.
        assert proc.stdin is not None
        proc.stdin.write(b'{"cmd": "quit"}\n')
        proc.stdin.flush()
    finally:
        proc.wait(timeout=60)


def accept_app(out: Path) -> None:
    """The MIT/GPL split has to survive freezing, not just the wheel build."""
    bundle = out / ("Almathus.app" if sys.platform == "darwin" else "Almathus")
    if not bundle.exists():
        raise SystemExit(f"missing {bundle}")
    leaks = [
        p.name
        for p in bundle.rglob("*")
        if "piper" in p.name.lower() or "espeak" in p.name.lower()
    ]
    if leaks:
        raise SystemExit(f"GPL engine leaked into the app bundle: {leaks[:5]}")
    print(f"app bundle at {bundle}, no piper/espeak inside")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="version to stamp into the build")
    parser.add_argument("--out", default="dist", help="where the artefacts go")
    parser.add_argument(
        "--only",
        choices=("app", "sidecar", "all"),
        default="all",
        help="which program to freeze",
    )
    parser.add_argument(
        "--accept-model", type=Path, help="a .onnx voice for the acceptance test"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would happen, do nothing"
    )
    args = parser.parse_args(argv)

    out = (ROOT / args.out).resolve()
    if args.version and not args.dry_run:
        stamp_version(args.version)

    if args.dry_run:
        print(f"would freeze into {out}: {args.only}")
        print(f"  spec dir: {PYINSTALLER_DIR}")
        print(f"  sidecar : {out / 'piper-sidecar' / SIDECAR_NAME}")
        return 0

    out.mkdir(parents=True, exist_ok=True)

    if args.only in ("sidecar", "all"):
        freeze("piper-sidecar.spec", out)
        accept_sidecar(out / "piper-sidecar", args.accept_model)

    if args.only in ("app", "all"):
        freeze("almathus.spec", out)
        accept_app(out)

    return 0


def stamp_version(version: str) -> None:
    """Write `version.py` from the tag. A build-time edit, never committed."""
    path = ROOT / "version.py"
    text = path.read_text(encoding="utf-8")
    lines = [
        f'__version__ = "{version}"' if line.startswith("__version__") else line
        for line in text.splitlines()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    shutil.rmtree(ROOT / "build" / "pyinstaller", ignore_errors=True)
    raise SystemExit(main(sys.argv[1:]))
