"""Slip-derived dependant pricing: per-tier family increments / per-dependant
rates, and the dependant OPTION rows that stick to the employee's elected plan.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Product
from app.schemas.api import PlanFinancials
from app.services.cohort_tiers import tier_key
from app.services.el_report_rules import positive_number, reviewed_si_caps, with_si_cap
from app.services.flex_pricing.bag import DependantMode, _tier_row
from app.services.plan_hydration import basis_amount, member_financials

# Placement-slip ``rate_tiers`` labels → canonical role (EO is the baseline rate).
_SLIP_TIER_ROLE: dict[str, str] = {"ES": "spouse", "EC": "child", "EF": "both"}


def _tier_rate(cell: object) -> float | None:
    """The per-member ``rate`` from a slip ``rate_tiers`` cell (``{rate, premium}``)."""
    if isinstance(cell, dict):
        r = cell.get("rate")
        return float(r) if isinstance(r, (int, float)) else None
    return None


def slip_family_increments(rate_tiers: object) -> dict[str, float]:
    """The per-role dependant increments over Employee-Only from one tier's slip
    ``rate_tiers`` (``{EO/ES/EC/EF: {rate, premium}}``).

    The increment for a role is its rate minus the EO rate. A role whose rate is
    0.0 is an UNPRICED column (slips often list only EO/EF) — not a free tier — so
    it's omitted and the caller falls back to the manual matrix. Returns {} when
    there's no EO baseline."""
    if not isinstance(rate_tiers, dict):
        return {}
    eo = _tier_rate(rate_tiers.get("EO"))
    if eo is None:
        return {}
    incr: dict[str, float] = {}
    for label, role in _SLIP_TIER_ROLE.items():
        rate = _tier_rate(rate_tiers.get(label))
        if rate is not None and rate != 0.0:
            incr[role] = round(rate - eo, 2)
    return incr


def _slip_dependant_for_tier(fin: PlanFinancials | None) -> dict[str, float]:
    """The slip-derived dependant pricing for ONE tier, as either:

    - family increments ``{spouse, child, both}`` from an EO/ES/EC/EF rate table, or
    - a per-dependant rate ``{"per_pax": rate}`` from a flat per-member table that
      lists a separate Dependents rate (e.g. GCGP "1 - Dependents $396.90", or the
      combined "Employees / Dependents" rate). The per-dependant rate is the
      dependant's FULL premium (drawn per covered dependant, additive over the
      employee tag), not an increment.

    ``{}`` when the slip carries no dependant pricing for the tier."""
    incr = slip_family_increments(fin.rate_tiers if fin else None)
    if incr:
        return incr
    dr = getattr(fin, "dependant_rate", None) if fin is not None else None
    if isinstance(dr, (int, float)):
        return {"per_pax": round(float(dr), 2)}
    return {}


def family_slip_index(
    db: Session, policy_year_id: str
) -> dict[str, dict[str, dict[str, float]]]:
    """``{product_id: {tier_key: <slip dependant pricing>}}`` — the slip-derived
    dependant pricing for every electable tier in the year (see
    ``_slip_dependant_for_tier``). Each tier value is EITHER family increments
    ``{spouse, child, both}`` (EO/ES/EC/EF slip) OR ``{"per_pax": rate}`` (a flat
    per-member table with a Dependents rate). Mirrors ``slip_premium_index``'s key
    shape so the employee tag and the dependant tag resolve a tier identically."""
    from app.services.cohort_tiers import list_product_tiers

    return _merge_family_overlay(
        family_slip_index_from(list_product_tiers(db, policy_year_id)),
        dependant_option_overlay(db, policy_year_id),
    )


