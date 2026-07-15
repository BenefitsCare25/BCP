"""Product-wide voluntary age-band rate table — config for life products.

A life product's VOLUNTARY upgrade/downgrade plans all price off ONE age-band
rate table (per S$1000 sum assured, by age last birthday): premium = the member's
amount covered / 1000 x rate[their age band]. The slip parser stores that table
on every voluntary category's ``plan_assignments['voluntary_rates']`` (identical
copies). This module edits it as a SINGLE table — write fans out to every
age-banded voluntary category of the product so the copies stay in sync — so the
configuration UI shows it once instead of repeating it per plan.

- GET /policy-years/{id}/products/{pid}/voluntary-rates — the shared table.
- PUT /policy-years/{id}/products/{pid}/voluntary-rates — update it everywhere.

Tenant scoping rides on ``load_policy_year``; a product/category outside the
tenant simply has no matching voluntary categories → 404.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_editable, load_policy_year
from app.db.session import get_db
from app.models import PolicyYear
from app.models.category import Category
from app.schemas.api import (
    ProductVoluntaryRatesIn,
    ProductVoluntaryRatesOut,
    VoluntaryRateBand,
)
from app.services.plan_hydration import GROUP_RATE_FIELDS

router = APIRouter(tags=["voluntary-rates"])


def _age_banded_voluntary_categories(
    db: Session, policy_year_id: str, product_id: str
) -> list[Category]:
    """Voluntary categories of this product that price by age band (carry a
    ``voluntary_rates`` table / ``rate_basis == age_banded``). GPA-style flat
    voluntary options are deliberately excluded — they have no age-band table."""
    cats = db.execute(
        select(Category).where(
            Category.policy_year_id == policy_year_id,
            Category.product_id == product_id,
            Category.participation_model == "voluntary",
        )
    ).scalars().all()
    return [
        c
        for c in cats
        if (c.plan_assignments or {}).get("voluntary_rates") is not None
        or (c.plan_assignments or {}).get("rate_basis") == "age_banded"
    ]


def _validate_bands(bands: list[VoluntaryRateBand]) -> list[str]:
    errors: list[str] = []
    if not bands:
        errors.append("At least one age band is required.")
    for i, b in enumerate(bands):
        if b.min is not None and b.max is not None and b.min > b.max:
            errors.append(f"Band {i + 1} ({b.label}): min {b.min} > max {b.max}.")
        if b.rate < 0:
            errors.append(f"Band {i + 1} ({b.label}): rate must be >= 0.")
    return errors


@router.get(
    "/policy-years/{policy_year_id}/products/{product_id}/voluntary-rates",
    response_model=ProductVoluntaryRatesOut,
)
def get_voluntary_rates(
    policy_year_id: str,
    product_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> ProductVoluntaryRatesOut:
    cats = _age_banded_voluntary_categories(db, py.id, product_id)
    if not cats:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No age-banded voluntary plans for this product.",
        )
    # All copies are identical — read off the first category that actually carries
    # the table (the filter also admits rate_basis==age_banded rows that might lack
    # it, and the query has no stable order, so don't blindly take cats[0]).
    src = next(
        (c for c in cats if (c.plan_assignments or {}).get("voluntary_rates")),
        cats[0],
    )
    raw = (src.plan_assignments or {}).get("voluntary_rates") or []
    bands = [VoluntaryRateBand.model_validate(b) for b in raw]
    return ProductVoluntaryRatesOut(
        product_id=product_id, bands=bands, voluntary_plan_count=len(cats)
    )


@router.put(
    "/policy-years/{policy_year_id}/products/{product_id}/voluntary-rates",
    response_model=ProductVoluntaryRatesOut,
)
def set_voluntary_rates(
    policy_year_id: str,
    product_id: str,
    body: ProductVoluntaryRatesIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductVoluntaryRatesOut:
    assert_policy_year_editable(py)
    errors = _validate_bands(body.bands)
    if errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Malformed voluntary rate table.", "errors": errors},
        )
    cats = _age_banded_voluntary_categories(db, py.id, product_id)
    if not cats:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No age-banded voluntary plans for this product.",
        )
    bands_json = [b.model_dump() for b in body.bands]
    for c in cats:
        pa = dict(c.plan_assignments or {})
        for field in GROUP_RATE_FIELDS:
            pa.pop(field, None)
        pa["rate_basis"] = "age_banded"
        pa["voluntary_rates"] = bands_json
        # Reassign (don't mutate in place) so SQLAlchemy flags the JSON dirty.
        c.plan_assignments = pa
    db.flush()
    write_audit(
        db, user, action="update_voluntary_rates", entity_type="product",
        entity_id=product_id,
        after={"bands": len(bands_json), "plans": len(cats)},
    )
    db.commit()
    return ProductVoluntaryRatesOut(
        product_id=product_id, bands=body.bands, voluntary_plan_count=len(cats)
    )
