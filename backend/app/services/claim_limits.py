"""Structured claim-limit settings stored inside a plan's benefit schedule.

The Schedule of Benefits is deliberately dynamic JSON, so the configuration
lives beside the exact plan/benefit row it describes instead of in a parallel
table that could drift after a broker edits the schedule.  Only a positive
monetary ``policy_year`` setting is enforceable.  Every other basis is display
guidance for members and assessors.

Text parsing creates broker-review suggestions only. It never creates member
balances or approval authority until a broker verifies the structured setting.
Once a ``claim_limit`` key exists on a row, that explicit setting wins --
including ``not_limit``.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.member_schedule import is_absent_value

LIMIT_BASIS_POLICY_YEAR = "policy_year"
LIMIT_BASIS_LIFETIME = "lifetime"
LIMIT_BASIS_PER_VISIT = "per_visit"
LIMIT_BASIS_PER_DAY = "per_day"
LIMIT_BASIS_PERCENTAGE = "percentage"
LIMIT_BASIS_AS_CHARGED = "as_charged"
LIMIT_BASIS_INFORMATIONAL = "informational"
# A COUNT of visits per policy year (``amount`` holds the count). Tracked and
# guarded like the annual SGD balance: every claim against the row is a visit.
LIMIT_BASIS_VISITS_PER_YEAR = "visits_per_year"
# Money per disability/admission. Shown to members and assessors; never a
# running balance, because claims aren't grouped by disability.
LIMIT_BASIS_PER_DISABILITY = "per_disability"
LIMIT_BASES = frozenset(
    {
        LIMIT_BASIS_POLICY_YEAR,
        LIMIT_BASIS_VISITS_PER_YEAR,
        LIMIT_BASIS_PER_DISABILITY,
        LIMIT_BASIS_LIFETIME,
        LIMIT_BASIS_PER_VISIT,
        LIMIT_BASIS_PER_DAY,
        LIMIT_BASIS_PERCENTAGE,
        LIMIT_BASIS_AS_CHARGED,
        LIMIT_BASIS_INFORMATIONAL,
    }
)

LIMIT_STATUS_NEEDS_REVIEW = "needs_review"
LIMIT_STATUS_VERIFIED = "verified"
LIMIT_STATUS_NOT_LIMIT = "not_limit"
LIMIT_STATUSES = frozenset(
    {LIMIT_STATUS_NEEDS_REVIEW, LIMIT_STATUS_VERIFIED, LIMIT_STATUS_NOT_LIMIT}
)

_AMOUNT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_MONETARY_CONTEXT_RE = re.compile(r"\$|\bsgd\b|\bdollars?\b", re.I)
_PER_VISIT_RE = re.compile(r"(?:/|\bper\s+)(?:visit|consult(?:ation)?)\b", re.I)
_PER_DAY_RE = re.compile(r"(?:/|\bper\s+)(?:day|night)\b|\bdaily\b", re.I)
_PER_YEAR_RE = re.compile(r"\bper\s+(?:policy\s+)?year\b|\bper\s+annum\b|/year\b", re.I)
_VISIT_COUNT_RE = re.compile(r"\b(\d{1,3})\s*visits?\b", re.I)
_PER_DISABILITY_RE = re.compile(r"\bper\s+(?:disability|admission)\b", re.I)


def parse_limit_amount(value: Any) -> float | None:
    """Return the first non-negative numeric amount in a display value."""
    if value is None or isinstance(value, bool):
        return None
    match = _AMOUNT_RE.search(str(value))
    if match is None:
        return None
    try:
        amount = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return amount if amount >= 0 else None


def has_monetary_context(value: Any) -> bool:
    """Whether wording explicitly describes money rather than a usage count."""
    text = " ".join(str(value or "").split())
    return bool(_MONETARY_CONTEXT_RE.search(text))


def infer_limit_basis(value: Any) -> str | None:
    """Classify obvious SoB limit wording for a broker-review suggestion."""
    text = " ".join(str(value or "").split())
    if is_absent_value(text):
        return None
    folded = text.casefold()
    if "as charged" in folded:
        return LIMIT_BASIS_AS_CHARGED
    if "%" in text:
        return LIMIT_BASIS_PERCENTAGE
    if _PER_DAY_RE.search(text):
        return LIMIT_BASIS_PER_DAY
    if _PER_VISIT_RE.search(text):
        return LIMIT_BASIS_PER_VISIT
    if "lifetime" in folded:
        return LIMIT_BASIS_LIFETIME
    if _VISIT_COUNT_RE.search(text) and not has_monetary_context(text):
        return LIMIT_BASIS_VISITS_PER_YEAR
    if _PER_DISABILITY_RE.search(text):
        return LIMIT_BASIS_PER_DISABILITY
    if _PER_YEAR_RE.search(text):
        return LIMIT_BASIS_POLICY_YEAR
    # A bare monetary amount historically behaved as an annual allowance. Keep
    # that behaviour during migration, but mark it for broker review.
    if parse_limit_amount(text) is not None and any(
        token in folded for token in ("$", "sgd", "limit", "maximum", "max ")
    ):
        return LIMIT_BASIS_POLICY_YEAR
    return None


def suggested_limit_setting(
    display: Any,
    *,
    claim_scope_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Build a non-authoritative setting from extracted SoB wording."""
    basis = infer_limit_basis(display)
    if basis is None:
        return None
    text = " ".join(str(display or "").split()) or None
    amount: float | None = None
    if basis == LIMIT_BASIS_POLICY_YEAR:
        amount = parse_limit_amount(text)
    elif basis == LIMIT_BASIS_VISITS_PER_YEAR:
        match = _VISIT_COUNT_RE.search(text or "")
        amount = float(match.group(1)) if match else None
    return {
        "basis": basis,
        "amount": amount,
        "currency": "SGD",
        "display": text,
        "claim_scope_codes": list(dict.fromkeys(claim_scope_codes or [])),
        "status": LIMIT_STATUS_NEEDS_REVIEW,
        "source": "detected",
    }


