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

import json
import os
import queue
import random
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont

import pdfreport
import results as resultslog
import sfx

try:
    import voice as voicemod
except Exception:  # keep the game usable even if voice.py is absent/broken
    voicemod = None

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

LANG = "fr"  # default language; "en" also supported

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
        "girly_toggle": "👑 Mode princesse",
        "voice_off_hint": "🎤 Voix indisponible : {reason}",
        "listening": "🎧 à l'écoute…",
        "thinking": "🤔 …",
        "didnt_get": "Je n'ai pas compris, réessaie 🎤",
        "mic_denied": "Micro non autorisé — Réglages ▸ Confidentialité ▸ Microphone",
        "score_spoken": "Tu as eu {c} sur {n}.",
        "progress": "📈 Mes progrès",
        "back": "← Retour",
        "progress_title": "📈 Mes progrès",
        "chart_title": "Temps moyen par jour",
        "avg_label": "moyenne",
        "no_results": "Pas encore de résultats — joue une partie !",
        "col_date": "Date",
        "col_family": "Famille",
        "col_time": "Temps",
        "col_cards": "Cartes",
        "col_note": "Note",
        "col_avg": "Temps moyen / carte",
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
        "girly_toggle": "👑 Girly mode",
        "voice_off_hint": "🎤 Voice unavailable: {reason}",
        "listening": "🎧 listening…",
        "thinking": "🤔 …",
        "didnt_get": "I didn't catch that, try again 🎤",
        "mic_denied": "Mic not allowed — Settings ▸ Privacy ▸ Microphone",
        "score_spoken": "You got {c} out of {n}.",
        "progress": "📈 My progress",
        "back": "← Back",
        "progress_title": "📈 My progress",
        "chart_title": "Average time per day",
        "avg_label": "average",
        "no_results": "No results yet — play a game!",
        "col_date": "Date",
        "col_family": "Family",
        "col_time": "Time",
        "col_cards": "Cards",
        "col_note": "Grade",
        "col_avg": "Avg time / card",
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
    "fr": {"addition": "plus", "subtraction": "moins",
           "multiplication": "fois", "division": "divisé par"},
    "en": {"addition": "plus", "subtraction": "minus",
           "multiplication": "times", "division": "divided by"},
}

# Month names written out, rather than `strftime("%B")`: that one follows the
# operating system's locale, which has nothing to do with the language the app
# was started in — a French app on an English Mac would say "July".
MONTHS = {
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"),
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
}


def T(key: str, **kw) -> str:
    s = STRINGS[LANG].get(key, key)
    return s.format(**kw) if kw else s


WHEEL_LINE = 22          # px one line of scrolling is worth — about a table row
WHEEL_MAX = 5 * WHEEL_LINE   # ...and the most a single event may scroll


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
    try:
        widget.bind("<TouchpadScroll>", lambda e: scroll(touchpad_pixels(e)))
    except tk.TclError:                  # Tk 8 has no such event: wheel only
        pass
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
    name = MONTHS[LANG][ym[1] - 1]
    return f"{name[:1].upper()}{name[1:]} {ym[0]}"


# Colors
#
# Two palettes with the same keys: the dark default, and the pink "girly" one
# a kid can flip to from the settings footer. Every screen reads these names as
# module globals *while it builds its widgets*, so switching themes is
# apply_theme() followed by a redraw — nothing repaints itself in place.
THEMES = {
    "default": dict(
        BG="#0f1226",
        CARD_BG="#1b1f3b",
        ACCENT="#ffd23f",
        GOOD="#3ddc97",
        BAD="#ff6b6b",
        TEXT="#f5f6ff",
        MUTED="#9aa0c8",
        INK="#10121f",        # text laid on top of a bright tile
        SURFACE="#191d3d",    # raised panel, one step above the page background
        BORDER="#2b3160",     # hairline outline for panels and chips
        ENTRY_BG="#12152e",   # the answer box, one step *below* the page
        # Soft, cheerful and deliberately gender-neutral: mint, sky, apricot,
        # lilac. All four are light enough to carry the same near-black text.
        MODE_COLORS={
            "addition": "#4fd1a5",
            "subtraction": "#6cb8f5",
            "multiplication": "#ff9e7d",
            "division": "#b39ddb",
        },
        NUM_ON_BG="#4fd1a5", NUM_ON_FG="#10121f",
        NUM_OFF_BG="#333a63", NUM_OFF_FG="#8b93c4",
        NUM_HOVER_BG="#4b5490",
        # Hovering an operation tile lifts it away from the page: bright here,
        # deep on the light palette. Either way the label stays legible.
        TILE_HOVER_BG="#f5f6ff", TILE_HOVER_FG="#10121f",
        TITLE_SVG="mario.svg",
        TITLE_ICON="🧮",       # fallback if the drawing cannot be loaded
    ),
    # Peach-pink page, plum ink. The whole palette flips light here, so the
    # greens and reds are darkened too: they carry text on a pale background
    # now, where the dark theme's pastels would wash out.
    "girly": dict(
        BG="#ffe6ef",
        CARD_BG="#ffc8dd",
        ACCENT="#e6338c",
        GOOD="#12996f",
        BAD="#e0304f",
        TEXT="#43152c",
        MUTED="#8f5872",
        INK="#3d0f26",
        SURFACE="#fff4f8",
        BORDER="#f3aecb",
        ENTRY_BG="#fff4f8",
        MODE_COLORS={
            "addition": "#ff9ec7",
            "subtraction": "#cbaaf2",
            "multiplication": "#ffb08a",
            "division": "#ffe08a",
        },
        NUM_ON_BG="#ff7ab8", NUM_ON_FG="#3d0f26",
        NUM_OFF_BG="#ffd9e6", NUM_OFF_FG="#b07f96",
        NUM_HOVER_BG="#ffb3d3",
        # A deeper cut of ACCENT, so the tile reads as the same hot pink the rest
        # of this palette uses for "chosen", with white text over it.
        TILE_HOVER_BG="#d81b7a", TILE_HOVER_FG="#fff4f8",
        TITLE_SVG="peach.svg",
        TITLE_ICON="👑",
    ),
}


def apply_theme(name: str) -> None:
    """Rebind the palette globals to one of THEMES. Widgets that already exist
    keep the colors they were built with, so callers redraw the screen after."""
    global THEME
    THEME = name if name in THEMES else "default"
    globals().update(THEMES[THEME])


THEME = "default"
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

