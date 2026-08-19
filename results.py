"""The results log — one CSV that doubles as the app's little database.

Every finished session appends one line to `results/results.csv`. The same file
is read back by the progress screen (chart + table), so the file is the single
source of truth: edit it by hand between sessions and the app follows.

It is the digital continuation of the paper "Calcul mental" sheet, which had
three hand-written columns — Date / Famille / Temps — plus one derived column
we now compute for you.

═══════════════════════════════════════════════════════════════════════════════
HEADER DEFINITION
═══════════════════════════════════════════════════════════════════════════════

Canonical French header (written when LANG = "fr"):

    date,famille,temps,cartes,note,temps_moyen_par_carte

Canonical English header (written when LANG = "en"):

    date,family,time,cards,grade,average_time_per_card

| field         | fr header               | en header              | meaning                                   |
|---------------|-------------------------|------------------------|-------------------------------------------|
| `date`        | `date`                  | `date`                 | day the session was played                |
| `famille`     | `famille`               | `family`               | which operation family was drilled ("-3") |
| `temps`       | `temps`                 | `time`                 | total time of the whole session           |
| `cartes`      | `cartes`                | `cards`                | how many cards were asked                 |
| `note`        | `note`                  | `grade`                | cards answered right the first time       |
| `temps_moyen` | `temps_moyen_par_carte` | `average_time_per_card`| `temps / cartes`                          |

Either language is accepted on read, in any column order, and header matching
ignores case, accents, spaces and underscores — so `Temps`, `temps`,
`Temps moyen par carte` and `temps_moyen` all land on the right field. A handful
of synonyms are accepted too (see ALIASES). Columns the app doesn't know are
kept in place and left empty when it appends. A column the app *does* know but
the file lacks is added to the end of the header the first time there is
something to write in it — at the end, so every existing row keeps its cells.

Hand-editing rules
------------------
* **date** — `2026-06-16`, `16/06/2026` or just `16/06` (year defaults to the
  most recent one that isn't in the future). **An empty date is carried down
  from the row above**, exactly like the blank cells on the paper sheet.
* **famille** — free text. The app writes the operator sign followed by the
  numbers that were switched on: `-3`, `-1 et -2`, `x7`, `+1–10`. Signs are
  ASCII so the column stays easy to type: `+` `-` `x` `/`.
* **temps** — many notations are read (see `parse_duration`):
  `1:23` `1:23.4` `1'23` `1'23"4` `83"` `83` `83.4` `1,23` `2,01,95`.
  A comma followed by exactly two digits below 60 is read as the minute
  separator (`1,29` = 1 min 29 s); otherwise it is a decimal point
  (`32,60` = 32.6 s). Use a dot when you mean a decimal.
* **cartes** — an integer, or empty if unknown (the old paper rows).
* **note** — how many cards were answered right on the first try. Written as a
  bare number (`15`), because the denominator is the `cartes` cell next to it;
  the screen, the chart and the PDF all show it as `15/20`. A hand-written
  `15/20` is read too — only the numerator is kept. Empty on the old paper rows,
  and on any row with no card count there is nothing to show. A day played in
  several sessions is graded on the *average* of them, not their total — see
  `grade`.
* **temps_moyen_par_carte** — normally left to the app, in plain seconds. If
  empty it is computed as `temps / cartes`; fill it in by hand for the old paper
  rows, where `cartes` is unknown.

  Note this is **not** the average the recap screen shows. The recap averages the
  time spent thinking on each card; this column divides the *whole* session —
  including the pause after each answer and the reinforcement round — by the
  number of cards, so it stays consistent with the two columns beside it and can
  be checked with a calculator.

Rows that can't be parsed at all are skipped on read; they are never rewritten,
so nothing you type here can be destroyed by the app.

The progress screen can narrow itself down to one month (and print that month as
a report), but that is only ever a *view*: this stays one flat file, appended to
in the order the sessions were played, with no per-month sections to keep in sync.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_FILE = RESULTS_DIR / "results.csv"

# The six logical fields, in canonical order. `temps_moyen` is derived but kept
# as a real column so it can be hand-filled for rows with no card count.
FIELDS = ("date", "famille", "temps", "cartes", "note", "temps_moyen")

HEADERS = {
    "fr": {
        "date": "date",
        "famille": "famille",
        "temps": "temps",
        "cartes": "cartes",
        "note": "note",
        "temps_moyen": "temps_moyen_par_carte",
    },
    "en": {
        "date": "date",
        "famille": "family",
        "temps": "time",
        "cartes": "cards",
        "note": "grade",
        "temps_moyen": "average_time_per_card",
    },
}

# Every spelling we accept for each field, already normalized by `_key`.
ALIASES = {
    "date": ("date", "jour", "day"),
    "famille": ("famille", "family", "familles", "families"),
    "temps": ("temps", "time", "duree", "duration", "temps_total", "total_time"),
    "cartes": (
        "cartes",
        "cards",
        "nb_cartes",
        "n_cartes",
        "n_cards",
        "card_count",
        "nombre_de_cartes",
    ),
    "note": (
        "note",
        "notes",
        "grade",
        "grades",
        "score",
        "reussites",
        "reussies",
        "bonnes_reponses",
        "correct",
        "correct_answers",
    ),
    "temps_moyen": (
        "temps_moyen_par_carte",
        "temps_moyen",
        "temps_moyen_carte",
        "moyenne",
        "average_time_per_card",
        "average_time",
        "avg_time",
        "avg_time_per_card",
        "average",
    ),
}

# ASCII operator signs, so the `famille` column stays typeable on any keyboard.
CSV_OP = {"addition": "+", "subtraction": "-", "multiplication": "x", "division": "/"}
JOINER = {"fr": " et ", "en": " and "}


def _key(name: str) -> str:
    """Fold a header cell to its comparison key: no accents, no case, no spaces."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    out = [c if c.isalnum() else "_" for c in s]
    return "".join(out).strip("_")


