"""The progress screen, on paper — chart + full table, exported to an A4 PDF.

No dependency: a PDF is a plain text container around a page-description
stream, and everything this report draws (filled rectangles, polylines, circles,
text in a standard font) is a handful of operators. So the file is written by
hand rather than pulling in a print library — the app stays "runs anywhere
Python has Tk".

Two halves:

* `Pdf` — the tiny engine. A4 pages, Helvetica / Helvetica-Bold, filled and
  stroked paths, text with real width measurement (so columns can be
  right-aligned and labels centered).
* `export_progress()` — the report layout: header, the same chart the screen
  draws, then every session in a table that flows onto as many pages as it
  needs.

The paper uses the app's live palette, so an export made in girly mode comes out
pink. The chart is not a screenshot: it is redrawn as vectors from the on-screen
`TimeChart`, borrowing that widget's day averages, y-scale and monotone-cubic
curve so the printed line is the same line, only sharper.

Text is encoded as WinAnsi (cp1252), which covers French and English; an accent
the encoding lacks falls back to its base letter, and anything left over (the
emoji in the screen titles) is dropped rather than printed as garbage.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from pathlib import Path

import results as resultslog

# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #

A4 = (595.28, 841.89)          # points, portrait

# Helvetica / Helvetica-Bold advance widths (units per 1000), characters 32..126.
# Straight from the Adobe core-font metrics: the base-14 fonts are guaranteed to
# be present in every reader, so no font has to be embedded.
_W_REGULAR = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    278, 278, 584, 584, 584, 556, 1015,
    667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667,
    778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    278, 278, 278, 469, 556, 333,
    556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556,
    556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
    334, 260, 334, 584,
)
_W_BOLD = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    333, 333, 584, 584, 584, 611, 975,
    722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667,
    778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    333, 278, 333, 584, 556, 333,
    556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611,
    611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
    389, 280, 389, 584,
)
# The punctuation the report actually reaches for outside ASCII. Same advance in
# both weights, so one table serves.
_W_EXTRA = {"—": 1000, "–": 556, "…": 1000, "·": 278, "°": 400, "€": 556,
            "’": 222, "“": 333, "”": 333, "«": 556, "»": 556}
_W_DEFAULT = 556


def _fold(text: str) -> str:
    """Down to what WinAnsi can print: keep what encodes, strip accents off what
    doesn't, drop the rest (emoji) rather than emit a replacement blob."""
    out = []
    for ch in text or "":
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            bare = "".join(c for c in unicodedata.normalize("NFKD", ch)
                           if not unicodedata.combining(c))
            try:
                bare.encode("cp1252")
            except UnicodeEncodeError:
                continue
            ch = bare
        out.append(ch)
    # Stripped, so a screen title whose emoji was just dropped ("📈 Mes progrès")
    # does not print with a hole where the icon used to be.
    return "".join(out).strip()


def _char_width(ch: str, bold: bool) -> int:
    o = ord(ch)
    if 32 <= o <= 126:
        return (_W_BOLD if bold else _W_REGULAR)[o - 32]
    if ch in _W_EXTRA:
        return _W_EXTRA[ch]
    # An accented letter advances exactly like the letter it is built on.
    bare = "".join(c for c in unicodedata.normalize("NFKD", ch)
                   if not unicodedata.combining(c))
    if len(bare) == 1 and 32 <= ord(bare) <= 126:
        return (_W_BOLD if bold else _W_REGULAR)[ord(bare) - 32]
    return _W_DEFAULT


def measure(text: str, size: float, bold: bool = False) -> float:
    """Width of `text` in points — the same number the reader will lay out."""
    return sum(_char_width(c, bold) for c in _fold(text)) / 1000.0 * size


def _n(v: float) -> str:
    """A coordinate, short: PDF wants numbers, not float repr noise."""
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _rgb(color: str) -> tuple[float, float, float]:
    c = (color or "#000000").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _escape(text: str) -> str:
    """A PDF literal string, all-ASCII: high WinAnsi bytes go out as octal."""
    out = []
    for byte in _fold(text).encode("cp1252", "ignore"):
        ch = chr(byte)
        if ch in "()\\":
            out.append("\\" + ch)
        elif 32 <= byte <= 126:
            out.append(ch)
        else:
            out.append(f"\\{byte:03o}")
    return "".join(out)


