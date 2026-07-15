"""Bulk plan update — reassign (or decline) one product's plan for many members.

``evaluate`` runs the per-employee validation and returns a structured outcome
list; with ``apply=True`` it also writes the sparse ``EmployeePlanOverride`` rows
and stamps each with the batch record id. Preview reuses the same evaluation with
``apply=False`` so the dry-run can never diverge from the real apply.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.models import (
    Category,
    Dependant,
    Employee,
    EmployeePlanOverride,
    PolicyYear,
    Product,
)
from app.models.employee_plan_override import OverrideSource
from app.schemas.enrollment import BulkPlanUpdateRequest, BulkRowOutcome
from app.services.cohort_tiers import first_category_per_product
from app.services.coverage_resolver import (
    batch_category_defaults,
    is_sparse_default,
    load_overrides,
    resolve_plan,
)
from app.services.flex_pricing_resolver import (
    compulsory_dependant_category_ids,
    covered_dependant_profiles,
    dependant_age_limits,
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


def _resolve_employees(
    db: Session, py: PolicyYear, product: Product, req: BulkPlanUpdateRequest
) -> tuple[list[Employee], list[BulkRowOutcome]]:
    """Resolve selector ids/staff-ids/filters to employees in this policy year.

    Returns (employees, error_rows) — error rows flag ids that didn't resolve.
    """
    sel = req.selector
    found: dict[str, Employee] = {}
    errors: list[BulkRowOutcome] = []

    for emp_id in sel.employee_ids:
        emp = db.get(Employee, emp_id)
        if emp is None or emp.policy_year_id != py.id or emp.client_id != py.client_id:
            errors.append(BulkRowOutcome(
                employee_id=emp_id, staff_id=None, outcome="error",
                reason="Employee not found in this policy year.",
            ))
            continue
        found[emp.id] = emp

    if sel.staff_ids:
        rows = db.execute(
            select(Employee).where(
                Employee.policy_year_id == py.id,
                Employee.staff_id.in_(sel.staff_ids),
            )
        ).scalars().all()
        by_staff = {e.staff_id: e for e in rows}
        for staff in sel.staff_ids:
            emp = by_staff.get(staff)
            if emp is None:
                errors.append(BulkRowOutcome(
                    employee_id=None, staff_id=staff, outcome="error",
                    reason="No employee with this staff id in the policy year.",
                ))
            else:
                found[emp.id] = emp

    # Filter selectors (category / current-plan) both scan the active roster;
    # load it ONCE and share so a request carrying both filters doesn't scan
    # twice.
    if sel.category_id or sel.current_plan_code:
        candidates = _active_employees(db, py)
        if sel.category_id:
            for emp in _filter_by_category(candidates, sel.category_id):
                found[emp.id] = emp
        if sel.current_plan_code:
            for emp in _filter_by_plan(db, py, product, candidates, sel.current_plan_code):
                found[emp.id] = emp

    return list(found.values()), errors


def _active_employees(db: Session, py: PolicyYear) -> list[Employee]:
    """All active employees in the policy year — the candidate pool for the
    filter-based bulk selectors (which examine every member's cohort/plan)."""
    return list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == py.id, Employee.status == "active",
            )
        ).scalars()
    )


def _filter_by_category(
    candidates: list[Employee], category_id: str
) -> list[Employee]:
    """Candidates whose matched categories include ``category_id`` — a cohort/tier
    bulk-selection filter (no manual id entry)."""
    return [
        e for e in candidates
        if any(m.get("category_id") == category_id for m in (e.matched_categories or []))
    ]


def _filter_by_plan(
    db: Session,
    py: PolicyYear,
    product: Product,
    candidates: list[Employee],
    plan_code: str,
) -> list[Employee]:
    """Candidates whose EFFECTIVE plan for ``product`` equals ``plan_code`` — the
    "everyone currently on Plan A" filter (e.g. moving a whole plan to its
    renewal replacement without typing every staff id). Effective coverage is
    resolved through the canonical ``resolve_plan`` so the filter matches exactly
    what the benefit statement and exports show."""
    defaults = batch_category_defaults(db, candidates)
    overrides = load_overrides(db, py.id, [e.id for e in candidates])
    out: list[Employee] = []
    for e in candidates:
        default_plan = defaults.get(e.id, {}).get(product.id, (None, None))[1]
        resolved = resolve_plan(overrides.get((e.id, product.id)), default_plan)
        if not resolved.declined and resolved.plan_code == plan_code:
            out.append(e)
    return out


