"""Almathus — a kid-friendly mental-math game (addition, subtraction, ...).

Pure-stdlib Tkinter app. Runs anywhere Python has Tk:

    uv run almathus          # or:  python main.py

Flow:  Settings -> 5-4-3-2-1-GO! countdown -> cards -> reinforcement round -> recap.
Per-card time is measured so a parent can see which operations need work.

Four moments of that flow have a sound (see sfx.py and assets/sounds/CREDITS.md): the
countdown, a right answer, a wrong one, and the winner's music over the recap.
They need a command-line player on PATH and are silently skipped without one.

Every session is logged twice: the full per-card detail to stats/*.json, and
one summary line to results.csv — the hand-editable log the progress screen
charts and tabulates (see results.py for the header definition). Both live in
the user's Documents/Almathus folder, never beside the code.
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
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import font as tkfont
from typing import Any, ClassVar, Literal

import i18n
import languages
import pdfreport
import results as resultslog
import sfx
import voices

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

# The language the app runs in. Every supported language has screen text (see
# i18n.py); a missing entry still falls back to English rather than a raw key.
DEFAULT_LANG = "fr"  # used until a language is chosen and its model installed
LANG = DEFAULT_LANG  # resolved at startup by resolve_language()


# The operator as it is said out loud, per language. A card is read as
# "7 × 4" -> "<seven> <word> <four>", so each entry has to sit between two
# spoken numbers. English was the fallback for every language but French until
# now, which meant a Spanish card was read "siete times cuatro"; the voice was
# Spanish and the word was not. The table follows the app's language registry
# (languages.py) and is looked up by base code, so en-us/en-gb/en-in share "en".
#
# Only fr and en are user-verified. The rest are the standard school reading in
# each language (the word a teacher says between the two numbers); one word per
# mode to correct if a native speaker hears something off.
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
    "es": {
        "addition": "más",
        "subtraction": "menos",
        "multiplication": "por",
        "division": "entre",
    },
    "de": {
        "addition": "plus",
        "subtraction": "minus",
        "multiplication": "mal",
        "division": "geteilt durch",
    },
    "it": {
        "addition": "più",
        "subtraction": "meno",
        "multiplication": "per",
        "division": "diviso",
    },
    "pt": {
        "addition": "mais",
        "subtraction": "menos",
        "multiplication": "vezes",
        "division": "dividido por",
    },
    "nl": {
        "addition": "plus",
        "subtraction": "min",
        "multiplication": "keer",
        "division": "gedeeld door",
    },
    "pl": {
        "addition": "plus",
        "subtraction": "minus",
        "multiplication": "razy",
        "division": "podzielić przez",
    },
    "ru": {
        "addition": "плюс",
        "subtraction": "минус",
        "multiplication": "умножить на",
        "division": "разделить на",
    },
    "uk": {
        "addition": "плюс",
        "subtraction": "мінус",
        "multiplication": "помножити на",
        "division": "поділити на",
    },
    "cs": {
        "addition": "plus",
        "subtraction": "minus",
        "multiplication": "krát",
        "division": "lomeno",
    },
    "sv": {
        "addition": "plus",
        "subtraction": "minus",
        "multiplication": "gånger",
        "division": "delat med",
    },
    "tr": {
        "addition": "artı",
        "subtraction": "eksi",
        "multiplication": "çarpı",
        "division": "bölü",
    },
    "ar": {
        "addition": "زائد",
        "subtraction": "ناقص",
        "multiplication": "ضرب",
        "division": "تقسيم",
    },
    "fa": {
        "addition": "به‌علاوه",
        "subtraction": "منهای",
        "multiplication": "ضربدر",
        "division": "تقسیم بر",
    },
    "hi": {
        "addition": "जमा",
        "subtraction": "घटा",
        "multiplication": "गुणा",
        "division": "भाग",
    },
    "gu": {
        "addition": "વત્તા",
        "subtraction": "ઓછા",
        "multiplication": "ગુણ્યા",
        "division": "ભાગ્યા",
    },
    "te": {
        "addition": "ప్లస్",
        "subtraction": "మైనస్",
        "multiplication": "గుణించు",
        "division": "భాగించు",
    },
    "ja": {
        "addition": "たす",
        "subtraction": "ひく",
        "multiplication": "かける",
        "division": "わる",
    },
    "ko": {
        "addition": "더하기",
        "subtraction": "빼기",
        "multiplication": "곱하기",
        "division": "나누기",
    },
    "cn": {
        "addition": "加",
        "subtraction": "减",
        "multiplication": "乘",
        "division": "除以",
    },
    "vi": {
        "addition": "cộng",
        "subtraction": "trừ",
        "multiplication": "nhân",
        "division": "chia",
    },
    "ca": {
        "addition": "més",
        "subtraction": "menys",
        "multiplication": "per",
        "division": "dividit per",
    },
    "eo": {
        "addition": "plus",
        "subtraction": "minus",
        "multiplication": "multiplikite per",
        "division": "dividite per",
    },
    "ka": {
        "addition": "პლუს",
        "subtraction": "მინუს",
        "multiplication": "გამრავლებული",
        "division": "გაყოფილი",
    },
    "ky": {
        "addition": "кошуу",
        "subtraction": "кемитүү",
        "multiplication": "көбөйтүү",
        "division": "бөлүү",
    },
    "kz": {
        "addition": "қосу",
        "subtraction": "азайту",
        "multiplication": "көбейту",
        "division": "бөлу",
    },
    "tg": {
        "addition": "ҷамъ",
        "subtraction": "тарҳ",
        "multiplication": "зарб",
        "division": "тақсим",
    },
    "uz": {
        "addition": "qo'shish",
        "subtraction": "ayirish",
        "multiplication": "ko'paytirish",
        "division": "bo'lish",
    },
}

# The recap sentence, spoken after the fanfare. It lives here, not in i18n.py,
# because it is heard and never drawn: it has no place in the screen dictionary
# and must stay separate from the equivalent printed labels.
# Neutral phrasing, so it fits a boy or a girl; {c} correct out of {n}.
SPOKEN_SCORE = {
    "fr": "Tu as eu {c} sur {n}.",
    "en": "You got {c} out of {n}.",
    "es": "Has acertado {c} de {n}.",
    "de": "Du hast {c} von {n} richtig.",
    "it": "Hai fatto {c} su {n}.",
    "pt": "Acertaste {c} de {n}.",
    "nl": "Je had {c} van de {n} goed.",
    "pl": "Poprawnie: {c} z {n}.",
    "ru": "Правильно: {c} из {n}.",
    "uk": "Правильно: {c} з {n}.",
    "cs": "Správně: {c} z {n}.",
    "sv": "Du fick {c} av {n} rätt.",
    "tr": "{n} sorudan {c} tanesini doğru yaptın.",
    "ar": "أجبت بشكل صحيح على {c} من {n}.",
    "fa": "به {c} سؤال از {n} درست پاسخ دادی.",
    "hi": "आपने {n} में से {c} सही किए।",
    "gu": "તમે {n} માંથી {c} સાચા કર્યા.",
    "te": "మీరు {n} లో {c} సరిగ్గా చేశారు.",
    "ja": "{n} 問中 {c} 問正解でした。",
    "ko": "{n} 문제 중 {c} 개를 맞혔어요.",
    "cn": "{n} 道中你答对了 {c} 道。",
    "vi": "Bạn làm đúng {c} trên {n} câu.",
    "ca": "Has encertat {c} de {n}.",
    "eo": "Vi atingis {c} el {n}.",
    "ka": "შენ {n}-დან {c} სწორად უპასუხე.",
    "ky": "Сиз {n} суроодон {c} туура жооп бердиңиз.",
    "kz": "Сіз {n} сұрақтан {c} дұрыс жауап бердіңіз.",
    "tg": "Шумо аз {n} савол {c}-ро дуруст ҷавоб додед.",
    "uz": "Siz {n} ta savoldan {c} tasiga to'g'ri javob berdingiz.",
}


def spoken_words(lang: str) -> dict[str, str]:
    """The operator words for a language, by base code, English as last resort."""
    return OP_SPOKEN.get(lang) or OP_SPOKEN.get(lang.split("-", 1)[0], OP_SPOKEN["en"])


def spoken_score(lang: str, correct: int, total: int) -> str:
    """The recap sentence in the app's language, English as last resort."""
    template = SPOKEN_SCORE.get(lang) or SPOKEN_SCORE.get(
        lang.split("-", 1)[0], SPOKEN_SCORE["en"]
    )
    return template.format(c=correct, n=total)


def T(key: str, **kw) -> str:
    # Missing text falls back to English rather than to raw keys: a speakable
    # setup and settings screen beats a cryptic one.
    s = i18n.text(LANG, key)
    return s.format(**kw) if kw else s


def pdf_text(key: str, **kw) -> str:
    """A label for the PDF report, in English when the script cannot print.

    The report has no Unicode font, so a Cyrillic, Arabic or CJK label would
    otherwise come out blank. The screen still uses :func:`T`; only paper falls
    back.
    """
    s = T(key, **kw)
    if pdfreport.supports_text(s):
        return s
    english = i18n.text("en", key)
    return english.format(**kw) if kw else english


def month_label_pdf(ym: tuple[int, int]) -> str:
    """The report's month heading, using English if the script cannot print."""
    label = month_label(ym)
    if pdfreport.supports_text(label):
        return label
    return f"{i18n.month('en', ym[1])} {ym[0]}"


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
    name = i18n.month(LANG, ym[1])
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
        # The results table's shaded rows. It has to stand a clear step away
        # from the CARD_BG rows beside it, or the zebra stripes vanish on the
        # dark palette and the eye loses the line it is reading across.
        "ROW_ALT": "#3a4380",
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
        "TITLE_SVG": "boy.svg",
        "TITLE_ICON": "🧮",  # fallback if the drawing cannot be loaded
    },
    # Warm-pink page, plum ink. The whole palette flips light here, so the
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
        "ROW_ALT": "#fff4f8",  # the shaded table rows: near-white against the pink
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
        "TITLE_SVG": "girl.svg",
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
ROW_ALT: str = _DEFAULT["ROW_ALT"]
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

STATS_DIR = resultslog.stats_dir()


