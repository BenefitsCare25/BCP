"""Keep listed cover and joiner rules in step when slip categories are rebuilt.

A placement-slip re-upload replaces unreviewed categories with fresh rows, so
listed cover loses its category link (``category_id`` is set null) and the
listing-learned rules go with the old rows. The company's saved listing
mapping (listing wording -> product + category signature + plan) re-links each
listed person to the new rows, and the rules are derived again from the
re-linked cover — no listing re-upload needed.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, Product
from app.models.employee_listing import ElLayoutProfile, ListingAssignment
from app.services.el_import.joiner_rules import derive_product_rules, persist_rules
from app.services.el_import.mapping import NOT_COVERED, normalize_label
from app.services.eligibility_mapping import category_signature


def refresh_listing_links(db: Session, client_id: str, policy_year_id: str) -> tuple[int, int]:
    """Re-link listed cover to current categories, then re-derive joiner rules.

    Returns (assignments re-linked, rules written). Flushes, never commits.
    """
    profile = db.execute(
        select(ElLayoutProfile).where(ElLayoutProfile.client_id == client_id)
    ).scalar_one_or_none()
    assignments = list(db.execute(
        select(ListingAssignment).where(ListingAssignment.policy_year_id == policy_year_id)
    ).scalars())
    if profile is None or not assignments:
        return 0, 0
    categories = {c.id: c for c in db.execute(
        select(Category).where(Category.policy_year_id == policy_year_id)
    ).scalars()}
    codes = {p.id: p.code for p in db.execute(
        select(Product).where(Product.id.in_({c.product_id for c in categories.values()}))
    ).scalars()}
    by_identity = {
        (codes.get(c.product_id or ""), category_signature(c.raw_description or c.display_name),
         str((c.plan_assignments or {}).get("plan_code") or "")): c.id
        for c in categories.values()
    }
    label_map = profile.label_map or {}

    relinked = 0
    employee_category: dict[tuple[str, str], str] = {}
    for a in sorted(assignments, key=lambda x: x.dependant_id is not None):
        if a.category_id in categories:
            if a.dependant_id is None:
                employee_category[(a.employee_id, a.product_id)] = a.category_id
            continue
        target = None
        if a.listed_category:
            entry = (label_map.get(str(a.block_index)) or {}).get(
                normalize_label(a.listed_category)
            )
            if isinstance(entry, dict) and not entry.get(NOT_COVERED):
                target = by_identity.get((
                    entry.get("product_code"), entry.get("category_signature") or "",
                    str(entry.get("plan_code") or ""),
                ))
        elif a.dependant_id is not None:
            target = employee_category.get((a.employee_id, a.product_id))
        if target is not None:
            a.category_id = target
            relinked += 1
            if a.dependant_id is None:
                employee_category[(a.employee_id, a.product_id)] = target
    db.flush()

    employees = {e.id: e for e in db.execute(
        select(Employee).where(Employee.policy_year_id == policy_year_id)
    ).scalars()}
    # Every listed employee counts for every product of the listing's blocks:
    # no listed category under a product is "not covered" there (a director
    # under the main GHS policy), exactly as in the import preview.
    listed: dict[str, dict[str, str | None]] = {}
    for a in assignments:
        if a.dependant_id is None and a.employee_id in employees:
            listed.setdefault(a.employee_id, {})[codes.get(a.product_id, "")] = a.category_id
    block_codes = {c for codes_ in (profile.block_products or {}).values() for c in codes_}
    rows: dict[str, list[tuple[str, dict[str, Any], str | None]]] = {}
    for code in sorted(block_codes & set(codes.values())):
        rows[code] = [
            (employees[e].staff_id or "", employees[e].attribute_values or {},
             cover.get(code))
            for e, cover in listed.items()
        ]
    rules = [r for code, product_rows in sorted(rows.items())
             if (r := derive_product_rules(code, product_rows)) is not None]
    written = persist_rules(categories, rules)
    db.flush()
    return relinked, written
