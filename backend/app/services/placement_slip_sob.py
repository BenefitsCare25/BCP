"""Schedule-of-Benefits (SOB) extraction for placement slips.

Split out of the parser to keep modules under the file-size cap. Shared cell
helpers and ``Extracted*`` dataclasses come from the ``slip_parsing`` package;
``placement_slip_parser`` (the stable import shim) re-exports this module's
public entry points.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.services.excel_reader import Cell
from app.services.slip_parsing.models import (
    ExtractedBenefitItem,
    ExtractedLimit,
    ExtractedPlan,
    ExtractedSubItem,
)
from app.services.slip_parsing.text import (
    _cell_text,
    _int_code,
    _non_empty,
    _norm,
    _row_text,
)

# ── Schedule of Benefits (SOB) Parser ─────────────────────────────────────────

_BENEFIT_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
_LETTER_KEY = re.compile(r"^\s*([A-Za-z])\s*$")
_UPPER_KEY = re.compile(r"^\s*([A-Z])\s*$")
_SUB_ITEM_KEY = re.compile(r"^\s*\(\s*([a-z])\s*\)\s*$")
# Dash-numbered group rows in outpatient (GCGP-family) schedules: "-1 Panel",
# "-2 Polyclinic", … Each starts a benefit item whose following (a)/(b)/(c)
# qualifier rows are the structured per-visit / co-payment / per-policy-year
# values rather than free-form sub-items.
_GROUP_KEY = re.compile(r"^-(\d+)$")
_PLAN_LABEL = re.compile(r"(?:plan)\s+(.+)", re.IGNORECASE)

# (a)/(b)/(c) qualifier labels inside a dash-numbered group → the structured
# property key the SOB editor's copay fields use. Site variants ("- Restructured
# Hospital" / "- Private Hospital") get their own keys so A&E-style splits
# survive. Mirror of the copay field vocabulary in
# frontend ScheduleOfBenefitsSection COPAY_FIELDS + presets.
_COPAY_VARIANTS: tuple[tuple[str, str], ...] = (
    ("restructured", "restructured"),
    ("private", "private"),
)


def _copay_property_key(label: str) -> str | None:
    """Structured copay key for a group qualifier label, or None."""
    low = label.lower()
    if "co" in low and ("payment" in low or "insurance" in low):
        base = "co_payment"
    elif "per visit" in low:
        base = "per_visit"
    elif "per policy year" in low:
        base = "per_policy_year"
    else:
        return None
    for token, suffix in _COPAY_VARIANTS:
        if token in low:
            return f"{base}_{suffix}"
    return base


def _match_benefit_key(text: str, *, allow_letters: bool) -> str | None:
    """Return a normalized benefit key from an enumerator cell, or None.

    Numbers normalize via ``_int_code`` ("1.0" -> "1"). Only UPPERCASE single
    letters ("A") are top-level keys, and only when ``allow_letters`` (the
    profiler saw a letter-keyed schedule like GCGP A-G). Lowercase letters are
    sub-item markers (GCSP a/b), never top-level — so they don't start phantom
    items on a numbered sheet.
    """
    m = _BENEFIT_NUMBER.match(text)
    if m:
        return _int_code(m.group(1))
    if allow_letters:
        m = _UPPER_KEY.match(text)
        if m:
            return m.group(1).upper()
    return None


_SOB_PROPERTY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("maximum_days", re.compile(r"maximum\s+(?:no\.?\s+of\s+)?days", re.IGNORECASE)),
    ("qualification_period", re.compile(r"qualification\s+period", re.IGNORECASE)),
    ("co_insurance", re.compile(r"co[\s-]*insurance", re.IGNORECASE)),
    ("surgical_schedule", re.compile(r"surgical\s+schedule", re.IGNORECASE)),
)

_SOB_STOP_PHRASES = (
    "ENDORSEMENTS:",
    # Singular so CDL GCGP's "Additional Arrangement" heading also terminates
    # the schedule (startswith → still matches the plural form).
    "ADDITIONAL ARRANGEMENT",
    "EXCLUSIONS",
    "LIST OF EXCLUSION",
    "TO NOTE",
)

# Value-column detection (descriptive layout). A candidate column must be filled
# on at least this fraction of benefit rows, and the chosen value column's mean
# content length must clear this floor (so a one-word reviewer-note column like
# "O.K" can't be mistaken for the value column).
_VALUE_COL_SCAN_ROWS = 80
_VALUE_COL_MIN_FILL_RATIO = 0.25
_VALUE_COL_MIN_AVG_LEN = 3

# --- Nested reference lists -------------------------------------------------
#
# A schedule may embed a REFERENCE LIST: a heading in the name column followed
# by its own enumerated entries, numbered in a column of their own rather than
# in the benefit key column. CDL's and Hartree's GCI sheets both do it:
#
#     [0] 5   [1] Above subject to max. limit per insured person   [4] 500000
#     [0] —   [1] List of Dread Diseases            [3] 1   [4] Major Cancers
#     [0] —   [1] —                                 [3] 2   [4] Heart Attack…
#     [0] —   [1] —                                 [3] 3   [4] Stroke     (and 30 more)
#
# With the key column empty, every one of those entries looked like a
# continuation of benefit 5 and was appended to its VALUE, so the schedule
# reported the max limit as "500000.0 Heart Attack of Specified Severity Stroke
# Coronary Artery By-pass Surgery …" — on the member's own coverage page as well
# as the broker's.
#
# The rule is deliberately strict, because a false positive silently rewrites a
# benefit into a list: the enumerator must sit in a column carrying NO
# structural role, the opening row must read exactly "1" AND name the list, and
# each entry must increment by one. Anything else ends the list and falls
# through to the ordinary continuation handling.
_LIST_ENUM_RE = re.compile(r"^\(?(\d{1,3})(?:\.0)?\)?[.)]?$")


def _list_enumerator(row: list[Cell], col: int | None) -> int | None:
    """The bare ordinal at ``row[col]`` ("1", "1.0", "(1)", "1."), else None."""
    if col is None or col < 0:
        return None
    match = _LIST_ENUM_RE.match(_cell_text(row, col))
    return int(match.group(1)) if match else None


def _nested_list_column(row: list[Cell], skip: set[int]) -> int | None:
    """Column where a nested list OPENS on this row — an enumerator reading
    exactly "1" outside every structural column — else None."""
    for col in range(len(row)):
        if col in skip:
            continue
        if _list_enumerator(row, col) == 1:
            return col
    return None


def _is_stop_row(row: list[Cell]) -> bool:
    """True when a row marks the end of the benefit schedule.

    The terminating heading (Endorsements / Exclusions / To Note) may be a plain
    row ("Endorsements:" in col0) or a numbered row ("1. List of exclusions :"
    with the heading in col1). For the numbered case the row text starts with the
    number, not the phrase, so the col1 *label* is checked — but only when it
    reads as a section heading (the whole label is the phrase, or it is
    colon-terminated). That keeps a real benefit merely *starting* with the word
    (e.g. "Exclusions buy-back extension") from being mistaken for the end.
    """
    col0 = _norm(row[0]) if row and _non_empty(row[0]) else ""
    col1 = _norm(row[1]) if len(row) > 1 and _non_empty(row[1]) else ""
    label = (col1 or col0).upper().strip()
    full = _row_text(row).upper().strip()
    for s in _SOB_STOP_PHRASES:
        if full.startswith(s):
            return True
        if label.startswith(s) and (label == s or label.endswith(":")):
            return True
    return False


def _find_sob_section(rows: list[list[Cell]]) -> int:
    """Find the row index of the SOB header. Returns -1 if not found."""
    for i, row in enumerate(rows):
        # The header label sits in the FIRST non-empty cell ("SCHEDULE OF
        # BENEFITS / INSURER / PLAN") — usually col0, but a merged title cell can
        # land in col1 with col0 empty. Anchoring on the leading cell avoids
        # false-matching the "Cover:" description that repeats the phrase ("... as
        # per schedule of benefits") in col1 (its col0 is "Cover:"), and mid-table
        # references.
        col0 = _norm(row[0]).upper() if row and _non_empty(row[0]) else ""
        col1 = _norm(row[1]).upper() if len(row) > 1 and _non_empty(row[1]) else ""
        header_label = col0 if col0 else col1
        if header_label.startswith("SCHEDULE OF BENEFITS"):
            # The header row is usually the one with plan column labels.
            # Sometimes the "Cover:" description precedes the actual SOB header.
            # Look for the row that actually has plan labels in cols > 4.
            if any(
                _non_empty(c) and _PLAN_LABEL.search(str(c))
                for c in (row[5:] if len(row) > 5 else [])
            ):
                return i
            # Check next row for plan labels
            if i + 1 < len(rows):
                next_row = rows[i + 1] or []
                if any(
                    _non_empty(c) and _PLAN_LABEL.search(str(c))
                    for c in (next_row[5:] if len(next_row) > 5 else [])
                ):
                    return i + 1
            # The header row itself has "SCHEDULE OF BENEFITS" + plan labels
            return i
    return -1


def _detect_plan_columns(
    rows: list[list[Cell]], sob_idx: int
) -> list[tuple[str, str, int]]:
    """Detect plan columns from the SOB header row.

    Returns list of (code, display_name, col_index).
    """
    header_row = rows[sob_idx] if sob_idx < len(rows) else []
    results: list[tuple[str, str, int]] = []

    for col_idx in range(5, len(header_row)):
        cell = header_row[col_idx]
        if not _non_empty(cell):
            continue
        label = _norm(cell)
        m = _PLAN_LABEL.match(label)
        if m:
            # Normalize numeric codes: "1.0" → "1"
            raw_code = _int_code(m.group(1))
            results.append((raw_code, label, col_idx))
        elif re.match(r"^\s*\d+(?:\.\d+)?\s*$", label):
            # Bare number used as plan column header. xlrd yields these as
            # floats ("1.0"), so normalize "1.0" → "1" before display.
            code = _int_code(label)
            results.append((code, f"Plan {code}", col_idx))

    return results


def _is_sob_property(text: str) -> str | None:
    """If the text matches a known property pattern, return the property key."""
    for key, pattern in _SOB_PROPERTY_PATTERNS:
        if pattern.search(text):
            return key
    return None


def _fmt_value(cell: Cell) -> str | None:
    """Format a SOB cell value as a display string."""
    if cell is None:
        return None
    if isinstance(cell, float):
        if cell == int(cell):
            return str(int(cell))
        return str(cell)
    s = str(cell).strip()
    if not s or s.upper() == "NA":
        return None
    return s


def _is_na(cell: Cell) -> bool:
    """True when the cell EXPLICITLY reads "NA".

    ``_fmt_value`` maps both "NA" and a blank cell to ``None`` — right for
    display, wrong for folding per-plan grids into the column model, where
    blank means "inherit" and "NA" means "this plan doesn't have it". See
    ``ExtractedBenefitItem.not_applicable``.
    """
    if cell is None or isinstance(cell, float):
        return False
    first = str(cell).split("\n")[0].strip()
    return first.upper() == "NA"


def _split_value_note(cell: Cell) -> tuple[str | None, str | None, bool]:
    """Split a SOB cell into (value, footnote, not_applicable).

    Insurers cram a primary value and a qualifying footnote into one cell,
    separated by newlines — e.g. "4 Bed Pte\\n* Bargainable employees:\\n4 Bed
    Govt/Restr. Hospital". The first line is the value; the rest (often an
    asterisked exception) becomes the note so the value stays clean for display
    and matching. Numeric cells never carry a note.

    The third element distinguishes an explicit "NA" from a blank cell; both
    yield ``value=None``. It is a 3-tuple rather than an optional extra so no
    call site can silently keep discarding it.
    """
    if cell is None or isinstance(cell, float):
        return _fmt_value(cell), None, False
    parts = [p.strip() for p in str(cell).split("\n") if p.strip()]
    if not parts:
        return None, None, False
    value = _fmt_value(parts[0])
    note = " ".join(parts[1:]).strip() or None
    return value, note, _is_na(cell)


def _parse_sob_items(
    rows: list[list[Cell]],
    data_start: int,
    plan_col: int,
    name_col: int = 1,
    key_col: int = 0,
    allow_letters: bool = False,
) -> list[ExtractedBenefitItem]:
    """Parse benefit items from SOB rows for a single plan column.

    ``key_col`` holds the enumerator (digits, or letters when ``allow_letters``
    — GCGP's A/B/C feature rows), ``name_col`` the benefit name, ``plan_col`` the
    value. Defaults (key col0, name col1) match the classic numbered layout.

    Beyond the benefit number/name/value, this captures three layers insurers
    routinely cram into the schedule and that a flat value field would lose:
      - footnotes (the qualifier lines inside a value cell, or stand-alone rows
        like "Include Implants") -> ``note``;
      - qualifier rows ("Maximum no. of days", "Maximum limit per policy year")
        -> ``limits``, attached to whichever item/sub-item they follow;
      - per-plan sub-item values (Hospital Misc / Surgical Fees / doctor visit).
    """
    items: list[ExtractedBenefitItem] = []
    current_number: str | None = None
    current_name: str = ""
    current_value: str | None = None
    current_note: str | None = None
    current_na = False
    current_limits: list[ExtractedLimit] = []
    current_sub_items: list[dict[str, Any]] = []
    current_properties: dict[str, str] = {}
    # Where trailing limit/footnote rows attach: the last sub-item dict, or None
    # for the item itself.
    target: dict[str, Any] | None = None
    # True while the current item came from a dash-numbered group row
    # ("-1 Panel"): its (a)/(b)/(c) qualifier rows become structured copay
    # properties instead of sub-items.
    in_group = False
    # A colon-terminated header line ("Primary Care comprising consultation and
    # medication :") introducing the dash-numbered groups below it; carried onto
    # each group item as its note so the grouping context isn't lost.
    pending_group_note: str | None = None
    consec_blank = 0

    def _flush() -> None:
        if current_number and current_name:
            items.append(ExtractedBenefitItem(
                number=current_number,
                name=current_name.strip(),
                value=current_value,
                note=current_note,
                limits=tuple(current_limits),
                sub_items=tuple(
                    ExtractedSubItem(
                        key=s["key"], name=s["name"], value=s["value"],
                        note=s["note"], limits=tuple(s["limits"]),
                        not_applicable=bool(s.get("na")),
                    )
                    for s in current_sub_items
                ),
                properties=dict(current_properties),
                not_applicable=current_na,
            ))

    def _attach_note(note: str | None) -> None:
        if not note:
            return
        if target is not None:
            target["note"] = " ".join(
                p for p in (target["note"], note) if p
            ).strip()
        else:
            nonlocal current_note
            current_note = " ".join(
                p for p in (current_note, note) if p
            ).strip() or None

    def _attach_limit(label: str, value: str | None) -> None:
        lim = ExtractedLimit(label=label.strip(), value=value)
        if target is not None:
            target["limits"].append(lim)
        else:
            current_limits.append(lim)

    for i in range(data_start, len(rows)):
        row = rows[i] or []
        text = _row_text(row)

        if not text.strip():
            consec_blank += 1
            if consec_blank >= 4:
                break
            continue
        consec_blank = 0

        if _is_stop_row(row):
            break

        key_cell = _cell_text(row, key_col)
        name_cell = _cell_text(row, name_col)
        plan_cell = row[plan_col] if plan_col < len(row) else None

        # New benefit item (enumerated row - number, or letter when allowed)
        new_key = _match_benefit_key(key_cell, allow_letters=allow_letters)
        if new_key:
            _flush()
            current_number = new_key
            # The name is in name_col; key_col is the bare enumerator, never a
            # usable name, so don't fall back to it — a nameless enumerated row
            # is dropped by _flush rather than labelled with its own key.
            current_name = name_cell
            # Handle multiline cell names — take first line
            if "\n" in current_name:
                current_name = current_name.split("\n")[0].strip()
            current_value, current_note, current_na = _split_value_note(plan_cell)
            current_limits = []
            current_sub_items = []
            current_properties = {}
            target = None
            in_group = False
            pending_group_note = None
            continue

        # Dash-numbered group row ("-1 Panel"): a benefit item of its own whose
        # qualifier rows are structured copay values. Keeps the sheet's literal
        # "-N" as the number — it can't collide with a genuine enumerated row
        # ("7 Extension to cover GST" vs "-7 WhiteCoat Teleconsultation").
        group_match = _GROUP_KEY.match(_int_code(key_cell)) if key_cell else None
        if group_match and name_cell:
            _flush()
            current_number = f"-{group_match.group(1)}"
            current_name = name_cell.split("\n")[0].strip()
            current_value, current_note, current_na = _split_value_note(plan_cell)
            if pending_group_note:
                current_note = " ".join(
                    p for p in (pending_group_note, current_note) if p
                ).strip() or None
            current_limits = []
            current_sub_items = []
            current_properties = {}
            target = None
            in_group = True
            continue

        # Sub-item: parenthesised "(a)", or a bare letter "a"/"b" when letters
        # are NOT top-level keys (GCSP nests a/b under a numbered parent, each
        # carrying the real per-plan value the numbered row itself lacks).
        sub_match = _SUB_ITEM_KEY.match(key_cell)
        bare_letter = None if (sub_match or allow_letters) else _LETTER_KEY.match(key_cell)
        if (sub_match or bare_letter) and current_number:
            name = name_cell or ""
            if "\n" in name:
                name = name.split("\n")[0].strip()
            # Inside a dash-numbered group the (a)/(b)/(c) rows ARE the item's
            # structured values — store them as copay properties (the editor's
            # per-visit / co-payment / per-policy-year fields). Unrecognized
            # labels still fall back to ordinary sub-items below.
            if in_group:
                prop = _copay_property_key(name)
                if prop:
                    raw = _norm(plan_cell) if plan_cell is not None else ""
                    val = _fmt_value(plan_cell) or raw
                    if val:
                        current_properties[prop] = val
                    continue
            item_match = sub_match or bare_letter
            if item_match is None:
                continue
            letter = item_match.group(1)
            key = f"({letter})"
            value, note, sub_na = _split_value_note(plan_cell)
            sub: dict[str, Any] = {
                "key": key, "name": name, "value": value,
                "note": note, "limits": [], "na": sub_na,
            }
            current_sub_items.append(sub)
            target = sub
            continue

        # Property / qualifier row (continuation of current benefit). Recorded
        # both in `properties` (back-compat) and as a structured limit on the
        # current target so the renderer can show it inline.
        if current_number and name_cell:
            prop_key = _is_sob_property(name_cell)
            if prop_key:
                val = _fmt_value(plan_cell) or name_cell
                current_properties[prop_key] = val
                _attach_limit(name_cell, _fmt_value(plan_cell))
                continue

        # Continuation row with no enumerator. Either a labelled qualifier, a
        # stand-alone footnote (text only in the plan column, e.g. "Include
        # Implants"), or a sub-detail condition.
        if current_number and not key_cell:
            if name_cell:
                prop_key = _is_sob_property(name_cell)
                if prop_key:
                    current_properties[prop_key] = _fmt_value(plan_cell) or name_cell
                    _attach_limit(name_cell, _fmt_value(plan_cell))
                elif name_cell.rstrip().endswith(":") and not _non_empty(plan_cell):
                    # Section header introducing dash-numbered groups below
                    # ("Primary Care comprising consultation and medication :").
                    # Remembered so the group items keep their context; inert on
                    # sheets without dash groups.
                    pending_group_note = name_cell.rstrip(": ").strip()
                elif _non_empty(plan_cell):
                    # Labelled sub-detail with a value — attach as sub-item.
                    value, note, sub_na = _split_value_note(plan_cell)
                    sub = {
                        "key": "", "name": name_cell, "value": value,
                        "note": note, "limits": [], "na": sub_na,
                    }
                    current_sub_items.append(sub)
                    target = sub
            elif _non_empty(plan_cell):
                # No label at all, only a plan-column annotation — a footnote on
                # whatever item/sub-item we're inside (e.g. "Include Implants").
                _attach_note(_fmt_value(plan_cell))

    _flush()
    return items


def _find_data_start(rows: list[list[Cell]], sob_idx: int) -> int:
    """First numbered benefit row after the header anchors both layouts."""
    for i in range(sob_idx + 1, min(sob_idx + 12, len(rows))):
        row = rows[i] or []
        col0 = _norm(row[0]) if len(row) > 0 and _non_empty(row[0]) else ""
        if _BENEFIT_NUMBER.match(col0):
            return i
    return sob_idx + 1


@dataclass(frozen=True)
class _SobRoles:
    """Content-derived column roles for one Schedule-of-Benefits table.

    The single source of truth for *where* the benefit key/name/value columns
    sit, replacing the old per-parser positional assumptions. Every layout
    (numbered, letter-keyed, name-first, descriptive) is expressed as a
    combination of these roles, so one profiler drives all parse paths.
    """

    name_col: int                # column holding benefit names
    key_col: int | None          # column holding enumerators, or None
    allow_letter_keys: bool      # key column uses letters (A, B, …) not digits
    value_col: int | None        # single value column (descriptive only)
    name_first: bool             # names in col0, no separate key column
    confidence: float            # 0-1, how cleanly the roles resolved


def roles_to_dict(roles: _SobRoles) -> dict[str, Any]:
    """Serialize column roles for storage / transport (template memory + API)."""
    return {
        "name_col": roles.name_col,
        "key_col": roles.key_col,
        "allow_letter_keys": roles.allow_letter_keys,
        "value_col": roles.value_col,
        "name_first": roles.name_first,
        "confidence": roles.confidence,
    }


def roles_from_dict(data: dict[str, Any]) -> _SobRoles:
    """Rebuild column roles from a stored override (broker-corrected mapping).

    Tolerant of partial dicts: missing fields fall back to safe defaults, and
    ``name_first`` is re-derived when absent so an override only needs the
    columns the broker actually set.
    """
    key_col = data.get("key_col")
    raw_name = data.get("name_col")
    name_col = 0 if raw_name is None else int(raw_name)
    name_first = data.get("name_first")
    if name_first is None:
        name_first = key_col is None and name_col == 0
    return _SobRoles(
        name_col=name_col,
        key_col=None if key_col is None else int(key_col),
        allow_letter_keys=bool(data.get("allow_letter_keys", False)),
        value_col=None if data.get("value_col") is None else int(data["value_col"]),
        name_first=bool(name_first),
        confidence=float(data.get("confidence", 1.0)),
    )


def _fingerprint_from_parts(
    product_code: str,
    insurer: str | None,
    plan_cols: list[tuple[str, str, int]],
) -> str:
    """Build a template fingerprint from STABLE structural parts only.

    Deliberately excludes the SOB header row text: that row often carries
    volatile per-client / per-year content (policyholder name, policy year)
    that would change the hash on every re-upload and defeat reuse. The
    product, insurer, and the plan-column shape (codes + positions + count)
    are what actually identify the layout.
    """
    plan_codes = ",".join(code for code, _, _ in plan_cols)
    plan_positions = ",".join(str(c) for _, _, c in plan_cols)
    sig = "|".join([
        product_code.upper(), (insurer or "").lower(),
        plan_codes, plan_positions, str(len(plan_cols)),
    ])
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]


def sob_template_fingerprint(
    rows: list[list[Cell]], product_code: str, insurer: str | None
) -> str | None:
    """Stable signature of a template's Schedule-of-Benefits layout, or None.

    Same carrier template across re-uploads -> same fingerprint; structurally
    different templates -> different fingerprints.
    """
    sob_idx = _find_sob_section(rows)
    if sob_idx < 0:
        return None
    plan_cols = _detect_plan_columns(rows, sob_idx)
    return _fingerprint_from_parts(product_code, insurer, plan_cols)


def _is_sob_metadata_row(col0: str) -> bool:
    """A "Cover :" / "Definition:" / colon-terminated label row, not a benefit."""
    return col0.rstrip().endswith(":") or col0.upper().startswith("COVER")


def _profile_sob_columns(
    rows: list[list[Cell]],
    data_start: int,
    plan_cols: list[tuple[str, str, int]],
) -> _SobRoles:
    """Classify the key/name/value columns of a SOB table from its content.

    Scans the benefit-data region and scores each left-hand column by fill,
    average text length, and how often it reads as an enumerator (number,
    single letter, or "(a)"). From that it picks:
      - ``key_col``: leftmost column whose filled cells are mostly key-like;
      - ``name_col``: the most text-heavy non-key column;
      - ``value_col``: (descriptive only) the benefit-value column right of name.

    This makes layout a derived fact, not a positional guess: GHS (digit key,
    name col1), GCGP (letter key, name col1), GBT/OSI (name col0, no key) and
    GPA (name col1, value col4, no key) all fall out of the same scoring.
    """
    scan_stop = min(data_start + _VALUE_COL_SCAN_ROWS, len(rows))
    first_plan_col = plan_cols[0][2] if plan_cols else None
    # Only columns left of the first plan/value region can hold key or name.
    scan_max = max(2, min(first_plan_col if first_plan_col is not None else 6, 6))

    stats: dict[int, dict[str, int]] = {}
    n_rows = 0
    for i in range(data_start, scan_stop):
        row = rows[i] or []
        if not _row_text(row).strip():
            continue
        if _is_stop_row(row):
            break
        col0 = _norm(row[0]) if len(row) > 0 and _non_empty(row[0]) else ""
        if _is_sob_metadata_row(col0):
            continue
        n_rows += 1
        for c in range(scan_max):
            if c < len(row) and _non_empty(row[c]):
                t = _norm(row[c])
                st = stats.setdefault(
                    c, {"fill": 0, "len": 0, "key": 0, "digit": 0, "upper": 0}
                )
                st["fill"] += 1
                st["len"] += len(t)
                if _BENEFIT_NUMBER.match(t):
                    st["key"] += 1
                    st["digit"] += 1
                elif _LETTER_KEY.match(t):
                    st["key"] += 1
                    if _UPPER_KEY.match(t):
                        st["upper"] += 1
                elif _SUB_ITEM_KEY.match(t):
                    st["key"] += 1

    # key_col: an enumerator column. Keys are always leftmost (col0/col1) — a
    # value column full of bare numbers ("10000.0") must NOT be mistaken for one.
    key_col: int | None = None
    allow_letters = False
    for c in sorted(c for c in stats if c <= 1):
        st = stats[c]
        if st["fill"] >= 2 and st["key"] >= 0.5 * st["fill"]:
            key_col = c
            # UPPERCASE letters are TOP-LEVEL keys when they're at least as
            # common as digit keys (GCGP A-G, which has no/few numbered rows).
            # Lowercase a/b stay sub-items (GCSP 1.0/2.0 parents), so they never
            # flip this on.
            allow_letters = st["upper"] > 0 and st["upper"] >= st["digit"]
            break

    # name_col: for a keyed schedule the name sits immediately right of the key
    # (universally true: GHS/GMM/GCGP/GCI/GCSP). For a key-less schedule it's the
    # leftmost substantial text column (col0 for GBT/OSI/GD, col1 for GPA).
    min_fill = max(2, 0.3 * n_rows)
    if key_col is not None:
        name_col = key_col + 1
    else:
        text_cols = [
            c for c in sorted(stats)
            if stats[c]["fill"] >= min_fill
            and stats[c]["key"] < 0.5 * stats[c]["fill"]
        ]
        name_col = text_cols[0] if text_cols else 0

    name_first = key_col is None and name_col == 0
    name_fill = stats.get(name_col, {}).get("fill", 0)
    confidence = min(1.0, name_fill / n_rows) if n_rows else 0.0

    value_col: int | None = None
    if not plan_cols:
        value_col = _detect_value_column(rows, data_start, name_col, key_col)

    return _SobRoles(
        name_col=name_col,
        key_col=key_col,
        allow_letter_keys=allow_letters,
        value_col=value_col,
        name_first=name_first,
        confidence=confidence,
    )


def _extract_cover(rows: list[list[Cell]], sob_idx: int) -> str | None:
    """Pull the "Cover:" description sitting just above the SOB header."""
    for look_back in range(max(0, sob_idx - 6), sob_idx):
        r = rows[look_back] or []
        if _row_text(r).upper().startswith("COVER"):
            parts: list[str] = [str(c) for c in r if _non_empty(c)]
            # The description may wrap onto the next rows — but only absorb
            # *continuation* rows (empty col0). A row that introduces a new
            # labelled field (non-empty col0) ends the cover, so unrelated
            # metadata between the cover line and the header isn't swept in.
            for j in range(look_back + 1, sob_idx):
                nxt = rows[j] or []
                if nxt and _non_empty(nxt[0]):
                    break
                parts.extend(str(c) for c in nxt if _non_empty(c))
            cover = " ".join(parts).strip()
            return cover[:500] if len(cover) > 500 else cover
    return None


def _detect_value_column(
    rows: list[list[Cell]],
    data_start: int,
    name_col: int = 1,
    key_col: int | None = 0,
) -> int | None:
    """Find the benefit-value column for a descriptive (non per-plan) schedule.

    Term-life / GPA / WICI schedules carry a single plan: the benefit value sits
    in a free-text column (e.g. "Pays 100% of sum insured", "From S$91,000 to
    S$296,000"), while other columns hold internal reviewer notes ("O.K", "N.A",
    "to check"). Insurers place the value column to the LEFT of any note columns,
    so among the columns that actually carry content pick the leftmost one whose
    text is substantial — this beats a pure max-average rule, which a verbose
    reviewer-note column ("confirmed with underwriter…") could otherwise win, and
    it still accepts terse-but-real values ("S$250", "100%").

    A "benefit row" is identified by an enumerator in ``key_col`` (or a "-"
    bullet) when a key column exists, else by text in ``name_col`` — so GPA-style
    sheets (names in col1, no number column) are scanned, not skipped. The value
    column is searched strictly to the right of ``name_col``.
    """
    stats: dict[int, list[int]] = {}  # col -> [fill_count, total_chars]
    n_items = 0
    for i in range(data_start, min(data_start + _VALUE_COL_SCAN_ROWS, len(rows))):
        row = rows[i] or []
        text = _row_text(row)
        if not text.strip():
            continue
        if _is_stop_row(row):
            break
        key_cell = (
            _norm(row[key_col])
            if key_col is not None and key_col < len(row) and _non_empty(row[key_col])
            else ""
        )
        name_cell = (
            _norm(row[name_col])
            if name_col < len(row) and _non_empty(row[name_col])
            else ""
        )
        if key_col is not None:
            is_benefit = bool(_match_benefit_key(key_cell, allow_letters=True)
                              or key_cell == "-")
        else:
            is_benefit = bool(name_cell) and not _is_sob_metadata_row(name_cell)
        if not is_benefit:
            continue
        n_items += 1
        for c in range(name_col + 1, len(row)):
            if _non_empty(row[c]):
                st = stats.setdefault(c, [0, 0])
                st[0] += 1
                st[1] += len(_norm(row[c]))
    if not n_items:
        return None
    min_fill = max(2, n_items * _VALUE_COL_MIN_FILL_RATIO)
    filled = [
        (c, total / fill)
        for c, (fill, total) in sorted(stats.items())
        if fill >= min_fill
    ]
    if not filled:
        return None
    # Leftmost column whose average content clears the floor is the value column
    # (notes sit further right); fall back to the most descriptive if none clear.
    for c, avg in filled:
        if avg >= _VALUE_COL_MIN_AVG_LEN:
            return c
    return max(filled, key=lambda ca: ca[1])[0]


def _parse_descriptive_items(
    rows: list[list[Cell]],
    data_start: int,
    value_col: int,
    name_col: int = 1,
    key_col: int | None = 0,
    allow_letters: bool = False,
) -> list[ExtractedBenefitItem]:
    """Parse a single-plan descriptive schedule (term life, GPA, WICI).

    Keyed sheets (``key_col`` set): enumerated rows are benefits; "-" rows are
    bulleted sub-benefits under the most recent item. Key-less sheets (GPA: name
    in col1, no number column) treat every name-bearing row as its own benefit
    with an auto-assigned sequential number. The value is read from ``value_col``;
    reviewer-annotation columns are ignored. "OR" connectors and "Section X"
    dividers are skipped.
    """
    items: list[ExtractedBenefitItem] = []
    used_numbers: set[str] = set()
    number: str | None = None
    name = ""
    value: str | None = None
    note: str | None = None
    subs: list[ExtractedSubItem] = []
    kind: str | None = None
    consec_blank = 0
    # Column an open nested list is enumerated in, and the ordinal its next
    # entry must carry. See _nested_list_column.
    list_col: int | None = None
    list_next = 0

    def _flush() -> None:
        if not (number and name):
            return
        # Sheets that restart numbering per section (e.g. WICI Section A/B) would
        # otherwise collide; keep numbers unique within the plan. The suffix is a
        # LETTER ("1" -> "1a") so it can't be confused with — or collide against —
        # a genuine sub-numbered benefit like "1.1".
        num = number
        suffix = 0
        while num in used_numbers:
            num = f"{number}{chr(ord('a') + suffix)}"
            suffix += 1
        used_numbers.add(num)
        items.append(ExtractedBenefitItem(
            number=num, name=name.strip(), value=value, note=note,
            sub_items=tuple(subs), kind=kind,
        ))

    for i in range(data_start, len(rows)):
        row = rows[i] or []
        text = _row_text(row)
        if not text.strip():
            consec_blank += 1
            if consec_blank >= 4:
                break
            continue
        consec_blank = 0
        if _is_stop_row(row):
            break

        key_cell = _cell_text(row, key_col) if key_col is not None else ""
        name_cell = _cell_text(row, name_col)
        # A FLOAT cell is normalized the way the per-plan parser has always
        # normalized it; everything else keeps the existing text path, so "NA"
        # and prose are untouched. xlrd yields every numeric cell as a float, so
        # without this a money value arrived as "500000.0" / "2000.0" and was
        # stored — and rendered to members — with the artifact intact. It is why
        # every descriptive product (GTL/GCI/GPA/WICI) carried them.
        raw_val = row[value_col] if 0 <= value_col < len(row) else None
        cell_val = (
            _fmt_value(raw_val)
            if isinstance(raw_val, float)
            else (_cell_text(row, value_col) or None)
        )

        if key_col is None:
            # Key-less (GPA): each name row is its own benefit; value-only rows
            # extend the current value.
            if name_cell:
                if name_cell.upper() == "OR":
                    continue
                if not cell_val and (
                    name_cell.startswith("Section ") or _is_sob_metadata_row(name_cell)
                ):
                    continue  # section divider / label, not a benefit
                _flush()
                number, name, value, note, subs = (
                    str(len(items) + 1), name_cell, cell_val, None, [],
                )
                kind = None
            elif number and cell_val:
                value = " ".join(p for p in (value, cell_val) if p).strip() or None
            continue

        key = _match_benefit_key(key_cell, allow_letters=allow_letters)
        if key:
            _flush()
            number, name, value, note, subs = (key, name_cell, cell_val, None, [])
            kind, list_col = None, None
            continue

        # An open nested list consumes its own entries, in order. A row that
        # doesn't continue the sequence closes the list and is handled below as
        # it would have been otherwise.
        if list_col is not None:
            if not name_cell and _list_enumerator(row, list_col) == list_next:
                subs.append(
                    ExtractedSubItem(key=str(list_next), name=cell_val or "", value=None)
                )
                list_next += 1
                continue
            list_col = None

        # …and a row naming a list AND enumerating its first entry opens one.
        # The heading becomes a benefit row of its own rather than a qualifier
        # of whatever preceded it — the list is reference material, not a limit
        # on the benefit above it.
        if not key_cell and name_cell and cell_val:
            opened = _nested_list_column(
                row, {c for c in (key_col, name_col, value_col) if c is not None}
            )
            if opened is not None:
                _flush()
                list_col, list_next = opened, 2
                number, name, value, note = str(len(items) + 1), name_cell, None, None
                subs = [ExtractedSubItem(key="1", name=cell_val, value=None)]
                kind = "list"
                continue

        if key_cell == "-" and number:
            # Bulleted sub-benefit under the current item.
            subs.append(ExtractedSubItem(key="", name=name_cell, value=cell_val))
            continue

        if not key_cell and number:
            if name_cell.upper() == "OR":
                continue
            if not cell_val and (name_cell.startswith("Section ") or name_cell.endswith(":")):
                continue  # section divider, not a benefit
            if name_cell and cell_val:
                subs.append(ExtractedSubItem(key="", name=name_cell, value=cell_val))
            elif name_cell:
                # Qualifying clause with no value of its own -> note. Captured
                # even when the item already has a value, so a trailing clause
                # (e.g. "Subject to 30-day waiting period") isn't dropped.
                note = " ".join(p for p in (note, name_cell) if p).strip() or None
            elif cell_val:
                # Value-only continuation -> extend the current value.
                value = " ".join(p for p in (value, cell_val) if p).strip() or None

    _flush()
    return items


def _parse_name_first_items(
    rows: list[list[Cell]],
    data_start: int,
    plan_col: int,
    all_plan_cols: list[int],
    name_col: int = 0,
) -> list[ExtractedBenefitItem]:
    """Parse SOB where the name column carries the benefit name directly.

    Used by GBT/GTI/OSI-style sheets where the layout is:
      name_col: benefit name   (no enumerator)   plan_col: this plan's value

    Whether a name row is a *benefit* (vs a "SECTION A:" header) is decided from
    whether ANY plan column on that row carries a value — not just this plan's.
    That keeps the benefit rows IDENTICAL across plans, so a plan whose own
    column is blank (GD's "As Charged" Panel-dentist plan) still emits the full
    benefit list with empty values, instead of vanishing. Continuation rows
    (name empty, a qualifier label in the next column, value optional) become
    sub-items or notes on the current benefit.
    """
    label_col = name_col + 1
    items: list[ExtractedBenefitItem] = []
    current_name: str = ""
    current_value: str | None = None
    current_note: str | None = None
    current_na = False
    current_sub_items: list[dict[str, Any]] = []
    consec_blank = 0

    def _flush() -> None:
        if not current_name:
            return
        items.append(ExtractedBenefitItem(
            number=str(len(items) + 1),
            name=current_name.strip(),
            value=current_value,
            note=current_note,
            not_applicable=current_na,
            sub_items=tuple(
                ExtractedSubItem(
                    key=s["key"], name=s["name"], value=s["value"],
                    note=s["note"], limits=(),
                    not_applicable=bool(s.get("na")),
                )
                for s in current_sub_items
            ),
        ))

    for i in range(data_start, len(rows)):
        row = rows[i] or []
        if not _row_text(row).strip():
            consec_blank += 1
            if consec_blank >= 4:
                break
            continue
        consec_blank = 0
        if _is_stop_row(row):
            break

        name_cell = _cell_text(row, name_col)
        label_cell = _cell_text(row, label_col)
        plan_cell = row[plan_col] if 0 <= plan_col < len(row) else None
        any_value = any(
            0 <= c < len(row) and _non_empty(row[c]) for c in all_plan_cols
        )

        if name_cell:
            # A name row with NO value in any plan column is a section header.
            if not any_value:
                continue
            _flush()
            current_name = name_cell
            current_value, current_note, current_na = _split_value_note(plan_cell)
            current_sub_items = []
        elif label_cell and current_name:
            # Continuation: qualifier label in the column after the name.
            #
            # Gated on the CELL being non-empty, not on `plan_val` being truthy:
            # `_fmt_value` folds an explicit "NA" to None, so a truthiness test
            # dropped exactly the rows that assert NO cover into the note text.
            # The sub-row then didn't exist for that column, and the fold
            # inherited the richer plan's figure — overstating cover, which is
            # the one error worse than showing a blank. (The numbered layout has
            # always gated on `_non_empty` for this reason.)
            if _non_empty(plan_cell):
                value, note, sub_na = _split_value_note(plan_cell)
                current_sub_items.append({
                    "key": "", "name": label_cell, "value": value, "note": note,
                    "limits": [], "na": sub_na,
                })
            else:
                current_note = " ".join(p for p in (current_note, label_cell) if p).strip() or None

    _flush()
    return items


def _extract_plans_from_sheet(
    rows: list[list[Cell]],
    roles_override: _SobRoles | None = None,
    *,
    sob_idx: int | None = None,
    plan_cols: list[tuple[str, str, int]] | None = None,
    data_start: int | None = None,
) -> tuple[ExtractedPlan, ...]:
    """Extract Schedule of Benefits plans from a product sheet.

    Column roles (key / name / value) are derived from content by
    ``_profile_sob_columns``; the resulting profile drives every layout through
    one set of parsers instead of positional guesses:
      - per-plan numbered (GHS, GMM, SP): digit key, name col1, per-plan value;
      - per-plan letter-keyed (GCGP): letter key A-G, name col1;
      - per-plan name-first (GBT/OSI/GD): name col0, no key, per-plan value;
      - descriptive single-plan (GTL/GCI/GPA/WICI): one value column + notes.
    ``roles_override`` (a broker-corrected mapping from template memory) bypasses
    the profiler. ``sob_idx``/``plan_cols``/``data_start`` may be passed in when
    the caller already located them (the upload path), to avoid re-scanning the
    sheet; they are computed on demand otherwise. Returns an empty tuple when the
    sheet has no benefit schedule.
    """
    if sob_idx is None:
        sob_idx = _find_sob_section(rows)
    if sob_idx < 0:
        return ()

    if data_start is None:
        data_start = _find_data_start(rows, sob_idx)
    if plan_cols is None:
        plan_cols = _detect_plan_columns(rows, sob_idx)
    cover_desc = _extract_cover(rows, sob_idx)
    roles = roles_override or _profile_sob_columns(rows, data_start, plan_cols)

    if not plan_cols:
        # Descriptive single-plan.
        if roles.value_col is None:
            return ()
        items = _parse_descriptive_items(
            rows, data_start, roles.value_col,
            name_col=roles.name_col, key_col=roles.key_col,
            allow_letters=roles.allow_letter_keys,
        )
        if not items:
            return ()
        return (ExtractedPlan(
            code="1",
            display_name="Schedule of Benefits",
            cover_description=cover_desc,
            items=tuple(items),
            source_row=sob_idx + 1,
        ),)

    # Check for annual policy limit (appears between SOB header and first benefit)
    annual_limit: str | None = None
    for i in range(sob_idx + 1, data_start):
        row = rows[i] or []
        text = _row_text(row).upper()
        if "ANNUAL" in text and ("LIMIT" in text or "POLICY" in text):
            for _, _, col_idx in plan_cols:
                if col_idx < len(row) and _non_empty(row[col_idx]):
                    val = _fmt_value(row[col_idx])
                    if val:
                        annual_limit = val
                        break
            break

    all_plan_cols = [c[2] for c in plan_cols]

    # Build one ExtractedPlan per plan column. `name_first` (names in col0, no
    # enumerator) is the signal for the shared-row name-first parser — its rows
    # are shared across plans so a plan whose own column is blank still emits the
    # full benefit list (GD's "As Charged" plan). Otherwise use the keyed parser;
    # when key_col wasn't resolved (e.g. a sparse sheet where detection under-
    # fired) fall back to col0, the conventional enumerator column. A genuinely
    # key-less, names-in-col1 layout that this misreads surfaces as needs_attention
    # and is correctable via the column-mapping fixer.
    key_col = roles.key_col if roles.key_col is not None else 0
    plans: list[ExtractedPlan] = []
    for code, display_name, col_idx in plan_cols:
        if roles.name_first:
            items = _parse_name_first_items(
                rows, data_start, col_idx, all_plan_cols, name_col=roles.name_col,
            )
        else:
            items = _parse_sob_items(
                rows, data_start, col_idx,
                name_col=roles.name_col, key_col=key_col,
                allow_letters=roles.allow_letter_keys,
            )
        if not items:
            continue
        plans.append(ExtractedPlan(
            code=code,
            display_name=display_name,
            cover_description=cover_desc,
            annual_policy_limit=annual_limit,
            items=tuple(items),
            source_row=sob_idx + 1,
            # The sheet's own column header, kept verbatim. A composite header
            # ("PLAN 1/U01/U04/U06") is fanned out into one plan per code
            # downstream, which rewrites display_name — this is the only place
            # the broker's original wording survives.
            source_label=display_name,
        ))

    return tuple(plans)