class Pdf:
    """A few drawing primitives over a list of page content streams."""

    def __init__(self, size: tuple[float, float] = A4):
        self.w, self.h = size
        self.pages: list[list[str]] = []
        self._ops: list[str] = []

    # -- pages --------------------------------------------------------- #
    def new_page(self) -> None:
        self._ops = []
        self.pages.append(self._ops)

    def select_page(self, index: int) -> None:
        """Draw onto an earlier page again — used for the footers, which can
        only be numbered once the last row has told us how many pages there are."""
        self._ops = self.pages[index]

    # -- paint --------------------------------------------------------- #
    def rect(self, x, y, w, h, fill) -> None:
        r, g, b = _rgb(fill)
        self._ops.append(f"{_n(r)} {_n(g)} {_n(b)} rg "
                         f"{_n(x)} {_n(y)} {_n(w)} {_n(h)} re f")

    def line(self, x0, y0, x1, y1, color, width=0.6) -> None:
        self.polyline([x0, y0, x1, y1], color, width)

    def polyline(self, coords, color, width=1.0) -> None:
        if len(coords) < 4:
            return
        r, g, b = _rgb(color)
        parts = [f"{_n(r)} {_n(g)} {_n(b)} RG {_n(width)} w 1 J 1 j",
                 f"{_n(coords[0])} {_n(coords[1])} m"]
        for i in range(2, len(coords), 2):
            parts.append(f"{_n(coords[i])} {_n(coords[i + 1])} l")
        parts.append("S")
        self._ops.append(" ".join(parts))

    def polygon(self, coords, fill) -> None:
        if len(coords) < 6:
            return
        r, g, b = _rgb(fill)
        parts = [f"{_n(r)} {_n(g)} {_n(b)} rg",
                 f"{_n(coords[0])} {_n(coords[1])} m"]
        for i in range(2, len(coords), 2):
            parts.append(f"{_n(coords[i])} {_n(coords[i + 1])} l")
        parts.append("h f")
        self._ops.append(" ".join(parts))

    def round_rect(self, x, y, w, h, r, fill, outline=None, width=1.0) -> None:
        """A filled rectangle with rounded corners — the note tags on the chart.
        Each corner is one bezier, the same 0.5523 pull `circle` uses."""
        r = min(r, w / 2, h / 2)
        k = r * 0.5523
        x1, y1 = x + w, y + h
        path = (f"{_n(x + r)} {_n(y)} m {_n(x1 - r)} {_n(y)} l "
                f"{_n(x1 - r + k)} {_n(y)} {_n(x1)} {_n(y + r - k)} {_n(x1)} {_n(y + r)} c "
                f"{_n(x1)} {_n(y1 - r)} l "
                f"{_n(x1)} {_n(y1 - r + k)} {_n(x1 - r + k)} {_n(y1)} {_n(x1 - r)} {_n(y1)} c "
                f"{_n(x + r)} {_n(y1)} l "
                f"{_n(x + r - k)} {_n(y1)} {_n(x)} {_n(y1 - r + k)} {_n(x)} {_n(y1 - r)} c "
                f"{_n(x)} {_n(y + r)} l "
                f"{_n(x)} {_n(y + r - k)} {_n(x + r - k)} {_n(y)} {_n(x + r)} {_n(y)} c h")
        fr, fg, fb = _rgb(fill)
        head = f"{_n(fr)} {_n(fg)} {_n(fb)} rg"
        if outline:
            orr, og, ob = _rgb(outline)
            self._ops.append(f"{head} {_n(orr)} {_n(og)} {_n(ob)} RG {_n(width)} w "
                             f"{path} B")
        else:
            self._ops.append(f"{head} {path} f")

    def circle(self, cx, cy, r, fill, outline=None, width=1.0) -> None:
        k = r * 0.5523                       # bezier arc, close enough to round
        path = (f"{_n(cx + r)} {_n(cy)} m "
                f"{_n(cx + r)} {_n(cy + k)} {_n(cx + k)} {_n(cy + r)} {_n(cx)} {_n(cy + r)} c "
                f"{_n(cx - k)} {_n(cy + r)} {_n(cx - r)} {_n(cy + k)} {_n(cx - r)} {_n(cy)} c "
                f"{_n(cx - r)} {_n(cy - k)} {_n(cx - k)} {_n(cy - r)} {_n(cx)} {_n(cy - r)} c "
                f"{_n(cx + k)} {_n(cy - r)} {_n(cx + r)} {_n(cy - k)} {_n(cx + r)} {_n(cy)} c h")
        fr, fg, fb = _rgb(fill)
        head = f"{_n(fr)} {_n(fg)} {_n(fb)} rg"
        if outline:
            orr, og, ob = _rgb(outline)
            head += f" {_n(orr)} {_n(og)} {_n(ob)} RG {_n(width)} w"
            self._ops.append(f"{head} {path} B")
        else:
            self._ops.append(f"{head} {path} f")

    def text(self, x, y, s, size, color, bold=False, anchor="w") -> float:
        """Draw `s` with its baseline at y. `anchor` places x: w / e / center.
        Returns the width drawn, so a caller can chain."""
        s = _fold(s)
        if not s:
            return 0.0
        w = measure(s, size, bold)
        if anchor == "e":
            x -= w
        elif anchor in ("center", "c"):
            x -= w / 2
        r, g, b = _rgb(color)
        self._ops.append(f"BT {_n(r)} {_n(g)} {_n(b)} rg /{'F2' if bold else 'F1'} "
                         f"{_n(size)} Tf {_n(x)} {_n(y)} Td ({_escape(s)}) Tj ET")
        return w

    def clip_text(self, x, y, s, size, color, max_w, bold=False, anchor="w") -> None:
        """Same, but shortened with an ellipsis if it would not fit in `max_w`."""
        s = _fold(s)
        if measure(s, size, bold) > max_w:
            while s and measure(s + "…", size, bold) > max_w:
                s = s[:-1]
            s = (s.rstrip() + "…") if s else ""
        self.text(x, y, s, size, color, bold, anchor)

    # -- output -------------------------------------------------------- #
    def save(self, path: Path) -> None:
        objects: list[bytes] = []            # object 1 is objects[0]

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        # 1 catalog, 2 page tree, 3-4 fonts, then two objects per page.
        n_pages = len(self.pages)
        page_ids = [5 + 2 * i for i in range(n_pages)]
        add(b"<< /Type /Catalog /Pages 2 0 R >>")
        add(("<< /Type /Pages /Count %d /Kids [%s] >>"
             % (n_pages, " ".join(f"{i} 0 R" for i in page_ids))).encode("ascii"))
        for name in ("Helvetica", "Helvetica-Bold"):
            add(("<< /Type /Font /Subtype /Type1 /BaseFont /%s "
                 "/Encoding /WinAnsiEncoding >>" % name).encode("ascii"))
        for i, ops in enumerate(self.pages):
            stream = "\n".join(ops).encode("latin-1", "replace")
            add(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
                 "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                 "/Contents %d 0 R >>" % (_n(self.w), _n(self.h), page_ids[i] + 1)
                 ).encode("ascii"))
            add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n" % (len(objects) + 1)
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (len(objects) + 1, xref))

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(out))


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #

