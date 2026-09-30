"""Grouped dashboard counts scoped to explicitly authorized benefit years."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.category import Category
from app.models.claim import Claim
from app.models.claim_message import AUTHOR_MEMBER, ClaimMessage
from app.models.dependant import DEPENDANT_STATUS_PENDING, Dependant
from app.models.employee import Employee
from app.models.enrollment_window import EnrollmentWindow, WindowStatus
from app.models.member_enquiry import MemberEnquiry
from app.models.product import Product
from app.models.product_setup import ProductSetup
from app.models.underwriting_case import (
    DECIDED_UW_STATUSES,
    OPEN_REVIEW_STATUSES,
    UnderwritingCase,
    UnderwritingReview,
    normalize_uw_status,
)
from app.services.claim_filters import claim_insurer_filter
from app.services.product_insurer import _captured, _legacy, _norm

_CLAIMS_TO_REVIEW = ("submitted", "ai_verified", "ai_flagged")


def collect_counts(
    db: Session, year_ids: list[str], today: date,
    insurer: str | None, placements: dict[tuple[str, str], str],
) -> dict[str, Any]:
    return {
        "member_count": _grouped_count(
            db, Employee.policy_year_id, Employee, year_ids, Employee.status == "active",
        ),
        "dependant_count": _grouped_count(
            db, Dependant.policy_year_id, Dependant, year_ids, Dependant.status == "active",
        ),
        "dependants_pending": _grouped_count(
            db, Dependant.policy_year_id, Dependant, year_ids,
            Dependant.status == DEPENDANT_STATUS_PENDING,
        ),
        "underwriting_pending": _underwriting_by_year(db, year_ids),
        "messages_awaiting_reply": _messages_awaiting_reply_by_year(db, year_ids),
        "employees_unmatched": _unmatched_by_year(db, year_ids),
        "stale_years": _stale_matching_years(db, year_ids),
        "claims": _claim_counts(db, year_ids, today, insurer, placements),
        "windows": _window_counts(db, year_ids),
    }


def _underwriting_by_year(db: Session, year_ids: list[str]) -> dict[str, int]:
    """Project legacy orphan lines as the destination's adoption would, without writes."""
    if not year_ids:
        return {}
    reviews = list(db.scalars(select(UnderwritingReview).where(
        UnderwritingReview.policy_year_id.in_(year_ids),
    )))
    totals: dict[str, int] = {}
    known = {(review.policy_year_id, review.employee_id or review.dependant_id, review.insurer)
             for review in reviews}
    for review in reviews:
        if review.status in OPEN_REVIEW_STATUSES:
            totals[review.policy_year_id] = totals.get(review.policy_year_id, 0) + 1
    orphans = list(db.execute(select(UnderwritingCase, Product).join(
        Product, Product.id == UnderwritingCase.product_id,
    ).where(
        UnderwritingCase.policy_year_id.in_(year_ids), UnderwritingCase.review_id.is_(None),
    ).order_by(UnderwritingCase.created_at, UnderwritingCase.id)))
    if not orphans:
        return totals
    orphan_years = {line.policy_year_id for line, _ in orphans}
    setups = {(year_id, _norm(code)): _captured(answers) for year_id, code, answers in db.execute(
        select(ProductSetup.policy_year_id, ProductSetup.product_code, ProductSetup.answers)
        .where(ProductSetup.policy_year_id.in_(orphan_years)),
    )}
    for line, product in orphans:
        subject = line.employee_id or line.dependant_id
        if not subject:
            continue
        insurer = setups.get((line.policy_year_id, _norm(product.code)), _legacy(product))
        key = (line.policy_year_id, subject, insurer)
        if key in known:
            continue
        known.add(key)
        if normalize_uw_status(line.status) not in DECIDED_UW_STATUSES:
            totals[line.policy_year_id] = totals.get(line.policy_year_id, 0) + 1
    return totals


def _claim_counts(
    db: Session, year_ids: list[str], today: date,
    insurer: str | None, placements: dict[tuple[str, str], str],
) -> dict[str, dict[str, int]]:
    if not year_ids:
        return {}
    review = Claim.status.in_(_CLAIMS_TO_REVIEW)
    sent = Claim.status == "sent_to_insurer"
    conditions = {
        "claims_to_review": review,
        "verification_pending": Claim.status == "ai_review_pending",
        "insured_claims_to_review": review & (Claim.claim_kind == "insured"),
        "wallet_claims_to_review": review & (Claim.claim_kind == "flex"),
        "claims_with_insurer": sent,
        "claims_overdue": sent & (Claim.insurer_deadline_on < today),
    }
    stmt = select(
        Claim.policy_year_id,
        *(func.sum(case((condition, 1), else_=0)).label(name)
          for name, condition in conditions.items()),
    ).where(Claim.policy_year_id.in_(year_ids)).group_by(Claim.policy_year_id)
    if insurer:
        stmt = stmt.where(claim_insurer_filter(insurer, placements))
    return {
        row.policy_year_id: {name: int(row._mapping[name] or 0) for name in conditions}
        for row in db.execute(stmt)
    }


