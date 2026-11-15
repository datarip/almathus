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
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
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


def app_dir(out: Path) -> Path:
    """Where the game's own executable lives, on each OS."""
    if sys.platform == "darwin":
        return out / "Almathus.app" / "Contents" / "MacOS"
    return out / "Almathus"


def stage(out: Path, version: str) -> Path:
    """Put the two programs together and add everything a bundle must carry.

    The sidecar keeps its own `_internal` folder, so it cannot share a
    directory with the app: it goes in a subfolder named after its executable,
    which is the second place `voice._sidecar_command` looks.
    """
    home = app_dir(out)
    if not home.is_dir():
        raise SystemExit(f"missing {home} — freeze the app first")
    # On macOS the helper goes in Contents/Resources: a nested Python tree inside
    # Contents/MacOS makes codesign treat its .dist-info folders as broken
    # bundles and refuse to sign the app at all.
    helper = (
        out / "Almathus.app" / "Contents" / "Resources" / "piper-sidecar"
        if sys.platform == "darwin"
        else home / "piper-sidecar"
    )
    shutil.copytree(out / "piper-sidecar", helper, dirs_exist_ok=True)
    write_licences(home)
    write_source_offer(home, version)
    return home


def write_licences(home: Path) -> None:
    """GPL text, plus every installed package's own licence, next to the app."""
    dest = home / "licenses"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "sidecar" / "LICENSE", dest / "GPL-3.0.txt")

    import importlib.metadata as md

    rows = []
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name:
            continue
        licence = (
            dist.metadata.get("License-Expression")
            or (dist.metadata.get("License") or "").strip().splitlines()[0:1]
            or ["see file"]
        )
        if isinstance(licence, list):
            licence = licence[0] if licence else "see file"
        rows.append((name, dist.version or "?", licence))
        # `_path` is the dist-info folder, private but the only way to find it.
        dist_info = getattr(dist, "_path", None)
        source = dist_info / "licenses" if dist_info else None
        if source and source.is_dir():
            shutil.copytree(source, dest / name, dirs_exist_ok=True)

    rows.sort(key=lambda row: row[0].lower())
    table = "\n".join(f"| {n} | {v} | {lic} |" for n, v, lic in rows)
    (dest / "THIRD-PARTY.md").write_text(
        "# Third-party components\n\n"
        "Every package that was present in the build environment. Voice models are\n"
        "not shipped: they are downloaded at first run and keep their own licences.\n\n"
        "| Package | Version | Licence |\n|---|---|---|\n"
        f"{table}\n",
        encoding="utf-8",
    )
    print(f"licences: {len(rows)} packages + the GPL text")


def source_offer(version: str) -> str:
    return (
        "Almathus voice engine — GPL-3.0 source offer\n"
        "===========================================\n\n"
        "This product contains piper-sidecar, a separate program licensed under the\n"
        "GNU General Public License, version 3 or later. It links piper-tts and\n"
        "espeak-ng, which is why it cannot be MIT like the rest of the game.\n\n"
        f"The exact corresponding source for version {version} is available at:\n\n"
        f"  https://github.com/datarip/almathus/releases/tag/v{version}\n\n"
        "as almathus-voice-" + version + ".tar.gz, and on request for three years.\n"
        "It contains the sidecar source, the pinned engine sources and the build\n"
        "script used to produce this binary.\n\n"
        "The game itself is MIT; see licenses/ and the LICENSE file in the\n"
        "repository.\n"
    )


def write_source_offer(home: Path, version: str) -> None:
    (home / "install.txt").write_text(source_offer(version), encoding="utf-8")


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


def codesign(path: Path) -> None:
    """Ad-hoc signature: unsigned is "damaged", ad-hoc is "unidentified developer"."""
    if sys.platform != "darwin":
        return
    run("codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", str(path))


APPIMAGE_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)
ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)


