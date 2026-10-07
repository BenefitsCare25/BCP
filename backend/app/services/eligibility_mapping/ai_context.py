"""Bounded, non-PII context for AI eligibility suggestions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Category, EmployeeAttributeSchema, Plan, Product
from app.schemas.api import AttributeSchemaOut
from app.services.derivation_engine import resolve_attribute_schemas
from app.services.eligibility_mapping.base import AttributeValueCatalog
from app.services.eligibility_mapping.catalog import build_attribute_catalog
from app.services.eligibility_mapping.clauses import _explicit_grade_mapping
from app.services.eligibility_mapping.proposal import propose_category_rule
from app.services.eligibility_mapping.validation import validate_ai_matching_rule

_AI_CONTEXT_MAX_VALUES = 40
_AI_CONTEXT_MAX_DISTINCT = 100
_AI_CONTEXT_MAX_SIBLINGS = 25


def _prompt_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:128]


def _usable_ai_schemas(
    schemas: list[EmployeeAttributeSchema], catalog: AttributeValueCatalog
) -> list[EmployeeAttributeSchema]:
    """Keep AI attributes aligned with the available employee listing."""

    if catalog.roster_present:
        return [schema for schema in schemas if catalog.populated.get(schema.attribute_id, 0) > 0][
            :64
        ]
    return [
        schema
        for schema in schemas
        if catalog.values.get(schema.attribute_id)
        or catalog.configured_values.get(schema.attribute_id)
    ][:64]


def build_ai_eligibility_inputs(
    db: Session,
    *,
    category: Category,
    client_id: str,
    plan: Plan | None = None,
) -> tuple[list[AttributeSchemaOut], dict[str, Any], AttributeValueCatalog]:
    """Build bounded, non-PII context for one AI eligibility request.

    Employee rows, names, staff IDs, and high-cardinality/free-text values are
    deliberately never included. The model sees only resolved schemas plus a
    small company vocabulary that the deterministic validator will enforce
    again after generation.
    """

    catalog, _, _ = build_attribute_catalog(db, category.policy_year_id, client_id)
    resolved = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    safe_schemas = sorted(
        (
            schema
            for schema in resolved
            if not schema.is_pii and schema.allow_ai_values and schema.allow_matching
        ),
        key=lambda value: value.attribute_id,
    )
    usable_schemas = _usable_ai_schemas(safe_schemas, catalog)
    schema_out = []
    for schema in usable_schemas:
        enum_values = list(schema.enum_values or [])[:100]
        if catalog.roster_present and schema.data_type.casefold() == "enum":
            enum_values = list(catalog.values.get(schema.attribute_id, []))[:100]
        schema_out.append(
            AttributeSchemaOut.model_validate(schema).model_copy(
                update={"enum_values": enum_values or None}
            )
        )

    product = (
        db.execute(
            select(Product).where(
                Product.id == category.product_id,
                tenant_or_global(Product.client_id, client_id),
            )
        ).scalar_one_or_none()
        if category.product_id
        else None
    )
    pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
    plan_code = str(pa.get("plan_code") or "").strip()
    if plan is None and product is not None and plan_code:
        plan = db.execute(
            select(Plan).where(
                Plan.policy_year_id == category.policy_year_id,
                Plan.product_id == product.id,
                Plan.code == plan_code,
            )
        ).scalar_one_or_none()

    sibling_stmt = select(Category).where(
        Category.policy_year_id == category.policy_year_id,
        Category.product_id == category.product_id,
    )
    if category.id:
        sibling_stmt = sibling_stmt.where(Category.id != category.id)
    siblings = list(
        db.execute(
            sibling_stmt.order_by(Category.priority).limit(_AI_CONTEXT_MAX_SIBLINGS)
        ).scalars()
    )

    employee_attributes: list[dict[str, Any]] = []
    source_attribute_id, source_attribute_values, _ = _explicit_grade_mapping(
        category.raw_description, catalog
    )
    for schema in sorted(usable_schemas, key=lambda value: value.attribute_id):
        attribute_id = schema.attribute_id
        populated = catalog.populated.get(attribute_id, 0)
        observed = catalog.values.get(attribute_id, []) if populated else []
        include_values = len(observed) <= _AI_CONTEXT_MAX_DISTINCT
        employee_attributes.append(
            {
                "attribute_id": attribute_id,
                "data_type": schema.data_type,
                "populated_employee_count": populated,
                "observed_distinct_count": len(observed),
                "observed_values": (
                    [_prompt_scalar(value) for value in observed[:_AI_CONTEXT_MAX_VALUES]]
                    if include_values
                    else []
                ),
                "observed_values_withheld": not include_values,
                "authoritative_source_values": (
                    [_prompt_scalar(value) for value in source_attribute_values]
                    if attribute_id == source_attribute_id
                    else []
                ),
                "configured_values": [
                    _prompt_scalar(value)
                    for value in (
                        []
                        if catalog.roster_present
                        else catalog.configured_values.get(attribute_id, [])[:100]
                    )
                ],
            }
        )

    deterministic = propose_category_rule(category.raw_description, catalog)
    deterministic_validation = validate_ai_matching_rule(
        category.raw_description, deterministic.rule, catalog
    )
    deterministic_rule = deterministic.rule if deterministic_validation.valid else None
    ai_attribute_ids = {schema.attribute_id for schema in usable_schemas}
    internal_only_candidate = bool(
        set(deterministic_validation.referenced_attributes) - ai_attribute_ids
    )
    if internal_only_candidate:
        # The deterministic normalizer runs again after the model response, so
        # internal-only constraints (for example nationality="Thai") need not
        # and must not ride inside the external prompt as a fallback rule.
        deterministic_rule = None
    context: dict[str, Any] = {
        "product": (
            {"code": product.code, "display_name": product.display_name}
            if product is not None
            else None
        ),
        "plan": (
            {
                "code": plan.code,
                "display_name": plan.display_name,
                "cover_description": plan.cover_description,
            }
            if plan is not None
            else ({"code": plan_code} if plan_code else None)
        ),
        "target": {
            "display_name": category.display_name[:512],
            "plan_code": plan_code or None,
            "expected_count": pa.get("num_employees"),
        },
        "sibling_categories": [
            {
                "display_name": sibling.display_name[:256],
                "raw_description": sibling.raw_description[:512],
                "plan_code": str((sibling.plan_assignments or {}).get("plan_code") or "") or None,
                "current_reading": (
                    sibling.rule_human_readable[:512] if sibling.rule_human_readable else None
                ),
            }
            for sibling in siblings
        ],
        "employee_attributes": employee_attributes,
        "deterministic_candidate": {
            "rule": deterministic_rule,
            "human_readable": (
                None if internal_only_candidate else deterministic.human_readable
            ),
            "unresolved_clauses": (
                ["An internal-only condition will be restored after AI suggestion."]
                if internal_only_candidate
                else list(
                    dict.fromkeys(
                        [
                            *deterministic.unresolved_clauses,
                            *deterministic_validation.errors,
                        ]
                    )
                )
            ),
        },
        "employee_listing_available": catalog.roster_present,
        "employee_count": catalog.employee_count,
    }
    return schema_out, context, catalog