COUNTDOWN_STEPS = 3      # 3-2-1, then GO!
# countdown.mp3 is three beeps and a launch tone, laid out on an exact one-second
# grid: beeps at 485, 1485 and 2485 ms, the tone at 3485 ms, ringing to 4460. The
# numbers on screen are hung off that grid rather than the other way round, so a
# beep and a number land on the same instant instead of merely near one another.
COUNTDOWN_TICK = 1000    # ms one number stays on screen = the gap between beeps
COUNTDOWN_GO_AT = 3485   # ms into the sample where the launch tone starts
COUNTDOWN_GO_LEN = 975   # ms it rings for, after which the sample is silent


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
    try:
        PREFS_FILE.write_text(json.dumps(read_prefs() | updates), encoding="utf-8")
    except OSError:
        pass  # a read-only install must never break the game


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
    time_taken: float = 0.0        # seconds spent on the first attempt
    attempts: int = 0
    correct_first_try: bool | None = None
    is_retry: bool = False

    @property
    def text(self) -> str:
        return f"{self.a} {OP_SYMBOL[self.mode]} {self.b}"


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
    if nums[:2] == [card.a, card.b]:       # the question, read back
        nums = nums[2:]
    if not nums:
        return None
    if card.answer in nums:                # "quinze moins quatre, ça fait onze"
        return card.answer
    # A lone operand is taken as an ordinary wrong answer: a whole session is
    # built on one number ("− 4"), so refusing it made that number unsayable on
    # every card of the session. It is also the one wrong answer a half-heard
    # read-back can produce — the cost of hearing it is one card marked wrong and
    # asked again at the end, which is the same thing that happens when the child
    # really does answer 4.
    return nums[-1]                        # self-correction: the last number wins


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
            pairs += [(ans * b, b) for ans in range(RANGES["division"]["max_factor"] + 1)]
        else:
            raise ValueError(mode)
    return pairs


def make_deck(mode: str, n: int, allowed: set[int] | None = None) -> list[Card]:
    """Deal n cards as a shuffled bag: no operation comes back until every other
    one has been asked. Drawing each card independently meant the same easy sum
    could show up two or three times in a round.

    Locking the numbers down can leave fewer distinct operations than cards asked
    for (7× has only 11 facts). The card count wins there — the bag is reshuffled
    and dealt again, so repeats start only once the kid has seen the whole set.
    """
    pool = every_operation(mode, allowed or set(range(NUM_MIN, NUM_MAX + 1)))
    if not pool:
        return []
    deck: list[Card] = []
    while len(deck) < n:
        bag = random.sample(pool, len(pool))
        if deck and len(bag) > 1 and bag[0] == (deck[-1].a, deck[-1].b):
            bag.append(bag.pop(0))       # no repeat across the seam either
        deck += [make_card(mode, a, b) for a, b in bag[:n - len(deck)]]
    return deck


# --------------------------------------------------------------------------- #
# A colored button that works everywhere.
#
# macOS Tk renders native `tk.Button`s and ignores bg/fg, so themed buttons come
# out white-on-white. A Label honors colors on every platform, so we drive one
# like a button with mouse bindings.
# --------------------------------------------------------------------------- #