def _window_counts(db: Session, year_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not year_ids:
        return {}
    now = func.now()
    opened = (EnrollmentWindow.opens_at <= now) & (EnrollmentWindow.closes_at >= now)
    scheduled = EnrollmentWindow.opens_at > now
    overdue = EnrollmentWindow.closes_at < now
    rows = db.execute(select(
        EnrollmentWindow.policy_year_id,
        func.min(case((opened, EnrollmentWindow.closes_at))).label("closes_at"),
        func.sum(case((opened, 1), else_=0)).label("open_count"),
        func.sum(case((scheduled, 1), else_=0)).label("scheduled"),
        func.min(case((scheduled, EnrollmentWindow.opens_at))).label("opens_at"),
        func.sum(case((overdue, 1), else_=0)).label("overdue"),
    ).where(
        EnrollmentWindow.policy_year_id.in_(year_ids),
        EnrollmentWindow.status == WindowStatus.open,
    ).group_by(EnrollmentWindow.policy_year_id))
    return {row.policy_year_id: {
        "enrollment_open": row.closes_at is not None,
        "enrollment_open_count": int(row.open_count or 0),
        "enrollment_closes_at": row.closes_at,
        "enrollment_scheduled": int(row.scheduled or 0),
        "enrollment_opens_at": row.opens_at,
        "enrollment_overdue": int(row.overdue or 0),
    } for row in rows}


def _open_window_close_by_year(db: Session, year_ids: list[str]) -> dict[str, datetime]:
    return {year_id: values["enrollment_closes_at"]
            for year_id, values in _window_counts(db, year_ids).items()
            if values["enrollment_open"]}


def _grouped_count(
    db: Session, column: Any, model: type[Any], year_ids: list[str], *filters: Any
) -> dict[str, int]:
    """`{policy_year_id: count}` for `year_ids`, applying extra WHERE filters."""
    if not year_ids:
        return {}
    stmt = select(column, func.count()).where(column.in_(year_ids), *filters).group_by(column)
    return {row[0]: row[1] for row in db.execute(stmt).all()}


def _messages_awaiting_reply_by_year(db: Session, year_ids: list[str]) -> dict[str, int]:
    """Conversations whose latest message is from the member, grouped by year.

    A conversation counts once even when the member sends several consecutive
    messages. This matches the Claims Messages queue's `awaiting=us` total and
    includes both claim threads and general member enquiries.
    """
    if not year_ids:
        return {}

    def count_threads(
        model: type[Any],
        thread_id_column: Any,
        year_column: Any,
        message_owner_column: Any,
    ) -> dict[str, int]:
        scoped_ids = select(thread_id_column).where(year_column.in_(year_ids))
        ranked = (
            select(
                message_owner_column.label("thread_id"),
                ClaimMessage.author_type.label("author_type"),
                func.row_number()
                .over(
                    partition_by=message_owner_column,
                    order_by=(
                        ClaimMessage.created_at.desc(),
                        ClaimMessage.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(message_owner_column.in_(scoped_ids))
            .subquery()
        )
        rows = db.execute(
            select(year_column, func.count())
            .select_from(model)
            .join(ranked, ranked.c.thread_id == thread_id_column)
            .where(ranked.c.rn == 1, ranked.c.author_type == AUTHOR_MEMBER)
            .group_by(year_column)
        ).all()
        return {year_id: count for year_id, count in rows}

    totals = count_threads(
        Claim,
        Claim.id,
        Claim.policy_year_id,
        ClaimMessage.claim_id,
    )
    enquiries = count_threads(
        MemberEnquiry,
        MemberEnquiry.id,
        MemberEnquiry.policy_year_id,
        ClaimMessage.enquiry_id,
    )
    for year_id, count in enquiries.items():
        totals[year_id] = totals.get(year_id, 0) + count
    return totals


def _unmatched_by_year(db: Session, year_ids: list[str]) -> dict[str, int]:
    """`{policy_year_id: employees with no matched category}`.

    Counts ALL employees (matched = non-null `matched_category_id`), exactly as
    the match-results page does (`matches.py`). This dashboard number is the
    entry point to that page, so the two MUST agree — filtering to active only
    here would headline a smaller count than the page the broker lands on.
    """
    if not year_ids:
        return {}
    rows = db.execute(
        select(
            Employee.policy_year_id,
            func.count(Employee.id),
            func.count(Employee.matched_category_id),
        )
        .where(Employee.policy_year_id.in_(year_ids))
        .group_by(Employee.policy_year_id)
    ).all()
    return {yid: (total or 0) - (matched or 0) for yid, total, matched in rows}


def _stale_matching_years(db: Session, year_ids: list[str]) -> set[str]:
    """Years whose categories changed AFTER the last matching run.

    Same staleness rule as `matches.py`: a category re-parse / rule edit / tier
    change bumps `Category.updated_at` but matched-category snapshots don't
    self-heal, so stored matches silently drift. "Never run" is NOT flagged here
    — the unmatched count already carries that case.
    """
    if not year_ids:
        return set()
    cat_updated = {
        yid: ts
        for yid, ts in db.execute(
            select(Category.policy_year_id, func.max(Category.updated_at))
            .where(Category.policy_year_id.in_(year_ids))
            .group_by(Category.policy_year_id)
        ).all()
    }
    last_run = {
        eid: ts
        for eid, ts in db.execute(
            select(AuditLog.entity_id, func.max(AuditLog.created_at))
            .where(
                AuditLog.action == "run_matching",
                AuditLog.entity_type == "policy_year",
                AuditLog.entity_id.in_(year_ids),
            )
            .group_by(AuditLog.entity_id)
        ).all()
    }
    return {
        yid
        for yid, changed in cat_updated.items()
        if changed is not None and last_run.get(yid) is not None and changed > last_run[yid]
    }