def suggested_structured_policy_year_setting(
    value: Any,
    *,
    claim_scope_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Build a safe suggestion from a structured ``per_policy_year`` value.

    The field can contain either money (``SGD 300``) or a usage condition
    (``5 visits``).  Its key supplies the annual period, but it does not supply
    a currency.  Counts and ambiguous bare numbers therefore remain visible to
    the broker as informational wording and can never become an SGD balance
    without an explicit broker edit.
    """
    text = " ".join(str(value or "").split())
    if is_absent_value(text):
        return None
    display = text if _PER_YEAR_RE.search(text) else f"{text} per policy year"
    if has_monetary_context(text) or "as charged" in text.casefold():
        return suggested_limit_setting(display, claim_scope_codes=claim_scope_codes)
    return {
        "basis": LIMIT_BASIS_INFORMATIONAL,
        "amount": None,
        "currency": "SGD",
        "display": display,
        "claim_scope_codes": list(dict.fromkeys(claim_scope_codes or [])),
        "status": LIMIT_STATUS_NEEDS_REVIEW,
        "source": "detected",
    }


# Which claim type a benefit row most likely funds, by row-name terms. DATA,
# not code, because the broker editor needs the same rules: they ride to it on
# the setup template (`claim_scope_match_terms`), so the suggestion a broker sees
# and the one seeded at import can never disagree. A term joined with "+" needs
# every part ("pre+post+hospital"). Families are checked in order; the first
# matching scope wins.
_SCOPE_FAMILIES: dict[str, frozenset[str]] = {
    "gp": frozenset({"GP", "GCGP", "GOGP"}),
    "sp": frozenset({"SP", "GCSP", "GOSP"}),
    "dental": frozenset({"GD", "DENTAL"}),
    "hospital": frozenset({"GHS", "GHS2", "IMP"}),
}
_SCOPE_MATCH_TERMS: dict[str, dict[str, tuple[str, ...]]] = {
    "gp": {
        "gp_tcm": ("tcm", "traditional chinese", "chinese physician"),
        "gp_physiotherapy": ("physio",),
        "standard": ("general practitioner", "outpatient gp", "gp consult"),
    },
    "sp": {"standard": ("specialist", "consultation")},
    "dental": {"standard": ("dental",)},
    "hospital": {
        "ghs_pre_post": ("pre+post+hospital",),
        "ghs_dialysis_cancer": ("dialysis", "cancer treatment"),
        "ghs_emergency_outpatient": ("emergency", "a&e", "accidental outpatient"),
        "ghs_hospitalisation": ("hospitalisation", "hospitalization", "day surgery"),
    },
}
# Rows that name a claim type's words but are a different benefit: "Overseas
# Hospitalisation due to accidental causes" is not the hospitalisation claim
# type's balance, and a funeral benefit is never member-claimed.
_SCOPE_EXCLUDE_TERMS: dict[str, tuple[str, ...]] = {
    "hospital": ("overseas", "funeral", "death"),
}


def _scope_family(product_code: str | None) -> str | None:
    code = str(product_code or "").strip().upper()
    return next((family for family, codes in _SCOPE_FAMILIES.items() if code in codes), None)


def _term_matches(term: str, name: str) -> bool:
    return all(part in name for part in term.split("+"))


def claim_scope_match_terms(product_code: str | None) -> tuple[dict[str, list[str]], list[str]]:
    """``({scope_code: terms}, exclude_terms)`` for a product, for the editor."""
    family = _scope_family(product_code)
    if family is None:
        return {}, []
    return (
        {code: list(terms) for code, terms in _SCOPE_MATCH_TERMS[family].items()},
        list(_SCOPE_EXCLUDE_TERMS.get(family, ())),
    )


def suggested_scope_codes(product_code: str | None, row_name: Any) -> list[str]:
    """Conservative claim-type suggestion for a recognizable benefit row."""
    name = " ".join(str(row_name or "").split()).casefold()
    family = _scope_family(product_code)
    if not name or family is None:
        return []
    if any(term in name for term in _SCOPE_EXCLUDE_TERMS.get(family, ())):
        return []
    for scope, terms in _SCOPE_MATCH_TERMS[family].items():
        if any(_term_matches(term, name) for term in terms):
            return [scope]
    return []


def normalize_limit_setting(raw: Any, *, fallback_display: Any = None) -> dict[str, Any] | None:
    """Shape-guard one JSON setting and discard unknown client fields."""
    if not isinstance(raw, dict):
        return None
    basis = str(raw.get("basis") or "").strip().casefold()
    status = str(raw.get("status") or "").strip().casefold()
    if basis not in LIMIT_BASES or status not in LIMIT_STATUSES:
        return None
    amount = parse_limit_amount(raw.get("amount"))
    scopes = raw.get("claim_scope_codes")
    scope_codes = [
        str(code).strip().casefold()
        for code in (scopes if isinstance(scopes, list) else [])
        if str(code).strip()
    ]
    display = raw.get("display")
    if not isinstance(display, str) or not display.strip():
        display = fallback_display if isinstance(fallback_display, str) else None
    source = str(raw.get("source") or "manual").strip().casefold()
    if source not in {"detected", "manual"}:
        source = "manual"
    currency = str(raw.get("currency") or "SGD").strip().upper()
    # Every utilization and approval figure is currently policy-currency SGD.
    # Accepting another code here would compare unlike currencies.
    if currency != "SGD":
        return None
    return {
        "basis": basis,
        # Amounts on non-annual settings are deliberately ignored by every
        # enforcement path. Keeping a cleaned value is still useful to future
        # display/export work without changing today's contract.
        "amount": amount,
        "currency": currency,
        "display": " ".join(display.split()) if isinstance(display, str) else None,
        "claim_scope_codes": list(dict.fromkeys(scope_codes)),
        "status": status,
        "source": source,
    }


def enforceable_policy_year_amount(setting: Any) -> float | None:
    """Verified numeric approval/balance limit, or ``None``.

    ``needs_review`` is a broker work item, never policy authority.  Treating a
    detected suggestion as live made an extraction guess appear to members and
    participate in the approval guard before anyone had confirmed it.
    """
    normalized = normalize_limit_setting(setting)
    if (
        normalized is None
        or normalized["basis"] != LIMIT_BASIS_POLICY_YEAR
        or normalized["status"] != LIMIT_STATUS_VERIFIED
    ):
        return None
    amount = normalized.get("amount")
    return float(amount) if amount is not None and float(amount) > 0 else None


def enforceable_visit_limit(setting: Any) -> int | None:
    """Verified visits-per-year count, or ``None`` (same fail-closed rule as
    ``enforceable_policy_year_amount``)."""
    normalized = normalize_limit_setting(setting)
    if (
        normalized is None
        or normalized["basis"] != LIMIT_BASIS_VISITS_PER_YEAR
        or normalized["status"] != LIMIT_STATUS_VERIFIED
    ):
        return None
    amount = normalized.get("amount")
    if amount is None or float(amount) < 1 or float(amount) != int(float(amount)):
        return None
    return int(float(amount))


def setting_display(setting: Any, fallback: Any = None) -> str | None:
    normalized = normalize_limit_setting(setting, fallback_display=fallback)
    if normalized is None:
        return str(fallback).strip() if fallback else None
    amount = normalized.get("amount")
    # ``display`` is the extracted source wording. Once a broker edits and
    # verifies a monetary amount, that stale wording must not outrank the value
    # they actually approved (for example, showing S$2,000 beside a S$1,500
    # balance).
    if (
        normalized["source"] == "manual"
        and normalized["basis"] == LIMIT_BASIS_POLICY_YEAR
        and amount is not None
    ):
        return f"SGD {float(amount):,.2f} per policy year"
    if normalized["basis"] == LIMIT_BASIS_VISITS_PER_YEAR and amount is not None:
        count = int(float(amount))
        return f"{count} visit{'' if count == 1 else 's'} per policy year"
    if normalized.get("display"):
        return str(normalized["display"])
    if amount is not None:
        numeric_amount = float(amount)
        if normalized["basis"] == LIMIT_BASIS_PERCENTAGE:
            return f"{numeric_amount:g}%"
        suffix = {
            LIMIT_BASIS_POLICY_YEAR: "per policy year",
            LIMIT_BASIS_LIFETIME: "lifetime",
            LIMIT_BASIS_PER_VISIT: "per visit",
            LIMIT_BASIS_PER_DAY: "per day",
            LIMIT_BASIS_PER_DISABILITY: "per disability",
        }.get(normalized["basis"])
        if suffix:
            return f"{normalized['currency']} {numeric_amount:,.2f} {suffix}"
    if normalized["basis"] == LIMIT_BASIS_AS_CHARGED:
        return "As charged"
    return str(fallback).strip() if fallback else None


def item_setting(item: dict[str, Any]) -> dict[str, Any] | None:
    """Explicit item setting. Presence of the key suppresses legacy guessing."""
    if "claim_limit" not in item:
        return None
    return normalize_limit_setting(item.get("claim_limit"), fallback_display=item.get("value"))


def item_source_wording(item: dict[str, Any]) -> str | None:
    """Current SoB wording that an item-level setting was reviewed against."""
    properties = item.get("properties")
    raw_policy_year = (
        properties.get("per_policy_year") if isinstance(properties, dict) else None
    )
    policy_year = " ".join(str(raw_policy_year or "").split())
    if not is_absent_value(policy_year):
        return policy_year if _PER_YEAR_RE.search(policy_year) else f"{policy_year} per policy year"
    value = " ".join(str(item.get("value") or "").split())
    return value or None


def _normalized_wording(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def product_setting(schedule: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schedule, dict) or "claim_limit" not in schedule:
        return None
    return normalize_limit_setting(schedule.get("claim_limit"))


def configured_benefit_row(schedule: dict[str, Any] | None, scope_code: str | None) -> str | None:
    """The uniquely configured benefit row for a claim scope."""
    wanted = str(scope_code or "standard").strip().casefold()
    for item in (schedule or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        setting = item_setting(item)
        # A detected mapping is only a suggestion in the broker editor. It must
        # not stamp a member claim with the guessed row before verification.
        # ``not_limit`` remains authoritative for attribution: the broker has
        # explicitly said the row carries policy wording but no annual balance.
        if setting is None or setting["status"] == LIMIT_STATUS_NEEDS_REVIEW:
            continue
        if wanted in setting["claim_scope_codes"]:
            name = str(item.get("name") or "").strip()
            if name:
                return name
    return None


def configured_benefit_rows(schedule: dict[str, Any] | None) -> set[str]:
    """Authoritatively mapped rows that need a bucket before first claim."""
    rows: set[str] = set()
    for item in (schedule or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        setting = item_setting(item)
        name = str(item.get("name") or "").strip()
        if (
            name
            and setting is not None
            and setting["status"] != LIMIT_STATUS_NEEDS_REVIEW
            and setting["claim_scope_codes"]
        ):
            rows.add(name)
    return rows


# An unreviewed policy-year limit is never enforced, so confirming the plan
# with one would put it live with no annual cap on claims. Fail closed at
# confirm and tell the broker what resolves it.
_UNREVIEWED_LIMIT = (
    "{name}: detected policy-year limit still needs review. "
    "Verify the amount or mark it informational."
)


def validate_schedule_limits(
    schedule: dict[str, Any], *, valid_scope_codes: set[str] | frozenset[str]
) -> list[str]:
    """Return actionable configuration errors for one materialized plan."""
    errors: list[str] = []
    owners: dict[str, str] = {}
    root = product_setting(schedule)
    if "claim_limit" in schedule and root is None:
        errors.append("Overall plan limit has an invalid setting.")
    if root and root["basis"] == LIMIT_BASIS_POLICY_YEAR:
        if root["status"] == LIMIT_STATUS_NEEDS_REVIEW:
            errors.append(_UNREVIEWED_LIMIT.format(name="Overall plan limit"))
        elif (
            root["status"] == LIMIT_STATUS_VERIFIED
            and enforceable_policy_year_amount(root) is None
        ):
            errors.append("Overall policy-year limit needs an amount greater than zero.")

    for index, item in enumerate(schedule.get("items") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"Benefit line {index + 1}").strip()
        if "claim_limit" not in item:
            continue
        setting = item_setting(item)
        if setting is None:
            errors.append(f"{name}: invalid claim-limit setting.")
            continue
        if (
            setting["status"] in {LIMIT_STATUS_VERIFIED, LIMIT_STATUS_NOT_LIMIT}
            and _normalized_wording(setting.get("display"))
            != _normalized_wording(item_source_wording(item))
        ):
            errors.append(
                f"{name}: Schedule of Benefits wording changed; "
                "review the claim-limit setting again."
            )
        if setting["basis"] == LIMIT_BASIS_POLICY_YEAR:
            if setting["status"] == LIMIT_STATUS_NEEDS_REVIEW:
                errors.append(_UNREVIEWED_LIMIT.format(name=name))
            elif (
                setting["status"] == LIMIT_STATUS_VERIFIED
                and enforceable_policy_year_amount(setting) is None
            ):
                errors.append(f"{name}: policy-year limit needs an amount greater than zero.")
        if setting["basis"] == LIMIT_BASIS_VISITS_PER_YEAR:
            if setting["status"] == LIMIT_STATUS_NEEDS_REVIEW:
                errors.append(
                    f"{name}: detected visit limit still needs review. "
                    "Confirm the number of visits or mark it informational."
                )
            elif (
                setting["status"] == LIMIT_STATUS_VERIFIED
                and enforceable_visit_limit(setting) is None
            ):
                errors.append(f"{name}: visit limit needs a whole number of visits.")
        for scope in setting["claim_scope_codes"]:
            if scope not in valid_scope_codes:
                errors.append(f"{name}: unknown claim type '{scope}'.")
                continue
            previous = owners.get(scope)
            if previous and previous != name:
                errors.append(f"Claim type '{scope}' is mapped to both {previous} and {name}.")
            else:
                owners[scope] = name
    return errors