_ALIAS_TO_FIELD = {_key(a): f for f, names in ALIASES.items() for a in names}


# --------------------------------------------------------------------------- #
# Parsing / formatting
# --------------------------------------------------------------------------- #


def parse_duration(text: str) -> float | None:
    """Read a hand-written duration into seconds. See the header docs above.

    Handles the paper notations (`31"`, `2'10`, `1'23"4`), the clock notation
    (`1:23.4`), plain seconds (`83.4`) and the comma forms (`1,29`, `2,01,95`).
    """
    s = (text or "").strip().replace("″", '"').replace("’", "'").replace("′", "'")
    s = s.rstrip(" s\t")  # a trailing seconds letter carries no value
    if not s:
        return None

    # Split on every separator people actually write between minutes, seconds and
    # hundredths — `:` `'` `"` `,`. A `.` stays put: it is the decimal point.
    parts = [p.strip() for p in _split_any(s, ":'\",") if p.strip() != ""]
    if not parts:
        return None
    try:
        if len(parts) == 1:
            return float(parts[0].replace(",", "."))
        if len(parts) == 2:
            a, b = parts
            # `32,60` is 32.6 s (60 is not a legal seconds value) while `1,29`
            # is 1 min 29 s. Only a 2-digit value below 60 means "seconds".
            if "," in s and not any(c in s for c in ":'\""):
                if not (len(b) == 2 and b.isdigit() and int(b) < 60):
                    return float(s.replace(",", "."))
            return int(a or 0) * 60 + float(b)
        # three parts: minutes, seconds, hundredths — `2,01,95`
        m, sec, frac = parts[0], parts[1], parts[2]
        return int(m or 0) * 60 + int(sec) + float(f"0.{frac}")
    except ValueError:
        return None


def _split_any(s: str, seps: str) -> list[str]:
    out, cur = [], ""
    for ch in s:
        if ch in seps:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def fmt_duration(seconds: float | None, decimals: int = 1) -> str:
    """Canonical written form: `M:SS.s` — readable, and re-readable by us."""
    if seconds is None:
        return ""
    m, s = divmod(float(seconds), 60)
    width = 2 + (decimals + 1 if decimals else 0)
    return f"{int(m)}:{s:0{width}.{decimals}f}"


