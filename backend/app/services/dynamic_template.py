"""Synthesize a product-setup template from a product's parsed slip data.

The guided setup form is driven by a ``ProductTemplate`` (structure only — field
labels, plans, tiers, benefit-line definitions). Hand-authored templates live as
JSON files (see ``product_templates.py``), but authoring one per product doesn't
scale. Instead, for any product a client's placement slip touched, we already
persist the structure as ``Plan`` rows (the Schedule of Benefits) and
``Category`` rows (plan codes + premium tiers). This module reconstructs a
``ProductTemplate`` skeleton from those rows on demand, so every detected product
is set-up-able without a JSON file.

Values are never sourced here — only structure. The broker's values come from the
slip pre-fill (``slip_to_setup.build_setup_answers``) or their own input.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Plan, Product
from app.services.form_profiles import infer_profile
from app.services.product_templates import (
    BenefitKind,
    ParticipationModel,
    ProductTemplate,
    TemplateBenefitItem,
    TemplateField,
    TemplatePlan,
    TemplateSubItem,
    TemplateTier,
)

# Generic header fields — product-agnostic, reusing GHS's field ids so slip
# pre-fill (slip_to_setup) and suggestions key off the same names. Office Address
# + Type of Administration are added by the ProductTemplate validator, and the
# eligibility set is owned entirely by product_templates.standard_eligibility_fields
# (the validator injects it), so neither is enumerated here.
GENERIC_HEADER_FIELDS: tuple[TemplateField, ...] = (
    TemplateField(id="policyholder", label="Policyholder"),
    TemplateField(id="insured", label="Insured"),
    TemplateField(id="business", label="Business"),
    TemplateField(id="period_of_insurance", label="Period of Insurance"),
    TemplateField(id="insurer", label="Insurer"),
    TemplateField(id="policy_no", label="Policy No."),
)
_TIER_LABELS = {
    "EO": "Employee Only",
    "ES": "Employee + Spouse",
    "EC": "Employee + Children",
    "EF": "Employee + Family",
    # Dependant-only tiers (standalone dependant pricing — VDL dependants
    # sheets, Spouse/Child rate columns). Registry scheme labels.
    "SO": "Spouse Only",
    "CO": "Child(ren) Only",
    "FO": "Family Only",
    "SC": "Spouse & Child(ren)",
}
# Stable display order: composite employee tiers first, dependant tiers after;
# unknown codes keep insertion order.
_TIER_ORDER = {
    code: i for i, code in enumerate(("EO", "ES", "EC", "EF", "SO", "CO", "SC", "FO"))
}
_VALID_PARTICIPATION = {"standard", "extended", "eo_only"}
# Benefit-line kinds that mark a curated, non-line layout the slip parser can't
# reproduce (outpatient Yes/No + copay matrices, dread-disease/scale lists). When
# a file template uses any of these, its structure wins over the slip's lines.
_CURATED_SOB_KINDS = frozenset({"boolean", "copay", "list", "scale"})


def _participation_model(product: Product) -> ParticipationModel:
    # product.participation_model is a (str, Enum) member; str(member) yields
    # "ParticipationModel.standard", so read .value (falling back for plain str).
    raw = getattr(product, "participation_model", None)
    value = getattr(raw, "value", raw) or ""
    return value if value in _VALID_PARTICIPATION else "standard"  # type: ignore[return-value]


def _plan_label(code: str, display_name: str | None) -> str:
    """A human label for a plan, without doubling the word "Plan".

    Slip-derived plan codes are inconsistent: some already read like
    "Plan A - International / Asia", others are bare like "1 / International".
    Blindly prefixing ``f"Plan {code}"`` produced "Plan Plan A …". Use the
    display name when present, else prefix only if the code doesn't already
    start with "plan"."""
    dn = (display_name or "").strip()
    if dn:
        return dn
    c = (code or "").strip()
    return c if c.lower().startswith("plan") else f"Plan {c}"


def _plan_specs(plans: list[Plan], cats: list[Category]) -> list[tuple[str, str]]:
    """(code, label) per plan — from Plan rows, falling back to category plan codes."""
    specs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in plans:
        if p.code and p.code not in seen:
            seen.add(p.code)
            specs.append((p.code, _plan_label(p.code, p.display_name)))
    if not specs:
        for c in cats:
            code = str((c.plan_assignments or {}).get("plan_code") or "").strip()
            if code and code not in seen:
                seen.add(code)
                specs.append((code, _plan_label(code, None)))
    return specs


def _tier_codes(cats: list[Category]) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for c in cats:
        pa = c.plan_assignments or {}
        for source in (pa.get("rate_tiers"), pa.get("tier_counts")):
            if isinstance(source, dict):
                for code in source:
                    if code and code not in seen:
                        seen.add(code)
                        codes.append(str(code))
    return sorted(codes, key=lambda t: _TIER_ORDER.get(t, len(_TIER_ORDER) + len(t)))


def _slip_tier_labels(cats: list[Category]) -> dict[str, str]:
    """Union of the slips' own tier labels (persisted per category as
    ``plan_assignments.tier_labels``, e.g. {"SO": "Spouse"}) — first seen wins."""
    out: dict[str, str] = {}
    for c in cats:
        labels = (c.plan_assignments or {}).get("tier_labels")
        if isinstance(labels, dict):
            for code, label in labels.items():
                if code and label:
                    out.setdefault(str(code), str(label))
    return out


def _has_dependant_signals(cats: list[Category]) -> bool:
    """True when the parsed slip states dependant coverage for this product:
    a dependant participation clause, a per-dependant rate, a dependant-scope
    category, or any family-composition tier beyond Employee Only."""
    for c in cats:
        detail = c.participation_detail or {}
        if isinstance(detail, dict) and detail.get("dependant"):
            return True
        pa = c.plan_assignments or {}
        if pa.get("dependant_rate") is not None:
            return True
        if pa.get("member_scope") == "dependant":
            return True
        tiers = pa.get("rate_tiers")
        if isinstance(tiers, dict) and any(k != "EO" for k in tiers):
            return True
    return False


_COPAY_PROP_PREFIXES = ("per_visit", "co_payment", "per_policy_year")


def _line_kind(item: dict[str, Any]) -> BenefitKind:
    """Editor kind for a stored benefit line: copay when it carries structured
    per-visit / co-payment properties (outpatient dash groups), boolean for
    Yes/No values, else the generic amount input."""
    props = item.get("properties")
    if isinstance(props, dict) and any(
        str(k).startswith(_COPAY_PROP_PREFIXES) for k in props
    ):
        return "copay"
    if str(item.get("value") or "").strip().upper() in {"YES", "NO", "NA"}:
        return "boolean"
    return "amount"


def _benefit_lines(
    plans: list[Plan],
) -> list[tuple[str, str, BenefitKind, list[tuple[str, str]]]]:
    """Union of benefit lines across all plans, keyed by line number.

    The form renders one canonical line set shared by every plan (per-plan values
    differ, structure doesn't), so a line present on only one plan must still
    appear. We start from the richest plan (best ordering) then append any line
    numbers other plans introduce — otherwise a line unique to a non-richest plan
    would be dropped and blanked on confirm.
    """
    def _items(p: Plan) -> list[dict[str, Any]]:
        sched = p.benefit_schedule or {}
        items = sched.get("items") if isinstance(sched, dict) else None
        return items if isinstance(items, list) else []

    ordered = sorted(plans, key=lambda p: len(_items(p)), reverse=True)
    lines: list[tuple[str, str, BenefitKind, list[tuple[str, str]]]] = []
    seen: set[str] = set()
    for p in ordered:
        for it in _items(p):
            if not isinstance(it, dict):
                continue
            number = str(it.get("number") or "")
            if number in seen:
                continue
            seen.add(number)
            subs = [
                (str(s.get("key") or ""), str(s.get("name") or ""))
                for s in (it.get("sub_items") or [])
                if isinstance(s, dict)
            ]
            lines.append((number, str(it.get("name") or ""), _line_kind(it), subs))
    return lines


# Starter scaffold for raw setup (new client, no slip, no file template). A
# generic guess — the broker edits it. Standard group tiers + two toggleable
# plans + an empty Schedule of Benefits the broker fills via "Add benefit line".
_STARTER_TIERS: tuple[str, ...] = ("EO", "ES", "EC", "EF")
_STARTER_PLANS: tuple[tuple[str, str, bool], ...] = (
    ("1", "Plan 1", True),
    ("2", "Plan 2", False),
)


def _resolve_profile(product: Product) -> str:
    """Form profile for a product: an explicit ``product_metadata['form_profile']``
    override wins, else inferred from the code."""
    meta = getattr(product, "product_metadata", None) or {}
    override = meta.get("form_profile") if isinstance(meta, dict) else None
    return infer_profile(product.code, override)


def _build_template(
    product: Product,
    *,
    plans: list[TemplatePlan],
    tiers: list[TemplateTier],
    benefit_items: list[TemplateBenefitItem],
    has_dependants: bool | None = None,
) -> ProductTemplate:
    """Assemble a ProductTemplate from a product's catalog attributes + the given
    structure. Shared by the synthesized and starter builders so the common
    fields (generic header/eligibility, product attrs) stay in one place. The
    `form_profile` drives which sections + profile fields the template carries
    (filled by the ProductTemplate validator)."""
    return ProductTemplate(
        code=product.code,
        version=1,
        display_name=product.display_name or product.code,
        has_dependants=(
            product.has_dependants if has_dependants is None else has_dependants
        ),
        is_outpatient=product.is_outpatient,
        participation_model=_participation_model(product),
        form_profile=_resolve_profile(product),
        header_fields=list(GENERIC_HEADER_FIELDS),
        # eligibility_fields left empty — the ProductTemplate validator injects
        # the canonical standard_eligibility_fields set.
        plans=plans,
        tiers=tiers,
        benefit_items=benefit_items,
        additional_arrangements=[],
    )


def generic_starter_template(product: Product) -> ProductTemplate:
    """A blank, editable scaffold for configuring a product from scratch when no
    slip or hand-authored template exists. The shape is generic (group tiers +
    starter plans); the broker fills/edits everything."""
    return _build_template(
        product,
        plans=[
            TemplatePlan(code=c, label=label, default_selected=sel)
            for c, label, sel in _STARTER_PLANS
        ],
        tiers=[TemplateTier(code=t, label=_TIER_LABELS[t]) for t in _STARTER_TIERS],
        benefit_items=[],
    )


def merge_file_overlay(
    base: ProductTemplate, file_tpl: ProductTemplate | None
) -> ProductTemplate:
    """Overlay a hand-authored template's curated presentation onto a slip-derived
    structure.

    The slip wins on STRUCTURE — plans, premium tiers and benefit lines (the
    client's actual scheme, e.g. STM GHS has 6 plans, not the template's 4) — so
    the form shows what was really placed. The file template wins on PRESENTATION:
    form profile, basis/rate models, column axis, profile/header/eligibility
    fields, additional arrangements, and each benefit line's ``kind`` (matched by
    number, so an outpatient line stays a Yes/No or a copay row).

    Falls back to the file template's plans/tiers/benefit lines only where the
    slip yielded none (e.g. a sheet whose Schedule of Benefits the parser could
    not extract, so the file's curated lines are used). When the slip *does*
    carry a plain numbered SOB (now including the descriptive term-life / GPA /
    WICI layouts), its real lines drive the form. Returns ``base`` unchanged when
    no file template exists.
    """
    if file_tpl is None:
        return base
    kinds = {bi.number: bi.kind for bi in file_tpl.benefit_items if bi.number}
    # Schedule-of-Benefits structure: prefer the slip's extracted lines unless the
    # curated template uses a layout the line parser can't reproduce. The parser
    # reliably extracts plain numbered-line SOBs (GHS/GMM/SP/GCSP/GTL — amount/
    # text/currency values) so the slip's real schedule should drive the form even
    # when it has FEWER lines than the curated file (e.g. STM GMM has 5 lines vs
    # gmm.v1.json's 6 — a line-count gate would wrongly keep the generic template
    # and overlay slip values under the wrong names). It under-extracts only the
    # specialized layouts: the outpatient A-G + panel matrix (boolean/copay), the
    # dental Panel/Non-Panel column axis, and the dread-disease / scale-of-
    # compensation lists (list/scale). For those the curated template wins so the
    # form is never a broken partial. Slip values still overlay by number in
    # build_setup_answers regardless of which structure is chosen.
    prefer_slip_sob = (
        bool(base.benefit_items)
        and not file_tpl.column_axis
        and not any(bi.kind in _CURATED_SOB_KINDS for bi in file_tpl.benefit_items)
    )
    if prefer_slip_sob:
        benefit_items = [
            bi.model_copy(update={"kind": kinds.get(bi.number, bi.kind)})
            for bi in base.benefit_items
        ]
    else:
        benefit_items = [
            bi.model_copy() for bi in (file_tpl.benefit_items or base.benefit_items)
        ]
    return ProductTemplate(
        code=base.code,
        version=file_tpl.version,
        display_name=file_tpl.display_name or base.display_name,
        # Dependant support is a union: the curated template knows the product
        # family, but the slip knows THIS client's scheme (e.g. a CBRE GTL with
        # Spouse/Child voluntary rows must keep its Dependant section even if
        # the file template says no dependants).
        has_dependants=file_tpl.has_dependants or base.has_dependants,
        is_outpatient=file_tpl.is_outpatient,
        participation_model=file_tpl.participation_model,
        form_profile=file_tpl.form_profile,
        basis_model=file_tpl.basis_model,
        rate_model=file_tpl.rate_model,
        column_axis=list(file_tpl.column_axis),
        header_fields=[f.model_copy() for f in file_tpl.header_fields],
        eligibility_fields=[f.model_copy() for f in file_tpl.eligibility_fields],
        plans=[p.model_copy() for p in (base.plans or file_tpl.plans)],
        tiers=[t.model_copy() for t in (base.tiers or file_tpl.tiers)],
        benefit_items=benefit_items,
        additional_arrangements=[
            a.model_copy() for a in file_tpl.additional_arrangements
        ],
    )


def synthesize_template(
    db: Session, policy_year_id: str, product: Product
) -> ProductTemplate | None:
    """Build a structural ``ProductTemplate`` for a product from its slip-derived
    ``Plan`` / ``Category`` rows in this policy year. Returns None when the
    product has no such rows (nothing to synthesize from)."""
    plans = list(
        db.execute(
            select(Plan).where(
                Plan.product_id == product.id,
                Plan.policy_year_id == policy_year_id,
            )
        ).scalars()
    )
    cats = list(
        db.execute(
            select(Category).where(
                Category.product_id == product.id,
                Category.policy_year_id == policy_year_id,
            )
        ).scalars()
    )
    if not plans and not cats:
        return None

    plan_specs = _plan_specs(plans, cats)
    tier_codes = _tier_codes(cats)
    benefit_lines = _benefit_lines(plans)
    # The slip's own tier vocabulary wins over the canonical label; unknown
    # codes fall back to themselves.
    slip_labels = _slip_tier_labels(cats)

    # Dependant support: the catalog column is authoritative when set; a slip
    # that states dependant coverage (participation clause / dependant rate /
    # dependant-scope category / family tiers) turns it on even when the
    # catalog row was created without it — this is what un-hides the Dependant
    # section of the setup form.
    meta = getattr(product, "product_metadata", None) or {}
    has_dependants = bool(
        product.has_dependants
        or (isinstance(meta, dict) and meta.get("has_dependants"))
        or _has_dependant_signals(cats)
    )

    return _build_template(
        product,
        plans=[TemplatePlan(code=c, label=label) for c, label in plan_specs],
        tiers=[
            TemplateTier(
                code=t, label=slip_labels.get(t) or _TIER_LABELS.get(t, t)
            )
            for t in tier_codes
        ],
        benefit_items=[
            TemplateBenefitItem(
                number=number,
                name=name,
                kind=kind,
                sub_items=[TemplateSubItem(key=k, name=sn) for k, sn in subs],
            )
            for number, name, kind, subs in benefit_lines
        ],
        has_dependants=has_dependants,
    )
