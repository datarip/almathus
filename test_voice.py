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

import sys

from voice import strict_numbers

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
    # parakeet transcribes French speech into English words; without this the
    # French "trois" came back as "Two" and would have scored the card 2.
    ("two", "fr", None),
    ("huit", "en", None),
]


def main() -> int:
    failures = []
    for text, lang, expected in CASES:
        got = strict_numbers(text, lang)
        if got != expected:
            failures.append(f"  {lang} {text!r}: got {got!r}, expected {expected!r}")
    for line in failures:
        print(line)
    print(f"{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
