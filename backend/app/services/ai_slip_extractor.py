"""AI fallback for placement-slip sheets the deterministic parser can't read.

When reconciliation flags a product as ``needs_attention`` (no Schedule of
Benefits parsed, or categories citing plan codes no schedule covers), and the
tenant has an AI provider configured, re-extract that one sheet via the AI
gateway, rebuild the canonical ``ProductSlip``, and run it back through the same
reconciliation. The deterministic parser stays the default fast path; AI is
best-effort and never blocks the upload.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import load_ai_config
from app.services import product_registry
from app.services.ai_gateway import extract_product_structure_for_slip
from app.services.excel_reader import open_workbook
from app.services.placement_slip_parser import (
    ExtractedBenefitItem,
    ExtractedCategory,
    ExtractedPlan,
    PlacementSlip,
    ProductSlip,
)
from app.services.slip_parsing.walk import _category_member_scope
from app.services.slip_reconcile import ReconciledSlip, reconcile_slip

logger = logging.getLogger(__name__)

# Reconciliation outcomes that mean "this product is sound".
_SOUND = {"consistent", "fan_out", "assign_default"}

# rate_basis values the AI may emit (matches the tool schema enum).
_AI_RATE_BASES = frozenset(
    {"per_1000_si", "per_member", "tiered", "flat", "annual_flat", "earnings_based"}
)


def _num(value) -> float | None:
    """Defensively coerce an AI-emitted number; junk → None, never a crash."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _count(value) -> int | None:
    n = _num(value)
    return round(n) if n is not None and n >= 0 else None


def _tiers(value) -> dict[str, dict[str, float]] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, dict[str, float]] = {}
    for key, tv in value.items():
        if not isinstance(tv, dict):
            continue
        rate, prem = _num(tv.get("rate")), _num(tv.get("premium"))
        if rate is None and prem is None:
            continue
        out[str(key).strip().upper()] = {"rate": rate or 0.0, "premium": prem or 0.0}
    return out or None


def _rate_basis(value, product_code: str) -> str | None:
    """Validate the AI's rate_basis: must be a known value and, for registry-
    known products, one the product can actually persist."""
    if value not in _AI_RATE_BASES:
        return None
    if product_registry.is_known(product_code):
        entry = product_registry.resolve_entry(product_code)
        if value not in entry.rate_models:
            return None
    return value


def _build_product_slip(template: ProductSlip, payload) -> ProductSlip | None:
    code = template.product_code
    cats = tuple(
        ExtractedCategory(
            insured=str(c.get("insured") or ""),
            category=str(c.get("category") or "").strip(),
            participation=str(c.get("participation") or ""),
            plan_code=str(c.get("plan_code") or "").strip(),
            source_row=0,
            num_employees=_count(c.get("num_employees")),
            basis=(str(c["basis"]).strip() if c.get("basis") else None),
            sum_insured=_num(c.get("sum_insured")),
            premium_rate=_num(c.get("premium_rate")),
            annual_premium=_num(c.get("annual_premium")),
            rate_basis=_rate_basis(c.get("rate_basis"), code),
            rate_tiers=_tiers(c.get("rate_tiers")),
            estimated_annual_earnings=_num(c.get("estimated_annual_earnings")),
            dependant_rate=_num(c.get("dependant_rate")),
            member_scope=_category_member_scope(
                str(c.get("category") or "").strip(),
                code,
                str(c.get("participation") or ""),
            ),
        )
        for c in payload.categories
        if str(c.get("category") or "").strip()
    )
    plans = tuple(
        ExtractedPlan(
            code=str(p.get("code") or "").strip(),
            display_name=str(p.get("display_name") or f"Plan {p.get('code')}").strip(),
            cover_description=(str(p["cover_description"]) if p.get("cover_description") else None),
            items=tuple(
                ExtractedBenefitItem(
                    number=str(it.get("number") or ""),
                    name=str(it.get("name") or "").strip(),
                    value=(str(it["value"]) if it.get("value") is not None else None),
                    note=(str(it["note"]) if it.get("note") else None),
                )
                for it in (p.get("items") or [])
                if str(it.get("name") or "").strip()
            ),
        )
        for p in payload.plans
        if str(p.get("code") or "").strip()
    )
    if not cats and not plans:
        return None
    return replace(template, categories=cats, plans=plans)


