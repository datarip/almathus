"""Voice I/O for Almathus — text-to-speech + speech-to-text.

Two independent, gracefully-degrading pieces:

* ``TTS``          — speaks encouragement in a warm feminine voice.
                     Uses macOS ``say`` (offline, built-in); falls back to
                     ``pyttsx3`` if present; otherwise a silent no-op.

* ``SpeechInput``  — records from the mic with simple voice-activity detection
                     (one open stream per card, auto-stops when the speaker goes
                     quiet, so nothing is lost between two answers), transcribes with
                     **parakeet**, and parses the spoken number (French + English).
                     Needs ``sounddevice`` + ``numpy`` and a parakeet backend;
                     if any is missing it reports itself unavailable and the game
                     simply stays keypad-only.

Nothing here ever raises into the UI: probe with ``.available()`` first, and all
callbacks are plain values so the caller can marshal them onto the Tk main loop.

Environment overrides:
    ALMATHUS_TTS_VOICE     macOS voice name (default: Amélie for fr, Samantha for en)
    ALMATHUS_PARAKEET_MODEL  HF id for parakeet-mlx (default parakeet-tdt-0.6b-v2)
    ALMATHUS_PARAKEET_CMD  shell template with {wav}; stdout is used as transcript
                           (use this to plug in any parakeet CLI you already run)
"""

from __future__ import annotations

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
from typing import Callable, Optional

# Set ALMATHUS_VOICE_DEBUG=1 to trace the whole voice path on stderr.
_DEBUG = os.environ.get("ALMATHUS_VOICE_DEBUG") not in (None, "", "0")


def _dbg(*a):
    if _DEBUG:
        print("[voice]", *a, file=sys.stderr, flush=True)

# --------------------------------------------------------------------------- #
# Text-to-speech
# --------------------------------------------------------------------------- #

# Feminine voice name hints per language, used to pick a nice macOS `say` voice.
_FEMALE_HINTS = {
    "fr": ["amelie", "audrey", "aurelie", "marie", "chantal", "virginie", "julie"],
    "en": ["samantha", "karen", "moira", "tessa", "fiona", "allison", "ava",
           "susan", "zoe", "serena", "kate", "victoria"],
}


def _list_say_voices() -> list[tuple[str, str]]:
    """Return [(name, locale)] from `say -v '?'`, robust to spaces in names.

    Lines look like:  "Amélie              fr_CA    # Bonjour!"
                or:   "Amélie (French (Canada)) fr_CA    # Bonjour!"
    We anchor on the locale token (xx_XX / xx-XX) and take everything before it.
    """
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    voices = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([A-Za-z]{2}[-_][A-Za-z]{2})\b", line)
        if m:
            voices.append((m.group(1).strip(), m.group(2)))
    return voices


