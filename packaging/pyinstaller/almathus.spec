# -*- mode: python ; coding: utf-8 -*-
"""Freeze the game as a one-directory bundle.

    uv run python packaging/build.py --only app

`piper` is in `excludes` on purpose and that line is load-bearing. The quality
job installs the `sidecar` extra for mypy, so Piper is importable in the build
environment; if PyInstaller picked it up, the MIT app would contain the GPL-3.0
engine and the licence split documented in NOTICE would stop being true.
packaging/build.py asserts the result.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPECPATH)))
VERSION = "0.0.0"
with open(os.path.join(ROOT, "version.py"), encoding="utf-8") as fh:
    for line in fh:
        if line.startswith("__version__"):
            VERSION = line.split("=", 1)[1].strip().strip('"').strip("'")

# vosk opens libvosk.dyld/.so/.dll with dlopen(), so nothing in the import graph
# points at it and PyInstaller leaves it behind. Without it `import vosk` raises
# OSError inside the frozen app and the microphone silently stops existing.
#
# It takes two collectors: collect_data_files picks up the .dyld (macOS treats it
# as data), but skips .so/.dll because it recognises them as libraries, so those
# are declared as binaries and placed back inside the vosk package folder, which
# is where vosk looks for them at run time.
import importlib.util as _ilu

from PyInstaller.utils.hooks import collect_data_files

_vosk_dir = os.path.dirname(_ilu.find_spec("vosk").origin)
_vosk_binaries = [
    (os.path.join(_vosk_dir, name), "vosk")
    for name in os.listdir(_vosk_dir)
    if name.startswith(("libvosk", "libgcc_s", "libstdc++", "libwinpthread"))
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=_vosk_binaries,
    datas=[(os.path.join(ROOT, "assets"), "assets")] + collect_data_files("vosk"),
    # Optional stacks imported inside functions, so nothing static finds them.
    hiddenimports=[
        "vosk",
        "sounddevice",
        "webrtcvad",
        "num2words",
        "pyttsx3",
        "scipy.signal",
    ],
    hookspath=[os.path.join(SPECPATH, "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    # piper/espeak-ng are GPL-3.0 and travel only in the sidecar.
    excludes=["piper", "torch", "matplotlib", "pytest", "IPython", "tkinter.test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Almathus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # a windowed game, not a terminal program
    icon=os.path.join(ROOT, "assets", "icon.ico") if sys.platform == "win32" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Almathus",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Almathus.app",
        icon=os.path.join(ROOT, "assets", "icon.icns"),
        bundle_identifier="app.almathus",
        info_plist={
            # Without this macOS either kills the process or silently denies the
            # microphone, and the whole voice path fails with no message.
            "NSMicrophoneUsageDescription": (
                "Almathus listens so your child can answer out loud instead of typing."
            ),
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
        },
    )
