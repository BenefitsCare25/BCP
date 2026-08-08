"""Utilization — computed-on-read claim usage against limits.

No persisted counters: claim volume per employee is tiny, so every read sums
the live rows. Buckets are keyed ``(product_code, benefit_key)``:

- ``approved``  = Σ amount_approved of approved claims.
- ``pending``   = Σ claimed (or converted) amounts of in-flight claims —
  shown separately and NEVER subtracted from remaining (a pending claim may
  be rejected).
- ``remaining`` = limit - approved, when a numeric limit is known, and it
  **never goes below zero**. A benefit pays UP TO its limit: a member with S$500
  left who presents a S$700 bill utilises S$500 and pays the rest themselves, so
  "S$200 over the limit" is not a position anyone is in and reporting one is an
  indication of something that cannot happen. (Reachable on paper two ways: an
  acknowledged broker override past the guard, and pro-ration shrinking a
  leaver's allowance below what was already reimbursed.) The same floor applies
  to the flex wallet, its categories and `flex_ledger.MemberFlex.balance`, so no
  two surfaces can disagree about what a member has left.

Flex chain: tier wallet → minus enrollment price-tags (``flex_balance``) →
minus approved flex claims → ``available``. Per-category rows track the
scheme's ``sub_limit``s.

Shared by the member (`GET /portal/utilization`) and broker
(`GET /employees/{id}/utilization`) endpoints, and by the approve-guard in
the claim decision endpoint.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Employee
from app.models.claim import (
    CLAIM_KIND_FLEX,
    CLAIM_KIND_INSURED,
    CLAIM_STATUS_DRAFT,
    CLAIM_STATUS_REJECTED,
    CLAIM_STATUSES,
    SETTLED_STATUSES,
)
from app.schemas.api import BenefitStatementOut
from app.schemas.claims import (
    FlexCategoryUtilization,
    FlexProration,
    FlexUtilization,
    UtilizationBucket,
    UtilizationOut,
)
from app.services.member_statement import build_member_statement

# In-flight claims that may still consume the limit.
#
# Subtracts the WHOLE settled set, not just `approved`. Derived by subtraction,
# so a status added to the model lands here by default — which is right for a
# new in-flight state and catastrophically wrong for a new settled one: the
# claim would drop out of `approved` (which is subtracted from the limit) into
# `pending` (which is reported beside it and never subtracted), handing the
# member back a limit they have already spent.
PENDING_STATUSES = frozenset(
    CLAIM_STATUSES - {CLAIM_STATUS_DRAFT, CLAIM_STATUS_REJECTED} - SETTLED_STATUSES
)

_AMOUNT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# A benefit-item value only counts as an ANNUAL limit when it's a plain amount
# or explicitly per-year — "S$650/day", "80% co-pay", "As charged" are not.
_PER_UNIT_RE = re.compile(
    r"(/|\bper\b(?!\s+(?:year|annum|policy\s+year)))", re.IGNORECASE
)


def parse_limit_amount(text: str | None) -> float | None:
    """Numeric value out of a limit string ('S$1,000,000' → 1000000.0);
    None for non-numeric limits ('As charged')."""
    if not text:
        return None
    m = _AMOUNT_RE.search(text)
    if m is None:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _annual_benefit_limit(value: str | None) -> float | None:
    """Parse a Schedule-of-Benefits item value as an annual limit — only when
    it isn't qualified per-day/per-visit/percentage."""
    if not value or "%" in value or _PER_UNIT_RE.search(value):
        return None
    return parse_limit_amount(value)


def _limit_unparsed(limit: float | None, display: str | None) -> bool:
    """True when a limit text with digits exists but no annual limit was
    derived — the approve-guard is silently inactive for this bucket and the
    broker should know that's not the same as 'unlimited'."""
    return limit is None and bool(display) and any(c.isdigit() for c in display)


def _claim_amount(claim: Claim) -> float:
    return float(claim.amount_converted or claim.amount_claimed)


def _countable_claims(db: Session, employee: Employee) -> list[Claim]:
    return list(
        db.execute(
            select(Claim).where(
                Claim.employee_id == employee.id,
                Claim.policy_year_id == employee.policy_year_id,
                Claim.status.in_(PENDING_STATUSES | SETTLED_STATUSES),
            )
        ).scalars()
    )


