"""Guided product setup — the forward counterpart to slip parsing.

Drives a form that builds a brand-new product configuration from the insurer's
standard SME scheme template, then materializes it into the catalog `Product`
plus per-plan `Plan` rows (`source="manual"`) — the same shape the placement-slip
parser emits, so everything downstream (matching, activation) works unchanged.

- GET  /product-templates                                  — list available templates
- GET  /product-templates/{code}                           — full template (drives the form)
- GET  /policy-years/{id}/product-setups                   — list this year's setup drafts
- GET  /policy-years/{id}/product-setups/{code}            — one draft (resume editing)
- PUT  /policy-years/{id}/product-setups/{code}            — save/replace the draft answers
- POST /policy-years/{id}/product-setups/{code}/confirm    — save + materialize Product/Plan

Tenant scoping rides on `load_policy_year` (the setup is keyed by the already
tenant-checked policy year), matching the recommendations router pattern.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_editable,
    load_policy_year,
    require_client_id,
    tenant_or_global,
)
from app.db.session import get_db
from app.models import (
    Category,
    Employee,
    FlexPricing,
    Plan,
    PolicyYear,
    Product,
    ProductSetup,
    ProductTerm,
)
from app.models.category import CategoryStatus, SourceKind
from app.models.product_setup import ProductSetupOrigin, ProductSetupStatus
from app.schemas.api import InsuranceLineStr
from app.services import product_registry
from app.services.benefit_key_guard import (
    orphan_conflict_detail,
    orphaned_benefit_keys,
    schedule_benefit_names,
)
from app.services.category_factory import build_manual_category
from app.services.dynamic_template import (
    generic_starter_template,
    merge_file_overlay,
    synthesize_template,
)
from app.services.eligibility_mapping import auto_map_policy_year
from app.services.entity_vocab import entity_vocabulary
from app.services.form_profiles import basis_model_for, infer_profile, rate_model_for
from app.services.insurance_lines import infer_line
from app.services.matching_engine import insured_names, match_policy_year
from app.services.member_counts import DraftCategory, compute_member_counts
from app.services.placement_slip_parser import (
    normalize_participation,
    parse_participation,
)
from app.services.product_templates import (
    ProductTemplate,
    get_template,
    list_templates,
)
from app.services.sob_columns import resolve_plan_schedule

logger = logging.getLogger(__name__)

router = APIRouter(tags=["product-setup"])

_MANUAL = "manual"
_SETUP_REF = "product_setup"
_MAX_BENEFIT_ITEMS = 200
_MAX_COVER_DESC = 512


# ── DTOs ────────────────────────────────────────────────────────────────────


class SetupOut(BaseModel):
    id: str
    policy_year_id: str
    product_code: str
    template_version: int
    answers: dict[str, Any]
    status: str
    origin: str = "manual"
    confirmed_at: datetime | None = None
    materialized_product_id: str | None = None
    updated_at: datetime


class SetupSaveIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    template_version: int = 1
    # Proceed even though renaming/removing a benefit line would strand existing
    # claims that reference it by name (409 `orphaned_benefit_keys` otherwise).
    acknowledge: bool = False
    expected_updated_at: datetime | None = None


class ConfirmResult(BaseModel):
    product_id: str
    product_code: str
    plans_created: int
    plans_updated: int
    plans_removed: int
    categories_created: int
    categories_removed: int
    rematched: bool = False
    employees_matched: int | None = None


class FieldSuggestions(BaseModel):
    """Values used before for this client+product, to back the form's free-text
    suggestion pickers. Keyed by field id within each section."""

    header: dict[str, list[str]] = Field(default_factory=dict)
    eligibility: dict[str, list[str]] = Field(default_factory=dict)
    participation: list[str] = Field(default_factory=list)
    cover_description: list[str] = Field(default_factory=list)


class MemberCountCategoryIn(BaseModel):
    """One draft Basis-of-Cover row: client-side key + its category text.

    ``insured`` (the row's legal-entity list, when stated) scopes the preview
    to that entity's employees — mirroring the matching engine's gate."""

    key: str
    description: str = ""
    # Token list from the picker; a legacy comma-joined string still parses.
    insured: str | list[str] | None = None


class MemberCountsIn(BaseModel):
    # has_dependants comes from the template the form already loaded; if a catalog
    # row already exists for product_code (re-setup), the persisted product's flag
    # overrides it server-side so the dependant gate stays authoritative.
    product_code: str | None = None
    has_dependants: bool = False
    categories: list[MemberCountCategoryIn] = Field(default_factory=list)


class CategoryMemberCount(BaseModel):
    key: str
    employees: int
    dependants: int


class MemberCountsOut(BaseModel):
    counts: list[CategoryMemberCount]
    employees_total: int
    employees_matched: int
    has_dependants: bool


class EntityValueOut(BaseModel):
    value: str
    count: int
    claimed: bool
    # For an unreconciled entity: the roster spelling it most likely means.
    suggestion: str | None = None


class EntityVocabOut(BaseModel):
    """Legal entities the Insured picker offers — see `services/entity_vocab`."""

    employees_total: int
    roster: list[EntityValueOut]
    known: list[EntityValueOut]


def _setup_out(s: ProductSetup) -> SetupOut:
    return SetupOut(
        id=s.id,
        policy_year_id=s.policy_year_id,
        product_code=s.product_code,
        template_version=s.template_version,
        answers=s.answers or {},
        status=s.status,
        origin=s.origin,
        confirmed_at=s.confirmed_at,
        materialized_product_id=s.materialized_product_id,
        updated_at=s.updated_at,
    )


# ── Setup products (slip-detected + hand-authored, tenant-scoped) ────────────


class SetupProductSummary(BaseModel):
    code: str
    display_name: str
    has_template_file: bool
    has_slip_data: bool
    # Medical / General / Life / Flex line for tab routing.
    line: InsuranceLineStr = "medical"
    # True when the client has its own catalog row for this code (i.e. the user
    # added it, or a slip created it) — vs a bare global recognition row. Drives
    # whether the product surfaces as a configurable card in its tab.
    is_client_product: bool = False
    # Structural classification (registry + product_metadata override) so the
    # frontend needs no hardcoded code→profile map.
    form_profile: str | None = None
    layout_family: str | None = None
    has_dependants: bool = False
    # The client Product row id, when one exists — the target for the
    # classification PATCH (/schemas/products/{id}).
    product_id: str | None = None


@router.get(
    "/policy-years/{policy_year_id}/setup-products",
    response_model=list[SetupProductSummary],
)
def list_setup_products(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SetupProductSummary]:
    """Every product the broker can set up here: the client's whole catalog
    (so a new client with no slip can still configure any product from scratch),
    flagged with whether it has slip data or a hand-authored template. Products
    with neither open a generic starter form."""
    client_id = require_client_id(user)
    file_templates = {t.code: t for t in list_templates()}
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
    pids = {p for p in (cat_pids | plan_pids) if p}
    catalog = list(
        db.execute(
            select(Product).where(tenant_or_global(Product.client_id, client_id))
        ).scalars()
    )
    out: dict[str, SetupProductSummary] = {}
    for p in catalog:
        # A code can have both a global (client_id NULL) and a client-specific
        # row; collapse them, OR-ing slip data and preferring the client display.
        code = p.code.strip().upper()
        has_slip = p.id in pids
        is_client = p.client_id is not None
        meta = p.product_metadata or {}
        entry = product_registry.resolve_entry(p.code, meta)
        existing = out.get(code)
        if existing is None:
            out[code] = SetupProductSummary(
                code=code,
                display_name=p.display_name,
                has_template_file=code in file_templates,
                has_slip_data=has_slip,
                line=p.line,
                is_client_product=is_client,
                form_profile=infer_profile(p.code, meta.get("form_profile")),
                layout_family=entry.layout_family,
                has_dependants=bool(p.has_dependants or entry.has_dependants),
                product_id=p.id if is_client else None,
            )
        else:
            existing.has_slip_data = existing.has_slip_data or has_slip
            existing.is_client_product = existing.is_client_product or is_client
            if is_client:
                existing.display_name = p.display_name
                existing.line = p.line
                existing.form_profile = infer_profile(
                    p.code, meta.get("form_profile")
                )
                existing.layout_family = entry.layout_family
                existing.has_dependants = bool(
                    p.has_dependants or entry.has_dependants
                )
                existing.product_id = p.id
    for code, t in file_templates.items():
        normalized = code.strip().upper()
        entry = product_registry.resolve_entry(normalized)
        out.setdefault(
            normalized,
            SetupProductSummary(
                code=normalized,
                display_name=t.display_name,
                has_template_file=True,
                has_slip_data=False,
                line=infer_line(normalized),
                form_profile=infer_profile(normalized),
                layout_family=entry.layout_family,
                has_dependants=bool(t.has_dependants or entry.has_dependants),
            ),
        )
    return sorted(out.values(), key=lambda s: s.code)


# ── Product registry (static classification catalog) ────────────────────────


class RegistryProfileOut(BaseModel):
    id: str
    label: str
    basis_model: str
    rate_model: str
    # The slip layout family this profile implies — the classification picker
    # submits both so the parser dispatches the right extractor.
    layout_family: str


class RegistryEntryOut(BaseModel):
    code: str
    name: str
    line: str
    form_profile: str
    layout_family: str
    has_dependants: bool


class RegistryOut(BaseModel):
    entries: list[RegistryEntryOut]
    profiles: list[RegistryProfileOut]
    lines: list[str]
    layout_families: list[str]


_PROFILE_LABELS: dict[str, str] = {
    "tiered_medical": "Tiered medical (family-composition tiers)",
    "outpatient": "Outpatient clinic (per-member rate)",
    "dental": "Dental",
    "sum_assured": "Sum assured (life / critical illness)",
    "accident": "Accident / disability (capital sum)",
    "travel": "Business travel (flat policy premium)",
    "statutory": "Statutory (earnings-based, WICA)",
}
_PROFILE_LAYOUT_FAMILY: dict[str, str] = {
    "tiered_medical": "plan_tier",
    "outpatient": "plan_tier",
    "dental": "plan_tier",
    "sum_assured": "si_based",
    "accident": "si_based",
    "travel": "travel",
    "statutory": "earnings",
}


@router.get("/product-registry", response_model=RegistryOut)
def get_product_registry(
    user: CurrentUser = Depends(get_current_user),
) -> RegistryOut:
    """The static product-classification catalog: known product entries and the
    selectable profiles/lines/layout families. Global data (no tenant rows) —
    the frontend uses it instead of hardcoded code→line/profile maps, and the
    needs_classification picker offers `profiles` + `lines` from here."""
    return RegistryOut(
        entries=[
            RegistryEntryOut(
                code=e.code,
                name=e.name,
                line=e.line,
                form_profile=e.form_profile,
                layout_family=e.layout_family,
                has_dependants=e.has_dependants,
            )
            for e in product_registry.entries()
        ],
        profiles=[
            RegistryProfileOut(
                id=profile,
                label=_PROFILE_LABELS.get(profile, profile),
                basis_model=basis_model_for(profile),
                rate_model=rate_model_for(profile),
                layout_family=_PROFILE_LAYOUT_FAMILY.get(profile, "plan_tier"),
            )
            for profile in _PROFILE_LABELS
        ],
        lines=["medical", "general", "life", "flex"],
        layout_families=[
            "si_based", "plan_tier", "travel", "named_person", "earnings",
        ],
    )


@router.get(
    "/policy-years/{policy_year_id}/setup-products/{product_code}/template",
    response_model=ProductTemplate,
)
def get_setup_template(
    policy_year_id: str,
    product_code: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductTemplate:
    client_id = require_client_id(user)
    tpl = _resolve_template(db, policy_year_id, product_code, client_id)
    if tpl is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No template or slip data for product {product_code!r}",
        )
    return tpl


@router.get(
    "/policy-years/{policy_year_id}/entity-vocab",
    response_model=EntityVocabOut,
)
def get_entity_vocab(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> EntityVocabOut:
    """Legal entities available to a category's Insured field.

    ``roster`` entities carry headcounts and are safe to pick — the matching
    gate will let those employees through. ``known`` entities are named in the
    configuration (a category's insured list or a setup header) but match no
    roster entity, so they are the reconciliation backlog. Before the roster is
    uploaded ``roster`` is legitimately empty and the picker falls back to free
    entry. Tenant scoping rides on ``load_policy_year``.
    """
    vocab = entity_vocabulary(db, py)
    return EntityVocabOut(**asdict(vocab))


@router.post(
    "/policy-years/{policy_year_id}/member-counts",
    response_model=MemberCountsOut,
)
def preview_member_counts(
    policy_year_id: str,
    payload: MemberCountsIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberCountsOut:
    """Live employee + dependant match counts for draft Basis-of-Cover rows.

    Read-only preview: evaluates each row's description against the roster using
    the matching engine's semantics (exact → fuzzy → rule, most-specific wins),
    so the setup form can auto-populate 'No. of members' before the product is
    confirmed. Dependants are only counted when the product covers them.
    """
    client_id = require_client_id(user)

    has_dependants = bool(payload.has_dependants)
    product_id: str | None = None
    if payload.product_code:
        # The current setup selection drives the preview. The persisted product
        # may still reflect an older confirm, while the form can be mid-edit.
        # Keep the product id so the counter can reuse stored category rules.
        product = db.execute(
            select(Product)
            .where(tenant_or_global(Product.client_id, client_id))
            .where(Product.code == payload.product_code)
            # Client-specific row (NOT NULL client_id) wins over a global one.
            .order_by(Product.client_id.is_(None))
            .limit(1)
        ).scalar_one_or_none()
        if product is not None:
            product_id = product.id

    result = compute_member_counts(
        db,
        policy_year_id,
        client_id,
        has_dependants,
        [
            DraftCategory(key=c.key, description=c.description, insured=c.insured)
            for c in payload.categories
        ],
        product_id=product_id,
    )
    return MemberCountsOut(
        counts=[
            CategoryMemberCount(
                key=c.key, employees=c.employees, dependants=c.dependants
            )
            for c in result.counts
        ],
        employees_total=result.employees_total,
        employees_matched=result.employees_matched,
        has_dependants=result.has_dependants,
    )


# ── Setup drafts (tenant-scoped via the policy year) ────────────────────────


@router.get("/policy-years/{policy_year_id}/product-setups", response_model=list[SetupOut])
def list_setups(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> list[SetupOut]:
    rows = db.execute(
        select(ProductSetup)
        .where(ProductSetup.policy_year_id == policy_year_id)
        .order_by(ProductSetup.product_code)
    ).scalars().all()
    return [_setup_out(s) for s in rows]


@router.get(
    "/policy-years/{policy_year_id}/product-setups/{product_code}",
    response_model=SetupOut,
)
def get_setup(
    policy_year_id: str,
    product_code: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> SetupOut:
    setup = _find_setup(db, policy_year_id, product_code)
    if setup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No setup draft for this product")
    return _setup_out(setup)


@router.get(
    "/policy-years/{policy_year_id}/product-setups/{product_code}/field-suggestions",
    response_model=FieldSuggestions,
)
def field_suggestions(
    policy_year_id: str,
    product_code: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FieldSuggestions:
    """Distinct free-text values used before for this client + product.

    Read live from the client's *confirmed* setups in other policy years — the
    dynamic replacement for the old hardcoded choice lists. Tenant-scoped: the
    join restricts to the active client's policy years, so one firm never sees
    another's wordings.
    """
    client_id = require_client_id(user)
    rows = db.execute(
        select(ProductSetup)
        .join(PolicyYear, PolicyYear.id == ProductSetup.policy_year_id)
        .where(
            PolicyYear.client_id == client_id,
            ProductSetup.product_code == product_code.upper(),
            ProductSetup.status == ProductSetupStatus.confirmed,
            ProductSetup.policy_year_id != policy_year_id,
        )
        .order_by(ProductSetup.confirmed_at.desc())
        .limit(_SUGGESTION_SCAN_LIMIT)
    ).scalars().all()
    return _aggregate_suggestions(rows)


@router.put(
    "/policy-years/{policy_year_id}/product-setups/{product_code}",
    response_model=SetupOut,
)
def save_setup(
    policy_year_id: str,
    product_code: str,
    body: SetupSaveIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SetupOut:
    assert_policy_year_editable(py)
    client_id = require_client_id(user)
    # Only the canonical code is needed here — skip synthesizing structure.
    tpl = _resolve_template(
        db, policy_year_id, product_code, client_id, need_structure=False
    )
    if tpl is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No template or slip data for product {product_code!r}",
        )
    setup = _upsert_draft(db, policy_year_id, tpl.code, body, lock=True)
    write_audit(
        db, user, action="save_setup_draft", entity_type="product_setup",
        entity_id=setup.id, after={"product_code": tpl.code, "policy_year_id": policy_year_id},
    )
    db.commit()
    db.refresh(setup)
    return _setup_out(setup)


@router.delete(
    "/policy-years/{policy_year_id}/product-setups/{product_code}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def discard_setup(
    policy_year_id: str,
    product_code: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Discard a product's setup draft (e.g. the form's 'Discard draft' action).

    Only removes the draft answers — confirmed setups and any materialized
    Product/Plan/Category rows are left intact. Idempotent: a missing draft is
    a no-op 204."""
    assert_policy_year_editable(py)
    setup = _find_setup(db, policy_year_id, product_code)
    if setup is None:
        return None
    db.delete(setup)
    write_audit(
        db, user, action="discard_setup_draft", entity_type="product_setup",
        entity_id=setup.id,
        before={"product_code": setup.product_code, "policy_year_id": policy_year_id},
    )
    db.commit()
    return None


@router.delete(
    "/policy-years/{policy_year_id}/products/{product_code}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_product_from_year(
    policy_year_id: str,
    product_code: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Remove a product from this policy year's tab: delete its categories,
    plans, setup draft, and coverage override for this year. The client catalog
    row is also dropped when no *other* policy year still uses it, so the
    product leaves the tab and becomes re-addable — while a product configured
    in another year is preserved. Idempotent."""
    assert_policy_year_editable(py)
    client_id = require_client_id(user)
    code = product_code.strip().upper()
    products = list(
        db.execute(
            select(Product).where(
                tenant_or_global(Product.client_id, client_id),
                func.upper(Product.code) == code,
            )
        ).scalars()
    )
    pids = {p.id for p in products}

    cats = (
        list(
            db.execute(
                select(Category).where(
                    Category.policy_year_id == policy_year_id,
                    Category.product_id.in_(pids),
                )
            ).scalars()
        )
        if pids
        else []
    )
    plans = (
        list(
            db.execute(
                select(Plan).where(
                    Plan.policy_year_id == policy_year_id,
                    Plan.product_id.in_(pids),
                )
            ).scalars()
        )
        if pids
        else []
    )
    terms = (
        list(
            db.execute(
                select(ProductTerm).where(
                    ProductTerm.policy_year_id == policy_year_id,
                    ProductTerm.product_id.in_(pids),
                )
            ).scalars()
        )
        if pids
        else []
    )
    setup = _find_setup(db, policy_year_id, code)

    for row in (*cats, *plans, *terms):
        db.delete(row)
    if setup is not None:
        db.delete(setup)

    # Drop the client catalog row only when nothing in another policy year still
    # references it (so a product set up elsewhere survives).
    for p in (p for p in products if p.client_id is not None):
        used_elsewhere = db.execute(
            select(Category.id)
            .where(Category.product_id == p.id, Category.policy_year_id != policy_year_id)
            .limit(1)
        ).first() or db.execute(
            select(Plan.id)
            .where(Plan.product_id == p.id, Plan.policy_year_id != policy_year_id)
            .limit(1)
        ).first() or db.execute(
            select(ProductTerm.id)
            .where(
                ProductTerm.product_id == p.id,
                ProductTerm.policy_year_id != policy_year_id,
            )
            .limit(1)
        ).first()
        if used_elsewhere is None:
            db.delete(p)

    emp_count = db.execute(
        select(func.count(Employee.id)).where(
            Employee.policy_year_id == policy_year_id
        )
    ).scalar_one()
    match_summary = (
        match_policy_year(db, policy_year_id, user)
        if emp_count
        else None
    )
    write_audit(
        db, user, action="remove_product", entity_type="product", entity_id=None,
        before={"product_code": code, "policy_year_id": policy_year_id,
                "categories": len(cats), "plans": len(plans)},
    )
    if match_summary is not None:
        write_audit(
            db, user, action="run_matching", entity_type="policy_year",
            entity_id=policy_year_id,
            after={"employees_matched": match_summary.employees_matched,
                   "trigger": "remove_product"},
        )
    db.commit()
    return None


def _sync_term_policy_number(
    db: Session, product: Product, policy_year_id: str, answers: dict[str, Any]
) -> None:
    """Route the Header & Policy "Policy No." into this product's term.

    The policy number is entered once, under Header & Policy (``header.policy_no``);
    the placement-slip export reads it from ``ProductTerm.policy_number``. Mirror a
    NON-EMPTY header value into the (sparse) term row on confirm — creating the row
    when needed. A blank header is a no-op: it must never clear a number set
    out-of-band (e.g. before this field became the single input), so an unrelated
    re-confirm can't silently wipe the exported policy number. The value is capped
    to the column width (String(64)) so an over-long paste can't fail the confirm
    on Postgres.
    """
    header = answers.get("header") or {}
    raw = header.get("policy_no")
    policy_no = str(raw).strip()[:64] if raw is not None else ""
    if not policy_no:
        return
    term = db.execute(
        select(ProductTerm).where(
            ProductTerm.policy_year_id == policy_year_id,
            ProductTerm.product_id == product.id,
        )
    ).scalar_one_or_none()
    if term is not None:
        term.policy_number = policy_no
    else:
        db.add(
            ProductTerm(
                policy_year_id=policy_year_id,
                product_id=product.id,
                policy_number=policy_no,
            )
        )


@router.post(
    "/policy-years/{policy_year_id}/product-setups/{product_code}/confirm",
    response_model=ConfirmResult,
)
def confirm_setup(
    policy_year_id: str,
    product_code: str,
    body: SetupSaveIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConfirmResult:
    """Persist the current answers, then materialize Product + Plan + Category rows.

    Idempotent: re-confirming re-projects the draft — plans/categories are
    upserted and rows no longer present are removed. When a roster exists,
    matching is re-run afterwards so employee matches reflect the new categories
    (otherwise a re-confirm could leave matches pointing at deleted categories).
    """
    assert_policy_year_editable(py)
    client_id = require_client_id(user)
    # confirm materializes from the draft answers, not the template structure —
    # it only needs the product's attributes, so skip the Plan/Category scan.
    tpl = _resolve_template(
        db, policy_year_id, product_code, client_id, need_structure=False
    )
    if tpl is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No template or slip data for product {product_code!r}",
        )
    # Confirm has materializing side effects (plans, category seeds, matching).
    # Lock the one setup row so two fast confirms serialize instead of both
    # observing the first-materialization state.
    setup = _upsert_draft(db, policy_year_id, tpl.code, body, lock=True)

    selected = _selected_plans(setup.answers)
    if not selected:
        # Nothing was committed yet — the merged draft is rolled back on session
        # close, so a failed confirm never persists a half-built setup.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No plans selected — choose at least one plan before confirming.",
        )

    # Guarantee every category resolves to a selected plan (auto-map to a sole
    # plan, else 422). Prevents confirming a config with dangling plan links.
    if _map_category_plan_codes(setup.answers, selected):
        flag_modified(setup, "answers")

    # A benefit line's NAME is the join key for claims + utilization
    # (see services/benefit_key_guard). Renaming or deleting one strands every
    # claim that referenced it, so warn before materializing — confirmable,
    # because fixing a slip typo is a legitimate rename.
    # Projected ONCE and handed to _materialize_plans below — recomputing it
    # there would double the work and let the guard validate names that the
    # stored schedule never ends up carrying.
    schedules = {
        str(plan.get("code") or ""): _benefit_schedule(setup.answers, plan)
        for plan in selected
    }
    if not body.acknowledge:
        surviving: set[str] = set()
        for schedule in schedules.values():
            surviving |= schedule_benefit_names(schedule.get("items"))
        orphaned = orphaned_benefit_keys(
            db,
            policy_year_id=policy_year_id,
            product_code=tpl.code,
            new_items=[{"name": n} for n in surviving],
        )
        if orphaned:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=orphan_conflict_detail(orphaned, tpl.code),
            )

    cover_description = (
        str(setup.answers.get("cover_description") or "")[:_MAX_COVER_DESC] or None
    )
    try:
        product = _upsert_product(db, user, client_id, tpl, setup.answers)
        if setup.origin == ProductSetupOrigin.placement_slip:
            _adopt_slip_artifacts(db, user, product, policy_year_id)
        _sync_term_policy_number(db, product, policy_year_id, setup.answers)
        created, updated, removed = _materialize_plans(
            db, user, product, policy_year_id, setup.answers, selected,
            cover_description, schedules,
        )
        # Categories are now edited directly through the category cards
        # (PATCH/POST/DELETE /categories). Confirm only *seeds* them on a
        # product's FIRST materialization and only when it has none yet (manual
        # products with no slip parse). A re-confirm — or a confirm after the
        # broker deleted categories via the cards — never re-seeds, so deleted
        # rows can't resurrect from the now-hidden draft grid and inline edits
        # are never clobbered. (materialized_product_id is still None here; it's
        # set below after a successful first confirm.)
        first_materialization = setup.materialized_product_id is None
        has_categories = db.execute(
            select(func.count(Category.id)).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id == product.id,
            )
        ).scalar_one()
        if first_materialization and not has_categories:
            cats_created, cats_removed = _materialize_categories(
                db, user, product, policy_year_id, setup.answers,
                rate_model=tpl.rate_model or "tiered",
                basis_model=tpl.basis_model or "tiered",
            )
        else:
            cats_created, cats_removed = 0, 0
        setup.status = ProductSetupStatus.confirmed
        setup.confirmed_at = datetime.now(UTC)
        setup.confirmed_by = user.user_id
        setup.materialized_product_id = product.id
        write_audit(
            db, user, action="confirm_setup", entity_type="product_setup",
            entity_id=setup.id,
            after={"product_id": product.id, "plans_created": created,
                   "plans_updated": updated, "plans_removed": removed,
                   "categories_created": cats_created, "categories_removed": cats_removed},
        )

        # Product confirmation materializes plan/category facts. Matching rules
        # are a separate company-aware concern: compile proposals from the
        # current tenant schema/roster before any automatic re-match, without
        # auto-confirming the inferred eligibility mapping.
        mapping_summary = auto_map_policy_year(
            db,
            policy_year_id=policy_year_id,
            client_id=client_id,
        )
        write_audit(
            db,
            user,
            action="propose_eligibility_mappings",
            entity_type="policy_year",
            entity_id=policy_year_id,
            after={
                "trigger": "confirm_setup",
                "validated": mapping_summary.validated,
                "needs_review": mapping_summary.needs_review,
                "unmapped": mapping_summary.unmapped,
                "reused": mapping_summary.reused,
            },
        )

        # Keep setup materialization and matching in one transaction. A matching
        # failure must not leave a confirmed setup persisted behind an error.
        rematched = False
        employees_matched: int | None = None
        emp_count = db.execute(
            select(func.count(Employee.id)).where(
                Employee.policy_year_id == policy_year_id
            )
        ).scalar_one()
        if emp_count:
            summary = match_policy_year(db, policy_year_id, user)
            write_audit(
                db, user, action="run_matching", entity_type="policy_year",
                entity_id=policy_year_id,
                after={"employees_matched": summary.employees_matched,
                       "trigger": "confirm_setup"},
            )
            rematched = True
            employees_matched = summary.employees_matched
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another request modified this product concurrently — please retry.",
        ) from None
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception(
            "product setup confirm failed (policy_year=%s code=%s)",
            policy_year_id, tpl.code,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Could not materialize the product setup — see server logs.",
        ) from None

    return ConfirmResult(
        product_id=product.id, product_code=product.code,
        plans_created=created, plans_updated=updated, plans_removed=removed,
        categories_created=cats_created, categories_removed=cats_removed,
        rematched=rematched, employees_matched=employees_matched,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


_MAX_SUGGESTIONS = 8
# Cap how many confirmed setups we scan for suggestions — recent ones carry the
# relevant wordings, so we don't stream every year's full answers blob.
_SUGGESTION_SCAN_LIMIT = 25


def _collect_section(dst: dict[str, list[str]], section: Any) -> None:
    if not isinstance(section, dict):
        return
    for key, raw in section.items():
        # Suggestions back FREE-TEXT pickers only. `header.entities` is a token
        # list, and str()-ing it would offer the broker a Python repr
        # ("['Acme Pte Ltd', ...]") as a pickable value.
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if not val:
            continue
        bucket = dst.setdefault(str(key), [])
        if val not in bucket and len(bucket) < _MAX_SUGGESTIONS:
            bucket.append(val)


def _collect_scalar(bucket: list[str], raw: Any) -> None:
    val = str(raw or "").strip()
    if val and val not in bucket and len(bucket) < _MAX_SUGGESTIONS:
        bucket.append(val)


def _aggregate_suggestions(rows: Sequence[ProductSetup]) -> FieldSuggestions:
    out = FieldSuggestions()
    for setup in rows:
        answers = setup.answers or {}
        _collect_section(out.header, answers.get("header"))
        _collect_section(out.eligibility, answers.get("eligibility"))
        _collect_scalar(out.participation, answers.get("participation"))
        _collect_scalar(out.cover_description, answers.get("cover_description"))
    return out


def _resolve_template(
    db: Session,
    policy_year_id: str,
    code: str,
    client_id: str,
    *,
    need_structure: bool = True,
) -> ProductTemplate | None:
    """Structure for a product's setup form: the hand-authored JSON template if
    one exists, otherwise synthesized from the product's slip-derived Plan/
    Category rows in this policy year (or a blank starter when it has none).
    None when neither a template file nor a catalog product exists.

    ``need_structure=False`` skips the Plan/Category scan and returns the file
    template (or starter scaffold) — for callers (save/confirm) that only need the
    product's attributes (code/display_name/participation/basis_model/rate_model),
    not its actual plans/tiers/benefit lines.

    When the product has slip-derived Plan/Category rows, the synthesized
    structure (the client's real plans + benefit lines) is preferred and the
    hand-authored file template is overlaid for presentation (models, kinds,
    arrangements) — so a slip with more plans than the canned template still shows
    every plan. Without slip rows, the file template (or starter) is used as the
    from-scratch scaffold.
    """
    file_tpl = get_template(code)
    product = db.execute(
        select(Product)
        .where(
            tenant_or_global(Product.client_id, client_id),
            func.upper(Product.code) == code.upper(),
        )
        # Prefer the client-specific row over a global (client_id NULL) one of the
        # same code, so a confirm-created client product (which holds the slip rows)
        # wins over the empty seeded global product.
        .order_by(Product.client_id.is_(None))
    ).scalars().first()
    if not need_structure:
        if file_tpl is not None:
            return file_tpl
        return generic_starter_template(product) if product is not None else None
    if product is None:
        return file_tpl
    synth = synthesize_template(db, policy_year_id, product)
    if synth is not None:
        return merge_file_overlay(synth, file_tpl)
    return file_tpl or generic_starter_template(product)


def _find_setup(
    db: Session,
    policy_year_id: str,
    product_code: str,
    *,
    lock: bool = False,
) -> ProductSetup | None:
    stmt = select(ProductSetup).where(
        ProductSetup.policy_year_id == policy_year_id,
        ProductSetup.product_code == product_code.upper(),
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def _stale_setup(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"code": "stale_configuration", "message": message},
    )


def _upsert_draft(
    db: Session,
    policy_year_id: str,
    code: str,
    body: SetupSaveIn,
    *,
    lock: bool = False,
) -> ProductSetup:
    setup = _find_setup(db, policy_year_id, code, lock=lock)
    if setup is None:
        setup = ProductSetup(
            policy_year_id=policy_year_id,
            product_code=code.upper(),
            template_version=body.template_version,
            answers=dict(body.answers),
            status=ProductSetupStatus.draft,
        )
        db.add(setup)
        db.flush()
    else:
        if body.expected_updated_at is None:
            raise _stale_setup(
                "This setup has changed. Reload the latest version before saving."
            )
        current = setup.updated_at
        expected = body.expected_updated_at
        current_utc = current.replace(tzinfo=None)
        expected_utc = (
            expected.astimezone(UTC).replace(tzinfo=None)
            if expected.tzinfo
            else expected
        )
        if current_utc != expected_utc:
            raise _stale_setup(
                "This setup was updated by another user. Reload the latest version before saving."
            )
        # Shallow-merge top-level sections so a partial body (e.g. a confirm that
        # only carries `plans`) can't silently wipe a previously-saved section
        # like `categories` or `rate_table`. A section is only replaced when the
        # caller explicitly sends that key.
        setup.answers = {**(setup.answers or {}), **body.answers}
        setup.template_version = body.template_version
    return setup


def seed_draft_from_slip(
    db: Session,
    policy_year_id: str,
    slip_id: str,
    product_code: str,
    answers: dict[str, Any],
    template_version: int,
) -> bool:
    """Create or replace a slip-prefilled draft for a product.

    A re-upload is the latest source for unconfirmed setup drafts, so saved
    answers are replaced wholesale. Confirmed setups stay protected because they
    have already been materialized into product configuration.
    """
    setup = _find_setup(db, policy_year_id, product_code)
    if setup is not None:
        if setup.status != ProductSetupStatus.draft:
            return False
        setup.answers = answers
        setup.template_version = template_version
        setup.origin = ProductSetupOrigin.placement_slip
        setup.origin_ref = slip_id
        flag_modified(setup, "answers")
        return True
    db.add(
        ProductSetup(
            policy_year_id=policy_year_id,
            product_code=product_code.upper(),
            template_version=template_version,
            answers=answers,
            status=ProductSetupStatus.draft,
            origin=ProductSetupOrigin.placement_slip,
            origin_ref=slip_id,
        )
    )
    return True


def _selected_plans(answers: dict[str, Any]) -> list[dict[str, Any]]:
    plans = answers.get("plans")
    if not isinstance(plans, list):
        return []
    out: list[dict[str, Any]] = []
    for p in plans:
        if isinstance(p, dict) and p.get("selected") and str(p.get("code") or "").strip():
            out.append(p)
    return out


def _answers_have_dependants(answers: dict[str, Any]) -> bool:
    eligibility = answers.get("eligibility")
    if not isinstance(eligibility, dict):
        return False
    raw = eligibility.get("member_cover_eligibility")
    if isinstance(raw, (list, tuple, set)):
        selected = {str(v).strip().lower() for v in raw}
    else:
        selected = {
            part.strip().lower()
            for part in str(raw or "").split(",")
            if part.strip()
        }
    return bool(selected & {"spouse", "child", "dependant", "dependent"})


def _map_category_plan_codes(
    answers: dict[str, Any], selected: list[dict[str, Any]]
) -> bool:
    """Ensure every category row points at a selected plan, before materializing.

    Guarantees the confirmed config is internally consistent (no category whose
    plan_code lacks a Plan row). Auto-maps to the sole selected plan when there is
    exactly one; otherwise raises 422 listing the unmapped categories so the user
    assigns them. Returns True when it mutated ``answers``.
    """
    selected_codes = {str(p.get("code")).strip() for p in selected}
    rows = answers.get("categories")
    if not isinstance(rows, list):
        return False
    only_code = next(iter(selected_codes)) if len(selected_codes) == 1 else None
    mutated = False
    unmapped: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("category") or "").strip():
            continue
        code = str(row.get("plan_code") or "").strip()
        if code in selected_codes:
            continue
        if only_code is not None:
            row["plan_code"] = only_code
            mutated = True
        else:
            unmapped.append({"category": str(row["category"])[:80], "plan_code": code})
    if unmapped:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "unmapped_categories",
                "message": "Some categories are not assigned to a selected plan.",
                "categories": unmapped,
                "available_plans": sorted(selected_codes),
            },
        )
    return mutated


def _clean_value(v: Any) -> str | None:
    """Coerce a benefit cell to a string-or-None.

    Only scalar str/number/bool become strings; blanks and non-scalar JSON
    (lists/objects from a malformed payload) collapse to None rather than being
    stringified into junk like '[]' / '{}'.
    """
    if v is None or v == "":
        return None
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    return None


def _clean_limits(raw: Any) -> list[dict[str, Any]]:
    """Project answer `limits` ([{label, value}]) into stored shape, dropping
    rows with no label."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for lim in raw:
        if not isinstance(lim, dict):
            continue
        label = str(lim.get("label") or "").strip()
        if not label:
            continue
        out.append({"label": label, "value": _clean_value(lim.get("value"))})
    return out


def _benefit_schedule(answers: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Project a plan's effective benefit items into the benefit_schedule shape.

    Resolves from the decoupled column model (``answers.sob``) when present —
    each plan's schedule is the effective value (override → base) for the column
    it maps to. Falls back to a legacy per-plan grid (``plan.benefit_items``) so
    in-flight drafts saved before the redesign still confirm. Carries the full
    benefit detail — value, footnote, qualifier limits, and per-sub-item
    values/notes/limits — so the stored schedule is complete enough to render an
    employee-facing view without re-parsing the slip.
    """
    sob = answers.get("sob")
    if isinstance(sob, dict) and isinstance(sob.get("items"), list):
        items_in: Any = resolve_plan_schedule(
            sob, str(plan.get("code") or "").strip(), _MAX_BENEFIT_ITEMS
        )
    else:
        items_in = plan.get("benefit_items")
    items: list[dict[str, Any]] = []
    if isinstance(items_in, list):
        for it in items_in[:_MAX_BENEFIT_ITEMS]:
            if not isinstance(it, dict):
                continue
            raw_sub_items = it.get("sub_items")
            subs_in: list[Any] = (
                raw_sub_items if isinstance(raw_sub_items, list) else []
            )
            items.append({
                "number": str(it.get("number") or ""),
                "name": str(it.get("name") or ""),
                "value": _clean_value(it.get("value")),
                "note": _clean_value(it.get("note")),
                "limits": _clean_limits(it.get("limits")),
                "sub_items": [
                    {
                        "key": str(s.get("key") or ""),
                        "name": str(s.get("name") or ""),
                        "value": _clean_value(s.get("value")),
                        "note": _clean_value(s.get("note")),
                        "limits": _clean_limits(s.get("limits")),
                        "kind": _clean_value(s.get("kind")),
                    }
                    for s in subs_in if isinstance(s, dict)
                ],
                "properties": {
                    str(k): str(v) for k, v in (it.get("properties") or {}).items()
                } if isinstance(it.get("properties"), dict) else {},
                # Carried so the employee-facing renderer can format the value
                # by type instead of guessing from its digits.
                "kind": _clean_value(it.get("kind")),
            })
    return {"items": items}


_TERM_FIELDS = (
    "coverage_start",
    "coverage_end",
    "gst_included",
    "gst_rate",
    "free_cover_limit",
    "nel_age_limit",
    "underwriting_required",
    "policy_number",
    "pre_hosp_days",
    "post_hosp_days",
)


def _adoption_conflict(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"code": "duplicate_product_configuration", "message": message},
    )


def _adopt_slip_artifacts(
    db: Session, user: CurrentUser, product: Product, policy_year_id: str
) -> None:
    """Move same-code slip artifacts onto the company-owned setup product.

    Placement parsing commonly starts from a global catalog product, while a
    confirmed guided setup owns a client-specific product. The two IDs must not
    coexist for one code/year: matching and member summaries are product-ID
    based. On first materialization we therefore preserve the slip categories,
    terms, and pricing block by re-parenting them, then remove only provisional
    generated plans. Competing data on both IDs is ambiguous and fails closed.
    """
    source_products = list(
        db.scalars(
            select(Product).where(
                Product.id != product.id,
                tenant_or_global(Product.client_id, product.client_id or ""),
                func.upper(Product.code) == product.code.upper(),
            )
        ).all()
    )
    related_ids = [row.id for row in source_products]
    source_categories = (
        list(
            db.scalars(
                select(Category).where(
                    Category.policy_year_id == policy_year_id,
                    Category.product_id.in_(related_ids),
                )
            ).all()
        )
        if related_ids
        else []
    )
    target_categories = list(
        db.scalars(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id == product.id,
            )
        ).all()
    )
    if source_categories and target_categories:
        raise _adoption_conflict(
            f"{product.code} has eligibility categories on two product records. "
            "Reconcile the duplicate records before confirming."
        )
    for category in source_categories:
        category.product_id = product.id

    source_terms = (
        list(
            db.scalars(
                select(ProductTerm).where(
                    ProductTerm.policy_year_id == policy_year_id,
                    ProductTerm.product_id.in_(related_ids),
                )
            ).all()
        )
        if related_ids
        else []
    )
    if len(source_terms) > 1:
        raise _adoption_conflict(
            f"{product.code} has policy terms on multiple source products."
        )
    target_term = db.scalar(
        select(ProductTerm).where(
            ProductTerm.policy_year_id == policy_year_id,
            ProductTerm.product_id == product.id,
        )
    )
    if source_terms:
        source_term = source_terms[0]
        if target_term is None:
            source_term.product_id = product.id
        else:
            for field in _TERM_FIELDS:
                source_value = getattr(source_term, field)
                target_value = getattr(target_term, field)
                if source_value is None:
                    continue
                if target_value is None:
                    setattr(target_term, field, source_value)
                elif target_value != source_value:
                    raise _adoption_conflict(
                        f"{product.code} has conflicting policy terms on two "
                        "product records."
                    )
            db.delete(source_term)

    pricing = db.scalar(
        select(FlexPricing).where(FlexPricing.policy_year_id == policy_year_id)
    )
    pricing_moved = False
    if pricing is not None and isinstance(pricing.pricing, dict):
        bag = dict(pricing.pricing)
        blocks = bag.get("products")
        if isinstance(blocks, dict):
            blocks = dict(blocks)
            source_blocks = [pid for pid in related_ids if pid in blocks]
            if len(source_blocks) > 1 or (source_blocks and product.id in blocks):
                raise _adoption_conflict(
                    f"{product.code} has pricing on multiple product records."
                )
            if source_blocks:
                blocks[product.id] = blocks.pop(source_blocks[0])
                bag["products"] = blocks
                pricing.pricing = bag
                flag_modified(pricing, "pricing")
                pricing_moved = True

    provisional_ids = [product.id, *related_ids]
    plans_removed = 0
    for plan in db.execute(
        select(Plan).where(
            Plan.product_id.in_(provisional_ids),
            Plan.policy_year_id == policy_year_id,
            Plan.source == SourceKind.system_generated.value,
        )
    ).scalars():
        write_audit(
            db, user, action="delete", entity_type="plan", entity_id=plan.id,
            before={"code": plan.code, "product_id": plan.product_id,
                    "superseded_by": "product_setup"},
        )
        db.delete(plan)
        plans_removed += 1
    db.flush()
    if source_categories or source_terms or pricing_moved or plans_removed:
        write_audit(
            db,
            user,
            action="adopt_placement_slip_artifacts",
            entity_type="product",
            entity_id=product.id,
            before={"source_product_ids": related_ids},
            after={
                "categories_reparented": len(source_categories),
                "term_reparented": bool(source_terms),
                "pricing_rekeyed": pricing_moved,
                "provisional_plans_removed": plans_removed,
            },
        )


def _upsert_product(
    db: Session,
    user: CurrentUser,
    client_id: str,
    tpl: ProductTemplate,
    answers: dict[str, Any],
) -> Product:
    """Reuse the client's catalog product for this code, or create one.

    The Header & Policy "Insurer" answer is deliberately NOT copied onto the
    catalog row: the insurer is a per-benefit-year placement fact, and a catalog
    row spans every year (and, for firm-library rows, every company). Reports
    resolve it from the answers instead — see ``services/product_insurer.py``.
    """
    header = answers.get("header") or {}
    # The roster-anchored Entities multi-select — the matching gate for every
    # category of this product. Stored as tokens; absent/empty means no
    # restriction (categories then fall back to their own slip `insured`).
    entities = insured_names(header.get("entities"))
    code = tpl.code.strip().upper()
    has_dependants = _answers_have_dependants(answers)
    product = db.execute(
        select(Product).where(
            Product.client_id == client_id, func.upper(Product.code) == code
        )
    ).scalar_one_or_none()
    if product is None:
        product = Product(
            client_id=client_id,
            code=code,
            display_name=tpl.display_name,
            participation_model=tpl.participation_model,
            has_dependants=has_dependants,
            is_outpatient=tpl.is_outpatient,
            product_metadata={
                "line": infer_line(code),
                **({"entities": entities} if entities else {}),
            },
        )
        db.add(product)
        db.flush()
        write_audit(
            db, user, action="create", entity_type="product", entity_id=product.id,
            after={"code": code, "display_name": tpl.display_name, "source": "manual_setup"},
        )
        return product

    if product.code != code:
        product.code = code
    product.has_dependants = has_dependants
    # Written on every confirm (not just when non-empty) so CLEARING the field
    # actually lifts the restriction rather than silently keeping the old one.
    meta = dict(product.product_metadata or {})
    if entities:
        meta["entities"] = entities
    else:
        meta.pop("entities", None)
    if meta != (product.product_metadata or {}):
        product.product_metadata = meta
        flag_modified(product, "product_metadata")
    return product


def _materialize_plans(
    db: Session,
    user: CurrentUser,
    product: Product,
    policy_year_id: str,
    answers: dict[str, Any],
    selected: list[dict[str, Any]],
    cover_description: str | None,
    schedules: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, int, int]:
    existing = {
        p.code: p
        for p in db.execute(
            select(Plan).where(
                Plan.product_id == product.id,
                Plan.policy_year_id == policy_year_id,
            )
        ).scalars()
    }
    created = updated = 0
    seen_codes: set[str] = set()
    for plan in selected:
        code = str(plan["code"]).strip()
        seen_codes.add(code)
        # Reuse the caller's projection when it has one (confirm_setup builds
        # it for the orphan-key guard), so the two can never disagree.
        code = str(plan.get("code") or "")
        schedule = (schedules or {}).get(code) or _benefit_schedule(answers, plan)
        label = str(plan.get("label") or f"Plan {code}")[:255]
        row = existing.get(code)
        if row is None:
            row = Plan(
                product_id=product.id,
                policy_year_id=policy_year_id,
                code=code,
                display_name=label,
                benefit_schedule=schedule,
                cover_description=cover_description,
                source=_MANUAL,
                source_ref=_SETUP_REF,
                status="confirmed",
                human_modified=True,
                modified_by=user.user_id,
            )
            db.add(row)
            db.flush()
            write_audit(
                db, user, action="create", entity_type="plan", entity_id=row.id,
                after={"code": code, "product_id": product.id, "source": "manual_setup"},
            )
            created += 1
        else:
            row.display_name = label
            row.benefit_schedule = schedule
            row.cover_description = cover_description
            row.source = _MANUAL
            row.source_ref = _SETUP_REF
            row.status = "confirmed"
            row.human_modified = True
            row.modified_by = user.user_id
            write_audit(
                db, user, action="update", entity_type="plan", entity_id=row.id,
                after={"code": code, "product_id": product.id, "source": "manual_setup"},
            )
            updated += 1

    # Drop only plans THIS setup created (source=manual AND source_ref) that the
    # user deselected — never touch parsed plans or manual plans from other flows.
    removed = 0
    for code, row in existing.items():
        if code not in seen_codes and row.source == _MANUAL and row.source_ref == _SETUP_REF:
            write_audit(
                db, user, action="delete", entity_type="plan", entity_id=row.id,
                before={"code": code, "product_id": product.id},
            )
            db.delete(row)
            removed += 1
    return created, updated, removed


def _coerce_count(v: Any) -> int:
    """Coerce a headcount cell to a non-negative int, tolerating floats/strings.

    Decimals round to nearest; junk/negative coerce to 0 (rather than silently
    dropping the tier, which would understate num_employees)."""
    try:
        n = round(float(v))
    except (TypeError, ValueError):
        return 0
    return max(0, int(n))


# Untiered products (life/travel/statutory) carry a single rate, stored by the
# form under this synthetic key in rate_table[plan]. It mirrors the frontend
# RateTableSection FLAT_TIER constant. It is an internal encoding of the form
# draft only — confirm translates it into a first-class flat rate (rate_basis
# "flat" + premium_rate) so the sentinel never leaks into Category.plan_assignments
# (and so it can't resurface as a bogus tier on re-synthesis or in the financials view).
_FLAT_RATE_KEY = "flat"


def _coerce_money(v: Any) -> float | None:
    """Coerce a sum-insured / rate cell to a float, tolerating strings/commas."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _category_plan_assignments(
    row: dict[str, Any],
    rate_table: dict[str, Any],
    rate_model: str = "tiered",
    basis_model: str = "tiered",
) -> dict[str, Any]:
    """Build Category.plan_assignments (parser-compatible) from a grid row.

    Folds the plan's rate + this category's headcount into the same shape the
    slip parser emits, so the matched-plan financials view reads it unchanged.
    The encoding follows the product's ``rate_model`` / ``basis_model``:
      - tiered      → per-tier counts + a ``rate_tiers`` map.
      - per_member  -> single headcount + a per-member ``premium_rate``;
                      premium = rate x headcount when not supplied.
      - per_1000_si -> single headcount + ``sum_insured`` + ``basis``;
                      premium = (SI / 1,000) x rate when not supplied.
      - flat        -> GBT travel: one flat annual policy premium, no per-member
                      rate (``rate_basis="annual_flat"``).
      - earnings_based -> WICA statutory: premium = rate x estimated annual
                      earnings (``rate_basis="earnings_based"``).
    """
    plan_code = str(row.get("plan_code") or "").strip()
    tier_counts = {
        str(k): _coerce_count(v) for k, v in (row.get("tiers") or {}).items()
    }
    # Headcount: tiered rows sum their per-tier counts; untiered rows carry a
    # single num_employees on the row itself (no tier columns to sum).
    num_employees = (
        sum(tier_counts.values())
        if tier_counts
        else _coerce_count(row.get("num_employees"))
    )
    pa: dict[str, Any] = {
        "plan_code": plan_code,
        # Token list — one element per legal entity, so a comma inside a
        # registered name can't split it into two (see `insured_names`).
        "insured": insured_names(row.get("insured")),
        "num_employees": num_employees,
        "tier_counts": tier_counts,
    }
    # Sum-assured products carry SI + basis regardless of whether a rate was
    # entered, so the financials view and re-synthesis see the cover amount.
    sum_insured = _coerce_money(row.get("sum_insured"))
    basis = str(row.get("basis") or "").strip()
    if basis_model == "sum_assured":
        if sum_insured is not None:
            pa["sum_insured"] = sum_insured
        if basis:
            pa["basis"] = basis

    rate_cells = rate_table.get(plan_code) if isinstance(rate_table, dict) else None
    flat_cell = (
        rate_cells.get(_FLAT_RATE_KEY) if isinstance(rate_cells, dict) else None
    )

    def _flat(rate_basis: str, computed: float) -> dict[str, Any]:
        """Record a single-rate (flat) assignment; the entered premium wins over
        the computed fallback. Reads the synthetic 'flat' cell directly, so a
        stale EO/ES tier key left over from a prior classification can't divert a
        per_member/per_1000_si product into the tiered branch (review finding #2)."""
        if not isinstance(flat_cell, dict):
            return pa
        rate = float(flat_cell.get("rate") or 0)
        premium = float(flat_cell.get("premium") or 0)
        if rate or premium or (computed > 0):
            pa["rate_basis"] = rate_basis
            pa["premium_rate"] = rate
            annual = premium if premium > 0 else computed
            if annual > 0:
                pa["annual_premium"] = round(annual, 2)
        return pa

    # The product's rate_model is authoritative — branch on it first.
    if rate_model == "per_1000_si":
        computed = (sum_insured / 1000.0) * float(flat_cell.get("rate") or 0) \
            if (isinstance(flat_cell, dict) and sum_insured) else 0.0
        return _flat("per_1000_si", computed)
    if rate_model == "per_member":
        computed = num_employees * float(flat_cell.get("rate") or 0) \
            if (isinstance(flat_cell, dict) and num_employees) else 0.0
        return _flat("per_member", computed)
    if rate_model == "flat":
        # GBT travel: one flat annual policy premium covering everyone, no
        # per-member rate. The single figure rides in the flat cell's premium
        # (or rate, whichever the form supplied).
        premium = (
            float(flat_cell.get("premium") or flat_cell.get("rate") or 0)
            if isinstance(flat_cell, dict)
            else 0.0
        )
        pa["rate_basis"] = "annual_flat"
        if premium > 0:
            pa["annual_premium"] = round(premium, 2)
        return pa
    if rate_model == "earnings_based":
        # WICA statutory: premium = rate x estimated annual earnings. Carry the
        # earnings so the card can show it and recompute the premium.
        earnings = _coerce_money(row.get("estimated_annual_earnings"))
        rate = float(flat_cell.get("rate") or 0) if isinstance(flat_cell, dict) else 0.0
        pa["rate_basis"] = "earnings_based"
        if rate:
            pa["premium_rate"] = rate
        if earnings is not None:
            pa["estimated_annual_earnings"] = earnings
            if rate:
                pa["annual_premium"] = round(earnings * rate, 2)
        return pa

    # Tiered (and any legacy/default): build rate_tiers from the non-flat cells.
    if not isinstance(rate_cells, dict):
        return pa
    rate_tiers: dict[str, dict[str, float]] = {}
    annual_premium = 0.0
    for tier, cell in rate_cells.items():
        if tier == _FLAT_RATE_KEY or not isinstance(cell, dict):
            continue
        rate = float(cell.get("rate") or 0)
        premium = float(cell.get("premium") or 0)
        rate_tiers[str(tier)] = {"rate": rate, "premium": premium}
        annual_premium += premium
    if rate_tiers:
        pa["rate_basis"] = "tiered"
        pa["rate_tiers"] = rate_tiers
        if annual_premium > 0:
            pa["annual_premium"] = annual_premium
        return pa
    # No tier cells — fall back to a flat single rate if one was supplied.
    return _flat("flat", 0.0)


def _materialize_categories(
    db: Session,
    user: CurrentUser,
    product: Product,
    policy_year_id: str,
    answers: dict[str, Any],
    rate_model: str = "tiered",
    basis_model: str = "tiered",
) -> tuple[int, int]:
    """Create eligibility Category rows from the Basis-of-Cover grid.

    NOTE: ``confirm_setup`` now calls this only to *seed* a product that has no
    categories yet (manual products with no slip parse). Once any category
    exists it is left untouched, so the category cards (PATCH/POST/DELETE
    /categories) own editing and a re-confirm can't clobber inline edits.

    Idempotent: prior manual setup categories for this product are removed and
    rebuilt. Parsed categories (source != manual) are never touched.
    """
    rate_table = answers.get("rate_table") or {}
    rows = answers.get("categories")
    rows = rows if isinstance(rows, list) else []

    removed = 0
    for cat in db.execute(
        select(Category).where(
            Category.policy_year_id == policy_year_id,
            Category.product_id == product.id,
            Category.source == _MANUAL,
            Category.source_ref == _SETUP_REF,
        )
    ).scalars():
        write_audit(
            db, user, action="delete", entity_type="category", entity_id=cat.id,
            before={"display_name": cat.display_name, "product_id": product.id},
        )
        db.delete(cat)
        removed += 1
    db.flush()  # exclude the deleted rows from the max-priority probe below

    # Continue the policy year's priority sequence rather than restarting at 1,
    # so manual categories don't collide with parsed categories of the same
    # product (which would make matching tie-breaks order-dependent).
    base_priority = (
        db.execute(
            select(func.max(Category.priority)).where(
                Category.policy_year_id == policy_year_id
            )
        ).scalar()
        or 0
    )

    created = 0
    for offset, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        name = str(row.get("category") or "").strip()
        if not name:
            continue
        raw_participation = str(row.get("participation") or "")
        pspec = parse_participation(raw_participation)
        cat = build_manual_category(
            policy_year_id=policy_year_id,
            product_id=product.id,
            priority=base_priority + offset,
            display_name=name,
            source_ref=_SETUP_REF,
            # Product confirmation confirms the benefit setup, not an inferred
            # employee-matching rule. The company-aware mapper runs below and a
            # broker confirms the resulting eligibility mapping separately.
            status=CategoryStatus.needs_review.value,
            modified_by=user.user_id,
            human_modified=False,
            participation_model=pspec.employee
            or normalize_participation(raw_participation),
            participation_detail=pspec.to_dict(),
            plan_assignments=_category_plan_assignments(
                row, rate_table, rate_model, basis_model
            ),
        )
        db.add(cat)
        db.flush()
        write_audit(
            db, user, action="create", entity_type="category", entity_id=cat.id,
            after={"display_name": name[:512], "product_id": product.id,
                   "source": "manual_setup"},
        )
        created += 1
    return created, removed
