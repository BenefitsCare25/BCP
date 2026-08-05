"""Dependant selection — vocabulary and a filtered page of the Dependants tab.

- GET  /policy-years/{id}/dependant-facets      — filter vocabulary + headcounts
- POST /policy-years/{id}/dependant-query/list  — a filtered page

The mirror of ``member_query`` for the roster's other population. POST for the
list because the sponsoring-employee filter is a nested ``MemberFilters`` and
does not survive query-string encoding.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import load_policy_year
from app.db.session import get_db
from app.models import PolicyYear
from app.schemas.api import DependantList, DependantOut
from app.schemas.dependant_query import DependantFacetsOut, DependantQueryListIn
from app.services import dependant_query as svc

router = APIRouter(tags=["dependant-query"])


@router.get(
    "/policy-years/{policy_year_id}/dependant-facets",
    response_model=DependantFacetsOut,
)
def dependant_facets(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> DependantFacetsOut:
    return svc.build_facets(db, py)


@router.post(
    "/policy-years/{policy_year_id}/dependant-query/list",
    response_model=DependantList,
)
def dependant_query_list(
    policy_year_id: str,
    body: DependantQueryListIn,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> DependantList:
    rows, _idx = svc.resolve_listing(db, py, body.query)
    page = rows[body.offset : body.offset + body.limit]
    return DependantList(
        total=len(rows),
        offset=body.offset,
        limit=body.limit,
        items=[DependantOut.model_validate(r) for r in page],
    )
