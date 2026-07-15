"""Slip-driven configuration recommendations.

When a placement slip has been parsed into categories, this surface asks the AI
provider what employee attributes and product-catalog entries the client needs
to evaluate those eligibility categories — flagging what's missing from the
current schema/catalog. When a roster is also uploaded, it additionally proposes
derivation rules (validated against real sample values) to auto-fill the
attributes.

Two endpoints, both policy-year scoped (tenant check via `load_policy_year`):
- POST /policy-years/{id}/recommend-config — recompute recommendations (AI).
- POST /policy-years/{id}/apply-config — create the reviewer-approved
  attributes + products, re-link orphaned categories, optionally re-match.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from app.models import (
    Category,
    Employee,
    EmployeeAttributeSchema,
    PlacementSlipRow,
    PolicyYear,
    Product,
)
from app.schemas.api import (
    ApplyAttributeItem,
    ApplyConfigRequest,
    ApplyConfigResult,
    ApplyProductItem,
    AttributeRecommendation,
    AttributeSchemaOut,
    ConfigRecommendationOut,
    DerivationSample,
    ProductRecommendation,
)
from app.services import product_registry
from app.services.ai_breaker import CircuitOpenError
from app.services.ai_extractor import (
    RECOMMEND_DATA_TYPES,
    AINotConfiguredError,
    AIParseError,
)
from app.services.ai_gateway import (
    AIBudgetExceededError,
    propose_derivation_for_roster,
    recommend_schema_for_slip,
)
from app.services.derivation_engine import apply_rule, resolve_attribute_schemas
from app.services.insurance_lines import infer_line
from app.services.matching_engine import match_policy_year
from app.services.roster_profiler import profile_roster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/policy-years", tags=["recommendations"])

_DERIVATION_OPS = ("regex_extract", "regex_case", "passthrough")
_MAX_PATTERN_LEN = 200
_VALID_PARTICIPATION = ("standard", "extended", "eo_only")
# Sample category descriptions surfaced per product candidate (prompt + UI).
_MAX_SAMPLE_CATEGORIES = 6


# ── Shared helpers ────────────────────────────────────────────────────────────


def _norm_code(code: str) -> str:
    return code.upper().replace(" ", "").replace("-", "")


def _canon_code(code: str) -> str:
    # Alias codes (WICI → WICA) resolve via the product registry.
    return product_registry.resolve_code(code)


def _sheet_from_source_ref(ref: str | None) -> str | None:
    """Pull the sheet name out of `placement_slip://{slip}/{sheet}/row_{n}`.

    Stripped to match `_detected_products`, which strips the sheet from
    `parse_log` — otherwise a padded sheet name fails the re-link lookup.
    """
    if not ref or not ref.startswith("placement_slip://"):
        return None
    parts = ref[len("placement_slip://") :].split("/")
    if len(parts) < 2:
        return None
    sheet = parts[1].strip()
    return sheet or None


def _rule_patterns(rule: dict[str, Any]) -> list[str]:
    """All regex pattern strings a rule will compile (for length/validity guards)."""
    pats: list[str] = []
    if isinstance(rule.get("pattern"), str):
        pats.append(rule["pattern"])
    for case in rule.get("cases") or []:
        if isinstance(case, dict) and isinstance(case.get("pattern"), str):
            pats.append(case["pattern"])
    return pats


def _validate_proposal(
    rule: dict | None, source: str | None, samples: list[str]
) -> tuple[bool, int, list[DerivationSample], str | None]:
    """Run a proposed derivation rule against the source column's sample values.

    A rule is only `valid` if it compiles AND produces a value for at least one
    sample — so a pattern that silently matches nothing can't slip through review.
    """
    if not isinstance(rule, dict) or rule.get("op") not in _DERIVATION_OPS:
        return False, 0, [], "unsupported or missing derivation op"
    if not source:
        return False, 0, [], "no source column"
    for pat in _rule_patterns(rule):
        if len(pat) > _MAX_PATTERN_LEN:
            return False, 0, [], "proposed pattern is implausibly long — rejected"

    results: list[DerivationSample] = []
    match_count = 0
    try:
        for s in samples:
            value = apply_rule(rule, {source: s})
            results.append(DerivationSample(input=s, output=value))
            if value is not None:
                match_count += 1
    except re.error as exc:
        return False, 0, [], f"invalid regex: {exc}"
    except Exception as exc:
        return False, 0, [], f"rule failed on sample: {exc}"

    warning = None if match_count else "rule produced no value for any sampled row"
    return match_count > 0, match_count, results, warning


def _detected_products(
    db: Session, policy_year_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Read parsed `products_detected` across the policy year's placement slips.

    Returns (by_canonical_code, sheet_to_code). `by_canonical_code` maps a
    canonical product code to {code, sheet, count}; `sheet_to_code` maps a sheet
    name to the raw product code (for re-linking categories).
    """
    by_code: dict[str, dict[str, Any]] = {}
    sheet_to_code: dict[str, str] = {}
    slips = (
        db.execute(
            select(PlacementSlipRow).where(
                PlacementSlipRow.policy_year_id == policy_year_id
            )
        )
        .scalars()
        .all()
    )
    for slip in slips:
        detected = (slip.parse_log or {}).get("products_detected") or []
        for entry in detected:
            code = str(entry.get("code") or "").strip()
            sheet = str(entry.get("sheet") or "").strip()
            if not code:
                continue
            canon = _canon_code(code)
            count = int(entry.get("categories") or 0)
            if canon in by_code:
                by_code[canon]["count"] += count
                if sheet and sheet not in by_code[canon]["sheets"]:
                    by_code[canon]["sheets"].append(sheet)
            else:
                by_code[canon] = {
                    "code": code, "sheets": [sheet] if sheet else [], "count": count
                }
            if sheet:
                sheet_to_code.setdefault(sheet, code)
    return by_code, sheet_to_code


