"""Member-facing projection of a plan's Schedule of Benefits.

The stored schedule is the broker's working copy of the placement slip: it
keeps every row the insurer printed, including cells that say "NA", rows that
exist only for the insurer (panel remuneration model, GST extension) and
cross-references that only make sense beside the slip ("Refer to 1a"). The
broker needs all of that to reconcile against the slip. A member does not, and
seeing a dozen "NA" lines tells them nothing except that the page is broken.

``member_schedule`` returns the schedule a member reads. It never invents a
value: it only removes rows and values that say "nothing applies here" and
rewrites wording whose meaning is fixed by the schedule itself. Broker surfaces
and every claim/approval path keep reading the stored schedule untouched.

``is_absent_value`` is the single definition of "this cell carries no cover"
for the backend; the frontend mirrors it in ``lib/sobValues.ts``.
"""
from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any

# Cells that state nothing applies. Compared after folding case, collapsing
# whitespace and dropping trailing dots/colons, so "N.A.", "n/a " and "NA:"
# all fold to one of these.
_NOT_APPLICABLE = frozenset(
    {"na", "n/a", "n.a", "n. a", "not applicable", "nil", "none", "-", "--", "\u2014", "\u2013"}
)
_NOT_COVERED = frozenset({"not covered", "no cover", "not included"})

# Rows that describe the insurer's arrangements rather than the member's
# cover. Hidden by default; a broker can show one with ``member_hidden: false``.
_ADMIN_ROW_PATTERNS = (
    re.compile(r"remuneration model", re.I),
    re.compile(r"\bextension to cover gst\b", re.I),
    re.compile(r"^\s*surcharges?\s*:?\s*$", re.I),
)

# The setup form suffixes outpatient group names with their field set so the
# broker editor reads unambiguously; a member reads the clinic type alone.
_COPAY_NAME_SUFFIX = re.compile(
    r"\s*[\u2014\u2013-]\s*per visit\s*/\s*co-?payment.*$", re.I
)

# "Refer to 1a" / "(not part of 1a)": row 1, sub-row (a) of the same schedule.
_ROW_REF = r"(?:item\s+)?(\d+)\s*\(?\s*([a-z])?\s*\)?"
_REFER_TO = re.compile(rf"^\s*refer to\s+{_ROW_REF}\s*$", re.I)
_NOT_PART_OF = re.compile(rf"^\s*\(?\s*not part of\s+{_ROW_REF}\s*\)?\s*$", re.I)
# Only a row that NAMES a count ("Number of Visits", "Max. no. of days") holds
# one; "Hospital cash per day" names a unit of a dollar amount.
_COUNT_NAME = re.compile(
    r"\b(?:number|no\.?|max(?:imum)?\.?(?:\s+no\.?)?)\s+(?:of\s+)?"
    r"(visits?|days?|sessions?|treatments?|times)\b",
    re.I,
)
_CO_INSURANCE = re.compile(r"co\s*-?\s*insurance", re.I)
_ENUMERATION_KINDS = frozenset({"list", "scale"})
# A note carried by this many rows is the slip's section heading copied onto
# each one ("Primary Care comprising consultation and medication"), not a
# condition of any single benefit.
_HEADING_NOTE_ROWS = 3
_BARE_NUMBER = re.compile(r"^\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^\d+(?:\.\d+)?$")


def _fold(value: Any) -> str:
    text = " ".join(str(value or "").split()).casefold()
    return text.rstrip(".:").strip()


def is_not_applicable(value: Any) -> bool:
    return _fold(value) in _NOT_APPLICABLE


def is_not_covered(value: Any) -> bool:
    return _fold(value) in _NOT_COVERED


def is_absent_value(value: Any) -> bool:
    """Blank, "NA"-style or "Not covered": the cell gives the member no cover."""
    if value is None:
        return True
    folded = _fold(value)
    return not folded or folded in _NOT_APPLICABLE or folded in _NOT_COVERED


def is_admin_row(name: Any) -> bool:
    text = str(name or "")
    return any(p.search(text) for p in _ADMIN_ROW_PATTERNS)


def member_hidden(item: dict[str, Any]) -> bool:
    """Broker choice first; otherwise insurer-admin rows are hidden."""
    choice = item.get("member_hidden")
    if isinstance(choice, bool):
        return choice
    return is_admin_row(item.get("name"))


def _row_key(name: Any) -> str:
    text = str(name or "").replace("&", "and").casefold()
    return re.sub(r"[^a-z0-9]", "", text)


def _clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())
    text = _COPAY_NAME_SUFFIX.sub("", text)
    return text.rstrip(" :").strip()


def _fraction_percent(value: str) -> str:
    """Co-insurance stored as a fraction ("0.1") reads as a percentage."""
    try:
        number = float(value)
    except ValueError:
        return value
    if 0 < number < 1:
        return f"{number * 100:g}%"
    return value


def _reference_label(items: list[dict[str, Any]], number: str, key: str | None) -> str | None:
    """The member-readable name of schedule row ``number`` / sub-row ``key``."""
    for item in items:
        if _fold(item.get("number")).lstrip("-").split(".")[0] != number:
            continue
        if not key:
            return _clean_name(item.get("name")) or None
        for sub in item.get("sub_items") or []:
            if not isinstance(sub, dict):
                continue
            if re.sub(r"[^a-z]", "", _fold(sub.get("key"))) == key.casefold():
                name = _clean_name(sub.get("name"))
                # "Panel Specialists (on cashless basis) (including …)" → the
                # member only needs the service it names.
                return name.split("(")[0].strip() or name
    return None


