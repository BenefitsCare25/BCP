"""Confirm-time materialization for the guided product setup.

Projects a setup draft's answers into the catalog `Product` plus per-plan `Plan`
rows (`source="manual"`) and, on a product's first materialization, seed
eligibility `Category` rows — the same shape the placement-slip parser emits.
Also adopts same-code slip artifacts onto the setup product and applies the
reviewed policy-number assignments. Driven by `api/v1/product_setups.confirm_setup`.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.deps import tenant_or_global
from app.models import (
    Category,
    DependantCoverDecision,
    EmployeePlanOverride,
    EnrollmentElection,
    FlexPricing,
    ListingAssignment,
    Plan,
    Product,
    ProductTerm,
    UnderwritingCase,
)
from app.models.category import CategoryStatus, SourceKind
from app.services.category_factory import build_manual_category
from app.services.claim_limits import (
    enforceable_policy_year_amount,
    normalize_limit_setting,
    product_setting,
    setting_display,
)
from app.services.dependant_coverage import (
    per_head_dependant_rate,
    slip_participation_detail,
)
from app.services.insurance_lines import infer_line
from app.services.matching_engine import insured_names
from app.services.placement_slip_parser import (
    normalize_participation,
    parse_participation,
)
from app.services.product_templates import ProductTemplate
from app.services.sob_columns import resolve_plan_schedule

_MANUAL = "manual"
_SETUP_REF = "product_setup"
_MAX_BENEFIT_ITEMS = 200


def _sync_term_policy_number(
    db: Session, product: Product, policy_year_id: str, answers: dict[str, Any], user: CurrentUser
) -> None:
    """Apply reviewed assignments atomically with this product's setup."""
    from app.services.policy_numbers import (
        require_assignment_removal_permission,
        scalar_number,
        source_numbers,
        validate_assignments,
    )
    from app.services.product_terms import _term_for_update

    source = str((answers.get("header") or {}).get("policy_no") or "").strip()
    if "policy_number_mappings" in answers:
        try:
            mappings = validate_assignments(answers["policy_number_mappings"])
            if not mappings and len(source_numbers(source)) > 1:
                raise ValueError(
                    "Assign the policy number for this product and its covered entities "
                    "before confirming."
                )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        term = _term_for_update(db, policy_year_id, product.id)
        existing_mappings = term.policy_number_mappings
        if existing_mappings is None and source_numbers(term.policy_number):
            existing_mappings = [{"entity": None, "policy_number": term.policy_number}]
        require_assignment_removal_permission(existing_mappings, mappings, user.role)
        term.policy_number_mappings = mappings
        term.policy_number = scalar_number(mappings)
        answers["policy_number_mappings"] = mappings
        return
    if not source:
        return
    if len(source_numbers(source)) > 1 or len(source) > 64:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Review the source policy numbers and assign one number per product/entity "
            "before confirming.",
        )
    term = _term_for_update(db, policy_year_id, product.id)
    if term.policy_number_mappings is None:
        if source_numbers(term.policy_number) and not source_numbers(source):
            require_assignment_removal_permission([term.policy_number], [], user.role)
        term.policy_number = source


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
            raw_claim_limit = it.get("claim_limit")
            normalized_claim_limit = normalize_limit_setting(
                raw_claim_limit, fallback_display=it.get("value")
            )
            if normalized_claim_limit is not None and _clean_value(it.get("value")):
                # The editable SoB cell is the member-facing wording. A detected
                # snapshot must not stay stale after the broker corrects it.
                normalized_claim_limit["display"] = _clean_value(it.get("value"))
            projected = {
                "uid": str(it.get("uid") or ""),
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
            }
            if raw_claim_limit is not None:
                projected["claim_limit"] = normalized_claim_limit or raw_claim_limit
            if isinstance(it.get("member_hidden"), bool):
                projected["member_hidden"] = it["member_hidden"]
            items.append(projected)
    schedule: dict[str, Any] = {"items": items}
    if isinstance(sob, dict):
        raw_plan_limits = sob.get("plan_claim_limits")
        raw_plan_limit = (
            raw_plan_limits.get(str(plan.get("code") or "").strip())
            if isinstance(raw_plan_limits, dict)
            else None
        )
        if raw_plan_limit is not None:
            schedule["claim_limit"] = (
                normalize_limit_setting(raw_plan_limit) or raw_plan_limit
            )
    return schedule


