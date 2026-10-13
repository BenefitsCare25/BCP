"""Names and details for the plans a placement slip does not name.

A slip names its plans in the Schedule of Benefits column headers ("PLAN 1",
"Plan A - International / Asia"). Sum-assured products (GTL, GPA, GCI, WICA)
usually have no plan column: one schedule serves every category and the
categories differ only in their basis of cover. The parser then had nothing to
call the plan but the section heading ("Schedule of Benefits") or a category's
wording ("Directors"), neither of which is a plan name.

Here a plan the slip does not name is called by its code ("Plan 1"), or by the
plan its basis names ("Plan 1 - $50,000"). A plan whose categories state a
basis also gets that basis in short form as its details ("24 times last drawn
basic monthly salary" -> "24x BMS"), stored as the plan's insurer report label.
Nothing here depends on the product type: it reads only the slip's own plan
headers and category bases, so every slip layout is named the same way.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from app.services.slip_parsing.models import ExtractedCategory, ExtractedPlan

# Words of a Schedule-of-Benefits heading, never of a plan name: a "name" made
# only of these ("Schedule of Benefits", "SCHEDULE OF BENEFITS / DEFINITION",
# "Limits / Coverage", "Amount (S$)") names nothing.
_HEADING_WORDS = frozenset({
    "schedule", "of", "benefit", "benefits", "sob", "definition", "definitions",
    "limit", "limits", "coverage", "cover", "insurer", "response", "and",
    "description", "details", "amount", "amounts", "plan", "plans", "s", "sgd",
})
_WORD = re.compile(r"[a-z0-9]+")
# A plan code that is a word ("Regional", "Gold") rather than a code ("1", "B2").
_WORD_CODE = re.compile(r"[A-Za-z]{4,}")

# "Plan 1 - $50,000": a basis that names the plan before stating the cover.
_STATED_PLAN = re.compile(
    "^\\s*plan\\s+([a-z0-9]+)\\s*[-:\u2013\u2014]\\s*(.+)$", re.IGNORECASE
)
# A plain amount, as a slip or a broker writes it: "100000.0", "1,000,000",
# "S$10,000". Mirrors plan_hydration._PLAIN_AMOUNT.
_PLAIN_AMOUNT = re.compile(r"(S?\$)?\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
# Shortenings brokers use, applied in order. The bracketed abbreviation goes
# first so "Basic Monthly Salary (LDBMS)" doesn't become "BMS (BMS)".
_SHORTENINGS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s*\(\W*(?:ld)?bms\W*\)", re.IGNORECASE), ""),
    (
        re.compile(
            r"\b(?:last[\s-]*drawn\s+)?basic\s+monthly\s+salary\b|\b(?:ld)?bms\b",
            re.IGNORECASE,
        ),
        "BMS",
    ),
    (
        re.compile(
            r"\s*[,;]?\s*\b(?:subject\s+to\s+(?:a\s+)?maximum|capped\s+at|up\s+to\s+"
            r"(?:a\s+)?maximum)(?:\s+sum\s+insured)?(?:\s+of)?\b",
            re.IGNORECASE,
        ),
        ", max",
    ),
    (
        re.compile(
            r"\s*[,;]?\s*\b(?:with|subject\s+to)\s+(?:a\s+)?minimum"
            r"(?:\s+sum\s+insured)?(?:\s+of)?\b",
            re.IGNORECASE,
        ),
        ", min",
    ),
    # "24 times …" / "36 X …" -> "24x …". The lookbehind keeps the digits of a
    # grouped amount ("S$100,000 x 2") from reading as a multiple.
    (re.compile(r"(?<![\d,.])(\d+(?:\.\d+)?)\s*(?:x|\u00d7|times)\s+", re.IGNORECASE), r"\1x "),
)
# A plan's details are what sets it apart; more distinct bases than this on one
# plan means the plan is shared, not described by any of them.
_MAX_BASES = 3
_DETAILS_MAX = 255


def plan_name(code: str) -> str:
    """"Plan 1" for code "1", "Plan U01" for "U01". A code that already reads
    as a plan ("Plan A - International / Asia") or is a word ("Regional") is its
    own name, never "Plan Plan A …" or "Plan Regional"."""
    text = code.strip()
    if text.lower().startswith("plan") or _WORD_CODE.fullmatch(text):
        return text
    return f"Plan {text}"


def is_placeholder_name(name: str | None) -> bool:
    """True when ``name`` is blank or only Schedule-of-Benefits heading words."""
    words = _WORD.findall((name or "").lower())
    return all(word in _HEADING_WORDS for word in words)


def _norm(text: str | None) -> str:
    return " ".join(_WORD.findall((text or "").lower()))


def _amount(prefix: str | None, digits: str) -> str:
    value = float(digits.replace(",", ""))
    shown = f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"
    return f"{(prefix or '$').upper()}{shown}"


def basis_details(basis: object) -> str | None:
    """A basis of cover in the short form brokers write on a plan.

    "24 times last drawn basic monthly salary" -> "24x BMS";
    "36 x LDBMS, subject to a maximum of S$1,000,000" -> "36x BMS, max S$1,000,000";
    "100000.0" -> "$100,000". The currency is the slip's own, or a plain "$"
    when the cell held a bare number. None for a blank basis.
    """
    if isinstance(basis, bool) or basis is None:
        return None
    if isinstance(basis, (int, float)):
        return _amount(None, str(basis)) if basis > 0 else None
    text = " ".join(str(basis).split())
    if not text:
        return None
    amount = _PLAIN_AMOUNT.fullmatch(text)
    if amount:
        return _amount(amount.group(1), amount.group(2))
    for pattern, short in _SHORTENINGS:
        text = pattern.sub(short, text)
    text = re.sub(r"\s+,", ",", re.sub(r",\s*,", ",", text))
    return " ".join(text.split()).strip(" ,;") or None


def stated_plan(basis: object) -> tuple[str | None, object]:
    """``("Plan 1", "$50,000")`` from "Plan 1 - $50,000"; ``(None, basis)``
    for a basis that names no plan."""
    match = _STATED_PLAN.match(str(basis)) if isinstance(basis, str) else None
    if match is None:
        return None, basis
    return f"Plan {match.group(1)}", match.group(2)


def describing_bases(scoped: Iterable[tuple[object, str | None]]) -> list[object]:
    """The bases that describe a plan, from ``(basis, member_scope)`` pairs of
    the categories citing it: the employees' when any cite it, since a
    dependant row sharing the code (a voluntary spouse table numbered from 1
    again) states the dependant's cover, not the plan's."""
    pairs = list(scoped)
    staff = [basis for basis, scope in pairs if scope != "dependant"]
    return staff or [basis for basis, _ in pairs]


def plan_details(bases: Iterable[object]) -> str | None:
    """One plan's details from the bases of the categories it covers: their
    distinct short forms joined, or None when none is stated or too many
    differ for any one to describe the plan."""
    shown = list(dict.fromkeys(
        details
        for basis in bases
        if (details := basis_details(stated_plan(basis)[1]))
    ))
    if not shown or len(shown) > _MAX_BASES:
        return None
    return " / ".join(shown)[:_DETAILS_MAX]


def generated_name(
    code: str, current: str | None, category_texts: Iterable[str], bases: Iterable[object]
) -> str:
    """The name of a plan the slip does not name in a column header.

    A real name already given (an AI-read "Plan A - International", a fanned
    "Plan U01") is kept; a heading or one of the plan's own category wordings
    is replaced by the plan the basis names, else by ``plan_name(code)``.
    """
    texts = {_norm(text) for text in category_texts}
    if current and not is_placeholder_name(current) and _norm(current) not in texts:
        return current
    stated = {name for basis in bases if (name := stated_plan(basis)[0])}
    return stated.pop() if len(stated) == 1 else plan_name(code)


def name_plans(
    plans: Iterable[ExtractedPlan], categories: Iterable[ExtractedCategory]
) -> tuple[ExtractedPlan, ...]:
    """Every plan with a name the broker recognises and, where its categories
    state a basis, its details. A plan named by a slip column header keeps that
    name; its details are still filled from the basis."""
    cats = list(categories)
    out: list[ExtractedPlan] = []
    for plan in plans:
        code = plan.code.strip()
        own = [c for c in cats if (c.plan_code or "").strip() == code]
        bases = describing_bases((c.basis, c.member_scope) for c in own)
        named = bool(plan.source_label) and not is_placeholder_name(plan.source_label)
        name = plan.display_name if named else generated_name(
            code, plan.display_name, (c.category for c in own), bases
        )
        out.append(replace(
            plan, display_name=name, details=plan.details or plan_details(bases)
        ))
    return tuple(out)
