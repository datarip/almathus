"""Keep the display and the machine awake for the length of a game session.

A laptop on a battery-optimised power profile dims the display after a few
minutes without input. It suspends later. The game has no input between cards
beyond the child thinking, so the card in front of them goes dark mid-question.
`hold()` asks the OS to keep display sleep and system sleep off. `release()`
gives the machine back. `main.py` calls the two at the start and the end of a
game.

Each platform gets its own native call. macOS uses `caffeinate`. Windows uses
the `SetThreadExecutionState` bits. Linux uses a logind inhibitor and a D-Bus
screensaver cookie. Every one of them is best effort. A missing helper, a
desktop with no session bus or a refused request leaves the game exactly as it
was. A power hint is never worth interrupting a game for.

Both calls are idempotent. The helper processes are tied to this one. The macOS
helper watches the pid, and the Linux inhibitor's child reads a pipe that only
this process holds open. Neither outlives a crash.

Two limits belong to the OS, not to this module. A closed laptop lid still
sleeps the machine, and the display keeps its own dimming behaviour.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

_WHY = "Almathus is running a game"

_MACOS_CAFFEINATE = "/usr/bin/caffeinate"

# kernel32's execution-state bits. CONTINUOUS makes the state stick until the
# next call instead of resetting when a thread or thread input goes idle.
_WIN_CONTINUOUS = 0x80000000
_WIN_SYSTEM_REQUIRED = 0x0000_0001
_WIN_DISPLAY_REQUIRED = 0x0000_0002

# Session-inhibitor interfaces, most specific first. GNOME takes an idle flag
# and a suspend flag. The freedesktop one covers the screensaver alone, which is
# what KDE, XFCE and Cinnamon implement. The last entry on each row is the call
# that drops the cookie again.
_LINUX_INHIBITS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    (
        "org.gnome.SessionManager",
        "/org/gnome/SessionManager",
        "org.gnome.SessionManager.Inhibit",
        ("Almathus", "0", _WHY, "12"),  # 4 suspend | 8 session idle
        "org.gnome.SessionManager.Uninhibit",
    ),
    (
        "org.freedesktop.ScreenSaver",
        "/org/freedesktop/ScreenSaver",
        "org.freedesktop.ScreenSaver.Inhibit",
        ("Almathus", _WHY),
        "org.freedesktop.ScreenSaver.UnInhibit",
    ),
)


@dataclass
class _State:
    """What is currently held, so `release()` undoes exactly that."""

    active: bool = False
    macos_child: subprocess.Popen[bytes] | None = None
    linux_child: subprocess.Popen[bytes] | None = None
    linux_cookie: tuple[str, str, int] | None = None
    linux_xset: bool = False


_state = _State()


def hold() -> None:
    """Keep display and system sleep off until `release()`.

    Called when a game is dealt. A second call does nothing.
    """
    if _state.active:
        return
    _state.active = True
    try:
        if sys.platform == "darwin":
            _hold_macos()
        elif sys.platform == "win32":
            _hold_windows()
        else:
            _hold_linux()
    except OSError, subprocess.SubprocessError, ValueError, AttributeError:
        # Best effort by design: the game plays on a machine that says no.
        return


def release() -> None:
    """Give display and system sleep back. Safe without a `hold()`."""
    if not _state.active:
        return
    _state.active = False
    try:
        if sys.platform == "darwin":
            _release_macos()
        elif sys.platform == "win32":
            _release_windows()
        else:
            _release_linux()
    except OSError, subprocess.SubprocessError, ValueError, AttributeError:
        return


# --------------------------------------------------------------------------- #
# macOS: caffeinate holds the assertion, -w drops it when this process dies
# --------------------------------------------------------------------------- #
def _hold_macos() -> None:
    if not os.access(_MACOS_CAFFEINATE, os.X_OK):
        return
    # -d keeps the display on. -i keeps the machine from idle sleeping. -w ties
    # the assertions to the game process, so a crash cannot pin the Mac awake.
    # -s is left out because it only holds on AC power.
    _state.macos_child = subprocess.Popen(  # noqa: S603 - fixed system path
        [_MACOS_CAFFEINATE, "-d", "-i", "-w", str(os.getpid())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _release_macos() -> None:
    child, _state.macos_child = _state.macos_child, None
    if child is None:
        return
    child.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.wait(timeout=3)


# --------------------------------------------------------------------------- #
# Windows: the execution state belongs to the calling thread, which is Tk's
# --------------------------------------------------------------------------- #
def _hold_windows() -> None:
    if sys.platform != "win32":
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(
        _WIN_CONTINUOUS | _WIN_SYSTEM_REQUIRED | _WIN_DISPLAY_REQUIRED
    )


def _release_windows() -> None:
    if sys.platform != "win32":
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(_WIN_CONTINUOUS)


# --------------------------------------------------------------------------- #
# Linux: logind owns suspend, the desktop owns the screensaver
# --------------------------------------------------------------------------- #
def _hold_linux() -> None:
    _hold_logind()
    _state.linux_cookie = _inhibit_screensaver()
    if _state.linux_cookie is None:
        _hold_x11()


def _hold_logind() -> None:
    inhibitor = shutil.which("systemd-inhibit")
    # `cat` on a pipe this process holds open is the child. When the game dies
    # the pipe closes, cat sees EOF, and the inhibitor is released with it.
    reader = shutil.which("cat")
    if inhibitor is None or reader is None:
        return
    _state.linux_child = subprocess.Popen(  # noqa: S603 - resolved absolute path
        [
            inhibitor,
            "--what=idle:sleep",
            "--mode=block",
            f"--why={_WHY}",
            reader,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _inhibit_screensaver() -> tuple[str, str, int] | None:
    gdbus = shutil.which("gdbus")
    if gdbus is None:
        return None
    for dest, path, method, args, _uninhibit in _LINUX_INHIBITS:
        cookie = _uint32(_gdbus(gdbus, dest, path, method, args))
        if cookie is not None:
            return (dest, path, cookie)
    return None


def _gdbus(
    gdbus: str, dest: str, path: str, method: str, args: tuple[str, ...]
) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - resolved absolute path
            [
                gdbus,
                "call",
                "--session",
                "--dest",
                dest,
                "--object-path",
                path,
                "--method",
                method,
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout if result.returncode == 0 else None


def _uint32(output: str | None) -> int | None:
    """Pull the cookie out of gdbus's `(uint32 42,)` reply."""
    if not output:
        return None
    match = re.search(r"\d+", output)
    return int(match.group()) if match else None