def _samples_for_sheets(
    sheet_samples: dict[str, list[str]], sheets: list[str]
) -> list[str]:
    """Gather sample category descriptions across every sheet that maps to a
    product code (so an aliased/duplicate sheet doesn't lose its samples)."""
    out: list[str] = []
    for sh in sheets:
        for s in sheet_samples.get(sh, []):
            if s not in out and len(out) < _MAX_SAMPLE_CATEGORIES:
                out.append(s)
    return out


def _sample_categories_by_sheet(categories: list[Category]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in categories:
        sheet = _sheet_from_source_ref(c.source_ref)
        if not sheet:
            continue
        bucket = out.setdefault(sheet, [])
        if c.raw_description not in bucket and len(bucket) < _MAX_SAMPLE_CATEGORIES:
            bucket.append(c.raw_description)
    return out


def _distinct_descriptions(categories: list[Category]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in categories:
        d = (c.raw_description or "").strip()
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


# ── Recommend ─────────────────────────────────────────────────────────────────


def _attach_derivations(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str,
    attr_recs: list[AttributeRecommendation],
    employee_rows: list[dict[str, Any]],
) -> None:
    """Sample the roster and attach a validated derivation rule to each recommendation.

    Best-effort enrichment: if the AI provider is unavailable, attributes are
    returned without derivation rules rather than failing the whole request.
    """
    profile = profile_roster(employee_rows)
    columns_payload = [
        {"key": c.key, "samples": list(c.samples), "distinct_count": c.distinct_count,
         "total": c.total}
        for c in profile.columns
    ]
    samples_by_key = {c.key: list(c.samples) for c in profile.columns}
    targets = [
        AttributeSchemaOut(
            id="", client_id=None, attribute_id=a.attribute_id,
            display_name=a.display_name, data_type=a.data_type,
            enum_values=a.enum_values, is_required=False, is_pii=a.is_pii,
            description=a.description,
        )
        for a in attr_recs
    ]
    try:
        result = propose_derivation_for_roster(
            db, client_id=client_id, policy_year_id=policy_year_id,
            columns=columns_payload, targets=targets,
        )
    except (AINotConfiguredError, AIBudgetExceededError, CircuitOpenError, AIParseError):
        logger.warning("derivation proposal skipped during recommend-config", exc_info=True)
        return

    by_attr = {p.get("attribute_id"): p for p in result.proposals}
    for a in attr_recs:
        prop = by_attr.get(a.attribute_id)
        if not prop or not prop.get("mappable"):
            continue
        source = prop.get("source")
        rule = prop.get("derivation_rule")
        if not source or not rule:
            continue
        samples = samples_by_key.get(source, [])
        valid, match_count, sample_results, warning = _validate_proposal(rule, source, samples)
        a.derived_from = source
        a.derivation_rule = rule
        a.valid = valid
        a.match_count = match_count
        a.sample_size = len(samples)
        a.samples = sample_results[:8]
        a.warning = warning


@router.post("/{policy_year_id}/recommend-config", response_model=ConfigRecommendationOut)
@limiter.limit("20/minute")
def recommend_config(
    request: Request,
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConfigRecommendationOut:
    """Recompute attribute + product recommendations from the slip's categories.

    Persists nothing except the AI spend-log row — recommendations are
    recomputable and must be reviewed before being applied via `apply-config`.
    """
    client_id = require_client_id(user)
    categories = list(
        db.execute(
            select(Category).where(Category.policy_year_id == policy_year_id)
        ).scalars()
    )
    if not categories:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No categories for this policy year — upload a placement slip first.",
        )

    descriptions = _distinct_descriptions(categories)

    schemas = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    existing_attr_ids = {s.attribute_id for s in schemas}
    pii_attr_ids = {s.attribute_id for s in schemas if s.is_pii}
    existing_attrs_out = [AttributeSchemaOut.model_validate(s) for s in schemas]

    products = list(
        db.execute(
            select(Product).where(tenant_or_global(Product.client_id, client_id))
        ).scalars()
    )
    existing_codes = [p.code for p in products]
    existing_canon = {_canon_code(p.code) for p in products}

    detected, _ = _detected_products(db, policy_year_id)
    sheet_samples = _sample_categories_by_sheet(categories)
    product_candidates = [
        {"code": info["code"],
         "sample_categories": _samples_for_sheets(sheet_samples, info["sheets"])}
        for canon, info in detected.items()
        if canon not in existing_canon
    ]

    try:
        rec = recommend_schema_for_slip(
            db, client_id=client_id, policy_year_id=policy_year_id,
            category_descriptions=descriptions, product_candidates=product_candidates,
            existing_attributes=existing_attrs_out, existing_product_codes=existing_codes,
        )
    except AINotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except AIBudgetExceededError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except CircuitOpenError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI provider temporarily unavailable (circuit open). Try again shortly.",
        ) from exc
    except AIParseError as exc:
        logger.exception("Config recommendation parse failure")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "AI returned an unparseable recommendation."
        ) from exc

    db.commit()  # persist the AI spend-log row recorded by the gateway

    attr_recs = _build_attribute_recs(rec.attributes, existing_attr_ids, pii_attr_ids)
    prod_recs = _build_product_recs(rec.products, detected, existing_canon)

    employee_rows = [
        e.attribute_values or {}
        for e in db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
            )
        ).scalars()
    ]
    roster_present = len(employee_rows) > 0
    if roster_present and attr_recs:
        _attach_derivations(
            db, client_id=client_id, policy_year_id=policy_year_id,
            attr_recs=attr_recs, employee_rows=employee_rows,
        )
        db.commit()  # persist the second AI spend-log row

    return ConfigRecommendationOut(
        policy_year_id=policy_year_id,
        roster_present=roster_present,
        employee_count=len(employee_rows),
        category_count=len(categories),
        attributes=attr_recs,
        products=prod_recs,
        model=rec.metadata.get("model"),
        cache_hit=rec.cache_hit,
    )


