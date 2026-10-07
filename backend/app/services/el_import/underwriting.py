"""Carry the Employee Listing's underwriting columns onto open cases.

Cases themselves come from the underwriting sync: a case opens only where the
eligible sum insured exceeds what is guaranteed — the non-evidence limit or,
for existing cover, the listing's present sum insured — so unchanged cover is
never re-underwritten. This step adds what the listing knows about those
cases: letter dates, the last accepted standard amount, loading wording and the
workflow position.

An MU decision in the listing refers to an earlier amount (the listing keeps
charging the eligible amount while the increase is outstanding), so it is noted
on the case rather than recorded as the insurer's decision on the increase.
Cases a broker has already edited in the app are left alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PolicyYear
from app.models.employee_listing import ListingAssignment
from app.models.underwriting_case import (
    ReviewStatus,
    UnderwritingCase,
    UnderwritingReview,
)
from app.schemas.underwriting_reporting import UnderwritingReportDetails

_UW_ROLES = ("mu_status", "mu_decision", "mu_letter_member", "mu_letter_insurer",
             "last_accepted_si", "loading_rate", "acceptance_date")


@dataclass
class ListingUnderwritingResult:
    updated: int = 0
    skipped_broker_edited: int = 0
    no_open_case: int = 0


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _amount(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _review_status(recorded: dict[str, Any]) -> str | None:
    if "pending" not in str(recorded.get("mu_status") or "").lower():
        return None
    if _date(recorded.get("mu_letter_insurer")):
        return ReviewStatus.pending_insurer
    if _date(recorded.get("mu_letter_member")):
        return ReviewStatus.pending_employee
    return ReviewStatus.pending_requirements


def _details(case: UnderwritingCase, recorded: dict[str, Any]) -> dict[str, Any] | None:
    existing = recorded.get("present_si") is not None
    member, insurer = (
        ("renewal_member_letter_date", "renewal_insurer_letter_date") if existing
        else ("new_member_letter_date", "new_insurer_letter_date")
    )
    decision = str(recorded.get("mu_decision") or "").lower()
    last_standard = _amount(recorded.get("last_accepted_si"))
    if last_standard is None and "standard" in decision and "sub" not in decision:
        last_standard = _amount(recorded.get("present_si"))
    merged = dict(case.report_details or {})
    for key, value in (
        (member, _date(recorded.get("mu_letter_member"))),
        (insurer, _date(recorded.get("mu_letter_insurer"))),
        ("last_standard_accepted_si", last_standard),
        ("health_loading", str(recorded.get("loading_rate") or "")[:200] or None),
    ):
        if value is not None and merged.get(key) in (None, ""):
            merged[key] = value
    try:
        return UnderwritingReportDetails.model_validate(merged).model_dump(
            mode="json", exclude_none=True
        )
    except ValidationError:
        return None


def apply_listing_underwriting(db: Session, year: PolicyYear) -> ListingUnderwritingResult:
    """Copy listed underwriting state onto this year's open cases. Flushes only."""
    result = ListingUnderwritingResult()
    cases = {
        (c.employee_id or c.dependant_id or "", c.product_id): c
        for c in db.execute(
            select(UnderwritingCase).where(UnderwritingCase.policy_year_id == year.id)
        ).scalars()
    }
    reviews = {
        r.id: r for r in db.execute(
            select(UnderwritingReview).where(UnderwritingReview.policy_year_id == year.id)
        ).scalars()
    }
    for assignment in db.execute(
        select(ListingAssignment).where(ListingAssignment.policy_year_id == year.id)
    ).scalars():
        recorded = assignment.recorded or {}
        if not any(recorded.get(role) not in (None, "") for role in _UW_ROLES):
            continue
        case = cases.get((assignment.member_key[2:], assignment.product_id))
        if case is None:
            result.no_open_case += 1
            continue
        if case.modified_by is not None:
            result.skipped_broker_edited += 1
            continue
        if (details := _details(case, recorded)) is not None:
            case.report_details = details
        decision = str(recorded.get("mu_decision") or "").strip()
        if decision and not case.remarks:
            accepted_on = _date(recorded.get("acceptance_date"))
            when = f" on {accepted_on:%d/%m/%Y}" if accepted_on else ""
            case.remarks = (f"Employee Listing: earlier MU decision '{decision}'{when}; "
                            "confirm the decision on the current increase.")[:1024]
        review = reviews.get(case.review_id or "")
        status = _review_status(recorded)
        if review is not None and status and review.modified_by is None and (
            review.status == ReviewStatus.pending_requirements
        ):
            review.status = status
        result.updated += 1
    db.flush()
    return result
