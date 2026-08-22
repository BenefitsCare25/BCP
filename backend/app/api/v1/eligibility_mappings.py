"""Company-aware category matching proposal and review workflow."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_editable,
    load_policy_year,
    require_client_id,
    tenant_or_global,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import Category, Plan, PolicyYear, Product
from app.models.category import CategoryStatus, SourceKind
from app.schemas.api import AICategoryCreate, CategoryOut, EligibilityMappingSummaryOut
from app.services.ai_breaker import CircuitOpenError
from app.services.ai_extractor import AINotConfiguredError, AIParseError
from app.services.ai_gateway import AIBudgetExceededError, generate_rule_for_category
from app.services.eligibility_mapping import (
    assess_category_rule,
    auto_map_policy_year,
    build_ai_eligibility_inputs,
    stored_mapping_summary,
    validate_ai_matching_rule,
)
from app.services.matching_engine import match_policy_year

router = APIRouter(prefix="/policy-years", tags=["eligibility-mappings"])
logger = logging.getLogger(__name__)


@router.get(
    "/{policy_year_id}/eligibility-mappings",
    response_model=EligibilityMappingSummaryOut,
)
def get_eligibility_mappings(
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> EligibilityMappingSummaryOut:
    """Return the last stored mapping proposals without mutating configuration."""

    return EligibilityMappingSummaryOut.model_validate(
        stored_mapping_summary(db, policy_year_id=py.id), from_attributes=True
    )


@router.post(
    "/{policy_year_id}/eligibility-mappings/propose",
    response_model=EligibilityMappingSummaryOut,
)
def propose_eligibility_mappings(
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EligibilityMappingSummaryOut:
    """Generate company-aware rules, validate them, persist, and re-match.

    The compiler reuses confirmed company mappings first, then resolves against
    the current non-PII roster vocabulary. It never auto-confirms a category.
    """

    assert_policy_year_editable(py)
    summary = auto_map_policy_year(
        db,
        policy_year_id=py.id,
        client_id=require_client_id(user),
    )
    match_summary = match_policy_year(db, py.id, user) if summary.employee_count else None
    write_audit(
        db,
        user,
        action="propose_eligibility_mappings",
        entity_type="policy_year",
        entity_id=py.id,
        after={
            "validated": summary.validated,
            "proposed": summary.proposed,
            "needs_review": summary.needs_review,
            "unmapped": summary.unmapped,
            "reused": summary.reused,
            "employees_matched": (
                match_summary.employees_matched if match_summary is not None else None
            ),
        },
    )
    db.commit()
    return EligibilityMappingSummaryOut.model_validate(summary, from_attributes=True)


@router.post(
    "/{policy_year_id}/eligibility-mappings/ai-create-category",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
def ai_create_missing_category(
    request: Request,
    payload: AICategoryCreate,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    """Create a missing plan category from broker-authoritative wording.

    AI compiles the wording against this company's non-PII roster vocabulary;
    the rule must pass deterministic validation before the category is inserted.
    The result always remains ``needs_review`` until a broker confirms it.
    """

    assert_policy_year_editable(py)
    client_id = require_client_id(user)
    plan = db.execute(
        select(Plan)
        .where(Plan.id == payload.plan_id, Plan.policy_year_id == py.id)
        .with_for_update()
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    product = db.execute(
        select(Product).where(
            Product.id == plan.product_id,
            tenant_or_global(Product.client_id, client_id),
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    existing = list(
        db.execute(
            select(Category).where(
                Category.policy_year_id == py.id,
                Category.product_id == plan.product_id,
            )
        ).scalars()
    )
    if any(
        isinstance(category.plan_assignments, dict)
        and str(category.plan_assignments.get("plan_code") or "").strip().casefold()
        == plan.code.strip().casefold()
        for category in existing
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This plan already has an employee category",
        )

    description = payload.eligibility_description.strip()
    display_name = (payload.display_name or "").strip() or description
    priority = (
        db.execute(
            select(func.max(Category.priority)).where(Category.policy_year_id == py.id)
        ).scalar()
        or 0
    ) + 1
    category = Category(
        policy_year_id=py.id,
        product_id=product.id,
        priority=priority,
        display_name=display_name[:512],
        raw_description=description[:2048],
        matching_rule=None,
        rule_human_readable=None,
        participation_model=payload.participation_model,
        plan_assignments={"plan_code": plan.code},
        source=SourceKind.ai_extracted.value,
        source_ref=f"ai_missing_plan:{plan.id}",
        status=CategoryStatus.needs_review.value,
        human_modified=False,
        modified_by=user.user_id,
    )
    schema, context, catalog = build_ai_eligibility_inputs(
        db, category=category, client_id=client_id, plan=plan
    )
    try:
        result = generate_rule_for_category(
            db,
            client_id=client_id,
            policy_year_id=py.id,
            description=description,
            schema=schema,
            context=context,
            operation="ai_create_missing_category",
        )
    except AINotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except CircuitOpenError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except AIBudgetExceededError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except AIParseError as exc:
        logger.warning("Malformed AI response while creating plan %s category: %s", plan.id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "AI returned an invalid structured response; no category was created",
        ) from exc
    except Exception as exc:
        logger.error(
            "AI provider error while creating plan %s category (%s)",
            plan.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "AI provider error; no category was created",
        ) from exc

    envelope = result.envelope
    unresolved = [
        str(value)
        for value in result.metadata.get("unresolved_clauses", [])
        if isinstance(value, (str, int, float))
    ][:20]
    validation = validate_ai_matching_rule(description, envelope.rule, catalog)
    if envelope.rule is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "AI could not map the eligibility wording to this company's employee fields"
            + (f". Unresolved: {', '.join(unresolved)}" if unresolved else ""),
        )
    if not validation.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "AI suggestion was rejected before creation: "
            + "; ".join(validation.errors),
        )

    category.matching_rule = envelope.rule
    category.rule_human_readable = envelope.human_readable[:1024]
    category.confidence = envelope.confidence
    db.add(category)
    db.flush()
    assessment = assess_category_rule(
        db,
        category=category,
        client_id=client_id,
        unresolved_clauses=unresolved,
        source="ai_extracted",
    )
    category.rule_status = assessment.rule_status
    category.rule_validation = {
        **assessment.validation,
        "ai_reasoning": str(result.metadata.get("reasoning") or "")[:1024],
        "ai_prompt_version": result.metadata.get("prompt_version"),
        "ai_cache_hit": result.cache_hit,
        "created_for_missing_plan": True,
    }
    write_audit(
        db,
        user,
        action="ai_create_missing_category",
        entity_type="category",
        entity_id=category.id,
        after={
            "display_name": category.display_name,
            "product_id": product.id,
            "plan_id": plan.id,
            "plan_code": plan.code,
            "rule_status": category.rule_status,
            "matching_rule": category.matching_rule,
            "confidence": category.confidence,
            "ai_prompt_version": result.metadata.get("prompt_version"),
            "ai_cache_hit": result.cache_hit,
        },
    )
    db.commit()
    db.refresh(category)
    return category


__all__ = ["router"]
