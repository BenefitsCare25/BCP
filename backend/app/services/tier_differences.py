"""What actually changes between two benefit schedules.

The enrollment surface asks a member to choose a plan, and until now it showed
them a direction word and a price. "Less cover — adds back S$82.84" is half a
decision: it says a switch is cheaper without saying what is given up, and the
member has no way to find out, because the coverage tab only ever shows the
plan they hold today, not the one they are considering.

This produces the other half: the rows on which two plans DIFFER, which is a
far smaller and more decidable set than either schedule. Across CDL's book it
is 1 row for GCGP, 2 for GMM, 4 for GCSP, 9 for GD, and **zero** for the life
products, whose plans share one schedule and differ only in sum insured — a
figure the tier already carries. So this renders exactly where it adds
something and stays silent where it would repeat.

It is deliberately a DIFF and not the schedule. Rendering each option's full
schedule would reproduce the coverage tab three times over inside a form, and
DESIGN.md's "one description, and one count" rule exists to stop precisely
that.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from app.services.sob_columns import benefit_row_key

# A schedule that differs on more rows than this is not a comparison any more,
# and the payload stops being cheap. Well clear of the worst real case (9).
# The caller slices to it AND reports the true total, so nothing is dropped
# without the member being told (see `CohortTierOut.differences_total`).
MAX_DIFFERENCES = 40


def _items(schedule: Any) -> list[dict[str, Any]]:
    """The benefit rows of a stored schedule, however it was written.

    ``Plan.benefit_schedule`` is untyped JSON: the materializer writes
    ``{"items": [...]}``, but seeded and hand-PATCHed rows are a bare list, and
    a malformed row must render nothing rather than break a member's page.
    """
    if isinstance(schedule, dict):
        raw = schedule.get("items")
    else:
        raw = schedule
    if not isinstance(raw, list):
        return []
    return [it for it in raw if isinstance(it, dict)]


def _text(value: Any) -> str | None:
    """A cell as text, with blank and whitespace collapsing to None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _is_annotation(value: str | None) -> bool:
    """True for a cell that is wholly a parenthetical — "(not part of 1a)".

    Insurers use the value column for a footnote on the row above as well as
    for the row's own value. Those footnotes routinely differ between plans for
    no reason a member cares about: CDL's GCSP prints "(not part of 1a)" under
    plan 1 and "(not part of 1b)" under plan 2 for the same benefit at the same
    limit — the text differs only because it cross-references a different row
    number. Reported as a change it reads as gibberish and dilutes the two rows
    on the same list that genuinely alter cover.

    Narrow on purpose: only when BOTH sides are wholly parenthetical. One side
    being an annotation and the other a real value IS a change, and "As charged"
    never wears brackets.
    """
    return bool(value) and value.startswith("(") and value.endswith(")")




# Row identity across two plans. IMPORTED, not reimplemented: this must be the
# same identity the column fold uses, or the two disagree about whether a row
# changed — which is how the copy that used to live here drifted.
_key = benefit_row_key


# The insurer's qualifying wording, which it writes in brackets inside the
# benefit name: "Panel Specialists (on cashless basis) (including Specialist
# Outpatient Clinics in Govt Restructured hospitals - on reimbursement basis)".
_PARENTHETICAL = re.compile(r"\(([^()]*)\)")

# Shorter than this a bracketed fragment is an enumerator ("(a)", "1"), not a
# qualifier, and promoting it to its own line would be noise.
_MIN_QUALIFIER = 3

# Separators a removed bracket can leave stranded at either end of the headline
# (hyphen, en/em dash, middot, comma, semicolon). Written as escapes so the
# source stays ASCII — a literal en dash beside a hyphen is unreadable in a
# character class and ruff flags it as ambiguous.
_STRANDED = re.compile(r"^[\s\-\u2013\u2014\u00b7,;]+|[\s\-\u2013\u2014\u00b7,;]+$")


def _split_qualifier(name: str) -> tuple[str, str | None]:
    """Separate a benefit's HEADLINE from the insurer's bracketed qualifiers.

    Nothing is paraphrased or dropped — every word survives, it is only placed.
    That matters because the qualifiers are load-bearing ("on cashless basis"
    is the difference between being billed and not) but they are also most of
    the string: rendered inline they turned a benefit name into a four-line
    paragraph, and the member could no longer see where one changed benefit
    ended and the next began.
    """
    quals = [
        q.strip()
        for q in _PARENTHETICAL.findall(name)
        if len(q.strip()) >= _MIN_QUALIFIER
    ]
    headline = _PARENTHETICAL.sub("", name)
    # Dashes and separators the brackets left stranded at either end.
    headline = _STRANDED.sub("", re.sub(r"\s{2,}", " ", headline))
    # A name that is nothing BUT brackets keeps its original text rather than
    # rendering as an empty row.
    return (headline or name.strip()), (" · ".join(quals) or None)


class _Row(NamedTuple):
    """One benefit row of a schedule, split into the parts the UI lays out."""

    group: str | None  # the parent benefit, when this row is a sub-item
    benefit: str  # the row's own headline
    qualifier: str | None  # the insurer's bracketed wording, parent's first
    value: str | None
    kind: str | None


