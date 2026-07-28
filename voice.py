"""Voice I/O for Almathus — text-to-speech + speech-to-text.

Two independent, gracefully-degrading pieces:

* ``TTS``          — speaks encouragement in a warm feminine voice.
                     Uses macOS ``say`` (offline, built-in); falls back to
                     ``pyttsx3`` if present; otherwise a silent no-op.

* ``SpeechInput``  — records from the mic with simple voice-activity detection
                     (auto-stops on silence / timeout), transcribes locally with
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

    def stop(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None


# --------------------------------------------------------------------------- #
# Number parsing (French + English words, or digits)
# --------------------------------------------------------------------------- #

_UNITS = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
    "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50,
    "soixante": 60,
    # english
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six_en": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_HUNDRED = {"cent", "cents", "hundred"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def numbers_in(text: str, lang: str = "fr") -> list[int]:
    """Every number in a transcript, in the order it was said.

    The caller needs all of them, not just one: a kid reading the question out
    loud ("quinze moins quatre") says two numbers and has answered nothing,
    which is only visible if both survive this far.

    Primary path uses the multilingual ``text2num`` library (best for
    FR/EN/ES/PT/DE...); a built-in FR/EN parser is the fallback. Trailing
    punctuation from the recognizer (e.g. "huit.") is ignored either way.
    """
    if not text:
        return []
    try:
        from text_to_num import alpha2digit
        code = lang if lang in ("fr", "en", "es", "pt", "de", "ca", "nl") else "en"
        digits = re.findall(r"-?\d+", alpha2digit(text, code))
        if digits:
            return [int(d) for d in digits]
    except Exception:
        pass
    n = _fallback_parse_number(text)
    return [] if n is None else [n]


def parse_number(text: str, lang: str = "fr") -> Optional[int]:
    """The single number a transcript states, or None.

    The LAST one wins, which handles self-correction ("trois... non, quatre").
    """
    nums = numbers_in(text, lang)
    return nums[-1] if nums else None


def _fallback_parse_number(text: str) -> Optional[int]:
    """Built-in FR/EN word+digit parser, used when ``text2num`` is unavailable."""
    if not text:
        return None

    low = _strip_accents(text.lower())

    # 1) a plain digit run wins (e.g. "j'ai dit 8" -> 8, "20" -> 20)
    digits = re.findall(r"-?\d+", low)
    if digits:
        return int(digits[0])

    # 2) French irregular tens (quatre-vingt = 4*20, not 4+20) -> substitute numbers
    low = low.replace("-", " ")
    low = re.sub(r"\bquatre\s+vingt\s+dix\b", " 90 ", low)
    low = re.sub(r"\bquatre\s+vingts?\b", " 80 ", low)
    low = low.replace(" et ", " ").replace(" and ", " ")

    tokens = re.findall(r"\d+|[a-z]+", low)
    total = current = 0
    seen = False
    for tok in tokens:
        if tok.isdigit():
            current += int(tok)
            seen = True
        elif tok in _HUNDRED:
            current = (current or 1) * 100
            seen = True
        elif tok in _UNITS:
            current += _UNITS[tok]
            seen = True
        # everything else (filler words) is ignored
    total += current
    return total if seen else None


# --------------------------------------------------------------------------- #
# Speech-to-text (mic capture + VAD + parakeet)
# --------------------------------------------------------------------------- #

_SAMPLE_RATE = 16000
_BLOCK = 320               # 20 ms blocks
_PRE_TIMEOUT = 5.0         # give up if no speech starts within this
_SILENCE_HANG = 0.35       # stop this long after speech ends (snappy response)
_MAX_DURATION = 6.0        # hard cap on a single utterance
_VOICE_BAND = (300.0, 3400.0)   # Hz — speech band; cuts low rumble & high hiss

# v3 is multilingual (incl. French); v2 is English-only.
_DEFAULT_PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"

_WARMUP = object()   # sentinel job: preload the model on the STT thread

# Vosk number-only grammar (fallback). Constraining the vocabulary to number words
# rescues cases the general model can't nail — notably the French "un" (heard as
# "Mm"/"On" by parakeet). "[unk]" lets Vosk reject non-numbers instead of forcing one.
_VOSK_GRAMMAR = {
    "fr": ('["zéro un une deux trois quatre cinq six sept huit neuf dix onze douze '
           'treize quatorze quinze seize vingt trente quarante cinquante soixante '
           'cent et", "[unk]"]'),
    "en": ('["zero one two three four five six seven eight nine ten eleven twelve '
           'thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty '
           'thirty forty fifty sixty seventy eighty ninety hundred", "[unk]"]'),
}


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
        self._busy = False     # guard against overlapping listen() calls
        self._rec_sr = _SAMPLE_RATE
        self._jobs: queue.Queue = queue.Queue()   # audio -> the one STT thread
        self._stt_thread = None                   # owns & runs the model (MLX is thread-affine)
        self._vosk_model = None
        self._vosk_ok = None                      # None=unknown, False=unavailable
        self._noise_floor = None                  # persists across listens (no re-calibration)
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
        if self._busy:
            _dbg("listen() ignored — already busy")
            return                       # one capture at a time
        _dbg("listen() -> starting capture")
        self._busy = True
        self._cancel.clear()
        self._ensure_stt_thread()
        # Record on a throwaway thread (the mic is fine cross-thread); transcription
        # is handed to the single STT thread that owns the model.
        threading.Thread(target=self._capture, args=(on_result, on_state, pick),
                         daemon=True).start()

    def cancel(self):
        self._cancel.set()

    def stop(self):
        """Stop any in-progress capture and release the microphone right away
        (so the OS mic-in-use indicator turns off when the app quits)."""
        self._stopped = True             # prevent the continuous loop from reopening it
        self._cancel.set()
        s = self._active_stream
        self._active_stream = None
        if s is not None:
            for fn in ("abort", "close"):
                try:
                    getattr(s, fn)()
                except Exception:
                    pass

    def warmup(self):
        """Preload the recognizer on the STT thread so the first answer is instant."""
        self._ensure_stt_thread()
        self._jobs.put((_WARMUP, None, None, None))

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
        if self._stt_thread is None:
            self._stt_thread = threading.Thread(target=self._stt_loop, daemon=True)
            self._stt_thread.start()

    def _capture(self, on_result, on_state, pick=None):
        try:
            self._silent = False
            audio = self._record(on_state)
            if self._silent and on_state:
                _dbg("capture SILENT -> no_mic")
                on_state("no_mic")     # only silence -> no mic access / no usable device
            self._jobs.put((audio, on_result, on_state, pick))
        except Exception as e:
            _dbg("capture EXCEPTION:", repr(e))
            self._jobs.put((None, on_result, on_state, pick))

    def _stt_loop(self):
        """The ONE thread that creates and runs the parakeet model — MLX streams
        are thread-local, so the model must never cross threads."""
        while True:
            audio, on_result, on_state, pick = self._jobs.get()
            if audio is _WARMUP:
                try:
                    self._load_vosk()                 # primary recognizer, fast (~1s)
                except Exception as e:
                    _dbg("vosk warmup failed:", repr(e))
                if os.environ.get("ALMATHUS_PARAKEET") and self._backend == "mlx":
                    try:
                        self._ensure_model()          # optional backup, slow (~60s)
                    except Exception as e:
                        _dbg("parakeet warmup failed:", repr(e))
                continue
            try:
                if audio is None or self._cancel.is_set():
                    _dbg("no audio (timeout/cancel) -> None")
                    on_result(None)
                    continue
                if on_state:
                    on_state("thinking")
                # Vosk FIRST: fast (~100ms), number-grammar, and the accurate one for
                # isolated digits. Runs on the RAW audio (band-pass breaks Vosk).
                _t0 = time.monotonic()
                raw = self._last_raw if self._last_raw is not None else audio
                nums = self._vosk_numbers(raw)
                _dbg(f"vosk -> {nums} ({(time.monotonic() - _t0) * 1000:.0f}ms)")
                # parakeet only as a backup, and only if it's already loaded (never
                # block on its ~60s load — Vosk handles the common case).
                if not nums and self._model is not None:
                    text = self._transcribe(audio)
                    nums = numbers_in(text, self.lang)
                    _dbg(f"parakeet fallback transcript={text!r} -> {nums}")
                parsed = pick(nums) if pick else (nums[-1] if nums else None)
                _dbg(f"picked -> {parsed}")
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
        """
        sd = self._sd
        try:
            dev_sr = int(sd.query_devices(kind="input").get("default_samplerate") or 16000)
        except Exception:
            dev_sr = 16000
        # Prefer rates WebRTC VAD supports (8/16/32/48 kHz) so the VAD can run.
        for sr in (16000, 48000, 32000, 8000, dev_sr, 44100, 22050):
            try:
                sd.check_input_settings(samplerate=sr, channels=1, dtype="int16")
                return sr, int(sr * 0.02)   # exactly 20 ms frames (WebRTC VAD needs 10/20/30)
            except Exception:
                continue
        return None, 0

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

    def _record(self, on_state):
        if self._stopped:
            return None
        sd, np = self._sd, self._np
        sr, blk = self._pick_stream_params()
        if sr is None:
            self._silent = True          # no usable input device
            return None
        self._rec_sr = sr
        _dbg(f"record: samplerate={sr} blocksize={blk}")
        block_secs = blk / sr
        frames = []                       # every captured block (RAW), in order
        pre = int(0.25 / block_secs)      # keep a little before the onset
        post = int(0.25 / block_secs)     # ...and a little after the tail
        min_voiced = max(4, int(0.10 / block_secs))   # need ≥0.10s of real speech

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

        st = {"start": None, "last": None, "peak": 0.0, "run": 0, "efloor": None}
        lock = threading.Lock()

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
            if r > max(st["efloor"] * 4.0, 60.0):
                return True
            if wvad is not None and len(block) == blk:
                try:
                    return bool(wvad.is_speech(block.tobytes(), sr))
                except Exception:
                    pass
            return False

        def cb(indata, _frames, _tinfo, _status):
            block = indata[:, 0]
            r = band_rms(block)
            with lock:
                i = len(frames)
                frames.append(block.copy())
                if r > st["peak"]:
                    st["peak"] = r
                if st["start"] is None:
                    if is_speech(block, r):
                        st["run"] += 1
                        if st["run"] >= 2:              # ~40 ms of speech -> go
                            st["start"] = max(0, i - st["run"] + 1)
                            st["last"] = i
                    else:
                        st["run"] = 0
                elif r > max(st["peak"] * 0.12, 40.0):  # still speaking (energy)
                    st["last"] = i

        if on_state:
            on_state("listening")
        try:
            stream = sd.InputStream(samplerate=sr, channels=1,
                                    dtype="int16", blocksize=blk, callback=cb)
        except Exception:
            self._silent = True   # can't open the mic -> surface the hint
            return None
        actual = int(getattr(stream, "samplerate", sr) or sr)
        if actual != sr:
            _dbg(f"record: requested {sr} Hz but device opened at {actual} Hz")
        self._rec_sr = actual
        self._active_stream = stream
        if self._stopped or self._cancel.is_set():   # stop() raced with the open -> release now
            for fn in ("abort", "close"):
                try:
                    getattr(stream, fn)()
                except Exception:
                    pass
            self._active_stream = None
            return None

        t0 = time.monotonic()
        try:
            with stream:
                while not self._cancel.is_set():
                    time.sleep(block_secs)
                    now = time.monotonic()
                    with lock:
                        started = st["start"] is not None
                        last, n = st["last"], len(frames)
                    if now - t0 > _MAX_DURATION:
                        break
                    if not started and now - t0 > _PRE_TIMEOUT:
                        self._silent = st["peak"] < 3.0
                        _dbg(f"record: no speech in {_PRE_TIMEOUT}s (peak={st['peak']:.0f})")
                        return None
                    if started and last is not None and \
                            (n - last) * block_secs > _SILENCE_HANG:
                        break                       # speaker went quiet -> done
        finally:
            self._active_stream = None

        with lock:
            start, last, peak = st["start"], st["last"], st["peak"]
        voiced = (last - start + 1) if (start is not None and last is not None) else 0
        if start is None or voiced < min_voiced:
            self._silent = peak < 3.0
            _dbg(f"record: no sustained speech (start={start}, voiced={voiced}, peak={peak:.0f})")
            return None
        seg = frames[max(0, start - pre): min(len(frames), last + post + 1)]
        _dbg(f"record: captured {len(seg)} blocks (~{len(seg)*block_secs:.1f}s), peak={peak:.0f}")
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

    def _vosk_numbers(self, audio):
        """Recognize the numbers in an utterance via Vosk's number-only grammar
        (+N-best). Runs on the STT thread. Returns a list of ints, in order."""
        if not self._load_vosk():
            return []
        try:
            import json as _json
            from vosk import KaldiRecognizer
            grammar = _VOSK_GRAMMAR.get(self.lang, _VOSK_GRAMMAR["fr"])
            rec = KaldiRecognizer(self._vosk_model, float(self._rec_sr), grammar)
            rec.SetMaxAlternatives(6)          # N-best: the number is often just behind [unk]
            rec.AcceptWaveform(self._normalize(audio).tobytes())   # gain-only (raw audio)
            res = _json.loads(rec.FinalResult())
            texts = ([a.get("text", "") for a in res["alternatives"]]
                     if res.get("alternatives") else [res.get("text", "")])
            _dbg(f"vosk alts={texts[:4]}")
            for t in texts:                    # take the highest-confidence number
                nums = numbers_in(t, self.lang)
                if nums:
                    return nums
            return []
        except Exception as e:
            _dbg("vosk error:", repr(e))
            return []

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
