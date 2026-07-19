"""Per-product coverage periods — resolution and policy-year envelope.

A policy year carries a nominal coverage window; each product may override it
with its own period (`ProductTerm`). Overrides are stored sparsely, so this
module resolves the effective period for every product in a year (override or
the policy year's span) and rolls those up into the company-level envelope
(earliest start → latest end) shown at the policy-year level.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Plan, PolicyYear, Product, ProductTerm
from app.models.product_term import DEFAULT_GST_RATE


@dataclass(frozen=True)
class ResolvedTerm:
    product_id: str
    code: str
    display_name: str
    coverage_start: date
    coverage_end: date
    # True when the product inherits the policy year's span (no explicit dates —
    # the row may still exist to carry GST config).
    is_default: bool
    # Tri-state GST opinion: None = inherit (flex-scheme default), True = gross by
    # gst_rate, False = explicit "no GST". Slip amounts are always GST-exclusive.
    gst_included: bool | None = None
    gst_rate: float | None = None
    # Free cover limit (underwriting) — None = no FCL.
    free_cover_limit: float | None = None
    # Insurer-issued policy number — None until the placement is issued.
    policy_number: str | None = None


def gst_multiplier(included: bool | None, rate: float | None) -> float:
    """The premium gross-up factor for a GST config — 1.0 unless GST is on."""
    if not included:
        return 1.0
    valid = isinstance(rate, (int, float)) and not isinstance(rate, bool)
    return 1.0 + (rate if valid else DEFAULT_GST_RATE) / 100.0


def product_gst_multipliers(db: Session, policy_year_id: str) -> dict[str, float]:
    """``{product_id: gross-up factor}`` for products with an EXPLICIT GST opinion
    (``gst_included`` not None) in this policy year — an explicit "off" is present
    as ``1.0`` so it can override a flex-scheme default. Products absent from the
    map have no product-level opinion (they inherit; the caller decides the
    fallback)."""
    rows = db.execute(
        select(ProductTerm).where(
            ProductTerm.policy_year_id == policy_year_id,
            ProductTerm.gst_included.is_not(None),
        )
    ).scalars()
    return {t.product_id: gst_multiplier(t.gst_included, t.gst_rate) for t in rows}


def product_ids_in_year(db: Session, policy_year_id: str) -> set[str]:
    """Products that appear in this policy year — the distinct product ids
    referenced by its plans or (product-bound) categories. Mirrors the set the
    product-setup list builds, so coverage periods line up with configured
    products."""
    cat_pids = set(
        db.execute(
            select(Category.product_id).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id.is_not(None),
            )
        ).scalars()
    )
    plan_pids = set(
        db.execute(
            select(Plan.product_id).where(Plan.policy_year_id == policy_year_id)
        ).scalars()
    )
    return {pid for pid in (cat_pids | plan_pids) if pid}


def resolve_terms(db: Session, py: PolicyYear) -> list[ResolvedTerm]:
    """Effective coverage period for every product in the policy year.

    Products with a `ProductTerm` row use it; the rest inherit the policy year's
    span (`is_default=True`). Sorted by product code for a stable UI order.
    """
    pids = product_ids_in_year(db, py.id)
    if not pids:
        return []
    products = {
        p.id: p
        for p in db.execute(select(Product).where(Product.id.in_(pids))).scalars()
    }
    overrides = {
        t.product_id: t
        for t in db.execute(
            select(ProductTerm).where(
                ProductTerm.policy_year_id == py.id,
                ProductTerm.product_id.in_(pids),
            )
        ).scalars()
    }
    out: list[ResolvedTerm] = []
    for pid in pids:
        product = products.get(pid)
        if product is None:  # product row vanished — skip orphan reference
            continue
        term = overrides.get(pid)
        has_dates = term is not None and term.coverage_start is not None
        out.append(
            ResolvedTerm(
                product_id=pid,
                code=product.code,
                display_name=product.display_name,
                coverage_start=term.coverage_start if has_dates else py.start_date,
                coverage_end=(
                    term.coverage_end
                    if term is not None and term.coverage_end is not None
                    else py.end_date
                ),
                is_default=not has_dates,
                gst_included=term.gst_included if term else None,
                gst_rate=term.gst_rate if term else None,
                free_cover_limit=term.free_cover_limit if term else None,
                policy_number=term.policy_number if term else None,
            )
        )
    out.sort(key=lambda r: (r.code, r.display_name))
    return out


def _envelope_from_pairs(
    py: PolicyYear, pairs: list[tuple[date, date]]
) -> tuple[date, date]:
    if not pairs:
        return py.start_date, py.end_date
    return min(p[0] for p in pairs), max(p[1] for p in pairs)


def envelope_from_terms(
    py: PolicyYear, resolved: list[ResolvedTerm]
) -> tuple[date, date]:
    """Roll already-resolved per-product periods into the company envelope —
    lets callers that already hold the resolved list avoid a second query."""
    return _envelope_from_pairs(
        py, [(r.coverage_start, r.coverage_end) for r in resolved]
    )


def envelope_for(db: Session, py: PolicyYear) -> tuple[date, date]:
    """Company-level coverage window for one policy year: earliest product start
    → latest product end, falling back to the policy year's own span when no
    products are configured."""
    return envelope_from_terms(py, resolve_terms(db, py))


def envelopes_for(
    db: Session, policy_years: list[PolicyYear]
) -> dict[str, tuple[date, date]]:
    """Batched envelope for many policy years — three queries regardless of
    count, for the list endpoint hot path."""
    if not policy_years:
        return {}
    py_ids = [py.id for py in policy_years]

    by_year: dict[str, set[str]] = {pid: set() for pid in py_ids}
    for pyid, prod_id in db.execute(
        select(Plan.policy_year_id, Plan.product_id).where(
            Plan.policy_year_id.in_(py_ids)
        )
    ):
        if prod_id:
            by_year[pyid].add(prod_id)
    for pyid, prod_id in db.execute(
        select(Category.policy_year_id, Category.product_id).where(
            Category.policy_year_id.in_(py_ids),
            Category.product_id.is_not(None),
        )
    ):
        if prod_id:
            by_year[pyid].add(prod_id)

    overrides: dict[tuple[str, str], tuple[date, date]] = {}
    for t in db.execute(
        select(ProductTerm).where(ProductTerm.policy_year_id.in_(py_ids))
    ).scalars():
        # GST-only rows carry no dates — they don't shape the envelope.
        if t.coverage_start is not None and t.coverage_end is not None:
            overrides[(t.policy_year_id, t.product_id)] = (
                t.coverage_start,
                t.coverage_end,
            )

    result: dict[str, tuple[date, date]] = {}
    for py in policy_years:
        pairs = [
            overrides.get((py.id, pid), (py.start_date, py.end_date))
            for pid in by_year.get(py.id, set())
        ]
        result[py.id] = _envelope_from_pairs(py, pairs)
    return result