MARGIN = 40.0
CHART_H = 200.0
ROW_H = 17.0
FS_TITLE, FS_META, FS_AXIS, FS_ROW, FS_FOOT, FS_NOTE = 20, 9.5, 8, 9, 7.5, 7.5
NOTE_H, NOTE_GAP = 12.0, 7.0   # a note tag on the chart, and its clearance

# Same weights as the on-screen table, so the columns line up with the app.
COLUMNS = (("date", "w", 3), ("famille", "w", 4), ("temps", "e", 3),
           ("cartes", "e", 2), ("note", "e", 2), ("temps_moyen", "e", 4))


def default_filename(lang: str = "fr", when: datetime | None = None,
                     month: tuple[int, int] | None = None) -> str:
    """`almathus-progres-2026-07-28.pdf`, or `almathus-progres-2026-07.pdf` for a
    single month: a monthly report is named by the month it covers, not by the
    day it happened to be printed on."""
    when = when or datetime.now()
    stem = "progres" if lang == "fr" else "progress"
    if month:
        return f"almathus-{stem}-{month[0]:04d}-{month[1]:02d}.pdf"
    return f"almathus-{stem}-{when:%Y-%m-%d}.pdf"


def export_progress(path, sessions, chart, palette, labels, when=None) -> Path:
    """Write the progress report and return the path it landed on.

    `chart` is the live `TimeChart` (or None): we take its per-day averages and
    reuse its `_y_scale` / `_curve` so paper and screen agree to the pixel.
    `palette` is the theme dict; `labels` carries every already-translated
    string (`title`, `subtitle`, `chart_title`, `n_sessions`, `footer`, plus one
    per column key). `labels["period"]` is optional: the month the report covers,
    printed on every page — leave it out (or empty) for the whole history.
    """
    path = Path(path)
    when = when or datetime.now()
    pdf = Pdf()
    inner = pdf.w - 2 * MARGIN

    def start_page() -> float:
        """Open a page, paint the background and the title block, return the y
        the content may start at."""
        pdf.new_page()
        pdf.rect(0, 0, pdf.w, pdf.h, palette["BG"])
        y = pdf.h - MARGIN - FS_TITLE
        pdf.text(MARGIN, y, labels["title"], FS_TITLE, palette["ACCENT"], bold=True)
        pdf.text(pdf.w - MARGIN, y + 3, labels["n_sessions"], FS_META, palette["MUTED"],
                 anchor="e")
        y -= FS_META + 6
        pdf.text(MARGIN, y, labels["subtitle"], FS_META, palette["MUTED"])
        # The period sits opposite the date it was printed: same line, the accent
        # colour, so a monthly report says which month it is at a glance.
        if labels.get("period"):
            pdf.text(pdf.w - MARGIN, y, labels["period"], FS_META, palette["ACCENT"],
                     bold=True, anchor="e")
        y -= 12
        pdf.line(MARGIN, y, pdf.w - MARGIN, y, palette["BORDER"])
        return y - 22

    y = start_page()

    if chart is not None and getattr(chart, "days", None):
        pdf.text(MARGIN, y, labels["chart_title"], FS_META, palette["MUTED"])
        y -= 8
        _draw_chart(pdf, chart, MARGIN, y - CHART_H, inner, CHART_H, palette)
        y -= CHART_H + 24

    # -- the table ------------------------------------------------------ #
    total_weight = sum(w for _, _, w in COLUMNS)
    edges, x = [], MARGIN
    for _, _, weight in COLUMNS:
        width = inner * weight / total_weight
        edges.append((x, width))
        x += width
    pad = 8.0
    bottom = MARGIN + 16                     # leave the footer its band

    def head(y_top: float) -> float:
        for (x0, width), (key, align, _) in zip(edges, COLUMNS):
            tx = x0 + pad if align == "w" else x0 + width - pad
            pdf.text(tx, y_top - FS_ROW, labels[key], FS_ROW, palette["MUTED"],
                     anchor="w" if align == "w" else "e")
        y_line = y_top - FS_ROW - 7
        pdf.line(MARGIN, y_line, pdf.w - MARGIN, y_line, palette["BORDER"])
        return y_line - 4

    y = head(y)

    # Newest first, exactly like the screen: the last session read first.
    for i, s in enumerate(reversed(sessions)):
        if y - ROW_H < bottom:
            y = head(start_page())
        pdf.rect(MARGIN, y - ROW_H, inner, ROW_H,
                 palette["SURFACE"] if i % 2 else palette["CARD_BG"])
        cells = (
            (s.day.strftime("%d/%m/%Y") if s.day else "—", palette["TEXT"]),
            (s.famille or "—", palette["TEXT"]),
            (resultslog.fmt_duration(s.seconds) if s.seconds is not None
             else (s.raw_time or "—"), palette["ACCENT"]),
            (str(s.cards) if s.cards else "—", palette["MUTED"]),
            (resultslog.fmt_note(s.note) or "—", palette["ACCENT"]),
            (f"{s.avg:.1f} s" if s.avg is not None else "—", palette["GOOD"]),
        )
        baseline = y - ROW_H + (ROW_H - FS_ROW) / 2 + 1.5
        for (text, color), (x0, width), (_, align, _w) in zip(cells, edges, COLUMNS):
            color = palette["MUTED"] if text == "—" else color   # a blank is not a value
            tx = x0 + pad if align == "w" else x0 + width - pad
            pdf.clip_text(tx, baseline, text, FS_ROW, color, width - 2 * pad,
                          anchor="w" if align == "w" else "e")
        y -= ROW_H

    total_pages = len(pdf.pages)
    for i in range(total_pages):             # footers last: the count is known now
        pdf.select_page(i)
        pdf.text(pdf.w / 2, MARGIN - 4,
                 labels["footer"].format(page=i + 1, total=total_pages),
                 FS_FOOT, palette["MUTED"], anchor="center")

    pdf.save(path)
    return path