def _bucket_sums(
    claims: list[Claim],
) -> dict[tuple[str | None, str | None], dict[str, float | int]]:
    sums: dict[tuple[str | None, str | None], dict[str, float | int]] = defaultdict(
        lambda: {"approved": 0.0, "pending": 0.0, "count": 0}
    )

    def _add(key: tuple[str | None, str | None], claim: Claim) -> None:
        row = sums[key]
        row["count"] += 1
        if claim.status in SETTLED_STATUSES:
            row["approved"] += float(claim.amount_approved or 0.0)
        else:
            row["pending"] += _claim_amount(claim)

    for claim in claims:
        if claim.claim_kind != CLAIM_KIND_INSURED:
            continue
        _add((claim.product_code, None), claim)  # product-level roll-up
        if claim.benefit_key:
            _add((claim.product_code, claim.benefit_key.strip()), claim)
    return sums


def _insured_buckets(
    statement: BenefitStatementOut, claims: list[Claim]
) -> list[UtilizationBucket]:
    sums = _bucket_sums(claims)
    buckets: list[UtilizationBucket] = []
    seen_products: set[str] = set()

    for line in statement.coverage:
        seen_products.add(line.product_code)
        product_sum = sums.pop((line.product_code, None), None) or {
            "approved": 0.0, "pending": 0.0, "count": 0,
        }
        limit = parse_limit_amount(line.annual_policy_limit)
        buckets.append(
            UtilizationBucket(
                product_code=line.product_code,
                product_name=line.product_name,
                benefit_key=None,
                limit=limit,
                limit_display=line.annual_policy_limit,
                approved=round(float(product_sum["approved"]), 2),
                pending=round(float(product_sum["pending"]), 2),
                remaining=(
                    max(0.0, round(limit - float(product_sum["approved"]), 2))
                    if limit is not None
                    else None
                ),
                claim_count=int(product_sum["count"]),
                limit_unparsed=_limit_unparsed(limit, line.annual_policy_limit),
            )
        )

        items = (line.benefit_schedule or {}).get("items") or []
        item_values = {
            str(i.get("name", "")).strip().lower(): i.get("value")
            for i in items
            if isinstance(i, dict)
        }
        for (product, key), row in sorted(
            ((k, v) for k, v in sums.items() if k[0] == line.product_code and k[1]),
            key=lambda kv: kv[0][1] or "",
        ):
            item_limit = _annual_benefit_limit(item_values.get(key.lower()))
            item_display = (
                str(item_values[key.lower()])
                if key.lower() in item_values and item_values[key.lower()]
                else None
            )
            buckets.append(
                UtilizationBucket(
                    product_code=product,
                    product_name=line.product_name,
                    benefit_key=key,
                    limit=item_limit,
                    limit_display=item_display,
                    approved=round(float(row["approved"]), 2),
                    pending=round(float(row["pending"]), 2),
                    remaining=(
                        max(0.0, round(item_limit - float(row["approved"]), 2))
                        if item_limit is not None
                        else None
                    ),
                    claim_count=int(row["count"]),
                    limit_unparsed=_limit_unparsed(item_limit, item_display),
                )
            )
            sums.pop((product, key), None)

    # Claims against products no longer on the statement (coverage changed
    # after submission) still surface — the broker needs to see them.
    for (product, key), row in sorted(
        sums.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")
    ):
        if product in seen_products and key is None:
            continue
        buckets.append(
            UtilizationBucket(
                product_code=product,
                product_name=None,
                benefit_key=key,
                limit=None,
                limit_display=None,
                approved=round(float(row["approved"]), 2),
                pending=round(float(row["pending"]), 2),
                remaining=None,
                claim_count=int(row["count"]),
                orphaned=True,
            )
        )
    return buckets


