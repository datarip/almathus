"""Almathus — a kid-friendly mental-math game (addition, subtraction, ...).

Pure-stdlib Tkinter app. Runs anywhere Python has Tk:

    uv run almathus          # or:  python main.py

Flow:  Settings -> 5-4-3-2-1-GO! countdown -> cards -> reinforcement round -> recap.
Per-card time is measured so a parent can see which operations need work.

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
from tkinter import font as tkfont

import results as resultslog

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
        "col_avg": "Temps moyen / carte",
        "n_sessions": "{n} sessions",
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
        "col_avg": "Avg time / card",
        "n_sessions": "{n} sessions",
    },
}

OP_SPOKEN = {
    "fr": {"addition": "plus", "subtraction": "moins",
           "multiplication": "fois", "division": "divisé par"},
    "en": {"addition": "plus", "subtraction": "minus",
           "multiplication": "times", "division": "divided by"},
}


def T(key: str, **kw) -> str:
    s = STRINGS[LANG].get(key, key)
    return s.format(**kw) if kw else s


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
    """The answer a kid meant, out of every number heard in one utterance.

    Thinking out loud is how kids do mental maths, so the mic hears the question
    read back ("quinze moins quatre…") far more often than a bare answer. Those
    utterances used to be submitted as an answer, which failed the card and moved
    on before the kid had said anything. None here means "not an answer" — the
    app just keeps listening, so hesitating aloud costs nothing.
    """
    if not nums:
        return None
    if nums[:2] == [card.a, card.b]:       # the question, read back
        nums = nums[2:]
    if not nums:
        return None
    if card.answer in nums:                # "quinze moins quatre, ça fait onze"
        return card.answer
    n = nums[-1]                           # self-correction: the last number wins
    if len(nums) == 1 and n in (card.a, card.b):
        return None                        # a lone operand is the card, not an answer
    return n


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
    # Round y steps a parent reads at a glance: 5 s up to 15 min.
    STEPS = (5, 10, 15, 20, 30, 60, 90, 120, 180, 300, 600, 900)

    def __init__(self, parent, sessions, font, height=220):
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
        self._card_done = False     # answer committed: ignore anything else on this card
        self._voice_mic_bad = False

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
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(40, self._pump)

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

    # -- frame helper ------------------------------------------------------ #
    def _clear(self):
        if self._tick_job:
            self.after_cancel(self._tick_job)
            self._tick_job = None
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
        # preload the STT model + raise mic gain so the first spoken answer is instant
        if self.voice_enabled.get() and self.stt and self.stt.available():
            self.stt.warmup()
            self.stt.boost_input_volume()
        self.countdown(5)

    def countdown(self, n: int):
        self._clear()
        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)
        tk.Label(f, text=str(n) if n > 0 else T("go"),
                 font=self.f_giant, fg=ACCENT if n > 0 else GOOD, bg=BG
                 ).place(relx=0.5, rely=0.45, anchor="center")
        if n > 0:
            self.after(850, lambda: self.countdown(n - 1))
        else:
            self.game_start = time.perf_counter()
            self._advance_after(750)

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

        # on-screen keypad (kid-friendly; works with touch/mouse too)
        kp = tk.Frame(body, bg=BG)
        kp.grid(row=3, column=0)
        self._build_keypad(kp)

        self.card_start = time.perf_counter()
        self._tick()

        # read the operation aloud, then listen continuously (parallel to the keypad)
        if self.voice_enabled.get() and self.stt and self.stt.available():
            self._speak_operation(card, then=lambda: self._voice_arm(card))

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

    def _voice_status(self, state):
        if state == "no_mic":
            self._voice_mic_bad = True   # stop re-arming; capture is silent
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
            self.answer_var.set(str(n))
            self.submit()                     # auto-submit, in parallel with the keypad
            return
        # nothing recognized this round: keep listening (unless the mic is unusable)
        if not self._voice_mic_bad and self.voice_enabled.get():
            self._voice_arm(target)

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
            self._flash(T("correct"), GOOD)
            self._advance_after(650)
        else:
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

        # speak the same encouragement shown on screen, in a warm voice
        if self.tts and self.tts.available():
            spoken = f"{msg} {T('score_spoken', c=correct, n=n)}"
            self.after(400, lambda: self.tts.speak(spoken))

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
        sessions = resultslog.load_sessions()

        f = tk.Frame(self.container, bg=BG)
        f.pack(fill="both", expand=True)

        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(18, 6))
        Btn(bar, T("back"), command=self.show_settings, font=self.f_body,
            bg=CARD_BG, fg=TEXT).pack(side="left")
        tk.Label(bar, text=T("progress_title"), font=self.f_title, fg=ACCENT, bg=BG
                 ).pack(side="left", padx=20)
        tk.Label(bar, text=T("n_sessions", n=len(sessions)), font=self.f_small,
                 fg=MUTED, bg=BG).pack(side="right")

        if not sessions:
            tk.Label(f, text=T("no_results"), font=self.f_body, fg=MUTED, bg=BG
                     ).pack(expand=True)
            return

        # The chart takes a third of the height; the table gets the rest and scrolls.
        tk.Label(f, text=T("chart_title"), font=self.f_small, fg=MUTED, bg=BG,
                 anchor="w").pack(fill="x", padx=32, pady=(6, 0))
        chart = TimeChart(f, sessions, self.f_small,
                          height=max(190, int(self.winfo_height() * 0.30)))
        chart.pack(fill="x", padx=28, pady=(2, 12))
        tk.Frame(f, bg=BORDER, height=1).pack(fill="x", padx=28)

        self._build_results_table(f, sessions)

    def _build_results_table(self, parent, sessions):
        """Every logged session, oldest first — the paper sheet, on screen."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        table = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=table, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(window, width=e.width))
        table.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Only steal the wheel while the pointer is over the table. The pointer
        # is usually over a row Label, not the canvas, so the binding has to be
        # global — hence dropping it again on the way out, and on destroy (a
        # screen change kills the canvas without ever sending <Leave>).
        def wheel(ev):
            if canvas.winfo_exists():
                canvas.yview_scroll(-1 * (ev.delta or 1), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        canvas.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        cols = (
            (T("col_date"), "w", 3),
            (T("col_family"), "w", 4),
            (T("col_time"), "e", 3),
            (T("col_cards"), "e", 2),
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
                (f"{s.avg:.1f} s" if s.avg is not None else "—", GOOD),
            )
            for i, ((text, fg), (_, anchor, _)) in enumerate(zip(cells, cols)):
                tk.Label(table, text=text, font=self.f_small,
                         fg=MUTED if text == "—" else fg,   # a blank is never a value
                         bg=rowbg, anchor=anchor, padx=14, pady=7
                         ).grid(row=r + 2, column=i, sticky="nsew")

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