def _rewrite_reference(value: str, items: list[dict[str, Any]]) -> str:
    match = _REFER_TO.match(value)
    if match:
        label = _reference_label(items, match.group(1), match.group(2))
        return f"Shares the {label} limit" if label else "Shares a limit listed above"
    match = _NOT_PART_OF.match(value)
    if match:
        label = _reference_label(items, match.group(1), match.group(2))
        return f"Separate from the {label} limit" if label else "Separate limit"
    return value


def _clean_value(
    value: Any, *, name: str, kind: Any, items: list[dict[str, Any]]
) -> tuple[str | None, Any]:
    """Member wording for one cell, and the kind it should render with."""
    if is_absent_value(value):
        return None, kind
    text = " ".join(str(value).split())
    text = _rewrite_reference(text, items)
    if _BARE_NUMBER.match(text) and kind in (None, "", "amount", "text"):
        # A bare number is only money when the row isn't naming a count.
        count = _COUNT_NAME.search(name)
        if count:
            noun = count.group(1).casefold().rstrip("s")
            unit = noun if text in {"1", "1.0"} else f"{noun}s"
            return f"{float(text.replace(',', '')):g} {unit}", "text"
        return text, "currency"
    return text, kind


def _clean_limits(limits: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for limit in limits or []:
        if not isinstance(limit, dict) or is_absent_value(limit.get("value")):
            continue
        label = " ".join(str(limit.get("label") or "").split())
        value = " ".join(str(limit.get("value")).split())
        if _CO_INSURANCE.search(label):
            # "Co - insurance Singapore / Overseas" is the slip's column
            # heading; the member reads what it is.
            label, value = "Co-insurance (your share)", _fraction_percent(value)
        out.append({"label": label, "value": value})
    return out


def _clean_properties(properties: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (properties or {}).items():
        if is_absent_value(value):
            continue
        text = " ".join(str(value).split())
        if "co_insurance" in str(key):
            # The parser also stores a column heading here ("Co - insurance
            # Singapore / Overseas") when the row has no rate of its own.
            if not re.search(r"\d", text):
                continue
            text = _fraction_percent(text)
        out[str(key)] = text
    return out


def _clean_sub(
    sub: dict[str, Any], items: list[dict[str, Any]], *, enumeration: bool
) -> dict[str, Any] | None:
    raw_value = sub.get("value")
    name = _clean_name(sub.get("name"))
    value, kind = _clean_value(raw_value, name=name, kind=sub.get("kind"), items=items)
    limits = _clean_limits(sub.get("limits"))
    note = (
        None if is_absent_value(sub.get("note"))
        else _rewrite_reference(" ".join(str(sub.get("note")).split()), items)
    )
    if enumeration:
        # A covered-conditions list / compensation scale entry IS its name.
        return {**sub, "name": name, "value": value, "kind": kind, "note": note,
                "limits": limits} if name else None
    if value is None and not note and not limits:
        return None
    if value is None and is_absent_value(raw_value) and str(raw_value or "").strip():
        # The row stated "NA"/"Not covered" and only a heading-level note is
        # left: the note describes something the member doesn't have.
        return None
    return {**sub, "name": name, "value": value, "kind": kind, "note": note, "limits": limits}


def _clean_item(item: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if member_hidden(item):
        return None
    name = _clean_name(item.get("name"))
    raw_value = item.get("value")
    value, kind = _clean_value(raw_value, name=name, kind=item.get("kind"), items=items)
    properties = _clean_properties(item.get("properties"))
    limits = _clean_limits(item.get("limits"))
    enumeration = item.get("kind") in _ENUMERATION_KINDS
    subs = [
        cleaned
        for sub in item.get("sub_items") or []
        if isinstance(sub, dict)
        and (cleaned := _clean_sub(sub, items, enumeration=enumeration)) is not None
    ]
    note = (
        None if is_absent_value(item.get("note"))
        else _rewrite_reference(" ".join(str(item.get("note")).split()), items)
    )
    stated_absent = bool(str(raw_value or "").strip()) and value is None
    had_props = bool(item.get("properties"))
    if value is None and not properties and not subs and not limits:
        # A note alone survives only on a row that never stated a value: a
        # heading like "Includes surgical implants". Once every cell said "NA",
        # the note is a label for cover the member doesn't have.
        if stated_absent or had_props or not note:
            return None
    cleaned = {
        **item,
        "name": name,
        "value": value,
        "kind": kind,
        "note": note,
        "properties": properties,
        "limits": limits,
        "sub_items": subs,
    }
    cleaned.pop("member_hidden", None)
    return cleaned


def member_schedule(schedule: dict[str, Any] | None) -> dict[str, Any] | None:
    """The schedule as a member reads it. ``None`` stays ``None``."""
    if not isinstance(schedule, dict):
        return None
    raw_items = [i for i in schedule.get("items") or [] if isinstance(i, dict)]
    note_counts = Counter(
        _fold(i.get("note")) for i in raw_items if not is_absent_value(i.get("note"))
    )
    heading_notes = {n for n, count in note_counts.items() if count >= _HEADING_NOTE_ROWS}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        if _fold(item.get("note")) in heading_notes:
            item = {**item, "note": None}
        cleaned = _clean_item(item, raw_items)
        if cleaned is None:
            continue
        key = _row_key(cleaned["name"])
        # Provisional plans can carry the parser's raw row beside the setup's
        # canonical row for the same benefit; the first (canonical) one wins.
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(cleaned)
    result = deepcopy({k: v for k, v in schedule.items() if k != "items"})
    result["items"] = out
    return result