class TTS:
    def __init__(self, lang: str = "fr", rate: int = 180):
        self.lang = lang
        self.rate = rate
        self._proc: Optional[subprocess.Popen] = None
        self._say = shutil.which("say")
        self._voice = self._pick_voice(lang)
        self._pyttsx = None
        if not self._say:
            try:
                import pyttsx3  # noqa: F401
                self._pyttsx = True
            except Exception:
                self._pyttsx = False

    def _pick_voice(self, lang: str) -> Optional[str]:
        if not shutil.which("say"):
            return None
        env = os.environ.get("ALMATHUS_TTS_VOICE")
        if env:
            return env
        try:
            voices = _list_say_voices()
        except Exception:
            return None
        pref = "fr" if lang == "fr" else "en"
        cands = [(n, loc) for n, loc in voices if loc.lower().startswith(pref)]
        if not cands:
            return None
        hints = _FEMALE_HINTS.get(lang, [])

        def rank(nl):
            name, loc = nl
            nn = _strip_accents(name).lower()
            female = any(h in nn for h in hints)
            local_match = loc.lower().replace("-", "_") == (pref + "_" + pref)
            return (female, local_match)

        cands.sort(key=rank, reverse=True)
        # Use the short base name (e.g. "Amélie"), which `say -v` accepts even when
        # the listing shows an enhanced variant like "Amélie (French (Canada))".
        return cands[0][0].split(" (")[0].strip()

    def available(self) -> bool:
        return bool(self._say or self._pyttsx)

    def speak(self, text: str, on_done: Optional[Callable[[], None]] = None):
        """Speak asynchronously; interrupts any speech already in progress."""
        self.stop()
        if self._say:
            cmd = [self._say, "-r", str(self.rate)]
            if self._voice:
                cmd += ["-v", self._voice]
            cmd.append(text)
            try:
                self._proc = subprocess.Popen(cmd)
            except Exception:
                return
            if on_done:
                threading.Thread(target=self._wait_then, args=(self._proc, on_done),
                                 daemon=True).start()
        elif self._pyttsx:
            threading.Thread(target=self._pyttsx_speak, args=(text, on_done),
                             daemon=True).start()
        elif on_done:
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
        except Exception:
            pass
        finally:
            if on_done:
                on_done()

    def speaking(self) -> bool:
        """Is the app talking right now? The mic must not be armed while it is,
        or the question read aloud gets captured as the answer to itself."""
        return bool(self._proc and self._proc.poll() is None)

    def stop(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None


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
    "fr": {"zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
           "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
           "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
           "seize": 16},
    "en": {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
           "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
           "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
           "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19},
}
# 20..90 — a tens word can carry a ones word after it ("vingt et un", "sixty two").
_TEN_WORDS = {
    "fr": {"vingt": 20, "vingts": 20, "trente": 30, "quarante": 40,
           "cinquante": 50, "soixante": 60,
           # Belgian / Swiss forms, said by plenty of French-speaking children
           "septante": 70, "octante": 80, "huitante": 80, "nonante": 90},
    "en": {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
           "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90},
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
    "fr": ((r"\bquatre\s+vingts?\s+dix\b", " 90 "),
           (r"\bquatre\s+vingts?\b", " 80 ")),
    "en": (),
}

# What a recognizer emits for "something I have no word for". Never an answer.
_UNKNOWN_TOKENS = {"unk", "[unk]", "<unk>", "unknown"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _lang_key(lang: str) -> str:
    return "fr" if lang == "fr" else "en"


def _tokenize(text: str, lang: str) -> list[str]:
    """A transcript, lowercased and accent-free, as a list of word/digit tokens.

    Hyphens and apostrophes split ("dix-sept" -> dix, sept); sentence punctuation
    the recognizer adds ("huit." / "Eight?") is dropped; anything else is kept as
    a token so the caller can refuse it.
    """
    low = _strip_accents(text.lower())
    low = re.sub(r"[.,;:!?…\"'`’\-–—/]+", " ", low)
    # ...only then the rewrites, so they see "quatre vingt dix" whether it was
    # written hyphenated or spaced.
    for pattern, repl in _PRESUB[_lang_key(lang)]:
        low = re.sub(pattern, repl, low)
    return low.split()


def strict_numbers(text: str, lang: str = "fr") -> Optional[list[int]]:
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
    key = _lang_key(lang)
    units, tens = _UNIT_WORDS[key], _TEN_WORDS[key]
    scales, glue = _SCALE_WORDS[key], _GLUE_WORDS[key]

    tokens = _tokenize(text, lang)
    if not tokens:
        return []

    out: list[int] = []
    cur: Optional[int] = None
    last = ""          # "unit" | "ten" | "scale" — what the previous token was
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
            continue                    # "vingt ET un", "one hundred AND five"
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
            return None                 # a word that is not a number: refuse it all
        # Where does this token go — on the number being built, or a new one?
        if kind == "ten":
            if last in ("unit", "ten"):
                flush()                 # "vingt trente" is two numbers, not one
            cur = (cur or 0) + val
        else:                           # a ones word
            # Two ones words in a row start a new number ("trois quatre"), except
            # the French teens, which are literally "ten seven" / "ten eight".
            if last == "unit" and not (last_val == 10 and val in (7, 8, 9)):
                flush()
            cur = (cur or 0) + val
        last, last_val = kind, val
    flush()
    return out


def numbers_in(text: str, lang: str = "fr") -> list[int]:
    """Every number in a strictly-numeric transcript, in the order it was said.

    An utterance that says anything besides numbers yields ``[]``: it is not an
    answer, and the caller must keep listening rather than score the card.
    """
    nums = strict_numbers(text, lang)
    return nums or []


def parse_number(text: str, lang: str = "fr") -> Optional[int]:
    """The single number a strictly-numeric transcript states, or None.

    The LAST one wins, which handles self-correction ("trois, quatre").
    """
    nums = numbers_in(text, lang)
    return nums[-1] if nums else None


# --------------------------------------------------------------------------- #
# Speech-to-text (mic capture + VAD + parakeet)
# --------------------------------------------------------------------------- #

_SAMPLE_RATE = 16000
_BLOCK = 320               # 20 ms blocks
_PRE_TIMEOUT = 5.0         # mic health check: this much silence, and *nothing* heard
_SILENCE_HANG = 0.35       # stop this long after speech ends (snappy response)
_MAX_DURATION = 6.0        # hard cap on a single utterance, from its onset
_VOICE_BAND = (300.0, 3400.0)   # Hz — speech band; cuts low rumble & high hiss


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


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
                      # peak must clear the room's own noise floor by this factor
_MIN_LEVEL = _envf("ALMATHUS_VOICE_MIN_LEVEL", 120.0)
                      # ...and this absolute band RMS. Room tone measured 41-100;
                      # the quietest real answer of that same session, 295.
_MIN_VOICED = _envf("ALMATHUS_VOICE_MIN_VOICED", 0.25)
                      # fraction of energetic frames that must be *voiced* — i.e.
                      # show vocal-fold pitch. This is the one that matters: over
                      # TTS speech (adult and child pitch, clean and at 24 dB SNR)
                      # it measured 0.45-1.00, over noise at every level 0.00.
                      # A whisper has no vocal-fold vibration, so it lands with
                      # the noise; that is exactly why whispers must not count.
_NACF_MIN = _envf("ALMATHUS_VOICE_NACF", 0.40)
                      # per-frame normalised autocorrelation peak that is "voiced"
_F0_BAND = (100.0, 600.0)   # Hz — pitch search range, low man to shouting child

# v3 is multilingual (incl. French); v2 is English-only.
_DEFAULT_PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"

_WARMUP = object()   # sentinel job: preload the model on the STT thread

# Vosk number-only grammar. Constraining the vocabulary to number words rescues
# cases the general model can't nail — notably the French "un" (heard as "Mm"/"On"
# by parakeet). "[unk]" is what lets Vosk say "that was speech, but not a number"
# instead of being forced to pick a number word for every sound it hears.
_VOSK_GRAMMAR = {
    "fr": ('["zéro un une deux trois quatre cinq six sept huit neuf dix onze douze '
           'treize quatorze quinze seize dix-sept dix-huit dix-neuf vingt trente '
           'quarante cinquante soixante quatre-vingt quatre-vingts septante nonante '
           'cent cents et", "[unk]"]'),
    "en": ('["zero one two three four five six seven eight nine ten eleven twelve '
           'thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty '
           'thirty forty fifty sixty seventy eighty ninety hundred and", "[unk]"]'),
}

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
_VOSK_ALTS = 6             # how many hypotheses to ask Vosk for

# Second opinion: the same audio decoded by the FULL model, with no grammar to
# push it towards numbers. If that decode is a confident run of words and those
# words are not numbers, the utterance was speech — just not an answer — and the
# grammar decode is overruled. Two independent decoders must agree that this was
# a number before a card is ever scored from it. Worth 1 fewer false accept and
# 'tu peux répéter' at the default gap.
_VETO_CONF = _envf("ALMATHUS_VOICE_VETO_CONF", 0.9)
_VETO_WORDS = 2            # ...over at least this many words


def _quiet_hf_hub(model_id: str) -> None:
    """Quiet the Hugging Face Hub 'unauthenticated request' warning that parakeet's
    model download prints. When the model is already cached, go fully offline so the
    Hub is never contacted — no warning and a faster start. Set before importing
    parakeet (which imports huggingface_hub) so HF_HUB_OFFLINE takes effect.
    """
    import logging
    from pathlib import Path

    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    if (hub / ("models--" + model_id.replace("/", "--"))).is_dir():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


class SpeechInput:
    def __init__(self, lang: str = "fr"):
        self.lang = lang
        self._cancel = threading.Event()
        self._model = None
        self._reason = ""
        self._sd = None
        self._np = None
        self._silent = False   # set True when the last capture was pure silence (no mic access)
        self._silent_streak = 0  # consecutive silent captures (2 in a row = really no mic)
        self._busy = False     # guard against overlapping listen() calls
        self._rec_sr = _SAMPLE_RATE
        self._jobs: queue.Queue = queue.Queue()   # audio -> the one STT thread
        self._stt_thread = None                   # owns & runs the model (MLX is thread-affine)
        self._vosk_model = None
        self._vosk_ok = None                      # None=unknown, False=unavailable
        self._noise_floor = None                  # persists across listens (no re-calibration)
        self._audio_q: queue.Queue = queue.Queue()   # mic blocks, filled while the stream is open
        self._stream_params = None                # cached (samplerate, blocksize)
        self._blk = int(_SAMPLE_RATE * 0.02)
        self._boosted = False                     # did we raise the OS mic input gain?
        self._last_raw = None                     # last capture, un-band-passed (for Vosk)
        self._active_stream = None                # the open mic stream, so we can close it on quit
        self._stopped = False                     # latched on quit: no more captures
        self._probe()

    # -- capability probing ------------------------------------------------ #
    def _probe(self):
        try:
            import sounddevice as sd
            import numpy as np
            self._sd, self._np = sd, np
        except Exception:
            self._reason = "audio libs manquantes (pip: sounddevice numpy)"
            return
        if os.environ.get("ALMATHUS_PARAKEET_CMD"):
            self._backend = "cli"
            return
        try:
            import parakeet_mlx  # noqa: F401
            self._backend = "mlx"
        except Exception:
            self._backend = None
            self._reason = "parakeet introuvable (pip: parakeet-mlx, ou ALMATHUS_PARAKEET_CMD)"

    def available(self) -> bool:
        if not (self._sd and self._np):
            return False
        # usable with either a parakeet backend OR a Vosk model (Vosk is primary now)
        return bool(getattr(self, "_backend", None)) or self._vosk_model_dir() is not None

    def reason(self) -> str:
        return self._reason

    # -- public API -------------------------------------------------------- #
    def listen(self, on_result: Callable[[Optional[int]], None],
               on_state: Optional[Callable[[str], None]] = None,
               pick: Optional[Callable[[list], Optional[int]]] = None):
        """Record one utterance in a background thread, then call on_result(int|None).

        ``pick`` turns the numbers heard into the one answer to report (None =
        heard nothing usable). It is how the caller rejects an utterance that
        isn't an answer at all; the default keeps the last number said.

        on_state("listening"|"thinking") lets the UI show progress. Both callbacks
        fire from a worker thread — marshal to Tk with widget.after(0, ...).
        """
        if self._stopped:
            return                       # app is quitting — never reopen the mic
        # Before the busy check, not after: a recognizer thread that died left
        # `_busy` stuck True, and checking first meant every later listen() was
        # refused with nothing left alive to un-stick it.
        self._ensure_stt_thread()
        if self._busy:
            _dbg("listen() ignored — already busy")
            return                       # one capture at a time
        _dbg("listen() -> starting capture")
        self._busy = True
        self._cancel.clear()
        # Record on a throwaway thread (the mic is fine cross-thread); transcription
        # is handed to the single STT thread that owns the model.
        threading.Thread(target=self._capture, args=(on_result, on_state, pick),
                         daemon=True).start()

    def listening(self) -> bool:
        """Is a capture actually running right now?

        The caller re-arms from the result callback, so a dropped or refused
        callback would end the chain and leave the card deaf with no sign of it.
        Its watchdog polls this instead of trusting the chain.
        """
        return self._busy and not self._cancel.is_set() and not self._stopped

    def cancel(self):
        """End the current listening session (the card is over): the mic closes,
        so the next card starts on a clean stream and the mic light goes out."""
        self._cancel.set()
        self._close_stream()

    def stop(self):
        """Stop any in-progress capture and release the microphone right away
        (so the OS mic-in-use indicator turns off when the app quits)."""
        self._stopped = True             # prevent the continuous loop from reopening it
        self._cancel.set()
        self._close_stream()

    def _close_stream(self):
        s = self._active_stream
        self._active_stream = None
        if s is not None:
            _dbg("closing mic stream")
            for fn in ("abort", "close"):
                try:
                    getattr(s, fn)()
                except Exception:
                    pass

    def warmup(self):
        """Preload the recognizer on the STT thread so the first answer is instant."""
        self._ensure_stt_thread()
        self._jobs.put((_WARMUP, None, None, None, None))

    def boost_input_volume(self, level: int = 80):
        """Raise the macOS mic input gain (moderate — max gain over-amplifies noise
        and breaks speech detection). Registers an atexit restore for any exit."""
        if sys.platform != "darwin" or self._boosted:
            return
        try:
            subprocess.run(["osascript", "-e", f"set volume input volume {int(level)}"],
                           timeout=3, capture_output=True)
            self._boosted = True
            import atexit
            atexit.register(self.restore_input_volume)   # covers X, Quit, Ctrl-C, crash-exit
            _dbg(f"boosted macOS input volume to {level}")
        except Exception:
            pass

    def restore_input_volume(self, level: int = 50):
        """Put the mic input gain back to a neutral 50% on exit — only if we raised
        it. Idempotent; safe to call from atexit and from main()'s finally."""
        if not self._boosted or sys.platform != "darwin":
            return
        self._boosted = False
        try:
            subprocess.run(["osascript", "-e", f"set volume input volume {int(level)}"],
                           timeout=3, capture_output=True)
            _dbg(f"restored macOS input volume to {level}")
        except Exception:
            pass

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
            self._last_raw = None
            audio = self._record(on_state)
            # Two silent captures in a row before crying "no mic": the caller stops
            # re-arming on that signal, so one unlucky window (a stream hiccup, a
            # capture cut short) used to leave the kid talking to a deaf card.
            self._silent_streak = self._silent_streak + 1 if self._silent else 0
            if self._silent_streak >= 2 and on_state:
                _dbg(f"capture SILENT x{self._silent_streak} -> no_mic")
                on_state("no_mic")     # only silence -> no mic access / no usable device
            elif self._silent:
                _dbg("capture SILENT (1st) -> keep listening")
            # The un-filtered audio travels *with* the job. Reading it off the
            # instance at transcribe time meant a capture that returned nothing
            # could leave the previous utterance's audio in place, and the same
            # answer could be recognized twice.
            self._jobs.put((audio, self._last_raw, on_result, on_state, pick))
        except Exception as e:
            _dbg("capture EXCEPTION:", repr(e))
            self._jobs.put((None, None, on_result, on_state, pick))

    def _stt_loop(self):
        """The ONE thread that creates and runs the parakeet model — MLX streams
        are thread-local, so the model must never cross threads.

        Nothing raised in here may end this loop. It used to be possible: a job
        tuple of the wrong shape killed the thread on the very first unpack, and
        from then on every answer was recognized by nobody at all — silently,
        because the exception went to a thread no one was watching.
        """
        while True:
            try:
                job = self._jobs.get()
            except Exception as e:                    # queue itself is broken
                _dbg("STT queue EXCEPTION, thread ending:", repr(e))
                return
            try:
                self._run_job(job)
            except Exception as e:
                _dbg("STT job EXCEPTION (thread survives):", repr(e))
                self._busy = False

    def _run_job(self, job):
        audio, raw, on_result, on_state, pick = job
        if audio is _WARMUP:
            try:
                self._load_vosk()                 # primary recognizer, fast (~1s)
            except Exception as e:
                _dbg("vosk warmup failed:", repr(e))
            # Parakeet is the recognizer of last resort: slow to load (~60s),
            # so it is only warmed when asked for, or when there is no Vosk
            # model for this language and it is the only one left. Without
            # that second case English had no recognizer at all — Vosk found
            # no model and parakeet was never loaded, so every spoken answer
            # in English was silently dropped.
            want_parakeet = (os.environ.get("ALMATHUS_PARAKEET")
                             or self._vosk_model is None)
            if want_parakeet and self._backend == "mlx":
                try:
                    self._ensure_model()
                except Exception as e:
                    _dbg("parakeet warmup failed:", repr(e))
            return
        try:
            if audio is None or self._cancel.is_set():
                _dbg("no audio (timeout/cancel) -> None")
                self._busy = False       # before the callback: it re-arms from here
                on_result(None)
                return
            if on_state:
                on_state("thinking")
            # Vosk FIRST: fast (~100ms), number-grammar, and the accurate one for
            # isolated digits. Runs on the RAW audio (band-pass breaks Vosk).
            _t0 = time.monotonic()
            nums = self._vosk_numbers(raw if raw is not None else audio)
            _dbg(f"vosk -> {nums} ({(time.monotonic() - _t0) * 1000:.0f}ms)")
            # parakeet only as a backup, and only if it's already loaded (never
            # block on its ~60s load — Vosk handles the common case).
            if not nums and self._model is not None:
                text = self._transcribe(audio)
                # Same strict rule as everywhere else: a transcript that says
                # anything other than numbers is not an answer.
                nums = strict_numbers(text, self.lang) or []
                _dbg(f"parakeet fallback transcript={text!r} -> {nums}")
            parsed = pick(nums) if pick else (nums[-1] if nums else None)
            _dbg(f"picked -> {parsed}")
            self._busy = False           # a callback that re-arms must not be refused
            on_result(parsed)
        except Exception as e:
            _dbg("transcribe EXCEPTION:", repr(e))
            try:
                on_result(None)
            except Exception:
                pass
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
            dev_sr = int(sd.query_devices(kind="input").get("default_samplerate") or 16000)
        except Exception:
            dev_sr = 16000
        # Prefer rates WebRTC VAD supports (8/16/32/48 kHz) so the VAD can run.
        for sr in (16000, 48000, 32000, 8000, dev_sr, 44100, 22050):
            try:
                sd.check_input_settings(samplerate=sr, channels=1, dtype="int16")
                # exactly 20 ms frames (WebRTC VAD needs 10/20/30)
                self._stream_params = (sr, int(sr * 0.02))
                return self._stream_params
            except Exception:
                continue
        return None, 0

    def _judge(self, span, sr, peak, floor, voiced_blocks, min_voiced):
        """Was that a frank, spoken answer? -> (ok, a line of metrics for the log).

        Three questions, cheapest first: was it long enough to be a word, was it
        loud enough to be someone talking rather than the room, and was it voiced
        — an actual voice rather than noise or a whisper.
        """
        if voiced_blocks < min_voiced:
            return False, f"too short ({voiced_blocks} blocks)"
        floor = floor or 0.0
        snr = peak / max(floor, 1.0)
        if peak < _MIN_LEVEL or snr < _MIN_SNR:
            return False, f"too faint (peak={peak:.0f} floor={floor:.0f} snr={snr:.1f})"
        vf = self._voiced_fraction(self._np.concatenate(span), sr)
        stats = f"peak={peak:.0f} snr={snr:.1f} voiced={vf:.2f}"
        if vf < _MIN_VOICED:
            return False, f"not a voice ({stats})"
        return True, stats

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
        frames = np.stack([x[i:i + win] for i in starts])
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
        ac = np.fft.irfft(spec * np.conj(spec), n=n, axis=1)[:, :hi + 1]
        zero = ac[:, 0]
        nacf = np.where(zero > 0, ac[:, lo:hi + 1].max(axis=1) / np.where(zero > 0, zero, 1), 0.0)
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
            return x - np.convolve(pad, np.ones(k) / k, mode="valid")[:len(x)]

    def _make_bandpass(self, sr):
        """Butterworth band-pass over the speech band. Returns (sos, sosfilt,
        sosfilt_zi) or (None, ...) if scipy is unavailable (then VAD uses raw RMS)."""
        try:
            from scipy.signal import butter, sosfilt, sosfilt_zi
            hi = min(_VOICE_BAND[1], sr / 2 - 100)   # stay under Nyquist
            sos = butter(4, [_VOICE_BAND[0], hi], btype="band", fs=sr, output="sos")
            return sos, sosfilt, sosfilt_zi
        except Exception:
            return None, None, None

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
            stream = self._sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                                          blocksize=blk, callback=cb)
            stream.start()
        except Exception as e:
            _dbg("stream open failed:", repr(e))
            self._stream_params = None    # re-probe next time (device may have changed)
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
            self._silent = True          # no usable input device
            return None
        if self._cancel.is_set():        # cancel() raced with the open
            self._close_stream()
            return None
        block_secs = blk / sr
        pre = int(0.25 / block_secs)      # keep a little before the onset
        post = int(0.25 / block_secs)     # ...and a little after the tail
        min_voiced = max(4, int(0.16 / block_secs))   # need ≥0.16s of real speech —
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
                wvad = webrtcvad.Vad(0)     # 0 = most sensitive
            except Exception:
                wvad = None
        _dbg(f"record: detector={'webrtcvad' if wvad else 'energy'} sr={sr}")

        # The noise floor carries over between captures: it is a property of the
        # room, not of one utterance, and re-learning it from scratch each time
        # left the very first blocks of a capture with no reference at all.
        st = {"peak": 0.0, "run": 0, "efloor": self._noise_floor}

        def band_rms(block):
            b = block.astype(np.float32)
            if sos is not None:
                b, zi["z"] = sosfilt(sos, b, zi=zi["z"])
            return float(np.sqrt(np.mean(b * b)) + 1e-9)

        def is_speech(block, r):
            # Track the noise floor (drop fast, rise slow). Trigger on a clear energy
            # jump OR on WebRTC VAD — the energy path is the robust backup for when
            # WebRTC misfires (e.g. it did nothing at very high input gain).
            f = st["efloor"]
            st["efloor"] = r if f is None else (r if r < f else f * 0.98 + r * 0.02)
            self._noise_floor = st["efloor"]
            if r > max(st["efloor"] * 4.0, 60.0):
                return True
            if wvad is not None and len(block) == blk:
                try:
                    return bool(wvad.is_speech(block.tobytes(), sr))
                except Exception:
                    pass
            return False

        if on_state:
            on_state("listening")

        # Segment the running stream block by block. Waiting for the onset has no
        # deadline: the mic stays open until the card ends, so a kid who thinks in
        # silence for twenty seconds and then answers is still heard. The timeout
        # below is only a mic health check — nothing but digital silence trips it.
        from collections import deque
        preroll = deque(maxlen=max(1, pre))   # rolling window of the last blocks
        frames = []                           # the utterance, once it has started
        onset = voiced_end = 0
        started = done = accepted = False
        waited = starved = 0
        verdict = ""                          # the accepted utterance's metrics
        while not self._cancel.is_set():
            try:
                block = self._audio_q.get(timeout=0.2)
            except queue.Empty:
                starved += 1
                if starved * 0.2 > 2.0:       # stream died (device unplugged?)
                    _dbg("record: mic stream starved -> reopening next time")
                    self._close_stream()
                    self._silent = True
                    return None
                continue
            starved = 0
            r = band_rms(block)
            if r > st["peak"]:
                st["peak"] = r
            preroll.append(block)             # always: a false start keeps its lead-in
            if not started:
                if is_speech(block, r):
                    st["run"] += 1
                    if st["run"] >= 2:                  # ~40 ms of speech -> go
                        started = True
                        frames = list(preroll)
                        onset = voiced_end = len(frames) - 1
                else:
                    st["run"] = 0
                waited += 1
                if waited * block_secs > _PRE_TIMEOUT:
                    waited = 0
                    if st["peak"] < 3.0:                # not even room tone -> no mic
                        _dbg(f"record: silence for {_PRE_TIMEOUT}s (peak={st['peak']:.0f})")
                        self._silent = True
                        return None
                    st["peak"] = 0.0        # forget a stale bang; keep waiting for speech
                continue
            frames.append(block)
            i = len(frames) - 1
            # Still speaking? Energy has to stand above both this utterance's own
            # peak and the room — the flat 40 floor alone meant that in a quiet
            # room every block of silence counted as speech.
            if r > max(st["peak"] * 0.12, (st["efloor"] or 0.0) * 1.5, 40.0):
                voiced_end = i
            if (i - voiced_end) * block_secs > _SILENCE_HANG:
                done = True                             # speaker went quiet -> done
            elif (i - onset) * block_secs > _MAX_DURATION:
                done = True                             # hard cap on one utterance
            if not done:
                continue
            ok, verdict = self._judge(frames[onset:voiced_end + 1], sr, st["peak"],
                                      st["efloor"], voiced_end - onset + 1, min_voiced)
            if ok:
                accepted = True
                break                                   # a real utterance -> transcribe it
            # A cough, a chair, a door, a gurgle, a whisper: not an answer. Drop it
            # and keep listening on the same stream — bouncing back through the
            # caller to re-arm would reopen the mic and go deaf for ~200 ms each
            # time, which in a room with any ambient noise happens every few
            # hundred ms.
            _dbg(f"record: not an answer — {verdict}, still listening")
            started = done = False
            st["run"] = 0
            st["peak"] = 0.0
            frames = []

        peak = st["peak"]
        voiced = voiced_end - onset + 1 if started else 0
        # Only audio that came out of the `break` above — i.e. that _judge passed —
        # is ever returned. Leaving the loop any other way means the card was
        # cancelled mid-utterance, and that half-captured sound was never judged
        # to be a voice at all; it must not reach the recognizer.
        if not accepted or not started or voiced < min_voiced:
            self._silent = peak < 3.0
            _dbg(f"record: cancelled (accepted={accepted}, started={started}, "
                 f"voiced={voiced}, peak={peak:.0f})")
            return None
        seg = frames[:voiced_end + post + 1]
        _dbg(f"record: captured {len(seg)} blocks (~{len(seg)*block_secs:.1f}s), {verdict}")
        if not seg:
            return None
        raw = np.concatenate(seg).astype(np.float32)
        self._last_raw = raw              # keep un-filtered audio for the Vosk fallback
        audio = sosfilt(sos, raw) if sos is not None else raw   # band-pass for parakeet
        return self._normalize(audio)

    def _normalize(self, audio):
        """Amplify a quiet capture to a healthy, consistent level so the recognizer
        hears it clearly (a low-gain mic otherwise hands parakeet near-silence)."""
        np = self._np
        audio = np.asarray(audio, dtype=np.float32)
        peak = float(np.abs(audio).max())
        if peak < 1:
            return audio.astype(np.int16)
        gain = min((0.7 * 32767) / peak, 120.0)   # allow more gain for quiet mics
        return (audio * gain).clip(-32768, 32767).astype(np.int16)

    def _transcribe(self, audio) -> str:
        np = self._np
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            path = tf.name
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self._rec_sr)   # actual capture rate; parakeet resamples
                w.writeframes(audio.astype(np.int16).tobytes())
            if _DEBUG:
                shutil.copy(path, "/tmp/almathus_capture.wav")
                _dbg(f"saved capture -> /tmp/almathus_capture.wav "
                     f"(sr={self._rec_sr}, {len(audio)} samples, "
                     f"{len(audio)/self._rec_sr:.1f}s)")
            if self._backend == "cli":
                tmpl = os.environ["ALMATHUS_PARAKEET_CMD"]
                cmd = tmpl.format(wav=path)
                out = subprocess.run(cmd, shell=True, capture_output=True,
                                     text=True, timeout=30)
                return out.stdout.strip()
            return self._transcribe_mlx(path)
        except Exception:
            return ""
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _vosk_model_dir(self):
        env = os.environ.get("ALMATHUS_VOSK_MODEL")
        if env:
            return env
        from pathlib import Path
        base = Path(__file__).resolve().parent / "models"
        key = "fr" if self.lang == "fr" else "en"
        if base.is_dir():
            for d in sorted(base.glob(f"vosk-model*{key}*")):
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
            self._vosk_ok = True
            _dbg(f"vosk model loaded from {d}")
            return True
        except Exception as e:
            self._vosk_ok = False
            _dbg("vosk load failed:", repr(e))
            return False

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
        rec = (KaldiRecognizer(self._vosk_model, float(self._rec_sr), grammar)
               if grammar else KaldiRecognizer(self._vosk_model, float(self._rec_sr)))
        if alts:
            rec.SetMaxAlternatives(alts)
        if words:
            rec.SetWords(True)
        data = self._normalize(audio).tobytes()        # gain-only (raw audio)
        step = max(2, int(self._rec_sr * 0.25) * 2)    # 0.25s chunks, int16-aligned
        out = []
        for i in range(0, len(data), step):
            if rec.AcceptWaveform(data[i:i + step]):
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
        try:
            grammar = _VOSK_GRAMMAR.get(self.lang, _VOSK_GRAMMAR["fr"])
            nums = []
            for res in self._vosk_decode(audio, grammar=grammar, alts=_VOSK_ALTS):
                nums += self._vosk_best(res)
            if not nums:
                return []
            if self._vosk_vetoes(audio):
                return []
            return nums
        except Exception as e:
            _dbg("vosk error:", repr(e))
            return []

    def _vosk_best(self, res: dict) -> list[int]:
        """The numbers of the best all-number hypothesis in one Vosk result.

        Nothing is returned unless some hypothesis is numbers *only* and scores
        close enough to the top one — see ``_VOSK_ALT_GAP``.
        """
        alts = res.get("alternatives")
        if not alts:                                   # no N-best: the plain text
            return strict_numbers(res.get("text", ""), self.lang) or []
        top = alts[0].get("confidence", 0.0)
        if any(a.get("text", "").strip() for a in alts):
            _dbg("vosk alts=" + str([(round(a.get("confidence", 0.0), 2), a.get("text", ""))
                                     for a in alts[:4]]))
        for a in alts:
            text = a.get("text", "").strip()
            if not text:
                continue
            nums = strict_numbers(text, self.lang)
            if not nums:                               # None (not a number) or []
                continue
            gap = top - a.get("confidence", 0.0)
            if gap > _VOSK_ALT_GAP:
                _dbg(f"vosk: refused {text!r} — {gap:.2f} behind the top hypothesis")
                return []                              # numbers, but not convincingly
            return nums
        return []

    def _vosk_vetoes(self, audio) -> bool:
        """Did the un-constrained model clearly hear words that are not numbers?

        Only a confident run of at least ``_VETO_WORDS`` words counts: a single
        low-confidence word out of the general model is a coin toss, and vetoing
        on it would throw away real answers.
        """
        try:
            heard = [w for res in self._vosk_decode(audio, words=True)
                     for w in (res.get("result") or [])]
        except Exception as e:
            _dbg("vosk veto decode failed:", repr(e))
            return False
        if len(heard) < _VETO_WORDS:
            return False
        if any(w.get("conf", 0.0) < _VETO_CONF for w in heard):
            return False
        text = " ".join(w.get("word", "") for w in heard)
        if strict_numbers(text, self.lang):
            return False                               # it heard numbers too — agree
        _dbg(f"vosk: vetoed — the full model confidently heard {text!r}")
        return True

    def _ensure_model(self):
        """Load the parakeet model. MUST be called on the STT thread only."""
        if self._model is None:
            model_id = os.environ.get("ALMATHUS_PARAKEET_MODEL", _DEFAULT_PARAKEET)
            _quiet_hf_hub(model_id)
            _dbg("loading parakeet model (first time can take ~60s)...")
            from parakeet_mlx import from_pretrained
            self._model = from_pretrained(model_id)
            _dbg("parakeet model loaded")

    def _transcribe_mlx(self, path: str) -> str:
        self._ensure_model()
        result = self._model.transcribe(path)
        return getattr(result, "text", str(result)).strip()
