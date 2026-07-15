"""Shared product/plan resolution for the enrollment module.

Overrides, elections, and bulk updates all need to turn a ``product_code`` into a
concrete ``Product`` scoped to a policy year, and to know which plan tiers are
electable for that product. Centralized here so the rules stay consistent.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Plan, PolicyYear, Product
from app.services.product_terms import product_ids_in_year


def resolve_product_by_code(
    db: Session, py: PolicyYear, product_code: str
) -> Product | None:
    """Find the Product for ``product_code`` usable in this policy year.

    Prefers a product already configured in the year (via its plans/categories);
    otherwise a catalog product matching the code that is global or owned by this
    client. Tenant-specific rows win over global ones. Returns None if unresolved.
    """
    in_year = product_ids_in_year(db, py.id)
    candidates = list(
        db.execute(
            select(Product).where(
                Product.code == product_code,
                tenant_or_global(Product.client_id, py.client_id),
            )
        ).scalars()
    )
    if not candidates:
        return None
    # Prefer a product that is actually configured in this year, then a
    # client-owned catalog row, then a global one.
    candidates.sort(
        key=lambda p: (p.id in in_year, p.client_id is not None), reverse=True
    )
    return candidates[0]


def available_plan_codes(db: Session, policy_year_id: str, product_id: str) -> set[str]:
    """Electable plan tier codes for a product in a policy year (its Plan rows)."""
    return set(
        db.execute(
            select(Plan.code).where(
                Plan.policy_year_id == policy_year_id,
                Plan.product_id == product_id,
            )
        ).scalars()
    )
