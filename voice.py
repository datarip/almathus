"""Voice I/O for Almathus — text-to-speech + speech-to-text.

Two independent, gracefully-degrading pieces:

* ``TTS``          — speaks the question and the encouragement in a warm
                     feminine voice: the **Piper** model for the language (see
                     :mod:`voices`), which is the same neural voice on macOS,
                     Windows and Linux. Without that model — a fresh install, or
                     a language Piper has none for — it falls back to the
                     operating system's own voice (macOS ``say``, Linux
                     ``espeak-ng``, ``pyttsx3``), and to a silent no-op if even
                     that is missing.

* ``SpeechInput``  — records from the mic with simple voice-activity detection
                     (one open stream per card, auto-stops when the speaker goes
                     quiet, so nothing is lost between two answers), recognizes
                     the spoken number with a small, per-language **Vosk** model,
                     and accepts it only if it is numbers and nothing else.
                     Needs ``sounddevice`` + ``numpy`` + ``vosk`` and a model
                     installed by the setup screen (see :mod:`languages`); if any
                     is missing it reports itself unavailable and the game simply
                     stays keypad-only.

                     Calibrates itself to the room: ``.calibrate()`` listens to
                     the ambient noise in the background (nothing on screen,
                     nothing to wait for), and every threshold that decides "that
                     was an answer" is a factor over what it measured, not a
                     constant. The same measurement gives a noise spectrum, which
                     is subtracted from the utterance before it is recognized.
                     Both are kept current between answers, so a room that gets
                     noisier through the day carries the gates with it.

Nothing here ever raises into the UI: probe with ``.available()`` first, and all
callbacks are plain values so the caller can marshal them onto the Tk main loop.

Environment overrides:
    ALMATHUS_TTS_BACKEND   "piper", "say", "espeak" or "pyttsx3" to force one
                           (default: auto — Piper first, then the OS voice)
    ALMATHUS_TTS_VOICE     voice name for the native backends, or a path to an
                           .onnx model for Piper
    ALMATHUS_VOSK_MODEL    path to a Vosk model directory, bypassing the per-user
                           store the setup screen installs into
    ALMATHUS_MODELS_DIR    where installed models are kept (tests / packaging)
    ALMATHUS_VOICES_DIR    where Piper voices are kept (tests / packaging)
"""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import wave
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import languages
import voices

# Set ALMATHUS_VOICE_DEBUG=1 to trace the whole voice path on stderr.
_DEBUG = os.environ.get("ALMATHUS_VOICE_DEBUG") not in (None, "", "0")


def _dbg(*a):
    if _DEBUG:
        print("[voice]", *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Text-to-speech
# --------------------------------------------------------------------------- #

# One voice for every platform: a Piper model (see voices.py) when it is on
# disk, otherwise whatever the operating system has. The system voice is no
# longer the normal path — it is the state of a fresh install, and the only
# option for the few languages Piper has no model for.
#
# The selections below are what makes the fallback listenable rather than
# alphabetical: `say -v '?'` lists the joke voices (Bells, Boing, Whisper) and
# the new character voices (Flo, Grandma, Eddie) in every locale, and sorting
# them by name is how the app ended up reading English with Albert, a MacinTalk
# voice from 1994, in every language but French.
#
# Curated adult voices per app language, women first. Region codes are their own
# keys because British and Indian English are not read by a US voice.
_SAY_VOICES: dict[str, dict[str, tuple[str, ...]]] = {
    "fr": {
        "f": ("Amélie", "Audrey", "Marie", "Julie", "Chantal", "Virginie"),
        "m": ("Thomas", "Jacques"),
    },
    "en-us": {
        "f": ("Samantha", "Ava", "Allison", "Susan", "Zoe", "Kate"),
        "m": ("Alex", "Tom", "Nathan", "Daniel", "Fred", "Ralph"),
    },
    "en-gb": {
        # Kate and Serena are the good ones when they have been installed; Flo
        # and the others are the modern character voices a stock Mac does have.
        "f": ("Kate", "Serena", "Fiona", "Stephanie", "Flo", "Shelley", "Sandy"),
        "m": ("Daniel", "Oliver"),
    },
    "en-in": {
        "f": ("Tara", "Veena"),
        "m": ("Aman", "Rishi"),
    },
    "en": {
        "f": ("Samantha", "Karen", "Moira", "Tessa", "Fiona", "Serena"),
        "m": ("Daniel",),
    },
    "es": {"f": ("Mónica", "Paulina"), "m": ("Diego", "Jorge")},
    "de": {"f": ("Anna", "Petra", "Katja"), "m": ("Markus", "Yannick", "Viktor")},
    "it": {"f": ("Alice", "Federica"), "m": ("Luca", "Paolo")},
    "pt": {"f": ("Joana", "Luciana", "Catarina"), "m": ("Felipe", "Daniel")},
    "nl": {"f": ("Ellen", "Nathalie"), "m": ("Xander", "Maarten")},
    "pl": {"f": ("Zosia",), "m": ("Krzysztof",)},
    "ru": {"f": ("Milena",), "m": ("Yuri",)},
    "uk": {"f": ("Lesya",), "m": ("Dmytro",)},
    "cs": {"f": ("Zuzana", "Iveta"), "m": ()},
    "sv": {"f": ("Alva",), "m": ()},
    "tr": {"f": ("Yelda",), "m": ("Tolga",)},
    "ar": {"f": (), "m": ("Majed",)},
    "hi": {"f": ("Lekha", "Pooja"), "m": ("Kabir",)},
    "te": {"f": ("Geeta",), "m": ()},
    "ja": {"f": ("Kyoko",), "m": ("Otoya", "Hattori")},
    "ko": {"f": ("Yuna",), "m": ()},
    "cn": {"f": ("Tingting", "Meijia", "Sinji"), "m": ("Li-mu",)},
    "vi": {"f": ("Linh",), "m": ()},
    "ca": {"f": ("Montse",), "m": ()},
    "kz": {"f": ("Aru",), "m": ()},
}

# Never preferred while anything else exists in the language: the joke voices and
# the oldest MacinTalk set. They are still usable, because a silly voice that a
# child can hear beats silence.
_SAY_CARTOON = frozenset(
    {
        "Albert",
        "Bad News",
        "Bahh",
        "Bells",
        "Boing",
        "Bubbles",
        "Cellos",
        "Fred",
        "Good News",
        "Jester",
        "Junior",
        "Kathy",
        "Organ",
        "Ralph",
        "Superstar",
        "Trinoids",
        "Whisper",
        "Wobble",
        "Zarvox",
    }
)

# `say` locales are not always the app's code (Kazakh is kk_KZ, Chinese zh_CN).
_SAY_BASE = {"kz": "kk", "cn": "zh"}


def _list_say_voices() -> list[tuple[str, str]]:
    """Return [(name, locale)] from `say -v '?'`, robust to spaces in names.

    Lines look like:  "Amélie              fr_CA    # Bonjour!"
                or:   "Amélie (French (Canada)) fr_CA    # Bonjour!"
                or:   "Majed               ar_001   # مرحبًا!"
    We anchor on the locale token (xx_XX, xx-XX or a numeric region) and take
    everything before it. The variant suffix is dropped so one name matches the
    catalog entry whether or not the listing spells out "(English (US))".
    """
    out = subprocess.run(
        ["say", "-v", "?"],  # noqa: S607 - a macOS system binary, resolved on PATH
        capture_output=True,
        text=True,
        timeout=5,
        check=False,  # `say` exits non-zero when no voices are installed
    ).stdout
    found: list[tuple[str, str]] = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([A-Za-z]{2}[-_][A-Za-z0-9]{2,3})\b", line)
        if m:
            name = m.group(1).split(" (")[0].strip()
            pair = (name, m.group(2))
            if pair not in found:  # the listing repeats enhanced variants
                found.append(pair)
    return found


def _say_voice(lang: str, listing: list[tuple[str, str]]) -> str | None:
    """The best installed `say` voice for a language, or None if it has none.

    Ranked by: a woman's voice at all, then a curated adult voice, then the
    curated order (Amélie before Audrey), with an exact locale as the last
    tie-break so en-gb prefers a British voice over Daniel's other recordings.
    """
    base = _SAY_BASE.get(lang.split("-", 1)[0], lang.split("-", 1)[0])
    table = _SAY_VOICES.get(lang) or _SAY_VOICES.get(base) or {"f": (), "m": ()}
    women, men = set(table["f"]), set(table["m"])
    candidates = [(n, loc) for n, loc in listing if loc.lower().startswith(base)]
    if not candidates:
        return None
    target = lang.replace("-", "_").lower()

    def rank(candidate: tuple[str, str]) -> tuple[bool, int, int, bool]:
        name, loc = candidate
        woman = name in women
        curated = woman or name in men
        tier = 3 if curated else (0 if name in _SAY_CARTOON else 2)
        order = (table["f"] if woman else table["m"]).index(name) if curated else 99
        exact = loc.lower().replace("-", "_").startswith(target)
        return (woman, tier, -order, exact)

    candidates.sort(key=rank, reverse=True)
    return candidates[0][0]


# espeak-ng, the Linux fallback; `+f3` is its female variant. The app's code is
# not always espeak's (Chinese is "cmn", Kazakh "kk").
_ESPEAK_LANG = {
    "fr": "fr",
    "en-us": "en-us",
    "en-gb": "en-gb",
    "en-in": "en-gb",
    "es": "es",
    "de": "de",
    "it": "it",
    "pt": "pt",
    "nl": "nl",
    "pl": "pl",
    "ru": "ru",
    "uk": "uk",
    "cs": "cs",
    "sv": "sv",
    "tr": "tr",
    "ar": "ar",
    "ar-tn": "ar",
    "fa": "fa",
    "hi": "hi",
    "gu": "gu",
    "te": "te",
    "ja": "ja",
    "ko": "ko",
    "cn": "cmn",
    "vi": "vi",
    "ca": "ca",
    "eo": "eo",
    "ka": "ka",
    "ky": "ky",
    "kz": "kk",
    "tg": "tg",
    "uz": "uz",
}


def _espeak_voice(lang: str) -> str | None:
    code = _ESPEAK_LANG.get(lang) or _ESPEAK_LANG.get(lang.split("-", 1)[0])
    return f"{code}+f3" if code else None


def _have_piper() -> bool:
    """Is the Piper stack importable? Checked without importing onnxruntime."""
    return (
        importlib.util.find_spec("piper") is not None
        and importlib.util.find_spec("sounddevice") is not None
    )


def _no_window() -> int:
    """Keep a helper process from flashing a console on Windows."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class TTS:
    """The game's voice. Piper when its model is installed, else the OS's own."""

    def __init__(self, lang: str = "fr", rate: int = 180):
        self.lang = lang
        self.rate = rate
        self._proc: subprocess.Popen | None = None
        self._say = shutil.which("say")
        self._espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self._voice: str | None = None
        self._backend = "none"
        self._fallback: tuple[str, str | None] = ("none", None)
        self._piper: Any = None  # a piper.PiperVoice once loaded
        self._piper_path: Path | None = None
        self._piper_speaker: str | None = None
        self._piper_thread: threading.Thread | None = None
        self._play_until = 0.0  # performance-clock second the speech ends
        self._gen = 0  # so a superseded utterance cannot touch the current one
        self._pyttsx = False
        self._setup(lang)

    # -- which voice ------------------------------------------------------- #

    def _setup(self, lang: str) -> None:
        self._fallback = self._native(lang)
        forced = os.environ.get("ALMATHUS_TTS_BACKEND", "auto").strip().lower()
        entry = voices.VOICES.get(lang)
        if (
            entry is not None
            and voices.is_installed(lang)
            and _have_piper()
            and forced in ("auto", "piper")
        ):
            self._backend = "piper"
            self._voice = entry.model
            self._piper_speaker = entry.speaker
            self._piper_path = voices.voice_path(lang)
            override = os.environ.get("ALMATHUS_TTS_VOICE")
            if override and Path(override).suffix == ".onnx":
                self._piper_path = Path(override)
            self._piper_thread = threading.Thread(target=self._load_piper, daemon=True)
            self._piper_thread.start()
        elif forced in ("auto", "say", "espeak", "pyttsx3"):
            self._backend, self._voice = self._fallback
        _dbg("tts:", self.describe())

    def _native(self, lang: str) -> tuple[str, str | None]:
        """The operating system's own voice, as a (backend, voice) pair."""
        forced = os.environ.get("ALMATHUS_TTS_BACKEND", "auto").strip().lower()
        override = os.environ.get("ALMATHUS_TTS_VOICE")
        if self._say and forced in ("auto", "say"):
            try:
                listing = _list_say_voices()
            except Exception as e:
                _dbg("say -v '?' failed:", repr(e))
                listing = []
            return "say", override or _say_voice(lang, listing)
        if self._espeak and forced in ("auto", "espeak"):
            return "espeak", override or _espeak_voice(lang)
        if forced in ("auto", "pyttsx3"):
            try:
                import pyttsx3  # noqa: F401
            except Exception:
                return "none", None
            else:
                self._pyttsx = True
                return "pyttsx3", None
        return "none", None

    def _load_piper(self) -> None:
        try:
            from piper import PiperVoice

            if self._piper_path is not None:
                self._piper = PiperVoice.load(str(self._piper_path))
            _dbg("piper ready:", self._voice)
        except Exception as e:
            _dbg("piper load failed:", repr(e))
            self._piper = None

    def describe(self) -> str:
        """What will speak, for the debug log and the setup screen."""
        if self._backend == "piper":
            return f"piper {self._voice}"
        if self._backend in ("say", "espeak"):
            return f"{self._backend} {self._voice or '(system default)'}"
        return self._backend

    def available(self) -> bool:
        return self._backend != "none"

    # -- speaking ---------------------------------------------------------- #

    def speak(self, text: str, on_done: Callable[[], None] | None = None):
        """Speak asynchronously; interrupts any speech already in progress."""
        self.stop()
        if self._backend == "piper":
            gen = self._gen
            threading.Thread(
                target=self._piper_speak, args=(text, on_done, gen), daemon=True
            ).start()
        else:
            self._native_speak(text, on_done)

    def _native_speak(self, text: str, on_done: Callable[[], None] | None) -> None:
        if self._backend == "say" and self._say:
            cmd = [self._say, "-r", str(self.rate)]
            if self._voice:
                cmd += ["-v", self._voice]
            cmd.append(text)
            self._spawn(cmd, on_done)
        elif self._backend == "espeak" and self._espeak:
            cmd = [self._espeak, "-s", str(self.rate)]
            if self._voice:
                cmd += ["-v", self._voice]
            cmd.append(text)
            self._spawn(cmd, on_done)
        elif self._backend == "pyttsx3" and self._pyttsx:
            threading.Thread(
                target=self._pyttsx_speak, args=(text, on_done), daemon=True
            ).start()
        elif on_done:
            on_done()

    def _spawn(self, cmd: list[str], on_done: Callable[[], None] | None) -> None:
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_no_window(),
            )
        except Exception as e:
            _dbg("speak failed:", repr(e))
            self._proc = None
            if on_done:
                on_done()
            return
        if on_done:
            threading.Thread(
                target=self._wait_then, args=(self._proc, on_done), daemon=True
            ).start()

    def _piper_speak(
        self, text: str, on_done: Callable[[], None] | None, gen: int
    ) -> None:
        if gen != self._gen:  # a newer utterance took over while this one waited
            if on_done:
                on_done()
            return
        # Hold the mic off for the whole synthesis as well as the playback:
        # `speaking()` is a clock, and the synthesizer is silent but not done.
        self._play_until = time.perf_counter() + 30.0
        handed_off = False
        try:
            if self._piper_thread is not None:
                self._piper_thread.join()
            voice = self._piper
            if voice is None:  # the model would not load: use the OS voice
                self._backend, self._voice = self._fallback
                if gen == self._gen:
                    self._play_until = 0.0
                self._native_speak(text, on_done)
                handed_off = True
                return
            import numpy as np
            import sounddevice as sd
            from piper.config import SynthesisConfig

            speaker = None
            if self._piper_speaker:
                speaker = voice.config.speaker_id_map.get(self._piper_speaker)
            config = SynthesisConfig(
                length_scale=max(0.5, min(2.0, 180 / max(1, self.rate))),
                speaker_id=speaker,
            )
            chunks = list(voice.synthesize(text, config))
            pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
            if not pcm or gen != self._gen:
                return
            audio = np.frombuffer(pcm, dtype=np.int16)
            sample_rate = chunks[0].sample_rate
            self._play_until = time.perf_counter() + len(audio) / sample_rate + 0.05
            sd.play(audio, sample_rate)
            sd.wait()
        except Exception as e:
            _dbg("piper speak failed:", repr(e))
        finally:
            if not handed_off:
                if gen == self._gen:
                    self._play_until = 0.0
                if on_done:
                    on_done()

    def _wait_then(self, proc, on_done):
        try:
            proc.wait()
        finally:
            on_done()

    def _pyttsx_speak(self, text, on_done):
        try:
            import pyttsx3

            eng = pyttsx3.init()
            eng.setProperty("rate", self.rate)
            eng.say(text)
            eng.runAndWait()
        except Exception as e:
            _dbg("pyttsx3 speak failed:", repr(e))
        finally:
            if on_done:
                on_done()

    def speaking(self) -> bool:
        """Is the app talking right now? The mic must not be armed while it is,
        or the question read aloud gets captured as the answer to itself."""
        if self._proc and self._proc.poll() is None:
            return True
        return time.perf_counter() < self._play_until

    def stop(self):
        self._gen += 1
        self._play_until = 0.0
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
        if self._backend == "piper":
            with contextlib.suppress(Exception):
                import sounddevice as sd

                sd.stop()


