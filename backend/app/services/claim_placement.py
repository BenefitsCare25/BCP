"""Placement identifiers frozen when a claim is filed; legacy values labelled as live."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Employee, PolicyYear, Product
from app.models.claim import CLAIM_KIND_INSURED, CLAIM_STATUS_NEEDS_INFO
from app.services.policy_numbers import member_entity, resolve_policy_number, source_numbers
from app.services.product_insurer import insurer_map
from app.services.product_terms import resolve_terms

PLACEMENT_COLUMNS = [
    "Policy Number", "Insurer", "Product Name", "Placement Source", "Policy Number Status"
]


def current_placement(db: Session, claim: Claim) -> dict[str, Any]:
    if claim.claim_kind != CLAIM_KIND_INSURED:
        return {}
    year = db.get(PolicyYear, claim.policy_year_id)
    if year is None or year.client_id != claim.client_id:
        return {}
    term = next((t for t in resolve_terms(db, year) if t.code == claim.product_code), None)
    if term is None:
        return {"product_code": claim.product_code, "product_name": claim.product_code}
    product = db.scalar(select(Product).where(Product.id == term.product_id))
    insurer = insurer_map(db, year.id, [product]).get(product.id, "") if product else ""
    employee = db.get(Employee, claim.employee_id)
    entity = (
        member_entity(employee.attribute_values)
        if employee and employee.client_id == claim.client_id and employee.policy_year_id == year.id
        else None
    )
    policy = resolve_policy_number(term, entity)
    return {
        "product_code": claim.product_code,
        "product_name": term.display_name,
        "policy_number": policy.number,
        "policy_entity": entity,
        "policy_number_status": policy.status,
        "insurer": insurer,
        "coverage_start": term.coverage_start.isoformat(),
        "coverage_end": term.coverage_end.isoformat(),
    }


def capture_claim_placement(db: Session, claim: Claim) -> None:
    if claim.claim_kind != CLAIM_KIND_INSURED:
        return
    # Today's placement cannot become a legacy case's original filing snapshot.
    if claim.status == CLAIM_STATUS_NEEDS_INFO:
        return
    meta = dict(claim.intake_meta) if isinstance(claim.intake_meta, dict) else {}
    old = meta.get("placement_snapshot")
    if isinstance(old, dict) and old.get("product_code") == claim.product_code:
        return
    value = current_placement(db, claim)
    value["captured_at"] = datetime.now(UTC).isoformat()
    meta["placement_snapshot"] = value
    claim.intake_meta = meta


def placement_cells(db: Session, claim: Claim) -> list[object]:
    if claim.claim_kind != CLAIM_KIND_INSURED:
        return [None, None, None, "Not insured", "Not insured"]
    meta = claim.intake_meta if isinstance(claim.intake_meta, dict) else {}
    value = meta.get("placement_snapshot")
    frozen = isinstance(value, dict) and value.get("product_code") == claim.product_code
    placement: dict[str, Any] = value if frozen and isinstance(value, dict) else current_placement(
        db, claim
    )
    number = placement.get("policy_number")
    numbers = source_numbers(number)
    policy_status = placement.get("policy_number_status") or (
        "Legacy filing snapshot" if frozen else "Legacy product setting"
    )
    if len(numbers) > 1:
        number = None
        policy_status = "Needs review: legacy filing snapshot" if frozen else "Needs review"
    elif not numbers:
        number = None
        policy_status = placement.get("policy_number_status") or (
            "Not assigned at filing" if frozen else "Not assigned"
        )
    return [
        number,
        placement.get("insurer"),
        placement.get("product_name"),
        "At filing" if frozen else "Current configuration; no filing snapshot",
        policy_status,
    ]
