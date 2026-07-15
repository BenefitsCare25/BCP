"""Seed flex-tier match sets from AI-extracted eligibility against the roster.

After a Flex document is extracted, each tier carries free-text eligibility
(``employee_type.raw``) and, sometimes, a numeric job-grade band. Employees are
matched, however, against the roster's *actual* vocabulary — which rarely matches
the document verbatim. This module bridges the two: it pre-fills each tier's
roster-anchored match sets (``match_grades`` / ``match_designations``) from the
extraction, and records any eligibility token it could NOT map to a roster value
as ``employee_type.unresolved`` so the broker maps it by hand.

Seeding is a convenience the broker reviews — it favours PRECISION (exact,
token-level matches) over recall, so an unmappable term surfaces as unresolved
rather than being silently mis-assigned.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.flex_membership import (
    RosterVocab,
    _coerce_int,
    _normalize_label,
    _tier_has_match_sets,
)

# Separators that split a compound eligibility phrase into candidate tokens
# ("Executive to AM and Secretary" → Executive | AM | Secretary). A hyphen is
# deliberately excluded so grade ranges like "8-17" stay intact.
_TOKEN_SPLIT = re.compile(r"\s*(?:,|;|/|\band\b|&|\bto\b)\s*", re.IGNORECASE)
# A token worth flagging as an unresolved designation: has a letter and isn't a
# pure grade phrase ("job grade 8", "jg8").
_GRADE_PHRASE = re.compile(r"^(job\s*grade|grade|jg|band)\s*\d", re.IGNORECASE)


def _grade_in_band(num: int | None, lo: int | None, hi: int | None) -> bool:
    if num is None:
        return False
    if lo is not None and num < lo:
        return False
    if hi is not None and num > hi:
        return False
    return lo is not None or hi is not None


def _designation_tokens(text: str) -> list[str]:
    """Candidate designation tokens from a tier's eligibility text."""
    tokens: list[str] = []
    for part in _TOKEN_SPLIT.split(text):
        t = part.strip()
        if t and t not in tokens:
            tokens.append(t)
    return tokens


def seed_tier_match_sets(scheme: dict[str, Any], vocab: RosterVocab) -> None:
    """Mutate ``scheme`` in place, seeding each tier's match sets from the roster.

    Tiers already carrying match sets (a broker reconciled them) are left alone.
    """
    desig_by_norm = {_normalize_label(v.value): v.value for v in vocab.designations}
    graded_values = [(v.value, _coerce_int(v.value)) for v in vocab.grades]

    for tier in scheme.get("tiers") or []:
        if not isinstance(tier, dict) or _tier_has_match_sets(tier):
            continue
        et = tier.get("employee_type")
        if not isinstance(et, dict):
            et = {}
            tier["employee_type"] = et

        # The eligibility text is the source of truth; fall back to the tier name
        # only when the document gave no separate eligibility phrase.
        raw = str(et.get("raw") or "").strip() or str(tier.get("name") or "")
        raw_norm = _normalize_label(raw)

        # Grades: select roster grade values whose numeric coercion falls in band.
        lo, hi = _coerce_int(et.get("job_grade_min")), _coerce_int(et.get("job_grade_max"))
        matched_grades = [
            value for value, num in graded_values if _grade_in_band(num, lo, hi)
        ]

        # Designations: select any roster value that appears as a WHOLE phrase in
        # the eligibility text (word-boundary), so multi-word roster titles like
        # "Sales and Marketing Manager" seed correctly instead of being split into
        # non-matching tokens.
        matched_desigs = [
            v.value
            for v in vocab.designations
            if len(_normalize_label(v.value)) >= 2
            and re.search(rf"\b{re.escape(_normalize_label(v.value))}\b", raw_norm)
        ]
        # Drop a shorter title fully contained (as a whole phrase) in a longer
        # match — e.g. don't also select "Manager" when "General Manager" matched.
        matched_desigs = [
            m
            for m in matched_desigs
            if not any(
                other != m
                and re.search(
                    rf"\b{re.escape(_normalize_label(m))}\b", _normalize_label(other)
                )
                for other in matched_desigs
            )
        ]

        # Unresolved: eligibility tokens that name a designation the roster has no
        # value for (and aren't already covered by a selected title). Suppressed on
        # grade-resolved tiers, whose text is a band label rather than titles.
        covered = _normalize_label(" ".join(matched_desigs))
        unresolved: list[str] = []
        for token in _designation_tokens(raw):
            tn = _normalize_label(token)
            if (
                not tn
                or not re.search(r"[a-z]", token, re.IGNORECASE)
                or _GRADE_PHRASE.match(token)
                or tn in desig_by_norm
                or (covered and re.search(rf"\b{re.escape(tn)}\b", covered))
            ):
                continue
            if token not in unresolved:
                unresolved.append(token)

        et["match_grades"] = matched_grades
        et["match_designations"] = matched_desigs
        if unresolved and not matched_grades:
            et["unresolved"] = unresolved
        else:
            et.pop("unresolved", None)
