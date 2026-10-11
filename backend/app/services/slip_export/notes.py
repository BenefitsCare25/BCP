"""Captured slip wording on the exported slip: terms and free-standing sections.

The upload keeps every ``Label : value`` term and every titled block the
structured parsers don't own (``answers["terms"]`` / ``answers["sections"]``).
The export puts them back where the slip printed them, so a re-issued slip says
everything the placed one said — configured values still win where both exist.
A quotation slip withholds the incumbent's commercial terms
(``QUOTATION_COMMERCIAL_TERMS``) and keeps the benefit-defining wording.
"""
from __future__ import annotations

import re
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from app.services.slip_export.styles import (
    HEADER,
    MIDDLE_WRAP,
    SECTION,
    border_row,
    spacer_row,
    style_row,
)

# The Annual Premium line is a figure, not a term: its label's qualifier
# ("sbj to GST") is used by the Rate section instead.
_PREMIUM_LABEL = re.compile(r"^\s*annual\s+premium\b", re.IGNORECASE)
_QUALIFIER = re.compile(r"\(([^)]+)\)")

QUOTATION_COMMERCIAL_TERMS: tuple[str, ...] = (
    "experience refund",
    "loss ratio",
    "profit commission",
    "discount",
    "package discount",
    "minimum premium",
    "commission",
    "brokerage",
    "rated together",
    "rate guarantee",
)
"""Commercial terms a quotation slip must not repeat from the placed slip.

A quotation goes to insurers competing for the business, so the incumbent's
negotiated commercials (refund formula, loss ratio, discounts, minimum premium,
commission, rating groups, rate guarantee) are theirs to quote, not ours to
disclose. A term or section whose label contains any of these phrases as whole
words (the last one optionally plural) keeps its label on a quotation but goes
out without its value or body.
Benefit-defining wording (Non Evidence Limit, limits, eligibility, waiting
periods) is not listed and still reaches every quoting insurer. Placement slips
print every captured term unchanged.
"""


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


_COMMERCIAL_PHRASES: tuple[tuple[str, ...], ...] = tuple(
    tuple(_words(term)) for term in QUOTATION_COMMERCIAL_TERMS
)


def is_commercial_term(label: str) -> bool:
    """Whether ``label`` names a quotation-blanked commercial term
    (``QUOTATION_COMMERCIAL_TERMS``), matched on whole words in order. The last
    word may be plural ("Package Discounts", "Commissions")."""
    words = _words(label)
    return any(
        words[i : i + len(phrase) - 1] == list(phrase[:-1])
        and words[i + len(phrase) - 1] in (phrase[-1], f"{phrase[-1]}s")
        for phrase in _COMMERCIAL_PHRASES
        for i in range(len(words) - len(phrase) + 1)
    )


def captured_terms(answers: dict[str, Any]) -> list[tuple[str, str]]:
    """``(label, value)`` terms the upload captured, excluding the premium line."""
    out: list[tuple[str, str]] = []
    for term in answers.get("terms") or []:
        if not isinstance(term, dict):
            continue
        label = str(term.get("label") or "").strip()
        value = str(term.get("value") or "").strip()
        if label and value and not _PREMIUM_LABEL.match(label):
            out.append((label, value))
    return out


def premium_qualifier(answers: dict[str, Any]) -> str | None:
    """The slip's own wording for its premium ("sbj to GST"), if it had one."""
    for term in answers.get("terms") or []:
        label = str((term or {}).get("label") or "") if isinstance(term, dict) else ""
        if _PREMIUM_LABEL.match(label) and (m := _QUALIFIER.search(label)):
            return m.group(1).strip() or None
    return None


def _same_term(ladder_label: str, captured_label: str) -> bool:
    """A captured label names a ladder row when its words lead that row's words
    ("Experience Refund" → "Experience Refund Formula / Maximum Loss Ratio")."""
    ladder, captured = _words(ladder_label), _words(captured_label)
    if not captured or not ladder:
        return False
    shorter, longer = sorted((captured, ladder), key=len)
    return longer[: len(shorter)] == shorter


def merge_terms(
    ladder: list[tuple[str, str]],
    answers: dict[str, Any],
    *,
    quotation: bool = False,
) -> list[tuple[str, str]]:
    """The terms ladder with captured values filled in and extras appended.

    A configured value always wins; a captured one only fills a blank row.
    Captured terms matching no ladder row follow it, in slip order. On a
    quotation every commercial term (``QUOTATION_COMMERCIAL_TERMS``) keeps its
    label but prints blank, for the quoting insurer to fill in.
    """
    rows = list(ladder)
    for label, value in captured_terms(answers):
        if quotation and is_commercial_term(label):
            value = ""
        index = next(
            (i for i, (ladder_label, _) in enumerate(rows) if _same_term(ladder_label, label)),
            None,
        )
        if index is None:
            rows.append((f"{label} :", value))
        elif not rows[index][1]:
            rows[index] = (rows[index][0], value)
    if quotation:
        # A ladder row is blanked by its own label too: a non-commercial
        # captured label can still lead a commercial ladder row's words.
        rows = [
            (label, "" if is_commercial_term(label) else value) for label, value in rows
        ]
    return rows


def write_sections(
    ws: Worksheet,
    answers: dict[str, Any],
    placement: str,
    *,
    quotation: bool = False,
) -> None:
    """Render the captured sections printed at ``placement`` ("basis" /
    "after_sob"), each as its title and a bordered, column-aligned table.

    A quotation leaves out sections titled after a commercial term
    (``QUOTATION_COMMERCIAL_TERMS``); benefit wording still prints.
    """
    for section in answers.get("sections") or []:
        if not isinstance(section, dict) or section.get("placement") != placement:
            continue
        rows = [
            [str(cell or "") for cell in row]
            for row in section.get("rows") or []
            if isinstance(row, list) and any(str(cell or "").strip() for cell in row)
        ]
        title = str(section.get("title") or "").strip()
        if not rows and not title:
            continue
        if quotation and is_commercial_term(title):
            continue
        spacer_row(ws)
        if title:
            ws.append([f"{title} :"])
            style_row(ws, font=SECTION)
        width = max((len(r) for r in rows), default=0)
        for index, row in enumerate(rows):
            ws.append(["", *row])
            r = style_row(ws, font=HEADER if index == 0 and not title else None)
            for col in range(2, width + 2):
                ws.cell(row=r, column=col).alignment = MIDDLE_WRAP
            border_row(ws, 2, width + 1)