def _covered_dependants(
    db: Session, employee: Employee, req: BulkPlanUpdateRequest
) -> tuple[list[str] | None, str | None]:
    """Resolve the dependant-coverage list for one employee. Returns (ids, error)."""
    da = req.dependant_action
    if da is None:
        return None, None
    owned = set(
        db.execute(
            select(Dependant.id).where(Dependant.employee_id == employee.id)
        ).scalars()
    )
    if da.mode == "include_all":
        return sorted(owned), None
    if da.mode == "exclude_all":
        return [], None
    # mode == "set"
    missing = [d for d in da.dependant_ids if d not in owned]
    if missing:
        return None, f"Dependants not owned by employee: {', '.join(missing)}."
    return list(da.dependant_ids), None


def evaluate(
    db: Session,
    py: PolicyYear,
    product: Product,
    req: BulkPlanUpdateRequest,
    *,
    apply: bool,
    record_id: str | None = None,
    user: CurrentUser | None = None,
) -> tuple[list[BulkRowOutcome], dict[str, int]]:
    employees, rows = _resolve_employees(db, py, product, req)
    emp_ids = [e.id for e in employees]
    overrides = load_overrides(db, py.id, emp_ids)
    defaults_by_emp = batch_category_defaults(db, employees)
    applied_label = "applied" if apply else "would_apply"
    # Flex price-tag inputs (resolved once; per-employee age below). A bulk update
    # carries no cohort tier, so price against the member's baseline category —
    # the same key ``summarize_employee`` resolves to for a tier-less override. The
    # company-wide flex config (source per product + drawdown rule) governs the
    # amount, so a bulk-applied tag matches the benefit statement's recompute.
    pricing = get_pricing(db, py.id) if apply else None
    ref = reference_date(db, py.id) if apply else None
    baseline_cat = baseline_cat_by_product(db, employees, product.id) if apply else {}
    source_map, drawdown_rule = governing_flex_config(db, py.id) if apply else ({}, "full")
    slip_idx = maybe_slip_index(db, py.id, source_map) if apply else None
    family_slip_idx = (
        maybe_family_slip_index(db, py.id, source_map) if apply else None
    )
    # Baseline categories with compulsory (employer-funded) dependant cover —
    # their dependants draw no member flex (same exemption as the statement).
    compulsory_dep_cats = (
        compulsory_dependant_category_ids(db, set(baseline_cat.values()))
        if apply
        else set()
    )

    for emp in employees:
        defaults = defaults_by_emp.get(emp.id, {})
        if product.id not in defaults:
            rows.append(BulkRowOutcome(
                employee_id=emp.id, staff_id=emp.staff_id, outcome="skipped",
                reason="Employee is not enrolled in this product.",
            ))
            continue
        _code, default_plan = defaults[product.id]
        ov = overrides.get((emp.id, product.id))
        # Effective "from" plan via the canonical resolver so the preview's
        # from-column agrees with the current-plan selector (a dependant-only
        # override keeps the cohort default plan, not a blank).
        from_plan = resolve_plan(ov, default_plan).plan_code
        to_plan = None if req.action == "decline" else req.target_plan_code

        dep_ids, dep_err = _covered_dependants(db, emp, req)
        if dep_err:
            rows.append(BulkRowOutcome(
                employee_id=emp.id, staff_id=emp.staff_id, outcome="error",
                reason=dep_err, from_plan=from_plan, to_plan=to_plan,
            ))
            continue

        if apply:
            base_cat = baseline_cat.get(emp.id)
            # Price against the effective dependant coverage: the new list when the
            # batch sets one, otherwise the override's existing coverage (untouched).
            covered_for_price = (
                dep_ids if dep_ids is not None else (ov.covered_dependant_ids if ov else None)
            )
            # Elected dependant option LEVELS are tier-independent (attached to
            # every tier), so a plan change preserves the member's existing
            # choice — dropping it would silently unprice covered dependants.
            dep_options = ov.dependant_option_ids if ov else None
            # A bulk set_plan back to the member's cohort default with no dependant
            # deviation needs no override — keep storage sparse (delete any stale
            # override) rather than materialize a redundant default-equal row, which
            # would pin the member off future category changes and re-price flex for
            # a no-op. Mirrors the enrollment-projection sparse rule.
            if is_sparse_default(
                declined=req.action == "decline",
                plan_code=to_plan,
                tier_category_id=None,
                covered_dependant_ids=covered_for_price,
                default_plan=default_plan,
                base_tier=base_cat,
                dependant_option_ids=dep_options,
            ):
                _clear_override(db, emp, ov, user)
            else:
                dep_profiles = covered_dependant_profiles(
                    db, covered_for_price,
                    age_limits=dependant_age_limits(pricing, product.id),
                    ref=ref,
                )
                spouse_count, child_count = profile_counts(dep_profiles)
                price = member_coverage_tag(
                    source_map=source_map,
                    rule=drawdown_rule,
                    pricing=pricing,
                    slip_idx=slip_idx,
                    family_slip_idx=family_slip_idx,
                    product_id=product.id,
                    age=employee_age(emp, ref) if ref else None,
                    declined=req.action == "decline",
                    tier_category_id=base_cat,
                    plan_code=to_plan,
                    default_tier_category_id=base_cat,
                    default_plan=default_plan,
                    spouse_count=spouse_count,
                    child_count=child_count,
                    dep_profiles=dep_profiles,
                    dep_option_ids=dep_options,
                    dependants_compulsory=base_cat in compulsory_dep_cats,
                )
                _write_override(
                    db, emp, py, product, req, dep_ids, record_id, user, price,
                    dep_options,
                )
        rows.append(BulkRowOutcome(
            employee_id=emp.id, staff_id=emp.staff_id, outcome=applied_label,
            from_plan=from_plan, to_plan=to_plan,
        ))

    counts = {applied_label: 0, "skipped": 0, "error": 0}
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    return rows, counts