class Btn(tk.Label):
    def __init__(self, parent, text, command=None, bg=None, fg=None, font=None,
                 active_bg=None, active_fg=None, padx=14, pady=10, **kw):
        # The palette defaults are filled in here rather than in the signature:
        # default arguments are evaluated once at import, which would nail every
        # unstyled button to whichever theme happened to be loaded first.
        bg = bg or CARD_BG
        fg = fg or TEXT
        active_bg = active_bg or ACCENT
        active_fg = active_fg or INK
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font,
                         padx=padx, pady=pady, cursor="hand2", **kw)
        self._bg, self._fg, self._abg, self._afg = bg, fg, active_bg, active_fg
        self._command = command
        self.bind("<Enter>", lambda e: self._paint(self._abg, self._afg))
        self.bind("<Leave>", lambda e: self._paint(self._bg, self._fg))
        self.bind("<Button-1>", lambda e: self._paint(self._abg, self._afg))
        self.bind("<ButtonRelease-1>", self._release)

    def _paint(self, bg, fg):
        self.config(bg=bg, fg=fg)

    def _release(self, e):
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._paint(*(( self._abg, self._afg) if inside else (self._bg, self._fg)))
        if inside and self._command:
            self._command()

    def set_colors(self, bg, fg):
        self._bg, self._fg = bg, fg
        self.config(bg=bg, fg=fg)


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
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def _show(self):
        self.job = None
        if self.tip or not self.widget.winfo_exists():
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)          # no title bar, no shadow frame
        try:
            self.tip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(self.tip, text=self.text, font=self.font, bg=SURFACE, fg=TEXT,
                 padx=10, pady=5, highlightthickness=1, highlightbackground=BORDER
                 ).pack()
        self.tip.update_idletasks()
        # Hang it under the widget, right edges aligned — the button lives in the
        # top-right corner, so a left-aligned bubble would run off the screen.
        w = self.tip.winfo_reqwidth()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() - w
        x = max(4, min(x, self.widget.winfo_screenwidth() - w - 4))
        self.tip.wm_geometry(f"+{x}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 6}")

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
    MAX_SCREEN = 0.6                 # of the screen height: taller lists scroll

    def __init__(self, parent, options, value, command, font, **kw):
        self.options = list(options)
        self.value = value
        self._on_pick = command
        self._font = font
        self._pop = None
        super().__init__(parent, self._text_for(value), command=self._toggle,
                         font=font, **kw)
        # A screen change destroys the button; the popup is a Toplevel, so it
        # would otherwise be left floating over whatever comes next.
        self.bind("<Destroy>", lambda e: self._close(), add="+")

    def _text_for(self, value) -> str:
        return next((lb for v, lb in self.options if v == value), "") + self.ARROW

    def _toggle(self):
        self._close() if self._pop else self._open()

    def _open(self):
        self.update_idletasks()          # the list hangs off this button's geometry
        self._pop = top = tk.Toplevel(self)
        top.wm_overrideredirect(True)                # no title bar, no shadow
        # Built out of sight: measuring the rows maps the window, and the window
        # manager places it before it hears where we want it — so an unhidden
        # popup flashes in the corner of the screen on its way under the button.
        top.wm_withdraw()
        try:
            top.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(top, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, bg=SURFACE, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        rows = tk.Frame(canvas, bg=SURFACE)
        window = canvas.create_window((0, 0), window=rows, anchor="nw")
        here = None
        for v, label in self.options:
            chosen = v == self.value
            row = Btn(rows, label, command=lambda v=v: self._choose(v), font=self._font,
                      bg=ACCENT if chosen else SURFACE, fg=INK if chosen else TEXT,
                      padx=16, pady=7, anchor="w")
            row.pack(fill="x")
            here = row if chosen else here

        rows.update_idletasks()
        w = max(rows.winfo_reqwidth(), self.winfo_width())
        h = rows.winfo_reqheight()
        view = min(h, int(self.winfo_screenheight() * self.MAX_SCREEN))
        # `yscrollincrement=1`: without it a canvas scrolls a tenth of its own
        # height per unit, which on a list this size is half the popup a nudge.
        canvas.configure(width=w, height=view, scrollregion=(0, 0, w, h),
                         yscrollincrement=1)
        canvas.itemconfig(window, width=w)           # rows span the full width
        if h > view:
            # Bound on the Toplevel: a row Label's bindtags include its window,
            # so a wheel event over a row is looked up here as well.
            bind_wheel(top, lambda px: canvas.yview_scroll(px, "units"))
            if here is not None:                     # open on the month you are on
                canvas.yview_moveto(max(0.0, (here.winfo_y() - view / 2) / h))

        # Hang it under the button, pushed back inside the screen if it overruns.
        top.update_idletasks()
        x = min(max(self.winfo_rootx(), 4),
                self.winfo_screenwidth() - top.winfo_reqwidth() - 4)
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
            try:
                self._pop.grab_release()
            except tk.TclError:
                pass
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
    HIT = 32                       # px: how close the pointer must be to a dot
    NOTE_GAP = 8                   # px: clearance between a note tag and its dot
    # Round y steps a parent reads at a glance: 5 s up to 15 min.
    STEPS = (5, 10, 15, 20, 30, 60, 90, 120, 180, 300, 600, 900)

    def __init__(self, parent, sessions, font, note_font=None, height=220):
        super().__init__(parent, bg=BG, height=height, highlightthickness=0, bd=0)
        # Plotting needs both a date (x) and a time (y). Rows missing either
        # still show up in the table — they just can't be placed on the chart.
        plottable = [s for s in sessions if s.day and s.seconds is not None]
        by_day: dict[object, list] = {}
        for s in plottable:
            by_day.setdefault(s.day, []).append(s)
        # (day, mean seconds, the sessions behind it) — one entry per day, in order
        self.days = [(d, sum(g_.seconds for g_ in g) / len(g), g)
                     for d, g in sorted(by_day.items())]
        self.font = font
        # The tags get their own, smaller face. They sit *inside* the plot, one per
        # day, so at the axis' size a fortnight of daily sessions covered its own
        # curve and the tags ran into each other; small and bold they still read.
        self.note_font = note_font or font
        self.NOTE_H = self.note_font.metrics("linespace") + 4
        # A note tag sits above its dot, so the plot needs headroom for one —
        # otherwise the best day's tag is clipped by the top of the canvas.
        if any(resultslog.grade(g) for _, _, g in self.days):
            self.PAD_T = self.NOTE_H + self.NOTE_GAP + 6
        # Reserve the bottom band from the real font metrics — the fonts scale
        # with the screen, so a fixed 30 px clipped the date labels on a tall one.
        self.PAD_B = font.metrics("linespace") + 16
        self.pts: list[tuple[float, float, tuple]] = []
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("tip"))

    @staticmethod
    def _curve(pts: list[tuple[float, float, object]]) -> list[float]:
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
            if d[i] == 0:                       # a flat run stays flat
                m[i] = m[i + 1] = 0.0
                continue
            a, b = m[i] / d[i], m[i + 1] / d[i]
            if a * a + b * b > 9:               # clamp the tangents: no overshoot
                t = 3.0 / (a * a + b * b) ** 0.5
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]

        out: list[float] = []
        for i in range(n - 1):
            h = xs[i + 1] - xs[i]
            steps = max(2, min(48, int(h / 4)))     # ~4 px per sampled segment
            for k in range(steps + (1 if i == n - 2 else 0)):
                t = k / steps
                t2, t3 = t * t, t * t * t
                out += [xs[i] + h * t,
                        (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i]
                        + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1]]
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

        def Y(v):
            return y1 - (v / top) * (y1 - y0)

        # grid + y labels (a zero baseline: these are durations, so the height
        # of a point has to mean its whole magnitude)
        v = 0.0
        while v <= top + 1e-9:
            y = Y(v)
            self.create_line(x0, y, x1, y, fill=BORDER)
            self.create_text(x0 - 10, y, text=resultslog.fmt_duration(v, 0),
                             anchor="e", font=self.font, fill=MUTED)
            v += step

        # x is real calendar time, so plateaus and gaps are visible.
        ords = [d.toordinal() for d, _, _ in self.days]
        lo, span = min(ords), max(ords) - min(ords)

        def X(o):
            return (x0 + x1) / 2 if not span else x0 + (o - lo) / span * (x1 - x0)

        pts = []
        for entry in self.days:
            day, mean, _ = entry
            pts.append((X(day.toordinal()), Y(mean), entry))
        if len(pts) > 1:
            self.create_line(self._curve(pts), fill=ACCENT, width=2,
                             capstyle="round", joinstyle="round")
        for x, y, _ in pts:
            # a 2px background ring keeps a dot legible where the curve runs steep
            self.create_oval(x - 4, y - 4, x + 4, y + 4,
                             fill=ACCENT, outline=BG, width=2)
        self.pts = pts
        self._draw_notes(pts, x0, x1)

        # x labels: distinct days, thinned by *pixel* distance — the axis is
        # calendar time, so evenly spaced days are not evenly spaced columns
        # (two sessions a day apart would print on top of each other).
        gap = self.font.measure("00/00") + 22
        kept: list[tuple[object, float]] = []
        for d, _, _ in self.days:
            xd = X(d.toordinal())
            if not kept or xd - kept[-1][1] >= gap:
                kept.append((d, xd))
        last = self.days[-1][0]
        if kept and kept[-1][0] != last:      # the newest date always gets a label
            xd = X(last.toordinal())
            while kept and xd - kept[-1][1] < gap:
                kept.pop()
            kept.append((last, xd))
        for d, xd in kept:
            self.create_text(xd, y1 + 10, text=d.strftime("%d/%m"),
                             anchor="n", font=self.font, fill=MUTED)

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        """Tk has no rounded rectangle. A polygon that repeats its corner points
        and is then smoothed gives one: the doubled points pin the straight
        edges, and the single ones round the turns."""
        return self.create_polygon(
            x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y0 + r,
            x1, y1 - r, x1, y1 - r, x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1,
            x0 + r, y1, x0, y1, x0, y1 - r, x0, y1 - r, x0, y0 + r, x0, y0 + r,
            x0, y0, smooth=True, **kw)

    def _draw_notes(self, pts, x_min, x_max):
        """The day's note as a small tag over its dot — `15/20`, the number the
        sheet is really about. Days that never recorded one (the old paper rows)
        simply get no tag.

        Tags are placed left to right; one that would land on a tag already
        placed climbs above it and trades its speech-bubble tail for a thin
        stem, so it still says which dot it belongs to. They are kept within the
        plot (give or take a few pixels), so the leftmost one cannot sit on top
        of the y-axis labels the way it did when the canvas edge was the limit.
        """
        placed: list[tuple[float, float, float, float]] = []
        bh = self.NOTE_H

        def free(bx, by, bw):
            return not any(bx < px + pw and px < bx + bw and by < py + ph
                           and py < by + bh for px, py, pw, ph in placed)

        for x, y, (_, _, group) in pts:
            text = resultslog.fmt_note(resultslog.grade(group))
            if not text:
                continue
            bw = self.note_font.measure(text) + 8
            # 6 px of slack on either side: enough for the first and last tags to
            # stay centred on their dots, not enough to reach an axis label.
            lo, hi = x_min - 6, x_max + 6
            bx = min(max(x - bw / 2, lo), max(lo, hi - bw))
            home = y - self.NOTE_GAP - bh          # where it would sit undisturbed
            by = home
            while not free(bx, by, bw) and by - (bh + 2) >= 2:
                by -= bh + 2
            by = max(by, 2)
            placed.append((bx, by, bw, bh))

            if by == home and abs(bx + bw / 2 - x) < 0.5:
                # right above its own dot: a speech-bubble tail down to it
                self.create_polygon(x - 4, by + bh - 1, x + 4, by + bh - 1,
                                    x, y - 5, fill=ACCENT, outline=ACCENT)
            else:
                self.create_line(bx + bw / 2, by + bh, x, y - 5, fill=ACCENT, width=1)
            self._round_rect(bx, by, bx + bw, by + bh, 4, fill=ACCENT, outline=BG)
            self.create_text(bx + bw / 2, by + bh / 2, text=text, font=self.note_font,
                             fill=BG, anchor="center")

    def _hover(self, e):
        self.delete("tip")
        best, closest = None, self.HIT ** 2
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
            lines.append(f"{T('n_sessions', n=len(group))} · "
                         f"{T('avg_label')} {resultslog.fmt_duration(mean)}")
        else:
            lines.append(resultslog.fmt_duration(mean))
        per_card = [s.avg for s in group if s.avg is not None]
        if per_card:
            lines.append(f"{T('col_avg')} : {sum(per_card) / len(per_card):.1f} s")

        lh = self.font.metrics("linespace") + 2
        bw = max(self.font.measure(t) for t in lines) + 20
        bh = lh * len(lines) + 14
        bx = min(max(x + 14, 4), self.winfo_width() - bw - 4)
        by = min(max(y - bh - 12, 4), self.winfo_height() - bh - 4)
        self.create_rectangle(bx, by, bx + bw, by + bh,
                              fill=SURFACE, outline=BORDER, tags="tip")
        for i, t in enumerate(lines):
            self.create_text(bx + 10, by + 7 + i * lh, text=t, anchor="nw",
                             font=self.font, fill=ACCENT if i == 0 else TEXT, tags="tip")
        self.create_oval(x - 6, y - 6, x + 6, y + 6, outline=TEXT, width=2, tags="tip")


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #


