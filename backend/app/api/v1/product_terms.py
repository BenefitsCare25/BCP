"""Per-product coverage periods within a policy year.

Each product (Life, Medical, Dental, …) can run on its own renewal cycle, so
its coverage window may differ from the policy year's nominal span. Overrides
are stored sparsely (`ProductTerm`); products without one inherit the year's
dates. The company-level envelope is exposed on the policy year itself
(`PolicyYearOut.coverage_start/end`).

- GET    /policy-years/{id}/product-terms              — effective period per product
- PUT    /policy-years/{id}/product-terms/{product_id} — set an override
- DELETE /policy-years/{id}/product-terms/{product_id} — reset to the year's span

Tenant scoping rides on `load_policy_year`; the target product is additionally
proven to belong to this policy year before any write.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_editable, load_policy_year
from app.db.session import get_db
from app.models import PolicyYear, Product, ProductTerm
from app.schemas.api import ProductTermOut, ProductTermUpdate
from app.services.claim_intake import is_inpatient_product
from app.services.product_terms import (
    product_ids_in_year,
    resolve_terms,
    term_window,
    uses_life_thresholds,
)
from app.services.underwriting import refresh_underwriting_cases

router = APIRouter(tags=["product-terms"])


@router.get(
    "/policy-years/{policy_year_id}/product-terms",
    response_model=list[ProductTermOut],
)
def list_product_terms(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProductTermOut]:
    resolved = resolve_terms(db, py)
    seen = {r.product_id for r in resolved}
    # Line lookup needs the Product (resolved rows may reference global products).
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(Product.id.in_(seen))
        ).scalars()
    } if seen else {}
    items = [
        ProductTermOut(
            product_id=r.product_id,
            code=r.code,
            display_name=r.display_name,
            coverage_start=r.coverage_start,
            coverage_end=r.coverage_end,
            is_default=r.is_default,
            line=products[r.product_id].line if r.product_id in products else "medical",
            gst_included=r.gst_included,
            gst_rate=r.gst_rate,
            free_cover_limit=r.free_cover_limit,
            nel_age_limit=r.nel_age_limit,
            underwriting_required=r.underwriting_required,
            policy_number=r.policy_number,
            is_inpatient=is_inpatient_product(r.code),
            pre_hosp_days=r.pre_hosp_days,
            post_hosp_days=r.post_hosp_days,
        )
        for r in resolved
    ]
    # A freshly-added client product has no plans/categories yet, so it isn't in
    # `resolve_terms`. Surface it with its stored override when one exists (set
    # before any plans were configured), else a default (policy-year span) row
    # so its tab can set a period the moment it's created.
    if user.client_id:
        added = [
            cp
            for cp in db.execute(
                select(Product).where(Product.client_id == user.client_id)
            ).scalars()
            if cp.id not in seen
        ]
        added_terms = {
            t.product_id: t
            for t in db.execute(
                select(ProductTerm).where(
                    ProductTerm.policy_year_id == py.id,
                    ProductTerm.product_id.in_([cp.id for cp in added]),
                )
            ).scalars()
        } if added else {}
        for cp in added:
            t = added_terms.get(cp.id)
            start, end, is_default = term_window(
                t.coverage_start if t else None, t.coverage_end if t else None, py
            )
            items.append(
                ProductTermOut(
                    product_id=cp.id,
                    code=cp.code,
                    display_name=cp.display_name,
                    coverage_start=start,
                    coverage_end=end,
                    is_default=is_default,
                    line=cp.line,
                    gst_included=t.gst_included if t else None,
                    gst_rate=t.gst_rate if t else None,
                    free_cover_limit=(
                        t.free_cover_limit if t and uses_life_thresholds(cp) else None
                    ),
                    nel_age_limit=(
                        t.nel_age_limit if t and uses_life_thresholds(cp) else None
                    ),
                    underwriting_required=(
                        bool(t.underwriting_required)
                        if t and cp.line in ("medical", "general")
                        else False
                    ),
                    policy_number=t.policy_number if t else None,
                    is_inpatient=is_inpatient_product(cp.code),
                    pre_hosp_days=t.pre_hosp_days if t else None,
                    post_hosp_days=t.post_hosp_days if t else None,
                )
            )
    items.sort(key=lambda x: (x.code, x.display_name))
    return items


@router.put(
    "/policy-years/{policy_year_id}/product-terms/{product_id}",
    response_model=ProductTermOut,
)
def set_product_term(
    policy_year_id: str,
    product_id: str,
    body: ProductTermUpdate,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
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
    product = _require_product_in_year(db, py, product_id)
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
    db.commit()
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


@router.delete(
    "/policy-years/{policy_year_id}/product-terms/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def reset_product_term(
    policy_year_id: str,
    product_id: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Remove a product's override so it inherits the policy year's span again.
    Idempotent: a missing override is a no-op 204."""
    assert_policy_year_editable(py)
    term = db.execute(
        select(ProductTerm).where(
            ProductTerm.policy_year_id == py.id,
            ProductTerm.product_id == product_id,
        )
    ).scalar_one_or_none()
    if term is None:
        return None
    had_nel = term.free_cover_limit is not None or term.nel_age_limit is not None
    db.delete(term)
    write_audit(
        db, user, action="reset_product_term", entity_type="product_term",
        entity_id=term.id,
        before={"policy_year_id": py.id, "product_id": product_id},
    )
    # Dropping the row drops its Non-Evidence Limits, so the product's cases are
    # moot — re-sync here too (the PUT path does). Undecided lines hold a
    # guaranteed-SI SNAPSHOT, so without this the insurer listing keeps
    # reporting a pending excess for a product that no longer has a limit.
    if had_nel:
        db.flush()
        refresh_underwriting_cases(db, py)
    db.commit()
    return None


def _require_product_in_year(
    db: Session, py: PolicyYear, product_id: str
) -> Product:
    """404 unless `product_id` belongs to this (already tenant-checked) policy
    year: either it's configured via the year's plans/categories, or it's a
    catalog product owned by this client (a just-added product with no plans
    yet). A product from another tenant or year can't get an override here."""
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    configured = product_id in product_ids_in_year(db, py.id)
    owned = product.client_id is not None and product.client_id == py.client_id
    if not (configured or owned):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Product is not configured in this policy year.",
        )
    return product
