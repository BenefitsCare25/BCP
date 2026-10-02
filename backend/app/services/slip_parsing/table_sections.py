"""Identify table boundaries from labels and column roles, independent of position."""

from __future__ import annotations

import re

from app.services.excel_reader import Cell
from app.services.slip_parsing.text import _non_empty, _norm


def leading_text(row: list[Cell]) -> str:
    return next((_norm(value) for value in row if _non_empty(value)), "")


def is_rate_heading(row: list[Cell]) -> bool:
    label = leading_text(row).lower()
    if label in {"rate", "rates"}:
        return sum(_non_empty(value) for value in row) == 1
    return bool(re.match(r"^(?:rates?\s*(?::|for\b)|premium\s+rates?\b)", label))


def is_rate_table_header(row: list[Cell]) -> bool:
    labels = [_norm(value).lower() for value in row if _non_empty(value)]
    has_key = any(re.match(r"^(?:category|plan)\b", label) for label in labels)
    has_price = any(re.search(r"\b(?:rate|premium)\b", label) for label in labels)
    has_basis = any(re.match(r"^(?:participation|basis)\b", label) for label in labels)
    return has_key and has_price and not has_basis


def ends_basis_table(row: list[Cell]) -> bool:
    label = leading_text(row).lower()
    return (
        is_rate_heading(row)
        or is_rate_table_header(row)
        or bool(
            re.match(r"^(?:maximum\s+limit|non[ -]evidence|cover\s*:|schedule of benefits)", label)
        )
        or bool(
            re.search(
                r"for illustration only|actual (?:headcount|figures)|figures above are for", label
            )
        )
    )