def _build_attribute_recs(
    raw: list[dict[str, Any]],
    existing_attr_ids: set[str],
    pii_attr_ids: set[str],
) -> list[AttributeRecommendation]:
    out: list[AttributeRecommendation] = []
    seen: set[str] = set()
    for a in raw:
        aid = (a.get("attribute_id") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        dtype = (a.get("data_type") or "string").strip().lower()
        if dtype not in RECOMMEND_DATA_TYPES:
            dtype = "string"
        enum_values = a.get("enum_values") if isinstance(a.get("enum_values"), list) else None
        # Trust the model's PII flag, but never drop PII protection an existing
        # schema row already asserts for this attribute.
        is_pii = bool(a.get("is_pii")) or aid in pii_attr_ids
        out.append(
            AttributeRecommendation(
                attribute_id=aid,
                display_name=str(a.get("display_name") or aid),
                data_type=dtype,
                enum_values=enum_values,
                is_pii=is_pii,
                description=a.get("description"),
                reasoning=str(a.get("reasoning") or ""),
                already_exists=aid in existing_attr_ids,
            )
        )
    return out


def _build_product_recs(
    raw: list[dict[str, Any]],
    detected: dict[str, dict[str, Any]],
    existing_canon: set[str],
) -> list[ProductRecommendation]:
    count_by_canon = {canon: info["count"] for canon, info in detected.items()}
    out: list[ProductRecommendation] = []
    seen: set[str] = set()
    for p in raw:
        code = (p.get("code") or "").strip()
        if not code:
            continue
        canon = _canon_code(code)
        if canon in seen:
            continue
        seen.add(canon)
        participation = str(p.get("participation_model") or "standard")
        if participation not in _VALID_PARTICIPATION:
            participation = "standard"
        out.append(
            ProductRecommendation(
                code=code,
                display_name=str(p.get("display_name") or code),
                insurer=p.get("insurer"),
                participation_model=participation,  # type: ignore[arg-type]
                has_dependants=bool(p.get("has_dependants")),
                is_outpatient=bool(p.get("is_outpatient")),
                reasoning=str(p.get("reasoning") or ""),
                already_exists=canon in existing_canon,
                category_count=count_by_canon.get(canon, 0),
            )
        )
    return out


# ── Apply ─────────────────────────────────────────────────────────────────────


def _validate_attribute(item: ApplyAttributeItem) -> None:
    if item.data_type not in RECOMMEND_DATA_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unsupported data_type {item.data_type!r} for {item.attribute_id!r}.",
        )
    if item.data_type == "enum" and not item.enum_values:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"enum attribute {item.attribute_id!r} needs at least one enum value.",
        )
    if item.derivation_rule is not None:
        rule = item.derivation_rule
        op = rule.get("op") if isinstance(rule, dict) else None
        if op not in _DERIVATION_OPS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Invalid derivation rule for {item.attribute_id!r}.",
            )
        # A rule with a recognised op but no usable source/pattern compiles
        # nothing and silently derives None for every employee — reject it so a
        # dead rule can't masquerade as a configured attribute.
        if not rule.get("source"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Derivation rule for {item.attribute_id!r} needs a 'source' column.",
            )
        if op == "regex_extract" and not rule.get("pattern"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"regex_extract rule for {item.attribute_id!r} needs a 'pattern'.",
            )
        if op == "regex_case" and not (
            isinstance(rule.get("cases"), list) and rule["cases"]
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"regex_case rule for {item.attribute_id!r} needs at least one case.",
            )
        for pat in _rule_patterns(item.derivation_rule):
            if len(pat) > _MAX_PATTERN_LEN:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Derivation pattern for {item.attribute_id!r} is too long.",
                )
            try:
                re.compile(pat)
            except re.error as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Invalid regex for {item.attribute_id!r}: {exc}",
                ) from exc