def package(out: Path, version: str) -> list[Path]:
    arch = platform.machine()
    os_name = (
        "macos"
        if sys.platform == "darwin"
        else "windows"
        if os.name == "nt"
        else "linux"
    )
    stem = f"Almathus-{version}-{os_name}-{arch}"
    made: list[Path] = []

    if sys.platform == "darwin":
        made += package_macos(out, stem)
    elif os.name == "nt":
        made += package_windows(out, stem, version, arch)
    else:
        made += package_linux(out, stem)

    sums = out / "SHA256SUMS"
    sums.write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in made
        ),
        encoding="utf-8",
    )
    for path in made:
        print(f"artefact: {path.name} ({path.stat().st_size // 1_000_000} MB)")
    return made


def package_macos(out: Path, stem: str) -> list[Path]:
    app = out / "Almathus.app"
    # The helper is signed first: signing the app afterwards seals its contents.
    codesign(app / "Contents" / "Resources" / "piper-sidecar" / SIDECAR_NAME)
    codesign(app)
    run("codesign", "--verify", "--deep", "--strict", str(app))
    archive = out / f"{stem}.zip"
    run("ditto", "-c", "-k", "--keepParent", str(app), str(archive))
    dmg = out / f"{stem}.dmg"
    run(
        "hdiutil",
        "create",
        "-volname",
        "Almathus",
        "-srcfolder",
        str(app),
        "-ov",
        "-format",
        "UDZO",
        str(dmg),
    )
    return [archive, dmg]


def package_windows(out: Path, stem: str, version: str, arch: str) -> list[Path]:
    made: list[Path] = []
    zip_path = out / f"{stem}.zip"
    shutil.make_archive(
        str(zip_path.with_suffix("")), "zip", root_dir=out, base_dir="Almathus"
    )
    made.append(zip_path)

    iscc = shutil.which("iscc") or next(
        (p for p in ISCC_CANDIDATES if Path(p).exists()), None
    )
    if not iscc:
        print("Inno Setup not found: leaving the installer out (the zip still works)")
        return made
    run(
        str(iscc),
        f"/DVERSION={version}",
        f"/DARCH={arch}",
        f"/DOUTDIR={out}",
        # Inno resolves relative paths against the .iss file, not the working
        # directory, so everything it reads is passed absolutely.
        f"/DSRCDIR={out / 'Almathus'}",
        f"/DICONFILE={ROOT / 'assets' / 'icon.ico'}",
        str(ROOT / "packaging" / "windows" / "installer.iss"),
    )
    made += sorted(out.glob(f"{stem}.exe"))
    return made


def package_linux(out: Path, stem: str) -> list[Path]:
    made: list[Path] = []
    archive = out / f"{stem}.tar.gz"
    run("tar", "-czf", str(archive), "-C", str(out), "Almathus")
    made.append(archive)

    appimage = build_appimage(out, stem)
    if appimage:
        made.append(appimage)
    return made


def build_appimage(out: Path, stem: str) -> Path | None:
    """An AppDir, then appimagetool. Returns None when the tool cannot be had."""
    appdir = out / "Almathus.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(out / "Almathus", appdir / "usr" / "bin" / "Almathus")
    shutil.copyfile(ROOT / "packaging" / "linux" / "AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)
    shutil.copyfile(
        ROOT / "packaging" / "linux" / "almathus.desktop", appdir / "almathus.desktop"
    )
    shutil.copyfile(ROOT / "assets" / "icon.png", appdir / "almathus.png")

    tool = ROOT / "build" / "appimagetool"
    if not tool.exists():
        tool.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(APPIMAGE_URL, tool)
        tool.chmod(0o755)

    target = out / f"{stem}.AppImage"
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1", VERSION="")
    print(f"+ appimagetool {appdir} {target}")
    subprocess.run([str(tool), str(appdir), str(target)], check=True, env=env)  # noqa: S603
    return target


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

    if args.only == "all":
        stage(out, args.version or "0.0.0")
        package(out, args.version or "0.0.0")

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
