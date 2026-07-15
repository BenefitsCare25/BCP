"""Per-employee plan overrides — the effective-coverage deviation from cohort.

An override changes one employee's plan (or declines coverage / adjusts covered
dependants) for a single product, deviating from their matched category default.
This router is the manual-admin write path; enrollment confirmation and bulk
updates write the same rows programmatically.

- GET    /employees/{employee_id}/plan-overrides                  — list
- PUT    /employees/{employee_id}/plan-overrides/{product_code}   — upsert
- DELETE /employees/{employee_id}/plan-overrides/{product_code}   — revert to default

Tenant scoping rides on `load_employee`; the target product is proven to belong
to the employee's policy year before any write.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_employee, load_policy_year
from app.core.pagination import MAX_LIMIT
from app.db.session import get_db
from app.models import Employee, EmployeePlanOverride, PolicyYear
from app.models.employee_plan_override import OverrideSource
from app.schemas.enrollment import (
    CoverageHistoryOut,
    CoverageRevertRequest,
    CoverageRevertResult,
    PlanOverrideOut,
    PlanOverrideUpsert,
)
from app.services.bulk_plan_update import baseline_cat_by_product
from app.services.cohort_tiers import tier_key
from app.services.coverage_history import coverage_history
from app.services.coverage_resolver import batch_category_defaults, find_orphan_overrides
from app.services.coverage_revert import (
    latest_enrollment_with_baseline,
    revert_leave,
    revert_to_baseline,
    revert_to_default,
)
from app.services.enrollment_products import available_plan_codes, resolve_product_by_code
from app.services.enrollment_validation import (
    assert_dependants_owned,
    assert_plan_available,
    assert_valid_dependant_options,
)
from app.services.flex_pricing_resolver import (
    compulsory_dependant_category_ids,
    covered_dependant_profiles,
    dependant_age_limits,
    dependant_option_choices,
    employee_age,
    get_pricing,
    governing_flex_config,
    maybe_family_slip_index,
    maybe_slip_index,
    member_coverage_tag,
    profile_counts,
    reference_date,
)
from app.services.override_writer import override_snapshot, upsert_override

router = APIRouter(tags=["plan-overrides"])


@router.get(
    "/policy-years/{policy_year_id}/plan-overrides/orphans",
    response_model=list[PlanOverrideOut],
)
def list_orphan_overrides(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> list[EmployeePlanOverride]:
    """Overrides stranded by a re-match — the elected product is no longer in the
    employee's cohort. They are inert (the resolver skips them) but surfaced here
    so ops can reconcile rather than leave silent ghosts."""
    employees = list(
        db.execute(
            select(Employee).where(Employee.policy_year_id == py.id)
        ).scalars().all()
    )
    return find_orphan_overrides(db, py.id, employees)


@router.get(
    "/employees/{employee_id}/plan-overrides",
    response_model=list[PlanOverrideOut],
)
def list_plan_overrides(
    employee_id: str,
    emp: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> list[EmployeePlanOverride]:
    rows = (
        db.execute(
            select(EmployeePlanOverride)
            .where(EmployeePlanOverride.employee_id == emp.id)
            .order_by(EmployeePlanOverride.product_code)
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.put(
    "/employees/{employee_id}/plan-overrides/{product_code}",
    response_model=PlanOverrideOut,
)
def set_plan_override(
    employee_id: str,
    product_code: str,
    body: PlanOverrideUpsert,
    emp: Employee = Depends(load_employee),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmployeePlanOverride:
    py = db.get(PolicyYear, emp.policy_year_id)
    if py is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy year not found.")
    product = resolve_product_by_code(db, py, product_code)
    if product is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Product '{product_code}' is not configured in this policy year.",
        )

    # Validate the elected plan is a real tier for this product/year.
    if body.plan_code:
        assert_plan_available(
            body.plan_code, available_plan_codes(db, py.id, product.id), product_code
        )
    # Validate that any named dependants belong to this employee.
    if body.covered_dependant_ids:
        assert_dependants_owned(db, emp, body.covered_dependant_ids)

    # Resolve the post-write effective state so the manual path validates and
    # prices exactly like enrollment projection and bulk apply — an admin edit
    # must not leave a stale flex tag or a stale elected dependant level.
    row = db.execute(
        select(EmployeePlanOverride).where(
            EmployeePlanOverride.employee_id == emp.id,
            EmployeePlanOverride.product_id == product.id,
        )
    ).scalar_one_or_none()
    options_provided = "dependant_option_ids" in body.model_fields_set
    dep_options = (
        None
        if body.declined
        else (
            body.dependant_option_ids
            if options_provided
            else (row.dependant_option_ids if row else None)
        )
    )

    pricing = get_pricing(db, py.id)
    ref = reference_date(db, py.id)
    source_map, rule = governing_flex_config(db, py.id)
    slip_idx = maybe_slip_index(db, py.id, source_map)
    family_slip_idx = maybe_family_slip_index(db, py.id, source_map)
    base_cat = baseline_cat_by_product(db, [emp], product.id).get(emp.id)
    default_plan = (
        batch_category_defaults(db, [emp])
        .get(emp.id, {})
        .get(product.id, (None, None))[1]
    )
    if options_provided and body.dependant_option_ids:
        assert_valid_dependant_options(
            body.dependant_option_ids,
            dependant_option_choices(
                family_slip_idx, product.id,
                tier_key(base_cat, body.plan_code or default_plan),
            ),
        )
    dep_profiles = covered_dependant_profiles(
        db, body.covered_dependant_ids,
        age_limits=dependant_age_limits(pricing, product.id), ref=ref,
    )
    spouse_count, child_count = profile_counts(dep_profiles)
    compulsory_deps = base_cat is not None and base_cat in (
        compulsory_dependant_category_ids(db, {base_cat})
    )
    price = member_coverage_tag(
        source_map=source_map, rule=rule, pricing=pricing, slip_idx=slip_idx,
        family_slip_idx=family_slip_idx, product_id=product.id,
        age=employee_age(emp, ref), declined=body.declined,
        tier_category_id=base_cat, plan_code=body.plan_code,
        default_tier_category_id=base_cat, default_plan=default_plan,
        spouse_count=spouse_count, child_count=child_count,
        dep_profiles=dep_profiles, dep_option_ids=dep_options,
        dependants_compulsory=compulsory_deps,
    )

    existing, before = upsert_override(
        db,
        employee_id=emp.id,
        policy_year_id=emp.policy_year_id,
        client_id=emp.client_id,
        product_id=product.id,
        product_code=product.code,
        declined=body.declined,
        plan_code=body.plan_code,
        covered_dependant_ids=body.covered_dependant_ids,
        dependant_option_ids=dep_options,
        flex_price_tag=price,
        effective_from=body.effective_from,
        source=OverrideSource.manual_admin,
        modified_by=user.user_id,
    )
    db.flush()
    write_audit(
        db, user,
        action="update_plan_override" if before else "set_plan_override",
        entity_type="employee_plan_override",
        entity_id=existing.id, before=before, after=override_snapshot(existing),
        employee_id=emp.id,
    )
    db.commit()
    db.refresh(existing)
    return existing


@router.delete(
    "/employees/{employee_id}/plan-overrides/{product_code}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_plan_override(
    employee_id: str,
    product_code: str,
    emp: Employee = Depends(load_employee),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Remove an override so the employee reverts to their category default.
    Idempotent: a missing override is a no-op 204."""
    row = db.execute(
        select(EmployeePlanOverride).where(
            EmployeePlanOverride.employee_id == emp.id,
            EmployeePlanOverride.product_code == product_code,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    before = override_snapshot(row)
    db.delete(row)
    write_audit(
        db, user, action="delete_plan_override", entity_type="employee_plan_override",
        entity_id=row.id, before=before, employee_id=emp.id,
    )
    db.commit()
    return None


@router.get(
    "/employees/{employee_id}/coverage-history",
    response_model=CoverageHistoryOut,
)
def get_coverage_history(
    employee_id: str,
    emp: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
) -> CoverageHistoryOut:
    """Newest-first timeline of this member's coverage changes (the 'track' view)."""
    return CoverageHistoryOut(
        employee_id=emp.id,
        entries=coverage_history(db, emp, limit=limit),
        has_baseline=latest_enrollment_with_baseline(db, emp) is not None,
    )


@router.post(
    "/employees/{employee_id}/coverage/revert",
    response_model=CoverageRevertResult,
)
def revert_coverage(
    employee_id: str,
    body: CoverageRevertRequest,
    emp: Employee = Depends(load_employee),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverageRevertResult:
    """Revert a member's coverage to the window baseline or the cohort default.

    Allowed at any time (including after a window closes) — a post-close revert is
    a broker correction and is fully audited per product.
    """
    if body.target == "default":
        changes = revert_to_default(db, emp, user, body.product_codes)
    else:
        enrollment = latest_enrollment_with_baseline(db, emp, body.window_id)
        if enrollment is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "No enrollment baseline is available to revert this member to.",
            )
        changes = revert_to_baseline(db, emp, enrollment, user, body.product_codes)
    # A full (unscoped) revert also clears any buy/sell-leave trade — baseline leave
    # is "none", so the flex-wallet impact is reversed alongside the coverage.
    if not body.product_codes:
        leave_change = revert_leave(db, emp, user)
        if leave_change is not None:
            changes.append(leave_change)
    db.commit()
    return CoverageRevertResult(
        employee_id=emp.id,
        target=body.target,
        changes=changes,
    )
