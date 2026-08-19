"""Extract year-labelled endorsement notes from placement-slip sheets."""
from __future__ import annotations

import re

from app.services.excel_reader import Sheet
from app.services.slip_parsing.models import ExtractedEndorsement
from app.services.slip_parsing.text import _norm

_END_NO_RE = re.compile(
    r"\b((?:19|20)\d{2})\s+(.+?ENDORSEMENT\s+NO\.?\s*\d+)\b",
    re.I,
)
_YEAR_LINE_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*[-\u2013]\s*(.+?)\s*$", re.I)
_SECOND_OPINION_RE = re.compile(r"\bsecond\s+medical\s+opinion\b", re.I)
_PIL_RE = re.compile(r"\b(prolonged\s+illness|PIL)\b", re.I)


def _cell(rows: list[list[object]], row: int, col: int) -> str:
    if row < 0 or col < 0 or row >= len(rows) or col >= len(rows[row]):
        return ""
    return _norm(rows[row][col])


def _source_cell(row: int, col: int) -> str:
    letters = ""
    n = col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row + 1}"


def _split_author(comment: str, fallback_author: str | None) -> tuple[str | None, str]:
    lines = [line.strip() for line in comment.splitlines() if line.strip()]
    if not lines:
        return fallback_author, comment.strip()
    first = lines[0]
    if ":" not in first:
        return fallback_author, "\n".join(lines).strip()
    author, rest = first.split(":", 1)
    body = [rest.strip()] if rest.strip() else []
    body.extend(lines[1:])
    return author.strip() or fallback_author, "\n".join(body).strip()


def _labels(comment: str) -> list[tuple[str, str, str]]:
    normalized = re.sub(r"\s+", " ", comment).strip()
    matches = [
        (
            match.group(1),
            f"{match.group(1)} {re.sub(r'\s+', ' ', match.group(2)).strip()}",
            re.sub(r"\s+", " ", match.group(2)).strip(),
        )
        for match in _END_NO_RE.finditer(normalized)
    ]
    if matches:
        return matches
    return [
        (match.group(1), f"{match.group(1)} - {match.group(2).strip()}", match.group(2).strip())
        for line in comment.splitlines()
        if (match := _YEAR_LINE_RE.match(line))
    ]


def _item_no(rows: list[list[object]], row: int) -> str | None:
    for candidate in (row, row - 1):
        value = _cell(rows, candidate, 0)
        if re.fullmatch(r"\d+(?:\.0)?", value):
            return str(int(float(value)))
    return None


def _subject(rows: list[list[object]], row: int, col: int, text: str, label: str) -> str:
    candidates = []
    if col != 1:
        candidates.append(_cell(rows, row, 1))
    candidates.extend((text, _cell(rows, row - 1, 1)))
    for candidate in candidates:
        compact = " ".join(candidate.split())
        if not compact:
            continue
        if len(compact) <= 170:
            return compact
        if "endorsement" in compact.lower() and len(compact) <= 220:
            return compact
    if _SECOND_OPINION_RE.search(text):
        return "Second Medical Opinion Endorsement"
    if _PIL_RE.search(text):
        return "Prolonged Illness Endorsement"
    subject = re.sub(r"\s*ENDORSEMENT\s+NO\.?\s*\d+\s*$", "", label, flags=re.I).strip()
    return subject or label


def _content(rows: list[list[object]], row: int, text: str) -> str:
    if row < 0 or row >= len(rows):
        return text
    extras = []
    for col, value in enumerate(rows[row][:12]):
        extra = _norm(value)
        if not extra or extra == text:
            continue
        if col == 0 and re.fullmatch(r"\d+(?:\.0)?", extra):
            continue
        if len(extra) <= 160:
            extras.append(extra)
    if extras and len(text) <= 180:
        return "\n".join([text, *extras[:4]])
    return text


def extract_endorsements(sheet: Sheet) -> tuple[ExtractedEndorsement, ...]:
    endorsements: list[ExtractedEndorsement] = []
    for (row, col), note in sheet.comments.items():
        author, comment_body = _split_author(note.text, note.author)
        labels = _labels(comment_body)
        if not labels:
            continue
        text = _cell(sheet.rows, row, col)
        for year, full_label, label_without_year in labels:
            endorsements.append(
                ExtractedEndorsement(
                    source_row=row + 1,
                    source_cell=_source_cell(row, col),
                    item_no=_item_no(sheet.rows, row),
                    year=year,
                    label=full_label,
                    name=_subject(sheet.rows, row, col, text, label_without_year),
                    content=_content(sheet.rows, row, text),
                    comment=note.text,
                    author=author,
                )
            )
    return tuple(endorsements)