# --------------------------------------------------------------------------- #
# Number parsing — STRICT: an utterance is either only numbers, or nothing
# --------------------------------------------------------------------------- #
#
# The old parser picked the numbers out of whatever it was handed and threw the
# rest away, so "je sais pas" (heard by a number-grammar recognizer as "quinze
# seize [unk]") scored the card 16, and "quinze moins quatre" — the kid reading
# the question aloud — scored it 4. Measured over a 68-clip French corpus, 19 of
# the 30 non-number utterances came back as an answer.
#
# So the rule is now the other way round: an utterance is accepted only if
# *every* token in it is a number word (or a digit, or the "et"/"and" that glues
# a compound number together). One stray word — a filler, an operator, a
# recognizer's "[unk]" — and the whole utterance is refused. That takes the same
# corpus down to 3 of 30 (see the table further down). A refused utterance is not
# an answer at all: the card stays up and the game keeps listening, which costs
# the child nothing.

# 0..19 — the words that can only be the ones place of a number.
_UNIT_WORDS = {
    "fr": {
        "zero": 0,
        "un": 1,
        "une": 1,
        "deux": 2,
        "trois": 3,
        "quatre": 4,
        "cinq": 5,
        "six": 6,
        "sept": 7,
        "huit": 8,
        "neuf": 9,
        "dix": 10,
        "onze": 11,
        "douze": 12,
        "treize": 13,
        "quatorze": 14,
        "quinze": 15,
        "seize": 16,
    },
    "en": {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
    },
}
# 20..90 — a tens word can carry a ones word after it ("vingt et un", "sixty two").
_TEN_WORDS = {
    "fr": {
        "vingt": 20,
        "vingts": 20,
        "trente": 30,
        "quarante": 40,
        "cinquante": 50,
        "soixante": 60,
        # Belgian / Swiss forms, said by plenty of French-speaking children
        "septante": 70,
        "octante": 80,
        "huitante": 80,
        "nonante": 90,
    },
    "en": {
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fourty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
    },
}
_SCALE_WORDS = {
    "fr": {"cent": 100, "cents": 100, "mille": 1000},
    "en": {"hundred": 100, "hundreds": 100, "thousand": 1000},
}
# The only non-number words allowed inside a number, and only between two of them.
_GLUE_WORDS = {"fr": {"et"}, "en": {"and"}}

# French builds 70/80/90 by arithmetic ("quatre-vingt-dix" is 4×20+10, not
# 4+20+10). Rewriting those to digits up front keeps the parser a plain
# left-to-right accumulator.
_PRESUB = {
    "fr": ((r"\bquatre\s+vingts?\s+dix\b", " 90 "), (r"\bquatre\s+vingts?\b", " 80 ")),
    "en": (),
}

