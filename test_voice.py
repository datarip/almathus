"""Regression tests for the rule that decides what counts as a spoken answer.

Run with:  uv run python test_voice.py

These are the cases that cost real games: a kid reading the question aloud, a
filler word, a recognizer's "[unk]", the room. Every one of them used to be
scored as an answer and moved the card on. The rule now is that an utterance is
an answer only if it is numbers and nothing else — so all of them must come back
as "not an answer", and the card must stay up.

No audio and no model needed: this covers the text layer, which is where the
accept/refuse decision is finally made. The acoustic gates in front of it are
measured against a recorded corpus instead (see the comments in voice.py).
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

import languages
import voice as voicemod
import voices
from voice import _espeak_voice, _say_voice, _spoken_form, strict_numbers

# (transcript, language, expected)
#   [ints] — an answer
#   None   — words were said, but not only numbers: refuse it
#   []     — nothing was said
CASES = [
    # -- plain numbers ---------------------------------------------------- #
    ("huit", "fr", [8]),
    ("Huit.", "fr", [8]),
    ("huit ?", "fr", [8]),
    ("un", "fr", [1]),
    ("une", "fr", [1]),
    ("zéro", "fr", [0]),
    ("dix", "fr", [10]),
    ("seize", "fr", [16]),
    ("4", "fr", [4]),
    ("15", "fr", [15]),
    # -- French compounds, including the arithmetic ones ------------------ #
    ("dix-sept", "fr", [17]),
    ("dix sept", "fr", [17]),
    ("vingt et un", "fr", [21]),
    ("soixante-dix", "fr", [70]),
    ("soixante et onze", "fr", [71]),
    ("quatre-vingts", "fr", [80]),
    ("quatre-vingt-dix", "fr", [90]),
    ("quatre-vingt-dix-neuf", "fr", [99]),
    ("cent", "fr", [100]),
    ("cent cinq", "fr", [105]),
    ("deux cents", "fr", [200]),
    ("septante-cinq", "fr", [75]),  # Belgian / Swiss, said by plenty of kids
    ("nonante", "fr", [90]),
    # -- several numbers in one breath (the caller picks which one) ------- #
    ("trois quatre", "fr", [3, 4]),
    ("deux deux", "fr", [2, 2]),
    ("vingt trente", "fr", [20, 30]),
    # -- NOT an answer: the card must stay up ----------------------------- #
    ("quinze moins quatre", "fr", None),  # the question, read aloud
    ("quinze moins quatre égale", "fr", None),
    ("trois plus quatre ça fait", "fr", None),
    ("je sais pas", "fr", None),
    ("euh", "fr", None),
    ("attends", "fr", None),
    ("c'est huit", "fr", None),  # a number, but not on its own
    ("huit euh", "fr", None),
    ("maman", "fr", None),
    ("[unk]", "fr", None),  # what a recognizer says for "no idea"
    ("quinze [unk]", "fr", None),  # ...even next to a number
    ("<unk>", "fr", None),
    ("et", "fr", []),  # glue with nothing to glue
    ("", "fr", []),
    ("   ", "fr", []),
    # -- English ----------------------------------------------------------- #
    ("eight", "en", [8]),
    ("Eight.", "en", [8]),
    ("one", "en", [1]),
    ("zero", "en", [0]),
    ("seventeen", "en", [17]),
    ("twenty one", "en", [21]),
    ("twenty-one", "en", [21]),
    ("forty two", "en", [42]),
    ("ninety nine", "en", [99]),
    ("one hundred", "en", [100]),
    ("one hundred and five", "en", [105]),
    ("three four", "en", [3, 4]),
    ("fifteen minus four", "en", None),
    ("i don't know", "en", None),
    ("um", "en", None),
    ("let me think", "en", None),
    ("one more time", "en", None),  # a number, but not on its own
    ("no", "en", None),
    ("that's too hard", "en", None),
    # -- a French word is not an English number, and the other way round --- #
    # A recognizer can answer in the wrong language (the English "two" for the
    # French "trois"); without this the card would have scored 2.
    ("two", "fr", None),
    ("huit", "en", None),
    # -- the six languages num2words cannot spell -------------------------- #
    # Chinese, Hindi, Gujarati, Georgian, Kyrgyz and Uzbek get their words from
    # a table in voice.py. The full 0..100 round trip is in table_failures();
    # these are the cases that pin the shape of each language.
    ("七", "cn", [7]),
    ("二十三", "cn", [23]),
    ("二 十 三", "cn", [23]),  # a Chinese decoder may space the characters
    ("सैंतालीस", "hi", [47]),
    ("સિત્તેર", "gu", [70]),
    ("ოცდაშვიდი", "ka", [27]),
    ("жыйырма жети", "ky", [27]),
    ("yigirma yetti", "uz", [27]),
    ("o'n", "uz", [10]),  # an ASCII apostrophe for the ʻ the table uses
    ("yigirma ettinchi", "uz", None),  # a number plus a word: refuse it
    ("धन्यवाद", "hi", None),
    ("谢谢", "cn", None),
]


# -- which voice speaks ------------------------------------------------------ #
#
# The app used to pick `say` voices alphabetically, which is how Albert — a 1994
# MacinTalk voice listed first among the English ones — became the voice of every
# language but French. These are the catalogues that decision is made from, with
# the joke voices and the character voices that `say -v '?'` lists everywhere.

# (voice, locale) as `say -v '?'` reports them, minus the variant suffix.
MAC_LISTING = [
    ("Albert", "en_US"),
    ("Samantha", "en_US"),
    ("Flo", "en_US"),
    ("Bells", "en_US"),
    ("Daniel", "en_GB"),
    ("Flo", "en_GB"),
    ("Karen", "en_AU"),
    ("Majed", "ar_001"),
    ("Mónica", "es_ES"),
    ("Thomas", "fr_FR"),
    ("Amélie", "fr_CA"),
]

VOICE_CASES = [
    # An English language is read by an English voice, and the best one wins
    # over the alphabetically first.
    ("en-us", "Samantha"),
    # en-gb's curated woman is a character voice on a stock Mac, but she is
    # still a woman's voice and the only one installed; a man is not preferred
    # over her just because Apple files her under "Fun".
    ("en-gb", "Flo"),
    # A language is never read by another language's voice. This is the bug that
    # read Spanish cards with Albert.
    ("es", "Mónica"),
    ("ar", "Majed"),  # ar_001 used to be dropped by the locale parser
    ("fr", "Amélie"),  # the woman curated first wins over Thomas
    # No voice for the language at all: None means "let the OS use its default",
    # never "borrow the nearest English voice".
    ("eo", None),
    ("ka", None),
]

# Piper is the harmonized voice; these are the languages it has no model for.
NO_PIPER = {"eo", "gu", "ky", "tg", "uz"}


def voice_failures() -> list[str]:
    failures = []
    for lang, expected in VOICE_CASES:
        got = _say_voice(lang, MAC_LISTING)
        if got != expected:
            failures.append(f"  say {lang}: got {got!r}, expected {expected!r}")
    available = {lang.code for lang in languages.LANGUAGES}
    missing = available - set(voices.VOICES)
    if missing != NO_PIPER:
        failures.append(f"  piper voices: unknown coverage, missing {sorted(missing)}")
    for code in sorted(available):
        if _espeak_voice(code) is None:
            failures.append(f"  espeak: no voice mapping for {code}")
    for code, entry in voices.VOICES.items():
        if not entry.sha256 or len(entry.sha256) != 64 or entry.size <= 0:
            failures.append(f"  piper {code}: incomplete catalog entry")
    return failures


# -- how a French phrase must be pronounced ---------------------------------- #
#
# The phonemizer elides French's final -s before a consonant, so "10 fois 4" was
# heard as /di fwa katʁ/ and the operator "+" as /ply/. These are the respellings
# that put the sounds back; the digits are spelled out first so the substitution
# has a word to apply to.
SPOKEN_CASES = [
    # Already words: no library needed.
    ("dix plus quatre", "disse plusse quatre"),
    ("six fois quatre", "sisse fois quatre"),
    # dix-sept and dix-neuf keep the -s they already pronounce; dix-huit does
    # not, so its tail is respelled like a bare huit.
    ("dix-sept moins neuf", "dix-sept moins neuf"),
    ("dix-huit divisé par trois", "dix-huite divisé par trois"),
    # Only French is respelled.
    ("10 plus 4", "10 plus 4"),
]
SPOKEN_LANGS = ["fr", "fr", "fr", "fr", "en-us"]

# The digit cases need num2words, which ships in the voice extra.
DIGIT_CASES = [
    ("10 plus 4", "disse plusse quatre"),
    ("6 fois 4", "sisse fois quatre"),
    ("8 plus 4", "huite plusse quatre"),  # the -t goes the same way as the -s
    ("17 moins 9", "dix-sept moins neuf"),  # dix-sept is one token, and right
    ("18 plus 4", "dix-huite plusse quatre"),  # a compound's tail is affected
    ("88 plus 4", "quatre-vingt-huite plusse quatre"),
    ("90 sur 100", "quatre-vingt-disse sur cent"),  # the tail keeps its -s too
    ("Tu as eu 18 sur 20.", "Tu as eu dix-huite sur vingt."),
]


def spoken_failures() -> list[str]:
    failures = []
    for (text, expected), lang in zip(SPOKEN_CASES, SPOKEN_LANGS, strict=True):
        got = _spoken_form(text, lang)
        if got != expected:
            failures.append(f"  spoken {lang} {text!r}: got {got!r}")
    try:
        import num2words  # noqa: F401
    except ImportError:
        return failures
    for text, expected in DIGIT_CASES:
        got = _spoken_form(text, "fr")
        if got != expected:
            failures.append(f"  spoken fr {text!r}: got {got!r}")
    return failures


# -- the out-of-process voice ------------------------------------------------ #
#
# The game talks to a separate program over stdin/stdout. A stub speaks the same
# protocol with no model and no audio device, so the framing, the readiness
# handshake and the fallback path are tested without downloading 60 MB or
# making a sound.
FAKE_SIDECAR = """
import json, sys
out = sys.stdout.buffer
out.write(b'{"ready": true}\\n'); out.flush()
for line in sys.stdin:
    request = json.loads(line)
    if request.get("cmd") == "quit":
        break
    pcm = bytes(400)
    out.write((json.dumps({"sample_rate": 22050, "n": len(pcm)}) + "\\n").encode())
    out.flush()
    out.write(pcm); out.flush()