def _relink_categories(
    db: Session, policy_year_id: str, created_products: list[Product], user: CurrentUser
) -> int:
    """Link orphaned (product_id=None) categories to newly created products.

    Matches each category's detected sheet/code against the new product code.
    """
    if not created_products:
        return 0
    _, sheet_to_code = _detected_products(db, policy_year_id)
    by_canon = {_canon_code(p.code): p for p in created_products}
    orphans = list(
        db.execute(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id.is_(None),
            )
        ).scalars()
    )
    relinked_per_product: dict[str, int] = {}
    for cat in orphans:
        sheet = _sheet_from_source_ref(cat.source_ref)
        code = sheet_to_code.get(sheet) if sheet else None
        if not code:
            continue
        product = by_canon.get(_canon_code(code))
        if product is None:
            continue
        cat.product_id = product.id
        relinked_per_product[product.id] = relinked_per_product.get(product.id, 0) + 1

    total = 0
    for product in created_products:
        n = relinked_per_product.get(product.id, 0)
        if n:
            write_audit(
                db, user, action="relink_categories", entity_type="product",
                entity_id=product.id,
                after={"categories_relinked": n, "policy_year_id": policy_year_id},
            )
            total += n
    return total


def _create_products(
    db: Session, user: CurrentUser, client_id: str, items: list[ApplyProductItem]
) -> tuple[list[Product], list[str]]:
    """Create client-scoped products for codes not already in the catalog."""
    existing_canon = {
        _canon_code(p.code)
        for p in db.execute(
            select(Product).where(Product.client_id == client_id)
        ).scalars()
    }
    created: list[Product] = []
    codes: list[str] = []
    for item in items:
        canon = _canon_code(item.code)
        if canon in existing_canon:
            continue  # idempotent — already in the catalog
        row = Product(
            client_id=client_id, code=item.code, display_name=item.display_name,
            insurer=item.insurer, participation_model=item.participation_model,
            has_dependants=item.has_dependants, is_outpatient=item.is_outpatient,
            product_metadata={"line": infer_line(item.code)},
        )
        db.add(row)
        db.flush()
        write_audit(
            db, user, action="create", entity_type="product", entity_id=row.id,
            after={"code": item.code, "display_name": item.display_name,
                   "source": "ai_recommendation"},
        )
        created.append(row)
        codes.append(item.code)
        existing_canon.add(canon)
    return created, codes


