"""Almathus — a kid-friendly mental-math game (addition, subtraction, ...).

Pure-stdlib Tkinter app. Runs anywhere Python has Tk:

    uv run almathus          # or:  python main.py

Flow:  Settings -> 5-4-3-2-1-GO! countdown -> cards -> reinforcement round -> recap.
Per-card time is measured so a parent can see which operations need work.

Four moments of that flow have a sound (see sfx.py and assets/README.md): the
countdown, a right answer, a wrong one, and the winner's music over the recap.
They need a command-line player on PATH and are silently skipped without one.

Every session is logged twice: the full per-card detail to ./stats/*.json, and
one summary line to ./results/results.csv — the hand-editable log the progress
screen charts and tabulates (see results.py for the header definition).
"""

from __future__ import annotations

import base64
import contextlib
import json
import math
import os
import queue
import random
import signal
import struct
import threading
import time
import tkinter as tk
import traceback
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import font as tkfont
from typing import Any, Literal

import languages
import pdfreport
import results as resultslog
import sfx

try:
    import voice as voicemod
except Exception:  # noqa: BLE001 - keep the game usable if voice.py is absent/broken
    # The module is optional; every use site already guards on it being None.
    voicemod = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DEFAULT_CARDS = 20

MODES = ("addition", "subtraction", "multiplication", "division")

# Operand ranges per mode. Addition/subtraction stay in 0..20 as requested;
# multiplication/division use smaller factors so results stay kid-sized.
RANGES = {
    "addition": {"max": 20},
    "subtraction": {"max": 20},
    "multiplication": {"max_factor": 10},
    "division": {"max_factor": 10},
}

# The second operand is picked from the numbers switched on in the settings
# screen, so the kid can drill one at a time (only [7] => 3×7, 9×7, 4×7...).
# 0 is excluded: it would make division undefined and +0 / ×0 pointless.
NUM_MIN, NUM_MAX = 1, 10

# The language the app runs in. Only fr/en have translated strings today; the
# others fall back to English in T() while their voice/number support is real.
DEFAULT_LANG = "fr"  # used until a language is chosen and its model installed
LANG = DEFAULT_LANG  # resolved at startup by resolve_language()

STRINGS = {
    "fr": {
        "title": "Almathus — les maths en jouant",
        "how_many": "Combien de cartes ?",
        "choose_numbers": "Avec quels nombres ?",
        "numbers_hint": "Touche un nombre pour l'allumer ou l'éteindre",
        "choose_mode": "Choisis ton opération",
        "addition": "Addition",
        "subtraction": "Soustraction",
        "multiplication": "Multiplication",
        "division": "Division",
        "go": "GO !",
        "timer_total": "Temps total",
        "timer_card": "Cette carte",
        "card_of": "Carte {n} / {total}",
        "retry_round": "🔁 On rejoue ce qu'on a raté !",
        "score": "Score",
        "total_time": "Temps total",
        "avg_time": "Temps moyen / carte",
        "slowest": "Les plus longues :",
        "again": "Rejouer",
        "quit": "Quitter",
        "correct": "Bravo ! 🎉",
        "wrong": "Presque ! La réponse était {a}",
        "voice_toggle": "🎤 Répondre à la voix",
        "dictation_toggle": "🔊 Lire la carte",
        "girly_toggle": "👑 Mode princesse",
        "language_toggle": "🌐 Langue",
        "language_title": "Langue de l'application",
        "language_intro": "Choisissez la langue de l'application et téléchargez son modèle vocal. Il sera conservé pour les prochaines fois.",
        "language_skip": "Continuer sans voix",
        "voice_off_hint": "🎤 Voix indisponible : {reason}",
        "listening": "🎧 à l'écoute…",
        "thinking": "🤔 …",
        "wait_answer": "Attends la fin de la question, puis réponds…",
        "articulate_tip_title": "🗣️ Avant de commencer",
        "articulate_tip": "Parle fort et articule bien, comme si tu parlais à quelqu'un juste à côté de toi.",
        "articulate_tip_go": "Réponds à la fin de la question, quand tu vois « GO ! ».",
        "didnt_get": "Je n'ai pas compris, réessaie 🎤",
        "too_quiet": "🔊 Plus fort ! Je t'entends à peine",
        "mic_denied": "Micro non autorisé — Réglages ▸ Confidentialité ▸ Microphone",
        "score_spoken": "Tu as eu {c} sur {n}.",
        "progress": "📈 Mes progrès",
        "back": "← Retour",
        "progress_title": "📈 Mes progrès",
        "chart_title": "Temps moyen par jour",
        "axis_grade": "Notes",
        "legend_time": "Temps moyen",
        "legend_grade": "Note moyenne",
        "avg_label": "moyenne",
        "no_results": "Pas encore de résultats — joue une partie !",
        "col_date": "Date",
        "col_family": "Famille",
        "col_time": "Temps",
        "col_cards": "Cartes",
        "col_note": "Note",
        "col_avg": "Temps moyen / carte",
        "col_difficult": "Difficultés",
        "n_sessions": "{n} sessions",
        "month_all": "Tous les mois",
        "month_tip": "Filtrer par mois",
        "no_month_results": "Rien pour ce mois — choisis-en un autre",
        "pdf_button": "📄 PDF",
        "pdf_tip": "Exporter en PDF",
        "pdf_dialog": "Enregistrer mes progrès en PDF",
        "pdf_exported_on": "Exporté le {when}",
        "pdf_footer": "Almathus · page {page} / {total}",
        "pdf_saved": "✓ PDF enregistré",
        "pdf_failed": "Échec de l'export PDF",
    },
    "en": {
        "title": "Almathus — math through play",
        "how_many": "How many cards?",
        "choose_numbers": "With which numbers?",
        "numbers_hint": "Tap a number to switch it on or off",
        "choose_mode": "Choose your operation",
        "addition": "Addition",
        "subtraction": "Subtraction",
        "multiplication": "Multiplication",
        "division": "Division",
        "go": "GO!",
        "timer_total": "Total time",
        "timer_card": "This card",
        "card_of": "Card {n} / {total}",
        "retry_round": "🔁 Let's redo the ones we missed!",
        "score": "Score",
        "total_time": "Total time",
        "avg_time": "Avg time / card",
        "slowest": "Slowest ones:",
        "again": "Play again",
        "quit": "Quit",
        "correct": "Well done! 🎉",
        "wrong": "Almost! The answer was {a}",
        "voice_toggle": "🎤 Answer by voice",
        "dictation_toggle": "🔊 Read the card",
        "girly_toggle": "👑 Girly mode",
        "language_toggle": "🌐 Language",
        "language_title": "App language",
        "language_intro": "Choose the app language and download its speech model. It is kept for next time.",
        "language_skip": "Continue without voice",
        "voice_off_hint": "🎤 Voice unavailable: {reason}",
        "listening": "🎧 listening…",
        "thinking": "🤔 …",
        "wait_answer": "Wait for the question to end, then answer…",
        "articulate_tip_title": "🗣️ Before you start",
        "articulate_tip": "Speak up and say each number clearly, as if talking to someone right beside you.",
        "articulate_tip_go": "Answer at the end of the question, when you see \u201cGO!\u201d.",
        "didnt_get": "I didn't catch that, try again 🎤",
        "too_quiet": "🔊 Louder! I can barely hear you",
        "mic_denied": "Mic not allowed — Settings ▸ Privacy ▸ Microphone",
        "score_spoken": "You got {c} out of {n}.",
        "progress": "📈 My progress",
        "back": "← Back",
        "progress_title": "📈 My progress",
        "chart_title": "Average time per day",
        "axis_grade": "Grades",
        "legend_time": "Average time",
        "legend_grade": "Average grade",
        "avg_label": "average",
        "no_results": "No results yet — play a game!",
        "col_date": "Date",
        "col_family": "Family",
        "col_time": "Time",
        "col_cards": "Cards",
        "col_note": "Grade",
        "col_avg": "Avg time / card",
        "col_difficult": "Difficulty",
        "n_sessions": "{n} sessions",
        "month_all": "All months",
        "month_tip": "Filter by month",
        "no_month_results": "Nothing for this month — pick another one",
        "pdf_button": "📄 PDF",
        "pdf_tip": "Export to PDF",
        "pdf_dialog": "Save my progress as PDF",
        "pdf_exported_on": "Exported on {when}",
        "pdf_footer": "Almathus · page {page} / {total}",
        "pdf_saved": "✓ PDF saved",
        "pdf_failed": "PDF export failed",
    },
}

OP_SPOKEN = {
    "fr": {
        "addition": "plus",
        "subtraction": "moins",
        "multiplication": "fois",
        "division": "divisé par",
    },
    "en": {
        "addition": "plus",
        "subtraction": "minus",
        "multiplication": "times",
        "division": "divided by",
    },
}