# What a recognizer emits for "something I have no word for". Never an answer.
_UNKNOWN_TOKENS = {"unk", "[unk]", "<unk>", "unknown"}


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _hand_key(lang: str) -> str | None:
    """The key into the hand-tuned tables, or None for a generated language.

    French and English (and their regional variants) are hand-written because
    they encode what num2words cannot: the French 70/80/90 arithmetic and the
    Belgian forms. Every other language is built from num2words instead, so this
    returns None to say "generate it".
    """
    base = lang.split("-", 1)[0].lower()
    if base == "fr":
        return "fr"
    if base == "en":
        return "en"
    return None


def _tokenize(text: str, lang: str) -> list[str]:
    """A transcript, lowercased and accent-free, as a list of word/digit tokens.

    Hyphens and apostrophes split ("dix-sept" -> dix, sept); sentence punctuation
    the recognizer adds ("huit." / "Eight?") is dropped; anything else is kept as
    a token so the caller can refuse it.

    The French rewrites (``_PRESUB``) run only on the hand-tuned path; a
    generated language is compared against its own num2words spelling verbatim.
    """
    low = _strip_accents(text.lower())
    low = re.sub(r"[.,;:!?…\"'`’\-–—/]+", " ", low)
    # ...only then the rewrites, so they see "quatre vingt dix" whether it was
    # written hyphenated or spaced.
    key = _hand_key(lang)
    if key is not None:
        for pattern, repl in _PRESUB[key]:
            low = re.sub(pattern, repl, low)
    return low.split()


@functools.cache
def _generated(lang: str) -> tuple[dict[tuple[str, ...], int], str] | None:
    """Canonical 0..100 phrases and the Vosk grammar for a generated language.

    Returns ``None`` when the language has no num2words converter — the six the
    library lacks (Hindi, Gujarati, Chinese, Georgian, Kyrgyz, Uzbek) — or when
    num2words is not installed at all. Those languages simply run keypad-only,
    the same contract as a missing voice stack everywhere else.

    Cached: the tables are built once per language per process, on the STT
    thread, from a library that is pure Python and quick to call.
    """
    entry = languages.BY_CODE.get(lang)
    n2w = entry.num2words if entry is not None else None
    if n2w is None:
        return None
    try:
        from num2words import num2words
    except Exception:
        return None
    phrases: dict[tuple[str, ...], int] = {}
    words: set[str] = set()
    try:
        for n in range(101):
            tokens = tuple(_tokenize(num2words(n, lang=n2w), lang))
            if tokens:
                phrases[tokens] = n
                words.update(tokens)
    except Exception:
        return None
    if not phrases:
        return None
    # Vosk reads the grammar as one space-joined word list, plus "[unk]" for
    # "that was speech, but not a word I have".
    grammar = json.dumps([" ".join(sorted(words)), "[unk]"])
    return phrases, grammar


def _strict_generated(
    tokens: list[str], phrases: dict[tuple[str, ...], int], longest: int
) -> list[int] | None:
    """Numbers from a token list, matched against whole canonical phrases.

    A generated language has no unit/ten grammar to accumulate: num2words gives
    one exact spelling per number, so the utterance is read left to right,
    taking the longest canonical phrase that starts here. A token that begins no
    phrase is a non-number word and refuses the whole utterance — the same
    "numbers and nothing else" rule, matched instead of accumulated.
    """
    out: list[int] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in _UNKNOWN_TOKENS:
            return None
        if re.fullmatch(r"\d+", tok):
            out.append(int(tok))
            i += 1
            continue
        for length in range(min(longest, n - i), 0, -1):
            value = phrases.get(tuple(tokens[i : i + length]))
            if value is not None:
                out.append(value)
                i += length
                break
        else:
            return None  # a word that is not a number: refuse it all
    return out


def _hand_numbers(tokens: list[str], key: str) -> list[int] | None:
    """The hand-tuned accumulator over French/English number words."""
    units, tens = _UNIT_WORDS[key], _TEN_WORDS[key]
    scales, glue = _SCALE_WORDS[key], _GLUE_WORDS[key]

    out: list[int] = []
    cur: int | None = None
    last = ""  # "unit" | "ten" | "scale" — what the previous token was
    last_val = 0

    def flush():
        nonlocal cur, last
        if cur is not None:
            out.append(cur)
        cur, last = None, ""

    for tok in tokens:
        if tok in _UNKNOWN_TOKENS:
            return None
        if tok in glue:
            continue  # "vingt ET un", "one hundred AND five"
        if re.fullmatch(r"\d+", tok):
            val, kind = int(tok), ("ten" if int(tok) >= 20 else "unit")
        elif tok in scales:
            cur = (cur or 1) * scales[tok]
            last, last_val = "scale", scales[tok]
            continue
        elif tok in tens:
            val, kind = tens[tok], "ten"
        elif tok in units:
            val, kind = units[tok], "unit"
        else:
            return None  # a word that is not a number: refuse it all
        # Where does this token go — on the number being built, or a new one?
        if kind == "ten":
            if last in ("unit", "ten"):
                flush()  # "vingt trente" is two numbers, not one
            cur = (cur or 0) + val
        else:  # a ones word
            # Two ones words in a row start a new number ("trois quatre"), except
            # the French teens, which are literally "ten seven" / "ten eight".
            if last == "unit" and not (last_val == 10 and val in (7, 8, 9)):
                flush()
            cur = (cur or 0) + val
        last, last_val = kind, val
    flush()
    return out


def strict_numbers(text: str, lang: str = "fr") -> list[int] | None:
    """The numbers in an utterance that is *only* numbers — else None.

    ``None`` and ``[]`` mean different things and both mean "no answer":
    ``None`` is "this was speech, but not a number" (refuse it), ``[]`` is
    "nothing was said". Callers treat both the same; the distinction is kept
    because it is what the debug log needs to explain a refusal.

    >>> strict_numbers("huit", "fr")
    [8]
    >>> strict_numbers("vingt et un", "fr")
    [21]
    >>> strict_numbers("quinze moins quatre", "fr") is None
    True
    >>> strict_numbers("one hundred and five", "en")
    [105]
    >>> strict_numbers("i don't know", "en") is None
    True
    """
    if not text or not text.strip():
        return []
    tokens = _tokenize(text, lang)
    if not tokens:
        return []
    key = _hand_key(lang)
    if key is not None:
        return _hand_numbers(tokens, key)
    generated = _generated(lang)
    if generated is None:
        return None  # no number words for this language: nothing can match
    phrases, _ = generated
    longest = max(len(phrase) for phrase in phrases)
    return _strict_generated(tokens, phrases, longest)


def numbers_in(text: str, lang: str = "fr") -> list[int]:
    """Every number in a strictly-numeric transcript, in the order it was said.

    An utterance that says anything besides numbers yields ``[]``: it is not an
    answer, and the caller must keep listening rather than score the card.
    """
    nums = strict_numbers(text, lang)
    return nums or []


def parse_number(text: str, lang: str = "fr") -> int | None:
    """The single number a strictly-numeric transcript states, or None.

    The LAST one wins, which handles self-correction ("trois, quatre").
    """
    nums = numbers_in(text, lang)
    return nums[-1] if nums else None


# --------------------------------------------------------------------------- #
# Speech-to-text (mic capture + VAD + Vosk)
# --------------------------------------------------------------------------- #

_SAMPLE_RATE = 16000
_BLOCK = 320  # 20 ms blocks
_PRE_TIMEOUT = 5.0  # mic health check: this much silence, and *nothing* heard
_SILENCE_HANG = 0.35  # stop this long after speech ends (snappy response)
_MAX_DURATION = 6.0  # hard cap on a single utterance, from its onset
_VOICE_BAND = (300.0, 3400.0)  # Hz — speech band; cuts low rumble & high hiss


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _envf_opt(name: str) -> float | None:
    """A number from the environment, or None if it was never set.

    The difference from ``_envf`` matters for the gates that are normally
    measured: "not set" means *calibrate me*, and there is no default that can
    stand for that.
    """
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return None


# --- "was that actually a spoken answer?" ---------------------------------- #
# The onset detector is deliberately trigger-happy (WebRTC VAD at its most
# sensitive) so a soft answer is never missed. That leaves it starting on room
# tone, a stomach gurgling under the laptop, a chair. Those captures used to go
# straight to Vosk, which — asked to choose among number words — obliges: 0.6 s
# of room tone at peak 41 came back "cent et" and scored the card as 100.
#
# So the utterance is judged AFTER the fact, on three measurements (thresholds
# from measured speech-vs-noise separation; see the constants' comments):
# Each is overridable from the environment, so a room can be tuned without an edit.
_MIN_SNR = _envf("ALMATHUS_VOICE_MIN_SNR", 3.0)
# peak must clear the room's own noise by this factor
#
# --- and the level gate, which is measured, not baked in ------------------- #
#
# There used to be one absolute number here (220 band RMS) for "said out loud".
# It was measured once, on one mic, in one quiet room, at one distance — and it
# is the wrong shape of thing. Loudness at the mic is not a property of the
# voice: it falls with the square of the distance (a child two metres away
# arrives ~15 dB down on the same child at arm's length) and it rises with the
# input gain. A constant cannot be right for both, and set for a quiet room at
# arm's length it simply goes deaf at two metres — which is what happened.
#
# What "said out loud" actually means is *clearly above this room, right now*.
# So the gate is measured: the app listens to the room at startup (see
# ``SpeechInput.calibrate``) and keeps re-measuring it between utterances, and
# the bar sits a fixed factor above whatever the room is doing. Outside, at
# noon, with cars going past, it rises by itself; at night in a bedroom it drops
# and picks up a small voice from across the room. The whisper gate survives
# this move — a murmur does not stand 3x above the room, at any distance — and
# it stays honest at both ends, which a constant never was.
_LEVEL_OVER_ROOM = _envf("ALMATHUS_VOICE_LEVEL_OVER_ROOM", 3.0)
# how far above the measured room a voice must stand
_MIN_LEVEL_FLOOR = _envf("ALMATHUS_VOICE_MIN_LEVEL_FLOOR", 45.0)
# ...but never ask for less than this in absolute band
# RMS, so a mic sitting in an anechoic silence (room
# near 0) cannot end up with a gate of nothing.
_MIN_LEVEL_BLIND = _envf("ALMATHUS_VOICE_MIN_LEVEL_BLIND", 120.0)
# the gate before the room has ever been measured
# (calibration refused, or the very first blocks of the
# very first capture). Deliberately mid-range: it was
# the shipped default before the whisper gate went up.
_MIN_LEVEL = _envf_opt("ALMATHUS_VOICE_MIN_LEVEL")
# a hard override. Set it and nothing is measured — this
# is the escape hatch for a room where the automatic
# gate reads wrong, and it is why the variable is kept.
_MIN_VOICED = _envf("ALMATHUS_VOICE_MIN_VOICED", 0.25)
# fraction of energetic frames that must be *voiced* — i.e.
# show vocal-fold pitch. This is the one that matters: over
# TTS speech (adult and child pitch, clean and at 24 dB SNR)
# it measured 0.45-1.00, over noise at every level 0.00.
# A whisper has no vocal-fold vibration, so it lands with
# the noise; that is exactly why whispers must not count.
_NACF_MIN = _envf("ALMATHUS_VOICE_NACF", 0.40)
# per-frame normalised autocorrelation peak that is "voiced"
_F0_BAND = (100.0, 600.0)  # Hz — pitch search range, low man to shouting child