def _draw_chart(pdf: Pdf, chart, x, y, w, h, palette) -> None:
    """The screen chart as vectors. PDF's y grows upward, so the scale is
    flipped relative to the widget — everything else is the widget's own maths."""
    days = chart.days
    step, top = chart._y_scale(max(mean for _, mean, _ in days))

    y_labels = [resultslog.fmt_duration(v, 0)
                for v in _frange(0, top, step)]
    pad_l = max(measure(t, FS_AXIS) for t in y_labels) + 12
    has_notes = any(resultslog.grade(g) for _, _, g in days)
    pad_r, pad_b = 10.0, FS_AXIS + 12
    pad_t = (NOTE_H + NOTE_GAP + 4) if has_notes else 10.0   # headroom for a tag
    x0, x1 = x + pad_l, x + w - pad_r
    y0, y1 = y + pad_b, y + h - pad_t      # y0 is the zero baseline, y1 the top

    def Y(v):
        return y0 + (v / top) * (y1 - y0)

    for v in _frange(0, top, step):
        gy = Y(v)
        pdf.line(x0, gy, x1, gy, palette["BORDER"], 0.5)
        pdf.text(x0 - 8, gy - FS_AXIS * 0.35, resultslog.fmt_duration(v, 0),
                 FS_AXIS, palette["MUTED"], anchor="e")

    ords = [d.toordinal() for d, _, _ in days]
    lo, span = min(ords), max(ords) - min(ords)

    def X(o):
        return (x0 + x1) / 2 if not span else x0 + (o - lo) / span * (x1 - x0)

    pts = [(X(day.toordinal()), Y(mean), entry) for entry in days
           for day, mean, _ in (entry,)]
    if len(pts) > 1:
        pdf.polyline(type(chart)._curve(pts), palette["ACCENT"], 1.6)
    for px, py, _ in pts:
        pdf.circle(px, py, 2.6, palette["ACCENT"], outline=palette["BG"], width=1.2)
    _draw_notes(pdf, pts, x + 2, x + w - 2, y + h - NOTE_H - 2, palette)

    # Date labels thinned by pixel distance, like the widget: the axis is
    # calendar time, so neighbouring days are not neighbouring columns.
    gap = measure("00/00", FS_AXIS) + 16
    kept: list[tuple[object, float]] = []
    for d, _, _ in days:
        xd = X(d.toordinal())
        if not kept or xd - kept[-1][1] >= gap:
            kept.append((d, xd))
    last = days[-1][0]
    if kept and kept[-1][0] != last:          # the newest date always gets a label
        xd = X(last.toordinal())
        while kept and xd - kept[-1][1] < gap:
            kept.pop()
        kept.append((last, xd))
    for d, xd in kept:
        pdf.text(xd, y0 - FS_AXIS - 4, d.strftime("%d/%m"), FS_AXIS,
                 palette["MUTED"], anchor="center")