def parse_date(text: str) -> _date | None:
    """`2026-06-16`, `16/06/2026`, `16-06-26` or a bare `16/06`."""
    s = (text or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # Day/month only: pick the latest year that doesn't put the date in the future.
    for fmt in ("%d/%m", "%d-%m", "%d.%m"):
        try:
            d = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        today = _date.today()
        year = (
            today.year
            if (d.month, d.day) <= (today.month, today.day)
            else today.year - 1
        )
        try:
            return d.replace(year=year)
        except ValueError:  # 29/02 on a non-leap year
            return d.replace(year=year, day=28)
    return None


def parse_int(text: str) -> int | None:
    try:
        return int(float((text or "").strip().replace(",", ".")))
    except ValueError:
        return None


def parse_note(text: str) -> int | None:
    """The `note` cell: `15`, or the `15/20` a parent naturally writes.

    Only the numerator is kept — the denominator is the `cartes` cell, and the
    two would only ever disagree by accident.
    """
    return parse_int((text or "").split("/")[0])


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class Session:
    """One row of the log, already parsed. `raw_*` keeps what was typed, so the
    table can show a hand-written value exactly as the parent wrote it."""

    day: _date | None
    famille: str
    seconds: float | None
    cards: int | None
    avg_seconds: float | None
    correct: int | None = None
    raw_time: str = ""

    @property
    def avg(self) -> float | None:
        """Stored average, else derived from total / cards."""
        if self.avg_seconds is not None:
            return self.avg_seconds
        if self.seconds is not None and self.cards:
            return self.seconds / self.cards
        return None

    @property
    def note(self) -> tuple[int, int] | None:
        """`(right, asked)` — the grade, or None if this row never recorded one."""
        return grade([self])


def grade(sessions) -> tuple[int, int] | None:
    """The grade over a group of rows: an *average*, over one deck.

    A single row grades itself. Several rows — three tries in an afternoon —
    average their scores instead of adding them up: three sessions of 20 cards
    read `16/20`, not `47/60`. A grade only says something if it is out of the
    same thing every day, and `47/60` says "played a lot" rather than "did
    well".

    The deck it is written over is the one played most that day, the smaller on
    a tie — so a 20-card session followed by a 30-card one is still shown out of
    20, the shape the child actually sat in front of. Mixed decks are averaged
    as *ratios*, so a short session weighs exactly as much as a long one.

    Rows that recorded neither a score nor a card count are simply not part of
    the average, so a day that mixes a played session with a hand-typed paper
    row is graded on the session alone. None when nothing in the group carries
    a score.
    """
    scored = [
        (s.correct, s.cards) for s in sessions if s.correct is not None and s.cards
    ]
    if not scored:
        return None
    if len(scored) == 1:
        return scored[0]
    counts = Counter(n for _, n in scored)
    asked = min(counts, key=lambda n: (-counts[n], n))
    mean = sum(c / n for c, n in scored) / len(scored)
    # round half up, and never past the deck: a rounded 20/20 has to mean 20/20.
    return max(0, min(asked, int(mean * asked + 0.5))), asked


def fmt_note(note: tuple[int, int] | None) -> str:
    """`15/20`, the way it is written on the sheet. Empty when there is none."""
    return f"{note[0]}/{note[1]}" if note else ""


def months(sessions) -> list[tuple[int, int]]:
    """Every `(year, month)` the log has a dated row in, newest first.

    The progress screen's month filter is built from this, so a month only ever
    appears in the list once there is something to show in it.
    """
    return sorted({(s.day.year, s.day.month) for s in sessions if s.day}, reverse=True)


def in_month(session: Session, ym: tuple[int, int] | None) -> bool:
    """Is this row part of `(year, month)`? `None` means "every month", which is
    also the only view an undated row can show up in."""
    if ym is None:
        return True
    return session.day is not None and (session.day.year, session.day.month) == ym


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def _header_map(header: list[str]) -> dict[str, int]:
    """Column index for each field we recognize, whatever the language/order."""
    out: dict[str, int] = {}
    for i, cell in enumerate(header):
        field = _ALIAS_TO_FIELD.get(_key(cell))
        if field and field not in out:
            out[field] = i
    return out


def load_sessions(path: Path | None = None) -> list[Session]:
    """Every readable row, oldest first. Blank dates inherit the row above."""
    path = path or RESULTS_FILE
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
    except (OSError, csv.Error):
        return []
    if not rows:
        return []

    cols = _header_map(rows[0])
    body = rows[1:] if cols else rows
    if not cols:  # headerless file: assume canonical order
        cols = {f: i for i, f in enumerate(FIELDS)}

    def cell(row: list[str], field: str) -> str:
        i = cols.get(field, -1)
        return row[i].strip() if 0 <= i < len(row) else ""

    sessions: list[Session] = []
    last_day: _date | None = None
    for row in body:
        if not any(c.strip() for c in row):
            continue
        day = parse_date(cell(row, "date")) or last_day
        last_day = day
        raw_time = cell(row, "temps")
        seconds = parse_duration(raw_time)
        if seconds is None and not cell(row, "famille"):
            continue  # nothing usable on this line
        sessions.append(
            Session(
                day=day,
                famille=cell(row, "famille"),
                seconds=seconds,
                cards=parse_int(cell(row, "cartes")),
                avg_seconds=parse_duration(cell(row, "temps_moyen")),
                correct=parse_note(cell(row, "note")),
                raw_time=raw_time,
            )
        )
    # Oldest first; undated rows sink to the bottom but keep their file order.
    sessions.sort(key=lambda s: (s.day is None, s.day or _date.min))
    return sessions


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def famille_label(mode: str, numbers: set[int], lang: str = "fr") -> str:
    """The `famille` cell for a session: sign + the numbers it was played with.

    `-3`, `-1 et -2`, `x7`. A run of four or more consecutive numbers collapses
    to a range (`+1–10`) so the cell stays short when everything is switched on.
    """
    sign = CSV_OP.get(mode, "?")
    nums = sorted(numbers)
    if not nums:
        return sign
    if len(nums) >= 4 and nums == list(range(nums[0], nums[-1] + 1)):
        return f"{sign}{nums[0]}–{nums[-1]}"
    return JOINER.get(lang, " et ").join(f"{sign}{n}" for n in nums)


def _header_names(header: list[str], default: dict[str, str]) -> dict[str, str]:
    """The language dictionary the file is already written in.

    Used when adding a column to an existing file, so the new name matches the
    ones beside it rather than the app's current language. `date` is spelled the
    same in both, so a tie falls back to the app's own setting.
    """
    keys = {_key(c) for c in header}
    scores = {
        lang: sum(1 for n in names.values() if _key(n) in keys)
        for lang, names in HEADERS.items()
    }
    ranked = sorted(scores, key=lambda lang: -scores[lang])
    top, runner_up = ranked[0], ranked[1:]
    if runner_up and scores[top] == scores[runner_up[0]]:
        return default
    return HEADERS[top]


def _extend_header(path: Path, header: list[str], extra: list[str]) -> list[str]:
    """Add column names to the header line of an existing file.

    Only the first line is rewritten; every data line is copied through byte for
    byte, so hand-typed rows — including the ones we cannot even parse — survive
    untouched. New names go at the *end*: the old rows are then simply short by
    those cells, which reads as empty, where inserting mid-header would shift
    every value one column to the right.
    """
    try:
        # newline="" on both ends: the line endings the file came with are data
        # here, not something to translate.
        with path.open(newline="", encoding="utf-8-sig") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError):
        return header
    first, sep, rest = raw.partition("\n")
    grown = header + extra
    line = io.StringIO()
    csv.writer(line, lineterminator="").writerow(grown)
    try:
        with path.open("w", newline="", encoding="utf-8") as fh:
            fh.write(line.getvalue() + first[len(first.rstrip("\r")) :] + sep + rest)
    except OSError:
        return header
    return grown