_TERM_FIELDS = (
    "coverage_start",
    "coverage_end",
    "gst_included",
    "gst_rate",
    "free_cover_limit",
    "nel_age_limit",
    "underwriting_required",
    "policy_number",
    "policy_number_mappings",
    "pre_hosp_days",
    "post_hosp_days",
)


def _adoption_conflict(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"code": "duplicate_product_configuration", "message": message},
    )


# Member records of one policy year, keyed by product: they belong with the
# categories, so they move with them. Left on the catalog product, the
# company's listing, elections and underwriting stop applying to its plans.
_MemberRecord = (
    ListingAssignment
    | EmployeePlanOverride
    | EnrollmentElection
    | UnderwritingCase
    | DependantCoverDecision
)
_MEMBER_RECORDS: tuple[type[_MemberRecord], ...] = (
    ListingAssignment,
    EmployeePlanOverride,
    EnrollmentElection,
    UnderwritingCase,
    DependantCoverDecision,
)


def _adopt_member_records(
    db: Session, product: Product, policy_year_id: str, related_ids: list[str]
) -> dict[str, int]:
    """Re-parent the year's member records from same-code products; fail
    closed when both records hold some, as for categories."""
    moved: dict[str, int] = {}
    if not related_ids:
        return moved
    for model in _MEMBER_RECORDS:
        stranded = (
            model.policy_year_id == policy_year_id,
            model.product_id.in_(related_ids),
        )
        count = db.scalar(select(func.count()).select_from(model).where(*stranded)) or 0
        if not count:
            continue
        held = db.scalar(
            select(func.count()).select_from(model).where(
                model.policy_year_id == policy_year_id, model.product_id == product.id
            )
        )
        if held:
            raise _adoption_conflict(
                f"{product.code} has {model.__tablename__.replace('_', ' ')} on two "
                "product records. Reconcile the duplicate records before confirming."
            )
        db.execute(update(model).where(*stranded).values(product_id=product.id))
        moved[model.__tablename__] = count
    return moved


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
    members_moved = _adopt_member_records(db, product, policy_year_id, related_ids)

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
    if source_categories or source_terms or pricing_moved or plans_removed or members_moved:
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
                "member_records_reparented": members_moved,
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
        configured_overall = product_setting(schedule)
        configured_annual_display = (
            setting_display(configured_overall)
            if enforceable_policy_year_amount(configured_overall) is not None
            else None
        )
        label = str(plan.get("label") or f"Plan {code}")[:255]
        row = existing.get(code)
        if row is None:
            row = Plan(
                product_id=product.id,
                policy_year_id=policy_year_id,
                code=code,
                display_name=label,
                benefit_schedule=schedule,
                annual_policy_limit=configured_annual_display,
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
            # Presence of an explicit root setting is authoritative, including
            # "not a limit" and non-annual informational bases. Without one,
            # preserve a legacy parser-populated annual limit during rollout.
            if "claim_limit" in schedule:
                row.annual_policy_limit = configured_annual_display
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
    # The basis model decides, not the row: an untiered draft can still carry the
    # starter scaffold's zero-filled EO/ES/EC/EF dict beside its synced headcount.
    tier_counts = (
        {str(k): _coerce_count(v) for k, v in (row.get("tiers") or {}).items()}
        if basis_model == "tiered"
        else {}
    )
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
    for field in ("location_scope", "source_insured"):
        if row.get(field):
            pa[field] = str(row[field]).strip()
    if isinstance(row.get("location_breakdown"), list) and row["location_breakdown"]:
        pa["location_breakdown"] = [
            dict(item) for item in row["location_breakdown"] if isinstance(item, dict)
        ]
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
        assignments = _category_plan_assignments(row, rate_table, rate_model, basis_model)
        detail = slip_participation_detail(
            pspec.to_dict(),
            has_dependants=bool(product.has_dependants),
            plan_assignments=assignments,
            display_name=name,
            raw_description=name,
        )
        assignments = per_head_dependant_rate(assignments, detail.get("dependant"))
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
            participation_detail=detail,
            plan_assignments=assignments,
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