def family_slip_index_from(
    tier_sets: dict[str, Any],
    overlay: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """``family_slip_index`` from already-loaded ``ProductTierSet``s (see
    ``slip_premium_index_from``)."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for ts in tier_sets.values():
        prod_row: dict[str, dict[str, float]] = {}
        for t in ts.tiers:
            dep = _slip_dependant_for_tier(t.financials)
            if dep:
                prod_row[tier_key(t.tier_category_id, t.plan_code)] = dep
        if prod_row:
            out[ts.product_id] = prod_row
    return _merge_family_overlay(out, overlay) if overlay else out


# ── Dependant OPTION rows (slip categories that stick to the employee plan) ──
#
# The parser extracts dependant-scope categories (`plan_assignments.member_scope
# == "dependant"`): GPA "Spouse (Option N)" / "Child (Option N)" rows, VDL's
# "GHS - Dependants" sheet, GTL/GCI Spouse/Child plan rows. A dependant never
# elects its OWN option level — the covered dependant rides the EMPLOYEE's
# elected plan, so each dependant row must be keyed to the employee tier(s) it
# serves. Three linkage rules, unambiguous only:
#
# 1. Marker match — "Spouse (Option 2)" serves employee tiers whose category
#    carries the same "(Option 2)" marker (GPA).
# 2. Composition rows (rate_tiers keyed SO/CO/FO/SC — a dependants sheet) serve
#    the employee tier with the SAME plan code (VDL mirrors codes by design).
# 3. A SOLE spouse row (and/or sole child row) with no marker serves every
#    employee tier.
#
# 4. MULTIPLE unmarked rows per role (e.g. CDL GTL's three Spouse levels whose
#    "Plan 1-6" numbers collide with employee plans by renumbering) are
#    freestanding OPTION LEVELS the slip leaves to the member: they attach to
#    every employee tier as electable ``choices`` — the member (broker) picks a
#    cover level per role at election time (``dependant_option_ids``) and each
#    covered dependant prices from the chosen row. Covered dependants with NO
#    chosen level stay unpriced — surfaced by the unpriced-election guard —
#    rather than mislinked.

_OPTION_MARKER = re.compile(r"\(option\b[^)]*\)", re.IGNORECASE)
_DEP_ROLE_RE = re.compile(r"^\s*(spouse|child(?:ren)?)\b", re.IGNORECASE)
# Dependant-sheet composition tier keys → canonical role.
_COMPOSITION_ROLE: dict[str, str] = {"SO": "spouse", "CO": "child", "SC": "both", "FO": "both"}


def _option_marker(text: str | None) -> str | None:
    m = _OPTION_MARKER.search(text or "")
    return re.sub(r"\s+", " ", m.group(0).lower()) if m else None


def dependant_option_role(text: str | None) -> str | None:
    """'spouse' | 'child' from a dependant option row's description, None when
    the description names neither role."""
    m = _DEP_ROLE_RE.match(text or "")
    if not m:
        return None
    return "spouse" if m.group(1).lower().startswith("spouse") else "child"


def _dependant_basis(pa: dict[str, Any]) -> float | None:
    amount = basis_amount(pa)
    if amount is None:
        amount = positive_number(pa.get("sum_insured"))
    cap = positive_number(pa.get("max_sum_insured"))
    return min(amount, cap) if amount is not None and cap is not None else amount


def _dependant_option_spec(
    pa: dict[str, Any]
) -> float | dict[str, Any] | None:
    """A dependant option row's per-dependant price spec: a flat amount, or an
    age-banded ``{basis, voluntary_rates}`` spec (same dual shape as
    ``slip_premium_index``) priced by the DEPENDANT's age at draw time.

    A dependant-scope row carries no headcount, so its ``sum_insured`` IS the
    per-dependant cover amount (a GPA option's SI) — unlike employee rows where
    group SI must reduce via ``basis``."""
    if not isinstance(pa, dict):
        return None
    amount = _dependant_basis(pa)
    vol = pa.get("voluntary_rates")
    if isinstance(vol, list) and vol and amount is not None:
        return {"basis": amount, "voluntary_rates": [dict(b) for b in vol]}
    rate = pa.get("premium_rate")
    if (
        pa.get("rate_basis") == "per_1000_si"
        and isinstance(rate, (int, float))
        and amount is not None
    ):
        return round(amount / 1000.0 * float(rate), 2)
    fin = member_financials(pa, None)
    prem = fin.annual_premium if fin else None
    return round(float(prem), 2) if isinstance(prem, (int, float)) else None


def _composition_amounts(pa: dict[str, Any]) -> dict[str, float]:
    """Standalone family amounts from a dependants-sheet row's SO/CO/FO/SC
    ``rate_tiers`` (the rate IS the dependant premium, not an increment)."""
    tiers = pa.get("rate_tiers") if isinstance(pa, dict) else None
    out: dict[str, float] = {}
    if not isinstance(tiers, dict):
        return out
    for label, cell in tiers.items():
        role = _COMPOSITION_ROLE.get(str(label).upper())
        rate = _tier_rate(cell)
        if role and rate is not None and rate > 0:
            out.setdefault(role, rate)
    return out


def dependant_option_overlay(
    db: Session, policy_year_id: str
) -> dict[str, dict[str, dict[str, Any]]]:
    """``{product_id: {employee_tier_key: row}}`` derived from dependant-scope
    categories, where a row is ``{"options": {role: spec}}`` (per-dependant
    option pricing linked to the tier), ``{"choices": {role: [choice]}}``
    (freestanding option LEVELS the member elects — see rule 4), or standalone
    family amounts ``{spouse/child/both: amt}`` (dependants-sheet composition
    rows). Merged UNDER the employee-tier slip data by ``_merge_family_overlay``
    (an employee tier's own EO/ES/EC/EF table wins)."""
    cats = list(
        db.execute(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id.is_not(None),
            )
        ).scalars()
    )
    dep_cats = [
        c for c in cats
        if isinstance(c.plan_assignments, dict)
        and c.plan_assignments.get("member_scope") == "dependant"
    ]
    if not dep_cats:
        return {}
    caps = reviewed_si_caps(db, policy_year_id)
    caps_by_id = {
        pid: caps.get(code.upper())
        for pid, code in db.execute(select(Product.id, Product.code).where(
            Product.id.in_({c.product_id for c in dep_cats if c.product_id is not None})
        ))
    }
    dep_ids = {c.id for c in dep_cats}
    emp_cats = [c for c in cats if c.id not in dep_ids]

    out: dict[str, dict[str, dict[str, Any]]] = {}

    def _add(pid: str, key: str, kind: str, payload: dict[str, Any]) -> None:
        row = out.setdefault(pid, {}).setdefault(key, {})
        if kind == "options":
            row.setdefault("options", {})
            for role, spec in payload.items():
                row["options"].setdefault(role, spec)
        elif kind == "choices":
            row.setdefault("choices", {})
            for role, choice_list in payload.items():
                row["choices"].setdefault(role, choice_list)
        else:  # standalone family amounts
            for role, amt in payload.items():
                row.setdefault(role, amt)

    for pid in {c.product_id for c in dep_cats if c.product_id is not None}:
        p_deps = [c for c in dep_cats if c.product_id == pid]
        p_emps = [c for c in emp_cats if c.product_id == pid]
        emp_keys_all = [
            tier_key(c.id, (c.plan_assignments or {}).get("plan_code")) for c in p_emps
        ]
        # Role rows (Spouse/Child) grouped for the sole-row rule.
        role_rows: dict[str, list[Category]] = {}
        for dc in p_deps:
            pa = with_si_cap(dc.plan_assignments or {}, caps_by_id.get(pid))
            comp = _composition_amounts(pa)
            if comp:
                # Rule 2: composition row → same-plan employee tiers.
                dep_plan = str(pa.get("plan_code") or "")
                for ec in p_emps:
                    if str((ec.plan_assignments or {}).get("plan_code") or "") == dep_plan:
                        _add(pid, tier_key(ec.id, dep_plan), "family", comp)
                continue
            role = dependant_option_role(dc.raw_description)
            if role is None:
                continue
            spec = _dependant_option_spec(pa)
            if spec is None:
                continue
            marker = _option_marker(dc.raw_description)
            if marker:
                # Rule 1: marker match against employee tier categories.
                for ec in p_emps:
                    if _option_marker(ec.raw_description) == marker:
                        _add(
                            pid,
                            tier_key(ec.id, (ec.plan_assignments or {}).get("plan_code")),
                            "options",
                            {role: spec},
                        )
            else:
                role_rows.setdefault(role, []).append(dc)
        # Rule 3: a SOLE unmarked row per role applies to every employee tier.
        # Rule 4: MULTIPLE unmarked rows per role are freestanding option LEVELS
        # — attached to every employee tier as electable choices; the elected
        # level (``dependant_option_ids``) selects which row prices each
        # covered dependant.
        for role, rows in role_rows.items():
            if len(rows) == 1:
                spec = _dependant_option_spec(
                    with_si_cap(rows[0].plan_assignments or {}, caps_by_id.get(pid))
                )
                if spec is None:
                    continue
                for key in emp_keys_all:
                    _add(pid, key, "options", {role: spec})
                continue
            choices = []
            for dc in rows:
                pa = with_si_cap(dc.plan_assignments or {}, caps_by_id.get(pid))
                spec = _dependant_option_spec(pa)
                if spec is None:
                    continue
                cover = _dependant_basis(pa)
                choices.append({
                    "category_id": dc.id,
                    "label": (dc.raw_description or "").strip() or role.title(),
                    "sum_insured": cover,
                    "spec": spec,
                })
            if not choices:
                continue
            # Stable, meaningful order: ascending cover amount.
            choices.sort(key=lambda c: (c["sum_insured"] is None, c["sum_insured"] or 0.0))
            for key in emp_keys_all:
                _add(pid, key, "choices", {role: choices})
    return out


def _merge_family_overlay(
    base: dict[str, dict[str, dict[str, Any]]],
    overlay: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Merge dependant-option rows UNDER the tier-derived index: an employee
    tier that already carries slip dependant pricing (its own EO/ES/EC/EF
    table or Dependents rate) keeps it; option rows fill the gaps."""
    for pid, rows in overlay.items():
        target = base.setdefault(pid, {})
        for key, row in rows.items():
            target.setdefault(key, row)
    return base


def _slip_dependant_shape(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str
) -> str | None:
    """The slip's dependant SHAPE for ONE tier — ``per_pax`` (a per-dependant rate),
    ``family_group`` (an EO/ES/EC/EF table), or None when the slip carries no
    dependant pricing for the tier. Tier-scoped (with the same plan-code fallback as
    the resolvers) so a product whose tiers mix the two formats classifies each tier
    by its own slip data rather than one mode for the whole product."""
    row = _tier_row((family_slip_idx or {}).get(product_id), key)
    if not isinstance(row, dict) or not row:
        return None
    if "options" in row or "choices" in row:
        return DependantMode.slip_options
    return DependantMode.per_pax if "per_pax" in row else DependantMode.family_group


def per_pax_slip_rate(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str
) -> float | None:
    """The slip-derived per-dependant rate for a (product, tier) — None when unset.
    Mirrors ``family_slip_incr``'s plan-code fallback."""
    row = _tier_row((family_slip_idx or {}).get(product_id), key)
    rate = row.get("per_pax") if isinstance(row, dict) else None
    return float(rate) if isinstance(rate, (int, float)) else None


def family_slip_incr(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str, role: str
) -> float | None:
    """The slip-derived incremental dependant amount for a (product, tier, role).

    Mirrors ``slip_premium_for``'s plan-code fallback so a tier whose category half
    differs from the configured key still resolves on an unambiguous plan_code."""
    incr = _tier_row((family_slip_idx or {}).get(product_id), key)
    amt = incr.get(role) if isinstance(incr, dict) else None
    return float(amt) if isinstance(amt, (int, float)) else None


def dependant_option_choices(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str
) -> dict[str, list[dict[str, Any]]]:
    """The freestanding dependant option LEVELS for a (product, tier):
    ``{role: [{category_id, label, sum_insured, spec}]}`` (rule 4 of
    ``dependant_option_overlay``). ``{}`` when the tier has none — its dependant
    pricing is either linked (``options``) or absent.

    Returns per-choice COPIES: the overlay aliases one list across every tier
    key, so handing callers the internal dicts would let a single mutation
    corrupt the shared index for the product's whole tier set."""
    row = _tier_row((family_slip_idx or {}).get(product_id), key)
    choices = row.get("choices") if isinstance(row, dict) else None
    if not isinstance(choices, dict):
        return {}
    return {role: [dict(c) for c in rows] for role, rows in choices.items()}
