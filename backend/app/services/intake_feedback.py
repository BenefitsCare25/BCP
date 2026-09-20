"""Server-owned autofill baselines and correction measurements; no model training."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Claim, Employee
from app.schemas.claims import ClaimIntakeSuggestionOut


def remember_intake(
    db: Session, suggestion: ClaimIntakeSuggestionOut, employee: Employee, member_id: str
) -> str:
    readings = {
        "0": {
            "fields": suggestion.fields.model_dump(),
            "sources": {
                k: [s.model_dump() for s in v] for k, v in suggestion.field_sources.items()
            },
        }
    }
    for document in suggestion.documents:
        if document.fields is not None and document.claim_index is not None:
            readings[str(document.claim_index)] = {
                "fields": document.fields.model_dump(),
                "sources": {
                    k: [s.model_dump() for s in v] for k, v in document.field_sources.items()
                },
            }
    audit = AuditLog(
        client_id=employee.client_id,
        actor_type="member",
        member_account_id=member_id,
        employee_id=employee.id,
        action="claim.intake_suggested",
        entity_type="employee",
        entity_id=employee.id,
        after={"policy_year_id": employee.policy_year_id, "readings": readings},
    )
    db.add(audit)
    db.flush()
    return audit.id


def _same(field: str, suggested: Any, actual: Any) -> bool:
    if field == "amount":
        try:
            return Decimal(str(suggested)) == Decimal(str(actual))
        except InvalidOperation:
            return False
    return str(suggested or "").strip() == str(actual or "").strip()


def record_intake_feedback(
    db: Session, claim: Claim, intake_id: str | None, claim_index: int, member_id: str
) -> None:
    if not intake_id:
        return
    audit = db.get(AuditLog, intake_id)
    if (
        audit is None
        or audit.action != "claim.intake_suggested"
        or audit.client_id != claim.client_id
        or audit.employee_id != claim.employee_id
        or audit.member_account_id != member_id
        or (audit.after or {}).get("policy_year_id") != claim.policy_year_id
    ):
        raise HTTPException(404, "Autofill suggestion not found")
    reading = (audit.after or {}).get("readings", {}).get(str(claim_index))
    if not isinstance(reading, dict):
        raise HTTPException(422, "Autofill claim index does not exist")
    fields = {key: value for key, value in reading["fields"].items() if value is not None}
    corrected = [
        key
        for key, value in fields.items()
        if not _same(key, value, getattr(claim, "amount_claimed" if key == "amount" else key))
    ]
    claim.intake_meta = {
        **(claim.intake_meta or {}),
        "autofill": {
            "intake_id": intake_id,
            "claim_index": claim_index,
            "suggested_fields": len(fields),
            "corrected_fields": corrected,
            "field_sources": reading.get("sources", {}),
        },
    }


def intake_quality(db: Session, client_id: str, policy_year_id: str) -> dict[str, Any]:
    metadata = db.scalars(
        select(Claim.intake_meta).where(
            Claim.client_id == client_id,
            Claim.policy_year_id == policy_year_id,
            Claim.status != "draft",
            Claim.intake_meta.is_not(None),
        )
    )
    claims = suggested = corrected = 0
    fields: Counter[str] = Counter()
    for item in metadata:
        feedback = (item or {}).get("autofill")
        if not isinstance(feedback, dict):
            continue
        claims += 1
        suggested += feedback.get("suggested_fields", 0)
        corrected += len(feedback.get("corrected_fields", []))
        fields.update(feedback.get("corrected_fields", []))
    return {
        "claims": claims,
        "suggested_fields": suggested,
        "corrected_fields": corrected,
        "correction_rate": corrected / suggested if suggested else None,
        "corrections_by_field": dict(fields),
    }
