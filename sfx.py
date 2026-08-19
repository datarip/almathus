"""Sound effects for Almathus — the short samples that mark the moments of a game.

Same shape as `voice.TTS`, and for the same reason: Python ships no sound
player, so a sample is handed to a command-line one as a subprocess. `afplay`
comes with macOS; `ffplay` / `mpg123` / `mpv` / `cvlc` cover a Linux box that
has any of them. Find none — a bare Windows box, say — and every call here is a
no-op, so the game plays exactly as it did before there was any sound at all.

Nothing raises into the UI, and one sound plays at a time: a new one replaces
whatever is still running, which is what makes `stop()` the way to hush a tail
that would otherwise talk over the speech synthesizer.

The samples live in `assets/sounds/` and are named after the moment they mark,
not after the game they were cut from, so a parent can drop in their own mp3
under the same name (see assets/README.md).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

# Players in order of preference, each with the flags that make it play one file
# and get out of the way: no window, no console output, exit at the end.
PLAYERS = (
    ("afplay", []),  # macOS, built in
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("mpg123", ["-q"]),
    ("mpv", ["--no-video", "--really-quiet"]),
    ("cvlc", ["--play-and-exit", "--quiet"]),
)


def find_player() -> Optional[list[str]]:
    """The first player on PATH, as the command prefix to append a file to."""
    for exe, flags in PLAYERS:
        path = shutil.which(exe)
        if path:
            return [path, *flags]
    return None


class Sound:
    """The game's sound effects. `play("correct")` plays `<dir>/correct.mp3`."""

    EXT = ".mp3"

    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.player = find_player()
        self._proc: Optional[subprocess.Popen] = None
        # Bumped by every stop() and every new sound, so a waiter thread can tell
        # "the sample finished" from "something cut it short" — see _wait_then.
        self._gen = 0

    def available(self) -> bool:
        return bool(self.player and self.dir.is_dir())

    def play(self, name: str, then: Optional[Callable[[], None]] = None) -> None:
        """Play a sample, cutting any still running. `then` runs when it ends.

        `then` is called from a worker thread, so a Tk caller has to marshal it
        onto the main loop — and it is called straight away when there is no
        player or no sample, so whatever waits on the sound still happens.
        """
        self.stop()
        path = self.dir / (name + self.EXT)
        if not (self.player and path.exists()):
            if then:
                then()
            return
        try:
            self._proc = subprocess.Popen(
                self.player + [str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._proc = None
            if then:
                then()
            return
        if then:
            threading.Thread(
                target=self._wait_then, args=(self._proc, self._gen, then), daemon=True
            ).start()

    def _wait_then(self, proc, gen, then):
        try:
            proc.wait()
        except Exception:
            pass
        # A sound that was cut short never earned its callback: terminate() makes
        # wait() return just the same, and the recap's spoken score must not fire
        # at the settings screen because a child clicked "Rejouer" mid-fanfare.
        if gen == self._gen:
            then()

    def stop(self) -> None:
        self._gen += 1
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