# --- listening to the room ------------------------------------------------- #
# The onset detector and the "still talking?" test used to have absolute floors
# in them too (60 and 40 band RMS), with the same problem: in a room noisier
# than that they fired on the room, and in a quiet one they were above a distant
# voice. Both are now the same shape as the level gate — a factor over the
# measured room, with a small absolute floor underneath.
_ONSET_OVER_ROOM = _envf("ALMATHUS_VOICE_ONSET_OVER_ROOM", 2.5)
_ONSET_FLOOR = _envf("ALMATHUS_VOICE_ONSET_FLOOR", 20.0)
_OFFSET_OVER_ROOM = 1.5  # ...for the tail of an utterance, which may trail off
_OFFSET_FLOOR = _envf("ALMATHUS_VOICE_OFFSET_FLOOR", 20.0)

_CALIB_SECS = _envf("ALMATHUS_VOICE_CALIB_SECS", 1.5)
# how long the startup listen to the room lasts
_CALIB_SETTLE = 0.35  # ...after dropping this much: a stream's first blocks are
# the driver waking up, not the room
_ROOM_WINDOW = 2.0  # seconds of pre-onset audio kept as "what the room sounds
# like", both to re-measure the level between utterances and
# to subtract as noise from the utterance that follows
_ROOM_PCTL = 25  # the room is the quiet quarter of that window, not its mean:
# a door, a cough or a word in the run-up must not become
# "the room" and take the gates up with it

# --- subtracting the room from the answer ---------------------------------- #
# Spectral subtraction: the room's average magnitude spectrum, measured in the
# seconds before the child speaks, is taken back out of the utterance bin by bin
# before it reaches the recognizer. This is what a fan, a road or a café does to
# a recognizer — it does not make the speech quieter, it fills the gaps between
# the formants — and it is exactly what subtracting a *spectrum* fixes and a
# band-pass cannot.
#
# Conservative on purpose. Over-subtracting leaves "musical noise" (isolated
# surviving bins warbling), which costs more recognition than the noise did, so
# the over-subtraction factor is mild and no bin is ever taken down more than
# ~18 dB. And if it still hurts, nothing is lost: an utterance the recognizer
# cannot read after cleaning is decoded a second time from the untouched audio
# (see ``_run_job``).
_DENOISE = os.environ.get("ALMATHUS_VOICE_DENOISE", "auto").lower()
_DENOISE_ALPHA = _envf("ALMATHUS_VOICE_DENOISE_ALPHA", 1.5)
_DENOISE_FLOOR = _envf("ALMATHUS_VOICE_DENOISE_FLOOR", 0.12)
_DENOISE_MIN_ROOM = _envf("ALMATHUS_VOICE_DENOISE_MIN_ROOM", 15.0)
# below this the room is already silent: subtracting it
# can only do harm, so "auto" leaves the audio alone


def level_gate(room: float | None) -> float:
    """The loudness an utterance must reach, for a room measured at ``room``.

    Kept module-level so ``mic_check.py`` reports the same number the game will
    actually apply, rather than a constant that no longer decides anything.
    """
    if _MIN_LEVEL is not None:
        return _MIN_LEVEL  # hard override: measure nothing
    if room is None:
        return _MIN_LEVEL_BLIND
    return max(_MIN_LEVEL_FLOOR, room * _LEVEL_OVER_ROOM)


def _nfft(sr: int) -> int:
    """Analysis window for the spectral work: the power of two nearest 32 ms."""
    return 1 << max(8, int(sr * 0.032).bit_length() - 1)


_WARMUP = object()  # sentinel job: preload the model on the STT thread

# Vosk number-only grammar. Constraining the vocabulary to number words is what
# makes isolated short words work at all — a general model hears the French "un"
# as "Mm"/"On", but asked to choose among numbers Vosk lands on one. "[unk]" is
# what lets it say "that was speech, but not a number" instead of being forced to
# pick a number word for every sound it hears.
_VOSK_GRAMMAR = {
    "fr": (
        '["zéro un une deux trois quatre cinq six sept huit neuf dix onze douze '
        "treize quatorze quinze seize dix-sept dix-huit dix-neuf vingt trente "
        "quarante cinquante soixante quatre-vingt quatre-vingts septante nonante "
        'cent cents et", "[unk]"]'
    ),
    "en": (
        '["zero one two three four five six seven eight nine ten eleven twelve '
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty "
        'thirty forty fifty sixty seventy eighty ninety hundred and", "[unk]"]'
    ),
}


def _grammar_for(lang: str) -> str | None:
    """The Vosk number grammar for a language, or None if it has no number words.

    fr/en return their hand-tuned list; every other language gets one generated
    from num2words (0..100). None means there is nothing to constrain the
    recognizer to, so voice is off for that language and the keypad is used.
    """
    key = _hand_key(lang)
    if key is not None:
        return _VOSK_GRAMMAR[key]
    generated = _generated(lang)
    return generated[1] if generated is not None else None


# --- how sure Vosk has to be that it heard numbers and only numbers ---------- #
# With a number-only grammar Vosk always *can* map a sound onto number words, so
# its N-best list is read the strict way: the winning hypothesis has to be one
# that is entirely numbers, and it has to be within `_VOSK_ALT_GAP` of the
# best-scoring hypothesis overall. Reading it the old way — "the first
# alternative that contains a number anywhere" — is what let "attends euh" score
# a card 7 (it was alternative #2, "sept [unk]", 0.6 behind "[unk]").
#
# Measured on a 68-clip French corpus (38 spoken numbers, 24 non-number
# utterances, 6 noise clips), each padded with the room tone a real capture keeps
# around an utterance, with the veto below switched on:
#     gap         numbers accepted    non-numbers accepted
#     old rule          37/38               19/30   <- the bug
#     0.30              33/38                3/30
#     0.50              33/38                3/30   <- default
#     1.00              33/38                5/30
#     1.50              34/38                5/30
# Past 0.5 the gap only buys false accepts. What survives at 0.5 is "euh", "non"
# and "oh non" — one-syllable French fillers that genuinely sound like "un" and
# "neuf"; no threshold here separates them, and the cost is now one wrong answer
# on a card rather than a card silently skipped.
_VOSK_ALT_GAP = _envf("ALMATHUS_VOICE_ALT_GAP", 0.5)
_VOSK_ALTS = 6  # how many hypotheses to ask Vosk for

# Second opinion: the same audio decoded by the FULL model, with no grammar to
# push it towards numbers. If that decode is a confident run of words and those
# words are not numbers, the utterance was speech — just not an answer — and the
# grammar decode is overruled. Two independent decoders must agree that this was
# a number before a card is ever scored from it. Worth 1 fewer false accept and
# 'tu peux répéter' at the default gap.
_VETO_CONF = _envf("ALMATHUS_VOICE_VETO_CONF", 0.9)
_VETO_WORDS = 2  # ...over at least this many words


