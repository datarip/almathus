"""Regression tests for the `difficultes` column and the revision it drives.

Run with:  uv run python test_difficulty.py

Two halves that have to agree, or the loop quietly stops turning:

* what a finished session *writes* — the five cards it fought with, ranked so
  that a card which was failed outranks one that was merely slow, and timed over
  both sittings so a card failed fast is not mistaken for an easy one;
* what the next session *reads* — the same cell, parsed back into operations and
  dealt twice each, without ever taking over the round or asking the same fact
  twice in a row.

No microphone, no Tk window: everything here is a pure function or a CSV in a
temporary directory. The one thing that needs a running game — a retried card
adding its second sitting to its first — is pinned in `test_game_flow.py`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from itertools import pairwise
from pathlib import Path

# main reads its preference path at import time. Without this the suite would
# read and write the real user's prefs.json, which is both a side effect on the
# machine and a source of flakiness: a saved "numbers: [4]" changes what the
# deck deals.
os.environ.setdefault("ALMATHUS_PREFS_DIR", tempfile.mkdtemp(prefix="almathus-prefs-"))

import main as almathus
import results as resultslog


def card(a: int, b: int, seconds: float, retry: float = 0.0, ok: bool = True):
    """One already-played subtraction card, with its outcome written on it."""
    c = almathus.make_card("subtraction", a, b)
    c.time_taken = seconds
    c.retry_time = retry
    c.correct_first_try = ok
    c.attempts = 1 if ok else 2
    return c


def test_operation_round_trip():
    """What we write reads back, and so does what a parent types instead."""
    for mode, a, b, text in (
        ("addition", 7, 4, "7 + 4"),
        ("subtraction", 18, 4, "18 - 4"),
        ("multiplication", 7, 8, "7 x 8"),
        ("division", 12, 4, "12 / 4"),
    ):
        assert resultslog.fmt_operation(mode, a, b) == text, text
        assert resultslog.parse_operation(text) == (mode, a, b), text

    # The signs the screen shows, and the ones a keyboard or a schoolbook puts
    # there — including the French `:` for a division.
    assert resultslog.parse_operation("18 − 4") == ("subtraction", 18, 4)
    assert resultslog.parse_operation("18−4") == ("subtraction", 18, 4)
    assert resultslog.parse_operation("7 × 8") == ("multiplication", 7, 8)
    assert resultslog.parse_operation("7*8") == ("multiplication", 7, 8)
    assert resultslog.parse_operation("12 ÷ 4") == ("division", 12, 4)
    assert resultslog.parse_operation("12 : 4") == ("division", 12, 4)

    # A `famille` cell is not an operation, and must never be read as one: the
    # two columns sit next to each other and use the same signs.
    for junk in ("", "  ", "-4", "abc", "-1 et -2", "+1–10", "18 -", "18 - x"):
        assert resultslog.parse_operation(junk) is None, repr(junk)


def test_difficulties_cell():
    """The cell is a list of operations and nothing else."""
    assert resultslog.fmt_difficulties(["18 - 4", "11 - 4"]) == "18 - 4, 11 - 4"
    assert resultslog.fmt_difficulties([]) == ""
    # Read tolerantly: the screen's signs, junk dropped, no card listed twice.
    assert resultslog.parse_difficulties("18 − 4, 11 - 4, poisson, 18-4") == [
        "18 - 4",
        "11 - 4",
    ]
    assert resultslog.parse_difficulties("") == []


def test_ranking():
    """Failed first, then slow — and both sittings count towards "slow"."""
    cards = [
        card(2, 1, 1.0),
        card(9, 4, 8.0),  # slowest of the ones that were right
        card(3, 1, 4.0),
        # Failed fast, but it came back at the end and cost another 9 s. It is
        # the hardest card of the session, and grading it on 0.5 s would have
        # put it last.
        card(17, 4, 0.5, retry=9.0, ok=False),
        card(13, 4, 6.0, ok=False),
        card(5, 2, 2.0),
    ]
    ranked = almathus.difficult_cards(cards)
    assert [c.text for c in ranked] == [
        "17 − 4",  # failed, 9.5 s over two goes
        "13 − 4",  # failed, 6 s
        "9 − 4",  # right, but the slowest of them
        "3 − 1",
        "5 − 2",
    ], [c.text for c in ranked]
    assert ranked[0].total_time == 9.5

    # A short number selection deals the same fact twice in a round. The list is
    # five cards, not five copies of one.
    twice = [card(9, 4, 9.0), card(9, 4, 8.0), card(2, 1, 1.0)]
    assert [c.text for c in almathus.difficult_cards(twice)] == ["9 − 4", "2 − 1"]

    assert almathus.difficult_cards([]) == []


def test_column_is_added_to_an_existing_log():
    """A log written before the column existed grows one, at the end, and every
    row it already had keeps its cells."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.csv"
        body = "2026-08-18,-4,2:15.9,20,6.8,20\n2026-08-18,x2,1:43.1,20,5.2,20\n"
        path.write_text("date,famille,temps,cartes,temps_moyen_par_carte,note\n" + body)

        resultslog.append_session(
            day=date(2026, 8, 19),
            famille="-4",
            seconds=120.0,
            cards=20,
            correct=18,
            lang="fr",
            path=path,
            difficultes=["17 - 4", "13 - 4", "9 - 4"],
        )
        lines = path.read_text().splitlines()
        assert lines[0].endswith(",difficultes"), lines[0]
        assert "\n".join(lines[1:3]) + "\n" == body, "an existing row was rewritten"
        assert lines[3].endswith('"17 - 4, 13 - 4, 9 - 4"'), lines[3]

        sessions = resultslog.load_sessions(path)
        assert [s.difficultes for s in sessions] == [
            [],
            [],
            ["17 - 4", "13 - 4", "9 - 4"],
        ]

        # A session with nothing to say leaves the cell empty rather than
        # writing a placeholder into it.
        resultslog.append_session(
            day=date(2026, 8, 20),
            famille="-4",
            seconds=100.0,
            cards=20,
            lang="fr",
            path=path,
        )
        assert resultslog.load_sessions(path)[-1].difficultes == []


