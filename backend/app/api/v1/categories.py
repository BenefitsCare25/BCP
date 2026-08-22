"""Categories CRUD with provenance envelope semantics.

PATCH flips `source` to manual and sets `human_modified=true`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_editable,
    assert_policy_year_for_user,
    load_category,
    require_client_id,
    tenant_or_global,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import (
    Category,
    Plan,
    PolicyYear,
    Product,
    ProductSetup,
)
from app.models.category import CategoryStatus, SourceKind
from app.models.product_setup import ProductSetupStatus
from app.schemas.api import (
    CategoryCreate,
    CategoryGrouped,
    CategoryOut,
    CategoryPatch,
)
from app.services.ai_breaker import CircuitOpenError
from app.services.ai_extractor import AINotConfiguredError, AIParseError
from app.services.ai_gateway import AIBudgetExceededError, generate_rule_for_category
from app.services.category_factory import build_manual_category
from app.services.eligibility_mapping import (
    assess_category_rule,
    build_ai_eligibility_inputs,
    confirm_category_mapping,
    validate_ai_matching_rule,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/categories", tags=["categories"])


def _assert_category_year_editable(db: Session, c: Category) -> None:
    """Configuration lock for handlers that load the Category first."""
    py = db.get(PolicyYear, c.policy_year_id)
    if py is not None:
        assert_policy_year_editable(py)


def _to_dict(c: Category) -> dict[str, Any]:
    return {
        "display_name": c.display_name,
        "matching_rule": c.matching_rule,
        "rule_human_readable": c.rule_human_readable,
        "mapping_profile_id": c.mapping_profile_id,
        "rule_status": c.rule_status,
        "rule_validation": c.rule_validation,
        "participation_model": c.participation_model,
        "participation_detail": c.participation_detail,
        "plan_assignments": c.plan_assignments,
        "status": c.status,
        "source": c.source,
        "confidence": c.confidence,
    }


@router.get("", response_model=list[CategoryOut])
def list_categories(
    policy_year_id: str,
    status_filter: CategoryStatus | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Category]:
    assert_policy_year_for_user(policy_year_id, user, db)
    stmt = select(Category).where(Category.policy_year_id == policy_year_id)
    if status_filter:
        stmt = stmt.where(Category.status == status_filter)
    stmt = stmt.order_by(Category.product_id, Category.priority)
    return list(db.execute(stmt).scalars().all())


@router.get("/grouped", response_model=list[CategoryGrouped])
def list_categories_grouped(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CategoryGrouped]:
    assert_policy_year_for_user(policy_year_id, user, db)
    cats = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == policy_year_id)
            .order_by(Category.priority)
        )
        .scalars()
        .all()
    )
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(tenant_or_global(Product.client_id, user.client_id))
        )
        .scalars()
        .all()
    }
    grouped: dict[str | None, list[Category]] = {}
    for c in cats:
        grouped.setdefault(c.product_id, []).append(c)
    out = []
    for product_id, items in grouped.items():
        product = products.get(product_id) if product_id else None
        out.append(
            CategoryGrouped(
                product_code=product.code if product else "(unassigned)",
                product_display_name=product.display_name if product else "Unassigned",
                product_id=product_id,
                line=product.line if product else "medical",
                categories=[CategoryOut.model_validate(c) for c in items],
            )
        )
    out.sort(key=lambda g: g.product_code)
    return out


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    """Create a new eligibility category (the cards' '+ Add category').

    The matching rule is seeded from the display name (the broker refines it via
    the rule editor). New categories land as ``needs_review`` so they surface for
    confirmation, mirroring slip-parsed rows.
    """
    assert_policy_year_editable(
        assert_policy_year_for_user(payload.policy_year_id, user, db)
    )
    if payload.product_id is not None:
        product = db.execute(
            select(Product).where(
                Product.id == payload.product_id,
                tenant_or_global(Product.client_id, user.client_id),
            )
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    name = payload.display_name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name required")
    base_priority = (
        db.execute(
            select(func.max(Category.priority)).where(
                Category.policy_year_id == payload.policy_year_id
            )
        ).scalar()
        or 0
    )
    cat = build_manual_category(
        policy_year_id=payload.policy_year_id,
        product_id=payload.product_id,
        priority=base_priority + 1,
        display_name=name,
        source_ref="category_card",
        status=CategoryStatus.needs_review.value,
        modified_by=user.user_id,
        participation_model=payload.participation_model,
        plan_assignments=payload.plan_assignments,
    )
    db.add(cat)
    db.flush()
    write_audit(
        db, user, action="create", entity_type="category", entity_id=cat.id,
        after={"display_name": name[:512], "product_id": payload.product_id},
    )
    db.commit()
    db.refresh(cat)
    return cat


@router.get("/{category_id}", response_model=CategoryOut)
def get_category(c: Category = Depends(load_category)) -> Category:
    return c


@router.patch("/{category_id}", response_model=CategoryOut)
def patch_category(
    payload: CategoryPatch,
    c: Category = Depends(load_category),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    _assert_category_year_editable(db, c)
    before = _to_dict(c)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(c, key, value)
    # Any human edit flips source to manual + sets human_modified
    c.source = SourceKind.manual.value
    c.human_modified = True
    c.modified_by = user.user_id
    if "matching_rule" in updates:
        # A broker-edited rule must pass the same company-schema validation as
        # generated rules before it can be confirmed/reused. Detach it from the
        # previous reusable profile so an unconfirmed edit never mutates memory.
        c.mapping_profile_id = None
        c.rule_status = (
            "unmapped" if c.matching_rule is None else "needs_review"
        )
        c.rule_validation = {
            "state": c.rule_status,
            "source": "manual",
            "errors": [],
            "warnings": ["Manual rule must be validated and confirmed"],
            "unresolved_clauses": [],
            "reused": False,
        }
        c.status = CategoryStatus.needs_review.value
    after = _to_dict(c)
    write_audit(
        db,
        user,
        action="update",
        entity_type="category",
        entity_id=c.id,
        before=before,
        after=after,
    )
    db.commit()
    db.refresh(c)
    return c


@router.post("/{category_id}/confirm", response_model=CategoryOut)
def confirm_category(
    c: Category = Depends(load_category),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    _assert_category_year_editable(db, c)
    before = _to_dict(c)
    try:
        confirm_category_mapping(
            db, category=c, client_id=require_client_id(user)
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Category cannot be confirmed: {exc}",
        ) from exc
    c.modified_by = user.user_id
    write_audit(
        db,
        user,
        action="confirm",
        entity_type="category",
        entity_id=c.id,
        before=before,
        after=_to_dict(c),
    )
    db.commit()
    db.refresh(c)
    return c


@router.post("/bulk-confirm", response_model=dict)
def bulk_confirm(
    policy_year_id: str,
    min_confidence: float = Query(0.85, ge=0.0, le=1.0),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    assert_policy_year_editable(
        assert_policy_year_for_user(policy_year_id, user, db)
    )
    rows = (
        db.execute(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.status == CategoryStatus.needs_review.value,
                Category.matching_rule.is_not(None),
                Category.confidence.is_not(None),
                Category.confidence >= min_confidence,
            )
        )
        .scalars()
        .all()
    )
    confirmed = 0
    skipped = 0
    for c in rows:
        before = _to_dict(c)
        try:
            confirm_category_mapping(
                db, category=c, client_id=require_client_id(user)
            )
        except ValueError:
            skipped += 1
            continue
        c.modified_by = user.user_id
        write_audit(
            db,
            user,
            action="bulk_confirm",
            entity_type="category",
            entity_id=c.id,
            before=before,
            after=_to_dict(c),
        )
        confirmed += 1
    db.commit()
    return {
        "confirmed": confirmed,
        "skipped_invalid_rules": skipped,
        "threshold": min_confidence,
    }


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    c: Category = Depends(load_category),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    _assert_category_year_editable(db, c)
    write_audit(
        db,
        user,
        action="delete",
        entity_type="category",
        entity_id=c.id,
        before=_to_dict(c),
    )
    db.delete(c)
    db.commit()


@router.delete("", response_model=dict)
@limiter.limit("10/minute")
def bulk_delete_categories(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Clear the slip-generated config for a policy year: every category, plus
    the unconfirmed guided-setup drafts and provisional (``system_generated``)
    plans that feed the setup form. These live in separate tables, so a
    categories-only delete would leave the form populated.

    Confirmed setups (and their plans) are authoritative and preserved. Writes
    one audit row with the counts — individual `before` snapshots aren't
    recorded (too noisy)."""
    assert_policy_year_editable(
        assert_policy_year_for_user(policy_year_id, user, db)
    )
    rows = list(
        db.execute(
            select(Category).where(Category.policy_year_id == policy_year_id)
        )
        .scalars()
        .all()
    )
    deleted = len(rows)
    for c in rows:
        db.delete(c)

    # Unconfirmed guided-setup drafts — what the form renders. Capture the
    # confirmed product codes first so their plans survive the prune below.
    confirmed_codes = set(
        db.execute(
            select(ProductSetup.product_code).where(
                ProductSetup.policy_year_id == policy_year_id,
                ProductSetup.status == ProductSetupStatus.confirmed,
            )
        ).scalars()
    )
    draft_setups = list(
        db.execute(
            select(ProductSetup).where(
                ProductSetup.policy_year_id == policy_year_id,
                ProductSetup.status != ProductSetupStatus.confirmed,
            )
        )
        .scalars()
        .all()
    )
    setups_deleted = len(draft_setups)
    for s in draft_setups:
        db.delete(s)

    # Provisional plans: system_generated and not owned by a confirmed setup.
    confirmed_product_ids = (
        {
            p.id
            for p in db.execute(
                select(Product).where(Product.code.in_(confirmed_codes))
            ).scalars()
        }
        if confirmed_codes
        else set()
    )
    plans = [
        p
        for p in db.execute(
            select(Plan).where(
                Plan.policy_year_id == policy_year_id,
                Plan.source == SourceKind.system_generated.value,
            )
        )
        .scalars()
        .all()
        if p.product_id not in confirmed_product_ids
    ]
    plans_deleted = len(plans)
    for p in plans:
        db.delete(p)

    write_audit(
        db,
        user,
        action="bulk_delete",
        entity_type="category",
        entity_id=None,
        after={
            "deleted": deleted,
            "setups_deleted": setups_deleted,
            "plans_deleted": plans_deleted,
            "policy_year_id": policy_year_id,
        },
    )
    db.commit()
    return {
        "deleted": deleted,
        "setups_deleted": setups_deleted,
        "plans_deleted": plans_deleted,
    }


@router.post("/{category_id}/ai-suggest", response_model=CategoryOut)
@limiter.limit("20/minute")
def ai_suggest_rule(
    request: Request,
    c: Category = Depends(load_category),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    """Route a single category through Gemini (Google Vertex)
    and store the generated rule. Source becomes `ai_extracted`, status
    stays `needs_review` so admin must confirm before activation.
    """
    _assert_category_year_editable(db, c)
    client_id = require_client_id(user)
    schema, context, catalog = build_ai_eligibility_inputs(
        db, category=c, client_id=client_id
    )

    try:
        result = generate_rule_for_category(
            db,
            client_id=client_id,
            policy_year_id=c.policy_year_id,
            description=c.raw_description,
            schema=schema,
            context=context,
        )
    except AINotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except CircuitOpenError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except AIBudgetExceededError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except AIParseError as exc:
        logger.warning("Malformed AI rule response for category %s: %s", c.id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "AI returned an invalid structured response; no category changes were saved",
        ) from exc
    except Exception as exc:
        # Provider messages can echo credentials or prompt data, so record only
        # the exception class and the correlation-scoped category id.
        logger.error(
            "AI provider error for category %s (%s)", c.id, type(exc).__name__
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "AI provider error — see server logs",
        ) from exc

    envelope = result.envelope
    unresolved = [
        str(value)
        for value in result.metadata.get("unresolved_clauses", [])
        if isinstance(value, (str, int, float))
    ][:20]
    validation = validate_ai_matching_rule(c.raw_description, envelope.rule, catalog)
    if envelope.rule is None:
        reason = str(result.metadata.get("reasoning") or "").strip()
        detail = "AI could not map this wording to the company's employee fields"
        if unresolved:
            detail += f". Unresolved: {', '.join(unresolved)}"
        elif reason:
            detail += f". {reason[:300]}"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)
    if not validation.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "AI suggestion was rejected before saving: " + "; ".join(validation.errors),
        )

    before = _to_dict(c)
    c.matching_rule = envelope.rule
    c.rule_human_readable = envelope.human_readable[:1024]
    c.confidence = envelope.confidence
    c.source = SourceKind.ai_extracted.value
    c.status = CategoryStatus.needs_review.value
    c.human_modified = False
    c.mapping_profile_id = None
    assessment = assess_category_rule(
        db,
        category=c,
        client_id=client_id,
        unresolved_clauses=unresolved,
        source="ai_extracted",
    )
    c.rule_status = assessment.rule_status
    c.rule_validation = {
        **assessment.validation,
        "ai_reasoning": str(result.metadata.get("reasoning") or "")[:1024],
        "ai_prompt_version": result.metadata.get("prompt_version"),
        "ai_cache_hit": result.cache_hit,
    }
    c.modified_by = user.user_id
    write_audit(
        db,
        user,
        action="ai_suggest",
        entity_type="category",
        entity_id=c.id,
        before=before,
        after={**_to_dict(c), "ai_meta": result.metadata, "cache_hit": result.cache_hit},
    )
    db.commit()
    db.refresh(c)
    return c


@router.get("/stats/coverage", response_model=dict)
def coverage_stats(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Coverage breakdown — collapsed into a single round trip with conditional
    aggregates so a policy year with 100k categories needs only one query."""
    assert_policy_year_for_user(policy_year_id, user, db)
    row = db.execute(
        select(
            func.count(Category.id),
            func.coalesce(
                func.sum(
                    case((Category.status == CategoryStatus.confirmed.value, 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((Category.status == CategoryStatus.needs_review.value, 1), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Category.confidence.is_not(None))
                            & (Category.confidence >= 0.85),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(Category.policy_year_id == policy_year_id)
    ).one()
    total, confirmed, needs_review, high_conf = (int(v or 0) for v in row)
    return {
        "total": total,
        "confirmed": confirmed,
        "needs_review": needs_review,
        "high_confidence": high_conf,
    }