class SpeechInput:
    def __init__(self, lang: str = "fr"):
        self.lang = lang
        self._cancel = threading.Event()
        self._has_vosk = False  # set by _probe when the vosk module imports
        self._reason = ""
        self._sd: Any = None  # the sounddevice module, if it imported
        self._np: Any = None  # the numpy module, if it imported
        self._silent = (
            False  # set True when the last capture was pure silence (no mic access)
        )
        self._silent_streak = (
            0  # consecutive silent captures (2 in a row = really no mic)
        )
        self._busy = False  # guard against overlapping listen() calls
        self._rec_sr = _SAMPLE_RATE
        self._jobs: queue.Queue = queue.Queue()  # audio -> the one STT thread
        # owns & runs the model (Vosk's Kaldi recognizer is not thread-safe)
        self._stt_thread: threading.Thread | None = None
        self._vosk_model: Any = None
        self._vosk_ok: bool | None = None  # None=unknown, False=unavailable
        # What the room sounds like with nobody talking: the band RMS every gate
        # is measured against, and the magnitude spectrum subtracted from an
        # utterance before it is recognized. Seeded by calibrate() at startup and
        # kept current between utterances, so a room that gets louder through the
        # day (or a game played outside) moves the gates with it instead of going
        # deaf or triggering on traffic.
        self._room: float | None = None
        self._room_spec: Any = None  # (samplerate, nfft, magnitudes)
        self._room_blocks: deque = deque()  # the last _ROOM_WINDOW of pre-onset audio
        self._room_levels: deque = deque()  # ...and its per-block band RMS
        self._calibrating = False
        self._mic_lock = threading.Lock()  # calibration vs. a capture: one at a time
        self._audio_q: queue.Queue = (
            queue.Queue()
        )  # mic blocks, filled while the stream is open
        self._stream_params: tuple[int, int] | None = None  # cached
        self._blk = int(_SAMPLE_RATE * 0.02)
        self._boosted = False  # did we raise the OS mic input gain?
        self._last_raw: Any = None  # last capture, un-band-passed (for Vosk)
        self._last_dirty: Any = None  # ...the same capture, un-denoised
        # the open mic stream, so we can close it on quit
        self._active_stream: Any = None
        self._stopped = False  # latched on quit: no more captures
        self._probe()
        # A language with no number words (num2words lacks a converter, or the
        # library is absent) cannot be answered by voice; say so rather than
        # arming a mic that would never score a card. The keypad still works.
        if self._sd and self._np and _grammar_for(self.lang) is None:
            self._reason = "reconnaissance vocale indisponible pour cette langue"

    # -- capability probing ------------------------------------------------ #
    def _probe(self):
        try:
            import numpy as np
            import sounddevice as sd

            self._sd, self._np = sd, np
        except Exception:
            self._reason = "audio libs manquantes (pip: sounddevice numpy)"
            return
        try:
            import vosk  # noqa: F401

            self._has_vosk = True
        except Exception:
            self._reason = "vosk introuvable (pip: vosk)"

    def available(self) -> bool:
        if not (self._sd and self._np and self._has_vosk):
            return False
        if _grammar_for(self.lang) is None:
            return False  # no number words for this language: keypad only
        return self._vosk_model_dir() is not None

    def reason(self) -> str:
        return self._reason

    # -- public API -------------------------------------------------------- #
    def listen(
        self,
        on_result: Callable[[int | None], None],
        on_state: Callable[[str], None] | None = None,
        pick: Callable[[list], int | None] | None = None,
    ):
        """Record one utterance in a background thread, then call on_result(int|None).

        ``pick`` turns the numbers heard into the one answer to report (None =
        heard nothing usable). It is how the caller rejects an utterance that
        isn't an answer at all; the default keeps the last number said.

        on_state("listening"|"thinking"|"too_quiet"|"no_mic") lets the UI show
        progress and say why nothing happened. Both callbacks fire from a worker
        thread — marshal to Tk with widget.after(0, ...).
        """
        if self._stopped:
            return  # app is quitting — never reopen the mic
        # Before the busy check, not after: a recognizer thread that died left
        # `_busy` stuck True, and checking first meant every later listen() was
        # refused with nothing left alive to un-stick it.
        self._ensure_stt_thread()
        if self._busy:
            _dbg("listen() ignored — already busy")
            return  # one capture at a time
        _dbg("listen() -> starting capture")
        self._busy = True
        self._cancel.clear()
        # Record on a throwaway thread (the mic is fine cross-thread); transcription
        # is handed to the single STT thread that owns the model.
        threading.Thread(
            target=self._capture, args=(on_result, on_state, pick), daemon=True
        ).start()

    def listening(self) -> bool:
        """Is a capture actually running right now?

        The caller re-arms from the result callback, so a dropped or refused
        callback would end the chain and leave the card deaf with no sign of it.
        Its watchdog polls this instead of trusting the chain.
        """
        return self._busy and not self._cancel.is_set() and not self._stopped

    def cancel(self):
        """End the current listening session (the card is over).

        Deliberately does NOT close the stream. Closing PortAudio from the Tk
        thread while the audio callback is running is what could deadlock
        CoreAudio and wedge the whole app mid-game; the stream is kept for the
        next card and only torn down off the UI thread — see ``release``.
        """
        self._cancel.set()

    def release(self):
        """Close the mic when the game is left (menu, recap, stats).

        The teardown runs on a worker thread: even a stubborn CoreAudio close can
        then only strand that thread, never the UI, which is free to keep drawing.
        """
        self._cancel.set()
        self._request_close()

    def stop(self):
        """Stop capture and release the microphone (used on quit / language
        change). Never blocks the caller: the close happens on a worker."""
        self._stopped = True
        self._cancel.set()
        self._request_close()

    def _request_close(self):
        """Tear the stream down under the mic lock, on a thread we own."""

        def work():
            if not self._mic_lock.acquire(timeout=5.0):
                return  # a capture is still using it; it will close on its own
            try:
                self._close_stream()
            finally:
                self._mic_lock.release()

        threading.Thread(target=work, daemon=True).start()

    def _close_stream(self):
        s = self._active_stream
        self._active_stream = None
        if s is not None:
            _dbg("closing mic stream")
            for fn in ("abort", "close"):
                with contextlib.suppress(Exception):
                    getattr(s, fn)()

    def warmup(self):
        """Preload the recognizer on the STT thread so the first answer is instant."""
        self._ensure_stt_thread()
        self._jobs.put((_WARMUP, None, None, None, None, None))

    def calibrate(self, seconds: float = _CALIB_SECS) -> None:
        """Listen to the room for a moment and set the gates from what it hears.

        Returns immediately: the measuring happens on a daemon thread, shows
        nothing, says nothing and blocks nothing. Called at startup, so by the
        time the first card is up the app already knows whether it is in a
        bedroom at night or on a terrace next to a road, and has a spectrum of
        that room to subtract from what the child says.

        Cheap enough to repeat (it is also called when a game starts, in case the
        app has been open since this morning). It is a no-op while a capture is
        running — a card being answered has the mic, and the room is being
        re-measured from that same stream anyway.
        """
        if not (self._sd and self._np) or self._stopped or self._calibrating:
            return
        if self._busy:
            _dbg("calibrate: mic is busy, keeping the room we have")
            return
        self._calibrating = True
        threading.Thread(target=self._calibrate, args=(seconds,), daemon=True).start()

    def room_level(self) -> float | None:
        """The measured room, or None if it has never been heard. Diagnostics."""
        return self._room

    def boost_input_volume(self, level: int = 80):
        """Raise the macOS mic input gain (moderate — max gain over-amplifies noise
        and breaks speech detection). Registers an atexit restore for any exit."""
        if sys.platform != "darwin" or self._boosted:
            return

        # On a worker: `osascript` can take a moment, and the game starts with
        # this call — a slow answer must not hold the countdown frozen.
        def work():
            try:
                subprocess.run(  # noqa: S603 - argv list; level is coerced to int
                    ["osascript", "-e", f"set volume input volume {int(level)}"],  # noqa: S607
                    timeout=3,
                    capture_output=True,
                    check=False,  # a refused volume change is not worth an exception
                )
                self._boosted = True
                import atexit

                atexit.register(
                    self.restore_input_volume
                )  # covers X, Quit, Ctrl-C, crash-exit
                _dbg(f"boosted macOS input volume to {level}")
            except Exception as e:
                _dbg("input volume boost failed:", repr(e))

        threading.Thread(target=work, daemon=True).start()

    def restore_input_volume(self, level: int = 50):
        """Put the mic input gain back to a neutral 50% on exit — only if we raised
        it. Idempotent; safe to call from atexit and from main()'s finally."""
        if not self._boosted or sys.platform != "darwin":
            return
        self._boosted = False
        try:
            subprocess.run(  # noqa: S603 - argv list; level is coerced to int
                ["osascript", "-e", f"set volume input volume {int(level)}"],  # noqa: S607
                timeout=3,
                capture_output=True,
                check=False,  # a refused volume change is not worth an exception
            )
            _dbg(f"restored macOS input volume to {level}")
        except Exception as e:
            _dbg("input volume restore failed:", repr(e))

    # -- the room ---------------------------------------------------------- #
    def _calibrate(self, seconds: float):
        """Measure the room on a private thread. Never raises, never blocks a card."""
        try:
            # A stream we opened ourselves is ours to close: leaving it open
            # would keep the OS microphone indicator lit for as long as the app
            # is on screen, which is not "in the background" in any sense.
            ours = self._active_stream is None
            if not self._mic_lock.acquire(timeout=2.0):
                _dbg("calibrate: mic held by a capture, skipped")
                return
            try:
                # Same clearing listen() does before a capture: `_cancel` belongs
                # to whatever capture last ran, and every screen change sets it.
                # Nothing is in flight here (the busy check and the lock above
                # say so), so a flag left standing from the last card must not
                # end this measurement before it has read a single block —
                # which is exactly what it did.
                self._cancel.clear()
                sr, blk = self._ensure_stream()
                if sr is None:
                    _dbg("calibrate: no usable input device")
                    return
                blocks = self._collect(seconds, sr, blk)
                if blocks is None:
                    return
                self._learn_room(blocks, sr, seed=True)
            finally:
                if ours:
                    self._close_stream()
                self._mic_lock.release()
        except Exception as e:
            _dbg("calibrate failed:", repr(e))
        finally:
            self._calibrating = False

    def _collect(self, seconds: float, sr: int, blk: int):
        """``seconds`` of audio off the open stream, past the driver's warm-up."""
        block_secs = blk / sr
        want = int(seconds / block_secs)
        skip = int(_CALIB_SETTLE / block_secs)
        blocks: list[Any] = []
        while len(blocks) < want + skip:
            if self._stopped or self._cancel.is_set():
                # A card started, or the app is quitting: the room is measured
                # again next time, and between answers in any case.
                _dbg("calibrate: interrupted, the room keeps its old measurement")
                return None
            try:
                blocks.append(self._audio_q.get(timeout=0.5))
            except queue.Empty:
                _dbg("calibrate: stream gave nothing")
                return None
        return blocks[skip:] or None

    def _learn_room(self, blocks, sr: int, seed: bool = False):
        """Set the room level and its noise spectrum from a run of quiet blocks.

        The level is a low percentile rather than a mean: whatever is in the
        window, the quiet quarter of it is the room, and a chair scraping or a
        word said in the run-up does not get to raise the bar for the answer
        that follows. The spectrum is averaged over that same quiet quarter.
        """
        np = self._np
        levels = self._band_levels(blocks, sr)
        if levels is None:
            return
        room = float(np.percentile(levels, _ROOM_PCTL))
        quiet = [b for b, r in zip(blocks, levels, strict=False) if r <= room * 2.0]
        self._room = room
        spec = self._noise_spectrum(quiet or blocks, sr)
        if spec is not None:
            self._room_spec = (sr, _nfft(sr), spec)
        _dbg(
            f"room{' (startup)' if seed else ''}: level={room:.0f} "
            f"gate={level_gate(room):.0f} spectrum={'yes' if spec is not None else 'no'}"
        )

    def _band_levels(self, blocks, sr: int):
        """Per-block RMS in the speech band — the same measure the VAD uses."""
        np = self._np
        if not blocks:
            return None
        n = min(len(b) for b in blocks)
        if n < 8:
            return None
        x = np.concatenate([b[:n] for b in blocks]).astype(np.float32)
        sos, sosfilt, _ = self._make_bandpass(sr)
        if sos is not None:
            x = sosfilt(sos, x)
        b = x.reshape(len(blocks), n)
        return np.sqrt((b * b).mean(axis=1)) + 1e-9

    def _noise_spectrum(self, blocks, sr: int):
        """The room's average magnitude spectrum, at the denoiser's resolution."""
        np = self._np
        x = np.concatenate(blocks).astype(np.float32)
        nfft = _nfft(sr)
        frames = self._frames(x, nfft, nfft // 2)
        if frames is None:
            return None
        mag = np.abs(np.fft.rfft(frames * np.hanning(nfft + 1)[:nfft], axis=1))
        # ...and again only over the quiet frames of it, for the same reason.
        energy = mag.sum(axis=1)
        keep = energy <= np.percentile(energy, 60)
        return mag[keep].mean(axis=0) if keep.any() else mag.mean(axis=0)

    def _frames(self, x, nfft: int, hop: int):
        """``x`` as overlapping frames, or None if there is not even one."""
        np = self._np
        if len(x) < nfft:
            return None
        n = 1 + (len(x) - nfft) // hop
        idx = np.arange(nfft)[None, :] + hop * np.arange(n)[:, None]
        return x[idx]

    def _denoise(self, x, sr: int):
        """Take the room back out of an utterance, bin by bin (spectral subtraction).

        Returns the audio unchanged whenever cleaning it cannot help: no spectrum
        measured yet, a different samplerate than the one it was measured at, a
        room already quiet enough that there is nothing to subtract, or a clip too
        short for one analysis window.
        """
        np = self._np
        if _DENOISE == "0" or self._room_spec is None:
            return x
        spec_sr, nfft, noise = self._room_spec
        if spec_sr != sr:
            return x  # the device changed under us; the old spectrum is meaningless
        if _DENOISE != "1" and (self._room or 0.0) < _DENOISE_MIN_ROOM:
            return x
        hop = nfft // 2
        win = np.hanning(nfft + 1)[:nfft].astype(np.float32)
        pad = np.zeros(nfft, dtype=np.float32)
        xp = np.concatenate([pad, np.asarray(x, dtype=np.float32), pad, pad])
        frames = self._frames(xp, nfft, hop)
        if frames is None:
            return x
        spectra = np.fft.rfft(frames * win, axis=1)
        mag = np.abs(spectra)
        # Smoothed across time before the gain is computed: musical noise is
        # frame-to-frame jitter in the gain, and this is what stops it.
        sm = mag.copy()
        if len(sm) > 2:
            sm[1:-1] = (mag[:-2] + 2.0 * mag[1:-1] + mag[2:]) / 4.0
        gain = np.clip(1.0 - _DENOISE_ALPHA * noise / (sm + 1e-9), _DENOISE_FLOOR, 1.0)
        out = np.fft.irfft(spectra * gain, n=nfft, axis=1) * win
        y = np.zeros(len(xp), dtype=np.float32)
        wsum = np.zeros(len(xp), dtype=np.float32)
        w2 = win * win
        for i in range(len(out)):
            j = i * hop
            y[j : j + nfft] += out[i]
            wsum[j : j + nfft] += w2
        y /= np.maximum(wsum, 1e-6)
        return y[nfft : nfft + len(x)]

    # -- internals --------------------------------------------------------- #
    def _ensure_stt_thread(self):
        """Start the recognizer thread, and restart it if it ever went away.

        It must not be possible for this thread to be missing while the game
        thinks it is listening: ``_busy`` is cleared from it, so a dead one
        leaves the flag stuck and every later listen() refused — the card would
        stay up (which is right) but stone deaf (which is not).
        """
        t = self._stt_thread
        if t is not None and t.is_alive():
            return
        if t is not None:
            _dbg("STT thread had died — restarting it")
            self._busy = False
        self._stt_thread = threading.Thread(target=self._stt_loop, daemon=True)
        self._stt_thread.start()

    def _capture(self, on_result, on_state, pick=None):
        try:
            self._silent = False
            self._last_raw = self._last_dirty = None
            # The startup calibration reads from the same stream, so the two must
            # not interleave. It gives up after two seconds rather than wait for a
            # card to be answered; a card never waits for more than that.
            with self._mic_lock:
                audio = self._record(on_state)
            if _DEBUG and self._last_raw is not None:
                self._dump_capture(self._last_raw)  # listen back to what was heard
            # Two silent captures in a row before crying "no mic": the caller stops
            # re-arming on that signal, so one unlucky window (a stream hiccup, a
            # capture cut short) used to leave the kid talking to a deaf card.
            self._silent_streak = self._silent_streak + 1 if self._silent else 0
            if self._silent_streak >= 2 and on_state:
                _dbg(f"capture SILENT x{self._silent_streak} -> no_mic")
                on_state("no_mic")  # only silence -> no mic access / no usable device
            elif self._silent:
                _dbg("capture SILENT (1st) -> keep listening")
            # The un-filtered audio travels *with* the job. Reading it off the
            # instance at transcribe time meant a capture that returned nothing
            # could leave the previous utterance's audio in place, and the same
            # answer could be recognized twice.
            self._jobs.put(
                (audio, self._last_raw, self._last_dirty, on_result, on_state, pick)
            )
        except Exception as e:
            _dbg("capture EXCEPTION:", repr(e))
            self._jobs.put((None, None, None, on_result, on_state, pick))

    def _stt_loop(self):
        """The ONE thread that creates and runs the Vosk model, which is not
        thread-safe, so it must never be shared across threads.

        Nothing raised in here may end this loop. It used to be possible: a job
        tuple of the wrong shape killed the thread on the very first unpack, and
        from then on every answer was recognized by nobody at all — silently,
        because the exception went to a thread no one was watching.
        """
        while True:
            try:
                job = self._jobs.get()
            except Exception as e:  # queue itself is broken
                _dbg("STT queue EXCEPTION, thread ending:", repr(e))
                return
            try:
                self._run_job(job)
            except Exception as e:
                _dbg("STT job EXCEPTION (thread survives):", repr(e))
                self._busy = False

    def _run_job(self, job):
        audio, raw, dirty, on_result, on_state, pick = job
        if audio is _WARMUP:
            try:
                self._load_vosk()  # load the model (~1s), so the first answer is fast
            except Exception as e:
                _dbg("vosk warmup failed:", repr(e))
            return
        try:
            if audio is None or self._cancel.is_set():
                _dbg("no audio (timeout/cancel) -> None")
                self._busy = False  # before the callback: it re-arms from here
                on_result(None)
                return
            if on_state:
                on_state("thinking")
            # Vosk: fast (~100ms), number-grammar, and the accurate one for
            # isolated digits. Runs on the RAW audio (band-pass breaks Vosk).
            _t0 = time.monotonic()
            nums = self._vosk_numbers(raw if raw is not None else audio)
            _dbg(f"vosk -> {nums} ({(time.monotonic() - _t0) * 1000:.0f}ms)")
            # Noise subtraction is a bet: it usually helps in a loud room and can
            # smear a word in a quiet one. Nothing is staked on it — if the
            # cleaned audio says nothing, the untouched capture gets its own go.
            if not nums and dirty is not None and dirty is not raw:
                nums = self._vosk_numbers(dirty)
                _dbg(f"vosk (un-denoised retry) -> {nums}")
            parsed = pick(nums) if pick else (nums[-1] if nums else None)
            _dbg(f"picked -> {parsed}")
            self._busy = False  # a callback that re-arms must not be refused
            on_result(parsed)
        except Exception as e:
            _dbg("transcribe EXCEPTION:", repr(e))
            with contextlib.suppress(Exception):
                on_result(None)
        finally:
            self._busy = False

    def _pick_stream_params(self):
        """Pick a samplerate the current input device supports (devices change:
        Bluetooth, external mics...). Prefer 16 kHz; fall back to the device
        default and common rates. Returns (samplerate, blocksize) or (None, 0).

        Cached: probing costs ~45 ms of CoreAudio round-trips, which used to be
        paid again on every re-arm. Cleared whenever the stream fails to open,
        so a device change is still picked up.
        """
        if self._stream_params is not None:
            return self._stream_params
        sd = self._sd
        try:
            dev_sr = int(
                sd.query_devices(kind="input").get("default_samplerate") or 16000
            )
        except Exception:
            dev_sr = 16000
        # Prefer rates WebRTC VAD supports (8/16/32/48 kHz) so the VAD can run.
        for sr in (16000, 48000, 32000, 8000, dev_sr, 44100, 22050):
            try:
                sd.check_input_settings(samplerate=sr, channels=1, dtype="int16")
            except Exception as e:
                _dbg(f"rate {sr} unusable:", repr(e))
                continue
            # exactly 20 ms frames (WebRTC VAD needs 10/20/30)
            self._stream_params = (sr, int(sr * 0.02))
            return self._stream_params
        return None, 0

    def _judge(self, span, sr, peak, floor, voiced_blocks, min_voiced):
        """Was that a frank, spoken answer? -> (ok, a line of metrics for the log).

        -> (ok, why it was refused or None, a line of metrics for the log)

        Three questions: was it long enough to be a word, was it a voice at all
        rather than noise, and was that voice *raised* — said out loud rather than
        whispered at the mic.

        The voicing test runs first even though it costs the most, so that `why`
        can tell a voice that was too quiet apart from a thump that was. Only the
        first of those is worth answering with "louder, please"; saying it to a
        chair would teach the child to shout at nothing.
        """
        if voiced_blocks < min_voiced:
            return False, "short", f"too short ({voiced_blocks} blocks)"
        floor = floor or 0.0
        snr = peak / max(floor, 1.0)
        vf = self._voiced_fraction(self._np.concatenate(span), sr)
        stats = (
            f"peak={peak:.0f} snr={snr:.1f} room={floor:.0f} "
            f"gate={level_gate(floor or None):.0f} voiced={vf:.2f}"
        )
        if vf < _MIN_VOICED:
            return False, "not_voice", f"not a voice ({stats})"
        if peak < level_gate(floor or None) or snr < _MIN_SNR:
            return False, "faint", f"a voice, but whisper-quiet ({stats})"
        return True, None, stats

    def _voiced_fraction(self, raw, sr) -> float:
        """How much of this utterance was *voiced* — produced with the vocal folds
        vibrating — as a fraction of its energetic frames, in 0..1.

        Speech carries a pitch: the waveform repeats at F0, so its normalised
        autocorrelation has a tall peak at the pitch lag. Noise does not repeat,
        and neither does a whisper (the folds stay open — a whisper is filtered
        turbulence). Measured over TTS number words this reads 0.45-1.00, and
        over noise of any loudness 0.00, which is a far wider margin than mere
        loudness gives: quiet speech and loud noise overlap in level, never here.

        Runs on the raw capture (the speech band-pass would cut a deep voice's
        fundamental) after a high-pass that removes the sub-90 Hz rumble a laptop
        picks up through the desk — that rumble is periodic and would otherwise
        read as perfectly "voiced".
        """
        np = self._np
        x = np.asarray(raw, dtype=np.float32)
        win, hop = int(sr * 0.04), int(sr * 0.02)
        if len(x) < win:
            return 0.0
        x = self._highpass(x, sr)
        lo, hi = int(sr / _F0_BAND[1]), int(sr / _F0_BAND[0])
        starts = range(0, len(x) - win + 1, hop)
        frames = np.stack([x[i : i + win] for i in starts])
        frames -= frames.mean(axis=1, keepdims=True)
        energy = np.sqrt((frames * frames).mean(axis=1)) + 1e-9
        # Only frames carrying the utterance itself: the pre/post-roll silence
        # around it would otherwise dilute the fraction towards zero.
        loud = energy > max(float(energy.max()) * 0.25, 1.0)
        if not loud.any():
            return 0.0
        frames = frames[loud]
        # Autocorrelation via FFT (zero-padded so it stays linear, not circular).
        n = 1 << int(2 * win - 1).bit_length()
        spec = np.fft.rfft(frames, n=n, axis=1)
        ac = np.fft.irfft(spec * np.conj(spec), n=n, axis=1)[:, : hi + 1]
        zero = ac[:, 0]
        nacf = np.where(
            zero > 0, ac[:, lo : hi + 1].max(axis=1) / np.where(zero > 0, zero, 1), 0.0
        )
        return float(np.mean(nacf > _NACF_MIN))

    def _highpass(self, x, sr):
        """Drop everything under ~90 Hz (desk rumble), keep every voice pitch."""
        np = self._np
        try:
            from scipy.signal import butter, sosfilt

            return sosfilt(butter(2, 90.0, btype="high", fs=sr, output="sos"), x)
        except Exception:
            # No scipy: subtract a moving average, which is the same thing crudely.
            k = max(3, int(sr / 90.0)) | 1
            pad = np.pad(x, k // 2, mode="edge")
            return x - np.convolve(pad, np.ones(k) / k, mode="valid")[: len(x)]

    def _make_bandpass(self, sr):
        """Butterworth band-pass over the speech band. Returns (sos, sosfilt,
        sosfilt_zi) or (None, ...) if scipy is unavailable (then VAD uses raw RMS)."""
        try:
            from scipy.signal import butter, sosfilt, sosfilt_zi

            hi = min(_VOICE_BAND[1], sr / 2 - 100)  # stay under Nyquist
            sos = butter(4, [_VOICE_BAND[0], hi], btype="band", fs=sr, output="sos")
        except Exception:
            return None, None, None
        else:
            return sos, sosfilt, sosfilt_zi

    def _ensure_stream(self):
        """Open the mic stream if it isn't already, and keep it open for the whole
        card. Every block flows into ``_audio_q`` continuously, so the ~200 ms it
        takes to probe the device and start a stream no longer falls between two
        listens — a word spoken right as one capture ends is still recorded.
        Returns (samplerate, blocksize), or (None, 0) if the mic is unusable.
        """
        if self._active_stream is not None:
            return self._rec_sr, self._blk
        sr, blk = self._pick_stream_params()
        if sr is None:
            return None, 0

        def cb(indata, _frames, _tinfo, _status):
            self._audio_q.put(indata[:, 0].copy())

        try:
            stream = self._sd.InputStream(
                samplerate=sr, channels=1, dtype="int16", blocksize=blk, callback=cb
            )
            stream.start()
        except Exception as e:
            _dbg("stream open failed:", repr(e))
            self._stream_params = None  # re-probe next time (device may have changed)
            return None, 0
        actual = int(getattr(stream, "samplerate", sr) or sr)
        if actual != sr:
            _dbg(f"record: requested {sr} Hz but device opened at {actual} Hz")
        self._rec_sr, self._blk = actual, blk
        while not self._audio_q.empty():  # drop anything stale from a previous stream
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                break
        self._active_stream = stream
        _dbg(f"record: opened mic stream samplerate={actual} blocksize={blk}")
        return actual, blk

    def _record(self, on_state):
        if self._stopped:
            return None
        np = self._np
        sr, blk = self._ensure_stream()
        if sr is None:
            self._silent = True  # no usable input device
            return None
        if self._cancel.is_set():  # cancel() raced with the open
            self._close_stream()
            return None
        # The mic stays open between cards, so drop anything the stream gathered
        # while nothing was listening, or it would arrive as the pre-roll of this
        # capture and be judged as part of the child's answer.
        while not self._audio_q.empty():
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                break
        block_secs = blk / sr
        pre = int(0.25 / block_secs)  # keep a little before the onset
        post = int(0.25 / block_secs)  # ...and a little after the tail
        min_voiced = max(4, int(0.16 / block_secs))  # need ≥0.16s of real speech —
        # shorter than any spoken digit,
        # longer than a click or a tap

        sos, sosfilt, sosfilt_zi = self._make_bandpass(sr)
        zi = {"z": sosfilt_zi(sos) * 0.0 if sos is not None else None}

        # Responsive segmentation: WebRTC VAD (most sensitive) triggers the ONSET fast
        # (~40 ms, catches soft speech — no need to talk loud); voice-band ENERGY
        # decides the OFFSET (energy drops when the speaker stops), so it can't run
        # away on ambient the way a WebRTC-only stop can.
        wvad = None
        if sr in (8000, 16000, 32000, 48000):
            try:
                import webrtcvad

                wvad = webrtcvad.Vad(0)  # 0 = most sensitive
            except Exception:
                wvad = None
        _dbg(f"record: detector={'webrtcvad' if wvad else 'energy'} sr={sr}")

        # The room carries over between captures — it is a property of the room,
        # not of one utterance — and it is re-measured continuously from the
        # blocks between utterances. It used to be a drop-fast/rise-slow follower
        # fed *every* block, speech included, so a talkative card walked the floor
        # upwards and took the gates with it; by the end of a session the same
        # voice that was heard at the start was being refused as whisper-quiet.
        # Only silence between answers is the room, so only that is measured.
        peak = 0.0  # loudest block of the utterance so far
        run = 0  # consecutive speech-like blocks, for the onset
        window = max(8, int(_ROOM_WINDOW / block_secs))
        if self._room_blocks.maxlen != window:
            self._room_blocks = deque(self._room_blocks, maxlen=window)
            self._room_levels = deque(self._room_levels, maxlen=window)
        since = 0  # blocks since the room level was last recomputed
        quiet = 0.0  # quietest block of this capture, as a floor of last resort

        def band_rms(block):
            b = block.astype(np.float32)
            if sos is not None:
                b, zi["z"] = sosfilt(sos, b, zi=zi["z"])
            return float(np.sqrt(np.mean(b * b)) + 1e-9)

        def room_now():
            """The room as it stands, with a fallback for the first moments.

            Calibration normally has this measured before the first card is up.
            When it has not — calibration refused, the mic taken by something
            else, the very first blocks of a session — the quietest block seen so
            far stands in. It can only ever *under*-read the room (speech never
            lowers a minimum), which fails the safe way: gates that are too low
            let a noise through to the judge, which refuses it; gates that were
            too high would drop the answer and say nothing.
            """
            return self._room if self._room is not None else quiet

        def note_room(block, r):
            """This block had nobody talking in it: it is what the room sounds like."""
            nonlocal since
            self._room_blocks.append(block)
            self._room_levels.append(r)
            since += 1
            if len(self._room_levels) >= 8 and since >= 10:  # every ~200 ms
                since = 0
                self._room = float(
                    np.percentile(
                        np.fromiter(self._room_levels, dtype=float), _ROOM_PCTL
                    )
                )

        def is_speech(block, r):
            # Trigger on a clear jump over the room OR on WebRTC VAD — the energy
            # path is the robust backup for when WebRTC misfires (e.g. it did
            # nothing at very high input gain). Deliberately trigger-happy: every
            # onset it invents is judged again, after the fact, by _judge.
            if r > max(room_now() * _ONSET_OVER_ROOM, _ONSET_FLOOR):
                return True
            if wvad is not None and len(block) == blk:
                try:
                    return bool(wvad.is_speech(block.tobytes(), sr))
                except Exception as e:
                    _dbg("webrtc vad rejected a block:", repr(e))
            return False

        if on_state:
            on_state("listening")

        # Segment the running stream block by block. Waiting for the onset has no
        # deadline: the mic stays open until the card ends, so a kid who thinks in
        # silence for twenty seconds and then answers is still heard. The timeout
        # below is only a mic health check — nothing but digital silence trips it.
        preroll: deque[Any] = deque(maxlen=max(1, pre))  # rolling window of blocks
        frames = []  # the utterance, once it has started
        onset = voiced_end = 0
        started = done = accepted = False
        waited = starved = 0
        verdict = ""  # the accepted utterance's metrics
        while not self._cancel.is_set():
            try:
                block = self._audio_q.get(timeout=0.2)
            except queue.Empty:
                starved += 1
                if starved * 0.2 > 2.0:  # stream died (device unplugged?)
                    _dbg("record: mic stream starved -> reopening next time")
                    self._close_stream()
                    self._silent = True
                    return None
                continue
            starved = 0
            r = band_rms(block)
            peak = max(peak, r)
            quiet = r if quiet == 0.0 else min(quiet, r)
            preroll.append(block)  # always: a false start keeps its lead-in
            if not started:
                note_room(block, r)
                if is_speech(block, r):
                    run += 1
                    if run >= 2:  # ~40 ms of speech -> go
                        started = True
                        frames = list(preroll)
                        onset = voiced_end = len(frames) - 1
                else:
                    run = 0
                waited += 1
                if waited * block_secs > _PRE_TIMEOUT:
                    waited = 0
                    if peak < 3.0:  # not even room tone -> no mic
                        _dbg(f"record: silence for {_PRE_TIMEOUT}s (peak={peak:.0f})")
                        self._silent = True
                        return None
                    peak = 0.0  # forget a stale bang; keep waiting for speech
                continue
            frames.append(block)
            i = len(frames) - 1
            # Still speaking? Energy has to stand above both this utterance's own
            # peak and the room — the flat 40 floor alone meant that in a quiet
            # room every block of silence counted as speech.
            if r > max(peak * 0.12, room_now() * _OFFSET_OVER_ROOM, _OFFSET_FLOOR):
                voiced_end = i
            if (i - voiced_end) * block_secs > _SILENCE_HANG:
                done = True  # speaker went quiet -> done
            elif (i - onset) * block_secs > _MAX_DURATION:
                done = True  # hard cap on one utterance
            if not done:
                continue
            ok, why, verdict = self._judge(
                frames[onset : voiced_end + 1],
                sr,
                peak,
                room_now(),
                voiced_end - onset + 1,
                min_voiced,
            )
            if ok:
                accepted = True
                break  # a real utterance -> transcribe it
            if why == "faint" and on_state:
                # Someone spoke and was not loud enough. Say so: a whisper that is
                # merely dropped looks exactly like a mic that is not working, and
                # a child who cannot tell the two apart just whispers again.
                on_state("too_quiet")
            # A cough, a chair, a door, a gurgle, a whisper: not an answer. Drop it
            # and keep listening on the same stream — bouncing back through the
            # caller to re-arm would reopen the mic and go deaf for ~200 ms each
            # time, which in a room with any ambient noise happens every few
            # hundred ms.
            _dbg(f"record: not an answer — {verdict}, still listening")
            started = done = False
            run = 0
            peak = 0.0
            frames = []

        voiced = voiced_end - onset + 1 if started else 0
        # Only audio that came out of the `break` above — i.e. that _judge passed —
        # is ever returned. Leaving the loop any other way means the card was
        # cancelled mid-utterance, and that half-captured sound was never judged
        # to be a voice at all; it must not reach the recognizer.
        if not accepted or not started or voiced < min_voiced:
            self._silent = peak < 3.0
            _dbg(
                f"record: cancelled (accepted={accepted}, started={started}, "
                f"voiced={voiced}, peak={peak:.0f})"
            )
            return None
        seg = frames[: voiced_end + post + 1]
        _dbg(
            f"record: captured {len(seg)} blocks (~{len(seg) * block_secs:.1f}s), {verdict}"
        )
        if not seg:
            return None
        raw = np.concatenate(seg).astype(np.float32)
        # The seconds just before the child spoke are the truest sample of this
        # room there will ever be — same mic, same gain, same second. Re-measure
        # it from them, then subtract that spectrum from what was actually said.
        if len(self._room_blocks) >= 8:
            self._learn_room(list(self._room_blocks), sr)
        clean = self._denoise(raw, sr)
        self._last_raw = clean  # denoised; Vosk wants it without the VAD band-pass
        self._last_dirty = raw  # ...and untouched, in case the cleaning hurt
        return self._normalize(clean)

    def _normalize(self, audio):
        """Amplify a quiet capture to a healthy, consistent level so the recognizer
        hears it clearly (a low-gain mic otherwise hands Vosk near-silence)."""
        np = self._np
        audio = np.asarray(audio, dtype=np.float32)
        peak = float(np.abs(audio).max())
        if peak < 1:
            return audio.astype(np.int16)
        gain = min((0.7 * 32767) / peak, 120.0)  # allow more gain for quiet mics
        return (audio * gain).clip(-32768, 32767).astype(np.int16)

    def _dump_capture(self, audio) -> None:
        """Write the last capture to /tmp so it can be listened back to.

        Only when ``ALMATHUS_VOICE_DEBUG`` is on: it is the one way to hear
        exactly what the recognizer did, and why it said what it said.
        """
        path = Path(tempfile.gettempdir()) / "almathus_capture.wav"
        try:
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self._rec_sr)
                w.writeframes(audio.astype(self._np.int16).tobytes())
        except Exception as e:
            _dbg("capture dump failed:", repr(e))
        else:
            _dbg(
                f"saved capture -> {path} "
                f"(sr={self._rec_sr}, {len(audio)} samples, "
                f"{len(audio) / self._rec_sr:.1f}s)"
            )

    def _vosk_model_dir(self):
        """Where this language's Vosk model lives, or None if it isn't installed.

        Three places are searched, in order: an explicit ``ALMATHUS_VOSK_MODEL``
        path, the per-user store the setup screen installs into (see
        :mod:`languages`), and finally a model dropped beside the app in
        ``./models``. The last one keeps existing fr/en installs working, and
        the registry name is tried before the old substring glob so a
        downloaded ``en-us`` is not shadowed by a hand-placed ``en``.
        """
        env = os.environ.get("ALMATHUS_VOSK_MODEL")
        if env:
            return env
        lang = languages.BY_CODE.get(self.lang)
        if lang is not None and languages.is_installed(self.lang):
            return str(languages.model_path(self.lang))
        base = Path(__file__).resolve().parent / "models"
        if base.is_dir():
            if lang is not None and (base / lang.model).is_dir():
                return str(base / lang.model)
            key = lang.code if lang is not None else self.lang
            matches = sorted(base.glob(f"vosk-model*{key}*"))
            if not matches and key.startswith("en") and key != "en":
                # an unqualified "en"/"en-us" language, a model named for any
                # English variant: try the older, broader glob too
                matches = sorted(base.glob("vosk-model*en*"))
            for d in matches:
                if d.is_dir():
                    return str(d)
        return None

    def _load_vosk(self) -> bool:
        """Load the Vosk model (fast). Returns True if usable. STT thread only."""
        if self._vosk_model is not None:
            return True
        if self._vosk_ok is False:
            return False
        try:
            from vosk import Model, SetLogLevel

            SetLogLevel(-1)
            d = self._vosk_model_dir()
            if not d:
                self._vosk_ok = False
                _dbg("vosk model not found -> disabled")
                return False
            self._vosk_model = Model(d)
        except Exception as e:
            self._vosk_ok = False
            _dbg("vosk load failed:", repr(e))
            return False
        else:
            self._vosk_ok = True
            _dbg(f"vosk model loaded from {d}")
            return True

    def _vosk_decode(self, audio, grammar=None, words=False, alts=0):
        """Decode one utterance and return its list of result dicts.

        Fed in chunks, and every *segment* is collected: Vosk closes a segment on
        its own when it hears a pause (``AcceptWaveform`` -> True), and that
        segment's text is then readable only from ``Result()``. Reading just
        ``FinalResult()`` silently threw the answer away — a capture with ~1s of
        trailing noise ends the segment, so "quinze" landed in ``Result()`` and
        the game heard nothing at all.
        """
        import json as _json

        from vosk import KaldiRecognizer

        rec = (
            KaldiRecognizer(self._vosk_model, float(self._rec_sr), grammar)
            if grammar
            else KaldiRecognizer(self._vosk_model, float(self._rec_sr))
        )
        if alts:
            rec.SetMaxAlternatives(alts)
        if words:
            rec.SetWords(True)
        data = self._normalize(audio).tobytes()  # gain-only (raw audio)
        step = max(2, int(self._rec_sr * 0.25) * 2)  # 0.25s chunks, int16-aligned
        out = []
        for i in range(0, len(data), step):
            if rec.AcceptWaveform(data[i : i + step]):
                out.append(_json.loads(rec.Result()))
        out.append(_json.loads(rec.FinalResult()))
        return out

    def _vosk_numbers(self, audio):
        """The numbers in an utterance, if it was numbers and nothing else.

        Two decodes of the same audio have to agree that this was a number:

        * the number-only grammar, read strictly — the winning hypothesis must be
          made of number words alone and must score within ``_VOSK_ALT_GAP`` of
          the best hypothesis overall;
        * the full model with no grammar, used only to veto — if it confidently
          hears a run of words that are not numbers, the utterance was speech but
          not an answer, whatever the grammar decode made of it.

        Runs on the STT thread. Returns a list of ints, in order; empty means
        "not an answer, keep listening".
        """
        if not self._load_vosk():
            return []
        grammar = _grammar_for(self.lang)
        if grammar is None:
            return []
        try:
            nums = []
            for res in self._vosk_decode(audio, grammar=grammar, alts=_VOSK_ALTS):
                nums += self._vosk_best(res)
            if not nums:
                return []
            if self._vosk_vetoes(audio):
                return []
        except Exception as e:
            _dbg("vosk error:", repr(e))
            return []
        else:
            return nums

    def _vosk_best(self, res: dict) -> list[int]:
        """The numbers of the best all-number hypothesis in one Vosk result.

        Nothing is returned unless some hypothesis is numbers *only* and scores
        close enough to the top one — see ``_VOSK_ALT_GAP``.
        """
        alts = res.get("alternatives")
        if not alts:  # no N-best: the plain text
            return strict_numbers(res.get("text", ""), self.lang) or []
        top = alts[0].get("confidence", 0.0)
        if any(a.get("text", "").strip() for a in alts):
            _dbg(
                "vosk alts="
                + str(
                    [
                        (round(a.get("confidence", 0.0), 2), a.get("text", ""))
                        for a in alts[:4]
                    ]
                )
            )
        for a in alts:
            text = a.get("text", "").strip()
            if not text:
                continue
            nums = strict_numbers(text, self.lang)
            if not nums:  # None (not a number) or []
                continue
            gap = top - a.get("confidence", 0.0)
            if gap > _VOSK_ALT_GAP:
                _dbg(f"vosk: refused {text!r} — {gap:.2f} behind the top hypothesis")
                return []  # numbers, but not convincingly
            return nums
        return []

    def _vosk_vetoes(self, audio) -> bool:
        """Did the un-constrained model clearly hear words that are not numbers?

        Only a confident run of at least ``_VETO_WORDS`` words counts: a single
        low-confidence word out of the general model is a coin toss, and vetoing
        on it would throw away real answers.
        """
        try:
            heard = [
                w
                for res in self._vosk_decode(audio, words=True)
                for w in (res.get("result") or [])
            ]
        except Exception as e:
            _dbg("vosk veto decode failed:", repr(e))
            return False
        if len(heard) < _VETO_WORDS:
            return False
        if any(w.get("conf", 0.0) < _VETO_CONF for w in heard):
            return False
        text = " ".join(w.get("word", "") for w in heard)
        if strict_numbers(text, self.lang):
            return False  # it heard numbers too — agree
        _dbg(f"vosk: vetoed — the full model confidently heard {text!r}")
        return True