def _draw_notes(pdf: Pdf, pts, x_min, x_max, y_max, palette) -> None:
    """The screen's note tags, on paper: `15/20` over the dot it grades.

    Same rules as the widget — centred over the dot, stepping up out of the way
    of a tag already placed, tail traded for a stem once it has moved.
    """
    placed: list[tuple[float, float, float]] = []
    for px, py, (_, _, group) in pts:
        text = resultslog.fmt_note(resultslog.grade(group))
        if not text:
            continue
        bw = measure(text, FS_NOTE) + 10
        bx = min(max(px - bw / 2, x_min), max(x_min, x_max - bw))
        home = py + NOTE_GAP
        by = home
        while any(bx < ox + ow and ox < bx + bw and abs(by - oy) < NOTE_H + 2
                  for ox, oy, ow in placed) and by + NOTE_H + 3 <= y_max:
            by += NOTE_H + 3
        by = min(by, y_max)
        placed.append((bx, by, bw))

        if by == home and abs(bx + bw / 2 - px) < 0.5:
            pdf.polygon([px - 4, by + 1, px + 4, by + 1, px, py + 4],
                        palette["ACCENT"])
        else:
            pdf.line(bx + bw / 2, by, px, py + 4, palette["ACCENT"], 0.8)
        pdf.round_rect(bx, by, bw, NOTE_H, 3.5, palette["ACCENT"],
                       outline=palette["BG"], width=0.8)
        pdf.text(bx + bw / 2, by + (NOTE_H - FS_NOTE) / 2 + 1.5, text, FS_NOTE,
                 palette["BG"], anchor="center")


def _frange(start: float, stop: float, step: float) -> list[float]:
    out, v = [], float(start)
    while v <= stop + 1e-9:
        out.append(v)
        v += step
    return out
