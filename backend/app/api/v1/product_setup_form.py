"""Guided product setup — the read endpoints that drive the setup form.

- GET    /policy-years/{id}/setup-products                         — products available to set up
- GET    /policy-years/{id}/setup-products/{code}/template         — template that drives the form
- GET    /product-registry                                          — product catalog registry
- GET    /policy-years/{id}/entity-vocab                            — insured-entity vocabulary
- POST   /policy-years/{id}/member-counts                           — headcounts for the form

These routers are included into `product_setups.router` / `.read_router`, ahead
of the draft endpoints, so main.py registration, tags and route order are those
of the single product-setup module this was split from. Tenant scoping rides on
`load_policy_year`.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_policy_year, require_client_id, tenant_or_global
from app.db.session import get_db
from app.models import Category, Plan, PolicyYear, Product, ProductSetup, ProductTerm
from app.schemas.api import InsuranceLineStr
from app.services import product_registry
from app.services.claim_intake import claim_scope_catalog
from app.services.claim_limits import claim_scope_match_terms
from app.services.entity_vocab import entity_vocabulary
from app.services.form_profiles import basis_model_for, infer_profile, rate_model_for
from app.services.insurance_lines import infer_line
from app.services.member_counts import DraftCategory, compute_member_counts
from app.services.product_setup_drafts import (
    _products_in_use,
    _resolve_template,
    _setup_codes_in_use,
)
from app.services.product_templates import ProductTemplate, list_templates

# The "product-setup" tag comes from the `product_setups` routers these are
# included into; tagging here too would list it twice on every operation.
router = APIRouter()
# Reads served over POST (the draft rows do not fit a query string). Included
# into `product_setups.read_router`, which main.py registers outside the
# method-based write gate, like member_query.
read_router = APIRouter()


# ── DTOs ────────────────────────────────────────────────────────────────────


class MemberCountCategoryIn(BaseModel):
    """One draft Basis-of-Cover row: client-side key + its category text.

    ``insured`` (the row's legal-entity list, when stated) scopes the preview
    to that entity's employees — mirroring the matching engine's gate."""

    key: str
    description: str = ""
    # Token list from the picker; a legacy comma-joined string still parses.
    insured: str | list[str] | None = None
    location_scope: str | None = None


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


class UnmatchedEmployeeOut(BaseModel):
    employee_id: str
    staff_id: str
    employee_name: str | None
    grade: str | None


class MemberCountsOut(BaseModel):
    counts: list[CategoryMemberCount]
    employees_total: int
    employees_in_scope: int
    employees_matched: int
    unmatched_grades: dict[str, int]
    unmatched_employees: list[UnmatchedEmployeeOut]
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


# ── Setup products (slip-detected + hand-authored, tenant-scoped) ────────────


class SetupProductSummary(BaseModel):
    code: str
    display_name: str
    has_template_file: bool
    has_slip_data: bool
    # Medical / General / Life / Flex line for tab routing.
    line: InsuranceLineStr = "medical"
    # True when the client's own catalog row for this code (the user added it,
    # or a slip created it) belongs to THIS year: the year has a category, plan,
    # coverage term or setup for it, or the row is not yet used in any year (just
    # added). Drives whether the product surfaces as a configurable card in its
    # tab, so a product removed from this year leaves it even while another year
    # still uses it. A bare global recognition row is never a client product.
    is_client_product: bool = False
    # Structural classification (registry + product_metadata override) so the
    # frontend needs no hardcoded code→profile map.
    form_profile: str | None = None
    layout_family: str | None = None
    has_dependants: bool = False
    # The client Product row id, when one exists (in any year) — the target for
    # the classification PATCH (/schemas/products/{id}). Set while
    # is_client_product is False, it means the product is re-added to this year
    # by creating its setup draft, not by creating the catalog row again.
    product_id: str | None = None
    # Product-variant identity (see ProductOut): None on a base product.
    base_code: str | None = None
    variant_label: str | None = None


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
    term_pids = set(
        db.execute(
            select(ProductTerm.product_id).where(
                ProductTerm.policy_year_id == policy_year_id
            )
        ).scalars()
    )
    year_setup_codes = {
        c.strip().upper()
        for c in db.execute(
            select(ProductSetup.product_code).where(
                ProductSetup.policy_year_id == policy_year_id
            )
        ).scalars()
    }
    catalog = list(
        db.execute(
            select(Product).where(tenant_or_global(Product.client_id, client_id))
        ).scalars()
    )
    used_pids = _products_in_use(
        db, [p.id for p in catalog if p.client_id is not None]
    )
    used_setup_codes = _setup_codes_in_use(db, client_id)
    out: dict[str, SetupProductSummary] = {}
    for p in catalog:
        # A code can have both a global (client_id NULL) and a client-specific
        # row; collapse them, OR-ing slip data and preferring the client display.
        code = p.code.strip().upper()
        has_slip = p.id in pids
        has_client_row = p.client_id is not None
        in_year = p.id in pids or p.id in term_pids or code in year_setup_codes
        unused = p.id not in used_pids and code not in used_setup_codes
        is_client = has_client_row and (in_year or unused)
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
                product_id=p.id if has_client_row else None,
                **product_registry.variant_fields(p.code, meta),
            )
        else:
            existing.has_slip_data = existing.has_slip_data or has_slip
            existing.is_client_product = existing.is_client_product or is_client
            if has_client_row:
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
                variant = product_registry.variant_fields(p.code, meta)
                existing.base_code = variant["base_code"]
                existing.variant_label = variant["variant_label"]
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
    scopes = claim_scope_catalog(tpl.code, tpl.display_name or tpl.code)
    match_terms, exclude_terms = claim_scope_match_terms(tpl.code)
    return tpl.model_copy(
        update={
            "claim_scopes": [
                {
                    "code": scope.code,
                    "label": scope.label,
                    "sub_type": scope.sub_type,
                    # The import-time suggestion rules, so the editor's
                    # "Suggested" marks agree with what was seeded.
                    "match_terms": match_terms.get(scope.code, []),
                    # Backend check order (first match wins); the catalog
                    # order above is display order and differs from it.
                    "match_priority": (
                        list(match_terms).index(scope.code)
                        if scope.code in match_terms
                        else None
                    ),
                    "exclude_terms": exclude_terms,
                }
                for scope in scopes
            ]
        }
    )


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


@read_router.post(
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
            DraftCategory(
                key=c.key,
                description=c.description,
                insured=c.insured,
                location_scope=c.location_scope,
            )
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
        employees_in_scope=result.employees_in_scope,
        employees_matched=result.employees_matched,
        unmatched_grades=result.unmatched_grades,
        unmatched_employees=[
            UnmatchedEmployeeOut(
                employee_id=employee.employee_id,
                staff_id=employee.staff_id,
                employee_name=employee.employee_name,
                grade=employee.grade,
            )
            for employee in result.unmatched_employees
        ],
        has_dependants=result.has_dependants,
    )
