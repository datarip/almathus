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

    date,famille,temps,cartes,temps_moyen_par_carte

Canonical English header (written when LANG = "en"):

    date,family,time,cards,average_time_per_card

| field         | fr header               | en header              | meaning                                   |
|---------------|-------------------------|------------------------|-------------------------------------------|
| `date`        | `date`                  | `date`                 | day the session was played                |
| `famille`     | `famille`               | `family`               | which operation family was drilled ("-3") |
| `temps`       | `temps`                 | `time`                 | total time of the whole session           |
| `cartes`      | `cartes`                | `cards`                | how many cards were asked                 |
| `temps_moyen` | `temps_moyen_par_carte` | `average_time_per_card`| `temps / cartes`                          |

Either language is accepted on read, in any column order, and header matching
ignores case, accents, spaces and underscores — so `Temps`, `temps`,
`Temps moyen par carte` and `temps_moyen` all land on the right field. A handful
of synonyms are accepted too (see ALIASES). Columns the app doesn't know are
kept in place and left empty when it appends.

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
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_FILE = RESULTS_DIR / "results.csv"

# The five logical fields, in canonical order. `temps_moyen` is derived but kept
# as a real column so it can be hand-filled for rows with no card count.
FIELDS = ("date", "famille", "temps", "cartes", "temps_moyen")

HEADERS = {
    "fr": {
        "date": "date",
        "famille": "famille",
        "temps": "temps",
        "cartes": "cartes",
        "temps_moyen": "temps_moyen_par_carte",
    },
    "en": {
        "date": "date",
        "famille": "family",
        "temps": "time",
        "cartes": "cards",
        "temps_moyen": "average_time_per_card",
    },
}

# Every spelling we accept for each field, already normalized by `_key`.
ALIASES = {
    "date": ("date", "jour", "day"),
    "famille": ("famille", "family", "familles", "families"),
    "temps": ("temps", "time", "duree", "duration", "temps_total", "total_time"),
    "cartes": ("cartes", "cards", "nb_cartes", "n_cartes", "n_cards", "card_count",
               "nombre_de_cartes"),
    "temps_moyen": ("temps_moyen_par_carte", "temps_moyen", "temps_moyen_carte",
                    "moyenne", "average_time_per_card", "average_time", "avg_time",
                    "avg_time_per_card", "average"),
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
    s = s.rstrip(" s\t")                 # a trailing seconds letter carries no value
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
        year = today.year if (d.month, d.day) <= (today.month, today.day) else today.year - 1
        try:
            return d.replace(year=year)
        except ValueError:                     # 29/02 on a non-leap year
            return d.replace(year=year, day=28)
    return None


def parse_int(text: str) -> int | None:
    try:
        return int(float((text or "").strip().replace(",", ".")))
    except ValueError:
        return None


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
    raw_time: str = ""

    @property
    def avg(self) -> float | None:
        """Stored average, else derived from total / cards."""
        if self.avg_seconds is not None:
            return self.avg_seconds
        if self.seconds is not None and self.cards:
            return self.seconds / self.cards
        return None


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
    if not cols:                              # headerless file: assume canonical order
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
            continue                          # nothing usable on this line
        sessions.append(
            Session(
                day=day,
                famille=cell(row, "famille"),
                seconds=seconds,
                cards=parse_int(cell(row, "cartes")),
                avg_seconds=parse_duration(cell(row, "temps_moyen")),
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


def append_session(day: _date, famille: str, seconds: float, cards: int,
                   lang: str = "fr", path: Path | None = None) -> None:
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
        with path.open("rb") as fh:                # keep our row on its own line
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

    values = {
        "date": day.isoformat(),
        "famille": famille,
        "temps": fmt_duration(seconds),
        "cartes": str(cards),
        # plain seconds: a per-card average reads better as `4.8` than `0:04.8`
        # (and is read back just as happily).
        "temps_moyen": f"{seconds / cards:.1f}" if cards else "",
    }
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