def _ai_extract_product(
    db: Session,
    client_id: str,
    policy_year_id: str,
    grid: list[list],
    product: ProductSlip,
) -> ProductSlip | None:
    """Best-effort AI re-extraction of one product sheet. Returns None on any failure."""
    try:
        result = extract_product_structure_for_slip(
            db,
            client_id=client_id,
            policy_year_id=policy_year_id,
            product_code=product.product_code,
            grid=grid,
        )
    except Exception:
        logger.exception(
            "AI slip extraction failed for sheet %s (product %s)",
            product.sheet, product.product_code,
        )
        return None
    return _build_product_slip(product, result)


def _resolved(product: ProductSlip) -> tuple[int, int]:
    """(# categories whose plan_code matches a plan, # categories naming a plan)."""
    codes = {p.code.strip() for p in product.plans}
    cats = [c for c in product.categories if (c.plan_code or "").strip()]
    resolved = sum(1 for c in cats if (c.plan_code or "").strip() in codes)
    return resolved, len(cats)


def _rated(product: ProductSlip) -> int:
    """# categories carrying any pricing signal (rate, tiers, or premium)."""
    return sum(
        1
        for c in product.categories
        if c.premium_rate is not None
        or c.rate_tiers
        or c.annual_premium is not None
    )


def _ai_improves(before: ProductSlip, ai: ProductSlip, before_sound: bool,
                 ai_sound: bool) -> bool:
    """Accept AI output only when it genuinely improves coverage and isn't lossy.

    Improves = resolves strictly more categories, turns an unsound product
    sound, or — without losing plan-code resolution — prices strictly more
    categories (the v2 schema returns rates/SI/tiers). Not-lossy = keeps at
    least half the deterministic result's named categories, so a thin AI answer
    that trivially 'reconciles' can't win by discarding most of the sheet.
    """
    b_res, b_tot = _resolved(before)
    a_res, a_tot = _resolved(ai)
    improves = (
        a_res > b_res
        or (ai_sound and not before_sound)
        or (a_res >= b_res and _rated(ai) > _rated(before))
    )
    not_lossy = a_tot >= max(1, b_tot) * 0.5
    return improves and not_lossy


def maybe_ai_augment(
    db: Session,
    client_id: str,
    policy_year_id: str,
    path: Path | str,
    reconciled: ReconciledSlip,
) -> ReconciledSlip:
    """Augment flagged products with AI extraction when a provider is configured.

    No-op (returns the input unchanged) when AI is not configured or nothing
    needs attention — so the deterministic path is unaffected without a key.
    Each accepted product is reconciled once and spliced in; unchanged products
    keep their original (per-product) reconciliation, so there's no full re-pass.
    """
    flagged = {d.sheet for d in reconciled.diagnostics if d.needs_attention}
    if not flagged or load_ai_config(db, client_id) is None:
        return reconciled

    products = list(reconciled.slip.products)
    diagnostics = list(reconciled.diagnostics)
    changed = False

    with open_workbook(path) as wb:
        sheet_names = set(wb.sheet_names)
        for i, product in enumerate(products):
            before_diag = diagnostics[i]
            if product.sheet not in flagged or product.sheet not in sheet_names:
                continue
            ai_product = _ai_extract_product(
                db, client_id, policy_year_id, wb.sheet(product.sheet).rows, product
            )
            if ai_product is None:
                continue
            ai_rec = reconcile_slip(PlacementSlip(client="", products=(ai_product,)))
            ai_slip_product = ai_rec.slip.products[0]
            ai_diag = ai_rec.diagnostics[0]
            if _ai_improves(
                product, ai_slip_product,
                before_diag.reconciliation in _SOUND, ai_diag.reconciliation in _SOUND,
            ):
                products[i] = ai_slip_product
                diagnostics[i] = replace(ai_diag, used_ai=True)
                changed = True

    if not changed:
        return reconciled
    return ReconciledSlip(
        slip=replace(reconciled.slip, products=tuple(products)),
        diagnostics=diagnostics,
    )