def append_session(
    day: _date,
    famille: str,
    seconds: float,
    cards: int,
    correct: int | None = None,
    lang: str = "fr",
    path: Path | None = None,
) -> None:
    """Add one line at the bottom, matching whatever header the file already has.

    The file wins: if it was hand-edited into English, or its columns reordered,
    or extra columns added, the new row follows it. Only a missing file gets a
    fresh header, in the app's current language.
    """
    path = path or RESULTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    header: list[str] = []
    needs_eol = False
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            first = fh.readline()
        if first.strip():
            header = next(csv.reader([first]))
        with path.open("rb") as fh:  # keep our row on its own line
            fh.seek(0, 2)
            if fh.tell():
                fh.seek(-1, 2)
                needs_eol = fh.read(1) != b"\n"
    except OSError:
        pass

    names = HEADERS.get(lang, HEADERS["fr"])
    write_header = not header
    if write_header:
        header = [names[f] for f in FIELDS]
        cols = {f: i for i, f in enumerate(FIELDS)}
    else:
        cols = _header_map(header)
        names = _header_names(header, names)

    values = {
        "date": day.isoformat(),
        "famille": famille,
        "temps": fmt_duration(seconds),
        "cartes": str(cards),
        # the numerator only: the denominator is the `cartes` cell beside it.
        "note": "" if correct is None else str(correct),
        # plain seconds: a per-card average reads better as `4.8` than `0:04.8`
        # (and is read back just as happily).
        "temps_moyen": f"{seconds / cards:.1f}" if cards else "",
    }

    # A file written before a column existed has nowhere to put it: give it one
    # rather than dropping the value on the floor.
    missing = [f for f in FIELDS if f not in cols and values.get(f)]
    if missing and not write_header:
        header = _extend_header(path, header, [names[f] for f in missing])
        cols = _header_map(header)

    row = [""] * len(header)
    for field, i in cols.items():
        if i < len(row):
            row[i] = values.get(field, "")

    try:
        with path.open("a", newline="", encoding="utf-8") as fh:
            if needs_eol:
                fh.write("\n")
            w = csv.writer(fh)
            if write_header:
                w.writerow(header)
            w.writerow(row)
    except OSError:
        pass  # logging must never break the game