class Almathus(tk.Tk):
    def __init__(self):
        super().__init__()
        apply_theme(load_theme())      # before any widget reads a color
        self.title(T("title"))
        self.configure(bg=BG)
        self.minsize(720, 640)
        # Maximize to fill the screen as a normal window — NOT macOS' native
        # fullscreen mode (which hides the menu bar and jumps to its own space).
        self._maximize()

    def _maximize(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if self.tk.call("tk", "windowingsystem") == "aqua":
            # Fill the whole screen from the top-left. macOS keeps the title bar
            # reachable; with the menu bar hidden this uses every pixel.
            self.geometry(f"{sw}x{sh}+0+0")
        else:
            try:
                self.state("zoomed")           # Windows / most Linux WMs
            except tk.TclError:
                self.geometry(f"{sw}x{sh}+0+0")  # fallback: size to the screen

        self._make_fonts()

        # session state
        self.mode = "addition"
        self.n_cards = DEFAULT_CARDS
        # numbers the operations are played with, restored from the last run
        self.allowed = load_allowed()
        self.deck: list[Card] = []
        self.results: list[Card] = []     # completed cards, in play order
        self.retry_queue: list[Card] = []
        self.retry_seen: set[tuple] = set()   # operations already queued for retry
        self.idx = 0
        self.current: Card | None = None
        self.card_start = 0.0
        self.game_start = 0.0
        self._tick_job = None
        self._advance_job = None    # the single pending "move to the next card" timer
        self._voice_job = None      # the watchdog that keeps this card's mic armed
        self._card_done = False     # answer committed: ignore anything else on this card
        self._fanfare_job = None    # the recap's pending "start the music" timer
        self._pump_job = None       # the worker-thread -> Tk callback pump
        self._stopping = False      # latched on quit: schedule no more timers
        self._voice_mic_bad = False

        # sound effects (a no-op if no command-line player is installed)
        self.sfx = sfx.Sound(SOUNDS_DIR)

        # voice engines (both degrade gracefully to no-ops)
        self.tts = voicemod.TTS(LANG) if voicemod else None
        self.stt = voicemod.SpeechInput(LANG) if voicemod else None
        stt_ok = bool(self.stt and self.stt.available())
        self.voice_enabled = tk.BooleanVar(value=stt_ok)

        # thread-safe channel: worker threads (STT/TTS) post callables here and
        # the pump runs them on the Tk main loop — never touch Tk off-thread.
        self._ui_queue: queue.Queue = queue.Queue()
        self._pump()

        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        self._install_exit_handlers()
        self.show_settings()

    # ------------------------------------------------------------------ #
    # Clean shutdown — restore the mic gain on EVERY exit path
    # ------------------------------------------------------------------ #
    def _install_exit_handlers(self):
        self.protocol("WM_DELETE_WINDOW", self._on_quit)     # window close box
        try:
            self.createcommand("tk::mac::Quit", self._on_quit)   # macOS Cmd+Q / Quit menu
        except tk.TclError:
            pass
        self.bind_all("<Command-q>", lambda e: self._on_quit())
        self.bind_all("<Control-c>", lambda e: self._on_quit())
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, self._on_signal)          # terminal kill / close
            except (OSError, ValueError):
                pass

    def _shutdown(self):
        # Timers first: a pending callback that runs after the window is gone
        # raises out of Tcl ("invalid command name ..._pump") with nowhere to
        # report it.
        self._stopping = True
        for job in ("_pump_job", "_tick_job", "_advance_job", "_voice_job",
                    "_fanfare_job"):
            pending = getattr(self, job, None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except Exception:
                    pass
                setattr(self, job, None)
        # The player is a child process: quitting through os._exit() leaves it
        # orphaned, still playing to an empty screen, unless it is killed here.
        self.sfx.stop()
        if self.stt:
            self.stt.stop()                                  # close mic stream (mic light off)
            self.stt.restore_input_volume(50)                # idempotent

    def _on_quit(self):
        self._shutdown()
        try:
            self.destroy()
        except Exception:
            pass
        # Force the process to die so the OS reclaims the mic no matter what
        # (an open PortAudio stream can otherwise stall interpreter shutdown).
        os._exit(0)

    def _on_signal(self, _signum, _frame):
        self._shutdown()
        os._exit(0)

    def _pump(self):
        if self._stopping:
            return                    # the window is going away; stop rescheduling
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            pass
        self._pump_job = self.after(40, self._pump)

    def _post(self, fn):
        """Schedule fn() to run on the Tk thread (safe to call from any thread)."""
        self._ui_queue.put(fn)

    # -- fonts ------------------------------------------------------------- #
    def _make_fonts(self):
        # Everything scales with the screen so the settings column still fits
        # (and stays readable) on a short laptop display.
        s = min(1.0, self.winfo_screenheight() / 900)

        def F(size, weight="normal"):
            return tkfont.Font(family="Helvetica", size=max(11, round(size * s)),
                               weight=weight)

        self.f_giant = F(120, "bold")
        self.f_huge = F(90, "bold")
        self.f_big = F(48, "bold")
        self.f_mode = F(30, "bold")
        self.f_title = F(28, "bold")
        self.f_body = F(20)
        self.f_small = F(15)
        self.f_tile = F(24, "bold")
        self.f_chip = F(14, "bold")
        self.f_num = F(20, "bold")
        self.f_hint = F(14)
        # The chart's note tags, below the 11 px floor F() keeps for everything
        # else: a tag is one short number on a coloured pill inside the plot, not
        # a line of text to read, and at any larger size a fortnight of them
        # covers the curve they annotate. Bold, so small still reads.
        self.f_tag = tkfont.Font(family="Helvetica", size=max(9, round(11 * s)),
                                 weight="bold")

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
        for w in self.container.winfo_children():
            w.destroy()

    # ------------------------------------------------------------------ #
    # Screen 1: settings
    # ------------------------------------------------------------------ #
    def show_settings(self):
        self._clear()
        self._hush()             # leaving the recap: the fanfare goes with it
        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        # One packed column, top to bottom. Never .place() the content here: a
        # placed frame is not part of the packing flow, so it silently lands on
        # top of whatever comes after it (that's how the voice toggle ended up
        # covering the operation tiles).
        col = tk.Frame(f, bg=BG)
        col.pack(expand=True, padx=24, pady=16)

        self._build_title(col)

        # -- how many cards (one compact toolbar row) ----------------------- #
        self.count_var = tk.IntVar(value=self.n_cards)
        panel = tk.Frame(col, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        panel.pack(fill="x", pady=(14, 18))
        bar = tk.Frame(panel, bg=SURFACE)
        bar.pack(pady=12)
        tk.Label(bar, text=T("how_many"), font=self.f_body, fg=MUTED, bg=SURFACE
                 ).pack(side="left", padx=(0, 18))

        def bump(d):
            self.count_var.set(max(1, min(99, self.count_var.get() + d)))

        Btn(bar, "−", command=lambda: bump(-1), font=self.f_mode, bg=CARD_BG, fg=TEXT,
            width=2, pady=0).pack(side="left", padx=6)
        tk.Label(bar, textvariable=self.count_var, font=self.f_mode, fg=ACCENT, bg=SURFACE,
                 width=3).pack(side="left")
        Btn(bar, "+", command=lambda: bump(1), font=self.f_mode, bg=CARD_BG, fg=TEXT,
            width=2, pady=0).pack(side="left", padx=6)

        # quick picks double as a segmented control: the live value stays lit
        chips: dict[int, Btn] = {}
        for q in (5, 10, 20, 30, 50):
            chips[q] = Btn(bar, str(q), command=lambda v=q: self.count_var.set(v),
                           font=self.f_chip, width=3, padx=10, pady=6,
                           active_bg=ACCENT, active_fg=INK)
            chips[q].pack(side="left", padx=(18 if q == 5 else 5, 0))

        def lit(*_):
            v = self.count_var.get()
            for q, c in chips.items():
                c.set_colors(*((ACCENT, INK) if q == v else (CARD_BG, MUTED)))

        self.count_var.trace_add("write", lit)
        lit()

        # -- which numbers (step 1) ---------------------------------------- #
        self._build_number_row(col)

        # -- which operation (step 2, and it starts the game) --------------- #
        tk.Label(col, text=T("choose_mode"), font=self.f_title, fg=TEXT, bg=BG
                 ).pack(pady=(4, 14))

        grid = tk.Frame(col, bg=BG)
        grid.pack(fill="x")
        for c in (0, 1):                       # keep both tiles the same width
            grid.grid_columnconfigure(c, uniform="ops", weight=1)
        for i, m in enumerate(MODES):
            Btn(grid, f"{T(m)}  {OP_SYMBOL[m]}", command=lambda mm=m: self.start_game(mm),
                font=self.f_tile, bg=MODE_COLORS[m], fg=INK,
                active_bg=TILE_HOVER_BG, active_fg=TILE_HOVER_FG, padx=20, pady=22
                ).grid(row=i // 2, column=i % 2, padx=10, pady=10, sticky="nsew")

        footer = tk.Frame(col, bg=BG)
        footer.pack(pady=(18, 4))
        self._build_voice_toggle(footer)
        self._build_girly_toggle(footer)
        Btn(footer, T("progress"), command=self.show_progress, font=self.f_body,
            bg=CARD_BG, fg=MUTED, padx=22, pady=10).pack(side="left", padx=8)

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
            tk.Label(row, text=TITLE_ICON, font=self.f_big, fg=ACCENT, bg=BG
                     ).pack(side="left", padx=(0, 12))
        tk.Label(row, text="Almathus", font=self.f_big, fg=ACCENT, bg=BG).pack(side="left")

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
        panel = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        panel.pack(fill="x", pady=(0, 22), ipady=10)
        tk.Label(panel, text=T("choose_numbers"), font=self.f_body, fg=MUTED, bg=SURFACE
                 ).pack(pady=(6, 2))

        # The row hugs its chips instead of stretching: slack is what Tk hands
        # out a pixel at a time, and that is what made the chips uneven.
        strip = tk.Frame(panel, bg=SURFACE)
        strip.pack(pady=(2, 4))
        boxes: dict[int, tk.Label] = {}

        def paint(n: int, hover: bool = False):
            on = n in self.allowed
            boxes[n].config(bg=NUM_ON_BG if on else (NUM_HOVER_BG if hover else NUM_OFF_BG),
                            fg=NUM_ON_FG if on else (TEXT if hover else NUM_OFF_FG))

        def toggle(n: int):
            if n in self.allowed:
                if len(self.allowed) == 1:
                    return          # always keep at least one number to play with
                self.allowed.discard(n)
            else:
                self.allowed.add(n)
            paint(n, hover=True)
            # write on every tap: quitting goes through os._exit(), so there is
            # no exit hook late enough to be trusted with this
            save_allowed(self.allowed)

        for n in range(NUM_MIN, NUM_MAX + 1):
            # width in characters, identical for every chip, so "10" does not
            # request more room than "1" and the uniform columns can all match
            lbl = tk.Label(strip, text=str(n), font=self.f_num, width=2, pady=8,
                           cursor="hand2")
            lbl.grid(row=0, column=n - NUM_MIN, sticky="nsew", pady=8,
                     padx=(8 if n == NUM_MIN else 4, 8 if n == NUM_MAX else 4))
            lbl.bind("<Button-1>", lambda e, k=n: toggle(k))
            lbl.bind("<Enter>", lambda e, k=n: paint(k, hover=True))
            lbl.bind("<Leave>", lambda e, k=n: paint(k))
            boxes[n] = lbl
            paint(n)

        tk.Label(panel, text=T("numbers_hint"), font=self.f_hint, fg=MUTED, bg=SURFACE
                 ).pack(pady=(2, 6))

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

        btn._command = lambda: (self.voice_enabled.set(not self.voice_enabled.get()), refresh())
        btn.pack(side="left", padx=8)
        refresh()

    def _build_girly_toggle(self, parent):
        on = THEME == "girly"
        btn = Btn(parent, f"{T('girly_toggle')} : {'ON 👑' if on else 'OFF'}",
                  font=self.f_body, padx=22, pady=10,
                  bg=ACCENT if on else CARD_BG, fg=INK if on else MUTED)

        def flip():
            apply_theme("default" if THEME == "girly" else "girly")
            write_prefs(theme=THEME)
            # The window and its container outlive every screen, so they are
            # repainted by hand; everything else is rebuilt from the new palette.
            self.configure(bg=BG)
            self.container.config(bg=BG)
            self.show_settings()

        btn._command = flip
        btn.pack(side="left", padx=8)

    # ------------------------------------------------------------------ #
    # Screen 2: countdown
    # ------------------------------------------------------------------ #
    def start_game(self, mode: str):
        self.mode = mode
        self.n_cards = self.count_var.get()
        self.deck = make_deck(mode, self.n_cards, self.allowed)
        self.results = []
        self.retry_queue = []
        self.retry_seen = set()
        self.idx = 0
        # No card is on screen yet, so the countdown's move to the first one is
        # not "leaving an unanswered card" — see _advance().
        self.current = None
        self._card_done = False
        # preload the STT model + raise mic gain so the first spoken answer is instant
        if self.voice_enabled.get() and self.stt and self.stt.available():
            self.stt.warmup()
            self.stt.boost_input_volume()
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
        tk.Label(f, text=str(n) if n > 0 else T("go"),
                 font=self.f_giant, fg=ACCENT if n > 0 else GOOD, bg=BG
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
        if self._advance_job is not None:      # any other pending move is stale
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

    def render_card(self, in_retry: bool):
        self._clear()
        card = self.current
        self._card_done = False
        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        # header (pinned top)
        header = (T("retry_round") if in_retry
                  else T("card_of", n=min(self.idx + 1, self.n_cards), total=self.n_cards))
        tk.Label(f, text=header, font=self.f_title,
                 fg=ACCENT if in_retry else MUTED, bg=BG).pack(side="top", pady=(20, 0))

        # timers (pinned bottom, always fully visible)
        bar = tk.Frame(f, bg=BG)
        bar.pack(side="bottom", fill="x", padx=40, pady=(0, 20))
        self.card_time_lbl = tk.Label(bar, text="", font=self.f_body, fg=MUTED, bg=BG)
        self.card_time_lbl.pack(side="left")
        self.total_time_lbl = tk.Label(bar, text="", font=self.f_title, fg=ACCENT, bg=BG)
        self.total_time_lbl.pack(side="right")

        # body fills the space between header and timers; weighted spacer rows
        # spread the content across the full height (fills the screen, never overlaps)
        body = tk.Frame(f, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)   # top spacer
        body.rowconfigure(4, weight=1)   # bottom spacer (equal -> group centered)

        tk.Label(body, text=card.text, font=self.f_huge, fg=TEXT, bg=CARD_BG,
                 padx=80, pady=12).grid(row=1, column=0, pady=(0, 8))

        voice_on = bool(self.voice_enabled.get() and self.stt and self.stt.available())

        inputs = tk.Frame(body, bg=BG)
        inputs.grid(row=2, column=0, pady=(0, 8))
        self.answer_var = tk.StringVar()
        entry = tk.Entry(inputs, textvariable=self.answer_var, font=self.f_big,
                         justify="center", width=6, bd=0,
                         bg=ENTRY_BG, fg=ACCENT, insertbackground=ACCENT)
        entry.pack(pady=(2, 2), ipady=6)
        entry.focus_set()
        entry.bind("<Return>", lambda e: self.submit())
        self.feedback = tk.Label(inputs, text="", font=self.f_body, fg=GOOD, bg=BG)
        self.feedback.pack(pady=1)
        # Voice input runs continuously in parallel with the keypad — no button.
        # A small status line shows listening / a mic-permission hint if needed.
        self.voice_lbl = None
        self._voice_mic_bad = False
        if self.stt and self.stt.available():
            self.voice_lbl = tk.Label(inputs, text="", font=self.f_small, fg=MUTED, bg=BG)
            self.voice_lbl.pack(pady=1)

        # On-screen keypad (kid-friendly; works with touch/mouse too). It is built
        # either way but stays hidden while answering by voice: a grid of digits
        # under the question only distracts a child who is meant to speak. It is
        # revealed again the moment the mic turns out to be unusable.
        self.keypad_frame = tk.Frame(body, bg=BG)
        self._build_keypad(self.keypad_frame)
        if not voice_on:
            self._show_keypad()

        self.card_start = time.perf_counter()
        self._tick()

        # read the operation aloud, then listen continuously
        if voice_on:
            self._speak_operation(card, then=lambda: self._voice_arm(card))
            self._voice_watch(card)

    def _show_keypad(self):
        kp = getattr(self, "keypad_frame", None)
        if kp and kp.winfo_exists() and not kp.winfo_manager():
            kp.grid(row=3, column=0)

    def _speak_operation(self, card: "Card", then=None):
        if not (self.tts and self.tts.available()):
            if then:
                then()
            return
        word = OP_SPOKEN[LANG][card.mode]
        phrase = f"{card.a} {word} {card.b}"
        # marshal the done-callback back onto the Tk thread via the pump
        self.tts.speak(phrase, on_done=(lambda: self._post(then)) if then else None)

    def _voice_arm(self, target):
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

    def _voice_watch(self, target):
        """Keep the mic armed for as long as this card is up.

        Listening re-arms itself from its own result callback, which is a chain:
        one dropped link (a callback that lands while a capture is already
        running, a worker that dies) and the card goes quietly deaf, with the
        child talking to a question that will never hear them. This second,
        independent timer notices within a second and arms it again.
        """
        self._voice_job = None
        if self.current is not target or self._card_done:
            return                        # this card is over; stop watching it
        talking = bool(self.tts and self.tts.speaking())
        if not talking and not self._voice_mic_bad and self.voice_enabled.get():
            if self.stt and self.stt.available() and not self.stt.listening():
                self._voice_arm(target)
        self._voice_job = self.after(1000, lambda: self._voice_watch(target))

    def _voice_status(self, state):
        if state == "no_mic":
            self._voice_mic_bad = True   # stop re-arming; capture is silent
            self._show_keypad()          # give the child a way back in
        lbl = getattr(self, "voice_lbl", None)
        if not (lbl and lbl.winfo_exists()):
            return
        if state == "no_mic":
            lbl.config(text=T("mic_denied"), fg=BAD)
        elif state == "listening":
            lbl.config(text=T("listening"), fg=MUTED)
        elif state == "thinking":
            lbl.config(text=T("thinking"), fg=MUTED)

    def _voice_result(self, n, target):
        if self.current is not target or self._card_done:   # card already over
            return
        if n is not None:
            self._voice_answer(n, target)
            return
        # nothing recognized this round: keep listening (unless the mic is unusable)
        if not self._voice_mic_bad and self.voice_enabled.get():
            self._voice_arm(target)

    def _voice_answer(self, n: int, target):
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
        self.answer_var.set(str(n))     # the child sees the number that was heard
        self.submit()                   # auto-submit, in parallel with the keypad

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
                    cmd = (lambda d=k: self.answer_var.set(self.answer_var.get() + d))
                    col, fg = CARD_BG, TEXT
                Btn(pad, k, command=cmd, font=self.f_mode, bg=col, fg=fg,
                    width=2, padx=16, pady=4).grid(row=r, column=c, padx=6, pady=4)

    def _tick(self):
        now = time.perf_counter()
        self.total_time_lbl.config(text=f"{T('timer_total')}  {self._fmt(now - self.game_start)}")
        self.card_time_lbl.config(text=f"{T('timer_card')}  {now - self.card_start:4.1f}s")
        self._tick_job = self.after(100, self._tick)

    @staticmethod
    def _fmt(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def submit(self):
        if self._card_done:      # already answered; the card is on its way out
            return
        card = self.current
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
            self.stt.cancel()    # drop the capture in flight; the card is over

        # record time + outcome only on the very first attempt of a card
        if card.correct_first_try is None:
            card.time_taken = time.perf_counter() - self.card_start
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
        total_time = time.perf_counter() - self.game_start
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

        tk.Label(col, text=f"{T('score')} : {correct}/{n}  ({pct:.0f}%)",
                 font=self.f_title, fg=GOOD if pct >= 80 else ACCENT, bg=BG).pack(pady=4)
        tk.Label(col, text=f"{T('total_time')} : {self._fmt(total_time)}",
                 font=self.f_body, fg=TEXT, bg=BG).pack(pady=2)
        tk.Label(col, text=f"{T('avg_time')} : {avg:.1f}s",
                 font=self.f_body, fg=MUTED, bg=BG).pack(pady=2)

        # slowest / struggled operations, for reinforcement
        slow = sorted(cards, key=lambda c: c.time_taken, reverse=True)[:5]
        if slow:
            tk.Label(col, text=T("slowest"), font=self.f_body, fg=ACCENT, bg=BG).pack(pady=(16, 4))
            box = tk.Frame(col, bg=CARD_BG)
            box.pack(pady=4)
            for c in slow:
                mark = "✅" if c.correct_first_try else "❌"
                tk.Label(box, text=f"{mark}  {c.text} = {c.answer}    {c.time_taken:.1f}s",
                         font=self.f_small, fg=TEXT, bg=CARD_BG, anchor="w").pack(fill="x", padx=16, pady=2)

        btns = tk.Frame(f, bg=BG)
        btns.pack(side="bottom", pady=28)
        Btn(btns, T("again"), command=self.show_settings, font=self.f_mode,
            bg=GOOD, fg=INK).pack(side="left", padx=12)
        Btn(btns, T("progress"), command=self.show_progress, font=self.f_mode,
            bg=CARD_BG, fg=TEXT).pack(side="left", padx=12)
        Btn(btns, T("quit"), command=self._on_quit, font=self.f_mode,
            bg=CARD_BG, fg=TEXT, active_bg=BAD).pack(side="left", padx=12)

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
            def say():
                self._post(lambda: self.tts.speak(spoken))

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
        return "🤕", "ALLEZ, ENCORE UN PEU DE TRAVAIL !" if LANG == "fr" else "A BIT MORE PRACTICE!"

    # ------------------------------------------------------------------ #
    # Screen 5: progress — the results CSV, charted and tabulated
    # ------------------------------------------------------------------ #
    def show_progress(self):
        self._clear()
        self._hush()             # ditto — this screen is for reading, not cheering
        sessions = resultslog.load_sessions()

        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(18, 6))
        Btn(bar, T("back"), command=self.show_settings, font=self.f_body,
            bg=CARD_BG, fg=TEXT).pack(side="left")
        tk.Label(bar, text=T("progress_title"), font=self.f_title, fg=ACCENT, bg=BG
                 ).pack(side="left", padx=20)
        # Reconfigured on every filter change: the count belongs to what is shown.
        self._n_label = tk.Label(bar, text=T("n_sessions", n=len(sessions)),
                                 font=self.f_small, fg=MUTED, bg=BG)
        self._n_label.pack(side="right")

        if not sessions:
            tk.Label(f, text=T("no_results"), font=self.f_body, fg=MUTED, bg=BG
                     ).pack(expand=True)
            return

        # Export lives next to the session count: same corner, same weight as the
        # rest of the header. Its result is reported in the label beside it.
        pdf_btn = Btn(bar, T("pdf_button"), command=self._export_pdf, font=self.f_small,
                      bg=CARD_BG, fg=MUTED, padx=14, pady=7)
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
        now = datetime.now()
        current = (now.year, now.month)
        listed = logged if current in logged else [current] + logged
        # Open on this month, or — if nothing has been played in it yet — on the
        # last month that was, so the screen never opens on an empty chart.
        self._month = current if current in logged else (logged[0] if logged else current)
        self._all_sessions = sessions
        picker = Dropdown(bar, [(None, T("month_all"))]
                          + [(ym, month_label(ym)) for ym in listed],
                          self._month, self._select_month, font=self.f_small,
                          bg=CARD_BG, fg=TEXT, padx=14, pady=7)
        picker.pack(side="left")
        Tooltip(picker, T("month_tip"), self.f_hint)

        # Only this frame is rebuilt when the month changes, so the header — and
        # the pick your eye just left — stays put.
        self._progress_body = tk.Frame(f, bg=BG)
        self._progress_body.pack(fill="both", expand=True)
        self._render_progress()

    def _select_month(self, ym: tuple[int, int] | None) -> None:
        self._month = ym
        self._render_progress()

    def _render_progress(self):
        """Chart + table for the month on show — the whole body of the screen."""
        body = self._progress_body
        for w in body.winfo_children():
            w.destroy()

        sessions = [s for s in self._all_sessions
                    if resultslog.in_month(s, self._month)]
        # Kept for the PDF export, which prints exactly what is on the screen.
        self._chart, self._sessions = None, sessions
        self._n_label.config(text=T("n_sessions", n=len(sessions)))

        if not sessions:
            tk.Label(body, text=T("no_month_results"), font=self.f_body,
                     fg=MUTED, bg=BG).pack(expand=True)
            return

        # The chart takes a third of the height; the table gets the rest and scrolls.
        tk.Label(body, text=T("chart_title"), font=self.f_small, fg=MUTED, bg=BG,
                 anchor="w").pack(fill="x", padx=32, pady=(6, 0))
        chart = TimeChart(body, sessions, self.f_small, note_font=self.f_tag,
                          height=max(190, int(self.winfo_height() * 0.30)))
        chart.pack(fill="x", padx=28, pady=(2, 12))
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", padx=28)
        self._chart = chart

        self._build_results_table(body, sessions)

    def _export_pdf(self):
        """The progress screen on A4: same chart, same rows, current palette.

        With a month selected that is the monthly report — the filter and the
        export are the same view, so what you see is what gets printed.
        """
        if not self._sessions:                    # an empty month has nothing to print
            self._flash_pdf_status(T("no_month_results"), MUTED)
            return
        stamp = datetime.now()
        path = filedialog.asksaveasfilename(
            parent=self, title=T("pdf_dialog"), defaultextension=".pdf",
            initialdir=str(resultslog.RESULTS_DIR),
            initialfile=pdfreport.default_filename(LANG, stamp, self._month),
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:                                   # the parent changed their mind
            return
        try:
            pdfreport.export_progress(
                path, self._sessions, self._chart,
                palette=THEMES[THEME],
                labels={
                    "title": T("progress_title"),
                    "subtitle": T("pdf_exported_on", when=stamp.strftime("%d/%m/%Y %H:%M")),
                    # The month it covers, named on the report itself; empty when
                    # the whole history is being printed.
                    "period": "" if self._month is None else month_label(self._month),
                    "chart_title": T("chart_title"),
                    "n_sessions": T("n_sessions", n=len(self._sessions)),
                    "footer": T("pdf_footer"),
                    "date": T("col_date"),
                    "famille": T("col_family"),
                    "temps": T("col_time"),
                    "cartes": T("col_cards"),
                    "note": T("col_note"),
                    "temps_moyen": T("col_avg"),
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
        self.after(ms, lambda: self._pdf_status.winfo_exists()
                   and self._pdf_status.config(text=""))

    def _build_results_table(self, parent, sessions):
        """Every logged session, oldest first — the paper sheet, on screen."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        # `yscrollincrement=1` makes a `yview_scroll` unit one pixel: a canvas
        # left at 0 scrolls a tenth of its own height per unit instead, so a
        # single nudge of the wheel would jump most of the table at a time.
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0,
                           yscrollincrement=1)
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
        canvas.bind("<Configure>", lambda e: e.widget is canvas
                    and canvas.itemconfig(window, width=e.width))
        table.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
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

        cols = (
            (T("col_date"), "w", 3),
            (T("col_family"), "w", 4),
            (T("col_time"), "e", 3),
            (T("col_cards"), "e", 2),
            (T("col_note"), "e", 2),
            (T("col_avg"), "e", 4),
        )
        for i, (_, _, weight) in enumerate(cols):
            table.grid_columnconfigure(i, weight=weight)

        for i, (label, anchor, _) in enumerate(cols):
            tk.Label(table, text=label, font=self.f_small, fg=MUTED, bg=BG,
                     anchor=anchor, padx=14, pady=8).grid(row=0, column=i, sticky="nsew")
        tk.Frame(table, bg=BORDER, height=1).grid(row=1, column=0, columnspan=len(cols),
                                                  sticky="ew")

        # Newest first: the session that just finished sits right under the
        # header, and the history scrolls away below it.
        for r, s in enumerate(reversed(sessions)):
            rowbg = SURFACE if r % 2 else CARD_BG
            cells = (
                (s.day.strftime("%d/%m/%Y") if s.day else "—", TEXT),
                (s.famille or "—", TEXT),
                (resultslog.fmt_duration(s.seconds) if s.seconds is not None
                 else (s.raw_time or "—"), ACCENT),
                (str(s.cards) if s.cards else "—", MUTED),
                (resultslog.fmt_note(s.note) or "—", ACCENT),
                (f"{s.avg:.1f} s" if s.avg is not None else "—", GOOD),
            )
            for i, ((text, fg), (_, anchor, _)) in enumerate(zip(cells, cols)):
                tk.Label(table, text=text, font=self.f_small,
                         fg=MUTED if text == "—" else fg,   # a blank is never a value
                         bg=rowbg, anchor=anchor, padx=14, pady=7
                         ).grid(row=r + 2, column=i, sticky="nsew")

        # Every cell scrolls the canvas it sits in: the canvas' path name is
        # appended to each row's bindtags, so a wheel event delivered to a Label
        # is looked up on the canvas too, and `wheel` above sees it.
        for w in (table, *table.winfo_children()):
            w.bindtags((*w.bindtags(), str(canvas)))

    def _save_session(self, cards, total_time, correct):
        stamp = datetime.now()

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
                    }
                    for c in cards
                ],
            }
            fname = STATS_DIR / f"{stamp.strftime('%Y%m%d-%H%M%S')}-{self.mode}.json"
            fname.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        except OSError:
            pass  # never let logging break the game


def main():
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