def _hold_x11() -> None:
    """Last resort for a desktop with no D-Bus inhibitor: stop blanking.

    Only X11 has `xset`, and on Wayland the call fails and changes nothing.
    """
    if os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("DISPLAY"):
        return
    xset = shutil.which("xset")
    if xset is None:
        return
    try:
        result = subprocess.run(  # noqa: S603 - resolved absolute path
            [xset, "s", "off", "-dpms"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return
    _state.linux_xset = result.returncode == 0


def _release_linux() -> None:
    child, _state.linux_child = _state.linux_child, None
    if child is not None:
        child.terminate()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            if child.stdin is not None:
                child.stdin.close()
            child.wait(timeout=3)

    cookie, _state.linux_cookie = _state.linux_cookie, None
    if cookie is not None:
        dest, path, value = cookie
        gdbus = shutil.which("gdbus")
        if gdbus is not None:
            for interface, _path, _method, _args, uninhibit in _LINUX_INHIBITS:
                if interface == dest:
                    _gdbus(gdbus, dest, path, uninhibit, (str(value),))

    if _state.linux_xset:
        _state.linux_xset = False
        # `xset s on +dpms` re-enables the screen saver and DPMS with their
        # built-in defaults. The exact timeouts set before are not restored.
        xset = shutil.which("xset")
        if xset is not None:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(  # noqa: S603 - resolved absolute path
                    [xset, "s", "on", "+dpms"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
