"""Almathus — a kid-friendly mental-math game (addition, subtraction, ...).

Pure-stdlib Tkinter app. Runs anywhere Python has Tk:

    uv run almathus          # or:  python main.py

Flow:  Settings -> 5-4-3-2-1-GO! countdown -> cards -> reinforcement round -> recap.
Per-card time is measured so a parent can see which operations need work.
Sessions are logged to ./stats/*.json for progress tracking.
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

try:
    import voice as voicemod
except Exception:  # keep the game usable even if voice.py is absent/broken
    voicemod = None

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DEFAULT_CARDS = 20

# Operand ranges per mode. Addition/subtraction stay in 0..20 as requested;
# multiplication/division use smaller factors so results stay kid-sized.
RANGES = {
    "addition": {"max": 20},
    "subtraction": {"max": 20},
    "multiplication": {"max_factor": 10},
    "division": {"max_factor": 10},
}

LANG = "fr"  # default language; "en" also supported

STRINGS = {
    "fr": {
        "title": "Almathus — les maths en jouant",
        "how_many": "Combien de cartes ?",
        "choose_mode": "Choisis ton opération :",
        "addition": "Addition\n+",
        "subtraction": "Soustraction\n−",
        "multiplication": "Multiplication\n×",
        "division": "Division\n÷",
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
        "voice_off_hint": "🎤 Voix indisponible : {reason}",
        "listening": "🎧 à l'écoute…",
        "thinking": "🤔 …",
        "didnt_get": "Je n'ai pas compris, réessaie 🎤",
        "mic_denied": "Micro non autorisé — Réglages ▸ Confidentialité ▸ Microphone",
        "score_spoken": "Tu as eu {c} sur {n}.",
    },
    "en": {
        "title": "Almathus — math through play",
        "how_many": "How many cards?",
        "choose_mode": "Choose your operation:",
        "addition": "Addition\n+",
        "subtraction": "Subtraction\n−",
        "multiplication": "Multiplication\n×",
        "division": "Division\n÷",
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
        "voice_off_hint": "🎤 Voice unavailable: {reason}",
        "listening": "🎧 listening…",
        "thinking": "🤔 …",
        "didnt_get": "I didn't catch that, try again 🎤",
        "mic_denied": "Mic not allowed — Settings ▸ Privacy ▸ Microphone",
        "score_spoken": "You got {c} out of {n}.",
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
BG = "#0f1226"
CARD_BG = "#1b1f3b"
ACCENT = "#ffd23f"
GOOD = "#3ddc97"
BAD = "#ff6b6b"
TEXT = "#f5f6ff"
MUTED = "#9aa0c8"
MODE_COLORS = {
    "addition": "#3ddc97",
    "subtraction": "#4d9de0",
    "multiplication": "#e15554",
    "division": "#e1bc29",
}
OP_SYMBOL = {
    "addition": "+",
    "subtraction": "−",
    "multiplication": "×",
    "division": "÷",
}

STATS_DIR = Path(__file__).resolve().parent / "stats"


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


def make_card(mode: str) -> Card:
    if mode == "addition":
        m = RANGES["addition"]["max"]
        a, b = random.randint(0, m), random.randint(0, m)
        return Card(a, b, mode, a + b)
    if mode == "subtraction":
        m = RANGES["subtraction"]["max"]
        a = random.randint(0, m)
        b = random.randint(0, a)          # keep the result >= 0
        return Card(a, b, mode, a - b)
    if mode == "multiplication":
        m = RANGES["multiplication"]["max_factor"]
        a, b = random.randint(0, m), random.randint(0, m)
        return Card(a, b, mode, a * b)
    if mode == "division":
        m = RANGES["division"]["max_factor"]
        b = random.randint(1, m)          # no divide by zero
        answer = random.randint(0, m)
        a = answer * b                    # guarantees an integer result
        return Card(a, b, mode, answer)
    raise ValueError(mode)


def make_deck(mode: str, n: int) -> list[Card]:
    return [make_card(mode) for _ in range(n)]


# --------------------------------------------------------------------------- #
# A colored button that works everywhere.
#
# macOS Tk renders native `tk.Button`s and ignores bg/fg, so themed buttons come
# out white-on-white. A Label honors colors on every platform, so we drive one
# like a button with mouse bindings.
# --------------------------------------------------------------------------- #


class Btn(tk.Label):
    def __init__(self, parent, text, command=None, bg=CARD_BG, fg=TEXT, font=None,
                 active_bg=ACCENT, active_fg="#10121f", padx=14, pady=10, **kw):
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
# App
# --------------------------------------------------------------------------- #


class Almathus(tk.Tk):
    def __init__(self):
        super().__init__()
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
        self.deck: list[Card] = []
        self.results: list[Card] = []     # completed cards, in play order
        self.retry_queue: list[Card] = []
        self.idx = 0
        self.current: Card | None = None
        self.card_start = 0.0
        self.game_start = 0.0
        self._tick_job = None

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
        self.f_giant = tkfont.Font(family="Helvetica", size=120, weight="bold")
        self.f_huge = tkfont.Font(family="Helvetica", size=90, weight="bold")
        self.f_big = tkfont.Font(family="Helvetica", size=48, weight="bold")
        self.f_mode = tkfont.Font(family="Helvetica", size=30, weight="bold")
        self.f_title = tkfont.Font(family="Helvetica", size=28, weight="bold")
        self.f_body = tkfont.Font(family="Helvetica", size=20)
        self.f_small = tkfont.Font(family="Helvetica", size=15)

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

        col = tk.Frame(f, bg=BG)
        col.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(col, text="🧮 Almathus", font=self.f_big, fg=ACCENT, bg=BG).pack(pady=(6, 2))

        # number of cards
        tk.Label(col, text=T("how_many"), font=self.f_title, fg=TEXT, bg=BG).pack(pady=(24, 8))
        self.count_var = tk.IntVar(value=self.n_cards)
        row = tk.Frame(col, bg=BG)
        row.pack()

        def bump(d):
            self.count_var.set(max(1, min(99, self.count_var.get() + d)))

        Btn(row, "−", command=lambda: bump(-1), font=self.f_mode,
            bg=CARD_BG, fg=TEXT, width=2).pack(side="left", padx=8)
        tk.Label(row, textvariable=self.count_var, font=self.f_big, fg=ACCENT, bg=BG,
                 width=3).pack(side="left")
        Btn(row, "+", command=lambda: bump(1), font=self.f_mode,
            bg=CARD_BG, fg=TEXT, width=2).pack(side="left", padx=8)

        quick = tk.Frame(col, bg=BG)
        quick.pack(pady=8)
        for q in (5, 10, 20, 30, 50):
            Btn(quick, str(q), command=lambda v=q: self.count_var.set(v), font=self.f_small,
                bg=CARD_BG, fg=MUTED, width=3, padx=8, pady=4).pack(side="left", padx=4)

        # mode buttons
        tk.Label(col, text=T("choose_mode"), font=self.f_title, fg=TEXT, bg=BG).pack(pady=(28, 12))
        grid = tk.Frame(col, bg=BG)
        grid.pack()
        for i, m in enumerate(("addition", "subtraction", "multiplication", "division")):
            Btn(grid, T(m), command=lambda mm=m: self.start_game(mm), font=self.f_mode,
                bg=MODE_COLORS[m], fg="#10121f", active_bg=TEXT, active_fg="#10121f",
                width=12, padx=16, pady=22,
                ).grid(row=i // 2, column=i % 2, padx=16, pady=14)

        self._build_voice_toggle(f)

    def _build_voice_toggle(self, parent):
        # Only offer the toggle when voice input actually works; otherwise show
        # nothing (a pip/dependency string is not something a kid should see).
        if not (self.stt and self.stt.available()):
            return

        btn = Btn(parent, "", font=self.f_body, pady=8)

        def refresh():
            on = self.voice_enabled.get()
            btn.config(text=f"{T('voice_toggle')} : {'ON ✅' if on else 'OFF'}")
            btn.set_colors(GOOD if on else CARD_BG, "#10121f" if on else MUTED)

        btn._command = lambda: (self.voice_enabled.set(not self.voice_enabled.get()), refresh())
        btn.pack(side="bottom", pady=8)
        refresh()

    # ------------------------------------------------------------------ #
    # Screen 2: countdown
    # ------------------------------------------------------------------ #
    def start_game(self, mode: str):
        self.mode = mode
        self.n_cards = self.count_var.get()
        self.deck = make_deck(mode, self.n_cards)
        self.results = []
        self.retry_queue = []
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
            self.after(750, self.next_card)

    # ------------------------------------------------------------------ #
    # Screen 3: play
    # ------------------------------------------------------------------ #
    def next_card(self):
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
                         bg="#12152e", fg=ACCENT, insertbackground=ACCENT)
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
        if self.current is not target or self._voice_mic_bad:
            return
        self.stt.listen(
            on_result=lambda n: self._post(lambda: self._voice_result(n, target)),
            on_state=lambda s: self._post(lambda: self._voice_status(s)),
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
        if self.current is not target:       # card already moved on
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
                    cmd, col, fg = (lambda: self.answer_var.set("")), BAD, "#10121f"
                elif k == "✓":
                    cmd, col, fg = self.submit, GOOD, "#10121f"
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

        # record time + outcome only on the very first attempt of a card
        if card.correct_first_try is None:
            card.time_taken = time.perf_counter() - self.card_start
            card.correct_first_try = is_correct
            self.results.append(card)
            if self.idx < len(self.deck):
                self.idx += 1
            if not is_correct:
                self.retry_queue.append(card)  # reinforcement later

        if is_correct:
            self._flash(T("correct"), GOOD)
            self.after(650, self.next_card)
        else:
            self._flash(T("wrong", a=card.answer), BAD)
            self.answer_var.set("")
            self.after(1500, self.next_card)

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
            bg=GOOD, fg="#10121f").pack(side="left", padx=12)
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

    def _save_session(self, cards, total_time, correct):
        try:
            STATS_DIR.mkdir(exist_ok=True)
            stamp = datetime.now()
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