def baseline_cat_by_product(
    db: Session, employees: list[Employee], product_id: str
) -> dict[str, str]:
    """``{employee_id: matched_category_id}`` for one product, in one query.

    Reuses the shared ``first_category_per_product`` selector so the price tag a
    bulk apply snapshots is keyed on the SAME baseline category the benefit
    statement later recomputes against (``summarize_employee``) — otherwise the two
    surfaces would show different flex spend for the same member.
    """
    all_ids = {
        m["category_id"]
        for e in employees
        for m in (e.matched_categories or [])
        if m.get("category_id")
    }
    if not all_ids:
        return {}
    prod_of = dict(
        db.execute(
            select(Category.id, Category.product_id).where(Category.id.in_(all_ids))
        ).all()
    )
    out: dict[str, str] = {}
    for e in employees:
        cid = first_category_per_product(e.matched_categories or [], prod_of).get(product_id)
        if cid:
            out[e.id] = cid
    return out


def _clear_override(
    db: Session,
    emp: Employee,
    ov: EmployeePlanOverride | None,
    user: CurrentUser | None,
) -> None:
    """The bulk target equals the member's cohort default — remove any existing
    override so coverage reverts to (and stays) the sparse default. No-op when
    there's no override to clear."""
    if ov is None:
        return
    before = override_snapshot(ov)
    ov_id = ov.id
    db.delete(ov)
    if user is not None:
        db.flush()
        write_audit(
            db, user, action="bulk_plan_override_cleared",
            entity_type="employee_plan_override", entity_id=ov_id,
            before=before, after=None, employee_id=emp.id,
        )


def _write_override(
    db: Session,
    emp: Employee,
    py: PolicyYear,
    product: Product,
    req: BulkPlanUpdateRequest,
    dep_ids: list[str] | None,
    record_id: str | None,
    user: CurrentUser | None,
    flex_price_tag: float | None,
    dependant_option_ids: dict | None,
) -> None:
    # dep_ids is None only when no dependant_action was requested — leave any
    # existing dependant coverage untouched in that case.
    row, before = upsert_override(
        db,
        employee_id=emp.id,
        policy_year_id=py.id,
        client_id=emp.client_id,
        product_id=product.id,
        product_code=product.code,
        declined=req.action == "decline",
        plan_code=req.target_plan_code,
        # A bulk update sets a plan_code directly, not a specific cohort tier —
        # clear any stale tier_category_id left by a prior enrollment election
        # so the override's tier can't contradict its new plan_code. Elected
        # dependant option LEVELS are tier-independent and carry over (they
        # priced the tag above). The flex price tag is re-resolved against the
        # member's baseline category.
        tier_category_id=None,
        dependant_option_ids=dependant_option_ids,
        flex_price_tag=flex_price_tag,
        source=OverrideSource.bulk_update,
        source_ref=record_id,
        modified_by=user.user_id if user else None,
        **({"covered_dependant_ids": dep_ids} if dep_ids is not None else {}),
    )
    # Per-employee audit row (tagged with employee_id) so a bulk change is visible
    # in the member's coverage-history timeline, not only in the batch record.
    if user is not None:
        db.flush()
        write_audit(
            db, user, action="bulk_plan_override",
            entity_type="employee_plan_override", entity_id=row.id,
            before=before, after=override_snapshot(row), employee_id=emp.id,
        )
