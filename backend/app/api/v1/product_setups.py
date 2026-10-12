"""Guided product setup — the forward counterpart to slip parsing.

Drives a form that builds a brand-new product configuration from the insurer's
standard SME scheme template, then materializes it into the catalog `Product`
plus per-plan `Plan` rows (`source="manual"`) — the same shape the placement-slip
parser emits, so everything downstream (matching, activation) works unchanged.

- GET    /policy-years/{id}/setup-products                         — products available to set up
- GET    /policy-years/{id}/setup-products/{code}/template         — template that drives the form
- GET    /product-registry                                          — product catalog registry
- GET    /policy-years/{id}/entity-vocab                            — insured-entity vocabulary
- POST   /policy-years/{id}/member-counts                           — headcounts for the form
- GET    /policy-years/{id}/product-setups                          — this year's setup drafts
- GET    /policy-years/{id}/product-setups/{code}                   — one draft (resume editing)
- GET    /policy-years/{id}/product-setups/{code}/field-suggestions — answer suggestions
- PUT    /policy-years/{id}/product-setups/{code}                   — save/replace the draft
- DELETE /policy-years/{id}/product-setups/{code}                   — discard the draft
- DELETE /policy-years/{id}/products/{code}                         — remove the product
- POST   /policy-years/{id}/product-setups/{code}/confirm           — save + materialize

Tenant scoping rides on `load_policy_year` (the setup is keyed by the already
tenant-checked policy year), matching the recommendations router pattern.

The first five (the form's reads) live in `product_setup_form`, mounted on this
module's routers; the draft and confirm helpers live in
`services/product_setup_drafts` and `services/product_setup_materialization`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api.v1 import product_setup_form
from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_editable,
    load_policy_year,
    policy_year_company,
    require_client_id,
    tenant_or_global,
)
from app.db.session import get_db
from app.models import Category, Employee, Plan, PolicyYear, Product, ProductSetup, ProductTerm
from app.models.product_setup import ProductSetupOrigin, ProductSetupStatus
from app.schemas.product_setup import ConfirmResult, FieldSuggestions, SetupOut, SetupSaveIn
from app.services.benefit_key_guard import (
    orphan_conflict_detail,
    orphaned_benefit_keys,
    schedule_benefit_names,
)
from app.services.claim_intake import claim_scope_catalog
from app.services.claim_limits import validate_schedule_limits
from app.services.eligibility_mapping import auto_map_policy_year
from app.services.matching_engine import match_policy_year
from app.services.product_setup_drafts import (
    _SUGGESTION_SCAN_LIMIT,
    _aggregate_suggestions,
    _find_setup,
    _guard_saved_removals,
    _products_in_use,
    _refuse_removal_with_records,
    _resolve_template,
    _setup_codes_in_use,
    _upsert_draft,
    seed_draft_from_slip,
)
from app.services.product_setup_materialization import (
    _FLAT_RATE_KEY,
    _adopt_slip_artifacts,
    _benefit_schedule,
    _category_plan_assignments,
    _coerce_money,
    _map_category_plan_codes,
    _materialize_categories,
    _materialize_plans,
    _selected_plans,
    _sync_term_policy_number,
    _upsert_product,
)
from app.services.product_term_updates import apply_product_term_update, parse_setup_policy_terms

# Names other modules and tests import from here. The helpers now live in the
# product-setup services and are re-exported so those imports keep working.
# Tests also monkeypatch `_resolve_template` and `auto_map_policy_year` on this
# module, so the endpoints below must keep calling them through this namespace.
__all__ = [
    "_FLAT_RATE_KEY",
    "SetupSaveIn",
    "_adopt_slip_artifacts",
    "_benefit_schedule",
    "_category_plan_assignments",
    "_coerce_money",
    "confirm_setup",
    "read_router",
    "router",
    "seed_draft_from_slip",
]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["product-setup"])
# Reads served over POST (the draft rows do not fit a query string). main.py
# registers this outside the method-based write gate, like member_query.
read_router = APIRouter(tags=["product-setup"])
# The setup form's reads (`product_setup_form`) are mounted first, keeping the
# original route order — and with it the OpenAPI document — unchanged.
router.include_router(product_setup_form.router)
read_router.include_router(product_setup_form.read_router)

_MAX_COVER_DESC = 512


def _setup_out(s: ProductSetup) -> SetupOut:
    from app.services.el_report_rules import prefill_report_headers

    return SetupOut(
        id=s.id,
        policy_year_id=s.policy_year_id,
        product_code=s.product_code,
        template_version=s.template_version,
        answers=prefill_report_headers(s.answers or {}),
        status=s.status,
        origin=s.origin,
        confirmed_at=s.confirmed_at,
        materialized_product_id=s.materialized_product_id,
        updated_at=s.updated_at,
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
    client_id = policy_year_company(py, user)
    # Only the canonical code is needed here — skip synthesizing structure.
    tpl = _resolve_template(
        db, policy_year_id, product_code, client_id, need_structure=False
    )
    if tpl is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No template or slip data for product {product_code!r}",
        )
    _guard_saved_removals(db, policy_year_id, tpl.code, body, user)
    setup = _upsert_draft(db, policy_year_id, tpl.code, body, lock=True)
    setup.status = ProductSetupStatus.draft
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
    if setup.status == ProductSetupStatus.confirmed and setup.materialized_product_id:
        from app.services.el_report_rules import apply_report_rules

        apply_report_rules(
            db, policy_year_id, setup.materialized_product_id, setup.answers,
            preserve_existing=True,
        )
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
    row is also dropped when no *other* policy year still uses it. A product
    configured in another year keeps its row but still leaves this year's tab
    (nothing here references it any more), and is re-added by creating its setup
    draft. Refused (409) while members hold enrolment, override, underwriting or
    claim records for it this year. Idempotent."""
    assert_policy_year_editable(py)
    client_id = policy_year_company(py, user)
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
    _refuse_removal_with_records(db, policy_year_id, pids, code)

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
    # references it (so a product set up elsewhere, and its member records, survive).
    # A setup there, even a draft with no plans yet, resolves its form through it.
    client_rows = [p for p in products if p.client_id is not None]
    used_elsewhere = _products_in_use(
        db, [p.id for p in client_rows], outside_year=policy_year_id
    )
    if code not in _setup_codes_in_use(db, client_id, outside_year=policy_year_id):
        for p in client_rows:
            if p.id not in used_elsewhere:
                db.delete(p)

    # Sessions disable autoflush. Apply the removals before matching so its
    # category index cannot retain rows that disappear at the first batch flush.
    # This stays in the same transaction; a matching failure rolls it all back.
    db.flush()

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
    client_id = policy_year_company(py, user)
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
    _guard_saved_removals(db, policy_year_id, tpl.code, body, user)
    setup = _upsert_draft(db, policy_year_id, tpl.code, body, lock=True)

    staged_terms = parse_setup_policy_terms(setup.answers.get("policy_terms", {}))

    selected = _selected_plans(setup.answers)
    if setup.answers.get("source_issues") and setup.answers.get("source_reviewed") is not True:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Review the source rate and mapping issues before confirming this setup.",
        )
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
    valid_limit_scopes = {
        scope.code
        for scope in claim_scope_catalog(tpl.code, tpl.display_name or tpl.code)
    }
    limit_errors: list[str] = []
    for plan_code, schedule in schedules.items():
        limit_errors.extend(
            f"{plan_code}: {message}"
            for message in validate_schedule_limits(
                schedule, valid_scope_codes=valid_limit_scopes
            )
        )
    if limit_errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_claim_limit_settings",
                "message": "Review the claim-limit settings before confirming.",
                "errors": limit_errors,
            },
        )
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
        _sync_term_policy_number(db, product, policy_year_id, setup.answers, user)
        if staged_terms.model_fields_set:
            apply_product_term_update(db, py, product, staged_terms, user)
        from app.services.product_term_updates import apply_source_term_defaults

        apply_source_term_defaults(
            db, py, product, setup.answers, staged_terms.model_fields_set, user,
        )
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
        from app.services.el_report_rules import apply_report_rules

        apply_report_rules(db, policy_year_id, product.id, setup.answers)
        # Applied patches must not be replayed over later operational term edits.
        setup.answers = {**setup.answers, "policy_terms": {}}
        write_audit(
            db, user, action="confirm_setup", entity_type="product_setup",
            entity_id=setup.id,
            after={"product_id": product.id,
                   "policy_number_mappings": setup.answers.get("policy_number_mappings"),
                   "plans_created": created,
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
