# -*- mode: python ; coding: utf-8 -*-
"""Freeze the GPL voice sidecar as a one-directory bundle.

    uv run pyinstaller packaging/pyinstaller/piper-sidecar.spec \
        --noconfirm --clean --distpath dist --workpath build/pyinstaller

One directory, never one file: a one-file build unpacks ~150 MB into a temp
directory on every launch, which costs a second per start and re-validates the
macOS signature each time.

The one thing that goes wrong here is silent: `piper` ships its espeak-ng data
*inside the package* (`piper/espeak-ng-data`, 123 files). Miss it and the bundle
builds, starts, answers `{"pong": true}` and then produces no audio at all,
because the phonemizer has no language data. `collect_data_files("piper")` is
what carries it; packaging/build.py's acceptance test is what proves it.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH is the directory holding this file; the repository root is two up.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPECPATH)))

datas = collect_data_files("piper")  # espeak-ng-data, templates, ...
# piper.train is the model-conversion toolkit: it needs torch and never runs in
# the sidecar.
hiddenimports = collect_submodules("piper", filter=lambda name: not name.startswith("piper.train"))

a = Analysis(
    [os.path.join(ROOT, "sidecar", "piper_sidecar.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The sidecar is one program: no Tk, no recognizer, no microphone.
    excludes=["tkinter", "vosk", "sounddevice", "webrtcvad", "scipy", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="piper-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # stdout is the audio channel; never hide it
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="piper-sidecar",
)