def _upsert_attributes(
    db: Session, user: CurrentUser, client_id: str, items: list[ApplyAttributeItem]
) -> tuple[list[str], list[str]]:
    """Upsert client-scoped attribute rows (overriding a global default copies
    its metadata). PII is sticky — never cleared by this surface."""
    existing_rows = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    client_by_attr = {r.attribute_id: r for r in existing_rows if r.client_id == client_id}
    global_by_attr = {r.attribute_id: r for r in existing_rows if r.client_id is None}
    created: list[str] = []
    updated: list[str] = []
    for item in items:
        row = client_by_attr.get(item.attribute_id)
        if row is not None:
            before = {
                "display_name": row.display_name, "data_type": row.data_type,
                "enum_values": row.enum_values, "derived_from": row.derived_from,
                "derivation_rule": row.derivation_rule,
            }
            row.display_name = item.display_name
            row.data_type = item.data_type
            row.enum_values = item.enum_values
            row.is_pii = item.is_pii or row.is_pii
            if item.description is not None:
                row.description = item.description
            row.derived_from = item.derived_from
            row.derivation_rule = item.derivation_rule
            write_audit(
                db, user, action="update", entity_type="employee_attribute_schema",
                entity_id=row.id, before=before,
                after={"attribute_id": item.attribute_id, "source": "ai_recommendation"},
            )
            updated.append(item.attribute_id)
        else:
            template = global_by_attr.get(item.attribute_id)
            row = EmployeeAttributeSchema(
                client_id=client_id, attribute_id=item.attribute_id,
                display_name=item.display_name, data_type=item.data_type,
                enum_values=item.enum_values,
                is_required=template.is_required if template else False,
                is_pii=item.is_pii or (template.is_pii if template else False),
                description=item.description if item.description is not None
                else (template.description if template else None),
                derived_from=item.derived_from, derivation_rule=item.derivation_rule,
            )
            db.add(row)
            db.flush()
            write_audit(
                db, user, action="create", entity_type="employee_attribute_schema",
                entity_id=row.id,
                after={"attribute_id": item.attribute_id, "source": "ai_recommendation",
                       "override_of_global": template is not None},
            )
            created.append(item.attribute_id)
            # In-request dedup: a second item with the same attribute_id now
            # updates this row instead of inserting a duplicate.
            client_by_attr[item.attribute_id] = row
    return created, updated


@router.post("/{policy_year_id}/apply-config", response_model=ApplyConfigResult)
@limiter.limit("20/minute")
def apply_config(
    request: Request,
    policy_year_id: str,
    payload: ApplyConfigRequest,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplyConfigResult:
    """Create the reviewer-approved attributes + products and optionally re-match.

    Attributes are upserted as client-scoped rows (copying a global default's
    metadata when overriding), products are created client-scoped, and orphaned
    categories are re-linked to newly created products.
    """
    assert_policy_year_editable(py)
    client_id = require_client_id(user)
    if not payload.attributes and not payload.products:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to apply.")

    for item in payload.attributes:
        _validate_attribute(item)

    try:
        created_products, products_created = _create_products(
            db, user, client_id, payload.products
        )
        attributes_created, attributes_updated = _upsert_attributes(
            db, user, client_id, payload.attributes
        )
        categories_relinked = _relink_categories(
            db, policy_year_id, created_products, user
        )
        db.commit()
    except IntegrityError:
        # Unique (client_id, attribute_id) / (client_id, code) violation from a
        # concurrent apply — nothing was persisted; the caller can safely retry.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another request modified this client's schema concurrently — please retry.",
        ) from None

    rematched = False
    employees_matched: int | None = None
    if payload.rerun_matching:
        summary = match_policy_year(db, policy_year_id, user)
        write_audit(
            db, user, action="run_matching", entity_type="policy_year",
            entity_id=policy_year_id,
            after={"employees_matched": summary.employees_matched,
                   "trigger": "apply_config"},
        )
        db.commit()
        rematched = True
        employees_matched = summary.employees_matched

    return ApplyConfigResult(
        attributes_created=attributes_created,
        attributes_updated=attributes_updated,
        products_created=products_created,
        categories_relinked=categories_relinked,
        rematched=rematched,
        employees_matched=employees_matched,
    )
