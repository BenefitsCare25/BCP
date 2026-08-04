"""Server-side flex-wallet guards for enrollment finalization.

The election panel shows a live balance, but display is not enforcement: these
checks run at submit/confirm so an enrollment whose elections draw more flex
than the member's wallet holds — or whose changed elections carry no price at
all — cannot be finalized silently.

Balance = wallet - (total election ``flex_price_tag``) + leave ``flex_amount``
(buy spends -, sell credits +), using the SNAPSHOTTED tags so the guard agrees
with what the member saw when electing. Members without a wallet aren't flex
participants — no guard applies.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee
from app.models.enrollment import ElectionAction, Enrollment, EnrollmentElection
from app.models.enrollment_window import EnrollmentWindow
from app.models.leave_election import LeaveAction, LeaveElection

# Float-noise tolerance: a balance below -0.5 cents counts as overdrawn.
_EPSILON = 0.005


@dataclass(frozen=True)
class FlexDraft:
    wallet: float
    total_price_tags: float
    leave_amount: float

    @property
    def balance(self) -> float:
        return round(self.wallet - self.total_price_tags + self.leave_amount, 2)


def _elections(db: Session, enrollment_id: str) -> list[EnrollmentElection]:
    return list(
        db.execute(
            select(EnrollmentElection).where(
                EnrollmentElection.enrollment_id == enrollment_id
            )
        ).scalars()
    )


def enrollment_flex_draft(db: Session, enr: Enrollment) -> FlexDraft | None:
    """The enrollment's draft wallet position, or None when flex doesn't apply
    (no wallet on the member)."""
    employee = db.get(Employee, enr.employee_id)
    wallet = employee.flex_wallet_amount if employee else None
    if not isinstance(wallet, (int, float)):
        return None
    total = sum(
        e.flex_price_tag
        for e in _elections(db, enr.id)
        if isinstance(e.flex_price_tag, (int, float))
    )
    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enr.id)
    ).scalar_one_or_none()
    leave_amount = (
        leave.flex_amount
        if leave is not None
        and leave.action != LeaveAction.none
        and isinstance(leave.flex_amount, (int, float))
        else 0.0
    )
    return FlexDraft(
        wallet=float(wallet),
        total_price_tags=round(float(total), 2),
        leave_amount=float(leave_amount),
    )


def assert_within_wallet(
    db: Session, enr: Enrollment, window: EnrollmentWindow
) -> None:
    """409 when the enrollment's elections overdraw the member's flex wallet
    and the window doesn't allow overdrafts. No-op for non-flex members."""
    if window.allow_overdraft:
        return
    draft = enrollment_flex_draft(db, enr)
    if draft is None or draft.balance >= -_EPSILON:
        return
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "flex_overdrawn",
            "message": (
                "The benefits selection draws more flex than the member's wallet "
                "holds. Reduce it, or enable overdraft on the enrolment period."
            ),
            "wallet": draft.wallet,
            "total_price_tags": draft.total_price_tags,
            "leave_amount": draft.leave_amount,
            "balance": draft.balance,
        },
    )


def unpriced_election_products(db: Session, enr: Enrollment) -> list[str]:
    """Product codes whose election CHANGES coverage but snapshotted no price.

    A changed election (enroll/upgrade/downgrade, or dependants added) with a
    None ``flex_price_tag`` would draw $0 flex silently — usually a pricing
    config gap (no slip premium, no matrix row, or an age-banded tier with the
    member's age unknown). Keep/decline elections are legitimately unpriced.
    Only meaningful for flex members (callers gate on the wallet).
    """
    out: list[str] = []
    for e in _elections(db, enr.id):
        if e.action in (ElectionAction.keep, ElectionAction.decline):
            # keep with dependants ADDED still draws flex — flag if unpriced.
            if not (e.action == ElectionAction.keep and e.covered_dependant_ids):
                continue
        if e.flex_price_tag is None:
            out.append(e.product_code)
    return out


def assert_elections_priced(
    db: Session, enr: Enrollment, acknowledge: bool
) -> None:
    """409 when changed elections carry no price tag, unless the broker
    explicitly acknowledged submitting them unpriced."""
    if acknowledge:
        return
    if enrollment_flex_draft(db, enr) is None:
        return  # not a flex member — price tags don't apply
    unpriced = unpriced_election_products(db, enr)
    if not unpriced:
        return
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "unpriced_elections",
            "message": (
                "These benefits selections change coverage but have no flex price "
                "configured — they would draw $0 from the wallet. Configure "
                "pricing (or the member's date of birth for age-banded "
                "products), or submit again acknowledging this."
            ),
            "products": unpriced,
        },
    )