def _app_root() -> Path:
    """Where the read-only app files live: a source checkout, or a frozen bundle.

    PyInstaller collects ``assets/`` under ``sys._MEIPASS`` in a one-file build and
    beside the executable in a one-directory build, and sets ``_MEIPASS`` in both,
    so one branch covers them. Nothing here is writable: preferences and results
    have their own per-user homes.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


APP_DIR = _app_root()
ASSETS_DIR = APP_DIR / "assets"
SOUNDS_DIR = ASSETS_DIR / "sounds"


def _prefs_dir() -> Path:
    """The per-user folder for ``prefs.json``.

    Preferences are the one thing the app *writes* under its own name, so they
    cannot live beside the code: a packaged app is read-only, and writing inside
    ``Almathus.app`` invalidates its signature. The folder is the same one the
    language models use, so a user has one place to look.
    """
    override = os.environ.get("ALMATHUS_PREFS_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Almathus"


PREFS_FILE = _prefs_dir() / "prefs.json"

COUNTDOWN_STEPS = 3  # 3-2-1, then GO!
# countdown.mp3 is three beeps and a launch tone, laid out on an exact one-second
# grid: beeps at 0, 1000 and 2000 ms, the tone at 3000 ms, ringing to 4000. The
# numbers on screen are hung off that grid rather than the other way round, so a
# beep and a number land on the same instant instead of merely near one another.
COUNTDOWN_TICK = 1000  # ms one number stays on screen = the gap between beeps
COUNTDOWN_GO_AT = 3000  # ms into the sample where the launch tone starts
COUNTDOWN_GO_LEN = 1000  # ms it rings for, after which the sample is silent
GO_FLASH_MS = 500  # how long GO! holds the question pill before the question returns

# How many times a card carried over from the last session is dealt again. Twice
# is what makes it stick: once is another draw of the same lottery, and a third
# would eat the round.
REPEAT_TIMES = 2


def read_prefs() -> dict:
    """Everything remembered between runs. Anything unreadable reads as empty."""
    try:
        prefs = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return prefs if isinstance(prefs, dict) else {}


def migrate_legacy_prefs() -> None:
    """Adopt a ``prefs.json`` left in a source checkout, once.

    Before the move to the per-user folder the file sat beside the code, so an
    existing checkout has the family's language and theme in it. It is copied,
    not moved: the old file is ignored by git and harmless, and leaving it alone
    means a checkout shared read-only still works.
    """
    legacy = APP_DIR / "prefs.json"
    if legacy == PREFS_FILE or PREFS_FILE.exists() or not legacy.exists():
        return
    with contextlib.suppress(OSError, ValueError):
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")


def write_prefs(**updates) -> None:
    """Merge and write. Writing the whole file from one caller's value would
    drop every other preference, so each key is folded into what is on disk."""
    # A read-only install must never break the game.
    with contextlib.suppress(OSError):
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(read_prefs() | updates), encoding="utf-8")


def load_allowed() -> set[int]:
    """Numbers selected last time. A drill often runs for weeks on one number,
    so the choice has to survive a restart. Anything unreadable or nonsensical
    falls back to 'all numbers on'."""
    try:
        raw = read_prefs()["numbers"]
        sel = {int(n) for n in raw if NUM_MIN <= int(n) <= NUM_MAX}
    except ValueError, TypeError, KeyError:
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
    """True when the app must ask about a language before it starts.

    A first run — no recognizer model at all — always asks. So does an install
    that predates the harmonized voice: its language has a Piper model left to
    fetch, and the picker is the screen that downloads it. That second question
    is asked once; skipping it sets ``voice_skipped`` so the game does not
    reopen the picker on every launch for someone who is happy with the system
    voice.
    """
    if not installed_languages():
        return True
    saved = load_language() or DEFAULT_LANG
    wants_voice = saved in voices.VOICES and not voices.is_installed(saved)
    return wants_voice and not read_prefs().get("voice_skipped")


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

# Overflow the settings page absorbs before it shows a scrollbar. The column
# ends in 16 px of padding, so a handful of pixels can be given back without
# clipping anything a reader came for — and a bar for two pixels reads as a
# rendering fault, not as a page with more to see.
FIT_SLACK = 6

# The window the menu was drawn for. The type is sized once for the display the
# app opens on, from this design: a window resize reflows the page and never
# re-scales its text. The floor keeps the type readable on a small display;
# below it the page scrolls rather than shrinking further.
PAGE_W, PAGE_H = 1440, 900
SCALE_MIN = 0.6


_ROUND_CACHE: dict[tuple, tk.PhotoImage] = {}
_CORNER_CACHE: dict[tuple[int, str, str | None, int, str], tk.PhotoImage] = {}


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

    For shapes built once and kept — the question pill, the answer field, the
    window icon — where a single raster is simpler than corner tiles. Anything
    that changes size while a window is dragged uses `RoundedRect` instead."""
    key = (w, h, radius, fill, border, border_width)
    cached = _ROUND_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_ROUND_CACHE) > 2048:
        _ROUND_CACHE.clear()
    fill_rgb = _hex_rgb(fill)
    border_rgb = _hex_rgb(border) if border else fill_rgb
    r = max(0, min(radius, w // 2, h // 2))
    bw = max(0, min(border_width, w // 2, h // 2))
    ri = max(0, r - bw)
    fill4 = bytes((*fill_rgb, 255))
    border4 = bytes((*border_rgb, 255))
    px = bytearray(w * h * 4)
    # The shape is x-monotone — at any row it is one run of pixels — so only the
    # few columns straddling its left/right edges need a coverage sample. The
    # run between them is solid, filled wholesale with a slice (C speed). That
    # turns an O(w·h) Python loop into O(h·edges), which is what makes resizing
    # a window cheap enough to keep up with the pointer.
    for y in range(h):
        cy = y + 0.5
        dyo = max(0.0, r - cy, cy - (h - r))
        inso = r - math.sqrt(max(0.0, r * r - dyo * dyo)) if dyo and r else 0.0
        left_o, right_o = inso, w - inso
        if bw:
            cyi = cy - bw
            dyi = max(0.0, ri - cyi, cyi - (h - 2 * bw - ri))
            insi = ri - math.sqrt(max(0.0, ri * ri - dyi * dyi)) if dyi and ri else 0.0
            left_i, right_i = bw + insi, w - bw - insi
        else:
            left_i, right_i = left_o, right_o
        base = y * w * 4
        # outer run (border colour), then the inner run (fill) painted over it
        ox0, ox1 = math.ceil(left_o), math.ceil(right_o) - 1
        if 0 <= ox0 <= ox1 < w:
            start = base + ox0 * 4
            px[start : start + (ox1 - ox0 + 1) * 4] = border4 * (ox1 - ox0 + 1)
        # The inner rounded rect only exists between the top and bottom border
        # bands: painting fill on those rows would erase the horizontal borders
        # and leave only the corners and side edges — a half-drawn outline.
        inner_row = not bw or bw <= y <= h - bw - 1
        ix0, ix1 = (math.ceil(left_i), math.ceil(right_i) - 1) if inner_row else (1, 0)
        if 0 <= ix0 <= ix1 < w:
            start = base + ix0 * 4
            px[start : start + (ix1 - ix0 + 1) * 4] = fill4 * (ix1 - ix0 + 1)
        # the antialiased columns at the (up to four) vertical edges of the runs
        edges: tuple[float, ...]
        if not bw:
            edges = (left_o, right_o)
        elif inner_row:
            edges = (left_o, left_i, right_i, right_o)
        else:
            edges = (left_o, right_o)
        for edge in edges:
            for x in range(max(0, math.floor(edge) - 1), min(w, math.ceil(edge) + 2)):
                a_out = _rrect_alpha(x, y, w, h, r, 0, 0)
                if a_out <= 0.0:
                    continue
                a_in = (
                    _rrect_alpha(x, y, w - 2 * bw, h - 2 * bw, ri, bw, bw)
                    if bw
                    else a_out
                )
                t = a_in / a_out
                i = base + x * 4
                px[i] = round(fill_rgb[0] * t + border_rgb[0] * (1 - t))
                px[i + 1] = round(fill_rgb[1] * t + border_rgb[1] * (1 - t))
                px[i + 2] = round(fill_rgb[2] * t + border_rgb[2] * (1 - t))
                px[i + 3] = round(a_out * 255)
    img = tk.PhotoImage(data=base64.b64encode(_png_bytes(w, h, px)), format="png")
    _ROUND_CACHE[key] = img
    return img


def corner_photo(
    radius: int,
    fill: str,
    border: str | None,
    border_width: int,
    corner: Literal["nw", "ne", "sw", "se"],
) -> tk.PhotoImage:
    """One quarter of a rounded corner, as a small cached raster.

    Panels and buttons change width while a window is dragged, and rebuilding
    their whole (often near-megapixel) raster each time is what made the drag
    stutter. Only the corners need antialiasing: the straight edges are exact
    rectangles, so they are drawn as canvas items and only these radius-sized
    tiles are rasterized — once per shape and colour, then reused at any size.
    """
    r = max(1, radius)
    bw = max(0, min(border_width, r))
    key = (r, fill, border, bw, corner)
    cached = _CORNER_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_CORNER_CACHE) > 1024:
        _CORNER_CACHE.clear()
    fill_rgb = _hex_rgb(fill)
    border_rgb = _hex_rgb(border) if border else fill_rgb
    ri = max(0, r - bw)
    px = bytearray(r * r * 4)
    for y in range(r):
        for x in range(r):
            # Fold the corner back onto the top-left quadrant: the rounded edge
            # depends on the radius there, never on the size of the box.
            lx = x if corner in ("nw", "sw") else r - 1 - x
            ly = y if corner in ("nw", "ne") else r - 1 - y
            a_out = _rrect_alpha(lx, ly, 2 * r, 2 * r, r, 0, 0)
            if a_out <= 0.0:
                continue
            a_in = (
                _rrect_alpha(lx, ly, 2 * (r - bw), 2 * (r - bw), ri, bw, bw)
                if bw and ri > 0
                else (0.0 if bw else a_out)
            )
            t = a_in / a_out
            i = (y * r + x) * 4
            px[i] = round(fill_rgb[0] * t + border_rgb[0] * (1 - t))
            px[i + 1] = round(fill_rgb[1] * t + border_rgb[1] * (1 - t))
            px[i + 2] = round(fill_rgb[2] * t + border_rgb[2] * (1 - t))
            px[i + 3] = round(a_out * 255)
    img = tk.PhotoImage(data=base64.b64encode(_png_bytes(r, r, px)), format="png")
    _CORNER_CACHE[key] = img
    return img


class RoundedRect:
    """A rounded rectangle painted on a canvas with reusable items.

    The straight parts are ordinary rectangles and the four corners are cached
    radius-sized tiles. Rebuilding those ten items for every pixel of a drag is
    measurably slower than moving the ones already there — a canvas restacks
    and repaints on each create and delete — so they are made once and then
    only their coordinates (and, on a colour change, their fill) move.
    """

    def __init__(self, canvas: tk.Canvas, tags: str | tuple[str, ...] = ()) -> None:
        self.canvas = canvas
        self.tags = tags
        self._style: tuple[int, int, bool, str, str | None] | None = None
        self._size = (0, 0)
        self._edges: list[int] = []
        self._fills: list[int] = []
        self._corners: list[int] = []

    def draw(
        self,
        w: int,
        h: int,
        radius: int,
        fill: str,
        border: str | None = None,
        border_width: int = 0,
    ) -> bool:
        """Fit the shape to `w`×`h`. Returns whether its items were rebuilt."""
        w, h = max(1, w), max(1, h)
        r = max(0, min(radius, w // 2, h // 2))
        bw = max(0, min(border_width, r))
        has_edge = bool(border and bw)
        style = (r, bw, has_edge, fill, border if has_edge else None)
        if style != self._style:
            self._style = style
            self._rebuild(w, h, r, bw, fill, border, has_edge)
            self._size = (w, h)
            return True
        if (w, h) != self._size:
            self._size = (w, h)
            self._layout(w, h, r, bw)
        return False

    def _rebuild(
        self,
        w: int,
        h: int,
        r: int,
        bw: int,
        fill: str,
        border: str | None,
        has_edge: bool,
    ) -> None:
        c = self.canvas
        for item in self._edges + self._fills + self._corners:
            c.delete(item)
        if has_edge:
            edge = border or ""
            self._edges = [
                c.create_rectangle(0, 0, 0, 0, fill=edge, outline="", tags=self.tags)
                for _ in range(4 if r else 1)
            ]
        else:
            self._edges = []
        self._fills = [
            c.create_rectangle(0, 0, 0, 0, fill=fill, outline="", tags=self.tags)
            for _ in range(2 if r else 1)
        ]
        self._corners = []
        if r:
            corners: tuple[tuple[Literal["nw", "ne", "sw", "se"], int, int], ...] = (
                ("nw", 0, 0),
                ("ne", w - r, 0),
                ("sw", 0, h - r),
                ("se", w - r, h - r),
            )
            self._corners = [
                c.create_image(
                    0,
                    0,
                    anchor="nw",
                    image=corner_photo(r, fill, border, bw, name),
                    tags=self.tags,
                )
                for name, _, _ in corners
            ]
        self._layout(w, h, r, bw)

    def _layout(self, w: int, h: int, r: int, bw: int) -> None:
        c = self.canvas
        if r == 0:
            if self._edges:
                c.coords(self._edges[0], 0, 0, w, h)
            c.coords(self._fills[0], bw, bw, w - bw, h - bw)
            return
        if self._edges:
            c.coords(self._edges[0], r, 0, w - r, bw)
            c.coords(self._edges[1], r, h - bw, w - r, h)
            c.coords(self._edges[2], 0, r, bw, h - r)
            c.coords(self._edges[3], w - bw, r, w, h - r)
        # Two fill bands: the vertical one between the corner tiles, the
        # horizontal one between the top and bottom edges. Their union leaves
        # exactly the four corner squares to the tiles.
        c.coords(self._fills[0], bw, r, w - bw, h - r)
        c.coords(self._fills[1], r, bw, w - r, h - bw)
        for item, (x, y) in zip(
            self._corners,
            ((0, 0), (w - r, 0), (0, h - r), (w - r, h - r)),
            strict=True,
        ):
            c.coords(item, x, y)


class RoundedPanel:
    """A rounded, bordered panel drawn behind an inner frame.

    A Tk frame is square, so the panel is a canvas with the rounded shape
    painted as canvas items; the content lives in a frame set *inside* it, far
    enough from the edges that its own square corners sit over the rounded fill
    and never show."""

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
        self._bg = RoundedRect(self.canvas, "bg")
        self._canvas_h = 0
        # Tracked from the events rather than re-asked: `winfo_reqheight` inside
        # a `<Configure>` handler forces a fresh layout pass each time, and that
        # is what made dragging a window edge stutter.
        self._inner_h = self.inner.winfo_reqheight()
        self._inner_w = self.inner.winfo_reqwidth()
        self._win_w = 0
        self._win_x = -1
        self.canvas.bind("<Configure>", self._fit)
        self.inner.bind("<Configure>", self._fit)

    def _fit(self, event=None) -> None:
        if self._inner_w < 2:  # content was added after the panel was built
            self._inner_w = max(1, self.inner.winfo_reqwidth())
        if event is not None and event.widget is self.inner:
            self._inner_h = event.height
            # The *requested* width, not the width the item was given: that is
            # the content's own size, which is what the panel centres. Asked
            # only when something other than us moved the frame: asking on every
            # event would force a fresh child layout on each drag step.
            if event.width != self._win_w:
                self._inner_w = max(1, self.inner.winfo_reqwidth())
        w = event.width if event is not None and event.widget is self.canvas else None
        if w is None:
            w = self.canvas.winfo_width()
        if w <= 1:
            w = self._inner_w + 2 * self.pad
        h = self._inner_h + 2 * self.pad
        # Only touch the canvas height when it moved: setting the same height
        # still re-lays out the whole page, and during a width drag it never does.
        if h != self._canvas_h:
            self.canvas.configure(height=h)
            self._canvas_h = h
        # The content keeps its natural width and is *centred* in the panel, so
        # a wider panel moves it and never re-lays it out. Stretching the inner
        # frame to the panel's width on every drag step reflowed every chip in
        # it for nothing: they are a centred row, not a stretching one. It only
        # shrinks when the panel is genuinely too narrow for them.
        content_w = max(1, min(self._inner_w, w - 2 * self.pad))
        if content_w != self._win_w:
            self.canvas.itemconfigure(self._win, width=content_w)
            self._win_w = content_w
        x = max(self.pad, (w - content_w) // 2)
        if x != self._win_x:
            self.canvas.coords(self._win, x, self.pad)
            self._win_x = x
        # Rectangles and cached corner tiles, not a panel-sized raster: a width
        # drag only moves items, so it stays smooth at any size.
        if self._bg.draw(w, h, self.radius, self.fill, self.border, 1):
            self.canvas.tag_lower("bg")


class Btn(tk.Canvas):
    """A rounded button, drawn on a canvas.

    A Tk widget has square corners, so the shape is painted as canvas items —
    antialiased corner tiles over exact straight edges — with the text (and
    optional icon) on top. The `tk.Label`-like surface the game used before is
    kept — `text`, `bg`, `fg`, `set_colors`, `anchor`, an icon with `compound` —
    so no caller cares how it is drawn.

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
        self._cfg_size: tuple[int, int] | None = None
        self._text_item: int | None = None
        self._ink = (0.0, 0.0)
        page = self._parent_bg(parent)
        super().__init__(
            parent, highlightthickness=0, bd=0, bg=page, cursor="hand2", **kw
        )
        self._reqw, self._reqh = self._measure()
        super().configure(width=self._reqw, height=self._reqh)
        self._bg = RoundedRect(self)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._on_configure)
        self._redraw()

    def _on_configure(self, event) -> None:
        """Redraw only on a real size change.

        A resize fires `<Configure>` several times per widget (the geometry
        settles, the scrollbar toggles, the parent re-lays out). Redrawing a
        raster on each was most of the cost of dragging a window edge — and the
        size is the same in nearly all of them."""
        if (event.width, event.height) == self._cfg_size:
            return
        self._cfg_size = (event.width, event.height)
        # A centred label with no icon only has to move: rebuilding the text
        # item (and asking Tk for its ink box) for every drag step is what made
        # the operation tiles and the footer switches cost a frame each.
        if self._text_item is not None and self._image is None:
            w, h = event.width, event.height
            if w > 1 and h > 1:
                fill = self._abg if self._hover else self._fill
                self._bg.draw(w, h, self._corner(w, h), fill)
                dx, dy = self._ink
                self.coords(self._text_item, w / 2 + dx, h / 2 + dy)
                return
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

    def remeasure(self) -> None:
        """Fit the button to its text again, after a named font changed size.

        Tk resizes every label that uses a named font on its own, but a `Btn`
        is a canvas: it measures its text once and keeps that width, so after a
        rescue its request still describes the text at the old size — wide
        enough to push the row it is in onto more lines than it needs.
        """
        reqw, reqh = self._measure()
        if (reqw, reqh) != (self._reqw, self._reqh):
            self._reqw, self._reqh = reqw, reqh
            super().configure(width=reqw, height=reqh)
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
        except tk.TclError, AttributeError:
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
        # The background keeps its items between repaints; only the text and
        # icon are rebuilt. A button stretched by the window (the operation
        # tiles, the footer switches) then costs a few coordinate moves per
        # drag step instead of a fresh raster.
        self.delete("fg")
        self._text_item = None
        self._ink = (0.0, 0.0)
        self._bg.draw(w, h, self._corner(w, h), fill)
        cy = h / 2
        if self._image is not None:
            iw = self._image.width()
            gap = BTN_GAP if self._text else 0
            content = iw + gap + self._font.measure(self._text)
            x = self._padx if self._anchor == "w" else (w - content) / 2
            self.create_image(x, cy, image=self._image, anchor="w", tags="fg")
            self.create_text(
                x + iw + gap,
                cy,
                text=self._text,
                font=self._font,
                fill=fg,
                anchor="w",
                tags="fg",
            )
        elif self._anchor == "w":
            self.create_text(
                self._padx,
                cy,
                text=self._text,
                font=self._font,
                fill=fg,
                anchor="w",
                tags="fg",
            )
        else:
            item = self.create_text(
                w / 2,
                cy,
                text=self._text,
                font=self._font,
                fill=fg,
                anchor="center",
                tags="fg",
            )
            # Anchor "center" centres the text's *layout* box, which leaves a
            # lone glyph like "+" or "−" a pixel or two off: the em box has more
            # ascent than descent, and the glyph does not fill it. Nudge by the
            # ink box so the mark the child sees sits dead in the middle. The
            # nudge is kept, so a resize can move the same item without asking
            # Tk to measure the ink again.
            box = self.bbox(item)
            if box is not None:
                x0, y0, x1, y1 = box
                dx, dy = w / 2 - (x0 + x1) / 2, cy - (y0 + y1) / 2
                self.move(item, dx, dy)
                self._ink = (dx, dy)
            self._text_item = item

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

    Drawn inside the app's own window rather than as a Toplevel. A Toplevel is
    a real window: a tiling window manager tiles it, focuses it, and the app
    behind stops taking clicks. Painting the bubble in the window cannot be
    seen by a window manager at all.
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
        root = self.widget.winfo_toplevel()
        self.tip = tk.Frame(
            root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER
        )
        tk.Label(
            self.tip,
            text=self.text,
            font=self.font,
            bg=SURFACE,
            fg=TEXT,
            padx=10,
            pady=5,
        ).pack()
        self.tip.update_idletasks()
        # Hang it under the widget with the right edges aligned: the buttons it
        # serves live in the top-right corner, so a left-aligned bubble would
        # run off the edge of the window.
        w = self.tip.winfo_reqwidth()
        h = self.tip.winfo_reqheight()
        x = self.widget.winfo_rootx() - root.winfo_rootx() + self.widget.winfo_width()
        x = max(4, min(x - w, root.winfo_width() - w - 4))
        y = (
            self.widget.winfo_rooty()
            - root.winfo_rooty()
            + self.widget.winfo_height()
            + 6
        )
        if y + h > root.winfo_height() - 4:  # no room below: sit above instead
            y = max(4, y - self.widget.winfo_height() - h - 12)
        self.tip.place(x=x, y=y)
        self.tip.lift()

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
        self._rows: list[tuple[Any, Btn]] = []  # (value, button), in list order
        self._index = 0  # the row the arrow keys are on
        self._keys: list[tuple[str, str]] = []  # window bindings while open
        self._all_bindings: list[tuple[str, object]] = []  # app-wide, while open
        self._canvas: tk.Canvas | None = None
        self._view = 0  # the visible height of the list
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
        # Drawn inside this app's own window, not as a Toplevel: a Toplevel is a
        # window, and a tiling window manager (OmniWM on this machine) tiles it
        # like any other, puts it in its own frame and moves the focus to it.
        # The app behind then gets no clicks at all and looks frozen, and the
        # list can be reparented to another workspace mid-click. A Frame that
        # the window paints itself cannot be seen by any window manager.
        root = self.winfo_toplevel()
        top = tk.Frame(
            root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER
        )
        self._pop = top
        canvas = tk.Canvas(top, bg=SURFACE, highlightthickness=0, bd=0)
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
            self._rows.append((v, row))
            here = row if chosen else here

        rows.update_idletasks()
        w = max(rows.winfo_reqwidth(), self.winfo_width())
        h = rows.winfo_reqheight()
        view = max(1, min(h, int(self.winfo_screenheight() * self.MAX_SCREEN)))
        # `yscrollincrement=1`: without it a canvas scrolls a tenth of its own
        # height per unit, which on a list this size is half the popup a nudge.
        canvas.configure(
            width=w, height=view, scrollregion=(0, 0, w, h), yscrollincrement=1
        )
        canvas.itemconfig(window, width=w)  # rows span the full width
        if h > view:
            # A Frame has no bindtags of its own, so every row needs the wheel
            # binding too: a row Label is a child, and events do not bubble.
            def scroll(px: int) -> None:
                canvas.yview_scroll(px, "units")

            for widget in (top, canvas, rows, *rows.winfo_children()):
                bind_wheel(widget, scroll)
            if here is not None:  # open on the month you are on
                canvas.yview_moveto(max(0.0, (here.winfo_y() - view / 2) / h))

        # Hang it under the button in this window's own coordinates, flipped
        # above when the window bottom is closer than the list is tall.
        x = self.winfo_rootx() - root.winfo_rootx()
        y = self.winfo_rooty() - root.winfo_rooty() + self.winfo_height() + 4
        x = max(4, min(x, root.winfo_width() - w - 4))
        if y + view > root.winfo_height() - 4:
            y = max(4, self.winfo_rooty() - root.winfo_rooty() - view - 4)
        top.place(x=x, y=y, width=w, height=view)
        top.lift()
        self._canvas, self._view = canvas, view
        self._index = next(
            (i for i, (v, _) in enumerate(self._rows) if v == self.value), 0
        )

        # Local grab, inside this app only: a click outside the list lands on
        # the list widget and closes it, so it behaves like a menu without
        # asking the window manager for anything.
        # No grab. A local grab rewrites which window a click is delivered to,
        # and on macOS a redirected event reports its root coordinates against
        # the grab window: a click on the far side of the page then looks like a
        # click *on* the list, and the list never closes. Without the grab the
        # click arrives at the widget it was aimed at, with honest coordinates.
        # The cost is that the dismissing click also reaches that widget (a Back
        # button navigates as it closes the list), which is how menus behave.
        for sequence in ("<ButtonPress-1>", "<ButtonRelease-1>"):
            top.bind(sequence, self._click_anywhere)
        canvas.bind("<Button-1>", lambda e: self._close())  # empty space under the rows
        self._bind_open_keys()

    def _bind_open_keys(self) -> None:
        """Arrows, Home/End, Enter and Escape work while the list is open.

        Bound on the window, not on the frame: a Frame does not take keyboard
        focus on macOS, so bindings on it never fire. Every one of them is
        removed again in `_close`, and each returns "break" so the same key
        does not also reach whatever is behind the list.

        The click handler is the "click outside closes it" rule. The grab makes
        an outside click arrive at the frame instead, which the frame's own
        binding already handles; this covers the cases where the grab did not
        take (a click on the title bar, a failed grab).
        """
        root = self.winfo_toplevel()
        for sequence, handler in (
            ("<Up>", lambda e: self._move(-1)),
            ("<Down>", lambda e: self._move(1)),
            ("<Home>", lambda e: self._jump(0)),
            ("<End>", lambda e: self._jump(len(self._rows) - 1)),
            ("<Return>", lambda e: self._take()),
            ("<KP_Enter>", lambda e: self._take()),
        ):
            self._keys.append((sequence, root.bind(sequence, handler, add="+")))
        # `bind_all`, not a binding on the window: the list holds a local grab,
        # and a grab decides which window a click is delivered to, not which
        # widget it happened over. An "all" binding runs whatever that widget
        # turns out to be, so a click anywhere in the app closes the list even
        # when the grab has quietly redirected it. Nothing else in the app binds
        # a global ButtonPress, so removing it here takes nothing with it.
        for sequence in ("<ButtonPress-1>", "<ButtonRelease-1>"):
            self._all_bindings.append((sequence, self._click_anywhere))
            root.bind_all(sequence, self._click_anywhere, add="+")

    def _move(self, step: int) -> str:
        return self._jump(self._index + step)

    def _jump(self, index: int) -> str:
        if not self._rows:
            return "break"
        self._index = max(0, min(len(self._rows) - 1, index))
        for i, (_, row) in enumerate(self._rows):
            row.configure(
                bg=ACCENT if i == self._index else SURFACE,
                fg=INK if i == self._index else TEXT,
            )
        if self._canvas is not None:
            _, row = self._rows[self._index]
            total = max(1, self._canvas.bbox("all")[3])
            self._canvas.yview_moveto(
                max(0.0, (row.winfo_y() - self._view / 2) / total)
            )
        return "break"

    def _take(self) -> str:
        if self._rows:
            self._choose(self._rows[self._index][0])
        return "break"

    def _click_anywhere(self, event) -> None:
        """Close when a press lands anywhere but on the list itself.

        Decided by where the pointer was, not by which widget got the event: the
        list holds a local grab, and a grab hands every click to the grab window
        (this Frame), so `event.widget` says "inside" for a click on the far side
        of the page. That is why an outside click used to leave the list open.
        """
        if self._pop is None:
            return
        if os.environ.get("ALMATHUS_DROPDOWN_DEBUG"):
            print(
                f"[dropdown] press on {event.widget}: {event.x_root},{event.y_root} "
                f"| list {self._pop.winfo_rootx()},{self._pop.winfo_rooty()} "
                f"{self._pop.winfo_width()}x{self._pop.winfo_height()}",
                file=sys.stderr,
                flush=True,
            )
        if self._over_list(event.x_root, event.y_root):
            return
        self._close()

    def _over_list(self, x_root: int, y_root: int) -> bool:
        pop = self._pop
        if pop is None:
            return False
        x, y = pop.winfo_rootx(), pop.winfo_rooty()
        return (
            x <= x_root < x + pop.winfo_width() and y <= y_root < y + pop.winfo_height()
        )

    def _choose(self, value):
        self._close()
        if value != self.value:
            self.value = value
            self.config(text=self._text_for(value))
            self._on_pick(value)

    def _close(self):
        for sequence, funcid in self._keys:
            with contextlib.suppress(tk.TclError):
                self.winfo_toplevel().unbind(sequence, funcid)
        self._keys.clear()
        if self._all_bindings:
            self._all_bindings.clear()
            for sequence in ("<ButtonPress-1>", "<ButtonRelease-1>"):
                with contextlib.suppress(tk.TclError):
                    self.winfo_toplevel().unbind_all(sequence)
        if self._pop:
            with contextlib.suppress(tk.TclError):
                self._pop.grab_release()
            self._pop.destroy()
            self._pop = None
        self._rows = []
        self._index = 0
        self._canvas = None


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
        # the two titles sit level with each other however the fonts scale. The
        # band leaves a clear gap between those headings and the first gridline,
        # so the title is not sitting on top of the plot.
        self.PAD_T = font.metrics("linespace") + 34
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
            8,
            y0 - 18,
            text=T("chart_title"),
            anchor="sw",
            font=self.font,
            fill=ACCENT,
        )
        if self.deck:
            self.create_text(
                x1 + 10,
                y0 - 18,
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


class ResultsTable(tk.Canvas):
    """The results sheet, drawn as canvas items.

    One Label per cell put ~400 widgets on the screen for a normal history, and
    Tk lays every one of them out again each time the table is given a new
    width: opening the progress screen froze for seconds while the columns
    stretched to the window. Canvas items are painted straight onto one widget,
    so the whole sheet — header, stripes, every value — is built and resized
    without a single child widget to measure or move.
    """

    PADX = 14  # px between a cell's text and its column edge
    HAIRLINE = 1  # px under the header, same as the rule the grid drew

    def __init__(
        self,
        parent,
        sessions,
        font: tkfont.Font,
        on_overflow: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(
            parent,
            bg=BG,
            highlightthickness=0,
            bd=0,
            yscrollincrement=1,
            xscrollincrement=1,
        )
        self.font = font
        self.on_overflow = on_overflow
        # Measured once from the font: a resize reuses these numbers instead of
        # asking hundreds of widgets what size they would like to be.
        self.linespace = font.metrics("linespace")
        self.rowh = self.linespace + 14
        self.headh = self.linespace + 16
        self.cols: tuple[tuple[str, Anchor, int], ...] = (
            (T("col_date"), "w", 3),
            (T("col_family"), "w", 4),
            (T("col_time"), "e", 3),
            (T("col_cards"), "e", 2),
            (T("col_note"), "e", 2),
            (T("col_avg"), "e", 4),
            (T("col_difficult"), "w", 6),
        )
        # Newest first: the session that just finished sits right under the
        # header, and the history scrolls away below it.
        self.rows: list[tuple[tuple[str, str], ...]] = []
        for s in reversed(sessions):
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
            # A blank is never a value: the dash stays muted in every column.
            self.rows.append(
                tuple((text, MUTED if text == "—" else fg) for text, fg in cells)
            )
        self.natural: list[int] = [
            max(font.measure(label), *(font.measure(row[i][0]) for row in self.rows))
            + 2 * self.PADX
            for i, (label, _, _) in enumerate(self.cols)
        ]
        self.content_width = float(sum(self.natural))
        self.content_height = self.headh + self.HAIRLINE + len(self.rows) * self.rowh
        self._width = 0
        self.bind("<Configure>", self._resized)
        bind_wheel(self, self._wheel)
        self.bind("<Shift-MouseWheel>", self._xwheel)
        with contextlib.suppress(tk.TclError):
            self.bind("<TouchpadScroll>", self._touchpad_x, add="+")
        self.redraw()

    def redraw(self, width: int | None = None) -> None:
        """Lay the whole sheet out for `width` (default: the widget's own)."""
        view = self.winfo_width() if width is None else width
        self.delete("all")
        widths, total = self._widths(view)
        edges = [0.0]
        for w in widths:
            edges.append(edges[-1] + w)

        def x_of(i: int, anchor: Anchor) -> float:
            return edges[i] + self.PADX if anchor == "w" else edges[i + 1] - self.PADX

        for i, (label, anchor, _) in enumerate(self.cols):
            self.create_text(
                x_of(i, anchor),
                self.headh / 2,
                text=label,
                font=self.font,
                fill=MUTED,
                anchor=anchor,
            )
        self.create_rectangle(
            0, self.headh, total, self.headh + self.HAIRLINE, fill=BORDER, outline=""
        )
        top = self.headh + self.HAIRLINE
        for r, cells in enumerate(self.rows):
            rowtop = top + r * self.rowh
            self.create_rectangle(
                0,
                rowtop,
                total,
                rowtop + self.rowh,
                fill=ROW_ALT if r % 2 else CARD_BG,
                outline="",
            )
            mid = rowtop + self.rowh / 2
            for i, (text, fg) in enumerate(cells):
                self.create_text(
                    x_of(i, self.cols[i][1]),
                    mid,
                    text=text,
                    font=self.font,
                    fill=fg,
                    anchor=self.cols[i][1],
                )
        self.content_width = total
        self.configure(scrollregion=(0, 0, total, self.content_height))

    def _widths(self, view: int) -> tuple[list[float], float]:
        """Column widths for a `view`-wide table: their natural size when the
        columns do not fit (the sideways bar reaches the rest), otherwise their
        natural size plus a share of the spare room, in the columns' weights."""
        natural_total = float(sum(self.natural))
        if view <= natural_total:
            return [float(w) for w in self.natural], natural_total
        spare = view - natural_total
        weights = sum(weight for _, _, weight in self.cols)
        return [
            w + spare * weight / weights
            for w, (_, _, weight) in zip(self.natural, self.cols, strict=True)
        ], float(view)

    def _resized(self, event) -> None:
        if event.width != self._width:
            self._width = event.width
            self.redraw(event.width)
        self._sync_bar()

    def _sync_bar(self) -> None:
        """Tell the screen whether the rows overrun the view, so it can show the
        vertical bar only when there is somewhere to scroll."""
        if self.on_overflow is not None:
            self.on_overflow(self.content_height > self.winfo_height())

    def _wheel(self, pixels: int) -> None:
        # Only scroll when the rows truly overrun the view: a canvas whose
        # content fits still nudges on some Tk builds, which read as scrolling
        # an already-complete table.
        if self.content_height > self.winfo_height():
            self.yview_scroll(pixels, "units")

    def _xwheel(self, ev) -> None:
        if self.content_width > self.winfo_width():
            self.xview_scroll(wheel_pixels(ev), "units")

    def _touchpad_x(self, ev) -> None:
        if self.content_width <= self.winfo_width():
            return
        # dx rides in the high 16 bits, dy in the low (see touchpad_pixels).
        raw = (ev.delta >> 16) & 0xFFFF
        dx = raw if raw < 0x8000 else raw - 0x10000
        self.xview_scroll(-dx, "units")


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


def _svg_image(path: Path, height: int) -> tk.PhotoImage | None:
    """A bundled SVG at ``height`` px, or None when Tk cannot read it.

    Tk 9 rasterizes SVG itself. A missing file, or a Tk built without the SVG
    image format, raises TclError and the caller uses its text fallback.
    """
    try:
        return tk.PhotoImage(file=str(path), format=f"svg -scaletoheight {height}")
    except tk.TclError:
        return None


def flag_image(code: str, height: int) -> tk.PhotoImage | None:
    """A language's flag as a Tk image, or None when the asset is not there.

    Tk 9 reads the SVG; a missing file degrades to plain text with no error.
    """
    lang = languages.BY_CODE.get(code)
    if lang is None or not lang.flag:
        return None
    return _svg_image(ASSETS_DIR / "flags" / f"{lang.flag}.svg", height)


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
        self._label: tk.Label | None = None
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
        self._label = tk.Label(
            self.body,
            text=f"{T('language_downloading')} {lang.endonym} …",
            font=("Helvetica", 13),
            fg=TEXT,
            bg=BG,
        )
        self._label.pack(anchor="w")
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
            text=f"{T('language_error')} {lang.endonym}.",
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
            self.body,
            T("language_retry"),
            command=self._show_list,
            font=("Helvetica", 12),
        ).pack(anchor="w")

    # -- actions ----------------------------------------------------------- #
    def _choose(self, code: str) -> None:
        wants_voice = code in voices.VOICES and not voices.is_installed(code)
        if code in self._installed and not wants_voice:
            self._finish(code)  # already on disk: this is just a switch
            return
        self._download(code)

    def _download(self, code: str) -> None:
        lang = languages.BY_CODE[code]
        self._show_download(lang)
        self.status.config(text=T("language_once"))
        self._polling = True

        def progress(done: int, total: int) -> None:
            self._queue.put(("progress", done, total))

        def work() -> None:
            try:
                languages.download(code, progress=progress)
            except Exception as error:  # noqa: BLE001 - every failure is shown here
                self._queue.put(("error", code, str(error)))
                return
            # The voice is what makes the game sound the same everywhere; a
            # failure to fetch it must not cost the language, which is usable
            # with the operating system's own voice.
            entry = voices.VOICES.get(code)
            if entry is not None and not voices.is_installed(code):
                self._queue.put(("label", T("voice_downloading"), entry.size))
                try:
                    voices.download(code, progress=progress)
                except Exception as error:  # noqa: BLE001 - optional, never fatal
                    print(f"[voice] voice download failed: {error}", file=sys.stderr)
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
                elif kind == "label":
                    if self._label is not None:
                        self._label.config(text=f"{first} …")
                    if self._bar is not None:
                        self._bar.config(maximum=max(1, second), value=0)
                    if self._bytes is not None:
                        self._bytes.config(text="")
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
        write_prefs(voice_skipped=False)  # a language was chosen: ask again if needed
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


def _running_in_app_bundle() -> bool:
    """Is this process the executable inside a real macOS ``.app`` bundle?

    Used to decide whether the Dock-icon workaround applies. A packaged build
    (py2app, PyInstaller, a hand-made bundle) has its own bundle identifier, so
    macOS gives it a normal, single, correct Dock icon that disappears when the
    app quits — exactly what we want, and exactly what the workaround below
    would take away.
    """
    if getattr(sys, "frozen", False):  # PyInstaller and friends
        return True
    # A .app's executable is always Foo.app/Contents/MacOS/foo.
    return ".app/Contents/MacOS/" in sys.executable


def relaunch_as_app_bundle() -> bool:
    """Hand the game off to its own macOS app bundle, if there is one to use.

    Running a bare Tk app from an editor's integrated terminal (Zed, Ghostty...)
    registers it with macOS LaunchServices under the *host* app's bundle,
    because a plain script has no bundle of its own. On macOS 26+ every such run
    leaves a stale Dock tile behind, so a morning of running the game from Zed
    fills the Dock with Zed icons (zed-industries/zed#64810). Changing the
    process's activation policy afterwards does not help: Tk already claimed a
    regular policy inside ``tk.Tk()``, before any of our code runs.

    Launching the sibling ``Almathus.app`` through ``open`` does help: the
    bundle is started by launchd, so it carries its own identity, and the game
    gets one ordinary Dock icon that disappears when it quits.

    Returns True when a bundle took over and this process should stop. Off
    macOS, inside a real bundle, or with no bundle beside the source, it is a
    no-op and the game runs here as usual.
    """
    if sys.platform != "darwin" or _running_in_app_bundle():
        return False
    if getattr(sys, "frozen", False):
        # A frozen build is already the identity macOS should see. Launching
        # anything from here would be a second copy of the same app.
        return False
    if os.environ.get("ALMATHUS_IN_APP") == "1":  # the bundled copy starting up
        return False
    bundle = APP_DIR / "Almathus.app"
    if not bundle.is_dir():
        return False
    try:
        # Fixed argv, only ever the game's own bundle: nothing user-supplied.
        subprocess.Popen(["/usr/bin/open", str(bundle)])  # noqa: S603
    except OSError:
        return False
    return True


def choose_language_at_startup() -> str | None:
    """Ask for a language when no model is installed; a no-op on later launches.

    Runs before the main window exists, so it owns a hidden root that exists
    only to parent the dialog. Choosing installs and saves the model; skipping
    leaves the app fully playable on the keypad.
    """
    if not needs_language_setup():
        return None
    first_run = not installed_languages()
    # The app language is not resolved yet, so the dialog cannot use the usual
    # startup language: a family that removed every model still gets its saved
    # language, while a genuine first run gets English — the one language a
    # picker full of endonyms can rely on being readable.
    global LANG  # noqa: PLW0603 - the one app-wide setting, set for the picker
    LANG = load_language() or "en"
    root = tk.Tk()
    root.withdraw()
    try:
        picker = LanguagePicker(
            root,
            title=T("language_title"),
            intro=T("language_intro"),
            skip_label=T("language_skip"),
        )
        chosen = picker.show()
    finally:
        root.destroy()
    if chosen is None and not first_run:
        # Only the voice was missing and it was declined: remember that, so the
        # question is asked once rather than at every launch.
        write_prefs(voice_skipped=True)
    return chosen


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #


class Almathus(tk.Tk):
    # The language page's widgets and its download queue. Declared here so the
    # helper methods can be typed; they are built by `show_language`.
    _lang_body: tk.Frame
    _lang_status: tk.Label
    _lang_bar: ttk.Progressbar | None
    _lang_bytes: tk.Label | None
    _lang_label: tk.Label | None
    _lang_queue: queue.Queue
    _lang_installed: set[str]
    _lang_images: list[tk.PhotoImage]

    # The named fonts, created by `_make_fonts` and resized by `_scale_fonts`.
    # Declared here because they are created in a loop over `FONT_SPECS`.
    f_giant: tkfont.Font
    f_huge: tkfont.Font
    f_big: tkfont.Font
    f_logo: tkfont.Font
    f_mode: tkfont.Font
    f_title: tkfont.Font
    f_body: tkfont.Font
    f_small: tkfont.Font
    f_tile: tkfont.Font
    f_chip: tkfont.Font
    f_num: tkfont.Font
    f_hint: tkfont.Font

    def __init__(self):
        super().__init__()
        # Rasters belong to the Tcl interpreter that made them: a fresh run may
        # share this module with a torn-down one (the tests do, one window per
        # case), so the cache starts empty rather than handing back a dead image.
        _ROUND_CACHE.clear()
        _CORNER_CACHE.clear()
        apply_theme(load_theme())  # before any widget reads a color
        self.title(T("title"))
        self.configure(bg=BG)
        self.minsize(720, 640)
        # Set before the first font pass: `_scale_fonts` re-renders the menu's
        # drawing, and at that point the menu has not been built yet.
        self._title_icon_label: tk.Label | None = None
        self._pinned_size: tuple[int, int] | None = None
        self._pin_job: str | None = None
        # Maximize to fill the screen as a normal window — NOT macOS' native
        # fullscreen mode (which hides the menu bar and jumps to its own space).
        self._maximize()

    def _page_width(self) -> int:
        """How wide the page will be — known before anything has been laid out.

        The page frame fills the window, so the window's own width is the page's
        width even when the canvas inside it still reports one pixel. Reading the
        canvas instead sizes the page to that pixel, paints it, and lets it jump
        to its real box on the next pass.
        """
        w = self.winfo_width()
        return w if w > 1 else self.winfo_screenwidth()

    def _pin_container(self, event=None) -> None:
        """Hold the page box at the window's size, and keep it there.

        The page box is what the window measures itself against: when it holds
        nothing — between destroying one screen and building the next — the
        request collapses to a single empty frame, and a window that is not
        user-sized follows it down to its minimum. That is what made the whole
        UI jump on a theme flip. Pinned to the window's own size, the request
        never moves, so the window never does either.

        Applied once the drag pauses, not per pixel: re-sizing the box is a
        fresh layout of the whole page, and doing it on every `<Configure>` was
        a measurable share of the cost of dragging an edge. During the drag the
        window is user-sized, so a request that lags by a few pixels changes
        nothing on screen.
        """
        if event is not None and event.widget is not self:
            return
        topbar = getattr(self, "topbar", None)
        top = (
            topbar.winfo_height()
            if topbar is not None and topbar.winfo_manager()
            else 0
        )
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        target = (w, max(1, h - top))
        if target == getattr(self, "_pinned_size", None):
            return
        self._pinned_size = target
        pending = getattr(self, "_pin_job", None)
        if pending is not None:
            self.after_cancel(pending)
        self._pin_job = self.after(120, self._apply_pin)

    def _apply_pin(self) -> None:
        self._pin_job = None
        target = getattr(self, "_pinned_size", None)
        container = getattr(self, "container", None)
        if target is None or container is None or not container.winfo_exists():
            return
        container.configure(width=target[0], height=target[1])

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
        self._countdown_base = 0.0  # perf_counter instant the 3-2-1 count starts at
        self._voice_job = None  # the watchdog that keeps this card's mic armed
        self._card_done = False  # answer committed: ignore anything else on this card
        self._answering = False  # the child has started speaking on this card
        self._fanfare_job = None  # the recap's pending "start the music" timer
        self._pump_job = None  # the worker-thread -> Tk callback pump
        self._stopping = False  # latched on quit: schedule no more timers
        self._voice_mic_bad = False
        self._pill_size: tuple[int, int] | None = None  # cached question-pill size
        # The play screen's "back to the menu" question and the clocks it freezes
        # while it is up. `_play_frame` is the page it covers; `_menu_deferred_advance`
        # remembers a move to the next card that was due when it opened.
        self._play_frame: tk.Frame | None = None
        self._menu_dialog: tk.Frame | None = None
        self._menu_pause: tuple[float, float | None, bool] | None = None
        self._menu_deferred_advance = False
        self._lang_job = None  # the language page's pending download-poll timer
        self._lang_polling = False

        # sound effects (a no-op if no command-line player is installed)
        self.sfx = sfx.Sound(SOUNDS_DIR)

        # voice engines (both degrade gracefully to no-ops)
        self.tts = voicemod.TTS(LANG) if voicemod else None
        self.stt = voicemod.SpeechInput(LANG) if voicemod else None
        self._wire_voice()
        stt_ok = bool(self.stt and self.stt.available())
        self.voice_enabled = tk.BooleanVar(value=stt_ok)
        # Dictation: the question read aloud before the child answers it. Off,
        # the card is only *shown* — and then nothing stands between showing it
        # and playing it, so the clock and the microphone both start at once.
        self.dictation_enabled = tk.BooleanVar(value=load_dictation())

        # thread-safe channel: worker threads (STT/TTS) post callables here and
        # the pump runs them on the Tk main loop — never touch Tk off-thread.
        self._ui_queue: queue.Queue = queue.Queue()
        # Heartbeat for the hang watchdog: the pump stamps this every 40ms while
        # the event loop is alive, so a frozen window can be identified instead
        # of guessed at.
        self._beat = time.monotonic()
        self._modal = False  # a native dialog is open: the loop is meant to wait
        self._pump()

        # The header band (`_header_band`) is measured from the type, so
        # `_scale_fonts` has already set it before the top bar places itself:
        # it needs the band to put its buttons on the same line as the headers.
        self._build_topbar()  # the always-visible language chip
        # The box every screen is built in. Its size is *ours* (propagation is
        # off and `_pin_container` keeps it equal to the window), so that
        # destroying a screen — which happens on every theme flip and every
        # move between screens — cannot change what the window asks to be: the
        # window is not treated as user-sized here, and it would follow that
        # request back to the minimum size, moving the whole UI.
        self.container = tk.Frame(
            self,
            bg=BG,
            width=self.winfo_screenwidth(),
            height=self._usable_height(),
        )
        self.container.pack(fill="both", expand=True)
        self.container.pack_propagate(False)
        self.bind("<Configure>", self._pin_container, add="+")

        self._install_exit_handlers()
        self.show_settings()
        # The window has no frame yet, so the type was sized from the display.
        # Settle it against the frame the window manager hands back: see
        # `_settle_type`. Bounded to the first seconds, so a later resize never
        # resizes the type.
        self._settle_job = None
        self._settled_size = (0, 0)
        self._settle_deadline = time.monotonic() + 4.0
        self.bind("<Configure>", self._schedule_settle, add="+")
        self._schedule_settle()
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
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            sig = getattr(signal, name, None)  # Windows has no SIGHUP at all
            if sig is None:
                continue
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
            "_pin_job",
        ):
            pending = getattr(self, job, None)
            if pending is not None:
                with contextlib.suppress(Exception):
                    self.after_cancel(pending)
                setattr(self, job, None)
        # The player and the voice sidecar are child processes: quitting through
        # os._exit() leaves them orphaned unless they are stopped here.
        self.sfx.stop()
        if self.tts:
            self.tts.close()  # language switch / quit: no orphan sidecar
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
        self._beat = time.monotonic()
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
    # The page's type at the size it was drawn for (a 1440x900 window).
    # `_scale_fonts` applies the display's scale to these, once, so the text
    # fits the screen instead of sitting at whatever size it happened to start
    # at — and stays put while the window is resized.
    FONT_SPECS: ClassVar[dict[str, tuple[int, Literal["normal", "bold"]]]] = {
        "f_giant": (120, "bold"),
        "f_huge": (90, "bold"),
        "f_big": (48, "bold"),
        "f_logo": (36, "bold"),  # the menu's wordmark: between big and title
        "f_mode": (30, "bold"),
        "f_title": (28, "bold"),
        "f_body": (20, "normal"),
        "f_small": (15, "normal"),
        "f_tile": (24, "bold"),
        "f_chip": (14, "bold"),
        "f_num": (20, "bold"),
        "f_hint": (14, "normal"),
    }

    def _make_fonts(self):
        # Created at the design size and then scaled for the display, rather
        # than created already scaled: `_scale_fonts` needs the same base sizes
        # to apply a different share of the design later.
        for name, (size, weight) in self.FONT_SPECS.items():
            setattr(
                self, name, tkfont.Font(family="Helvetica", size=size, weight=weight)
            )
        self._type_scale = 0.0  # nothing applied yet
        self._scale_fonts()

    def _page_scale(self) -> float:
        """The share of the design size this window actually gets.

        The menu was drawn for a 1440x900 window. Both dimensions matter: a
        short window cannot hold the column and a narrow one cannot hold the
        tiles. Capped at 1.0 so a big screen keeps the design size rather than
        stretching it, and floored so the type stays readable — past the floor
        the page scrolls instead of shrinking further.

        Measured from the window, not from the display: `wm_maxsize` answers
        what the window *could* be, and a tiling window manager hands back a
        smaller frame than that (826 px of content on a 900 px screen here). The
        page then laid itself out for a window 40 px taller than the one it had,
        and the settings column overflowed into a scrollbar on a desktop with
        room to spare. Before the window is mapped there is no size to measure,
        so the display stands in and `_fit_type` corrects it once it is.

        Quantized downwards to 1/40th: a font change re-lays out every label
        there is, and rounding up is how a page that just fits ends up 4 px too
        tall. The caller debounces it, so a drag reflows the page continuously
        while the type moves at most one step per settle.
        """
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:  # not mapped yet: the display is all we know
            w, h = self.winfo_screenwidth(), self._usable_height()
        scale = max(SCALE_MIN, min(1.0, w / PAGE_W, h / PAGE_H))
        return math.floor(scale * 40) / 40

    def _settle_type(self) -> None:
        """Size the type to the frame the window manager actually gave us.

        The first pass runs before there is a window, so it can only size the
        type from the display; a tiling window manager hands back a smaller
        frame than that, and the settings column laid out for the display
        overflows into a scrollbar on a desktop with room to spare. This runs
        again when the frame changes, but only in the first seconds of the
        window's life: that is when the window manager places it, and after that
        a resize reflows the page without resizing the type.

        By redrawing the screen, never by patching it: a font change under built
        widgets leaves every canvas button measured for the old size (one
        resize pass emptied half the labels), and re-deciding the type from the
        window on every `<Configure>` is what made a drag collapse the UI.
        """
        self._settle_job = None
        if self._stopping or time.monotonic() > self._settle_deadline:
            return
        size = (self.winfo_width(), self.winfo_height())
        if size == self._settled_size:
            return  # the frame did not move: nothing to settle
        self._settled_size = size
        scale = self._page_scale()
        if scale == self._type_scale:
            return
        self._scale_fonts(scale, remeasure=False)
        # The rebuild below replaces the page's buttons, but the topbar outlives
        # it. Its pills were measured at the pre-settle type, so they would keep
        # the old height and sit a couple of pixels lower than every header's
        # back button. Rebuild the bar at the settled type too.
        self._populate_topbar()
        self.show_settings()  # nothing else can be on screen this early

    def _schedule_settle(self, event=None) -> None:
        """Re-check the type when the window itself changes size, once settled."""
        if event is not None and event.widget is not self:
            return  # every child carries the window in its bindtags
        if self._stopping or time.monotonic() > self._settle_deadline:
            return
        if self._settle_job is not None:
            with contextlib.suppress(tk.TclError):
                self.after_cancel(self._settle_job)
        self._settle_job = self.after(300, self._settle_type)

    def _scale_fonts(self, scale: float | None = None, remeasure: bool = True) -> None:
        """Resize every named font for the display.

        Tk holds its own handle on a named font, so reconfiguring it resizes
        every label that uses it: the whole app fits the display without a
        widget being rebuilt. Run once, at startup: the type belongs to the
        display, not to whatever size the window happens to be.

        The header band and the menu's drawing are measured from the type, so
        they are refreshed with it — the wordmark beside the drawing is a label
        and follows on its own, the drawing does not.
        """
        if scale is None:
            scale = self._page_scale()
        if scale == self._type_scale:
            return
        for name, (size, weight) in self.FONT_SPECS.items():
            font: tkfont.Font = getattr(self, name)
            font.configure(size=max(11, round(size * scale)), weight=weight)
        self._type_scale = scale
        if remeasure:  # a rebuild is cheaper and safer than patching canvases
            self._remeasure_buttons()
        self._header_band = self._measure_header_band()
        self._refresh_title_icon()
        # The question pill is measured from the type, so it is stale now.
        self._pill_size = None
        topbar = getattr(self, "topbar", None)
        if topbar is not None and topbar.winfo_manager():
            self._refresh_lang_chip()  # its flag follows the type too

    def _remeasure_buttons(self, parent: tk.Misc | None = None) -> None:
        """Re-fit every button to the type, at whatever depth it sits.

        Only the canvas buttons cache their text's size; labels and entry fields
        follow the font handle on their own.
        """
        for child in (parent or self).winfo_children():
            if isinstance(child, Btn):
                child.remeasure()
            self._remeasure_buttons(child)

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
        # A language download outlives its screen otherwise, and its poll would
        # touch widgets the next screen has already destroyed.
        self._lang_polling = False
        if self._lang_job is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._lang_job)
            self._lang_job = None
        # The play screen's confirmation goes with the page it covers.
        self._play_frame = None
        self._menu_dialog = None
        self._menu_pause = None
        self._menu_deferred_advance = False
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
        if self.stt:
            self.stt.release()  # off the game: let the mic go (on a worker)
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

        # The canvas window is a holder, not the column itself: a widget cannot
        # be both a canvas item and managed by pack (which is where the column's
        # padding comes from), so the column lives packed inside the holder and
        # the holder is the one placed on the canvas.
        holder = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=holder, anchor="nw")
        # The page is given its final width *before* anything is built into it.
        # Building the column lays the page out (the footer re-lays itself with
        # an update_idletasks), and Tk paints what it finds at that moment: with
        # the holder still at its own natural width, the page is drawn narrow
        # against the left edge and only then widens to the real box — visible,
        # on every rebuild, as the whole UI sliding right.
        page_w = self._page_width()
        box0 = min(page_w, CONTENT_MAX)
        canvas.itemconfigure(window, width=box0)
        canvas.coords(window, (max(page_w - box0, 0) // 2, 0))

        # The last layout written to the canvas: the page box, its x, the
        # holder's height and the scroll region. All of it is ours to remember —
        # Tk's own state (yview, ismapped) only catches up a geometry pass
        # later, and deciding from it is how a bar was once left on screen.
        last: dict[str, object] = {
            "box": -1,
            "x": -1,
            "h": -1,
            "region": None,
            "bar": False,
        }
        # The content's natural height, measured once after the column is built
        # (and on every rebuild). It is not derived from the holder's Configure:
        # we set that height ourselves, so measuring it back would only echo it.
        holder_h = [0]

        def can_scroll() -> bool:
            return holder.winfo_reqheight() > canvas.winfo_height()

        def scroll_by(px: int) -> None:
            """Scroll the menu — but only when the page is taller than the view.

            A gesture on a page with nowhere to go must leave it exactly where
            it is: nudging it by a few pixels for no visible reason is what made
            the menu feel broken."""
            if can_scroll():
                canvas.yview_scroll(px, "units")

        def show_bar():
            # Overlaid, not packed: a packed bar steals 15 px from the canvas,
            # which re-centres the whole page and flips the bar on and off at
            # the threshold — a width wobble on every drag. Lifted so it is not
            # drawn under the canvas it belongs to.
            scrollbar.place(relx=1.0, rely=0.0, anchor="ne", relheight=1.0)
            scrollbar.lift()

        def hide_bar():
            scrollbar.place_forget()

        def fit_scrolled(_event=None):
            """Size the scrolled window to the content (or the view) and show
            the scrollbar only when there is something to scroll. The holder is
            the content's own height; when that fits it sits at the top of the
            view, and when it does not the view scrolls over it. Horizontally it
            is the page box: no wider than CONTENT_MAX, centred, and shrunk to
            the view when the window is narrower."""
            if not canvas.winfo_exists():
                return
            cw, ch = canvas.winfo_width(), canvas.winfo_height()
            if cw <= 1:  # not laid out yet: it will be as wide as the window
                cw = self._page_width()
            box = min(cw, CONTENT_MAX)
            # The holder is kept at the *content's* height, never stretched to
            # the view: stretching it made the measured height equal the view,
            # so a page that fit still looked like it overflowed and the bar
            # stayed on. When it fits it is pinned to the top, not centred: a
            # centred page left a gap under the logo and a matching one below
            # the footer, and the slack read as scrollable.
            #
            # The height is read fresh, not from the cached measurement: the
            # footer can fold onto more rows after the first pass, and a stale
            # height left the new rows outside the scroll region.
            # Measured from the column, not the holder: a Reingold "measure the
            # thing you then resize" loop, where the holder's height was set to
            # the last measurement and read back as the next one. A narrow window
            # (one wrapped row) therefore left the column permanently taller and
            # the scrollbar on a page that fits.
            h = holder.winfo_reqheight() or holder_h[0]
            x = (cw - box) // 2
            if (box, x, h) != (last["box"], last["x"], last["h"]):
                canvas.itemconfigure(window, width=box)
                canvas.coords(window, (x, 0))
                last["box"], last["x"], last["h"] = box, x, h
            # A page with nothing to scroll gets no scroll region at all. An
            # empty region cannot be scrolled, dragged or bounced — not by a
            # gesture taken mid-resize, not by a stale height — so the menu
            # simply stays where it is. The region covers the *view* and not a
            # fixed width: pinned to CONTENT_MAX it made a page with no
            # horizontal content scrollable sideways on any narrower window, and
            # left it off-centre once the window grew past that width.
            #
            # A few pixels of overflow are not worth a scrollbar: the column's
            # own bottom padding absorbs them, and a full-height thumb beside a
            # page that visibly fits reads as a fault. Only a real overflow (a
            # row that would be cut off) gets the bar.
            need_bar = h > ch + FIT_SLACK
            region: tuple[int, int, int, int] | tuple[()] = (
                (0, 0, cw, h) if need_bar else ()
            )
            if region != last["region"]:
                canvas.configure(scrollregion=region)
                last["region"] = region
            # Decide from the measured heights, not from `canvas.yview()`:
            # the view fraction is only recomputed once Tk processes the
            # geometry set just above, so reading it here returns the previous
            # state and the bar would never appear the first time it is needed.
            if need_bar != last["bar"]:
                last["bar"] = need_bar
                if need_bar:
                    show_bar()
                else:
                    hide_bar()
            if not need_bar and canvas.yview()[0] != 0.0:
                canvas.yview_moveto(0)
            scrollbar.set(*canvas.yview())

        # One packed column, top to bottom. Never .place() the content here: a
        # placed frame is not part of the packing flow, so it silently lands on
        # top of whatever comes after it (that's how the voice toggle ended up
        # covering the operation tiles).
        col = tk.Frame(holder, bg=BG)
        col.pack(fill="x", expand=True, padx=24, pady=(6, 16))

        # -- how many cards (one compact toolbar row) ----------------------- #
        self.count_var = tk.IntVar(value=self.n_cards)
        panel = RoundedPanel(col)
        panel.canvas.pack(fill="x", pady=(6, 18))
        bar = tk.Frame(panel.inner, bg=SURFACE)
        bar.pack(pady=(4, 2))
        # Two centred rows, always: the stepper on top and the quick picks
        # below. On one row a narrow window pushed the last picks off the panel
        # edge; folding them only sometimes left the two groups misaligned. Kept
        # apart, the panel reads the same at every width.
        stepper_row = tk.Frame(bar, bg=SURFACE)
        stepper_row.pack()
        stepper: list[tk.Widget] = [
            tk.Label(
                stepper_row, text=T("how_many"), font=self.f_body, fg=MUTED, bg=SURFACE
            )
        ]

        def bump(d):
            self.count_var.set(max(1, min(99, self.count_var.get() + d)))

        stepper.append(
            Btn(
                stepper_row,
                "−",
                command=lambda: bump(-1),
                font=self.f_body,
                bg=CARD_BG,
                fg=TEXT,
                width=1,
                padx=10,
                pady=4,
            )
        )
        stepper.append(
            tk.Label(
                stepper_row,
                textvariable=self.count_var,
                font=self.f_mode,
                fg=ACCENT,
                bg=SURFACE,
                width=3,
            )
        )
        stepper.append(
            Btn(
                stepper_row,
                "+",
                command=lambda: bump(1),
                font=self.f_body,
                bg=CARD_BG,
                fg=TEXT,
                width=1,
                padx=10,
                pady=4,
            )
        )
        stepper[0].pack(side="left", padx=(0, 18))
        for w in stepper[1:]:
            w.pack(side="left", padx=6)

        # quick picks double as a segmented control: the live value stays lit
        chips_row = tk.Frame(bar, bg=SURFACE)
        chips_row.pack(pady=(8, 0))
        chips: dict[int, Btn] = {}
        for q in (5, 10, 20, 30, 50):
            chips[q] = Btn(
                chips_row,
                str(q),
                command=lambda v=q: self.count_var.set(v),
                font=self.f_chip,
                width=3,
                padx=10,
                pady=6,
                active_bg=ACCENT,
                active_fg=INK,
            )
            chips[q].pack(side="left", padx=5)

        def lit(*_):
            v = self.count_var.get()
            for q, c in chips.items():
                c.set_colors(*((ACCENT, INK) if q == v else (CARD_BG, MUTED)))

        self.count_var.trace_add("write", lit)
        lit()

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
        footer.pack(fill="x", pady=(18, 2))

        settle: dict[str, str | None] = {"job": None}  # the follow-up measurement

        def refit(_event=None) -> None:
            """Keep the page box and the scroll region in step with the view.

            A resize reflows the page and nothing else: the type is sized for
            the display when the app starts, so dragging an edge stretches the
            rows and slides the box instead of re-scaling every label and
            re-deciding the spacing. A page too tall for the view scrolls.

            The footer can fold onto more rows *after* the first measurement,
            when the window maps and the real width is known; without this the
            region kept the old, shorter height and the new rows could not be
            scrolled to.
            """
            if not canvas.winfo_exists():
                return
            fit_scrolled()
            holder_h[0] = holder.winfo_reqheight() or holder_h[0]
            # One more measurement once the burst of Configure events is over:
            # the last one arrives before the page has its final height, which
            # left a scroll region on a page that fits (and a missing one on a
            # page that does not).
            if settle["job"] is not None:
                with contextlib.suppress(tk.TclError):
                    canvas.after_cancel(settle["job"])
            settle["job"] = canvas.after(80, settle_fit)

        def settle_fit() -> None:
            settle["job"] = None
            if canvas.winfo_exists():
                fit_scrolled()

        self._build_voice_toggle(footer)
        self._build_dictation_toggle(footer)
        self._build_girly_toggle(footer)
        self._layout_footer(footer, refit)
        # Tk delivers a wheel event to the widget under the pointer and its own
        # bindtags only — it never walks up to a parent — so binding the canvas
        # alone would scroll nothing unless the pointer is on bare canvas.
        # Binding the whole screen means a gesture anywhere — panels, down the
        # gaps, even over the fixed title — reaches the canvas.
        self._bind_wheel_tree(f, scroll_by)
        # Bound here, where both halves of the fit are ready to answer: the
        # canvas' size decides the page box, and it changes on every drag step.
        canvas.bind("<Configure>", refit)
        refit()  # measure now, not only on the first <Configure>

    def _bind_wheel_tree(self, widget, scroll) -> None:
        """Route every wheel gesture anywhere over `widget` to `scroll`.

        Tk delivers a wheel event to the widget under the pointer and its own
        bindtags only — never to a parent — so binding the canvas alone scrolls
        nothing unless the pointer is on bare canvas. Binding each descendant
        too means a gesture over any row still scrolls the view.
        """
        bind_wheel(widget, scroll)
        for child in widget.winfo_children():
            self._bind_wheel_tree(child, scroll)

    def _layout_footer(self, footer, on_relayout=None) -> None:
        """Lay the switches out in as many columns as their labels fit.

        They used to be a packed, content-sized row centred under the panels, so
        they floated in the middle at their own width while everything else met
        the box's edges. Now they divide the box's width in equal columns, and
        the number of columns is chosen from the widest label: three across on a
        roomy screen, two when that still fits each label, one when it does not.
        A fixed two-per-row split clipped the long "Répondre à la voix : ON" and
        "Mode princesse : ON" on a narrow screen; measuring the labels first
        means a button only shares a row when sharing still leaves it room.

        When the last row is short — three switches in two columns — its
        buttons divide the whole row between them instead of leaving a hole.
        Three in two columns gives two half-width switches and one that takes
        the full width; the box is never part-empty. Every place is two grid
        columns wide, so a row of one spans all of them and a row of two splits
        them evenly.

        `on_relayout` is called after the columns change: the screen's height is
        measured once when it is built, and a later fold onto more rows makes
        the content taller — without a nudge the scroll region stayed at the old
        height and the extra rows could not be reached.
        """
        buttons = footer.winfo_children()
        if not buttons:
            return
        pad = 10
        laid: list[int] = [0]  # the per-row count currently on screen
        last_avail = [-1]

        def relayout(_event=None) -> None:
            if not footer.winfo_exists():
                return
            # The column's width, not the footer's: unpacking the switches to
            # re-grid them briefly collapses the footer onto its own request,
            # and reading it back here would decide the layout from a width
            # that exists only mid-rebuild — which re-entered this function
            # until the page had been laid out twenty times for one drag step.
            parent = footer.master
            avail = parent.winfo_width() if parent is not None else 1
            if avail <= 1:  # not mapped yet; the page box is the best estimate
                avail = self._page_room()
            if avail == last_avail[0]:  # nothing to decide for this width
                return
            last_avail[0] = avail
            widest = max(b.winfo_reqwidth() for b in buttons) + 2 * pad
            # Every switch asks for the same width. The grid stretches them to
            # its cells anyway, but Tk only levels its uniform columns when
            # their own content is the same size — with a short switch beside a
            # long one the columns stayed uneven, and the centred row landed a
            # few pixels off the middle of the box.
            for b in buttons:
                if b.winfo_reqwidth() != widest - 2 * pad:
                    b.configure(width=widest - 2 * pad)
            per_row = max(1, min(len(buttons), avail // widest))
            if per_row == laid[0]:
                return
            laid[0] = per_row
            # Pack and grid cannot share a parent, so unpack first; both
            # geometry managers are the frame's own.
            for b in buttons:
                b.pack_forget()
                b.grid_forget()
            # Reset every column, not just the active ones. Lowering the count
            # used to leave the old columns weighted and `uniform`, so the grid
            # stayed three columns wide with one button in it — wider than the
            # footer, which pack then centred and clipped off both edges.
            for c in range(2 * len(buttons)):
                footer.grid_columnconfigure(
                    c,
                    weight=1 if c < 2 * per_row else 0,
                    uniform="foot" if c < 2 * per_row else "",
                )
            for i, b in enumerate(buttons):
                row, place = divmod(i, per_row)
                in_row = min(per_row, len(buttons) - row * per_row)
                # A short run divides the width it has: two switches across
                # three places take half the row each, one takes all of it.
                span = 2 * per_row // in_row
                b.grid(
                    row=row,
                    column=place * span,
                    columnspan=span,
                    sticky="ew",
                    padx=pad,
                    pady=6,
                )
            if on_relayout is not None:
                # Deferred, not called here: measuring the new rows now would
                # need a nested `update_idletasks` from inside the Configure
                # handler that started this, and the layout it forces delivers
                # more Configure events back into this same closure.
                footer.after_idle(on_relayout)

        # Re-decide when the window is resized, not only at build time.
        footer.bind("<Configure>", relayout, add="+")
        relayout()

    def _page_room(self) -> int:
        """Usable width of the page box, less the column's own padding."""
        return max(720, min(self.winfo_width(), CONTENT_MAX)) - 96

    def _build_title(self, parent):
        """'<drawing> Almathus' — the boy normally, the girl in girly mode.

        Kept deliberately small and tight to the top: it is decoration, and every
        pixel it takes is a pixel the menu's options below cannot use. On a short
        window that is the difference between seeing them all and scrolling.

        The wordmark is centred on the drawing, and the pair is centred in the
        page: two marks read as one lockup, whereas a name aligned to the
        drawing's top edge sat up beside the character's head and the pair
        looked off-centre even though it was not."""
        row = tk.Frame(parent, bg=BG)
        row.pack()
        font = self.f_logo
        icon = self._title_image(font)
        self._title_icon_label = None
        if icon is not None:
            # Tk keeps no reference of its own to an image: drop ours and the
            # label goes blank the moment the garbage collector runs.
            self._title_icon = icon
            self._title_icon_label = tk.Label(row, image=icon, bg=BG)
            self._title_icon_label.pack(side="left", padx=(0, 10), anchor="center")
        else:
            tk.Label(row, text=TITLE_ICON, font=font, fg=ACCENT, bg=BG).pack(
                side="left", padx=(0, 8), anchor="center"
            )
        tk.Label(row, text="Almathus", font=font, fg=ACCENT, bg=BG).pack(
            side="left", anchor="center"
        )

    def _refresh_title_icon(self) -> None:
        """Re-render the drawing at the size the type has now.

        The picture is rasterized from the SVG at a fixed height when the page
        is built, so unlike the wordmark beside it — a label, which follows its
        font on its own — it does not follow a font change."""
        label = self._title_icon_label
        if label is None or not label.winfo_exists():
            return
        icon = self._title_image(self.f_logo)
        if icon is None:
            return
        self._title_icon = icon  # Tk keeps no reference of its own
        label.configure(image=icon)

    def _title_image(self, font):
        """This theme's drawing, rendered to about 1.6 lines of the title font.

        Tk 9 rasterizes SVG itself and scales to a target height on the way in,
        so the drawing follows the window instead of being resampled in Tk.
        That is why the assets are SVG, not PNG: Tk's bitmap scaler only does
        integer steps.

        Rasterizing at the *display* size is the catch: a hairline does not
        survive it. At 6 units of the 512 canvas the badge's outline is 1.03 px
        at the ~88 px this draws, and one pixel in five of it came out pale or
        missing — the dotted rim this was reported as. Tk antialiases properly
        (its rim matches Quick Look's pixel for pixel), there is simply nothing
        to blend inside a single pixel, so `assets/*.svg` carry a 10-unit ring:
        1.7 px here, the narrowest that leaves no gaps at the design size.

        A missing file, or a Tk without the SVG image format, returns None and
        the caller falls back to the emoji, so a bad asset never breaks the
        screen."""
        height = round(font.metrics("linespace") * 1.6)
        return _svg_image(ASSETS_DIR / TITLE_SVG, height)

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
        self._pin_container()

    def _hide_topbar(self) -> None:
        if self.topbar.winfo_manager():
            self.topbar.pack_forget()
        self._pin_container()

    def _build_topbar(self) -> None:
        """The menu's slim bar: stats on the left, language on the right.

        Named by an icon so a child reads them at a glance: a chart for the
        stats, the country's own flag for the language. The two buttons are
        placed rather than packed so they can take exactly the edge inset the
        page headers use; the bar is its own frame with nothing but these two
        buttons, so they cannot float over the screen the way a placed settings
        tile once did.
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
            font=self.f_small,
            bg=SURFACE,
            fg=TEXT,
            padx=14,
            pady=7,
        )
        self._chip_image: tk.PhotoImage | None = None
        self._lang_btn = Btn(
            self.topbar,
            "",
            command=self.show_language,
            font=self.f_small,
            bg=SURFACE,
            fg=TEXT,
            padx=14,
            pady=7,
            compound="left",
        )
        self._refresh_lang_chip()
        self._place_topbar()

    def _place_topbar(self, _event=None) -> None:
        """Put the two buttons exactly where a page header puts its pills.

        Same edge inset and same top offset as the header's Retour and PDF, so
        the navigation stays put — sideways as well as vertically — when the
        child moves between the menu and the progress or language screens.
        """
        btn = getattr(self, "_progress_btn", None)
        if btn is None or not btn.winfo_exists():
            return
        w = self.topbar.winfo_width()
        if w <= 1:  # not mapped yet (or hidden); re-placed on <Configure>
            return
        inset = 28  # the header bars' own padx
        pill_h = max(
            self._progress_btn.winfo_reqheight(),
            self._lang_btn.winfo_reqheight(),
        )
        # 18px is the header bars' top pad; the extra centres the pill against
        # the shared title band, which is taller (the progress title's emoji).
        pad_top = 18 + max(0, (self._header_band - pill_h) // 2)
        height = pill_h + pad_top
        if self.topbar.winfo_height() != height:
            self.topbar.configure(height=height)
        self._progress_btn.place(x=inset, y=pad_top)
        self._lang_btn.place(x=w - inset - self._lang_btn.winfo_reqwidth(), y=pad_top)
        # The bar's height is part of the window's size request, so the page box
        # is re-pinned against it whenever the bar decides how tall it is.
        self._pin_container()

    def _refresh_lang_chip(self) -> None:
        """Redraw the chip as flag + language name for the current language."""
        lang = languages.BY_CODE.get(LANG)
        name = lang.endonym if lang is not None else LANG
        # The flag follows the type, so the chip reads as one pill at any size.
        self._chip_image = flag_image(LANG, max(12, round(18 * self._type_scale)))
        self._lang_btn.config(image=self._chip_image, text=name)
        self._place_topbar()

    def _measure_header_band(self) -> int:
        """Height of a page header's content row.

        The tallest thing in a header is its title, so that is the band the
        pills centre against. Measured from the progress title once, at startup,
        because it carries an emoji that lifts the line above the font's own
        line height — the number the top bar needs to place its buttons on the
        same line the page headers will use.
        """
        probe = tk.Label(self, text=T("progress_title"), font=self.f_title)
        probe.update_idletasks()
        band = probe.winfo_reqheight()
        probe.destroy()
        return band

    def _size_header_bar(self, bar: tk.Frame) -> None:
        """Give a page header the shared band height so its pills sit at the
        same y as the top bar's, whichever page is showing."""
        bar.configure(height=self._header_band)
        bar.pack_propagate(False)
        bar.grid_propagate(False)  # a header laid out with grid keeps the band too

    def show_language(self) -> None:
        """The app-language page — a screen of its own, not a popup.

        Choosing a language is a settings decision, and a modal over the menu
        covers the very screen whose look it will change. It is built like the
        stats page: header with Retour, then a scrollable list.
        """
        self._clear()
        self._hush()
        if self.stt:
            self.stt.release()
        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(18, 6))
        self._size_header_bar(bar)
        # Title centred, back button on its left — the same header shape as the
        # stats page.
        bar.columnconfigure(0, weight=1, uniform="side")
        bar.columnconfigure(1, weight=0)
        bar.columnconfigure(2, weight=1, uniform="side")
        Btn(
            bar,
            T("back"),
            command=self.show_settings,
            font=self.f_small,
            bg=CARD_BG,
            fg=TEXT,
            padx=14,
            pady=7,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            bar, text=T("language_title"), font=self.f_title, fg=ACCENT, bg=BG
        ).grid(row=0, column=1)

        tk.Label(
            f,
            text=T("language_intro"),
            font=self.f_body,
            fg=MUTED,
            bg=BG,
            justify="left",
            anchor="w",
            wraplength=760,
        ).pack(fill="x", padx=28, pady=(2, 10))

        self._lang_body = tk.Frame(f, bg=BG)
        self._lang_body.pack(fill="both", expand=True, padx=28, pady=(0, 6))
        self._lang_status = tk.Label(
            f, text="", font=self.f_small, fg=MUTED, bg=BG, anchor="w"
        )
        self._lang_status.pack(fill="x", padx=28, pady=(2, 16))

        self._lang_images: list[tk.PhotoImage] = []
        self._lang_installed = set(languages.installed())
        self._lang_queue = queue.Queue()
        self._lang_polling = False
        self._lang_job = None
        self._lang_bar = None
        self._lang_bytes = None
        self._lang_label = None
        self._show_language_list()

    def _show_language_list(self) -> None:
        for w in self._lang_body.winfo_children():
            w.destroy()
        self._lang_status.config(text="")
        wrap = tk.Frame(self._lang_body, bg=BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0, yscrollincrement=1)
        scroll = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        inner.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        bind_wheel(canvas, lambda px: canvas.yview_scroll(px, "units"))
        for lang in ordered_languages(LANG):
            label = f"{lang.endonym}   ·   {lang.english}"
            if lang.code in self._lang_installed:
                label += "   ✓"
            opts: dict = {}
            image = flag_image(lang.code, 22)
            if image is not None:
                self._lang_images.append(image)
                opts = {"image": image, "compound": "left"}
            Btn(
                inner,
                label,
                command=lambda code=lang.code: self._choose_language(code),
                anchor="w",
                padx=16,
                pady=12,
                font=self.f_body,
                **opts,
            ).pack(fill="x", pady=3)
        # The list is one row button per language, so the pointer is nearly
        # always over a button and the canvas binding above would never fire.
        # Every row routes its wheel events to the canvas too.
        self._bind_wheel_tree(inner, lambda px: canvas.yview_scroll(px, "units"))

    def _choose_language(self, code: str) -> None:
        # A language installed before voices existed has its Vosk model but no
        # voice yet; the same download screen fetches just the missing part.
        wants_voice = code in voices.VOICES and not voices.is_installed(code)
        if code not in self._lang_installed or wants_voice:
            self._download_language(code)
        elif code != LANG:
            self._apply_language(code)
        else:
            self.show_settings()

    def _download_language(self, code: str) -> None:
        lang = languages.BY_CODE[code]
        for w in self._lang_body.winfo_children():
            w.destroy()
        self._lang_label = tk.Label(
            self._lang_body,
            text=f"{T('language_downloading')} {lang.endonym} …",
            font=self.f_body,
            fg=TEXT,
            bg=BG,
        )
        self._lang_label.pack(anchor="w", pady=(6, 10))
        self._lang_bar = ttk.Progressbar(
            self._lang_body, mode="determinate", maximum=max(1, lang.size), length=520
        )
        self._lang_bar.pack(fill="x")
        self._lang_bytes = tk.Label(
            self._lang_body, text="", font=self.f_small, fg=MUTED, bg=BG
        )
        self._lang_bytes.pack(anchor="w", pady=(6, 0))
        self._lang_status.config(text=T("language_once"))
        self._lang_polling = True

        def progress(done: int, total: int) -> None:
            self._lang_queue.put(("progress", done, total))

        def work() -> None:
            try:
                languages.download(code, progress=progress)
            except Exception as error:  # noqa: BLE001 - every failure is shown here
                self._lang_queue.put(("error", code, str(error)))
                return
            # The voice is what makes the game sound the same on every machine,
            # but it is not what makes it playable: if it cannot be fetched, the
            # language still starts and the OS voice stands in for it.
            entry = voices.VOICES.get(code)
            if entry is not None and not voices.is_installed(code):
                self._lang_queue.put(("label", T("voice_downloading"), entry.size))
                try:
                    voices.download(code, progress=progress)
                except Exception as error:  # noqa: BLE001 - optional, never fatal
                    print(f"[voice] voice download failed: {error}", file=sys.stderr)
            self._lang_queue.put(("done", code, None))

        threading.Thread(target=work, daemon=True).start()
        self._poll_language()

    def _poll_language(self) -> None:
        self._lang_job = None
        if not self._lang_polling:
            return
        try:
            while True:
                kind, first, second = self._lang_queue.get_nowait()
                if kind == "progress":
                    if self._lang_bar is not None and self._lang_bytes is not None:
                        self._lang_bar.config(value=first)
                        self._lang_bytes.config(
                            text=f"{first / 1e6:.1f} / {second / 1e6:.1f} MB"
                        )
                elif kind == "label":
                    # The voice is a second download on the same bar; its size
                    # is the new maximum, not the model archive's.
                    if self._lang_label is not None:
                        self._lang_label.config(text=f"{first} …")
                    if self._lang_bar is not None:
                        self._lang_bar.config(maximum=max(1, second), value=0)
                    if self._lang_bytes is not None:
                        self._lang_bytes.config(text="")
                elif kind == "done":
                    self._lang_installed.add(first)
                    self._apply_language(first)
                    return
                else:  # "error"
                    self._lang_polling = False
                    self._show_language_error(languages.BY_CODE[first], second)
                    return
        except queue.Empty:
            pass
        if self._lang_polling:
            self._lang_job = self.after(_DOWNLOAD_POLL_MS, self._poll_language)

    def _show_language_error(self, lang: languages.Language, message: str) -> None:
        for w in self._lang_body.winfo_children():
            w.destroy()
        tk.Label(
            self._lang_body,
            text=f"{T('language_error')} {lang.endonym}.",
            font=self.f_body,
            fg=TEXT,
            bg=BG,
        ).pack(anchor="w", pady=(6, 4))
        tk.Label(
            self._lang_body,
            text=message,
            font=self.f_small,
            fg=MUTED,
            bg=BG,
            wraplength=620,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))
        Btn(
            self._lang_body,
            T("language_retry"),
            command=self._show_language_list,
            font=self.f_body,
            bg=CARD_BG,
            fg=TEXT,
        ).pack(anchor="w")

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
        if self.tts:
            self.tts.close()  # the old language's sidecar goes with its voice
        self.tts = voicemod.TTS(LANG) if voicemod else None
        self.stt = voicemod.SpeechInput(LANG) if voicemod else None
        self._wire_voice()
        self.voice_enabled.set(bool(self.stt and self.stt.available()))
        # The whole bar, not just the language chip: the stats pill outlives
        # every screen and carries its label, which is in the old language until
        # the button is rebuilt. Rebuilding costs two buttons and keeps every
        # long-lived label in step with LANG.
        self._populate_topbar()
        self.show_settings()
        if self.voice_enabled.get():
            self.after(700, self._calibrate_mic)

    def _wire_voice(self):
        """Point the voice at the microphone, so the mic can cancel it out.

        The speakers and the mic share a room: what the voice plays comes back
        into the capture, which is why the mic used to wait for the reading to
        end. These two hooks hand the exact phrase to the speech input before it
        is audible and report how much of it was actually played, so the capture
        can subtract it and hear the child over the question.
        """
        if self.tts is not None and self.stt is not None:
            self.tts.on_play = self.stt.set_echo
            self.tts.on_played = self.stt.played

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
        self._answering = False
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
        # The menu button takes the same corner as on the card that follows, so
        # a wrong setting caught at the reminder is one tap from the menu. The
        # game has not been dealt or timed yet, so this leaves without asking.
        head = tk.Frame(f, bg=BG)
        head.pack(fill="x", padx=28, pady=(18, 0))
        self._size_header_bar(head)  # the band every screen's pills centre on
        head.columnconfigure(0, weight=1, uniform="side")
        head.columnconfigure(1, weight=0)
        head.columnconfigure(2, weight=1, uniform="side")
        Btn(
            head,
            T("menu"),
            command=self.show_settings,
            font=self.f_small,
            bg=CARD_BG,
            fg=TEXT,
            padx=14,
            pady=7,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            head, text=T("articulate_tip_title"), font=self.f_title, fg=ACCENT, bg=BG
        ).grid(row=0, column=1)
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
        ).pack(padx=40, pady=(30, 0))
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
        # matter of making the shorter one wait for the longer. This sample
        # starts on its first beep, so its GO! is exactly three ticks in and the
        # lead is zero. A sample that opens on silence pushes COUNTDOWN_GO_AT
        # past three ticks and makes the picture wait instead; neither case needs
        # its own branch.
        lead = COUNTDOWN_STEPS * COUNTDOWN_TICK - COUNTDOWN_GO_AT
        # The settings screen must not sit there through the wait, whichever way
        # it falls: the game has begun, the count just has not caught up yet.
        self._clear()
        # One fixed instant for the whole count. Every number is scheduled from
        # it, never from the previous number's repaint, so a slow frame cannot
        # push the rest of the 3-2-1-GO behind the sample's beeps.
        self._countdown_base = time.perf_counter() + max(0, -lead) / 1000
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
            # The n-th number is due n ticks after the count began; measuring
            # from that shared instant keeps the grid absolute instead of
            # letting each repaint's own milliseconds add to the next delay.
            due = self._countdown_base + (COUNTDOWN_STEPS - n + 1) * (
                COUNTDOWN_TICK / 1000
            )
            self.after(
                max(0, round((due - time.perf_counter()) * 1000)),
                lambda: self.countdown(n - 1),
            )
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
        self._answering = False
        f = self._play_frame = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        voice_on = bool(self.voice_enabled.get() and self.stt and self.stt.available())
        dictation_on = bool(
            self.dictation_enabled.get() and self.tts and self.tts.available()
        )

        # header (pinned top). The menu button sits in the same left corner as
        # the back button on every other screen, so leaving is always in the
        # place the parent already reaches for. A started game is not saved, so
        # the button asks before it drops one.
        header = (
            T("retry_round")
            if in_retry
            else T("card_of", n=min(self.idx + 1, self.n_cards), total=self.n_cards)
        )
        head = tk.Frame(f, bg=BG)
        head.pack(side="top", fill="x", padx=28, pady=(18, 0))
        self._size_header_bar(head)  # the band every screen's pills centre on
        head.columnconfigure(0, weight=1, uniform="side")
        head.columnconfigure(1, weight=0)
        head.columnconfigure(2, weight=1, uniform="side")
        Btn(
            head,
            T("menu"),
            command=self._ask_leave_game,
            font=self.f_small,
            bg=CARD_BG,
            fg=TEXT,
            padx=14,
            pady=7,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            head,
            text=header,
            font=self.f_title,
            fg=ACCENT if in_retry else MUTED,
            bg=BG,
        ).grid(row=0, column=1)

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
        # With dictation on the mic is armed *before* the reading, not after it:
        # the voice's echo is cancelled out of the capture, so a child who
        # answers over the question is heard, and the reading stops for them
        # (see `_voice_status`).
        if voice_on:
            self._voice_arm(card)
            if dictation_on:
                self._speak_operation(card, then=lambda: self._after_question(card))
            self._voice_watch(card)
        elif dictation_on:
            self._speak_operation(card)

    def _ask_leave_game(self) -> None:
        """Ask before dropping the game in progress and going back to the menu.

        The game is only written down at the recap, so a tap here loses it. The
        button sits on a screen a child taps all the time, which is why it asks.
        The question is drawn inside the page, never in a Toplevel: a tiling
        window manager would give that its own frame and take the focus.
        """
        if self._menu_dialog is not None:
            return
        page = self._play_frame
        if page is None or not page.winfo_exists():
            return
        # The clocks stop while the question is up: the confirmation is not the
        # child thinking, and a parent may take a while over it. `_menu_pause`
        # carries what reopening the clocks needs to give the time back.
        self._menu_pause = (
            time.perf_counter(),
            self.card_start,
            self._speech_start is not None,
        )
        if self._tick_job is not None:
            self.after_cancel(self._tick_job)
            self._tick_job = None
        if self._advance_job is not None:
            # An answer was just committed: the move to the next card must not
            # fire behind the question, or it paints it over the menu.
            self.after_cancel(self._advance_job)
            self._advance_job = None
            self._menu_deferred_advance = True
        if self.stt:
            self.stt.cancel()  # a number heard here is not an answer

        self._menu_dialog = tk.Frame(page, bg=BG)
        self._menu_dialog.place(relx=0, rely=0, relwidth=1, relheight=1)
        box = tk.Frame(self._menu_dialog, bg=BG)
        box.place(relx=0.5, rely=0.45, anchor="center")
        panel = RoundedPanel(box, pad=24, radius=20)
        # The panel centres its content but never sets its own width, and a
        # canvas' default is narrower than this sentence: with no width given,
        # the label is clipped at both ends instead of read.
        panel.canvas.configure(width=520)
        panel.canvas.pack()
        inner = panel.inner
        tk.Label(
            inner, text=T("leave_title"), font=self.f_title, fg=TEXT, bg=SURFACE
        ).pack(pady=(4, 8))
        tk.Label(
            inner,
            text=T("leave_body"),
            font=self.f_body,
            fg=MUTED,
            bg=SURFACE,
            wraplength=420,
            justify="center",
        ).pack(padx=8, pady=(0, 18))
        row = tk.Frame(inner, bg=SURFACE)
        row.pack()
        Btn(
            row,
            T("leave_stay"),
            command=self._close_leave_dialog,
            font=self.f_body,
            bg=GOOD,
            fg=INK,
            padx=22,
            pady=10,
        ).pack(side="left", padx=8)
        Btn(
            row,
            T("leave_confirm"),
            command=self._leave_game,
            font=self.f_body,
            bg=BG,  # CARD_BG all but vanishes on the panel in the dark theme
            fg=TEXT,
            active_bg=BAD,
            padx=22,
            pady=10,
        ).pack(side="left", padx=8)

    def _close_leave_dialog(self) -> None:
        """Keep playing: drop the question and restart the clocks it stopped."""
        dialog = self._menu_dialog
        self._menu_dialog = None
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        pause = self._menu_pause
        self._menu_pause = None
        if pause is not None:
            started, card_start, reading = pause
            paused = time.perf_counter() - started
            # A reading still in progress is banked as read time, so its pause
            # is already out of the total; only a running clock is given back.
            if not reading:
                self.game_start += paused
                if card_start is not None and self.card_start is not None:
                    self.card_start = card_start + paused
        if self._menu_deferred_advance:
            self._menu_deferred_advance = False
            self._advance_after(650)  # the feedback was on screen before the ask
            return
        self._tick()
        card = self.current
        if card is not None and not self._card_done:
            self._voice_arm(card)

    def _leave_game(self) -> None:
        """Go back to the menu and drop the game, recording nothing."""
        dialog = self._menu_dialog
        self._menu_dialog = None
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        if self._advance_job is not None:
            self.after_cancel(self._advance_job)
            self._advance_job = None
        self._menu_pause = None
        self._menu_deferred_advance = False
        # Any late voice result or timer now finds no card to work on. The
        # results log is only written by the recap, which this path never runs.
        self.current = None
        self.show_settings()

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
        word = spoken_words(LANG)[card.mode]
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
        """The question has finished being read: cue GO!, and keep listening."""
        if self.current is not card:
            return  # the reading was cut off by the next card; nothing to cue
        if not self._answering:
            # A child who started speaking over the question does not need to be
            # told it is their turn; the flash would land over their answer.
            self._show_go_hint(card)
        # With the question cancelled out of the mic, the capture that is running
        # has been listening to the child the whole time and is kept. Without it
        # the capture is full of the reading, so it is dropped and a clean one
        # starts now — exactly the behaviour from before early arming.
        if self.stt and not self.stt.echo_was_cancelled():
            self.stt.cancel()
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
        if self._menu_dialog is not None:
            return  # the leave question is up; nothing is being answered
        if self.current is not target or self._voice_mic_bad or self._card_done:
            return
        if self.tts and self.tts.speaking() and not self.stt.echo_was_cancelled():
            # The reading is playing and its echo is not being taken out, so a
            # capture now would only hear the question. `_after_question` arms
            # the mic the moment the reading ends.
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
        # The reading no longer holds the mic back: the question is cancelled out
        # of the capture, so listening runs alongside it.
        if (
            self._menu_dialog is None
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
        elif state == "speech":
            # The child has started answering, possibly over the reading: stop
            # talking and get out of their way. The answer itself arrives from
            # the capture, which is what the echo guard exists for.
            self._answering = True
            if self.tts:
                self.tts.stop()
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
        if self._menu_dialog is not None:  # the leave question is up
            return
        if self.current is not target or self._card_done:  # card already over
            return
        if n is not None:
            if self.stt and self.stt.echo_doubtful():
                # This capture listened to the question and never cancelled it,
                # so the number in it may be ours, not the child's. Keep
                # listening; a clean capture follows the reading.
                self._voice_arm(target)
                return
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
        if self._menu_dialog is not None:
            return  # the leave question is up; the card is not being answered
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
        if self.stt:
            self.stt.release()
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

        # The action row is anchored first so the result block below it is
        # centred in whatever height is left — the buttons are the fixed base
        # and the score is the thing that breathes.
        btns = tk.Frame(f, bg=BG)
        btns.pack(side="bottom", pady=(0, 28))
        Btn(
            btns,
            T("again"),
            command=self.show_settings,
            font=self.f_body,
            bg=GOOD,
            fg=INK,
            padx=26,
            pady=12,
        ).pack(side="left", padx=10)
        Btn(
            btns,
            T("progress"),
            command=self.show_progress,
            font=self.f_body,
            bg=CARD_BG,
            fg=TEXT,
            padx=26,
            pady=12,
        ).pack(side="left", padx=10)
        Btn(
            btns,
            T("quit"),
            command=self._on_quit,
            font=self.f_body,
            bg=CARD_BG,
            fg=TEXT,
            active_bg=BAD,
            padx=26,
            pady=12,
        ).pack(side="left", padx=10)

        col = tk.Frame(f, bg=BG)
        col.pack(expand=True)

        # One scale of spacing between the blocks, and a size step per level of
        # importance: the face, the cheer, the score, the detail.
        tk.Label(col, text=emoji, font=self.f_huge, bg=BG, fg=ACCENT).pack()
        tk.Label(col, text=msg, font=self.f_big, fg=TEXT, bg=BG).pack(pady=(2, 14))
        tk.Label(
            col,
            text=f"{T('score')} : {correct}/{n}  ({pct:.0f}%)",
            font=self.f_title,
            fg=GOOD if pct >= 80 else ACCENT,
            bg=BG,
        ).pack(pady=(0, 6))
        tk.Label(
            col,
            text=f"{T('total_time')} : {self._fmt(total_time)}",
            font=self.f_body,
            fg=MUTED,
            bg=BG,
        ).pack(pady=1)
        tk.Label(
            col, text=f"{T('avg_time')} : {avg:.1f}s", font=self.f_body, fg=MUTED, bg=BG
        ).pack()

        # slowest / struggled operations, for reinforcement — a rounded card
        # like everything else, with the three columns on a grid so the times
        # line up instead of being spaced by hand.
        slow = sorted(cards, key=lambda c: c.time_taken, reverse=True)[:5]
        if slow:
            tk.Label(col, text=T("slowest"), font=self.f_body, fg=ACCENT, bg=BG).pack(
                pady=(22, 8)
            )
            panel = RoundedPanel(col, pad=14, radius=16)
            panel.canvas.pack()
            rows = tk.Frame(panel.inner, bg=SURFACE)
            rows.pack()
            for r, c in enumerate(slow):
                mark = "✅" if c.correct_first_try else "❌"
                tk.Label(rows, text=mark, font=self.f_body, bg=SURFACE).grid(
                    row=r, column=0, padx=(2, 10), pady=4
                )
                tk.Label(
                    rows,
                    text=f"{c.text} = {c.answer}",
                    font=self.f_body,
                    fg=TEXT,
                    bg=SURFACE,
                    anchor="w",
                ).grid(row=r, column=1, sticky="w", pady=4)
                tk.Label(
                    rows,
                    text=f"{c.time_taken:.1f}s",
                    font=self.f_body,
                    fg=MUTED,
                    bg=SURFACE,
                    anchor="e",
                ).grid(row=r, column=2, sticky="e", padx=(24, 2), pady=4)

        # The winner's music, once — then, over the silence it leaves, the same
        # encouragement that is on screen, spoken in a warm voice. Chained rather
        # than timed: the two would talk over each other, and a child who taps
        # "Rejouer" mid-fanfare gets neither (a cut-short sound skips its
        # callback), which is exactly right — that game is over.
        say = None
        if self.tts and self.tts.available():
            # The verdict word above it is an English fallback for every language
            # but French, and reading it in front of a local sentence was the one
            # English word left in the recap. fr/en keep it because their screens
            # are translated; the others say the whole sentence in their language.
            score = spoken_score(LANG, correct, n)
            spoken = f"{msg} {score}" if LANG in ("fr", "en") else score

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
        if self.stt:
            self.stt.release()
        sessions = resultslog.load_sessions()

        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        # Title centred, with the back button on its left — the same place it
        # holds on every other screen. The month filter and the PDF report share
        # the row beneath (filter left, report right), and the chart legend sits
        # under that with the session count against the right.
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(18, 6))
        title_row = tk.Frame(bar, bg=BG)
        title_row.pack(fill="x")
        # Equal side columns keep the title centred with the back button in the
        # left one, whatever the button's width.
        title_row.columnconfigure(0, weight=1, uniform="side")
        title_row.columnconfigure(1, weight=0)
        title_row.columnconfigure(2, weight=1, uniform="side")
        Btn(
            title_row,
            T("back"),
            command=self.show_settings,
            font=self.f_small,
            bg=CARD_BG,
            fg=TEXT,
            padx=14,
            pady=7,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            title_row, text=T("progress_title"), font=self.f_title, fg=ACCENT, bg=BG
        ).grid(row=0, column=1)

        btn_row = tk.Frame(bar, bg=BG)
        btn_row.pack(fill="x", pady=(8, 0))
        legend_row = tk.Frame(bar, bg=BG)
        legend_row.pack(fill="x", pady=(8, 0))

        # Reconfigured on every filter change: the count belongs to what is shown.
        self._n_label = tk.Label(
            legend_row,
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

        # Export lives against the right edge of the controls line.
        pdf_btn = Btn(
            btn_row,
            T("pdf_button"),
            command=self._export_pdf,
            font=self.f_small,
            bg=CARD_BG,
            fg=MUTED,
            padx=14,
            pady=7,
        )
        pdf_btn.pack(side="right")
        Tooltip(pdf_btn, T("pdf_tip"), self.f_hint)
        self._pdf_status = tk.Label(
            btn_row, text="", font=self.f_small, fg=MUTED, bg=BG
        )
        self._pdf_status.pack(side="right", padx=(0, 12))

        # The month filter, on the controls line. Every month the log covers,
        # newest first, plus the current one even when it is still empty — a
        # parent opening the screen on the 1st wants to see that this month has
        # begun. "Tous les mois" stays at the top: it is the whole history, the
        # view this screen had before there was anything to filter.
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
            btn_row,
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

        # The chart's key, on its own line under the controls: which colour is
        # which curve. The grade key only appears while the month on show has a
        # grade to draw, so a legend never promises a line that is not there.
        legend = tk.Frame(legend_row, bg=BG)
        legend.pack(side="left")
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
        # The native save sheet stops the Tk loop on purpose: tell the hang
        # watchdog that a silent event loop here is expected.
        self._modal = True
        try:
            path = filedialog.asksaveasfilename(
                parent=self,
                title=T("pdf_dialog"),
                defaultextension=".pdf",
                initialdir=str(resultslog.RESULTS_DIR),
                initialfile=pdfreport.default_filename(LANG, stamp, self._month),
                filetypes=[("PDF", "*.pdf")],
            )
        finally:
            self._modal = False
        if not path:  # the parent changed their mind
            return
        try:
            pdfreport.export_progress(
                path,
                self._sessions,
                self._chart,
                palette=THEMES[THEME],
                labels={
                    "title": pdf_text("progress_title"),
                    "subtitle": pdf_text(
                        "pdf_exported_on", when=stamp.strftime("%d/%m/%Y %H:%M")
                    ),
                    # The month it covers, named on the report itself; empty when
                    # the whole history is being printed.
                    "period": (
                        "" if self._month is None else month_label_pdf(self._month)
                    ),
                    "chart_title": pdf_text("chart_title"),
                    "axis_grade": pdf_text("axis_grade"),
                    "legend_time": pdf_text("legend_time"),
                    "legend_grade": pdf_text("legend_grade"),
                    "n_sessions": pdf_text("n_sessions", n=len(self._sessions)),
                    "footer": pdf_text("pdf_footer"),
                    "date": pdf_text("col_date"),
                    "famille": pdf_text("col_family"),
                    "temps": pdf_text("col_time"),
                    "cartes": pdf_text("col_cards"),
                    "note": pdf_text("col_note"),
                    "temps_moyen": pdf_text("col_avg"),
                    "difficultes": pdf_text("col_difficult"),
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
        """Every logged session, newest first — the paper sheet, on screen."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        vbar = tk.Scrollbar(wrap, orient="vertical")
        hbar = tk.Scrollbar(wrap, orient="horizontal")
        # The horizontal bar is always present: it is the only cue that there
        # are more columns off the right, and the only handle for reaching them
        # when the trackpad or a shift-wheel is not at hand.
        hbar.pack(side="bottom", fill="x")

        # The vertical bar is not shown until the rows actually overrun the
        # view: a short history sat in a mostly empty panel with a bar down the
        # side that still moved, which read as a scroll that went nowhere.
        bar_shown = [True]

        def show_vbar(need: bool) -> None:
            if need == bar_shown[0]:
                return
            bar_shown[0] = need
            if need:
                vbar.pack(side="right", fill="y")
            else:
                vbar.pack_forget()

        table = ResultsTable(wrap, sessions, self.f_small, on_overflow=show_vbar)
        # The bars take their slabs before the canvas expands into the rest.
        vbar.pack(side="right", fill="y")
        table.pack(side="left", fill="both", expand=True)
        vbar.configure(command=table.yview)
        hbar.configure(command=table.xview)
        table.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        # Let the layout settle once, then draw at the width the view really
        # has: drawn earlier, the sheet would be shown narrow for a frame and
        # then visibly stretch to fit.
        self.update_idletasks()
        table.redraw()
        show_vbar(table.content_height > table.winfo_height())

    def _save_session(self, cards, total_time, correct):
        # Record only a session that ran to the size the child chose. A game cut
        # short — or one that ended on a different count for any reason — writes
        # a row whose `cartes` is not the deck that was set up, and the progress
        # screen would chart and grade that fragment as if it were a whole deck.
        if len(cards) != self.n_cards:
            return
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
            STATS_DIR.mkdir(parents=True, exist_ok=True)
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


def _watch_hangs(app: Almathus, threshold: float = 4.0) -> None:
    """Dump every thread's stack to stderr when the UI stops ticking.

    A freeze with no beachball is a callback that never came back: Tk stops
    pumping, nothing is printed, and the evidence is gone once the app is force
    quit. The pump stamps a heartbeat every 40ms; this notices when the stamp
    stops moving and reports the stacks once per stall, so the next freeze names
    the line it happened on instead of its symptoms.
    """

    def watch() -> None:
        reported = 0.0
        while not app._stopping:
            time.sleep(0.5)
            stalled = time.monotonic() - app._beat
            if app._modal or stalled < threshold:
                continue
            if time.monotonic() - reported < 15.0:
                continue  # one report per stall, not one per sample
            reported = time.monotonic()
            print(f"[hang] UI frozen for {stalled:.1f}s", file=sys.stderr)
            for thread_id, frame in sys._current_frames().items():
                print(f"[hang] thread {thread_id}", file=sys.stderr)
                traceback.print_stack(frame, file=sys.stderr)
            sys.stderr.flush()

    threading.Thread(target=watch, daemon=True, name="hang-watch").start()


def _self_test() -> None:
    """Report whether the optional stacks survived freezing, then exit.

    The game imports them inside functions and falls back when they are missing,
    which is right for a player and wrong for a build: a bundle without
    libvosk.dyld starts, speaks through the sidecar, and quietly has no
    microphone. packaging/build.py runs this in the frozen binary.
    """
    import importlib

    failed = 0
    report = []
    for name in ("vosk", "sounddevice", "webrtcvad", "numpy", "scipy", "num2words"):
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - reporting is the whole job here
            failed += 1
            report.append(f"{name}: FAILED {type(exc).__name__}: {exc}")
        else:
            report.append(f"{name}: ok")

    # The title art and flags are SVG, which Tk 9 rasterizes. A bundle with an
    # older Tk would silently show emojis, so that is a failed build, not a note.
    try:
        import tkinter
    except Exception as exc:  # noqa: BLE001 - the report is the whole job here
        failed += 1
        report.append(f"tkinter: FAILED {type(exc).__name__}: {exc}")
    else:
        if tkinter.TkVersion < 9:
            failed += 1
            report.append(f"tkinter: Tk {tkinter.TkVersion} cannot read SVG")
        else:
            report.append(f"tkinter: Tk {tkinter.TkVersion}")

    titles = ("boy.svg", "girl.svg")
    missing_titles = [name for name in titles if not (ASSETS_DIR / name).is_file()]
    if missing_titles:
        failed += 1
        report.append("title art missing: " + ", ".join(missing_titles))
    else:
        report.append(f"title art: {len(titles)} SVGs")

    flag_svgs = sorted((ASSETS_DIR / "flags").glob("*.svg"))
    if flag_svgs:
        report.append(f"flag art: {len(flag_svgs)} SVGs")
    else:
        failed += 1
        report.append("flag art missing")

    text = "\n".join(report) + "\n"
    print(text, end="", flush=True)
    # A windowed binary has no console on Windows and loses stdout on some
    # runners, so the report is also written where the build can read it.
    destination = os.environ.get("ALMATHUS_SELF_TEST_OUT")
    if destination:
        Path(destination).write_text(text, encoding="utf-8")
    raise SystemExit(1 if failed else 0)


def main():
    if os.environ.get("ALMATHUS_SELF_TEST"):
        _self_test()
        return
    if relaunch_as_app_bundle():  # macOS: let the bundle own the process, not Zed
        return
    # Test-only: the launch smoke test in CI opens the real window and closes it
    # itself. It also skips the first-run language dialog, which would otherwise
    # sit waiting for a click that no runner will ever make. Nothing else reads it.
    smoke_ms = os.environ.get("ALMATHUS_SMOKE_MS")
    if not smoke_ms:
        choose_language_at_startup()  # asks only when no speech model is installed
    resolve_language()  # sets the module's LANG before any widget reads it
    resultslog.migrate_legacy()  # a log left in the checkout follows the user
    migrate_legacy_prefs()  # ...and so do the remembered choices
    app = Almathus()
    if smoke_ms:
        app.after(int(smoke_ms), app.destroy)
    _watch_hangs(app)
    try:
        app.mainloop()
    finally:
        # close the mic stream + restore input gain to 50% on exit (any close path)
        if app.stt:
            app.stt.stop()
            app.stt.restore_input_volume(50)
        if app.tts:
            app.tts.close()  # idempotent: _shutdown may have done it already


if __name__ == "__main__":
    main()
