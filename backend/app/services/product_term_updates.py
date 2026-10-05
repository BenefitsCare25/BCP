"""Apply product terms within the caller's transaction."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.deps import assert_policy_year_editable
from app.models import PolicyYear, Product, ProductTerm
from app.schemas.api import ProductTermOut, ProductTermUpdate
from app.services.claim_intake import is_inpatient_product
from app.services.product_terms import term_window, uses_life_thresholds
from app.services.underwriting import refresh_underwriting_cases


def parse_setup_policy_terms(value: Any) -> ProductTermUpdate:
    """Validate raw draft inputs only when the broker applies the setup."""
    fields = {
        "coverage_start", "coverage_end", "gst_included", "gst_rate",
        "free_cover_limit", "nel_age_limit", "underwriting_required",
        "pre_hosp_days", "post_hosp_days",
    }
    if not isinstance(value, dict) or set(value) - fields:
        raise HTTPException(422, "Review the draft policy term fields before confirming.")
    normalized = dict(value)
    for field in (
        "gst_rate", "free_cover_limit", "nel_age_limit", "pre_hosp_days", "post_hosp_days",
    ):
        raw = normalized.get(field)
        if isinstance(raw, str):
            normalized[field] = raw.strip().replace(",", "") or None
    # Empty dates are incomplete inputs, not an instruction to reset live dates.
    if any(normalized.get(field) == "" for field in ("coverage_start", "coverage_end")):
        raise HTTPException(422, "Set both policy term dates before confirming.")
    try:
        return ProductTermUpdate.model_validate(normalized)
    except ValidationError as error:
        messages = "; ".join(
            f"{'.'.join(map(str, item['loc'])) or 'Policy terms'}: {item['msg']}"
            for item in error.errors()
        )
        raise HTTPException(422, f"Review the draft policy terms: {messages}") from None


def apply_product_term_update(
    db: Session, py: PolicyYear, product: Product,
    body: ProductTermUpdate, user: CurrentUser,
) -> ProductTermOut:
    # The free cover limit and policy number are OPERATIONAL config (the policy
    # number is insurer-issued AFTER placement; FCL is report-facing) — bodies
    # touching only those stay editable after activation. Coverage dates / GST
    # keep the lock.
    if not body.model_fields_set <= {
        "free_cover_limit", "nel_age_limit", "underwriting_required",
        "policy_number",
    }:
        assert_policy_year_editable(py)
    product_id = product.id
    sent = body.model_fields_set
    term = db.execute(
        select(ProductTerm).where(
            ProductTerm.policy_year_id == py.id,
            ProductTerm.product_id == product_id,
        )
    ).scalar_one_or_none()
    has_life_thresholds = uses_life_thresholds(product)
    if not has_life_thresholds and {"free_cover_limit", "nel_age_limit"} & sent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Free cover limit and NEL age apply only to Life products.",
        )
    if product.line not in ("medical", "general") and "underwriting_required" in sent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Underwriting Yes/No applies only to Medical and General products.",
        )
    if term is None:
        term = ProductTerm(policy_year_id=py.id, product_id=product_id)
        db.add(term)
        action = "set_product_term"
    else:
        action = "update_product_term"

    # Partial update: apply ONLY the dimensions the caller actually sent, so a
    # GST-only body can't wipe the coverage period and a dates-only body can't
    # reset GST. Dates move as a pair (enforced by ProductTermUpdate).
    if "coverage_start" in sent or "coverage_end" in sent:
        term.coverage_start = body.coverage_start
        term.coverage_end = body.coverage_end
    if "gst_included" in sent:
        term.gst_included = body.gst_included
    if "gst_rate" in sent:
        term.gst_rate = body.gst_rate
    if "free_cover_limit" in sent:
        term.free_cover_limit = body.free_cover_limit
    if "nel_age_limit" in sent:
        term.nel_age_limit = body.nel_age_limit
    if "underwriting_required" in sent:
        term.underwriting_required = body.underwriting_required
        # Clean any legacy thresholds that were recorded before line scoping.
        term.free_cover_limit = None
        term.nel_age_limit = None
    for field in ("pre_hosp_days", "post_hosp_days"):
        if field in sent:
            setattr(term, field, getattr(body, field))
    if "policy_number" in sent:
        cleaned = (body.policy_number or "").strip()
        term.policy_number = cleaned or None
    db.flush()

    # A changed Non-Evidence Limit (dollar FCL or age gate) moves the
    # underwriting thresholds — re-sync cases in the same transaction so the
    # queue reflects the new limit without a manual refresh.
    if {"free_cover_limit", "nel_age_limit"} & sent:
        refresh_underwriting_cases(db, py)

    start, end, is_default = term_window(term.coverage_start, term.coverage_end, py)
    has_dates = not is_default
    write_audit(
        db, user, action=action, entity_type="product_term", entity_id=term.id,
        after={
            "policy_year_id": py.id,
            "product_id": product_id,
            "coverage_start": start.isoformat() if has_dates else None,
            "coverage_end": end.isoformat() if has_dates else None,
            "gst_included": term.gst_included,
            "gst_rate": term.gst_rate,
            "free_cover_limit": term.free_cover_limit,
            "nel_age_limit": term.nel_age_limit,
            "underwriting_required": term.underwriting_required,
            "pre_hosp_days": term.pre_hosp_days,
            "post_hosp_days": term.post_hosp_days,
            "policy_number": term.policy_number,
        },
    )
    return ProductTermOut(
        product_id=product_id,
        code=product.code,
        display_name=product.display_name,
        coverage_start=start,
        coverage_end=end,
        is_default=is_default,
        line=product.line,
        gst_included=term.gst_included,
        gst_rate=term.gst_rate,
        free_cover_limit=term.free_cover_limit if has_life_thresholds else None,
        nel_age_limit=term.nel_age_limit if has_life_thresholds else None,
        underwriting_required=(
            bool(term.underwriting_required)
            if product.line in ("medical", "general")
            else False
        ),
        policy_number=term.policy_number,
        is_inpatient=is_inpatient_product(product.code),
        pre_hosp_days=term.pre_hosp_days,
        post_hosp_days=term.post_hosp_days,
    )
