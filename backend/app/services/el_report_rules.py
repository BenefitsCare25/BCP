"""Reviewed placement rules used by the full employee listing.

Missing rules stay missing. In particular, benefit payout wording is not a
premium sum-insured instruction, and salary currency is not premium currency.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.roster_attributes import age_from_attrs, roster_date

RULE_CHOICES = {
    "el_age_basis": ("ANB", "ALB"),
    "el_age_reference": ("Product cover start", "Policy year start", "Member effective date"),
    "el_premium_si_basis": ("Eligible SI", "Accepted SI"),
}
RULE_LABELS = {
    "el_age_basis": "Full EL age convention (from placement slip)",
    "el_age_reference": "Full EL age reference (from placement slip)",
    "el_premium_si_basis": "Full EL premium SI basis (from placement slip)",
    "el_currency": "Full EL premium currency (ISO code)",
    "el_admin_resolution": "Full EL administration exceptions / reviewed interpretation",
    "el_max_sum_insured": "Maximum sum insured per life (blank if not specified)",
}


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def validate_report_rules(answers: dict[str, Any]) -> None:
    header = mapping(answers.get("header"))
    for key, choices in RULE_CHOICES.items():
        if header.get(key) not in (None, "", *choices):
            raise ValueError(f"{RULE_LABELS[key]} must be one of: {', '.join(choices)}.")
    currency = header.get("el_currency")
    if currency and not re.fullmatch(r"[A-Z]{3}", str(currency)):
        raise ValueError("Full EL premium currency must be a three-letter uppercase ISO code.")
    cap = header.get("el_max_sum_insured")
    if cap not in (None, "") and positive_number(cap) is None:
        raise ValueError("Maximum sum insured must be a positive finite amount.")


def explicit_age_basis(text: str) -> str:
    anb = bool(re.search(r"\bANB\b|age\s+next\s+birthday", text, re.I))
    alb = bool(re.search(r"\bALB\b|age\s+last\s+birthday", text, re.I))
    return ("ANB" if anb else "ALB") if anb != alb else ""


def source_maximum(answers: dict[str, Any]) -> float | None:
    amounts: set[float] = set()
    for section in answers.get("sections") or []:
        for row in mapping(section).get("rows") or []:
            if not isinstance(row, list):
                continue
            text = " ".join(str(cell) for cell in row if cell not in (None, ""))
            match = re.search(
                r"maximum\s+(?:limit\s+per\s+insured\s+person|sum\s+(?:insured|assured))"
                r"\s*[:=]?\s*(?:S\$|SGD)?\s*([\d,]+(?:\.\d+)?)\s*$",
                text,
                re.I,
            )
            if match:
                number = positive_number(match.group(1).replace(",", ""))
                if number is not None:
                    amounts.add(number)
    return next(iter(amounts)) if len(amounts) == 1 else None


def source_currency(answers: dict[str, Any]) -> str:
    schedules = [
        s
        for s in answers.get("source_rate_schedules") or []
        if isinstance(s, dict) and s.get("selected") is True
    ]
    if len(schedules) != 1:
        return ""
    rows = schedules[0].get("source_rows") or []
    header = str(rows[:2])
    currencies = set(re.findall(r"\b(?:SGD|USD|EUR|GBP|MYR|HKD|AUD)\b", header.upper()))
    if "S$" in header.upper():
        currencies.add("SGD")
    return next(iter(currencies)) if len(currencies) == 1 else ""


def source_policy_terms(answers: dict[str, Any]) -> dict[str, Any]:
    """Unambiguous retained source wording; never infer a rate or age reference."""
    from app.services.slip_parsing.header import _age_from_birthday, _nel_amount

    amounts, ages = set(), set()
    for item in answers.get("terms") or []:
        item = mapping(item)
        text = f"{item.get('label', '')} {item.get('value', '')}"
        if re.search(r"non[\s-]*evidence\s+limit|free\s+cover\s+limit", text, re.I):
            amount, age = _nel_amount(text), _age_from_birthday(text)
            if amount is not None:
                amounts.add(amount)
            if age is not None:
                ages.add(int(age))
    values: dict[str, Any] = {}
    if len(amounts) == 1:
        values["free_cover_limit"] = next(iter(amounts))
    if len(ages) == 1:
        values["nel_age_limit"] = next(iter(ages))
    schedules = [
        s
        for s in answers.get("source_rate_schedules") or []
        if isinstance(s, dict) and s.get("selected") is True
    ]
    if len(schedules) == 1 and re.search(
        r"GST\s+exempt",
        str((schedules[0].get("source_rows") or [])[:2]),
        re.I,
    ):
        values["gst_included"] = False
    return values


@dataclass(frozen=True)
class ReportRules:
    admin: str = ""
    admin_resolution: str = ""
    age_basis: str = ""
    age_reference: str = ""
    premium_si_basis: str = ""
    currency: str = ""
    max_sum_insured: float | None = None

    @classmethod
    def from_answers(cls, answers: dict[str, Any]) -> ReportRules:
        header = mapping(answers.get("header"))
        eligibility = mapping(answers.get("eligibility"))
        return cls(
            admin=str(header.get("admin_basis") or ""),
            admin_resolution=str(header.get("el_admin_resolution") or ""),
            age_basis=str(
                header.get("el_age_basis")
                or explicit_age_basis(str(eligibility.get("eligibility") or ""))
            ),
            age_reference=str(header.get("el_age_reference") or ""),
            premium_si_basis=str(header.get("el_premium_si_basis") or ""),
            currency=str(header.get("el_currency") or source_currency(answers)),
            max_sum_insured=positive_number(header.get("el_max_sum_insured"))
            or source_maximum(answers),
        )

    def age(self, attrs: dict[str, Any], product_start: date, year_start: date) -> int | None:
        ref = {
            "Product cover start": product_start,
            "Policy year start": year_start,
            "Member effective date": roster_date(attrs.get("effective_date")),
        }.get(self.age_reference)
        if not isinstance(ref, date) or self.age_basis not in ("ANB", "ALB"):
            return None
        age = age_from_attrs(attrs, ref)
        if age is None or age < 0:
            return None
        return age + (1 if self.age_basis == "ANB" else 0)


def reviewed_si_caps(db: Any, policy_year_id: str) -> dict[str, float]:
    from sqlalchemy import select

    from app.models import ProductSetup

    caps = {}
    for setup in db.scalars(
        select(ProductSetup).where(
            ProductSetup.policy_year_id == policy_year_id,
            ProductSetup.status == "confirmed",
        )
    ):
        cap = ReportRules.from_answers(mapping(setup.answers)).max_sum_insured
        if cap is not None:
            caps[setup.product_code.upper()] = cap
    return caps


def prefill_report_headers(answers: dict[str, Any]) -> dict[str, Any]:
    rules = ReportRules.from_answers(answers)
    header = dict(mapping(answers.get("header")))
    for key, value in (
        ("el_age_basis", rules.age_basis),
        ("el_currency", rules.currency),
        ("el_max_sum_insured", rules.max_sum_insured),
    ):
        if not header.get(key) and value not in (None, ""):
            header[key] = str(value)
    eligibility = dict(mapping(answers.get("eligibility")))
    source_age = source_policy_terms(answers).get("nel_age_limit")
    if source_age and eligibility.get("age_limit_no_underwriting") in (None, "", "0", 0):
        eligibility["age_limit_no_underwriting"] = str(source_age)
    return {**answers, "header": header, "eligibility": eligibility}
