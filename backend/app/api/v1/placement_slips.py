"""Placement slip ingest — parses workbook and persists categories.

Replaces the spike's ephemeral parse endpoint. Each parse:
1. Creates a `placement_slips` row (audit trail).
2. Parses the workbook via the existing services.
3. For each ExtractedCategory, runs `description_to_rule()` then inserts a
   `categories` row with the full provenance envelope.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.product_setups import seed_draft_from_slip
from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_editable,
    assert_policy_year_for_user,
    load_placement_slip,
    require_client_id,
    tenant_or_global,
)
from app.core.rate_limit import limiter
from app.core.uploads import WORKBOOK_SUFFIXES, saved_upload
from app.db.session import get_db
from app.models import (
    Category,
    Employee,
    PlacementSlipRow,
    Plan,
    PolicyYear,
    Product,
    ProductSetup,
)
from app.models.category import CategoryStatus, SourceKind
from app.models.placement_slip import ParseStatus
from app.models.product_setup import ProductSetupStatus
from app.schemas.api import (
    ParseResult,
    ProductDiagnostic,
    SlipTemplateProfileOut,
    SlipTemplateProfileSave,
)
from app.services import product_registry
from app.services.ai_slip_extractor import maybe_ai_augment
from app.services.dynamic_template import merge_file_overlay, synthesize_template
from app.services.matching_engine import match_policy_year
from app.services.period_parser import parse_period_of_insurance
from app.services.placement_slip_parser import (
    ProductSlip,
    normalize_participation,
    parse_participation,
    parse_placement_slip,
)
from app.services.plan_assignments import build_plan_assignments
from app.services.product_templates import get_template
from app.services.product_terms import autofill_nel_terms
from app.services.rule_generator import description_to_rule
from app.services.slip_reconcile import reconcile_slip
from app.services.slip_template_memory import make_resolver, save_profile
from app.services.slip_to_setup import build_setup_answers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/placement-slips", tags=["placement-slips"])


def _find_product(
    db: Session, code: str, sheet_hint: str, products_cache: dict[str, Product]
) -> Product | None:
    """Match parsed product_code to a Product row.

    Tries direct code match first (so `GP` doesn't accidentally match `GPA`
    via substring), then falls back to substring matching for compound codes
    like `GHS-LOCALS`. Alias codes (WICI → WICA) resolve via the registry.
    """
    def _norm(s: str) -> str:
        return s.upper().replace(" ", "").replace("-", "")

    code_n = product_registry.resolve_code(code)
    sheet_n = product_registry.resolve_code(sheet_hint)

    for p in products_cache.values():
        pcode = _norm(p.code)
        if pcode == code_n or pcode == sheet_n:
            return p
    for p in products_cache.values():
        pcode = _norm(p.code)
        if pcode and (pcode in code_n or pcode in sheet_n):
            return p
    return None


def _prefill_setup_drafts(
    db: Session,
    policy_year_id: str,
    slip_id: str,
    slip: ProductSlip,
    products_cache: dict[str, Product],
) -> list[str]:
    """Pre-fill a guided-setup draft for every detected product so its form
    opens populated from the slip.

    Structure comes from the slip itself (synthesized from the product's
    just-written Plan/Category rows, so the form shows the client's real plans and
    benefit lines — e.g. all 6 GHS plans, not the canned template's 4), with the
    hand-authored template overlaid for presentation (basis/rate models, benefit
    kinds, arrangements). When the slip yielded no structure, the file template is
    the fallback. Create-only (see ``seed_draft_from_slip``): an existing draft or
    confirmed setup is never overwritten. Returns the product codes freshly
    pre-filled.
    """
    prefilled: list[str] = []
    # Several sheets can map to one product code (e.g. VDL splits GHS into
    # Locals/Secondees/Dependants). Dedupe in-memory: the create-only DB check in
    # seed_draft_from_slip can't see this transaction's own un-committed inserts,
    # so two sheets for the same code would both insert and trip the
    # (policy_year_id, product_code) unique constraint. The synthesized structure
    # already unions every sheet's persisted Plan/Category rows for the product,
    # so the first sheet's draft covers them all.
    seen: set[str] = set()
    for product_slip in slip.products:
        product = _find_product(
            db, product_slip.product_code, product_slip.sheet, products_cache
        )
        if product is None:
            # No catalog product to attach to — can't synthesize structure.
            continue
        code = product.code.upper()
        if code in seen:
            logger.info(
                "Setup draft for %s already seeded this upload — sheet %s not "
                "merged (slip_id=%s)",
                code, product_slip.sheet, slip_id,
            )
            continue
        seen.add(code)
        synth = synthesize_template(db, policy_year_id, product)
        file_tpl = get_template(product.code)
        tpl = merge_file_overlay(synth, file_tpl) if synth is not None else file_tpl
        if tpl is None:
            continue
        answers = build_setup_answers(product_slip, tpl)
        if seed_draft_from_slip(
            db, policy_year_id, slip_id, tpl.code, answers, tpl.version
        ):
            prefilled.append(tpl.code)
    return prefilled


def _confirmed_setup_codes(db: Session, policy_year_id: str) -> set[str]:
    """Template codes whose guided setup is already confirmed for this year.

    The confirmed setup is the authoritative source for that product, so the
    upload must not re-write provisional ``system_generated`` Category/Plan rows
    for it — doing so would duplicate the confirmed manual rows.
    """
    return {
        code
        for code in db.execute(
            select(ProductSetup.product_code).where(
                ProductSetup.policy_year_id == policy_year_id,
                ProductSetup.status == ProductSetupStatus.confirmed,
            )
        ).scalars()
    }


# Slip-side plan_assignments construction lives in the shared service module
# (the confirm path builds the same shape in product_setups).
_build_plan_assignments = build_plan_assignments


@router.post("/parse", response_model=ParseResult)
@limiter.limit("20/minute")
async def parse_upload(
    request: Request,
    file: Annotated[UploadFile, File(description="Placement slip workbook")],
    policy_year_id: Annotated[str, Form(description="Target policy year")],
    acknowledge_period_mismatch: Annotated[
        bool, Form(description="Proceed even if the slip period differs from the year")
    ] = False,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ParseResult:
    client_id = require_client_id(user)
    policy_year = assert_policy_year_editable(
        assert_policy_year_for_user(policy_year_id, user, db)
    )

    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        slip_row = PlacementSlipRow(
            policy_year_id=policy_year_id,
            uploaded_by=user.user_id,
            filename=file.filename or "untitled",
            parse_status=ParseStatus.parsing,
        )
        db.add(slip_row)
        db.flush()

        # Classification resolver: a broker's stored product_metadata (form
        # profile / layout family, set via PATCH /schemas/products after a
        # needs_classification diagnostic) applies to the matching sheet on
        # this and every future upload.
        classification_by_code: dict[str, dict[str, Any]] = {
            product_registry.resolve_code(p.code): dict(p.product_metadata)
            for p in db.execute(
                select(Product).where(tenant_or_global(Product.client_id, client_id))
            ).scalars()
            if p.product_metadata
        }

        def _classification(code: str) -> dict[str, Any] | None:
            return classification_by_code.get(product_registry.resolve_code(code))

        try:
            raw_parsed = parse_placement_slip(
                tmp_path,
                client_label=client_id,
                profile_resolver=make_resolver(db, client_id),
                classification_resolver=_classification,
            )
        except Exception as exc:
            slip_row.parse_status = ParseStatus.error
            # Don't echo raw exception text to the client — it can contain
            # file paths or library internals. Log full detail server-side.
            logger.exception(
                "placement slip parse failed (slip_id=%s file=%s)",
                slip_row.id, file.filename,
            )
            slip_row.parse_log = {"error_type": type(exc).__name__}
            db.commit()
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Parser error — workbook could not be processed. See server logs.",
            ) from exc

        # Reconcile so every category's plan_code resolves to a real plan (fan
        # out descriptive/sum-assured products, split composite plan headers,
        # assign the sole plan to unlinked categories) and capture per-product
        # diagnostics. Then, for products the parser still couldn't read and
        # only while the workbook is on disk, try the AI fallback (no-op without
        # a configured provider).
        reconciled = reconcile_slip(raw_parsed)
        reconciled = maybe_ai_augment(
            db, client_id, policy_year_id, tmp_path, reconciled
        )
    parsed = reconciled.slip
    diagnostics = reconciled.diagnostics

    # Pre-commit guard: a slip's period of insurance covers the whole policy
    # year, so if it doesn't match the target year we stop *before* writing
    # (re-upload replaces this year's unreviewed auto rows) and let the user
    # switch years or acknowledge. Unparseable periods never block.
    if not acknowledge_period_mismatch:
        year_start, year_end = policy_year.start_date, policy_year.end_date
        period_text = next(
            (p.policy_header.period for p in parsed.products if p.policy_header.period),
            None,
        )
        slip_range = parse_period_of_insurance(period_text)
        if slip_range and (slip_range[0] != year_start or slip_range[1] != year_end):
            db.rollback()  # discard the provisional slip_row — nothing persists
            matching_id = (
                db.execute(
                    select(PolicyYear.id).where(
                        PolicyYear.client_id == client_id,
                        PolicyYear.start_date == slip_range[0],
                        PolicyYear.end_date == slip_range[1],
                        PolicyYear.id != policy_year_id,
                    )
                )
                .scalars()
                .first()
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "period_mismatch",
                    "detected_period": period_text,
                    "slip_start": slip_range[0].isoformat(),
                    "slip_end": slip_range[1].isoformat(),
                    "policy_year_start": year_start.isoformat(),
                    "policy_year_end": year_end.isoformat(),
                    "matching_policy_year_id": matching_id,
                },
            )

    products_cache: dict[str, Product] = {
        p.code: p
        for p in db.execute(
            select(Product).where(tenant_or_global(Product.client_id, client_id))
        )
        .scalars()
        .all()
    }

    confirmed_codes = _confirmed_setup_codes(db, policy_year_id)

    # Idempotent re-upload: drop the previous parse's still-unreviewed,
    # auto-generated categories for this year before inserting fresh ones.
    # Without this, re-uploading the same slip stacks a second full copy of
    # every category on top of the first (the bug behind the duplicate rows).
    # Human-confirmed/edited rows (status != needs_review) and manually-created
    # rows (source != system_generated) are preserved, as are categories owned
    # by a confirmed guided setup — those products are skipped on insert too,
    # so deleting them would lose data we don't regenerate. Orphan rows
    # (product_id IS NULL) are still cleared (NOT IN is NULL-blind otherwise).
    confirmed_product_ids = {
        products_cache[c].id for c in confirmed_codes if c in products_cache
    }
    clear_stmt = delete(Category).where(
        Category.policy_year_id == policy_year_id,
        Category.source == SourceKind.system_generated.value,
        Category.status == CategoryStatus.needs_review.value,
    )
    if confirmed_product_ids:
        clear_stmt = clear_stmt.where(
            or_(
                Category.product_id.is_(None),
                Category.product_id.notin_(confirmed_product_ids),
            )
        )
    replaced_categories = db.execute(clear_stmt).rowcount or 0

    # Same idempotency for plans: drop the previous parse's still-unreviewed,
    # auto-generated plans before re-materializing the Schedule of Benefits.
    # Without this, a re-parse that yields different plan codes (e.g. a fixed
    # parser now emitting GMM plans 1/2/3 where a stale empty "B1" was stored)
    # would leave the old plan orphaned instead of replacing it. Human-edited
    # plans and those under a confirmed setup are preserved. No table references
    # plan.id, so replacing these rows is safe.
    plan_clear = delete(Plan).where(
        Plan.policy_year_id == policy_year_id,
        Plan.source == SourceKind.system_generated.value,
        Plan.status == CategoryStatus.needs_review.value,
        Plan.human_modified.is_(False),
    )
    if confirmed_product_ids:
        plan_clear = plan_clear.where(
            Plan.product_id.notin_(confirmed_product_ids)
        )
    replaced_plans = db.execute(plan_clear).rowcount or 0

    high_conf = 0
    total = 0
    priority = 0
    for product_slip in parsed.products:
        product = _find_product(
            db, product_slip.product_code, product_slip.sheet, products_cache
        )
        if product is not None and product.code in confirmed_codes:
            # A confirmed guided setup owns this product — it's authoritative, so
            # skip writing provisional rows that would duplicate the manual ones.
            continue
        for cat in product_slip.categories:
            envelope = description_to_rule(cat.category)
            total += 1
            if envelope.confidence >= 0.85:
                high_conf += 1
            priority += 1
            pspec = parse_participation(cat.participation)
            # Attach the age-banded voluntary rate table only when THIS category's
            # premium is itself voluntary: a voluntary employee tier, or a
            # dependant-only category (Spouse/Child plan). A COMPULSORY-employee
            # category keeps its flat rate even if its dependants are voluntary —
            # otherwise member_financials would age-band the compulsory premium.
            is_voluntary = pspec.employee == "voluntary" or (
                pspec.employee is None and pspec.dependant == "voluntary"
            )
            row = Category(
                policy_year_id=policy_year_id,
                product_id=product.id if product else None,
                priority=priority,
                display_name=cat.category[:512],
                raw_description=cat.category,
                matching_rule=envelope.rule,
                rule_human_readable=envelope.human_readable,
                participation_model=pspec.employee or normalize_participation(cat.participation),
                participation_detail=pspec.to_dict(),
                plan_assignments=_build_plan_assignments(
                    cat,
                    product_slip.voluntary_rates,
                    is_voluntary,
                    tier_labels=product_slip.tier_labels,
                ),
                source=SourceKind.system_generated.value,
                source_ref=f"placement_slip://{slip_row.id}/{product_slip.sheet}/row_{cat.source_row}",
                confidence=envelope.confidence,
                status=CategoryStatus.needs_review.value,
            )
            db.add(row)

    # Persist parsed plans (Schedule of Benefits data).
    plans_created = 0
    plans_dup_skipped = 0
    plans_skipped_products: list[str] = []
    # Plans are unique on (product_id, policy_year_id, code). Several sheets can
    # resolve to the SAME product (e.g. VDL's "GHS - Locals/Secondees/Dependants"
    # all map to GHS) and reuse plan codes (B1/B2/A…), so the same key can recur
    # within one upload. The per-plan existence SELECT can't see un-flushed
    # inserts from earlier in this request, so without this guard the duplicate
    # only surfaces as an IntegrityError at commit (a 500). Track keys seen this
    # request and keep the first; later collisions are reported, not inserted.
    seen_plan_keys: set[tuple[str, str]] = set()
    for product_slip in parsed.products:
        if not product_slip.plans:
            continue
        product = _find_product(
            db, product_slip.product_code, product_slip.sheet, products_cache
        )
        if product is not None and product.code in confirmed_codes:
            continue
        if product is None:
            plans_skipped_products.append(product_slip.product_code)
            logger.warning(
                "Plans skipped — no matching product for code=%s sheet=%s (slip_id=%s)",
                product_slip.product_code, product_slip.sheet, slip_row.id,
            )
            continue
        for extracted_plan in product_slip.plans:
            key = (product.id, extracted_plan.code)
            if key in seen_plan_keys:
                plans_dup_skipped += 1
                logger.warning(
                    "Duplicate plan code %r for product %s across sheets — keeping "
                    "first, skipping sheet=%s (slip_id=%s)",
                    extracted_plan.code, product.code, product_slip.sheet, slip_row.id,
                )
                continue
            seen_plan_keys.add(key)
            existing = db.execute(
                select(Plan).where(
                    Plan.product_id == product.id,
                    Plan.policy_year_id == policy_year_id,
                    Plan.code == extracted_plan.code,
                )
            ).scalar_one_or_none()
            schedule_json = {
                "items": [
                    {
                        "number": item.number,
                        "name": item.name,
                        "value": item.value,
                        "note": item.note,
                        "limits": [
                            {"label": lim.label, "value": lim.value}
                            for lim in item.limits
                        ],
                        "sub_items": [
                            {
                                "key": s.key,
                                "name": s.name,
                                "value": s.value,
                                "note": s.note,
                                "limits": [
                                    {"label": lim.label, "value": lim.value}
                                    for lim in s.limits
                                ],
                            }
                            for s in item.sub_items
                        ],
                        "properties": item.properties,
                    }
                    for item in extracted_plan.items
                ]
            }
            if existing:
                existing.benefit_schedule = schedule_json
                existing.cover_description = extracted_plan.cover_description
                existing.annual_policy_limit = (
                    str(extracted_plan.annual_policy_limit)
                    if extracted_plan.annual_policy_limit else None
                )
                existing.source_ref = (
                    f"placement_slip://{slip_row.id}/{product_slip.sheet}"
                    f"/sob_row_{extracted_plan.source_row}"
                )
            else:
                db.add(Plan(
                    product_id=product.id,
                    policy_year_id=policy_year_id,
                    code=extracted_plan.code,
                    display_name=extracted_plan.display_name,
                    benefit_schedule=schedule_json,
                    cover_description=extracted_plan.cover_description,
                    annual_policy_limit=(
                        str(extracted_plan.annual_policy_limit)
                        if extracted_plan.annual_policy_limit else None
                    ),
                    source=SourceKind.system_generated.value,
                    source_ref=(
                        f"placement_slip://{slip_row.id}/{product_slip.sheet}"
                        f"/sob_row_{extracted_plan.source_row}"
                    ),
                    confidence=0.9,
                    status=CategoryStatus.needs_review.value,
                ))
                plans_created += 1

    # Auto-fill Non-Evidence-Limit terms from each sheet's footer ("Sum insured
    # exceeding S$500,000 … or age 69 (age last birthday) requires
    # underwriting"): FCL dollar amount + no-underwriting age land on the
    # product's term row, blanks only — a broker's manual entry always wins.
    # Applies even to setup-confirmed products (NEL is operational config).
    nel_autofilled = 0
    for product_slip in parsed.products:
        header = product_slip.policy_header
        nel_age_raw = header.age_limit_no_underwriting
        if header.non_evidence_limit is None and not nel_age_raw:
            continue
        product = _find_product(
            db, product_slip.product_code, product_slip.sheet, products_cache
        )
        if product is None:
            continue
        try:
            nel_age = int(nel_age_raw) if nel_age_raw else None
        except ValueError:
            nel_age = None
        if autofill_nel_terms(
            db, policy_year_id, product.id, header.non_evidence_limit, nel_age
        ):
            nel_autofilled += 1

    # Flush so the Category/Plan rows just added are queryable when we synthesize
    # a template from them for products without a hand-authored JSON file.
    db.flush()
    prefilled_setups = _prefill_setup_drafts(
        db, policy_year_id, slip_row.id, parsed, products_cache
    )

    slip_row.parse_status = ParseStatus.parsed
    slip_row.parse_log = {
        "rule_coverage": {
            "total": total,
            "high_confidence": high_conf,
            "needs_review": total - high_conf,
        },
        "replaced_categories": replaced_categories,
        "replaced_plans": replaced_plans,
        "plans_created": plans_created,
        "plans_skipped_no_product": plans_skipped_products,
        "plans_skipped_duplicate": plans_dup_skipped,
        "prefilled_setups": prefilled_setups,
        "nel_terms_autofilled": nel_autofilled,
        "skipped_sheets": parsed.diagnostics.get("skipped_sheets", []),
        "products_detected": [
            {
                "sheet": p.sheet,
                "code": p.product_code,
                "categories": len(p.categories),
                "plans": len(p.plans),
            }
            for p in parsed.products
        ],
        "product_diagnostics": [asdict(d) for d in diagnostics],
    }
    write_audit(
        db,
        user,
        action="upload",
        entity_type="placement_slip",
        entity_id=slip_row.id,
        after={"filename": slip_row.filename, "total_categories": total},
    )

    # A re-parse replaced category rows, which orphans every prior match
    # (matched_category_id FK-nulls; matched_categories snapshots dangle).
    # Re-match in the SAME transaction — mirrors product-setup confirm — so
    # the roster is never left half-parsed/half-matched.
    rematched = False
    employees_matched: int | None = None
    emp_count = db.execute(
        select(func.count(Employee.id)).where(
            Employee.policy_year_id == policy_year_id
        )
    ).scalar_one()
    if emp_count:
        db.flush()
        summary = match_policy_year(db, policy_year_id, user)
        write_audit(
            db, user, action="run_matching", entity_type="policy_year",
            entity_id=policy_year_id,
            after={
                "employees_total": summary.employees_total,
                "employees_matched": summary.employees_matched,
                "errors": summary.errors,
                "trigger": "slip_upload",
            },
        )
        rematched = True
        employees_matched = summary.employees_matched
    db.commit()

    return ParseResult(
        placement_slip_id=slip_row.id,
        policy_year_id=policy_year_id,
        total_categories=total,
        high_confidence=high_conf,
        needs_review=total - high_conf,
        replaced_categories=replaced_categories,
        replaced_plans=replaced_plans,
        skipped_sheets=list(parsed.diagnostics.get("skipped_sheets", [])),
        prefilled_setups=prefilled_setups,
        products=[ProductDiagnostic(**asdict(d)) for d in diagnostics],
        rematched=rematched,
        employees_matched=employees_matched,
    )


@router.get("", response_model=list[dict])
def list_slips(
    policy_year_id: Annotated[str, Query(description="Policy year to list uploads for")],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Prior placement-slip uploads for a policy year (newest first).

    Powers the upload-history panel and the re-upload duplicate warning in the
    UI. Tenant-scoped via the policy year.
    """
    assert_policy_year_for_user(policy_year_id, user, db)
    rows = (
        db.execute(
            select(PlacementSlipRow)
            .where(PlacementSlipRow.policy_year_id == policy_year_id)
            .order_by(PlacementSlipRow.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "parse_status": r.parse_status.value,
            "created_at": r.created_at.isoformat(),
            "total_categories": (r.parse_log or {})
            .get("rule_coverage", {})
            .get("total", 0),
        }
        for r in rows
    ]


@router.get("/{slip_id}", response_model=dict)
def get_slip(slip: PlacementSlipRow = Depends(load_placement_slip)) -> dict[str, Any]:
    return {
        "id": slip.id,
        "policy_year_id": slip.policy_year_id,
        "filename": slip.filename,
        "parse_status": slip.parse_status.value,
        "parse_log": slip.parse_log,
        "created_at": slip.created_at.isoformat(),
    }


@router.put("/template-profiles", response_model=SlipTemplateProfileOut)
def save_template_profile(
    payload: SlipTemplateProfileSave,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SlipTemplateProfileOut:
    """Persist a broker's SOB column-mapping correction for a template.

    Keyed by (active tenant, fingerprint): re-saving the same fingerprint updates
    the stored mapping. The next upload of a sheet with that fingerprint extracts
    the Schedule of Benefits using this mapping instead of the auto profiler.
    Tenant-scoped via the active client; a broker can only write their own
    tenant's overrides.
    """
    client_id = require_client_id(user)
    row = save_profile(
        db,
        client_id=client_id,
        fingerprint=payload.fingerprint,
        product_code=payload.product_code,
        roles=payload.roles,
        insurer=payload.insurer,
        sheet_label=payload.sheet_label,
        created_by=user.user_id,
    )
    write_audit(
        db,
        user,
        action="slip_template_profile.save",
        entity_type="slip_template_profile",
        entity_id=row.id,
    )
    db.commit()
    return SlipTemplateProfileOut(
        id=row.id,
        fingerprint=row.fingerprint,
        product_code=row.product_code,
        insurer=row.insurer,
        sheet_label=row.sheet_label,
        roles=row.roles,
    )