def test_english_log_names_the_column_in_english():
    """The file's language wins over the app's — the column joins the ones
    already beside it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.csv"
        path.write_text("date,family,time,cards,grade,average_time_per_card\n")
        resultslog.append_session(
            day=date(2026, 8, 19),
            famille="-4",
            seconds=120.0,
            cards=20,
            correct=18,
            lang="fr",  # the app is in French; the file is not
            path=path,
            difficultes=["17 - 4"],
        )
        assert path.read_text().splitlines()[0].endswith(",difficulty")


def test_regional_english_log_uses_english_names():
    """English (UK/US/India) is still English for names and the family cell.

    The picker offers three English codes; there is one English header and one
    English joiner, so a fresh file must not fall back to the French ones.
    """
    assert resultslog.famille_label("subtraction", {1, 2}, "en-gb") == "-1 and -2"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.csv"
        resultslog.append_session(
            day=date(2026, 8, 19),
            famille=resultslog.famille_label("subtraction", {1, 2}, "en-gb"),
            seconds=120.0,
            cards=20,
            correct=18,
            lang="en-gb",
            path=path,
        )
        header, row = path.read_text().splitlines()
        assert header == "date,family,time,cards,grade,average_time_per_card,difficulty"
        assert row.split(",")[1] == "-1 and -2"


def test_language_without_translated_names_uses_english():
    """A language the UI falls back to English for writes English names too."""
    assert resultslog.famille_label("subtraction", {1, 2}, "de") == "-1 and -2"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.csv"
        resultslog.append_session(
            day=date(2026, 8, 19),
            famille=resultslog.famille_label("subtraction", {1, 2}, "de"),
            seconds=120.0,
            cards=20,
            correct=18,
            lang="de",
            path=path,
        )
        header, row = path.read_text().splitlines()
        assert header == "date,family,time,cards,grade,average_time_per_card,difficulty"
        assert row.split(",")[1] == "-1 and -2"


def logged(day: date, famille: str, difficultes: list[str]):
    """One row of the log, as `load_sessions` would hand it over."""
    return resultslog.Session(
        day=day,
        famille=famille,
        seconds=120.0,
        cards=20,
        avg_seconds=6.0,
        correct=18,
        difficultes=difficultes,
    )


def test_carry_over_reads_the_last_day_played():
    """The revision list is the last played day's difficulties, filtered down to
    what this session can ask."""
    log = [
        logged(date(2026, 8, 17), "-4", ["20 - 4", "3 - 4"]),  # an older day
        logged(date(2026, 8, 18), "-4", ["17 - 4", "13 - 4"]),
        # Two sessions on the same day: both of them count, and a card named by
        # both is only dealt once.
        logged(date(2026, 8, 18), "-4 et -2", ["9 - 4", "17 - 4", "8 - 2"]),
        logged(date(2026, 8, 18), "x2", ["7 x 2"]),
    ]
    assert almathus.carry_over(log, "subtraction", {4}) == [(17, 4), (13, 4), (9, 4)]
    # `8 - 2` belongs to a family that is switched off today.
    assert almathus.carry_over(log, "subtraction", {2, 4}) == [
        (17, 4),
        (13, 4),
        (9, 4),
        (8, 2),
    ]
    # Same day, different operation.
    assert almathus.carry_over(log, "multiplication", {2}) == [(7, 2)]
    assert almathus.carry_over(log, "addition", {4}) == []

    # A day whose sessions recorded nothing is not "the last day played" as far
    # as revision is concerned — there is nothing in it to revise.
    quiet = [*log, logged(date(2026, 8, 19), "-4", [])]
    assert almathus.carry_over(quiet, "subtraction", {4}) == [(17, 4), (13, 4), (9, 4)]

    assert almathus.carry_over([], "subtraction", {4}) == []


def test_the_log_round_trips_into_the_next_deck():
    """End to end: a session writes its difficulties, and the next one deals
    them."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.csv"
        played = [
            card(6, 4, 1.0),
            card(17, 4, 0.5, retry=9.0, ok=False),
            card(13, 4, 6.0, ok=False),
            card(9, 4, 5.0),
        ]
        hardest = almathus.difficult_cards(played)
        resultslog.append_session(
            day=date(2026, 8, 19),
            famille="-4",
            seconds=120.0,
            cards=len(played),
            correct=2,
            lang="fr",
            path=path,
            difficultes=[resultslog.fmt_operation(c.mode, c.a, c.b) for c in hardest],
        )
        revision = almathus.carry_over(
            resultslog.load_sessions(path), "subtraction", {4}
        )
        assert revision == [(17, 4), (13, 4), (9, 4), (6, 4)], revision
        ops = [(c.a, c.b) for c in almathus.make_deck("subtraction", 20, {4}, revision)]
        for op in revision:
            assert ops.count(op) == almathus.REPEAT_TIMES, (op, ops)


