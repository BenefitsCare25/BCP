"""Dual coverage — lives insured twice under one company.

- GET    /policy-years/{id}/dual-coverage              — cases + opportunities
- POST   /policy-years/{id}/dual-coverage/decisions    — record a decision
- DELETE /policy-years/{id}/dual-coverage/decisions/{subject_key} — reopen

Detection is computed on read; only the DECISION is stored. A decision matches a
case when ANY of its recorded keys overlaps the case's, so filling in the missing
NRIC that a case prompted does not orphan the decision it produced.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_policy_year
from app.db.session import get_db
from app.models import Dependant, Employee, PolicyYear, User
from app.models.dual_coverage_decision import DualCoverageDecision
from app.schemas.dual_coverage import (
    DualCaseOut,
    DualCoverageOut,
    DualCoverIn,
    DualDecisionIn,
    DualDecisionOut,
    DualLifeRefOut,
    DualOpportunityOut,
    DualPartyOut,
)
from app.services import dual_coverage as svc
from app.services.dual_coverage_assignment import set_dependant_cover

router = APIRouter(tags=["dual-coverage"])


def _decisions_for(db: Session, py: PolicyYear) -> list[DualCoverageDecision]:
    return list(
        db.execute(
            select(DualCoverageDecision).where(
                DualCoverageDecision.policy_year_id == py.id
            )
        ).scalars()
    )


def _decider_names(
    db: Session, rows: list[DualCoverageDecision]
) -> dict[str, str]:
    """user id → the name to print. A decision is only useful if a broker can
    tell WHO took it, and the stored value is a uuid."""
    ids = {r.decided_by for r in rows if r.decided_by}
    if not ids:
        return {}
    found = db.execute(
        select(User.id, User.display_name, User.email).where(User.id.in_(ids))
    ).all()
    return {uid: (name or email or "") for uid, name, email in found if (name or email)}


def _upsert_decision(
    db: Session,
    py: PolicyYear,
    user: CurrentUser,
    *,
    subject_key: str,
    subject_keys: list[str],
    parties_digest: str,
    decision: str,
    carried_by: str | None,
    carried_by_staff_id: str | None,
    note: str | None,
    existing: DualCoverageDecision | None,
    subject_kind: str = "life",
) -> DualCoverageDecision:
    """Write one life's decision."""
    row = existing
    if row is None:
        row = DualCoverageDecision(
            id=str(uuid.uuid4()),
            client_id=py.client_id,
            policy_year_id=py.id,
            subject_key=subject_key,
        )
        db.add(row)
    else:
        # Re-key the existing row onto the subject's CURRENT key so later exact
        # lookups (and the unique constraint) track the life as it is now known.
        row.subject_key = subject_key
    row.decided_at = datetime.now(UTC).replace(tzinfo=None)
    row.subject_kind = subject_kind
    row.subject_keys = subject_keys
    row.decision = decision
    row.carried_by_employee_id = carried_by
    row.carried_by_staff_id = carried_by_staff_id
    row.note = note
    row.decided_by = user.user_id
    row.parties_digest = parties_digest
    return row


def _match_decision(
    rows: list[DualCoverageDecision], subject_key: str, keys: list[str]
) -> DualCoverageDecision | None:
    """Exact key first, then ANY overlap of the recorded candidate keys.

    The overlap leg is what survives the workflow's own success: a name+DOB case
    the broker resolves by filling in the NRIC gains an ``nric:`` key, changing
    its ``subject_key`` — and a decision found only by exact match would vanish
    at the moment it was acted on.
    """
    for row in rows:
        if row.subject_key == subject_key:
            return row
    wanted = set(keys)
    for row in rows:
        if wanted & set(row.subject_keys or []):
            return row
    return None


