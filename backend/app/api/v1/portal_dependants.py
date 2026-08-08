"""Member dependant self-service — additions are PENDING until a broker
approves them (approval re-runs flex assignment; a pending dependant never
affects family status or wallet size)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.portal_auth import (
    CurrentMember,
    get_current_member,
    resolve_member_employee,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import Dependant
from app.models.dependant import DEPENDANT_STATUS_PENDING
from app.models.stored_document import DOC_ENTITY_DEPENDANT
from app.schemas.api import DependantOut
from app.schemas.claims import PortalDependantCreateIn, StoredDocumentOut
from app.services.claims import attach_document
from app.services.member_access import Capability
from app.services.roster_attributes import normalize_nric

router = APIRouter(
    prefix="/portal/dependants",
    tags=["portal-dependants"],
    dependencies=[Depends(get_current_member)],
)


@router.post("", response_model=DependantOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_my_dependant(
    request: Request,
    body: PortalDependantCreateIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> DependantOut:
    employee = resolve_member_employee(db, member, requires=Capability.ELECT)
    attribute_values = {
        "name": body.name.strip(),
        "relationship": body.relationship.strip().lower(),
        "employee_staff_id": employee.staff_id,
    }
    if body.dob:
        attribute_values["dob"] = body.dob.isoformat()
    if body.gender:
        attribute_values["gender"] = body.gender.strip()
    if body.id_no:
        attribute_values["id_no"] = body.id_no.strip()

    dependant = Dependant(
        client_id=employee.client_id,
        policy_year_id=employee.policy_year_id,
        employee_id=employee.id,
        attribute_values=attribute_values,
        link_method="member_portal",
        status=DEPENDANT_STATUS_PENDING,
        # Stamp the identity column the rest of the platform matches on. This
        # path used to leave it NULL (and approval does not backfill it), so a
        # self-added dependant was invisible to every NRIC-keyed check —
        # including the dual-coverage detector, and this is the one path with no
        # dedup of its own.
        national_id_normalized=normalize_nric(body.id_no) or None,
    )
    db.add(dependant)
    db.flush()
    write_member_audit(
        db, member, "dependant.self_added", "dependant", dependant.id,
        after={"name": body.name, "relationship": body.relationship},
        employee_id=employee.id,
    )
    db.commit()
    return DependantOut.model_validate(dependant)


@router.post("/{dependant_id}/documents", response_model=StoredDocumentOut)
@limiter.limit("20/minute")
async def upload_my_dependant_proof(
    request: Request,
    dependant_id: str,
    file: UploadFile = File(...),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> StoredDocumentOut:
    employee = resolve_member_employee(db, member, requires=Capability.ELECT)
    dependant = db.get(Dependant, dependant_id)
    if dependant is None or dependant.employee_id != employee.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dependant not found")
    if dependant.status != DEPENDANT_STATUS_PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Proof documents can only be added while approval is pending.",
        )
    doc = await attach_document(
        db,
        client_id=dependant.client_id,
        broker_firm_id=member.broker_firm_id,
        entity_type=DOC_ENTITY_DEPENDANT,
        entity_id=dependant.id,
        file=file,
        uploaded_by_member_id=member.member_account_id,
    )
    write_member_audit(
        db, member, "dependant.proof_added", "dependant", dependant.id,
        after={"file_name": doc.file_name},
        employee_id=employee.id,
    )
    db.commit()
    return StoredDocumentOut.model_validate(doc)
