"""Checks that every supported language really has a complete screen vocabulary.

Run with:  uv run python test_i18n.py

No Tk window and no microphone: this only reads the dictionaries in i18n.py and
the registry in languages.py, so it is safe to run for every translation change.
A language may share another's dictionary through its base code (en-gb -> en,
ar-tn -> ar); the point is that no registry entry resolves to English by
accident, every dictionary carries the same keys as English, and no translation
lost or renamed a ``{placeholder}``.
"""

from __future__ import annotations

import string
import sys

import i18n
import languages

ENGLISH = i18n.STRINGS["en"]


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def test_every_registry_language_resolves():
    """Each pickable code finds text, and regional codes find their base."""
    for language in languages.LANGUAGES:
        assert i18n.text(language.code, "how_many") != "how_many", language.code
        assert i18n.month(language.code, 1), language.code
    assert i18n.text("en-gb", "how_many") == i18n.text("en", "how_many")
    assert i18n.text("ar-tn", "how_many") == i18n.text("ar", "how_many")


def test_every_dictionary_has_the_english_keys():
    """No dictionary silently drops a screen, and none invents a stray key."""
    for code, strings in i18n.STRINGS.items():
        assert set(strings) == set(ENGLISH), (
            f"{code}: missing {sorted(set(ENGLISH) - set(strings))}, "
            f"extra {sorted(set(strings) - set(ENGLISH))}"
        )


def test_placeholders_survive_translation():
    """A translated format string uses exactly the fields English passes in."""
    for code, strings in i18n.STRINGS.items():
        for key, template in strings.items():
            assert _placeholders(template) == _placeholders(ENGLISH[key]), (
                f"{code}/{key}: {_placeholders(template)} != "
                f"{_placeholders(ENGLISH[key])}"
            )


# The progress screen's vocabulary: its title, the table's columns, the chart's
# legend and axes, the month filter and the PDF button. These are the strings a
# parent reads when they look at a child's progress, so a column left in English
# is the gap nobody notices until the screen is in Arabic.
PROGRESS_KEYS = [
    key
    for key in ENGLISH
    if key.startswith(("col_", "legend_", "month_", "no_", "pdf_"))
] + ["progress", "progress_title", "chart_title", "axis_grade", "n_sessions"]

# Words that are legitimately the same in the target language and in English:
# "PDF" is a format name; "Date" is how French writes date; French and Catalan
# both say "sessions"; the footer is a product name plus "page".
SAME_AS_ENGLISH_OK = {"pdf_button", "pdf_footer", "col_date", "n_sessions"}


def test_progress_headers_are_translated():
    """No language is left with an English column heading."""
    for code, strings in i18n.STRINGS.items():
        if code.startswith("en"):
            continue  # the source language, by definition
        for key in PROGRESS_KEYS:
            if key in SAME_AS_ENGLISH_OK:
                continue
            assert strings[key] != ENGLISH[key], f"{code}/{key} is still English"


def test_every_dictionary_has_twelve_months():
    """The month chip can name any month, in every language."""
    for code, months in i18n.MONTHS.items():
        assert len(months) == 12, code
        assert all(months), code


def test_missing_key_falls_back_to_english_not_the_raw_key():
    """An entry added to English only still renders while translations catch up."""
    assert i18n.text("fr", "not_a_key") == "not_a_key"
    assert i18n._family("xx") is ENGLISH


def main() -> int:
    tests = (
        test_every_registry_language_resolves,
        test_every_dictionary_has_the_english_keys,
        test_placeholders_survive_translation,
        test_progress_headers_are_translated,
        test_every_dictionary_has_twelve_months,
        test_missing_key_falls_back_to_english_not_the_raw_key,
    )
    failures = []
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except Exception as e:  # noqa: BLE001 — a failure report, not a handler
            failures.append(f"FAIL {test.__name__}: {e}")
            print(failures[-1])
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