def _flex_utilization(
    statement: BenefitStatementOut, claims: list[Claim]
) -> FlexUtilization | None:
    flex = statement.flex
    if flex is None:
        return None

    approved = 0.0
    pending = 0.0
    per_category: dict[str, dict[str, float]] = defaultdict(
        lambda: {"approved": 0.0, "pending": 0.0}
    )
    for claim in claims:
        if claim.claim_kind != CLAIM_KIND_FLEX:
            continue
        cat = (claim.flex_category_name or "").strip()
        if claim.status in SETTLED_STATUSES:
            amount = float(claim.amount_approved or 0.0)
            approved += amount
            per_category[cat.lower()]["approved"] += amount
        else:
            amount = _claim_amount(claim)
            pending += amount
            per_category[cat.lower()]["pending"] += amount

    # Chain: wallet → minus price-tags (flex_balance) → minus approved claims.
    #
    # CLAIMS CANNOT TAKE THIS BELOW ZERO. A flex wallet pays UP TO the limit — a
    # member with S$500 left who presents a S$700 bill utilises S$500 and pays
    # the rest themselves — so "overspent by S$200" is not a state the product
    # can be in, and reporting one is an indication of something that cannot
    # happen. (It is reachable on paper only because pro-ration binds forward: it
    # can shrink an allowance below what was already reimbursed, and never
    # reaches back for that money.)
    #
    # A NEGATIVE `base` is a different thing and stays signed: the member's
    # elected cover costs more than their wallet, which is a real broker-facing
    # state the enrolment guard and the bulk `flex_overdraft` warning both exist
    # for. Flooring that too would hide it. `flex_ledger.MemberFlex.balance`
    # splits the same way, so the reports and this can never disagree about what
    # a member has left.
    base = flex.flex_balance if flex.flex_balance is not None else flex.wallet_amount
    if base is None:
        available = None
    else:
        drawn_down = round(float(base) - approved, 2)
        available = drawn_down if base < 0 else max(0.0, drawn_down)

    categories: list[FlexCategoryUtilization] = []
    for cat in flex.benefit_categories:
        if not cat.claimable:
            continue
        row = per_category.pop(cat.name.strip().lower(), {"approved": 0.0, "pending": 0.0})
        categories.append(
            FlexCategoryUtilization(
                name=cat.name,
                sub_limit=cat.sub_limit,
                approved=round(row["approved"], 2),
                pending=round(row["pending"], 2),
                # Floors at 0 for the same reason the wallet does: a sub-limit
                # pays UP TO its cap, so "SGD -200 left" is not a quantity
                # anyone has. Claims beyond the cap are the member's own cost.
                remaining=(
                    max(0.0, round(cat.sub_limit - row["approved"], 2))
                    if cat.sub_limit is not None
                    else None
                ),
            )
        )
    # Claims naming a category not on the current scheme still show up.
    for name, row in sorted(per_category.items()):
        if row["approved"] == 0.0 and row["pending"] == 0.0:
            continue
        categories.append(
            FlexCategoryUtilization(
                name=name,
                sub_limit=None,
                approved=round(row["approved"], 2),
                pending=round(row["pending"], 2),
                remaining=None,
            )
        )

    return FlexUtilization(
        currency=flex.currency,
        wallet_amount=flex.wallet_amount,
        proration=(
            FlexProration(**flex.proration.model_dump())
            if flex.proration is not None
            else None
        ),
        price_tags_total=flex.price_tags_total,
        flex_balance=flex.flex_balance,
        approved=round(approved, 2),
        pending=round(pending, 2),
        available=available,
        categories=categories,
    )


def build_utilization(db: Session, employee: Employee) -> UtilizationOut:
    statement = build_member_statement(db, employee)
    claims = _countable_claims(db, employee)
    return UtilizationOut(
        policy_year_id=employee.policy_year_id,
        insured=_insured_buckets(statement, claims),
        flex=_flex_utilization(statement, claims),
    )


def remaining_for_claim(db: Session, claim: Claim, employee: Employee) -> float | None:
    """The tightest applicable remaining amount for this claim's bucket —
    the approve-guard input. None = no numeric limit is known (no guard).

    ``approved`` sums only already-approved claims, so the claim being decided
    never counts against itself.
    """
    utilization = build_utilization(db, employee)
    candidates: list[float] = []
    if claim.claim_kind == CLAIM_KIND_FLEX:
        flex = utilization.flex
        if flex is not None:
            if flex.available is not None:
                candidates.append(flex.available)
            wanted = (claim.flex_category_name or "").strip().lower()
            for cat in flex.categories:
                if cat.name.strip().lower() == wanted and cat.remaining is not None:
                    candidates.append(cat.remaining)
    else:
        wanted_key = (claim.benefit_key or "").strip().lower()
        for bucket in utilization.insured:
            if bucket.product_code != claim.product_code:
                continue
            if bucket.benefit_key is None or (
                wanted_key and bucket.benefit_key.strip().lower() == wanted_key
            ):
                if bucket.remaining is not None:
                    candidates.append(bucket.remaining)
    return min(candidates) if candidates else None
