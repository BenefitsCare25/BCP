"""Roster selection — the vocabulary and the headcount behind a member query.

These endpoints exist so a broker never has to run a preview (or type a staff id)
to find out who a rule matches:

- GET  /policy-years/{id}/member-facets        — filter vocabulary + headcounts
- POST /policy-years/{id}/member-query/count   — live "N members match"
- POST /policy-years/{id}/member-query/resolve — pasted list → members

They are deliberately independent of the bulk-update module: a selection is a
general capability, and Coverage & Members is the next surface that wants it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import load_policy_year
from app.db.session import get_db
from app.models import PolicyYear
from app.schemas.member_query import (
    MemberFacetsOut,
    MemberListResolveIn,
    MemberListResolveOut,
    MemberQueryCountIn,
    MemberQueryCountOut,
)
from app.services import member_query as svc
from app.services.enrollment_products import resolve_product_by_code

router = APIRouter(tags=["member-query"])


@router.get(
    "/policy-years/{policy_year_id}/member-facets", response_model=MemberFacetsOut
)
def member_facets(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> MemberFacetsOut:
    return svc.build_facets(db, py)


@router.post(
    "/policy-years/{policy_year_id}/member-query/count",
    response_model=MemberQueryCountOut,
)
def member_query_count(
    policy_year_id: str,
    body: MemberQueryCountIn,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> MemberQueryCountOut:
    """How many members this rule matches right now.

    The product is optional and only scopes the coverage filters
    (``current_plan_codes`` / ``coverage_state``) — an unknown code leaves them
    inert rather than 404-ing, because the count is a live readout the picker
    fires on every keystroke and must never be the thing that breaks a page.
    """
    product = (
        resolve_product_by_code(db, py, body.product_code) if body.product_code else None
    )
    selection = svc.resolve_selection(
        db, py, body.query, product_id=product.id if product else None
    )
    return MemberQueryCountOut(
        total=len(selection.employees),
        unresolved=[u.out() for u in selection.unresolved],
    )


@router.post(
    "/policy-years/{policy_year_id}/member-query/resolve",
    response_model=MemberListResolveOut,
)
def resolve_member_list(
    policy_year_id: str,
    body: MemberListResolveIn,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> MemberListResolveOut:
    """Turn pasted text (a spreadsheet column, an email list) into members.

    Unmatched entries come back so they can be shown IN the picker — the old
    flow only discovered a bad staff id after a full preview run.
    """
    matched, unmatched, duplicates = svc.resolve_member_list(
        db, py, body.text, include_terminated=body.include_terminated
    )
    return MemberListResolveOut(
        matched=matched, unmatched=unmatched, duplicates=duplicates
    )