"""


def sidecar_failures() -> list[str]:
    try:
        import sounddevice  # the voice extra
    except ImportError:
        return []  # nothing to test without the playback stack

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        script = folder / "fake_sidecar.py"
        script.write_text(FAKE_SIDECAR, encoding="utf-8")
        entry = voices.VOICES["fr"]
        (folder / f"{entry.model}.onnx").write_bytes(b"x")
        (folder / f"{entry.model}.onnx.json").write_bytes(b"{}")

        names = (
            "ALMATHUS_VOICES_DIR",
            "ALMATHUS_PIPER_SIDECAR",
            "ALMATHUS_TTS_BACKEND",
        )
        old = {name: os.environ.get(name) for name in names}
        os.environ["ALMATHUS_VOICES_DIR"] = str(folder)
        os.environ["ALMATHUS_PIPER_SIDECAR"] = str(script)
        os.environ.pop("ALMATHUS_TTS_BACKEND", None)

        played: list[tuple[int, int]] = []
        saved = (sounddevice.play, sounddevice.wait, sounddevice.stop)
        sounddevice.play = lambda audio, rate: played.append((len(audio), rate))
        sounddevice.wait = lambda: None
        sounddevice.stop = lambda: None
        tts = None
        try:
            tts = voicemod.TTS("fr")
            if tts.describe().split()[0] != "piper-sidecar":
                failures.append(f"  sidecar: backend is {tts.describe()}")
            elif not tts._sidecar_ready.wait(10):
                failures.append("  sidecar: never became ready")
            else:
                done = threading.Event()
                tts.speak("10 plus 4", on_done=done.set)
                if not done.wait(10):
                    failures.append("  sidecar: no completion")
                elif not played:
                    failures.append("  sidecar: nothing was played")
        finally:
            if tts is not None:
                tts.close()
            sounddevice.play, sounddevice.wait, sounddevice.stop = saved
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    return failures


def table_failures() -> list[str]:
    """Every written-out number word is recognized as itself, 0..100.

    The six languages num2words cannot spell get their words from
    ``voice._TABLE_NUMBERS``. A word that is missing, mis-indexed or shadowed by
    a longer phrase would parse as the wrong number — the card would score the
    wrong answer — so the whole table is walked, not a sample.
    """
    failures: list[str] = []
    for lang, spellings in voicemod._TABLE_NUMBERS.items():
        if voicemod._grammar_for(lang) is None:
            failures.append(f"  {lang}: no Vosk grammar, voice would stay off")
            continue
        for n, spelling in enumerate(spellings):
            got = strict_numbers(spelling, lang)
            if got != [n]:
                failures.append(f"  {lang} {spelling!r}: got {got!r}, expected {[n]}")
    return failures


def main() -> int:
    failures = []
    for text, lang, expected in CASES:
        got = strict_numbers(text, lang)
        if got != expected:
            failures.append(f"  {lang} {text!r}: got {got!r}, expected {expected!r}")
    failures += voice_failures()
    failures += spoken_failures()
    failures += sidecar_failures()
    failures += table_failures()
    total = (
        len(CASES)
        + len(VOICE_CASES)
        + len(SPOKEN_CASES)
        + len(DIGIT_CASES)
        + 1  # the Piper coverage check
        + 1  # the sidecar round trip
        + len(languages.LANGUAGES)  # every language has an espeak mapping
        + len(voices.VOICES)  # every catalog entry is complete
        + len(voicemod._TABLE_NUMBERS)  # every table language has a grammar
        + sum(len(t) for t in voicemod._TABLE_NUMBERS.values())
    )
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