def test_deck_deals_the_repeats_twice():
    """Yesterday's hard cards come back, twice each, without taking the round
    over or asking the same fact twice in a row."""
    repeat = [(17, 4), (13, 4), (9, 4)]
    for _ in range(50):  # it is shuffled: run it enough to mean something
        deck = almathus.make_deck("subtraction", 20, {4}, repeat)
        assert len(deck) == 20
        ops = [(c.a, c.b) for c in deck]
        for op in repeat:
            assert ops.count(op) == almathus.REPEAT_TIMES, (op, ops)
        assert all(x != y for x, y in pairwise(ops)), ops

    # Revision never eats more than half the round: at 8 cards, only two of the
    # five can be carried over (2 × 2 = 4 of the 8).
    five = [(17, 4), (13, 4), (9, 4), (6, 4), (20, 4)]
    ops = [(c.a, c.b) for c in almathus.make_deck("subtraction", 8, {4}, five)]
    assert sum(ops.count(op) == 2 for op in five) == 2, ops
    # ...and a round too short for even one repeat is simply dealt as it always
    # was, rather than dealt short.
    assert len(almathus.make_deck("subtraction", 3, {4}, five)) == 3

    # An operation the current settings cannot ask is dropped, not forced in:
    # yesterday was −4, today is −2.
    ops = [(c.a, c.b) for c in almathus.make_deck("subtraction", 20, {2}, repeat)]
    assert all(b == 2 for _, b in ops), ops

    # No revision at all is the deck the game has always dealt.
    assert len(almathus.make_deck("subtraction", 20, {4})) == 20


def test_spread_separates_twins():
    """The shuffle that mixes revision in never leaves a pair side by side."""
    for n in (2, 3, 5, 8):
        ops = [(i, 4) for i in range(n) for _ in range(2)]
        for _ in range(50):
            out = almathus._spread(list(ops))
            assert sorted(out) == sorted(ops), out
            assert all(x != y for x, y in pairwise(out)), out


def main() -> int:
    tests = (
        test_operation_round_trip,
        test_difficulties_cell,
        test_ranking,
        test_carry_over_reads_the_last_day_played,
        test_the_log_round_trips_into_the_next_deck,
        test_column_is_added_to_an_existing_log,
        test_english_log_names_the_column_in_english,
        test_regional_english_log_uses_english_names,
        test_language_without_translated_names_uses_english,
        test_deck_deals_the_repeats_twice,
        test_spread_separates_twins,
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
