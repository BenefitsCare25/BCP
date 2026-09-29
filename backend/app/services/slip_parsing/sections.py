"""Terms and free-standing sections — the slip content no structured parser owns.

Two shapes are captured, both positionally rather than by wording, so a slip
template the platform has never seen keeps its text:

* **Terms** — ``Label : value`` rows between the Rate section and the Schedule
  of Benefits (Experience Refund, Policyholder(s) Rated Together, Discount, the
  Annual Premium line with its GST qualifier…).
* **Sections** — blank-row-separated blocks in the gap after the Basis-of-Cover
  table (FLEX options, dependant enrolment rules) and after the benefit
  schedule (Endorsements, Additional Arrangements).

Both are kept verbatim; nothing here interprets them. Interpreting is the job of
the structured parsers, and what they don't claim must still reach the setup
form and the exported slip instead of vanishing.
"""
from __future__ import annotations

import re

from app.services.excel_reader import Cell
from app.services.slip_parsing.models import SlipSection, SlipTerm
from app.services.slip_parsing.text import _non_empty, _norm

# Bounds on what one captured block may hold — a malformed sheet must not turn
# a stray region into a thousand-row "section".
_MAX_SECTION_ROWS = 60
_MAX_SECTION_COLS = 12
_MAX_CELL_CHARS = 2000

# A term label ends in a colon ("Experience Refund:", "Discount :"), optionally
# with its value in the same cell ("Discount : NA").
_TERM_LABEL = re.compile(r"^(?P<label>[^:]{2,120}?)\s*:\s*(?P<value>.*)$", re.DOTALL)

# Section labels the structured parsers already own — never re-captured as terms.
_OWNED_LABELS = ("rate", "cover", "basis of cover", "schedule of benefits")

_DISCLAIMER = re.compile(r"^\*|figures above are for", re.IGNORECASE)


def _text(value: Cell) -> str:
    """Cell text as printed: integral floats lose the xlrd ".0" artifact."""
    if isinstance(value, float):
        # Binary float noise (112770.53059999998) is not what the slip printed.
        value = round(value, 6)
        return str(int(value)) if value.is_integer() else str(value)
    return _norm(value)[:_MAX_CELL_CHARS]


def _cells(row: list[Cell] | None) -> list[tuple[int, str]]:
    return [(c, _text(v)) for c, v in enumerate(row or []) if _non_empty(v)]


def extract_terms(rows: list[list[Cell]], start: int, end: int) -> tuple[SlipTerm, ...]:
    """``Label : value`` rows in ``rows[start:end]``, in slip order.

    The label must lead the row (column A or B) — a colon deep inside a
    sentence is prose, not a term.
    """
    out: list[SlipTerm] = []
    for i in range(max(start, 0), min(end, len(rows))):
        cells = _cells(rows[i])
        if not cells or cells[0][0] > 1:
            continue
        m = _TERM_LABEL.match(cells[0][1])
        if m is None:
            continue
        label = m.group("label").strip()
        if label.lower().startswith(_OWNED_LABELS):
            continue
        parts = [m.group("value").strip()] + [text for _, text in cells[1:]]
        value = " ".join(p for p in parts if p).strip()
        if value:
            out.append(SlipTerm(label=label, value=value, source_row=i + 1))
    return tuple(out)


def _blocks(
    rows: list[list[Cell]], start: int, end: int, exclude: frozenset[int]
) -> list[list[int]]:
    """Row indexes of each blank-row-separated block in ``rows[start:end]``,
    skipping rows in ``exclude`` (already kept elsewhere, e.g. as terms)."""
    blocks: list[list[int]] = []
    current: list[int] = []
    for i in range(max(start, 0), min(end, len(rows))):
        if i in exclude:
            continue
        cells = _cells(rows[i])
        if not cells:
            if current:
                blocks.append(current)
                current = []
            continue
        if _DISCLAIMER.search(cells[0][1]):
            continue
        current.append(i)
    if current:
        blocks.append(current)
    return blocks


def extract_sections(
    rows: list[list[Cell]],
    start: int,
    end: int,
    placement: str,
    *,
    exclude: frozenset[int] = frozenset(),
) -> tuple[SlipSection, ...]:
    """Every titled block in ``rows[start:end]``, kept column-aligned.

    A block whose first row is a single cell uses it as the title ("FLEX
    Option", "Endorsements:"); otherwise the first row is the block's own column
    header and the title stays empty.
    """
    out: list[SlipSection] = []
    for block in _blocks(rows, start, end, exclude):
        first = _cells(rows[block[0]])
        title = ""
        body = block
        if len(first) == 1:
            title = first[0][1].rstrip(" :")
            body = block[1:]
        used = [c for i in body for c, _ in _cells(rows[i])]
        if not used and not title:
            continue
        lo = min(used) if used else 0
        hi = min(max(used) if used else 0, lo + _MAX_SECTION_COLS - 1)
        table: list[tuple[str, ...]] = []
        for i in body[:_MAX_SECTION_ROWS]:
            row = rows[i] or []
            cells = tuple(
                _text(row[c]) if c < len(row) and _non_empty(row[c]) else ""
                for c in range(lo, hi + 1)
            )
            # Trailing blanks carry no position information.
            while cells and not cells[-1]:
                cells = cells[:-1]
            if cells:
                table.append(cells)
        if len(body) > _MAX_SECTION_ROWS:
            # Say the block was cut rather than exporting it as if complete.
            table.append(
                (f"… {len(body) - _MAX_SECTION_ROWS} more row(s) — see the source slip, "
                 f"row {body[_MAX_SECTION_ROWS] + 1} onward",)
            )
        out.append(
            SlipSection(
                title=title,
                placement=placement,
                rows=tuple(table),
                source_row=block[0] + 1,
            )
        )
    return tuple(out)