def _decision_out(
    row: DualCoverageDecision | None,
    parties_digest: str,
    names: dict[str, str] | None = None,
) -> DualDecisionOut | None:
    if row is None:
        return None
    return DualDecisionOut(
        decision=row.decision,
        carried_by_employee_id=row.carried_by_employee_id,
        carried_by_staff_id=row.carried_by_staff_id,
        note=row.note,
        decided_by=row.decided_by,
        decided_by_name=(names or {}).get(row.decided_by or ""),
        decided_at=row.decided_at.isoformat() if row.decided_at else None,
        # A recorded digest that no longer describes the family means the
        # decision was taken about a different set of people.
        stale=bool(row.parties_digest) and row.parties_digest != parties_digest,
    )


def _party_out(p: svc.Party) -> DualPartyOut:
    return DualPartyOut(
        employee_id=p.employee_id,
        staff_id=p.staff_id,
        employee_name=p.employee_name,
        dependant_id=p.dependant_id,
        relationship=p.relationship,
        covered=p.covered,
        covered_products=p.covered_products,
        unlinked=p.unlinked,
    )


@router.get(
    "/policy-years/{policy_year_id}/dual-coverage", response_model=DualCoverageOut
)
def get_dual_coverage(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> DualCoverageOut:
    found = svc.detect(db, py)
    rows = _decisions_for(db, py)
    names = _decider_names(db, rows)

    cases: list[DualCaseOut] = []
    lives: list[DualLifeRefOut] = []
    unresolved = 0
    for case in found.cases:
        row = _match_decision(rows, case.life_key, case.life_keys)
        decision = _decision_out(row, case.parties_digest, names)
        # Undecided, or decided about a family that has since changed.
        if decision is None or decision.stale:
            unresolved += 1
        parties = [_party_out(p) for p in case.parties]
        # Built from every case, BEFORE the preview cap, so a row on page 40 of
        # the dependant table is marked as reliably as one on page 1.
        lives.extend(
            DualLifeRefOut(
                dependant_id=p.dependant_id,
                subject_key=case.life_key,
                severity=case.severity,  # type: ignore[arg-type]
                resolved=decision is not None and not decision.stale,
                parties=parties,
            )
            for p in case.parties
            if p.dependant_id
        )
        cases.append(
            DualCaseOut(
                subject_key=case.life_key,
                name=case.name,
                dob=case.dob or None,
                nric_masked=case.nric_masked,
                relationship=case.relationship,
                match_tier=case.match_tier,  # type: ignore[arg-type]
                flags=case.flags,
                parties=parties,
                overlapping_products=case.overlapping_products,
                severity=case.severity,  # type: ignore[arg-type]
                decision=decision,
            )
        )

    opportunities = [
        DualOpportunityOut(
            subject_key=o.couple_key,
            employees=[_party_out(p) for p in o.employees],
            child_name=o.child_name,
            child_dob=o.child_dob or None,
            listed_under_staff_id=o.listed_under_staff_id,
            other_staff_id=o.other_staff_id,
            decision=_decision_out(_match_decision(rows, o.couple_key, []), "", names),
        )
        for o in found.opportunities
    ]

    return DualCoverageOut(
        # Only CASES are counted into the alert. Opportunities are the normal
        # state of a dual-employee family and would bury the real duplicates.
        unresolved_cases=unresolved,
        total_cases=len(found.cases),
        total_opportunities=len(found.opportunities),
        cases=cases[: svc.PREVIEW_CAP],
        opportunities=opportunities[: svc.PREVIEW_CAP],
        preview_cap=svc.PREVIEW_CAP,
        lives=lives,
    )


@router.put(
    "/policy-years/{policy_year_id}/dual-coverage/dependants/{dependant_id}/cover",
    response_model=DualCoverIn,
)
def set_dependant_cover_endpoint(
    policy_year_id: str,
    dependant_id: str,
    body: DualCoverIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DualCoverIn:
    """Drop or restore ONE side's cover for a life listed under two employees.

    The roster keeps both rows either way — this changes who PAYS for them, not
    who is on file, which is the distinction the review sheet is built around.
    """
    dep = db.execute(
        select(Dependant).where(
            Dependant.id == dependant_id,
            Dependant.policy_year_id == py.id,
        )
    ).scalars().one_or_none()
    if dep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such dependant.")
    try:
        changed = set_dependant_cover(db, py, user, dependant=dep, covered=body.covered)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    write_audit(
        db,
        user,
        action="dual_coverage.set_cover",
        entity_type="dependant",
        entity_id=dep.id,
        after={"covered": body.covered, "products": changed},
    )
    db.commit()
    return DualCoverIn(covered=body.covered, products_changed=changed)


@router.post(
    "/policy-years/{policy_year_id}/dual-coverage/decisions",
    response_model=DualDecisionOut,
)
def record_decision(
    policy_year_id: str,
    body: DualDecisionIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DualDecisionOut:
    found = svc.detect(db, py)
    keys: list[str] = []
    digest = ""
    if body.subject_kind == "life":
        case = next(
            (c for c in found.cases if c.life_key == body.subject_key), None
        )
        if case is None:
            raise HTTPException(status_code=404, detail="No such dual-coverage case.")
        keys, digest = case.life_keys, case.parties_digest
        party_ids = {p.employee_id for p in case.parties if p.employee_id}
    else:
        opp = next(
            (o for o in found.opportunities if o.couple_key == body.subject_key), None
        )
        if opp is None:
            raise HTTPException(status_code=404, detail="No such dual-coverage case.")
        party_ids = {p.employee_id for p in opp.employees if p.employee_id}

    carried_by = body.carried_by_employee_id
    staff_id: str | None = None
    if carried_by:
        # Must be one of THIS case's parties — otherwise a decision could name
        # an unrelated employee (or one from another tenant).
        if carried_by not in party_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="carried_by_employee_id is not a party to this case.",
            )
        emp = db.get(Employee, carried_by)
        staff_id = emp.staff_id if emp else None

    # Resolve through the SAME overlap rule the GET renders with, not an exact
    # key match. A case whose key has since moved (the broker filled in the NRIC
    # this very case asked for) still shows its decision via the overlap leg, so
    # an exact-match upsert here would write a SECOND row for one life — and
    # deleting the new one would resurrect the old one.
    row = _match_decision(_decisions_for(db, py), body.subject_key, keys)
    before = (
        {"decision": row.decision, "carried_by_staff_id": row.carried_by_staff_id}
        if row
        else None
    )
    row = _upsert_decision(
        db,
        py,
        user,
        subject_key=body.subject_key,
        subject_keys=keys,
        parties_digest=digest,
        decision=body.decision,
        carried_by=carried_by,
        carried_by_staff_id=staff_id,
        note=body.note,
        existing=row,
        subject_kind=body.subject_kind,
    )

    write_audit(
        db,
        user,
        action="dual_coverage.decide",
        entity_type="dual_coverage_decision",
        entity_id=row.id,
        before=before,
        # Identity is deliberately NOT in the payload — `_scrub` redacts
        # secret-looking keys, not names or NRICs, and an audit row must not
        # become the one place a life's identity is stored in the clear.
        after={"decision": body.decision, "carried_by_staff_id": staff_id},
        employee_id=carried_by,
    )
    db.commit()
    db.refresh(row)
    return _decision_out(row, digest)  # type: ignore[return-value]


@router.delete(
    "/policy-years/{policy_year_id}/dual-coverage/decisions/{subject_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def reopen_decision(
    policy_year_id: str,
    subject_key: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    # Same overlap resolution as the GET and the POST. Deleting by exact key
    # alone 404s in precisely the scenario the design exists for: the case is
    # rendered as decided (matched on a candidate key) while its own Reopen
    # button reports that no decision exists, leaving it permanently settled.
    found = svc.detect(db, py)
    keys = next(
        (c.life_keys for c in found.cases if c.life_key == subject_key), []
    )
    row = _match_decision(_decisions_for(db, py), subject_key, keys)
    if row is None:
        raise HTTPException(status_code=404, detail="No decision recorded.")
    write_audit(
        db,
        user,
        action="dual_coverage.reopen",
        entity_type="dual_coverage_decision",
        entity_id=row.id,
        before={"decision": row.decision},
        after=None,
    )
    db.delete(row)
    db.commit()
