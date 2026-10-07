"""Apply product terms within the caller's transaction."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.deps import assert_policy_year_editable
from app.models import PolicyYear, Product, ProductSetup, ProductTerm
from app.schemas.api import ProductTermOut, ProductTermUpdate
from app.schemas.policy_numbers import assignment_models
from app.services.claim_intake import is_inpatient_product
from app.services.policy_numbers import (
    require_assignment_removal_permission,
    scalar_number,
    source_numbers,
    validate_assignments,
)
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


def apply_source_term_defaults(
    db: Session, py: PolicyYear, product: Product, answers: dict[str, Any],
    explicit_fields: set[str], user: CurrentUser,
) -> None:
    """Repair missing extraction-derived values as part of reviewed setup confirmation."""
    from app.services.el_report_rules import source_policy_terms

    source = source_policy_terms(answers)
    if not uses_life_thresholds(product):
        source = {key: value for key, value in source.items() if key == "gst_included"}
    term = db.scalar(select(ProductTerm).where(
        ProductTerm.policy_year_id == py.id, ProductTerm.product_id == product.id,
    ))
    missing = {key: value for key, value in source.items()
               if key not in explicit_fields and (term is None or getattr(term, key) is None)}
    if missing:
        apply_product_term_update(db, py, product, ProductTermUpdate.model_validate(missing), user)


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
        "policy_number", "policy_number_mappings",
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
    if term is None:
        term = next((
            item for item in db.new
            if isinstance(item, ProductTerm)
            and item.policy_year_id == py.id and item.product_id == product_id
        ), None)
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
    if "policy_number_mappings" in sent:
        try:
            mappings = validate_assignments(
                [item.model_dump() for item in body.policy_number_mappings]
                if body.policy_number_mappings is not None else None
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        existing_mappings = term.policy_number_mappings
        if existing_mappings is None and source_numbers(term.policy_number):
            existing_mappings = [{"entity": None, "policy_number": term.policy_number}]
        require_assignment_removal_permission(existing_mappings, mappings, user.role)
        number = scalar_number(mappings)
        if "policy_number" in sent and (body.policy_number or None) != number:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Policy number must agree with the entity assignments.",
            )
        term.policy_number_mappings = mappings
        term.policy_number = number
    elif "policy_number" in sent:
        if term.policy_number_mappings is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Update the policy-number assignments instead of the legacy policy number.",
            )
        cleaned = (body.policy_number or "").strip()
        if len(source_numbers(cleaned)) > 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Assign one policy number per entity instead of a list.",
            )
        if source_numbers(term.policy_number) and not source_numbers(cleaned):
            require_assignment_removal_permission([term.policy_number], [], user.role)
        term.policy_number = cleaned or None
    if {"policy_number", "policy_number_mappings"} & sent:
        setup = db.scalar(select(ProductSetup).where(
            ProductSetup.policy_year_id == py.id,
            ProductSetup.product_code == product.code,
        ))
        # Keep a confirmed setup's applied snapshot current. Preserve pending draft work.
        if setup is not None and setup.status == "confirmed":
            applied_mappings = term.policy_number_mappings
            if applied_mappings is None:
                numbers = source_numbers(term.policy_number)
                applied_mappings = (
                    [{"entity": None, "policy_number": numbers[0]}] if numbers else []
                )
            setup.answers = {**setup.answers, "policy_number_mappings": applied_mappings}
            setup.updated_at = datetime.now(UTC)
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
            "policy_number_mappings": term.policy_number_mappings,
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
        policy_number_mappings=assignment_models(term.policy_number_mappings),
        is_inpatient=is_inpatient_product(product.code),
        pre_hosp_days=term.pre_hosp_days,
        post_hosp_days=term.post_hosp_days,
    )
