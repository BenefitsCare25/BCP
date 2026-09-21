"""Placement identifiers frozen when a claim is filed; legacy values labelled as live."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, PolicyYear, Product
from app.models.claim import CLAIM_KIND_INSURED, CLAIM_STATUS_NEEDS_INFO
from app.services.product_insurer import insurer_map
from app.services.product_terms import resolve_terms

PLACEMENT_COLUMNS = ["Policy Number", "Insurer", "Product Name", "Placement Source"]


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
    return {
        "product_code": claim.product_code,
        "product_name": term.display_name,
        "policy_number": term.policy_number,
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
        return [None, None, None, "Not insured"]
    meta = claim.intake_meta if isinstance(claim.intake_meta, dict) else {}
    value = meta.get("placement_snapshot")
    frozen = isinstance(value, dict) and value.get("product_code") == claim.product_code
    placement: dict[str, Any] = value if frozen and isinstance(value, dict) else current_placement(
        db, claim
    )
    return [
        placement.get("policy_number"),
        placement.get("insurer"),
        placement.get("product_name"),
        "At filing" if frozen else "Current configuration; no filing snapshot",
    ]