def _rows(items: list[dict[str, Any]]) -> dict[str, _Row]:
    """Flatten a schedule to ``{identity: _Row}``.

    Sub-items are included because that is where several products keep the
    values that actually differ — GCSP's plans agree on every top-level benefit
    and differ only on "Panel" vs "Non Panel" underneath them, so a
    top-level-only diff reports two identical plans.

    A sub-item keeps its parent as a separate ``group`` rather than being
    joined into one string. Joined, the name read as one long sentence with no
    hierarchy; kept apart, the UI can set the parent quietly and the specific
    benefit in full ink, which is what makes a list of them scannable.
    """
    out: dict[str, _Row] = {}
    for it in items:
        raw_name = str(it.get("name") or "").strip()
        kind = _text(it.get("kind"))
        if raw_name:
            benefit, qualifier = _split_qualifier(raw_name)
            out.setdefault(
                _key(raw_name),
                _Row(None, benefit, qualifier, _text(it.get("value")), kind),
            )
        subs = it.get("sub_items")
        if not isinstance(subs, list):
            continue
        group, group_qual = _split_qualifier(raw_name) if raw_name else (None, None)
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            raw_sub = str(sub.get("name") or "").strip()
            if not raw_sub:
                continue
            benefit, sub_qual = _split_qualifier(raw_sub)
            # The identity stays the JOINED path: "Per visit" alone appears
            # under Panel, Polyclinic and Non-Panel on one GCGP schedule, so an
            # unqualified key would collide three ways.
            out.setdefault(
                _key(f"{raw_name} — {raw_sub}" if raw_name else raw_sub),
                _Row(
                    group,
                    benefit,
                    " · ".join(q for q in (group_qual, sub_qual) if q) or None,
                    _text(sub.get("value")),
                    _text(sub.get("kind")) or kind,
                ),
            )
    return out


def _entry(row: _Row, current: str | None, elected: str | None, kind: str | None):
    return {
        "group": row.group,
        "benefit": row.benefit,
        "qualifier": row.qualifier,
        "current": current,
        "elected": elected,
        "kind": row.kind or kind,
    }


def flatten_schedule(schedule: Any) -> dict[str, _Row]:
    """A stored schedule as ``{identity: _Row}``, ready to diff.

    Exposed so a caller diffing one baseline against several alternatives can
    flatten the baseline ONCE instead of once per alternative — the qualifier
    split runs a regex over every row and sub-item, and a GBT-class schedule is
    ~69 rows.
    """
    return _rows(_items(schedule))


def has_rows(schedule: Any) -> bool:
    """True when a stored schedule actually states any benefits.

    The distinction a caller must draw before diffing: a schedule that says
    NOTHING is unknown, not empty. See ``schedule_differences``.
    """
    return bool(_items(schedule))


def schedule_differences(current: Any, elected: Any) -> list[dict[str, str | None]]:
    """Rows where ``elected`` says something different from ``current``.

    Each entry is ``{group, benefit, qualifier, current, elected, kind}``; a
    ``None`` on either value side means that plan states nothing for the row,
    which the UI renders as "not covered" rather than as an empty cell.

    Rows absent from BOTH, or blank in both, are not differences. Order follows
    the elected plan's own schedule so the list reads in the insurer's document
    order, with any row only the current plan has appended after it.

    **Not truncated here.** Bounding the list is the caller's job precisely
    because the caller can report what it dropped: a cover comparison that
    silently omits rows is worse than a long one, and this is the surface a
    member decides their family's cover on.

    **A schedule that states nothing is UNKNOWN, not empty**, and the caller
    must not ask for a diff against one — see ``has_rows``. Diffing a populated
    schedule against a blank one reports every benefit as dropped, which the UI
    renders as "Not covered": a plan-to-column mapping gap presented to the
    member as total loss of cover. Guarded here as well as at the call site,
    because the failure is silent and the consequence is a cover misstatement.
    """
    return differences_from_rows(flatten_schedule(current), flatten_schedule(elected))


def differences_from_rows(
    cur: dict[str, _Row], new: dict[str, _Row]
) -> list[dict[str, str | None]]:
    """``schedule_differences`` over already-flattened schedules.

    Split out so a caller comparing one baseline against several alternatives
    flattens the baseline once (see ``flatten_schedule``).
    """
    # Either side empty means "we don't know what this plan says", never "this
    # plan covers nothing" — reporting no differences is the honest answer.
    if not cur or not new:
        return []

    out: list[dict[str, str | None]] = []
    for key, row in new.items():
        was = cur[key] if key in cur else None
        before = was.value if was else None
        if before == row.value:
            continue
        if _is_annotation(before) and _is_annotation(row.value):
            continue
        out.append(_entry(row, before, row.value, was.kind if was else None))
    # A benefit the current plan has and the elected one drops entirely is the
    # most consequential difference there is — it must never be the one the
    # diff omits by only walking the elected side.
    for key, row in cur.items():
        if key not in new and row.value is not None:
            out.append(_entry(row, row.value, None, None))
    return out