# Month names written out, rather than `strftime("%B")`: that one follows the
# operating system's locale, which has nothing to do with the language the app
# was started in — a French app on an English Mac would say "July".
MONTHS = {
    "fr": (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


def T(key: str, **kw) -> str:
    # A language with no translated strings yet falls back to English rather
    # than to raw keys: a speakable setup and settings screen beats a cryptic one.
    s = STRINGS.get(LANG, STRINGS["en"]).get(key, key)
    return s.format(**kw) if kw else s


WHEEL_LINE = 22  # px one line of scrolling is worth — about a table row
WHEEL_MAX = 5 * WHEEL_LINE  # ...and the most a single event may scroll


def bind_wheel(widget, scroll) -> None:
    """Call `scroll(pixels)` for every scroll gesture delivered to `widget`.

    Three different events mean "scroll", and a scrollable widget has to answer
    all of them:

    * `<MouseWheel>` — a mouse wheel, and on Tk 8 a trackpad as well.
    * `<TouchpadScroll>` — Tk 9's pixel-precise trackpad event. On macOS it is
      sent *instead* of `<MouseWheel>` for a two-finger swipe, so without this
      binding a trackpad scrolls nothing at all.
    * Buttons 4 and 5 — how X11 reports a wheel, and bound there only: on Aqua
      and Windows those numbers belong to the extra physical buttons of a mouse
      (Tk answers `<Button-5>` with button 9 here), which have nothing to do
      with scrolling.

    The handler is given pixels, not lines, because a trackpad speaks pixels;
    the canvases scrolled this way set `yscrollincrement=1` so that a "unit" of
    `yview_scroll` is one pixel.
    """
    widget.bind("<MouseWheel>", lambda e: scroll(wheel_pixels(e)))
    with contextlib.suppress(tk.TclError):  # Tk 8 has no such event: wheel only
        widget.bind("<TouchpadScroll>", lambda e: scroll(touchpad_pixels(e)))
    if widget.tk.call("tk", "windowingsystem") == "x11":
        widget.bind("<Button-4>", lambda e: scroll(wheel_pixels(e)))
        widget.bind("<Button-5>", lambda e: scroll(wheel_pixels(e)))


def wheel_pixels(ev) -> int:
    """A wheel event as a signed number of pixels to scroll by.

    Tk 9 reports a notch as 120 on all three platforms; Tk 8 on Aqua sent a
    small raw delta instead, which is read as lines as it comes so that an older
    build keeps working. X11 sends no delta at all — it reports the wheel as
    buttons 4 (up) and 5 (down), which is why a button number shows up here.
    Three lines per notch or button.
    """
    if getattr(ev, "num", 0) in (4, 5):
        lines = -3.0 if ev.num == 4 else 3.0
    else:
        delta = getattr(ev, "delta", 0) or 0
        if not delta:
            return 0
        # A positive delta is a scroll *up*, which walks the view towards the
        # top of the scrollregion — the negative direction.
        lines = -(delta / 120 * 3 if abs(delta) >= 120 else delta)
    return capped_scroll(lines * WHEEL_LINE)


def touchpad_pixels(ev) -> int:
    """The vertical half of a `<TouchpadScroll>` delta, as pixels to scroll by.

    One integer carries both axes: dx in the high 16 bits, dy in the low 16 as a
    signed value (this is what Tk's own `tk::PreciseScrollDeltas` unpacks). dx is
    dropped — nothing on these screens scrolls sideways. The sign is flipped for
    the same reason as a wheel delta's: the gesture and the view move opposite ways.
    """
    low = ev.delta & 0xFFFF
    dy = low if low < 0x8000 else low - 0x10000
    return capped_scroll(-dy)


def capped_scroll(pixels: float) -> int:
    """Whole pixels, never rounding a nudge down to nothing, never a wild jump.

    macOS sends a *lot* of events during one two-finger swipe, each a few pixels,
    so a slow drag must keep its smallest steps; and how large a single delta can
    get depends on the Tk build and on the gesture, so one outsized event must
    not throw the table from the top to the bottom.
    """
    if not pixels:
        return 0
    step = min(WHEEL_MAX, max(1, round(abs(pixels))))
    return step if pixels > 0 else -step


def month_label(ym: tuple[int, int] | None) -> str:
    """A `(year, month)` as a person says it: `Juillet 2026` / `July 2026`.

    Capitalized even in French, where a month is normally lower case: this is a
    label on a chip and a heading on a report, never a word inside a sentence.
    `None` is the "every month" entry of the filter.
    """
    if ym is None:
        return T("month_all")
    name = MONTHS.get(LANG, MONTHS["en"])[ym[1] - 1]
    return f"{name[:1].upper()}{name[1:]} {ym[0]}"


# Colors
#
# Two palettes with the same keys: the dark default, and the pink "girly" one
# a kid can flip to from the settings footer. Every screen reads these names as
# module globals *while it builds its widgets*, so switching themes is
# apply_theme() followed by a redraw — nothing repaints itself in place.
THEMES: dict[str, dict[str, Any]] = {
    "default": {
        "BG": "#0f1226",
        "CARD_BG": "#1b1f3b",
        "ACCENT": "#ffd23f",
        # The progress chart's second line — the grades, against the times drawn
        # in ACCENT. Blue against yellow is the widest hue gap the palette has,
        # and the one pair the common colour blindnesses still tell apart.
        "CHART_GRADE": "#6cb8f5",
        "GOOD": "#3ddc97",
        "BAD": "#ff6b6b",
        "TEXT": "#f5f6ff",
        "MUTED": "#9aa0c8",
        "INK": "#10121f",  # text laid on top of a bright tile
        "SURFACE": "#191d3d",  # raised panel, one step above the page background
        "BORDER": "#2b3160",  # hairline outline for panels and chips
        "ENTRY_BG": "#12152e",  # the answer box, one step *below* the page
        # Soft, cheerful and deliberately gender-neutral: mint, sky, apricot,
        # lilac. All four are light enough to carry the same near-black text.
        "MODE_COLORS": {
            "addition": "#4fd1a5",
            "subtraction": "#6cb8f5",
            "multiplication": "#ff9e7d",
            "division": "#b39ddb",
        },
        "NUM_ON_BG": "#4fd1a5",
        "NUM_ON_FG": "#10121f",
        "NUM_OFF_BG": "#333a63",
        "NUM_OFF_FG": "#8b93c4",
        "NUM_HOVER_BG": "#4b5490",
        # Hovering an operation tile lifts it away from the page: bright here,
        # deep on the light palette. Either way the label stays legible.
        "TILE_HOVER_BG": "#f5f6ff",
        "TILE_HOVER_FG": "#10121f",
        "TITLE_SVG": "mario.svg",
        "TITLE_ICON": "🧮",  # fallback if the drawing cannot be loaded
    },
    # Peach-pink page, plum ink. The whole palette flips light here, so the
    # greens and reds are darkened too: they carry text on a pale background
    # now, where the dark theme's pastels would wash out.
    "girly": {
        "BG": "#ffe6ef",
        "CARD_BG": "#ffc8dd",
        "ACCENT": "#e6338c",
        # Deep enough to read on the pink page, and far enough from the hot pink
        # of the time curve that no one has to guess which line is which.
        "CHART_GRADE": "#1b7fa3",
        "GOOD": "#12996f",
        "BAD": "#e0304f",
        "TEXT": "#43152c",
        "MUTED": "#8f5872",
        "INK": "#3d0f26",
        "SURFACE": "#fff4f8",
        "BORDER": "#f3aecb",
        "ENTRY_BG": "#fff4f8",
        "MODE_COLORS": {
            "addition": "#ff9ec7",
            "subtraction": "#cbaaf2",
            "multiplication": "#ffb08a",
            "division": "#ffe08a",
        },
        "NUM_ON_BG": "#ff7ab8",
        "NUM_ON_FG": "#3d0f26",
        "NUM_OFF_BG": "#ffd9e6",
        "NUM_OFF_FG": "#b07f96",
        "NUM_HOVER_BG": "#ffb3d3",
        # A deeper cut of ACCENT, so the tile reads as the same hot pink the rest
        # of this palette uses for "chosen", with white text over it.
        "TILE_HOVER_BG": "#d81b7a",
        "TILE_HOVER_FG": "#fff4f8",
        "TITLE_SVG": "peach.svg",
        "TITLE_ICON": "👑",
    },
}


# The palette names every screen below reads. They are spelled out here, rather
# than left for globals().update() to conjure, so a reader — and a linter, and a
# type checker — can see where they come from. apply_theme() rebinds them; these
# initial values are the default theme's, so the names are live from import on.
_DEFAULT = THEMES["default"]

BG: str = _DEFAULT["BG"]
CARD_BG: str = _DEFAULT["CARD_BG"]
ACCENT: str = _DEFAULT["ACCENT"]
CHART_GRADE: str = _DEFAULT["CHART_GRADE"]
GOOD: str = _DEFAULT["GOOD"]
BAD: str = _DEFAULT["BAD"]
TEXT: str = _DEFAULT["TEXT"]
MUTED: str = _DEFAULT["MUTED"]
INK: str = _DEFAULT["INK"]
SURFACE: str = _DEFAULT["SURFACE"]
BORDER: str = _DEFAULT["BORDER"]
ENTRY_BG: str = _DEFAULT["ENTRY_BG"]
MODE_COLORS: dict[str, str] = _DEFAULT["MODE_COLORS"]
NUM_ON_BG: str = _DEFAULT["NUM_ON_BG"]
NUM_ON_FG: str = _DEFAULT["NUM_ON_FG"]
NUM_OFF_BG: str = _DEFAULT["NUM_OFF_BG"]
NUM_OFF_FG: str = _DEFAULT["NUM_OFF_FG"]
NUM_HOVER_BG: str = _DEFAULT["NUM_HOVER_BG"]
TILE_HOVER_BG: str = _DEFAULT["TILE_HOVER_BG"]
TILE_HOVER_FG: str = _DEFAULT["TILE_HOVER_FG"]
TITLE_SVG: str = _DEFAULT["TITLE_SVG"]
TITLE_ICON: str = _DEFAULT["TITLE_ICON"]

THEME: str = "default"


Anchor = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]


def apply_theme(name: str) -> None:
    """Rebind the palette globals to one of THEMES. Widgets that already exist
    keep the colors they were built with, so callers redraw the screen after."""
    global THEME  # noqa: PLW0603 — the palette *is* module state; see above.
    THEME = name if name in THEMES else "default"
    globals().update(THEMES[THEME])


apply_theme(THEME)

OP_SYMBOL = {
    "addition": "+",
    "subtraction": "−",
    "multiplication": "×",
    "division": "÷",
}

STATS_DIR = Path(__file__).resolve().parent / "stats"
PREFS_FILE = Path(__file__).resolve().parent / "prefs.json"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SOUNDS_DIR = ASSETS_DIR / "sounds"

COUNTDOWN_STEPS = 3  # 3-2-1, then GO!
# countdown.mp3 is three beeps and a launch tone, laid out on an exact one-second
# grid: beeps at 485, 1485 and 2485 ms, the tone at 3485 ms, ringing to 4460. The
# numbers on screen are hung off that grid rather than the other way round, so a
# beep and a number land on the same instant instead of merely near one another.
COUNTDOWN_TICK = 1000  # ms one number stays on screen = the gap between beeps
COUNTDOWN_GO_AT = 3485  # ms into the sample where the launch tone starts
COUNTDOWN_GO_LEN = 975  # ms it rings for, after which the sample is silent
GO_FLASH_MS = 500  # how long GO! holds the question pill before the question returns

# How many times a card carried over from the last session is dealt again. Twice
# is what makes it stick: once is another draw of the same lottery, and a third
# would eat the round.
REPEAT_TIMES = 2


def read_prefs() -> dict:
    """Everything remembered between runs. Anything unreadable reads as empty."""
    try:
        prefs = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return prefs if isinstance(prefs, dict) else {}


def write_prefs(**updates) -> None:
    """Merge and write. Writing the whole file from one caller's value would
    drop every other preference, so each key is folded into what is on disk."""
    # A read-only install must never break the game.
    with contextlib.suppress(OSError):
        PREFS_FILE.write_text(json.dumps(read_prefs() | updates), encoding="utf-8")


def load_allowed() -> set[int]:
    """Numbers selected last time. A drill often runs for weeks on one number,
    so the choice has to survive a restart. Anything unreadable or nonsensical
    falls back to 'all numbers on'."""
    try:
        raw = read_prefs()["numbers"]
        sel = {int(n) for n in raw if NUM_MIN <= int(n) <= NUM_MAX}
    except (ValueError, TypeError, KeyError):
        sel = set()
    return sel or set(range(NUM_MIN, NUM_MAX + 1))


def save_allowed(sel: set[int]) -> None:
    write_prefs(numbers=sorted(sel))


def load_theme() -> str:
    """The palette chosen last time — girly mode outlives a restart."""
    name = read_prefs().get("theme")
    return name if name in THEMES else "default"


def load_dictation() -> bool:
    """Whether each question is read aloud. On unless it was switched off: a
    child who is still learning to read the signs needs to hear the card, and
    that is the mode the game was built around."""
    return bool(read_prefs().get("dictation", True))


def load_language() -> str | None:
    """The language chosen at setup, or None if the app has never been set up."""
    value = read_prefs().get("language")
    return value if isinstance(value, str) and value in languages.BY_CODE else None


def save_language(code: str) -> None:
    """Remember the language so the next launch starts in it."""
    write_prefs(language=code)


def installed_languages() -> list[str]:
    """Registered languages whose Vosk model is already on disk."""
    return languages.installed()


def needs_language_setup() -> bool:
    """True when the app has no speech model at all yet, so setup must run.

    A language whose model is present launches straight into it; only a totally
    empty store — the true first run, or a user who removed every model — asks.
    """
    return not installed_languages()


def resolve_language() -> str:
    """Choose the startup language, set it globally, and return it.

    The saved language wins when its model is actually installed — that is the
    ordinary case on every launch after setup. Otherwise the first installed
    model is used, and failing that the default. The setup screen replaces the
    last two fallbacks by asking, once it is wired in.
    """
    saved = load_language()
    if saved is not None and languages.is_installed(saved):
        chosen = saved
    else:
        available = installed_languages()
        chosen = available[0] if available else (saved or DEFAULT_LANG)
    global LANG  # noqa: PLW0603 - the one app-wide setting, assigned once here
    LANG = chosen
    return chosen


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class Card:
    a: int
    b: int
    mode: str
    answer: int
    # measurement / state
    time_taken: float = 0.0  # seconds spent on the first attempt
    retry_time: float = 0.0  # ...and on the reinforcement round, if it came back
    attempts: int = 0
    correct_first_try: bool | None = None
    is_retry: bool = False

    @property
    def text(self) -> str:
        return f"{self.a} {OP_SYMBOL[self.mode]} {self.b}"

    @property
    def total_time(self) -> float:
        """Every second this card cost, both sittings added up.

        A card that was missed is asked again at the end, and what it really
        took to answer is the two goes together — grading it on the first alone
        would make a card that was failed *fast* look like an easy one.
        """
        return self.time_taken + self.retry_time


def spoken_answer(nums: list[int], card: Card) -> int | None:
    """Which number of an utterance the kid meant as their answer.

    Thinking out loud is how kids do mental maths, so the mic hears the question
    read back ("quinze moins quatre…") far more often than a bare answer, and the
    two operands leading an utterance are dropped for that reason. None here
    means "not an answer" — the app just keeps listening, so hesitating aloud
    costs nothing.

    The recognizer only ever hands this function utterances that were numbers and
    nothing else (see ``voice.strict_numbers``), so this is only about *which* of
    several numbers was meant. Whether the number it picks is trustworthy enough
    to take the card off the screen is a separate question, and the one that
    matters — it is answered by the acoustic gate in ``voice.SpeechInput``.
    """
    if not nums:
        return None
    if nums[:2] == [card.a, card.b]:  # the question, read back
        nums = nums[2:]
    if not nums:
        return None
    if card.answer in nums:  # "quinze moins quatre, ça fait onze"
        return card.answer
    # A lone operand is taken as an ordinary wrong answer: a whole session is
    # built on one number ("− 4"), so refusing it made that number unsayable on
    # every card of the session. It is also the one wrong answer a half-heard
    # read-back can produce — the cost of hearing it is one card marked wrong and
    # asked again at the end, which is the same thing that happens when the child
    # really does answer 4.
    return nums[-1]  # self-correction: the last number wins


def make_card(mode: str, a: int, b: int) -> Card:
    if mode == "addition":
        return Card(a, b, mode, a + b)
    if mode == "subtraction":
        return Card(a, b, mode, a - b)
    if mode == "multiplication":
        return Card(a, b, mode, a * b)
    if mode == "division":
        return Card(a, b, mode, a // b)
    raise ValueError(mode)


def every_operation(mode: str, allowed: set[int]) -> list[tuple[int, int]]:
    """Every distinct operation this mode can ask with these numbers.

    `b` — the number the kid operates *with* — is the one taken from the allowed
    set, so locking it down to {1} yields x−1, y−1, z−1.
    """
    pairs: list[tuple[int, int]] = []
    for b in sorted(allowed):
        if mode == "addition":
            pairs += [(a, b) for a in range(RANGES["addition"]["max"] + 1)]
        elif mode == "subtraction":
            # start at b so the result never goes negative
            pairs += [(a, b) for a in range(b, RANGES["subtraction"]["max"] + 1)]
        elif mode == "multiplication":
            pairs += [(a, b) for a in range(RANGES["multiplication"]["max_factor"] + 1)]
        elif mode == "division":
            # a = answer*b, which guarantees an integer result
            pairs += [
                (ans * b, b) for ans in range(RANGES["division"]["max_factor"] + 1)
            ]
        else:
            raise ValueError(mode)
    return pairs


def _deal(pool: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    """n operations as a shuffled bag: no operation comes back until every other
    one has been asked. Drawing each card independently meant the same easy sum
    could show up two or three times in a round.

    Locking the numbers down can leave fewer distinct operations than cards asked
    for (7× has only 11 facts). The card count wins there — the bag is reshuffled
    and dealt again, so repeats start only once the kid has seen the whole set.
    """
    out: list[tuple[int, int]] = []
    while len(out) < n:
        bag = random.sample(pool, len(pool))
        if out and len(bag) > 1 and bag[0] == out[-1]:
            bag.append(bag.pop(0))  # no repeat across the seam either
        out += bag[: n - len(out)]
    return out


def _lone(seq: Sequence[tuple[int, int]], i: int) -> bool:
    """Is the card at `i` different from both the cards either side of it?"""
    return (i == 0 or seq[i] != seq[i - 1]) and (
        i + 1 == len(seq) or seq[i] != seq[i + 1]
    )


def _spread(ops: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Shuffle, then push identical operations apart.

    The bag deal already keeps its own repeats away from each other; this is for
    the deck once revision cards have been mixed into it, where the same
    operation is deliberately in the pile more than once. Asking it twice in a
    row would test the child's short-term memory of the last answer rather than
    the fact itself, which is the whole reason for repeating it.

    Each pair that lands side by side trades one of its halves for the first
    card, anywhere in the deck, that leaves both ends of the trade with
    strangers for neighbours. Cards already behind the pair are candidates too:
    a pair that lands on the last two slots has nothing ahead of it to trade
    with. Revision is capped at half the deck, so there is always somewhere to go.
    """
    out = random.sample(ops, len(ops))
    for i in range(1, len(out)):
        if _lone(out, i):
            continue
        # Everything except this card's twin at i - 1, which it cannot fix.
        for j in (*range(i + 1, len(out)), *range(i - 1)):
            out[i], out[j] = out[j], out[i]
            if _lone(out, i) and _lone(out, j):
                break
            out[i], out[j] = out[j], out[i]  # no good — put it back, try the next
    return out


def make_deck(
    mode: str,
    n: int,
    allowed: set[int] | None = None,
    repeat: Sequence[tuple[int, int]] = (),
    times: int = REPEAT_TIMES,
) -> list[Card]:
    """Deal n cards, with `repeat` — last session's difficulties — dealt `times`
    each so a fact that fought back is met again before it is forgotten.

    Revision never takes more than half the round: the rest of the family still
    has to get a turn, and a deck made only of yesterday's failures is a
    punishment rather than a drill. Operations the current settings cannot ask
    are dropped, so switching from −4 to ×2 quietly leaves the −4 revision behind
    instead of dealing a card that does not belong to the session.
    """
    pool = every_operation(mode, allowed or set(range(NUM_MIN, NUM_MAX + 1)))
    if not pool:
        return []
    askable = set(pool)
    wanted = [op for op in repeat if op in askable][: max(0, n // (2 * times))]
    forced = [op for op in wanted for _ in range(times)]
    if not forced:
        return [make_card(mode, a, b) for a, b in _deal(pool, n)]
    # The rest of the deck is dealt from what is *left*: a revision card already
    # has its turns, and letting the bag deal it a third one would drown the
    # round in one fact. Falling back to the whole pool covers the case where
    # revision has claimed every operation the settings can ask.
    rest = [op for op in pool if op not in set(wanted)] or pool
    ops = _spread(_deal(rest, max(0, n - len(forced))) + forced)
    return [make_card(mode, a, b) for a, b in ops]


def carry_over(
    sessions: Sequence[resultslog.Session], mode: str, allowed: set[int]
) -> list[tuple[int, int]]:
    """The operations to drill again: the difficulties of the last day played.

    Read back out of the log rather than carried in memory, so revision survives
    the app being closed — and so a parent can type a card into the cell and
    have it dealt tomorrow.

    Only what this session can actually ask comes through: the same operation,
    and a number that is switched on. Change family and the revision quietly
    waits for the day you come back to it, rather than dealing a card that does
    not belong to the round.
    """
    days = [s.day for s in sessions if s.day and s.difficultes]
    if not days:
        return []
    last = max(days)
    ops: list[tuple[int, int]] = []
    for session in sessions:
        if session.day != last:
            continue
        for text in session.difficultes:
            parsed = resultslog.parse_operation(text)
            if parsed is None:
                continue
            op_mode, a, b = parsed
            if op_mode == mode and b in allowed and (a, b) not in ops:
                ops.append((a, b))
    return ops


def difficult_cards(
    cards: Sequence[Card], top: int = resultslog.TOP_DIFFICULT
) -> list[Card]:
    """The cards this session fought with, hardest first.

    Two different things make a card hard, and the log wants both: getting it
    wrong, and labouring over it. Wrong ranks first — a fact that was not known
    is not made easy by a fast guess — and inside each group the slower card
    comes first. Time is `total_time`, so a card that was missed *and* then
    laboured over in the reinforcement round lands where it belongs.

    One entry per operation: a short number selection deals the same fact twice
    in a round, and the list is more useful five cards long than five copies of
    one. A clean, quick session still fills it — these are the session's
    *relatively* hardest cards, and worth another look tomorrow even on a good
    day.
    """
    ranked = sorted(
        cards, key=lambda c: (c.correct_first_try is not False, -c.total_time)
    )
    out: list[Card] = []
    seen: set[tuple[str, int, int]] = set()
    for card in ranked:
        key = (card.mode, card.a, card.b)
        if key in seen:
            continue
        seen.add(key)
        out.append(card)
        if len(out) == top:
            break
    return out


# --------------------------------------------------------------------------- #
# A colored button that works everywhere.
#
# macOS Tk renders native `tk.Button`s and ignores bg/fg, so themed buttons come
# out white-on-white. A Label honors colors on every platform, so we drive one
# like a button with mouse bindings.
# --------------------------------------------------------------------------- #


BTN_RADIUS = 12  # px; clamped to half the height so short buttons stay sane
BTN_GAP = 8  # px between an icon and its label
# The menu's page is a centred box no wider than this. Wider screens keep the
# content readable instead of stretching the tiles to the window edges; narrower
# ones shrink the box to fit, so it stays responsive rather than fixed.
CONTENT_MAX = 1400


_ROUND_CACHE: dict[tuple, tk.PhotoImage] = {}


def _hex_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rrect_alpha(x: int, y: int, w: int, h: int, r: float, ox: int, oy: int) -> float:
    """Coverage of one pixel by a rounded rectangle, from its signed distance.

    The rectangle is `w` by `h` with corner radius `r`, its top-left at (`ox`,
    `oy`). A distance of 0 is the edge, so `0.5 - d` is a one-pixel smooth band
    across it — the antialiasing a canvas polygon cannot give."""
    cx, cy = x - ox + 0.5, y - oy + 0.5
    qx = abs(cx - w / 2) - (w / 2 - r)
    qy = abs(cy - h / 2) - (h / 2 - r)
    d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
    return min(1.0, max(0.0, 0.5 - d))


def _png_bytes(w: int, h: int, pixels: bytearray) -> bytes:
    """A minimal RGBA PNG, so Tk can show the shape with its own transparency."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: none
        raw += pixels[y * w * 4 : (y + 1) * w * 4]

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def rounded_photo(
    w: int,
    h: int,
    radius: int,
    fill: str,
    border: str | None = None,
    border_width: int = 0,
) -> tk.PhotoImage:
    """An antialiased rounded rectangle as a Tk image, cached per shape/colour.

    Buttons redraw on every hover and panels on every resize, so the raster is
    kept: a colour called for again is served from `_ROUND_CACHE`."""
    key = (w, h, radius, fill, border, border_width)
    cached = _ROUND_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_ROUND_CACHE) > 512:
        _ROUND_CACHE.clear()
    fill_rgb = _hex_rgb(fill)
    border_rgb = _hex_rgb(border) if border else fill_rgb
    r = max(0, min(radius, w // 2, h // 2))
    bw = max(0, min(border_width, w // 2, h // 2))
    px = bytearray(w * h * 4)
    for y in range(h):
        base = y * w * 4
        for x in range(w):
            a_out = _rrect_alpha(x, y, w, h, r, 0, 0)
            if a_out <= 0.0:
                continue
            if bw > 0:
                a_in = _rrect_alpha(
                    x, y, w - 2 * bw, h - 2 * bw, max(0, r - bw), bw, bw
                )
                t = a_in / a_out
                rgb = (
                    round(fill_rgb[0] * t + border_rgb[0] * (1 - t)),
                    round(fill_rgb[1] * t + border_rgb[1] * (1 - t)),
                    round(fill_rgb[2] * t + border_rgb[2] * (1 - t)),
                )
            else:
                rgb = fill_rgb
            i = base + x * 4
            px[i] = rgb[0]
            px[i + 1] = rgb[1]
            px[i + 2] = rgb[2]
            px[i + 3] = round(a_out * 255)
    img = tk.PhotoImage(data=base64.b64encode(_png_bytes(w, h, px)), format="png")
    _ROUND_CACHE[key] = img
    return img


class RoundedPanel:
    """A rounded, bordered panel drawn behind an inner frame.

    A Tk frame is square, so the panel is a canvas carrying the rounded raster;
    the content lives in a frame set *inside* it, far enough from the edges that
    its own square corners sit over the rounded fill and never show."""

    def __init__(
        self,
        parent,
        fill: str | None = None,
        border: str | None = None,
        radius: int = 18,
        pad: int = 16,
        page: str | None = None,
    ):
        # Resolved here, not in the signature: a default argument is evaluated
        # once at import, which would nail the panel to the theme loaded first.
        self.fill = fill or SURFACE
        self.border = border or BORDER
        self.radius, self.pad = radius, pad
        self.canvas = tk.Canvas(parent, bg=page or BG, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=self.fill)
        self._win = self.canvas.create_window(pad, pad, window=self.inner, anchor="nw")
        self._size = (0, 0)
        self.canvas.bind("<Configure>", self._fit)
        self.inner.bind("<Configure>", self._fit)

    def _fit(self, _event=None) -> None:
        w = self.canvas.winfo_width()
        if w <= 1:
            w = self.inner.winfo_reqwidth() + 2 * self.pad
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if (w, h) == self._size:
            return
        self._size = (w, h)
        self.canvas.configure(height=h)
        self.canvas.itemconfigure(self._win, width=max(1, w - 2 * self.pad))
        self._img = rounded_photo(w, h, self.radius, self.fill, self.border, 1)
        self.canvas.delete("bg")
        self.canvas.create_image(0, 0, anchor="nw", image=self._img, tags="bg")
        self.canvas.tag_lower("bg")


class Btn(tk.Canvas):
    """A rounded button, drawn on a canvas.

    A Tk widget has square corners, so the shape is an antialiased raster with
    the text (and optional icon) painted on top. The `tk.Label`-like surface the game
    used before is kept — `text`, `bg`, `fg`, `set_colors`, `anchor`, an icon with
    `compound` — so no caller cares how it is drawn.

    `bg` is the button's *fill*; the canvas behind it takes the parent's colour,
    so the corners blend into the page instead of showing a box around them.
    """

    def __init__(
        self,
        parent,
        text,
        command=None,
        bg=None,
        fg=None,
        font=None,
        active_bg=None,
        active_fg=None,
        padx=14,
        pady=10,
        width=None,
        anchor="center",
        image=None,
        compound="left",
        radius=None,
        **kw,
    ):
        # The palette defaults are filled in here rather than in the signature:
        # default arguments are evaluated once at import, which would nail every
        # unstyled button to whichever theme happened to be loaded first.
        self._fill = bg or CARD_BG
        self._fg = fg or TEXT
        self._abg = active_bg or ACCENT
        self._afg = active_fg or INK
        self._text = str(text)
        self._font = self._norm_font(font)
        self._command = command
        self._padx, self._pady = padx, pady
        self._width = width
        self._anchor = anchor
        self._image = image
        self._compound = compound
        self._radius = radius
        self._hover = False
        page = self._parent_bg(parent)
        super().__init__(
            parent, highlightthickness=0, bd=0, bg=page, cursor="hand2", **kw
        )
        self._reqw, self._reqh = self._measure()
        super().configure(width=self._reqw, height=self._reqh)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._redraw)
        self._redraw()

    # -- compatible, Label-like surface ------------------------------------ #
    def configure(self, cnf=None, **kw):
        if cnf is not None:
            kw.update(cnf)
        redraw = False
        if "text" in kw:
            self._text = str(kw.pop("text"))
            redraw = True
        if "bg" in kw:
            self._fill = kw.pop("bg")
            redraw = True
        if "fg" in kw:
            self._fg = kw.pop("fg")
            redraw = True
        if "font" in kw:
            self._font = self._norm_font(kw.pop("font"))
            redraw = True
        if "image" in kw:
            self._image = kw.pop("image")
            redraw = True
        if "compound" in kw:
            self._compound = kw.pop("compound")
            redraw = True
        if redraw:
            self._reqw, self._reqh = self._measure()
            super().configure(width=self._reqw, height=self._reqh)
        if kw:
            super().configure(**kw)
        if redraw:
            self._redraw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "bg":
            return self._fill
        if key == "fg":
            return self._fg
        return super().cget(key)

    def set_colors(self, bg, fg):
        self._fill, self._fg = bg, fg
        self._redraw()

    # -- geometry + painting ------------------------------------------------ #
    @staticmethod
    def _norm_font(font):
        if isinstance(font, tkfont.Font):
            return font
        return (
            tkfont.Font(family="Helvetica", size=12)
            if font is None
            else tkfont.Font(font=font)
        )

    @staticmethod
    def _parent_bg(parent):
        try:
            return parent.cget("bg")
        except (tk.TclError, AttributeError):
            return BG

    def _measure(self) -> tuple[int, int]:
        th = self._font.metrics("linespace")
        tw = self._font.measure(self._text)
        iw = self._image.width() if self._image is not None else 0
        ih = self._image.height() if self._image is not None else 0
        content = tw + (iw + BTN_GAP if iw else 0)
        if self._width is not None:
            content = max(content, self._width * self._font.measure("0"))
        return content + 2 * self._padx, max(th, ih) + 2 * self._pady

    def _corner(self, w, h) -> int:
        r = (
            self._radius
            if self._radius is not None
            else min(BTN_RADIUS, max(6, h // 3))
        )
        return max(0, min(r, w // 2, h // 2))

    def _redraw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            w = self._reqw
        if h <= 1:
            h = self._reqh
        fill = self._abg if self._hover else self._fill
        fg = self._afg if self._hover else self._fg
        self.delete("all")
        self._bg_photo = rounded_photo(w, h, self._corner(w, h), fill)
        self.create_image(0, 0, anchor="nw", image=self._bg_photo)
        cy = h / 2
        if self._image is not None:
            iw = self._image.width()
            gap = BTN_GAP if self._text else 0
            content = iw + gap + self._font.measure(self._text)
            x = self._padx if self._anchor == "w" else (w - content) / 2
            self.create_image(x, cy, image=self._image, anchor="w")
            self.create_text(
                x + iw + gap, cy, text=self._text, font=self._font, fill=fg, anchor="w"
            )
        elif self._anchor == "w":
            self.create_text(
                self._padx, cy, text=self._text, font=self._font, fill=fg, anchor="w"
            )
        else:
            self.create_text(
                w / 2, cy, text=self._text, font=self._font, fill=fg, anchor="center"
            )

    # -- interaction -------------------------------------------------------- #
    def _on_enter(self, _e):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._redraw()

    def _on_press(self, _e):
        self._hover = True
        self._redraw()

    def _on_release(self, e):
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._hover = False
        self._redraw()
        if inside and self._command:
            self._command()


class Tooltip:
    """A small label that appears under a widget on hover and says what it does.

    A borderless Toplevel rather than a Label inside the screen: the bubble is
    wider than the icon it hangs off and has to be free to overflow the header
    row (and, near the right edge, to slide back inside the screen).
    """

    def __init__(self, widget, text, font, delay=350):
        self.widget, self.text, self.font, self.delay = widget, text, font, delay
        self.tip = None
        self.job = None
        # `add="+"` — Btn already binds these to its own hover paint.
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.job:
            with contextlib.suppress(tk.TclError):
                self.widget.after_cancel(self.job)
            self.job = None

    def _show(self):
        self.job = None
        if self.tip or not self.widget.winfo_exists():
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)  # no title bar, no shadow frame
        with contextlib.suppress(tk.TclError):
            self.tip.wm_attributes("-topmost", True)
        tk.Label(
            self.tip,
            text=self.text,
            font=self.font,
            bg=SURFACE,
            fg=TEXT,
            padx=10,
            pady=5,
            highlightthickness=1,
            highlightbackground=BORDER,
        ).pack()
        self.tip.update_idletasks()
        # Hang it under the widget, right edges aligned — the button lives in the
        # top-right corner, so a left-aligned bubble would run off the screen.
        w = self.tip.winfo_reqwidth()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() - w
        x = max(4, min(x, self.widget.winfo_screenwidth() - w - 4))
        self.tip.wm_geometry(
            f"+{x}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 6}"
        )

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Dropdown(Btn):
    """A `Btn` that drops a list under itself and calls back with what was picked.

    Neither a `ttk.Combobox` nor a `tk.OptionMenu`: both paint themselves in the
    platform's own colors — the very reason `Btn` is a Label — so a native popup
    over the pink palette comes out grey on grey. The list is a borderless
    Toplevel instead, like `Tooltip`, made of `Btn` rows.

    `options` is a list of `(value, label)` pairs; the row standing for the
    current value is highlighted. A list too tall for the screen scrolls, so
    years of months can pile up without the bottom of it falling off the display.
    """

    ARROW = "  ▾"
    MAX_SCREEN = 0.6  # of the screen height: taller lists scroll

    def __init__(self, parent, options, value, command, font, **kw):
        self.options = list(options)
        self.value = value
        self._on_pick = command
        self._font = font
        self._pop = None
        super().__init__(
            parent, self._text_for(value), command=self._toggle, font=font, **kw
        )
        # A screen change destroys the button; the popup is a Toplevel, so it
        # would otherwise be left floating over whatever comes next.
        self.bind("<Destroy>", lambda e: self._close(), add="+")

    def _text_for(self, value) -> str:
        return next((lb for v, lb in self.options if v == value), "") + self.ARROW

    def _toggle(self):
        self._close() if self._pop else self._open()

    def _open(self):
        self.update_idletasks()  # the list hangs off this button's geometry
        self._pop = top = tk.Toplevel(self)
        top.wm_overrideredirect(True)  # no title bar, no shadow
        # Built out of sight: measuring the rows maps the window, and the window
        # manager places it before it hears where we want it — so an unhidden
        # popup flashes in the corner of the screen on its way under the button.
        top.wm_withdraw()
        with contextlib.suppress(tk.TclError):
            top.wm_attributes("-topmost", True)
        frame = tk.Frame(
            top, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER
        )
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, bg=SURFACE, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        rows = tk.Frame(canvas, bg=SURFACE)
        window = canvas.create_window((0, 0), window=rows, anchor="nw")
        here = None
        for v, label in self.options:
            chosen = v == self.value
            row = Btn(
                rows,
                label,
                command=lambda v=v: self._choose(v),
                font=self._font,
                bg=ACCENT if chosen else SURFACE,
                fg=INK if chosen else TEXT,
                padx=16,
                pady=7,
                anchor="w",
            )
            row.pack(fill="x")
            here = row if chosen else here

        rows.update_idletasks()
        w = max(rows.winfo_reqwidth(), self.winfo_width())
        h = rows.winfo_reqheight()
        view = min(h, int(self.winfo_screenheight() * self.MAX_SCREEN))
        # `yscrollincrement=1`: without it a canvas scrolls a tenth of its own
        # height per unit, which on a list this size is half the popup a nudge.
        canvas.configure(
            width=w, height=view, scrollregion=(0, 0, w, h), yscrollincrement=1
        )
        canvas.itemconfig(window, width=w)  # rows span the full width
        if h > view:
            # Bound on the Toplevel: a row Label's bindtags include its window,
            # so a wheel event over a row is looked up here as well.
            bind_wheel(top, lambda px: canvas.yview_scroll(px, "units"))
            if here is not None:  # open on the month you are on
                canvas.yview_moveto(max(0.0, (here.winfo_y() - view / 2) / h))

        # Hang it under the button, pushed back inside the screen if it overruns.
        top.update_idletasks()
        x = min(
            max(self.winfo_rootx(), 4),
            self.winfo_screenwidth() - top.winfo_reqwidth() - 4,
        )
        top.wm_geometry(f"+{x}+{self.winfo_rooty() + self.winfo_height() + 4}")
        top.wm_deiconify()

        # The grab hands every click outside the list to the Toplevel itself —
        # which is how clicking anywhere else (this button included) closes it.
        top.bind("<ButtonRelease-1>", lambda e: e.widget is top and self._close())
        top.bind("<Escape>", lambda e: self._close())
        try:
            top.grab_set()
            top.focus_set()
        except tk.TclError:
            pass

    def _choose(self, value):
        self._close()
        if value != self.value:
            self.value = value
            self.config(text=self._text_for(value))
            self._on_pick(value)

    def _close(self):
        if self._pop:
            with contextlib.suppress(tk.TclError):
                self._pop.grab_release()
            self._pop.destroy()
            self._pop = None


# --------------------------------------------------------------------------- #
# Progress chart
#
# One measure (time) over one axis (the date), so it is one series: a soft curve
# with dots, a recessive horizontal grid, and a hover tooltip. No legend — a
# single series is named by the title above it — and deliberately no
# color-by-famille: families are categorical and open-ended (any text the parent
# types), and a cycled palette would be unreadable. The famille lives in the
# tooltip and in the table instead.
#
# One point per *day*, averaging that day's sessions: three tries in an
# afternoon used to stack into a vertical spike that said nothing about
# progress. The tooltip still names how many sessions the point stands for.
# --------------------------------------------------------------------------- #


class TimeChart(tk.Canvas):
    PAD_L, PAD_R, PAD_T, PAD_B = 66, 24, 16, 30
    HIT = 32  # px: how close the pointer must be to a dot
    # Round y steps a parent reads at a glance: 5 s up to 15 min.
    STEPS = (5, 10, 15, 20, 30, 60, 90, 120, 180, 300, 600, 900)

    def __init__(self, parent, sessions, font, height=220):
        super().__init__(parent, bg=BG, height=height, highlightthickness=0, bd=0)
        # Plotting needs both a date (x) and a time (y). Rows missing either
        # still show up in the table — they just can't be placed on the chart.
        plottable = [s for s in sessions if s.day and s.seconds is not None]
        by_day: dict[date, list[resultslog.Session]] = {}
        for s in plottable:
            by_day.setdefault(s.day, []).append(s)
        # (day, mean seconds, the sessions behind it) — one entry per day, in order
        self.days: list[tuple[date, float, list[resultslog.Session]]] = [
            (d, sum(s_.seconds for s_ in g if s_.seconds is not None) / len(g), g)
            for d, g in sorted(by_day.items())
        ]
        self.font = font
        # The second series: the day's grade as a ratio, 0..1. It is its own
        # curve on its own axis, so a day that recorded no score is simply not
        # on that line — the old paper rows leave a gap, they do not read as 0.
        self.grades: list[tuple[date, float]] = []
        decks: dict[int, int] = {}
        for day, _, group in self.days:
            note = resultslog.grade(group)
            if not note:
                continue
            self.grades.append((day, note[0] / note[1]))
            decks[note[1]] = decks.get(note[1], 0) + 1
        # The right axis is written out of the deck the grades are mostly out of
        # — the smaller one on a tie, the same rule `grade()` uses to pick the
        # deck a mixed day is graded over.
        self.deck = min(decks, key=lambda n: (-decks[n], n)) if decks else 0
        # Reserve the bottom band from the real font metrics — the fonts scale
        # with the screen, so a fixed 30 px clipped the date labels on a tall one.
        self.PAD_B = font.metrics("linespace") + 16
        # Both axes are headed inside the canvas, on one line above the plot, so
        # the two titles sit level with each other however the fonts scale.
        self.PAD_T = font.metrics("linespace") + 12
        if self.deck:
            # Room for the grade axis: its labels, `20/20` at their widest, and
            # the title above them saying what they count.
            self.PAD_R = max(
                font.measure(f"{self.deck}/{self.deck}") + 20,
                font.measure(T("axis_grade")) + 14,
            )
        self.pts: list[tuple[float, float, tuple]] = []
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("tip"))

    @staticmethod
    def _curve(pts: Sequence[tuple[float, float, Any]]) -> list[float]:
        """Sample a monotone cubic (Fritsch–Carlson) through every point.

        Tk's own `smooth=True` treats the coordinates as spline *control* points,
        so the curve misses the dots it is supposed to join. Monotone cubic goes
        exactly through each one and, unlike Catmull-Rom, cannot overshoot — so a
        dip between two slow days never draws a time faster than either of them.
        """
        n = len(pts)
        if n < 3:
            return [c for x, y, _ in pts for c in (x, y)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
        m = [d[0]] + [(d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
        for i in range(n - 1):
            if d[i] == 0:  # a flat run stays flat
                m[i] = m[i + 1] = 0.0
                continue
            a, b = m[i] / d[i], m[i + 1] / d[i]
            if a * a + b * b > 9:  # clamp the tangents: no overshoot
                t = 3.0 / (a * a + b * b) ** 0.5
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]

        out: list[float] = []
        for i in range(n - 1):
            h = xs[i + 1] - xs[i]
            steps = max(2, min(48, int(h / 4)))  # ~4 px per sampled segment
            for k in range(steps + (1 if i == n - 2 else 0)):
                t = k / steps
                t2, t3 = t * t, t * t * t
                out += [
                    xs[i] + h * t,
                    (2 * t3 - 3 * t2 + 1) * ys[i]
                    + (t3 - 2 * t2 + t) * h * m[i]
                    + (-2 * t3 + 3 * t2) * ys[i + 1]
                    + (t3 - t2) * h * m[i + 1],
                ]
        return out

    def _y_scale(self, peak: float) -> tuple[int, float]:
        """A step size and a top of scale that give at most 5 gridlines."""
        for step in self.STEPS:
            ticks = int(peak / step) + (1 if peak % step else 0)
            if ticks <= 5:
                return step, max(step, ticks * step)
        step = self.STEPS[-1]
        return step, max(step, (int(peak / step) + 1) * step)

    def redraw(self):
        self.delete("all")
        self.pts = []
        w, h = self.winfo_width(), self.winfo_height()
        if w < 160 or h < 90 or not self.days:
            return
        x0, x1 = self.PAD_L, w - self.PAD_R
        y0, y1 = self.PAD_T, h - self.PAD_B
        step, top = self._y_scale(max(mean for _, mean, _ in self.days))

        def to_y(v):
            return y1 - (v / top) * (y1 - y0)

        # grid + y labels (a zero baseline: these are durations, so the height
        # of a point has to mean its whole magnitude)
        rows: list[float] = []
        v = 0.0
        while v <= top + 1e-9:
            rows.append(v)
            v += step
        for i, v in enumerate(rows):
            y = to_y(v)
            self.create_line(x0, y, x1, y, fill=BORDER)
            self.create_text(
                x0 - 10,
                y,
                text=resultslog.fmt_duration(v, 0),
                anchor="e",
                font=self.font,
                fill=ACCENT,
            )
            # The grades ride the same gridlines on their own scale: zero at the
            # baseline, a full deck at the top. Each axis is written in the
            # colour of the curve it belongs to — that, not a legend, is what
            # says which line is being read.
            if self.deck and len(rows) > 1:
                self.create_text(
                    x1 + 10,
                    y,
                    text=f"{self.deck * i / (len(rows) - 1):.0f}/{self.deck}",
                    anchor="w",
                    font=self.font,
                    fill=CHART_GRADE,
                )

        # Each axis is headed in the colour of the curve it carries, and both
        # heads share the baseline y0 - 6 — the reader's eye runs straight from
        # one to the other.
        self.create_text(
            2,
            y0 - 10,
            text=T("chart_title"),
            anchor="sw",
            font=self.font,
            fill=ACCENT,
        )
        if self.deck:
            self.create_text(
                x1 + 10,
                y0 - 10,
                text=T("axis_grade"),
                anchor="sw",
                font=self.font,
                fill=CHART_GRADE,
            )

        # x is real calendar time, so plateaus and gaps are visible.
        ords = [d.toordinal() for d, _, _ in self.days]
        lo, span = min(ords), max(ords) - min(ords)

        def to_x(o):
            return (x0 + x1) / 2 if not span else x0 + (o - lo) / span * (x1 - x0)

        pts = []
        for entry in self.days:
            day, mean, _ = entry
            pts.append((to_x(day.toordinal()), to_y(mean), entry))
        # The grade line first, so where the two cross it is the times — the
        # thing the screen is titled after — that stay on top.
        by_day = {entry[0]: entry for entry in self.days}
        gpts = [
            (to_x(day.toordinal()), y1 - ratio * (y1 - y0), by_day[day])
            for day, ratio in self.grades
        ]
        self._series(gpts, CHART_GRADE, 3)
        self._series(pts, ACCENT, 4)
        # Both lines answer the pointer: the grade point of a day is as good a
        # handle on that day as its time point.
        self.pts = pts + gpts
        label_y = y1 + 10

        # x labels: distinct days, thinned by *pixel* distance — the axis is
        # calendar time, so evenly spaced days are not evenly spaced columns
        # (two sessions a day apart would print on top of each other).
        gap = self.font.measure("00/00") + 22
        kept: list[tuple[date, float]] = []
        for d, _, _ in self.days:
            xd = to_x(d.toordinal())
            if not kept or xd - kept[-1][1] >= gap:
                kept.append((d, xd))
        last = self.days[-1][0]
        if kept and kept[-1][0] != last:  # the newest date always gets a label
            xd = to_x(last.toordinal())
            while kept and xd - kept[-1][1] < gap:
                kept.pop()
            kept.append((last, xd))
        for d, xd in kept:
            self.create_text(
                xd,
                label_y,
                text=d.strftime("%d/%m"),
                anchor="n",
                font=self.font,
                fill=MUTED,
            )

    def _series(self, pts, color, radius):
        """One curve and its dots, in one colour — the times or the grades."""
        if len(pts) > 1:
            self.create_line(
                self._curve(pts),
                fill=color,
                width=2,
                capstyle="round",
                joinstyle="round",
            )
        for x, y, _ in pts:
            # a 2px background ring keeps a dot legible where the curve runs steep
            self.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill=color,
                outline=BG,
                width=2,
            )

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        """Tk has no rounded rectangle. A polygon that repeats its corner points
        and is then smoothed gives one: the doubled points pin the straight
        edges, and the single ones round the turns."""
        return self.create_polygon(
            x0 + r,
            y0,
            x1 - r,
            y0,
            x1 - r,
            y0,
            x1,
            y0,
            x1,
            y0 + r,
            x1,
            y0 + r,
            x1,
            y1 - r,
            x1,
            y1 - r,
            x1,
            y1,
            x1 - r,
            y1,
            x1 - r,
            y1,
            x0 + r,
            y1,
            x0 + r,
            y1,
            x0,
            y1,
            x0,
            y1 - r,
            x0,
            y1 - r,
            x0,
            y0 + r,
            x0,
            y0 + r,
            x0,
            y0,
            smooth=True,
            **kw,
        )

    def _hover(self, e):
        self.delete("tip")
        best, closest = None, self.HIT**2
        for x, y, s in self.pts:
            d = (x - e.x) ** 2 + (y - e.y) ** 2
            if d < closest:
                best, closest = (x, y, s), d
        if not best:
            return
        x, y, (day, mean, group) = best
        # Distinct families, in the order they were played that day.
        fams = list(dict.fromkeys(s.famille for s in group if s.famille))
        lines = [day.strftime("%d/%m/%Y"), ", ".join(fams) or "—"]
        if len(group) > 1:
            lines.append(
                f"{T('n_sessions', n=len(group))} · "
                f"{T('avg_label')} {resultslog.fmt_duration(mean)}"
            )
        else:
            lines.append(resultslog.fmt_duration(mean))
        per_card = [s.avg for s in group if s.avg is not None]
        if per_card:
            lines.append(f"{T('col_avg')} : {sum(per_card) / len(per_card):.1f} s")
        # The grade curve carries the shape; the number itself lives here.
        note = resultslog.fmt_note(resultslog.grade(group))
        if note:
            lines.append(f"{T('col_note')} : {note}")

        lh = self.font.metrics("linespace") + 2
        bw = max(self.font.measure(t) for t in lines) + 20
        bh = lh * len(lines) + 14
        bx = min(max(x + 14, 4), self.winfo_width() - bw - 4)
        by = min(max(y - bh - 12, 4), self.winfo_height() - bh - 4)
        self.create_rectangle(
            bx, by, bx + bw, by + bh, fill=SURFACE, outline=BORDER, tags="tip"
        )
        for i, t in enumerate(lines):
            self.create_text(
                bx + 10,
                by + 7 + i * lh,
                text=t,
                anchor="nw",
                font=self.font,
                fill=ACCENT if i == 0 else TEXT,
                tags="tip",
            )
        self.create_oval(x - 6, y - 6, x + 6, y + 6, outline=TEXT, width=2, tags="tip")


# --------------------------------------------------------------------------- #
# First-run language setup
# --------------------------------------------------------------------------- #

_DOWNLOAD_POLL_MS = 80


def ordered_languages(current: str | None) -> list[languages.Language]:
    """The picker's order: the current language first, then the rest by endonym."""
    return sorted(
        languages.LANGUAGES,
        key=lambda lang: (lang.code != current, lang.endonym.lower()),
    )


def flag_image(code: str, height: int) -> tk.PhotoImage | None:
    """A language's flag as a Tk image, or None when the asset is not there.

    Tk 9 rasterizes the bundled SVG itself, exactly as the title art does, so a
    missing flag file — or Tk 8, which cannot read SVG — degrades to plain text
    with no error. Assets arrive in a later step; until then every row is text.
    """
    lang = languages.BY_CODE.get(code)
    if lang is None or not lang.flag:
        return None
    path = ASSETS_DIR / "flags" / f"{lang.flag}.svg"
    if not path.is_file():
        return None
    try:
        return tk.PhotoImage(file=str(path), format=f"svg -scaletoheight {height}")
    except tk.TclError:
        return None


class LanguagePicker:
    """Modal "Choose your language" dialog, with model download.

    Two callers: the first launch, before the main window exists (its parent is
    a hidden root), and later the settings screen. ``show()`` returns the chosen
    language code, or None if the user chose to continue without voice. The
    model downloads on a worker thread and reports back through a queue, because
    no Tk call may run off the main thread.
    """

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        intro: str,
        skip_label: str,
        current: str | None = None,
    ):
        self._queue: queue.Queue = queue.Queue()
        self._images: list[tk.PhotoImage] = []  # Tk drops unreferenced images
        self._current = current  # pinned to the top of the list
        self._installed: set[str] = set(languages.installed())
        self._polling = False
        self._bar: ttk.Progressbar | None = None
        self._bytes: tk.Label | None = None
        self.result: str | None = None

        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.configure(bg=BG)
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self._skip)

        outer = tk.Frame(self.top, bg=BG, padx=24, pady=20)
        outer.pack(fill="both", expand=True)
        tk.Label(
            outer, text=title, font=("Helvetica", 20, "bold"), fg=ACCENT, bg=BG
        ).pack(anchor="w")
        tk.Label(
            outer,
            text=intro,
            font=("Helvetica", 12),
            fg=MUTED,
            bg=BG,
            justify="left",
            wraplength=440,
        ).pack(anchor="w", pady=(6, 14))
        self.body = tk.Frame(outer, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.status = tk.Label(
            outer, text="", font=("Helvetica", 11), fg=MUTED, bg=BG, anchor="w"
        )
        self.status.pack(fill="x", pady=(10, 0))
        Btn(outer, skip_label, command=self._skip, font=("Helvetica", 12)).pack(
            anchor="e", pady=(12, 0)
        )
        self._show_list()

    # -- views ------------------------------------------------------------- #
    def _clear_body(self) -> None:
        for widget in self.body.winfo_children():
            widget.destroy()

    def _show_list(self) -> None:
        self._clear_body()
        self.status.config(text="")
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            wrap,
            bg=BG,
            highlightthickness=0,
            width=470,
            height=360,
            yscrollincrement=1,
        )
        bar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        bind_wheel(canvas, lambda px: canvas.yview_scroll(px, "units"))
        for lang in ordered_languages(self._current):
            self._language_row(inner, lang)

    def _language_row(self, parent: tk.Misc, lang: languages.Language) -> None:
        label = f"{lang.endonym}   ·   {lang.english}"
        if lang.code in self._installed:
            label += "   ✓"
        opts: dict = {}
        image = flag_image(lang.code, 22)
        if image is not None:
            self._images.append(image)
            opts = {"image": image, "compound": "left"}
        Btn(
            parent,
            label,
            command=lambda code=lang.code: self._choose(code),
            anchor="w",
            padx=12,
            pady=8,
            font=("Helvetica", 13),
            **opts,
        ).pack(fill="x", pady=2)

    def _show_download(self, lang: languages.Language) -> None:
        self._clear_body()
        tk.Label(
            self.body,
            text=f"Downloading {lang.endonym} ({lang.english})…",
            font=("Helvetica", 13),
            fg=TEXT,
            bg=BG,
        ).pack(anchor="w")
        self._bar = ttk.Progressbar(
            self.body, mode="determinate", maximum=max(1, lang.size), length=450
        )
        self._bar.pack(fill="x", pady=(10, 4))
        self._bytes = tk.Label(
            self.body, text="", font=("Helvetica", 11), fg=MUTED, bg=BG
        )
        self._bytes.pack(anchor="w")

    def _show_error(self, lang: languages.Language, message: str) -> None:
        self._clear_body()
        tk.Label(
            self.body,
            text=f"Could not install {lang.endonym}.",
            font=("Helvetica", 13),
            fg=TEXT,
            bg=BG,
        ).pack(anchor="w")
        tk.Label(
            self.body,
            text=message,
            font=("Helvetica", 11),
            fg=MUTED,
            bg=BG,
            wraplength=450,
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        Btn(
            self.body, "Try again", command=self._show_list, font=("Helvetica", 12)
        ).pack(anchor="w")

    # -- actions ----------------------------------------------------------- #
    def _choose(self, code: str) -> None:
        if code in self._installed:
            self._finish(code)  # already on disk: this is just a switch
            return
        self._download(code)

    def _download(self, code: str) -> None:
        lang = languages.BY_CODE[code]
        self._show_download(lang)
        self.status.config(text="This happens once; the model is kept for next time.")
        self._polling = True

        def progress(done: int, total: int) -> None:
            self._queue.put(("progress", done, total))

        def work() -> None:
            try:
                languages.download(code, progress=progress)
            except Exception as error:  # noqa: BLE001 - every failure is shown here
                self._queue.put(("error", code, str(error)))
            else:
                self._queue.put(("done", code, None))

        threading.Thread(target=work, daemon=True).start()
        self.top.after(_DOWNLOAD_POLL_MS, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                kind, first, second = self._queue.get_nowait()
                if kind == "progress":
                    if self._bar is not None and self._bytes is not None:
                        self._bar.config(value=first)
                        self._bytes.config(
                            text=f"{first / 1e6:.1f} / {second / 1e6:.1f} MB"
                        )
                elif kind == "done":
                    self._installed.add(first)
                    self._finish(first)
                    return
                else:  # "error"
                    self._polling = False
                    self._show_error(languages.BY_CODE[first], second)
                    return
        except queue.Empty:
            pass
        if self._polling:
            self.top.after(_DOWNLOAD_POLL_MS, self._poll)

    def _finish(self, code: str) -> None:
        save_language(code)
        self.result = code
        self._close()

    def _skip(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        self._polling = False
        with contextlib.suppress(tk.TclError):
            self.top.grab_release()
        self.top.destroy()

    def _center(self) -> None:
        self.top.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        x = max(0, (self.top.winfo_screenwidth() - w) // 2)
        y = max(0, (self.top.winfo_screenheight() - h) // 3)
        self.top.geometry(f"+{x}+{y}")

    def show(self) -> str | None:
        self._center()
        self.top.wait_visibility()
        self.top.grab_set()
        self.top.wait_window()
        return self.result


def choose_language_at_startup() -> str | None:
    """Ask for a language when no model is installed; a no-op on later launches.

    Runs before the main window exists, so it owns a hidden root that exists
    only to parent the dialog. Choosing installs and saves the model; skipping
    leaves the app fully playable on the keypad.
    """
    if not needs_language_setup():
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        picker = LanguagePicker(
            root,
            title="Choose your language",
            intro=(
                "Almathus downloads a small speech model so your child can answer "
                "out loud in their language. You can add more languages later in "
                "settings."
            ),
            skip_label="Continue without voice",
        )
        return picker.show()
    finally:
        root.destroy()


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #


class Almathus(tk.Tk):
    def __init__(self):
        super().__init__()
        # Rasters belong to the Tcl interpreter that made them: a fresh run may
        # share this module with a torn-down one (the tests do, one window per
        # case), so the cache starts empty rather than handing back a dead image.
        _ROUND_CACHE.clear()
        apply_theme(load_theme())  # before any widget reads a color
        self.title(T("title"))
        self.configure(bg=BG)
        self.minsize(720, 640)
        # Maximize to fill the screen as a normal window — NOT macOS' native
        # fullscreen mode (which hides the menu bar and jumps to its own space).
        self._maximize()

    def _usable_height(self) -> int:
        """How tall a window may actually be here — not how tall the screen is.

        The two are not the same, and the difference is what everything on the
        settings screen was laid out over: on a 900 px Mac display the menu bar
        takes 28 of them, so a window asking for 900 gets 872 and lays out 916 px
        of column inside it. The window manager knows the number; ask it.
        """
        wm_h = self.wm_maxsize()[1]
        screen_h = self.winfo_screenheight()
        return wm_h if 0 < wm_h <= screen_h else screen_h

    def _maximize(self):
        sw, sh = self.winfo_screenwidth(), self._usable_height()
        if self.tk.call("tk", "windowingsystem") == "aqua":
            # Fill the whole usable screen from the top-left. macOS keeps the
            # title bar reachable; with the menu bar hidden this uses every pixel
            # — asking for more only got the bottom of the window cut off.
            self.geometry(f"{sw}x{sh}+0+0")
        else:
            try:
                self.state("zoomed")  # Windows / most Linux WMs
            except tk.TclError:
                self.geometry(f"{sw}x{sh}+0+0")  # fallback: size to the screen

        self._make_fonts()

        # session state
        self.mode = "addition"
        self.n_cards = DEFAULT_CARDS
        # numbers the operations are played with, restored from the last run
        self.allowed = load_allowed()
        self.deck: list[Card] = []
        self.results: list[Card] = []  # completed cards, in play order
        self.retry_queue: list[Card] = []
        self.retry_seen: set[tuple] = set()  # operations already queued for retry
        self.idx = 0
        self.current: Card | None = None
        # None while the question is being read aloud: the card's clock starts
        # when the reading ends, so what it measures is the child, not the app.
        self.card_start: float | None = None
        self.game_start = 0.0
        self._read_time = 0.0  # seconds this game has spent reading questions out
        self._speech_start: float | None = None  # when the reading on air began
        self._speech_id = 0  # which reading that is; see _speak_operation()
        self._tick_job = None
        self._advance_job = None  # the single pending "move to the next card" timer
        self._voice_job = None  # the watchdog that keeps this card's mic armed
        self._card_done = False  # answer committed: ignore anything else on this card
        self._fanfare_job = None  # the recap's pending "start the music" timer
        self._pump_job = None  # the worker-thread -> Tk callback pump
        self._stopping = False  # latched on quit: schedule no more timers
        self._voice_mic_bad = False
        self._pill_size: tuple[int, int] | None = None  # cached question-pill size

        # sound effects (a no-op if no command-line player is installed)
        self.sfx = sfx.Sound(SOUNDS_DIR)

        # voice engines (both degrade gracefully to no-ops)
        self.tts = voicemod.TTS(LANG) if voicemod else None
        self.stt = voicemod.SpeechInput(LANG) if voicemod else None
        stt_ok = bool(self.stt and self.stt.available())
        self.voice_enabled = tk.BooleanVar(value=stt_ok)
        # Dictation: the question read aloud before the child answers it. Off,
        # the card is only *shown* — and then nothing stands between showing it
        # and playing it, so the clock and the microphone both start at once.
        self.dictation_enabled = tk.BooleanVar(value=load_dictation())

        # thread-safe channel: worker threads (STT/TTS) post callables here and
        # the pump runs them on the Tk main loop — never touch Tk off-thread.
        self._ui_queue: queue.Queue = queue.Queue()
        self._pump()

        self._build_topbar()  # the always-visible language chip
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        self._install_exit_handlers()
        self.show_settings()
        # ...and once the menu is up, listen to the room. Deliberately after the
        # first screen is drawn: it must never be something the child waits for.
        self.after(700, self._calibrate_mic)

    def _calibrate_mic(self):
        """Measure the noise around the microphone, silently, in the background.

        The gates that decide "that was an answer" are measured against the room,
        not against a constant, because the room is not a constant: a bedroom at
        night, a kitchen at breakfast and a terrace by a road are three different
        microphones as far as the level of a spoken "huit" is concerned. This
        opens the mic for a couple of seconds, measures what silence sounds like
        here and now, and closes it again — no dialog, no message, nothing on
        screen. The input gain is raised first, or the room would be measured at
        a gain the game will never use and the numbers would not transfer.
        """
        if self._stopping or not self.stt or not self.voice_enabled.get():
            return
        self.stt.boost_input_volume()
        self.stt.calibrate()

    # ------------------------------------------------------------------ #
    # Clean shutdown — restore the mic gain on EVERY exit path
    # ------------------------------------------------------------------ #
    def _install_exit_handlers(self):
        self.protocol("WM_DELETE_WINDOW", self._on_quit)  # window close box
        with contextlib.suppress(tk.TclError):  # absent off macOS
            self.createcommand("tk::mac::Quit", self._on_quit)
        self.bind_all("<Command-q>", lambda e: self._on_quit())
        self.bind_all("<Control-c>", lambda e: self._on_quit())
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            # ValueError off the main thread, OSError where the signal is absent.
            with contextlib.suppress(OSError, ValueError):
                signal.signal(sig, self._on_signal)  # terminal kill / close

    def _shutdown(self):
        # Timers first: a pending callback that runs after the window is gone
        # raises out of Tcl ("invalid command name ..._pump") with nowhere to
        # report it.
        self._stopping = True
        for job in (
            "_pump_job",
            "_tick_job",
            "_advance_job",
            "_voice_job",
            "_fanfare_job",
        ):
            pending = getattr(self, job, None)
            if pending is not None:
                with contextlib.suppress(Exception):
                    self.after_cancel(pending)
                setattr(self, job, None)
        # The player is a child process: quitting through os._exit() leaves it
        # orphaned, still playing to an empty screen, unless it is killed here.
        self.sfx.stop()
        if self.stt:
            self.stt.stop()  # close mic stream (mic light off)
            self.stt.restore_input_volume(50)  # idempotent

    def _on_quit(self):
        self._shutdown()
        with contextlib.suppress(Exception):
            self.destroy()
        # Force the process to die so the OS reclaims the mic no matter what
        # (an open PortAudio stream can otherwise stall interpreter shutdown).
        os._exit(0)

    def _on_signal(self, _signum, _frame):
        self._shutdown()
        os._exit(0)

    def _pump(self):
        if self._stopping:
            return  # the window is going away; stop rescheduling
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:  # noqa: BLE001 - one bad callback must not stop the pump
                traceback.print_exc()
        self._pump_job = self.after(40, self._pump)

    def _post(self, fn):
        """Schedule fn() to run on the Tk thread (safe to call from any thread)."""
        self._ui_queue.put(fn)

    # -- fonts ------------------------------------------------------------- #
    def _make_fonts(self):
        # Everything scales with the display so the settings column still fits
        # (and stays readable) on a short laptop display. Measured against the
        # height a window may actually have, which is the constraint the column
        # is up against — the screen's own height is 28 px of menu bar too
        # generous, and that is a row of the settings screen.
        s = min(1.0, self._usable_height() / 900)

        def scaled(size, weight="normal"):
            return tkfont.Font(
                family="Helvetica", size=max(11, round(size * s)), weight=weight
            )

        self.f_giant = scaled(120, "bold")
        self.f_huge = scaled(90, "bold")
        self.f_big = scaled(48, "bold")
        self.f_mode = scaled(30, "bold")
        self.f_title = scaled(28, "bold")
        self.f_body = scaled(20)
        self.f_small = scaled(15)
        self.f_tile = scaled(24, "bold")
        self.f_chip = scaled(14, "bold")
        self.f_num = scaled(20, "bold")
        self.f_hint = scaled(14)

    # -- frame helper ------------------------------------------------------ #
    def _hush(self):
        """Silence the game — the sound playing, and the one about to.

        Called when a screen is left rather than from `_clear()`: the answer
        sounds are *meant* to ring out over the card that follows, and `_clear()`
        runs between two cards.
        """
        if self._fanfare_job is not None:
            self.after_cancel(self._fanfare_job)
            self._fanfare_job = None
        self.sfx.stop()

    def _clear(self):
        if self._tick_job:
            self.after_cancel(self._tick_job)
            self._tick_job = None
        if self._voice_job:
            self.after_cancel(self._voice_job)
            self._voice_job = None
        if self.stt:
            self.stt.cancel()
        if self.tts:
            self.tts.stop()
            self._close_reading()  # a sentence cut off still took the time it took
        for w in self.container.winfo_children():
            w.destroy()
        self._hide_topbar()  # off by default: only the menu asks for it back

    # ------------------------------------------------------------------ #
    # Screen 1: settings
    # ------------------------------------------------------------------ #
    def show_settings(self):
        self._clear()
        self._hush()  # leaving the recap: the fanfare goes with it
        self._show_topbar()
        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        # The title is a fixed header, not part of the scrolling column: it is
        # taller than any single row, and half a drawing clipped at the canvas
        # edge as it scrolls away reads as a rendering fault rather than motion.
        self._build_title(f)

        # The settings column scrolls when the window is too short to hold it.
        # A packed column taller than its window is not shrunk, it is cut off —
        # and what goes is whatever is packed last, the row of voice/dictation
        # toggles. On a short display the canvas lets the whole column be
        # reached (by wheel, trackpad or the scrollbar) instead of losing it.
        canvas = tk.Canvas(f, bg=BG, highlightthickness=0, bd=0, yscrollincrement=1)
        scrollbar = tk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bind_wheel(canvas, lambda px: canvas.yview_scroll(px, "units"))

        # The canvas window is a holder, not the column itself: a widget cannot
        # be both a canvas item and managed by pack (which is where the column's
        # padding comes from), so the column lives packed inside the holder and
        # the holder is the one placed on the canvas.
        holder = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=holder, anchor="nw")

        size = [(0, 0, 0)]
        shown = [False]

        def fit_scrolled(_event=None):
            """Size the scrolled window to the content (or the view) and show
            the scrollbar only when there is something to scroll. The holder is
            held to the view's height when the content fits, so the column's
            `expand` still centers it; taller, it takes its natural height and
            scrolls. Horizontally it is the page box: no wider than CONTENT_MAX,
            centred, and shrunk to the view when the window is narrower."""
            if not canvas.winfo_exists():
                return
            cw, ch = canvas.winfo_width(), canvas.winfo_height()
            box = min(cw, CONTENT_MAX)
            h = max(holder.winfo_reqheight(), ch)
            if size[0] != (cw, h, box):
                size[0] = (cw, h, box)
                canvas.itemconfigure(window, width=box, height=h)
                canvas.coords(window, (cw - box) // 2, 0)
                canvas.configure(scrollregion=(0, 0, cw, h))
            # Decide from the measured heights, not from `canvas.yview()`: the
            # view fraction is only recomputed once Tk processes the geometry
            # set just above, so reading it here returns the previous state and
            # the bar would never appear the first time it is needed.
            need_bar = h > ch
            if need_bar != shown[0]:
                shown[0] = need_bar
                if need_bar:
                    scrollbar.pack(side="right", fill="y")
                else:
                    scrollbar.pack_forget()
                    canvas.yview_moveto(0)
            scrollbar.set(*canvas.yview())

        canvas.bind("<Configure>", fit_scrolled)
        holder.bind("<Configure>", fit_scrolled)

        # One packed column, top to bottom. Never .place() the content here: a
        # placed frame is not part of the packing flow, so it silently lands on
        # top of whatever comes after it (that's how the voice toggle ended up
        # covering the operation tiles).
        col = tk.Frame(holder, bg=BG)
        col.pack(fill="x", expand=True, padx=24, pady=16)

        # -- how many cards (one compact toolbar row) ----------------------- #
        self.count_var = tk.IntVar(value=self.n_cards)
        panel = RoundedPanel(col)
        panel.canvas.pack(fill="x", pady=(14, 18))
        bar = tk.Frame(panel.inner, bg=SURFACE)
        bar.pack(pady=(4, 2))
        stepper: list[tk.Widget] = [
            tk.Label(bar, text=T("how_many"), font=self.f_body, fg=MUTED, bg=SURFACE)
        ]

        def bump(d):
            self.count_var.set(max(1, min(99, self.count_var.get() + d)))

        stepper.append(
            Btn(
                bar,
                "−",
                command=lambda: bump(-1),
                font=self.f_mode,
                bg=CARD_BG,
                fg=TEXT,
                width=2,
                pady=0,
            )
        )
        stepper.append(
            tk.Label(
                bar,
                textvariable=self.count_var,
                font=self.f_mode,
                fg=ACCENT,
                bg=SURFACE,
                width=3,
            )
        )
        stepper.append(
            Btn(
                bar,
                "+",
                command=lambda: bump(1),
                font=self.f_mode,
                bg=CARD_BG,
                fg=TEXT,
                width=2,
                pady=0,
            )
        )
        stepper[0].pack(side="left", padx=(0, 18))
        for w in stepper[1:]:
            w.pack(side="left", padx=6)

        # quick picks double as a segmented control: the live value stays lit
        chips: dict[int, Btn] = {}
        for q in (5, 10, 20, 30, 50):
            chips[q] = Btn(
                bar,
                str(q),
                command=lambda v=q: self.count_var.set(v),
                font=self.f_chip,
                width=3,
                padx=10,
                pady=6,
                active_bg=ACCENT,
                active_fg=INK,
            )
            chips[q].pack(side="left", padx=(18 if q == 5 else 5, 0))

        def lit(*_):
            v = self.count_var.get()
            for q, c in chips.items():
                c.set_colors(*((ACCENT, INK) if q == v else (CARD_BG, MUTED)))

        self.count_var.trace_add("write", lit)
        lit()
        self._wrap_count(bar, stepper, list(chips.values()))

        # -- which numbers (step 1) ---------------------------------------- #
        self._build_number_row(col)

        # -- which operation (step 2, and it starts the game) --------------- #
        tk.Label(col, text=T("choose_mode"), font=self.f_title, fg=TEXT, bg=BG).pack(
            pady=(4, 14)
        )

        grid = tk.Frame(col, bg=BG)
        grid.pack(fill="x")
        for c in (0, 1):  # keep both tiles the same width
            grid.grid_columnconfigure(c, uniform="ops", weight=1)
        for i, m in enumerate(MODES):
            Btn(
                grid,
                f"{T(m)}  {OP_SYMBOL[m]}",
                command=lambda mm=m: self.start_game(mm),
                font=self.f_tile,
                bg=MODE_COLORS[m],
                fg=INK,
                active_bg=TILE_HOVER_BG,
                active_fg=TILE_HOVER_FG,
                padx=20,
                pady=10,
            ).grid(row=i // 2, column=i % 2, padx=10, pady=10, sticky="nsew")

        # The footer holds the in-game switches only. Language and progress live
        # in the top bar, where they are reachable from every screen and shown
        # once: a flag for the language, a chart for the stats.
        footer = tk.Frame(col, bg=BG)
        footer.pack(pady=(18, 4))
        self._build_voice_toggle(footer)
        self._build_dictation_toggle(footer)
        self._build_girly_toggle(footer)
        self._wrap_footer(footer)
        self._fit_column(col)
        # Tk delivers a wheel event to the widget under the pointer and its own
        # bindtags only — it never walks up to a parent — so binding the canvas
        # alone would scroll nothing unless the pointer is on bare canvas. Every
        # descendant is bound too, so a wheel anywhere over the column scrolls it.
        self._bind_settings_wheel(col, canvas)
        fit_scrolled()  # measure now, not only on the first <Configure>

    def _bind_settings_wheel(self, widget, canvas) -> None:
        """Route every wheel gesture over the settings column to the canvas."""
        bind_wheel(widget, lambda px: canvas.yview_scroll(px, "units"))
        for child in widget.winfo_children():
            self._bind_settings_wheel(child, canvas)

    def _fit_column(self, col) -> None:
        """Give up the gaps between the panels rather than the bottom of the column.

        A packed column taller than its window is not shrunk to fit, it is cut
        off — and what goes is whatever is packed last, which here is the row of
        toggles. Nothing marks the loss: on a 900 px display the voice and
        dictation switches were simply not on the screen, on exactly the size of
        laptop most likely to be running this.

        How tall the column comes out cannot be predicted — the labels are
        translated, the fonts are scaled to the display, and the footer may have
        folded onto a second row — so it is measured, and the whitespace between
        the panels is handed back a step at a time until the whole of it fits.
        The panels themselves are never touched; it is the air between them that
        goes, and the screen is merely tighter rather than missing a row.
        """
        self.update_idletasks()
        avail = self.winfo_height()
        if avail <= 1:  # not configured yet (first build, before the window maps)
            avail = self._usable_height()
        gaps = [(w, w.pack_info()["pady"]) for w in col.pack_slaves()]
        gaps.append((col, col.pack_info()["pady"]))

        def needed():
            own = col.pack_info()["pady"]
            return col.winfo_reqheight() + sum(
                own if isinstance(own, tuple) else (own, own)
            )

        for k in (0.6, 0.35, 0.15, 0.0):
            if needed() <= avail:
                return
            for w, pady in gaps:
                w.pack_configure(
                    pady=tuple(round(v * k) for v in pady)
                    if isinstance(pady, tuple)
                    else round(pady * k)
                )
            self.update_idletasks()

    def _wrap_footer(self, footer) -> None:
        """Fold the footer onto a second row when one row is wider than the screen.

        A packed row that does not fit is not shrunk, it is cut off at both ends
        — the outermost options simply stop being there, with nothing to say they
        ever were. How wide the row comes out cannot be predicted: the labels are
        translated and the fonts are scaled to the display, so it is measured and
        folded only when it has to be.
        """
        buttons = footer.winfo_children()
        footer.update_idletasks()
        if len(buttons) < 2 or footer.winfo_reqwidth() <= self._page_room():
            return
        # Pack and grid cannot share a parent, so the row is unpacked first and
        # every button re-placed; both geometry managers are the frame's own.
        per_row = (len(buttons) + 1) // 2
        for b in buttons:  # every one of them, before the first grid() call
            b.pack_forget()
        for i, b in enumerate(buttons):
            b.grid(row=i // per_row, column=i % per_row, padx=8, pady=4, sticky="ew")

    def _page_room(self) -> int:
        """Usable width of the page box, less the column's own padding."""
        return max(720, min(self.winfo_width(), CONTENT_MAX)) - 96

    def _wrap_count(self, bar, stepper, chips) -> None:
        """Drop the quick picks under the stepper when the row is too wide.

        The stepper and the five chips are one row on a roomy screen; on a
        narrow one the picks fold onto a second line inside the panel rather
        than running off the edge — the same choice the footer makes, and the
        reason the count panel keeps fitting when everything else does.
        """
        bar.update_idletasks()
        if bar.winfo_reqwidth() <= self._page_room():
            return
        for w in (*stepper, *chips):
            w.pack_forget()
        for c, w in enumerate(stepper):
            w.grid(row=0, column=c, padx=6, pady=4)
        for c, w in enumerate(chips):
            w.grid(row=1, column=c, padx=6, pady=4)

    def _build_title(self, parent):
        """'<drawing> Almathus' — Mario normally, Peach in girly mode."""
        row = tk.Frame(parent, bg=BG)
        row.pack()
        icon = self._title_image()
        if icon is not None:
            # Tk keeps no reference of its own to an image: drop ours and the
            # label goes blank the moment the garbage collector runs.
            self._title_icon = icon
            tk.Label(row, image=icon, bg=BG).pack(side="left", padx=(0, 16))
        else:
            tk.Label(row, text=TITLE_ICON, font=self.f_big, fg=ACCENT, bg=BG).pack(
                side="left", padx=(0, 12)
            )
        tk.Label(row, text="Almathus", font=self.f_big, fg=ACCENT, bg=BG).pack(
            side="left"
        )

    def _title_image(self):
        """This theme's drawing, rendered to about two lines of title text.

        Tk 9 rasterizes SVG itself and scales to a target height on the way in,
        so the drawing stays crisp on any screen — that is the whole reason the
        assets are SVG and not PNG (Tk's bitmap scaler only does integer steps).

        Returns None if the file is missing or cannot be rendered; the caller
        falls back to the emoji, so a bad asset never breaks the screen."""
        try:
            return tk.PhotoImage(
                file=str(ASSETS_DIR / TITLE_SVG),
                format=f"svg -scaletoheight {self.f_big.metrics('linespace') * 2}",
            )
        except tk.TclError:
            return None

    def _build_number_row(self, parent):
        """Row of [1]..[10] toggles: the numbers every operation is played with."""
        panel = RoundedPanel(parent)
        panel.canvas.pack(fill="x", pady=(0, 22))
        body = panel.inner
        tk.Label(
            body, text=T("choose_numbers"), font=self.f_body, fg=MUTED, bg=SURFACE
        ).pack(pady=(4, 2))

        # The row hugs its chips instead of stretching: slack is what Tk hands
        # out a pixel at a time, and that is what made the chips uneven.
        strip = tk.Frame(body, bg=SURFACE)
        strip.pack(pady=(2, 4))
        boxes: dict[int, Btn] = {}

        def paint(n: int):
            on = n in self.allowed
            boxes[n].set_colors(
                NUM_ON_BG if on else NUM_OFF_BG, NUM_ON_FG if on else NUM_OFF_FG
            )

        def toggle(n: int):
            if n in self.allowed:
                if len(self.allowed) == 1:
                    return  # always keep at least one number to play with
                self.allowed.discard(n)
            else:
                self.allowed.add(n)
            paint(n)
            # write on every tap: quitting goes through os._exit(), so there is
            # no exit hook late enough to be trusted with this
            save_allowed(self.allowed)

        def command_for(n: int):
            return lambda: toggle(n)

        for n in range(NUM_MIN, NUM_MAX + 1):
            # width in characters, identical for every chip, so "10" does not
            # request more room than "1" and the uniform columns can all match
            lbl = Btn(
                strip,
                str(n),
                command=command_for(n),
                font=self.f_num,
                width=2,
                padx=10,
                pady=8,
                radius=8,
                active_bg=NUM_HOVER_BG,
                active_fg=TEXT,
            )
            lbl.grid(
                row=0,
                column=n - NUM_MIN,
                pady=8,
                padx=(8 if n == NUM_MIN else 4, 8 if n == NUM_MAX else 4),
            )
            boxes[n] = lbl
            paint(n)

        tk.Label(
            body, text=T("numbers_hint"), font=self.f_hint, fg=MUTED, bg=SURFACE
        ).pack(pady=(2, 6))

    def _show_topbar(self) -> None:
        """Put the bar back on the menu, where the two choices are asked for.

        It is deliberately *not* shown during play: a chart button and a flag
        sitting over the question are things to tap instead of answering. The
        menu is the one screen that wants them, and `_clear` takes the bar away
        again the moment a game begins.
        """
        if not self.topbar.winfo_manager():
            self.topbar.pack(side="top", fill="x", before=self.container)

    def _hide_topbar(self) -> None:
        if self.topbar.winfo_manager():
            self.topbar.pack_forget()

    def _build_topbar(self) -> None:
        """The menu's slim bar: stats on the left, language on the right.

        Named by an icon so a child reads them at a glance: a chart for the
        stats, the country's own flag for the language. The two buttons are
        placed rather than packed so they can sit on the *page box* the menu
        below uses — flush with its edges, not the window's. That is safe here
        because the bar is its own frame with nothing but these two buttons:
        they cannot float over the screen the way a placed settings tile did.
        """
        self.topbar = tk.Frame(self, bg=BG)
        self.topbar.pack(side="top", fill="x")
        self.topbar.bind("<Configure>", self._place_topbar)
        self._populate_topbar()

    def _populate_topbar(self) -> None:
        """(Re)fill the bar in the current palette.

        The bar is built once, at startup, but the theme can be flipped from the
        menu long after — so its colours are baked in by rebuilding it on every
        flip rather than left as the palette that happened to be loaded first.
        """
        for w in self.topbar.winfo_children():
            w.destroy()
        self.topbar.config(bg=BG)
        self._progress_btn = Btn(
            self.topbar,
            T("progress"),
            command=self.show_progress,
            font=self.f_body,
            bg=SURFACE,
            fg=TEXT,
            padx=14,
            pady=6,
        )
        self._chip_image: tk.PhotoImage | None = None
        self._lang_btn = Btn(
            self.topbar,
            "",
            command=self._open_language_picker,
            font=self.f_body,
            bg=SURFACE,
            fg=TEXT,
            padx=12,
            pady=6,
            compound="left",
        )
        self._refresh_lang_chip()
        self._place_topbar()

    def _place_topbar(self, _event=None) -> None:
        """Put the two buttons on the page box, aligned with the menu below."""
        btn = getattr(self, "_progress_btn", None)
        if btn is None or not btn.winfo_exists():
            return
        self.topbar.update_idletasks()
        w = self.topbar.winfo_width()
        if w <= 1:  # not mapped yet (or hidden); re-placed on <Configure>
            return
        box = min(w, CONTENT_MAX)
        left = (w - box) // 2
        # 24 is the column's own padding, so the buttons line up with the panel
        # edges rather than with the box, which is a hair wider.
        inset = 24
        pad = 8
        height = (
            max(
                self._progress_btn.winfo_reqheight(),
                self._lang_btn.winfo_reqheight(),
            )
            + 2 * pad
        )
        if self.topbar.winfo_height() != height:
            self.topbar.configure(height=height)
        self._progress_btn.place(x=left + inset, y=pad)
        self._lang_btn.place(
            x=left + box - inset - self._lang_btn.winfo_reqwidth(), y=pad
        )

    def _refresh_lang_chip(self) -> None:
        """Redraw the chip as flag + language name for the current language."""
        lang = languages.BY_CODE.get(LANG)
        name = lang.endonym if lang is not None else LANG
        self._chip_image = flag_image(LANG, 18)
        self._lang_btn.config(image=self._chip_image, text=name)
        self._place_topbar()

    def _open_language_picker(self) -> None:
        code = LanguagePicker(
            self,
            T("language_title"),
            T("language_intro"),
            T("language_skip"),
            current=LANG,
        ).show()
        if code and code != LANG:
            self._apply_language(code)

    def _apply_language(self, code: str) -> None:
        """Switch the running app to another language.

        LANG is read at call time by every string and table, so a switch is
        mostly rebuilding the voice engines and redrawing the screen — no restart
        needed. The old mic is stopped and its input gain restored first.
        """
        global LANG  # noqa: PLW0603 - the one app-wide setting, switched here
        LANG = code
        save_language(code)
        self.title(T("title"))
        if self.stt:
            self.stt.stop()
            self.stt.restore_input_volume(50)
        self.tts = voicemod.TTS(LANG) if voicemod else None
        self.stt = voicemod.SpeechInput(LANG) if voicemod else None
        self.voice_enabled.set(bool(self.stt and self.stt.available()))
        self._refresh_lang_chip()
        self.show_settings()
        if self.voice_enabled.get():
            self.after(700, self._calibrate_mic)

    def _build_voice_toggle(self, parent):
        # Only offer the toggle when voice input actually works; otherwise show
        # nothing (a pip/dependency string is not something a kid should see).
        if not (self.stt and self.stt.available()):
            return

        btn = Btn(parent, "", font=self.f_body, padx=22, pady=10)

        def refresh():
            on = self.voice_enabled.get()
            btn.config(text=f"{T('voice_toggle')} : {'ON ✅' if on else 'OFF'}")
            btn.set_colors(GOOD if on else CARD_BG, INK if on else MUTED)

        def toggle_voice() -> None:
            self.voice_enabled.set(not self.voice_enabled.get())
            refresh()

        btn._command = toggle_voice
        btn.pack(side="left", padx=8)
        refresh()

    def _build_dictation_toggle(self, parent):
        """Read the question aloud, or only show it.

        Hidden when there is no speech synthesis to switch off — the game then
        already plays the way this toggle's OFF position describes.
        """
        if not (self.tts and self.tts.available()):
            return

        btn = Btn(parent, "", font=self.f_body, padx=22, pady=10)

        def refresh():
            on = self.dictation_enabled.get()
            btn.config(text=f"{T('dictation_toggle')} : {'ON ✅' if on else 'OFF'}")
            btn.set_colors(GOOD if on else CARD_BG, INK if on else MUTED)

        def toggle_dictation() -> None:
            self.dictation_enabled.set(not self.dictation_enabled.get())
            # written on every tap: quitting goes through os._exit(), so there is
            # no exit hook late enough to be trusted with this
            write_prefs(dictation=self.dictation_enabled.get())
            refresh()

        btn._command = toggle_dictation
        btn.pack(side="left", padx=8)
        refresh()

    def _build_girly_toggle(self, parent):
        on = THEME == "girly"
        btn = Btn(
            parent,
            f"{T('girly_toggle')} : {'ON 👑' if on else 'OFF'}",
            font=self.f_body,
            padx=22,
            pady=10,
            bg=ACCENT if on else CARD_BG,
            fg=INK if on else MUTED,
        )

        def flip():
            apply_theme("default" if THEME == "girly" else "girly")
            write_prefs(theme=THEME)
            # The window, its container and the top bar outlive every screen, so
            # they are repainted by hand; everything else is rebuilt from the new
            # palette, which the bar's own buttons have to be too.
            self.configure(bg=BG)
            self.container.config(bg=BG)
            self._populate_topbar()
            self.show_settings()

        btn._command = flip
        btn.pack(side="left", padx=8)

    # ------------------------------------------------------------------ #
    # Screen 2: countdown
    # ------------------------------------------------------------------ #
    def start_game(self, mode: str):
        self.mode = mode
        self.n_cards = self.count_var.get()
        # Yesterday's hard cards, dealt again before they are forgotten.
        revision = carry_over(resultslog.load_sessions(), mode, self.allowed)
        self.deck = make_deck(mode, self.n_cards, self.allowed, revision)
        self.results = []
        self.retry_queue = []
        self.retry_seen = set()
        self.idx = 0
        # No card is on screen yet, so the countdown's move to the first one is
        # not "leaving an unanswered card" — see _advance().
        self.current = None
        self._card_done = False
        self._read_time = 0.0
        self._speech_start = None
        self._speech_id += 1  # nothing said for the last game may report now
        # preload the STT model + raise mic gain so the first spoken answer is instant
        if self.voice_enabled.get() and self.stt and self.stt.available():
            self.stt.warmup()
            self.stt.boost_input_volume()
            # The app may have been open since this morning, and the room is not
            # what it was then. Re-measured here, it lands well inside the
            # countdown; the room keeps being tracked between answers after that.
            self.stt.calibrate()
            # Answering by voice: remind the child to speak clearly before the
            # countdown, while the mic is already warming up in the background.
            self.show_articulation_tip()
        else:
            self._begin_countdown()

    def show_articulation_tip(self) -> None:
        """One screen telling the child to speak clearly, before a voiced game.

        Shown only when answers are captured by voice; the countdown starts when
        they tap GO, so the reminder is read at the child's own pace.
        """
        self._clear()
        f = self.tip_frame = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)
        tk.Label(
            f, text=T("articulate_tip_title"), font=self.f_title, fg=ACCENT, bg=BG
        ).pack(pady=(70, 14))
        # The "wait for GO" line is only true when the card is read aloud; with
        # dictation off there is no per-question cue to wait for.
        tip = T("articulate_tip")
        if self.dictation_enabled.get() and self.tts and self.tts.available():
            tip = f"{tip} {T('articulate_tip_go')}"
        tk.Label(
            f,
            text=tip,
            font=self.f_body,
            fg=TEXT,
            bg=BG,
            wraplength=560,
            justify="center",
        ).pack(padx=40)
        Btn(
            f,
            T("go"),
            command=self._begin_countdown,
            font=self.f_mode,
            bg=ACCENT,
            fg=INK,
            padx=30,
            pady=14,
        ).pack(pady=38)

    def _begin_countdown(self) -> None:
        # Both the count and the sample have a run-up to their GO! — three ticks
        # on screen, COUNTDOWN_GO_AT in the file — and lining the two up is a
        # matter of making the shorter one wait for the longer. This sample opens
        # with silence before its first beep, so here it is the picture that
        # waits; a sample that starts on its first beep would flip that, and
        # neither case needs its own branch.
        lead = COUNTDOWN_STEPS * COUNTDOWN_TICK - COUNTDOWN_GO_AT
        # The settings screen must not sit there through the wait, whichever way
        # it falls: the game has begun, the count just has not caught up yet.
        self._clear()
        self.after(max(0, lead), lambda: self.sfx.play("countdown"))
        self.after(max(0, -lead), lambda: self.countdown(COUNTDOWN_STEPS))

    def countdown(self, n: int):
        self._clear()
        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)
        tk.Label(
            f,
            text=str(n) if n > 0 else T("go"),
            font=self.f_giant,
            fg=ACCENT if n > 0 else GOOD,
            bg=BG,
        ).place(relx=0.5, rely=0.45, anchor="center")
        if n > 0:
            self.after(COUNTDOWN_TICK, lambda: self.countdown(n - 1))
        else:
            self.game_start = time.perf_counter()
            # GO! is held for exactly as long as the tone rings, so the word and
            # the sound end together and the first question is read aloud into
            # silence rather than over a tail. The stop() is then a no-op on this
            # sample and a safety net on a longer one. The answer sounds are
            # deliberately *not* cut this way: they are shorter than the pause
            # that follows them, so they ring out over the top of the next card,
            # which is the point of them.
            self.after(COUNTDOWN_GO_LEN, self.sfx.stop)
            self._advance_after(COUNTDOWN_GO_LEN)

    # ------------------------------------------------------------------ #
    # Screen 3: play
    # ------------------------------------------------------------------ #
    def _advance_after(self, ms: int):
        """Schedule the move to the next card, replacing any pending move.

        Exactly one advance can ever be in flight: a late voice result landing
        during the feedback flash used to queue a second one, which then popped
        two cards off the retry queue (one was never asked).
        """
        if self._advance_job is not None:
            self.after_cancel(self._advance_job)
        self._advance_job = self.after(ms, self._advance)

    def _advance(self):
        self._advance_job = None
        # A card is left ONLY once an answer has been committed to it. Nothing
        # else — no timer, no stray callback, no late voice result — can take a
        # question off the screen: a child who needs a minute to think gets it.
        if self.current is not None and not self._card_done:
            return
        self.next_card()

    def next_card(self):
        if self._advance_job is not None:  # any other pending move is stale
            self.after_cancel(self._advance_job)
            self._advance_job = None
        # main deck first, then the reinforcement queue
        if self.idx < len(self.deck):
            self.current = self.deck[self.idx]
            in_retry = False
        elif self.retry_queue:
            self.current = self.retry_queue.pop(0)
            self.current.is_retry = True
            in_retry = True
        else:
            self.show_recap()
            return
        self.render_card(in_retry)

    def render_card(self, in_retry: bool) -> None:
        card = self.current
        if card is None:  # next_card() always sets one before calling here
            return
        self._clear()
        self._card_done = False
        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        voice_on = bool(self.voice_enabled.get() and self.stt and self.stt.available())
        dictation_on = bool(
            self.dictation_enabled.get() and self.tts and self.tts.available()
        )

        # header (pinned top)
        header = (
            T("retry_round")
            if in_retry
            else T("card_of", n=min(self.idx + 1, self.n_cards), total=self.n_cards)
        )
        tk.Label(
            f, text=header, font=self.f_title, fg=ACCENT if in_retry else MUTED, bg=BG
        ).pack(side="top", pady=(20, 0))

        # The child answers too early, while the question is still being read.
        # This cue sits above the question and says what to do: wait for the end
        # of the question, then a green GO! once the mic opens. Shown only when
        # the question is read aloud into a live mic; without dictation there is
        # nothing to wait for.
        self.card_hint = None
        if voice_on and dictation_on:
            self.card_hint = tk.Label(
                f, text=T("wait_answer"), font=self.f_small, fg=MUTED, bg=BG
            )
            self.card_hint.pack(side="top", pady=(6, 0))

        # timers (pinned bottom, always fully visible)
        bar = tk.Frame(f, bg=BG)
        bar.pack(side="bottom", fill="x", padx=40, pady=(0, 20))
        self.card_time_lbl = tk.Label(bar, text="", font=self.f_body, fg=MUTED, bg=BG)
        self.card_time_lbl.pack(side="left")
        self.total_time_lbl = tk.Label(
            bar, text="", font=self.f_title, fg=ACCENT, bg=BG
        )
        self.total_time_lbl.pack(side="right")

        # body fills the space between header and timers; weighted spacer rows
        # spread the content across the full height (fills the screen, never overlaps)
        body = tk.Frame(f, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)  # top spacer
        body.rowconfigure(4, weight=1)  # bottom spacer (equal -> group centered)

        # The question pill, fixed at the width of the widest question so it does
        # not retract when it flashes the shorter GO!. It carries that cue: when
        # the reading ends it shows GO! in place and then the question again, so
        # the child's eyes — already on the card — see the moment to answer.
        width, height = self._pill_px()
        pill = tk.Canvas(
            body, width=width, height=height, bg=BG, highlightthickness=0, bd=0
        )
        pill.grid(row=1, column=0, pady=(0, 8))
        # Rounded like the buttons; the rule itself stays a plain sized canvas so
        # the width is fixed and the shorter GO! cannot make the pill retract.
        self._pill_img = rounded_photo(
            width, height, min(BTN_RADIUS * 2, height // 2), CARD_BG
        )
        pill.create_image(0, 0, anchor="nw", image=self._pill_img)
        self.question_lbl = tk.Label(
            pill, text=card.text, font=self.f_huge, fg=TEXT, bg=CARD_BG
        )
        pill.create_window(width // 2, height // 2, window=self.question_lbl)

        inputs = tk.Frame(body, bg=BG)
        inputs.grid(row=2, column=0, pady=(0, 8))
        self.answer_var = tk.StringVar()
        # The entry sits on a rounded raster: a plain Entry is a square box, and
        # the one thing a child aims at on this screen should not be the only
        # sharp-cornered thing on it.
        entry_box = tk.Canvas(inputs, bg=BG, highlightthickness=0, bd=0)
        entry = tk.Entry(
            entry_box,
            textvariable=self.answer_var,
            font=self.f_big,
            justify="center",
            width=6,
            bd=0,
            highlightthickness=0,
            bg=ENTRY_BG,
            fg=ACCENT,
            insertbackground=ACCENT,
        )
        entry.update_idletasks()
        ew = entry.winfo_reqwidth() + 52
        eh = entry.winfo_reqheight() + 20
        entry_box.configure(width=ew, height=eh)
        self._entry_img = rounded_photo(ew, eh, 18, ENTRY_BG, BORDER, 1)
        entry_box.create_image(0, 0, anchor="nw", image=self._entry_img)
        entry_box.create_window(ew // 2, eh // 2, window=entry)
        entry_box.pack(pady=(2, 2))
        entry.focus_set()
        entry.bind("<Return>", lambda e: self.submit())
        self.feedback = tk.Label(inputs, text="", font=self.f_body, fg=GOOD, bg=BG)
        self.feedback.pack(pady=1)
        # Voice input runs continuously in parallel with the keypad — no button.
        # A small status line shows listening / a mic-permission hint if needed.
        self.voice_lbl = None
        self._voice_mic_bad = False
        if self.stt and self.stt.available():
            self.voice_lbl = tk.Label(
                inputs, text="", font=self.f_small, fg=MUTED, bg=BG
            )
            self.voice_lbl.pack(pady=1)

        # On-screen keypad (kid-friendly; works with touch/mouse too). It is built
        # either way but stays hidden while answering by voice: a grid of digits
        # under the question only distracts a child who is meant to speak. It is
        # revealed again the moment the mic turns out to be unusable.
        self.keypad_frame = tk.Frame(body, bg=BG)
        self._build_keypad(self.keypad_frame)
        if not voice_on:
            self._show_keypad()

        # The clock starts with the card, dictation or not: what is being timed
        # is the whole moment the child is given the question, and with the
        # question read aloud that moment opens with the reading. Answering
        # before the sentence is finished is allowed, and the times of two
        # sessions played the same way stay comparable, which is the only thing
        # the progress screen ever asks of them.
        self.card_start = time.perf_counter()
        self._tick()

        # Dictation on: read the operation aloud, then listen. Off: the card is
        # only shown, so there is nothing to wait for — arm the mic on the spot.
        if voice_on:
            if dictation_on:
                self._speak_operation(card, then=lambda: self._after_question(card))
            else:
                self._voice_arm(card)
            self._voice_watch(card)
        elif dictation_on:
            self._speak_operation(card)

    def _show_keypad(self):
        kp = getattr(self, "keypad_frame", None)
        if kp and kp.winfo_exists() and not kp.winfo_manager():
            kp.grid(row=3, column=0)

    def _speak_operation(self, card: Card, then=None):
        """Read the question aloud, holding both clocks until it is over.

        What the times are meant to measure is a child thinking, not a machine
        talking: a card read out at the speed of the system voice would
        otherwise cost two and a half seconds more than the same card only
        shown, and the progress chart would plot that as the child.
        """
        if not (self.tts and self.tts.available()):
            if then:
                then()
            return
        word = OP_SPOKEN.get(LANG, OP_SPOKEN["en"])[card.mode]
        phrase = f"{card.a} {word} {card.b}"
        # Each reading is numbered, because stopping a sentence reports through
        # the same callback as finishing one: without this, the tail of the
        # question the child just answered over lands on the next card and
        # starts *its* clock.
        self._speech_id += 1
        token = self._speech_id
        self._speech_start = time.perf_counter()
        self.card_start = None  # this card's clock starts when the reading ends

        def done() -> None:
            if self._close_reading(token):
                self.card_start = time.perf_counter()
            if then:
                then()

        # marshal the done-callback back onto the Tk thread via the pump
        self.tts.speak(phrase, on_done=lambda: self._post(done))

    def _close_reading(self, token: int | None = None) -> bool:
        """Bank the time the reading on air has taken, so no clock counts it.

        Returns whether this call is the one that closed it — a late callback
        from a sentence that was cut off must not touch the card up now.
        """
        if self._speech_start is None:
            return False
        if token is not None and token != self._speech_id:
            return False
        self._read_time += time.perf_counter() - self._speech_start
        self._speech_start = None
        return True

    def _elapsed(self) -> float:
        """Seconds of play so far, with every question read aloud taken out."""
        now = time.perf_counter() if self._speech_start is None else self._speech_start
        return max(0.0, now - self.game_start - self._read_time)

    def _card_elapsed(self) -> float:
        """Seconds this card has been the child's — 0 while it is still being read.

        Clamped at zero because the keypad and the ↩ key stay live through the
        reading (only the mic is held back), so a card can be answered before
        its clock has started.
        """
        if self.card_start is None:
            return 0.0
        return max(0.0, time.perf_counter() - self.card_start)

    def _pill_px(self) -> tuple[int, int]:
        """Pixel size of the question pill — as wide as the widest question.

        Constant so the pill never changes size: a question is shown, GO! flashes
        in the same box, the question returns. Measured from the session's font
        (fixed once the window is up) and cached, with a little room over the
        longest card, which is a division like "100 ÷ 10".
        """
        if self._pill_size is None:
            widest = max(
                self.f_huge.measure(text)
                for text in ("GO !", "GO!", "100 ÷ 10", "20 + 10", "20 − 10", "10 × 10")
            )
            self._pill_size = (widest + 160, self.f_huge.metrics("linespace") + 24)
        return self._pill_size

    def _after_question(self, card: Card) -> None:
        """The question has finished being read: cue GO! and open the mic."""
        if self.current is not card:
            return  # the reading was cut off by the next card; nothing to cue
        self._show_go_hint(card)
        self._voice_arm(card)

    def _show_go_hint(self, card: Card) -> None:
        """Flash GO! in the question pill, then put the question back."""
        hint = self.card_hint
        if hint is not None and hint.winfo_exists():
            hint.config(text="")  # the wait is over; the pill carries the cue now
        pill = self.question_lbl
        if not pill.winfo_exists():
            return
        pill.config(text=T("go"), fg=GOOD)
        self.after(GO_FLASH_MS, lambda: self._restore_question(card))

    def _restore_question(self, card: Card) -> None:
        # Only restore the pill that belongs to the card still on screen: a late
        # timer must not overwrite the question of the card that replaced it.
        if self.current is card and self.question_lbl.winfo_exists():
            self.question_lbl.config(text=card.text, fg=TEXT)

    def _voice_arm(self, target: Card) -> None:
        """Listen for one spoken answer; re-arms itself until the card changes."""
        if not (self.stt and self.stt.available()):
            return
        if self.current is not target or self._voice_mic_bad or self._card_done:
            return
        self.stt.listen(
            on_result=lambda n: self._post(lambda: self._voice_result(n, target)),
            on_state=lambda s: self._post(lambda: self._voice_status(s)),
            pick=lambda nums: spoken_answer(nums, target),
        )

    def _voice_watch(self, target: Card) -> None:
        """Keep the mic armed for as long as this card is up.

        Listening re-arms itself from its own result callback, which is a chain:
        one dropped link (a callback that lands while a capture is already
        running, a worker that dies) and the card goes quietly deaf, with the
        child talking to a question that will never hear them. This second,
        independent timer notices within a second and arms it again.
        """
        self._voice_job = None
        if self.current is not target or self._card_done:
            return  # this card is over; stop watching it
        talking = bool(self.tts and self.tts.speaking())
        if (
            not talking
            and not self._voice_mic_bad
            and self.voice_enabled.get()
            and self.stt
            and self.stt.available()
            and not self.stt.listening()
        ):
            self._voice_arm(target)
        self._voice_job = self.after(1000, lambda: self._voice_watch(target))

    def _voice_status(self, state):
        if state == "no_mic":
            self._voice_mic_bad = True  # stop re-arming; capture is silent
            self._show_keypad()  # give the child a way back in
        lbl = getattr(self, "voice_lbl", None)
        if not (lbl and lbl.winfo_exists()):
            return
        if state == "no_mic":
            lbl.config(text=T("mic_denied"), fg=BAD)
        elif state == "too_quiet":
            # Heard, understood to be a voice, and refused for being whisper-quiet.
            # A whisper never answers a card, so the child has to know it landed on
            # nothing — otherwise a hedged, murmured guess is indistinguishable
            # from a dead microphone, and they murmur the next one too.
            lbl.config(text=T("too_quiet"), fg=ACCENT)
        elif state == "listening":
            lbl.config(text=T("listening"), fg=MUTED)
        elif state == "thinking":
            lbl.config(text=T("thinking"), fg=MUTED)

    def _voice_result(self, n: int | None, target: Card) -> None:
        if self.current is not target or self._card_done:  # card already over
            return
        if n is not None:
            self._voice_answer(n)
            return
        # nothing recognized this round: keep listening (unless the mic is unusable)
        if not self._voice_mic_bad and self.voice_enabled.get():
            self._voice_arm(target)

    def _voice_answer(self, n: int) -> None:
        """A number was heard: that is this card's answer, right or wrong.

        A spoken number is committed the moment it is understood — exactly as if
        ✓ had been pressed. A wrong one too: the child sees the number, hears the
        wrong-answer sound, and the card joins the reinforcement queue to be asked
        once more at the end of the game. Nothing is ever asked to be repeated to
        confirm it; that made every wrong answer cost a second round of talking,
        and left the child reading a hint instead of doing maths.

        What keeps a slip — a chair, a gurgle, "euh", an operand muttered while
        re-reading the question — from answering for the child is the gate in
        front of this and not this: the acoustic judge in
        ``voice.SpeechInput._judge`` (loud enough, voiced, not a whisper), the
        numbers-and-nothing-else rule in ``voice.strict_numbers``, and
        ``spoken_answer`` picking which number of an utterance was meant.
        """
        self.answer_var.set(str(n))  # the child sees the number that was heard
        self.submit()  # auto-submit, in parallel with the keypad

    def _build_keypad(self, parent):
        pad = tk.Frame(parent, bg=BG)
        pad.pack(pady=8)
        keys = [["7", "8", "9"], ["4", "5", "6"], ["1", "2", "3"], ["C", "0", "✓"]]
        for r, rowk in enumerate(keys):
            for c, k in enumerate(rowk):
                if k == "C":
                    cmd, col, fg = (lambda: self.answer_var.set("")), BAD, INK
                elif k == "✓":
                    cmd, col, fg = self.submit, GOOD, INK
                else:

                    def cmd(d=k):
                        return self.answer_var.set(self.answer_var.get() + d)

                    col, fg = CARD_BG, TEXT
                Btn(
                    pad,
                    k,
                    command=cmd,
                    font=self.f_mode,
                    bg=col,
                    fg=fg,
                    width=2,
                    padx=16,
                    pady=4,
                ).grid(row=r, column=c, padx=6, pady=4)

    def _tick(self):
        self.total_time_lbl.config(
            text=f"{T('timer_total')}  {self._fmt(self._elapsed())}"
        )
        self.card_time_lbl.config(
            text=f"{T('timer_card')}  {self._card_elapsed():4.1f}s"
        )
        self._tick_job = self.after(100, self._tick)

    @staticmethod
    def _fmt(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def submit(self) -> None:
        if self._card_done:  # already answered; the card is on its way out
            return
        card = self.current
        if card is None:  # nothing is being asked; nothing to answer
            return
        raw = self.answer_var.get().strip()
        if raw in ("", "-"):
            return
        try:
            val = int(raw)
        except ValueError:
            self.answer_var.set("")
            return

        card.attempts += 1
        is_correct = val == card.answer
        self._card_done = True
        if self.stt:
            self.stt.cancel()  # drop the capture in flight; the card is over

        # record time + outcome only on the very first attempt of a card
        if card.correct_first_try is None:
            card.time_taken = self._card_elapsed()
            card.correct_first_try = is_correct
            self.results.append(card)
            if self.idx < len(self.deck):
                self.idx += 1
            # Reinforce later — but only once per *operation*. A short number
            # selection deals fewer distinct facts than cards asked for, so the
            # same operation can be dealt (and failed) twice; queueing both
            # copies asked it again and again at the end.
            key = (card.mode, card.a, card.b)
            if not is_correct and key not in self.retry_seen:
                self.retry_seen.add(key)
                self.retry_queue.append(card)
        else:
            # The same card, back for the reinforcement round. Its outcome is
            # already written — only the time it cost is still being added up.
            card.retry_time += self._card_elapsed()

        if is_correct:
            self.sfx.play("correct")
            self._flash(T("correct"), GOOD)
            self._advance_after(650)
        else:
            self.sfx.play("wrong")
            self._flash(T("wrong", a=card.answer), BAD)
            self.answer_var.set("")
            self._advance_after(1500)

    def _flash(self, text: str, color: str):
        if self._tick_job:
            self.after_cancel(self._tick_job)
            self._tick_job = None
        self.feedback.config(text=text, fg=color)

    # ------------------------------------------------------------------ #
    # Screen 4: recap
    # ------------------------------------------------------------------ #
    def show_recap(self):
        self._clear()
        total_time = self._elapsed()
        cards = self.results
        n = len(cards)
        correct = sum(1 for c in cards if c.correct_first_try)
        pct = (correct / n * 100) if n else 0
        avg = (sum(c.time_taken for c in cards) / n) if n else 0

        self._save_session(cards, total_time, correct)
        emoji, msg = self._verdict(pct)

        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        col = tk.Frame(f, bg=BG)
        col.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(col, text=emoji, font=self.f_giant, bg=BG, fg=ACCENT).pack(pady=(4, 0))
        tk.Label(col, text=msg, font=self.f_big, fg=TEXT, bg=BG).pack(pady=(0, 8))

        tk.Label(
            col,
            text=f"{T('score')} : {correct}/{n}  ({pct:.0f}%)",
            font=self.f_title,
            fg=GOOD if pct >= 80 else ACCENT,
            bg=BG,
        ).pack(pady=4)
        tk.Label(
            col,
            text=f"{T('total_time')} : {self._fmt(total_time)}",
            font=self.f_body,
            fg=TEXT,
            bg=BG,
        ).pack(pady=2)
        tk.Label(
            col, text=f"{T('avg_time')} : {avg:.1f}s", font=self.f_body, fg=MUTED, bg=BG
        ).pack(pady=2)

        # slowest / struggled operations, for reinforcement
        slow = sorted(cards, key=lambda c: c.time_taken, reverse=True)[:5]
        if slow:
            tk.Label(col, text=T("slowest"), font=self.f_body, fg=ACCENT, bg=BG).pack(
                pady=(16, 4)
            )
            box = tk.Frame(col, bg=CARD_BG)
            box.pack(pady=4)
            for c in slow:
                mark = "✅" if c.correct_first_try else "❌"
                tk.Label(
                    box,
                    text=f"{mark}  {c.text} = {c.answer}    {c.time_taken:.1f}s",
                    font=self.f_small,
                    fg=TEXT,
                    bg=CARD_BG,
                    anchor="w",
                ).pack(fill="x", padx=16, pady=2)

        btns = tk.Frame(f, bg=BG)
        btns.pack(side="bottom", pady=28)
        Btn(
            btns,
            T("again"),
            command=self.show_settings,
            font=self.f_mode,
            bg=GOOD,
            fg=INK,
        ).pack(side="left", padx=12)
        Btn(
            btns,
            T("progress"),
            command=self.show_progress,
            font=self.f_mode,
            bg=CARD_BG,
            fg=TEXT,
        ).pack(side="left", padx=12)
        Btn(
            btns,
            T("quit"),
            command=self._on_quit,
            font=self.f_mode,
            bg=CARD_BG,
            fg=TEXT,
            active_bg=BAD,
        ).pack(side="left", padx=12)

        # The winner's music, once — then, over the silence it leaves, the same
        # encouragement that is on screen, spoken in a warm voice. Chained rather
        # than timed: the two would talk over each other, and a child who taps
        # "Rejouer" mid-fanfare gets neither (a cut-short sound skips its
        # callback), which is exactly right — that game is over.
        say = None
        if self.tts and self.tts.available():
            spoken = f"{msg} {T('score_spoken', c=correct, n=n)}"

            # The sound player calls this from a worker thread, so it goes back
            # onto the Tk loop through the pump like every other voice callback.
            def say() -> None:
                tts = self.tts
                if tts is not None:
                    self._post(lambda: tts.speak(spoken))

        # A beat before the music, so that the cheer for the card that just ended
        # the game gets to finish — starting a sound cuts the one before it, and
        # this screen arrives while that one is still going.
        def fanfare():
            self._fanfare_job = None
            self.sfx.play("victory", then=say)

        self._fanfare_job = self.after(700, fanfare)

    @staticmethod
    def _verdict(pct: float):
        if pct >= 95:
            return "🤩", "BRAVO !" if LANG == "fr" else "AMAZING!"
        if pct >= 80:
            return "🎉", "SUPER !" if LANG == "fr" else "GREAT JOB!"
        if pct >= 60:
            return "😊", "BIEN JOUÉ !" if LANG == "fr" else "WELL DONE!"
        if pct >= 40:
            return "💪", "ENCORE UN EFFORT !" if LANG == "fr" else "KEEP GOING!"
        return (
            "🤕",
            "ALLEZ, ENCORE UN PEU DE TRAVAIL !"
            if LANG == "fr"
            else "A BIT MORE PRACTICE!",
        )

    # ------------------------------------------------------------------ #
    # Screen 5: progress — the results CSV, charted and tabulated
    # ------------------------------------------------------------------ #
    def show_progress(self):
        self._clear()
        self._hush()  # ditto — this screen is for reading, not cheering
        sessions = resultslog.load_sessions()

        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(18, 6))
        Btn(
            bar,
            T("back"),
            command=self.show_settings,
            font=self.f_body,
            bg=CARD_BG,
            fg=TEXT,
        ).pack(side="left")
        tk.Label(
            bar, text=T("progress_title"), font=self.f_title, fg=ACCENT, bg=BG
        ).pack(side="left", padx=20)
        # Reconfigured on every filter change: the count belongs to what is shown.
        self._n_label = tk.Label(
            bar,
            text=T("n_sessions", n=len(sessions)),
            font=self.f_small,
            fg=MUTED,
            bg=BG,
        )
        self._n_label.pack(side="right")

        if not sessions:
            tk.Label(f, text=T("no_results"), font=self.f_body, fg=MUTED, bg=BG).pack(
                expand=True
            )
            return

        # Export lives next to the session count: same corner, same weight as the
        # rest of the header. Its result is reported in the label beside it.
        pdf_btn = Btn(
            bar,
            T("pdf_button"),
            command=self._export_pdf,
            font=self.f_small,
            bg=CARD_BG,
            fg=MUTED,
            padx=14,
            pady=7,
        )
        pdf_btn.pack(side="right", padx=(0, 14))
        Tooltip(pdf_btn, T("pdf_tip"), self.f_hint)
        self._pdf_status = tk.Label(bar, text="", font=self.f_small, fg=MUTED, bg=BG)
        self._pdf_status.pack(side="right", padx=(0, 12))

        # The month filter, next to the title. Every month the log covers, newest
        # first, plus the current one even when it is still empty — a parent
        # opening the screen on the 1st wants to see that this month has begun.
        # "Tous les mois" stays at the top: it is the whole history, the view
        # this screen had before there was anything to filter.
        logged = resultslog.months(sessions)
        now = datetime.now().astimezone()
        current = (now.year, now.month)
        listed = logged if current in logged else [current, *logged]
        # Open on this month, or — if nothing has been played in it yet — on the
        # last month that was, so the screen never opens on an empty chart.
        self._month: tuple[int, int] | None = (
            current if current in logged else (logged[0] if logged else current)
        )
        self._all_sessions = sessions
        picker = Dropdown(
            bar,
            [(None, T("month_all"))] + [(ym, month_label(ym)) for ym in listed],
            self._month,
            self._select_month,
            font=self.f_small,
            bg=CARD_BG,
            fg=TEXT,
            padx=14,
            pady=7,
        )
        picker.pack(side="left")
        Tooltip(picker, T("month_tip"), self.f_hint)

        # The chart's key, beside the filter: which colour is which curve. The
        # grade key only appears while the month on show has a grade to draw, so
        # a legend never promises a line that is not there.
        legend = tk.Frame(bar, bg=BG)
        legend.pack(side="left", padx=(20, 0))
        self._legend_key(legend, ACCENT, T("legend_time")).pack(side="left")
        self._legend_grade = self._legend_key(legend, CHART_GRADE, T("legend_grade"))

        # Only this frame is rebuilt when the month changes, so the header — and
        # the pick your eye just left — stays put.
        self._progress_body = tk.Frame(f, bg=BG)
        self._progress_body.pack(fill="both", expand=True)
        self._render_progress()

    def _legend_key(self, parent, color: str, text: str) -> tk.Frame:
        """One key of the chart's legend: the series drawn small — a stroke with
        a dot on it, in the colour that curve is drawn in — and its name. The
        colour is the whole of the pairing, so the swatch is the curve's own."""
        row = tk.Frame(parent, bg=BG)
        h = self.f_small.metrics("linespace")
        mid = h / 2
        swatch = tk.Canvas(
            row, width=22, height=h, bg=BG, highlightthickness=0, bd=0, takefocus=0
        )
        swatch.create_line(1, mid, 21, mid, fill=color, width=2, capstyle="round")
        swatch.create_oval(
            8, mid - 3.5, 15, mid + 3.5, fill=color, outline=BG, width=1.5
        )
        swatch.pack(side="left")
        tk.Label(row, text=text, font=self.f_small, fg=MUTED, bg=BG).pack(
            side="left", padx=(6, 0)
        )
        return row

    def _select_month(self, ym: tuple[int, int] | None) -> None:
        self._month = ym
        self._render_progress()

    def _render_progress(self):
        """Chart + table for the month on show — the whole body of the screen."""
        body = self._progress_body
        for w in body.winfo_children():
            w.destroy()

        sessions = [
            s for s in self._all_sessions if resultslog.in_month(s, self._month)
        ]
        # Kept for the PDF export, which prints exactly what is on the screen.
        self._chart, self._sessions = None, sessions
        self._n_label.config(text=T("n_sessions", n=len(sessions)))

        if not sessions:
            self._legend_grade.pack_forget()  # no chart, nothing to key
            tk.Label(
                body, text=T("no_month_results"), font=self.f_body, fg=MUTED, bg=BG
            ).pack(expand=True)
            return

        # The chart takes a third of the height; the table gets the rest and
        # scrolls. Its title is drawn inside it, level with the grade axis' own.
        chart = TimeChart(
            body,
            sessions,
            self.f_small,
            height=max(190, int(self.winfo_height() * 0.30)),
        )
        chart.pack(fill="x", padx=28, pady=(8, 12))
        if chart.grades:
            self._legend_grade.pack(side="left", padx=(16, 0))
        else:
            self._legend_grade.pack_forget()
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", padx=28)
        self._chart = chart

        self._build_results_table(body, sessions)

    def _export_pdf(self):
        """The progress screen on A4: same chart, same rows, current palette.

        With a month selected that is the monthly report — the filter and the
        export are the same view, so what you see is what gets printed.
        """
        if not self._sessions:  # an empty month has nothing to print
            self._flash_pdf_status(T("no_month_results"), MUTED)
            return
        stamp = datetime.now().astimezone()
        path = filedialog.asksaveasfilename(
            parent=self,
            title=T("pdf_dialog"),
            defaultextension=".pdf",
            initialdir=str(resultslog.RESULTS_DIR),
            initialfile=pdfreport.default_filename(LANG, stamp, self._month),
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:  # the parent changed their mind
            return
        try:
            pdfreport.export_progress(
                path,
                self._sessions,
                self._chart,
                palette=THEMES[THEME],
                labels={
                    "title": T("progress_title"),
                    "subtitle": T(
                        "pdf_exported_on", when=stamp.strftime("%d/%m/%Y %H:%M")
                    ),
                    # The month it covers, named on the report itself; empty when
                    # the whole history is being printed.
                    "period": "" if self._month is None else month_label(self._month),
                    "chart_title": T("chart_title"),
                    "axis_grade": T("axis_grade"),
                    "legend_time": T("legend_time"),
                    "legend_grade": T("legend_grade"),
                    "n_sessions": T("n_sessions", n=len(self._sessions)),
                    "footer": T("pdf_footer"),
                    "date": T("col_date"),
                    "famille": T("col_family"),
                    "temps": T("col_time"),
                    "cartes": T("col_cards"),
                    "note": T("col_note"),
                    "temps_moyen": T("col_avg"),
                    "difficultes": T("col_difficult"),
                },
                when=stamp,
            )
        except (OSError, ValueError) as exc:
            self._flash_pdf_status(f"{T('pdf_failed')} — {exc}", BAD)
            return
        self._flash_pdf_status(T("pdf_saved"), GOOD)

    def _flash_pdf_status(self, text, color, ms=4000):
        """Say how it went beside the button, then get out of the way."""
        if not self._pdf_status.winfo_exists():
            return
        self._pdf_status.config(text=text, fg=color)
        self.after(
            ms,
            lambda: (
                self._pdf_status.winfo_exists() and self._pdf_status.config(text="")
            ),
        )

    def _build_results_table(self, parent, sessions):
        """Every logged session, oldest first — the paper sheet, on screen."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        # `yscrollincrement=1` makes a `yview_scroll` unit one pixel: a canvas
        # left at 0 scrolls a tenth of its own height per unit instead, so a
        # single nudge of the wheel would jump most of the table at a time.
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0, yscrollincrement=1)
        bar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        table = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=table, anchor="nw")
        # `e.widget is canvas`: the rows carry the canvas as an extra bindtag (see
        # below), so this binding is also consulted for *their* <Configure> — and
        # widening the scrolled window to a single cell's width would collapse the
        # whole table, one feedback pass at a time.
        canvas.bind(
            "<Configure>",
            lambda e: e.widget is canvas and canvas.itemconfig(window, width=e.width),
        )
        table.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        # Wheel and trackpad. The pointer is nearly always over a row Label, not
        # over the canvas, and Tk hands an event to the widget's own bindtags
        # only — it never walks up to a parent widget — so a plain binding on the
        # canvas would never fire. The rows are given the canvas as an extra
        # bindtag at the end of this function instead, which routes their scroll
        # events here without a single global binding: an <Enter>/<Leave> pair
        # driving bind_all cannot work, because moving onto a child *is* leaving
        # the canvas as far as Tk is concerned, so it disarmed itself the moment
        # the pointer touched a row.
        def wheel(pixels):
            if canvas.winfo_exists():
                canvas.yview_scroll(pixels, "units")

        bind_wheel(canvas, wheel)

        cols: tuple[tuple[str, Anchor, int], ...] = (
            (T("col_date"), "w", 3),
            (T("col_family"), "w", 4),
            (T("col_time"), "e", 3),
            (T("col_cards"), "e", 2),
            (T("col_note"), "e", 2),
            (T("col_avg"), "e", 4),
            (T("col_difficult"), "w", 6),
        )
        for i, (_, _, weight) in enumerate(cols):
            table.grid_columnconfigure(i, weight=weight)

        for i, (label, anchor, _) in enumerate(cols):
            tk.Label(
                table,
                text=label,
                font=self.f_small,
                fg=MUTED,
                bg=BG,
                anchor=anchor,
                padx=14,
                pady=8,
            ).grid(row=0, column=i, sticky="nsew")
        tk.Frame(table, bg=BORDER, height=1).grid(
            row=1, column=0, columnspan=len(cols), sticky="ew"
        )

        # Newest first: the session that just finished sits right under the
        # header, and the history scrolls away below it.
        for r, s in enumerate(reversed(sessions)):
            rowbg = SURFACE if r % 2 else CARD_BG
            cells = (
                (s.day.strftime("%d/%m/%Y") if s.day else "—", TEXT),
                (s.famille or "—", TEXT),
                (
                    resultslog.fmt_duration(s.seconds)
                    if s.seconds is not None
                    else (s.raw_time or "—"),
                    ACCENT,
                ),
                (str(s.cards) if s.cards else "—", MUTED),
                (resultslog.fmt_note(s.note) or "—", ACCENT),
                (f"{s.avg:.1f} s" if s.avg is not None else "—", GOOD),
                (", ".join(s.difficultes) or "—", MUTED),
            )
            for i, ((text, fg), (_, anchor, _)) in enumerate(
                zip(cells, cols, strict=True)
            ):
                tk.Label(
                    table,
                    text=text,
                    font=self.f_small,
                    fg=MUTED if text == "—" else fg,  # a blank is never a value
                    bg=rowbg,
                    anchor=anchor,
                    padx=14,
                    pady=7,
                ).grid(row=r + 2, column=i, sticky="nsew")

        # Every cell scrolls the canvas it sits in: the canvas' path name is
        # appended to each row's bindtags, so a wheel event delivered to a Label
        # is looked up on the canvas too, and `wheel` above sees it.
        for w in (table, *table.winfo_children()):
            w.bindtags((*w.bindtags(), str(canvas)))

    def _save_session(self, cards, total_time, correct):
        stamp = datetime.now().astimezone()
        hardest = difficult_cards(cards)

        # One summary line in the CSV log — the file the progress screen reads
        # and the parent can edit by hand. `famille` records what was drilled:
        # the operator sign plus the numbers that were switched on ("-1 et -2").
        resultslog.append_session(
            day=stamp.date(),
            famille=resultslog.famille_label(self.mode, self.allowed, LANG),
            seconds=total_time,
            cards=len(cards),
            correct=correct,
            lang=LANG,
            # The cards that fought back, for the parent to read and for the
            # next session to deal again.
            difficultes=[resultslog.fmt_operation(c.mode, c.a, c.b) for c in hardest],
        )

        # ...and the full per-card detail as JSON, for finer analysis later.
        try:
            STATS_DIR.mkdir(exist_ok=True)
            payload = {
                "timestamp": stamp.isoformat(timespec="seconds"),
                "mode": self.mode,
                "n_cards": len(cards),
                "correct_first_try": correct,
                "total_time_s": round(total_time, 2),
                "cards": [
                    {
                        "op": c.text,
                        "answer": c.answer,
                        "correct": c.correct_first_try,
                        "attempts": c.attempts,
                        "time_s": round(c.time_taken, 2),
                        # Only ever non-zero on a card that came back at the
                        # end; `time_s` stays the first sitting alone, so older
                        # files keep meaning what they meant.
                        "retry_time_s": round(c.retry_time, 2),
                    }
                    for c in cards
                ],
            }
            fname = STATS_DIR / f"{stamp.strftime('%Y%m%d-%H%M%S')}-{self.mode}.json"
            fname.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        except OSError:
            pass  # never let logging break the game


def main():
    choose_language_at_startup()  # asks only when no speech model is installed
    resolve_language()  # sets the module's LANG before any widget reads it
    app = Almathus()
    try:
        app.mainloop()
    finally:
        # close the mic stream + restore input gain to 50% on exit (any close path)
        if app.stt:
            app.stt.stop()
            app.stt.restore_input_volume(50)


if __name__ == "__main__":
    main()
