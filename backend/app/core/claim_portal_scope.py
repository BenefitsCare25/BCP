"""Explicit member-owned period selection for claims; other portal flows stay current."""
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.portal_auth import (
    CurrentMember,
    active_policy_year,
    get_current_member,
    resolve_member_employee,
)
from app.db.session import get_db
from app.models import Employee, PolicyYear
from app.models.policy_year import PolicyYearStatus
from app.services.member_access import Capability, access_for_account, refusal


@dataclass(frozen=True)
class ClaimsMember(CurrentMember):
    claims_year_id: str | None = None


def get_claims_member(
    member: CurrentMember = Depends(get_current_member),
    year_id: str | None = Header(default=None, alias="X-Inspro-Claims-Year-ID"),
    db: Session = Depends(get_db),
) -> CurrentMember:
    if not year_id:
        return member
    year = db.get(PolicyYear, year_id)
    if (year is None or year.client_id != member.client_id
            or year.status not in {PolicyYearStatus.active, PolicyYearStatus.archived}):
        raise HTTPException(404, "Claim benefit year not found")
    # Historical selection never grants capabilities denied by the current
    # account's leaver/access state. The selected row is checked as well below.
    return ClaimsMember(
        member_account_id=member.member_account_id, client_id=member.client_id,
        broker_firm_id=member.broker_firm_id, email=member.email,
        staff_id=member.staff_id, display_name=member.display_name, claims_year_id=year.id,
    )


def claim_policy_year(db: Session, member: CurrentMember) -> PolicyYear | None:
    selected = getattr(member, "claims_year_id", None)
    return db.get(PolicyYear, selected) if selected else active_policy_year(db, member.client_id)


def resolve_claims_employee(
    db: Session, member: CurrentMember, *, requires: Capability | None = Capability.RECORD,
    year: Any = None,
) -> Employee:
    selected = year if year is not None else claim_policy_year(db, member)
    if getattr(member, "claims_year_id", None) and requires is not None:
        access = access_for_account(
            db, member_account_id=member.member_account_id,
            client_id=member.client_id, staff_id=member.staff_id,
        )
        denied = refusal(access, requires)
        if denied is not None:
            raise HTTPException(403, denied)
    return resolve_member_employee(db, member, requires=requires, year=selected)
