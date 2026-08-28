"""Revert a member's effective coverage — to the window baseline or the default.

Two flexibility actions for brokers (and, later, a member portal) once a plan
upgrade / downgrade has been made:

- ``revert_to_default``  drops the sparse ``EmployeePlanOverride`` rows so the
  member falls back to their matched-category (cohort) plan.
- ``revert_to_baseline`` rewrites the overrides to match the coverage the member
  had when the enrollment window opened (``Enrollment.baseline_snapshot``) — the
  "undo my election" case.

Both keep the sparse-storage invariant via the shared ``is_sparse_default``
predicate, re-resolve the flex price tag through the shared ``member_price_tag``
(so the snapshotted tag matches the projection / bulk paths and the benefit
statement's live recompute, including the policy year's source/drawdown config),
and write a per-product audit row (tagged with
``employee_id`` so it shows up in the coverage-history timeline). The caller
commits — matching the rest of the codebase.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.models import Employee, EmployeePlanOverride, Enrollment
from app.models.employee_plan_override import OverrideSource
from app.schemas.enrollment import CoverageChangeOut
from app.services import flex_proration
from app.services.cohort_tiers import tier_key
from app.services.coverage_resolver import is_sparse_default, load_overrides
from app.services.enrollment_lifecycle import _defaults_and_baseline
from app.services.flex_pricing_resolver import (
    active_dependant_profiles,
    compulsory_dependant_category_ids,
    covered_dependant_profiles,
    dependant_age_limits,
    effective_dependant_participation,
    employee_age,
    get_pricing,
    governing_flex_config,
    maybe_family_slip_index,
    maybe_slip_index,
    member_coverage_tag,
    profile_counts,
    reference_date,
)
from app.services.override_writer import (
    override_snapshot,
    restore_entry,
    restore_snapshot,
    upsert_override,
)


def latest_enrollment_with_baseline(
    db: Session, employee: Employee, window_id: str | None = None
) -> Enrollment | None:
    """The member's most recent enrollment carrying a baseline snapshot.

    ``window_id`` pins a specific window; otherwise the newest snapshot wins, so a
    "revert to baseline" uses the coverage the member had at the last window-open.
    """
    stmt = select(Enrollment).where(
        Enrollment.employee_id == employee.id,
        Enrollment.baseline_snapshot.is_not(None),
    )
    if window_id:
        stmt = stmt.where(Enrollment.window_id == window_id)
    stmt = stmt.order_by(Enrollment.created_at.desc())
    return db.execute(stmt).scalars().first()


def _live_category_ids(db: Session, ids: set[str]) -> set[str]:
    """Which of these baseline category ids still exist.

    A baseline snapshot records the CATEGORY the member sat in at window-open,
    but a slip re-upload REPLACES categories — new rows, new ids — so on a real
    roster most snapshot tier ids point at rows that are gone (87% of them on
    CDL). A dangling id is **not a coverage difference and cannot be restored**,
    so it must be read as "no tier opinion", exactly as `hydrate_plans` skips
    dangling `matched_categories` entries.

    Without this every member looked like they deviated from their cohort:
    `is_sparse_default` compares `tier_category_id in (None, base_tier)`, a dead
    id matches neither, and the revert gate showed BOTH buttons for all 982
    members with a baseline while their plan codes were identical.
    """
    if not ids:
        return set()
    from app.models.category import Category  # local import avoids a cycle

    return set(db.execute(select(Category.id).where(Category.id.in_(ids))).scalars())


def _snapshot_tier_ids(products: dict[str, Any]) -> set[str]:
    return {
        t
        for bp in products.values()
        if isinstance(bp, dict) and (t := bp.get("tier_category_id"))
    }


def revert_leave(
    db: Session, employee: Employee, user: CurrentUser
) -> CoverageChangeOut | None:
    """Clear the member's effective buy/sell-leave trade (baseline leave = none).

    Operates on the SAME row the balance reader counts (``latest_confirmed_leave``)
    so revert and read agree — a superseded older window's row is already ignored by
    the reader, so clearing the effective row is enough. Reverses the flex-wallet
    impact by zeroing the election + its snapshotted ``flex_amount``; audited
    per-employee so it shows in the coverage/flex timeline. Returns a change row, or
    None when there was no active leave trade.
    """
    from app.models.leave_election import LeaveAction
    from app.services.leave_pricing_resolver import latest_confirmed_leave

    row = latest_confirmed_leave(db, employee)
    if row is None or row.action == LeaveAction.none:
        return None
    before = {"action": row.action, "days": row.days, "flex_amount": row.flex_amount}
    row.action = LeaveAction.none
    row.days = 0.0
    row.flex_amount = None
    db.flush()
    write_audit(
        db, user, action="revert_leave", entity_type="leave_election",
        entity_id=row.id, before=before,
        after={"action": LeaveAction.none, "days": 0, "flex_amount": None},
        employee_id=employee.id,
    )
    return CoverageChangeOut(
        product_code="(leave)", outcome="reverted",
        from_plan=None, to_plan=None, detail="Buy/sell-leave trade cleared.",
    )


def _effective_from_override(
    ov: EmployeePlanOverride | None, default_plan: str | None
) -> str | None:
    """Human-facing 'current plan' label for a change row."""
    if ov is None:
        return default_plan
    if ov.declined:
        return None
    return ov.plan_code or default_plan


def revert_to_default(
    db: Session,
    employee: Employee,
    user: CurrentUser,
    product_codes: list[str] | None = None,
    restore: list[dict[str, Any]] | None = None,
) -> list[CoverageChangeOut]:
    """Drop overrides so the member reverts to their cohort default plan.

    ``product_codes`` limits the revert to those products; ``None`` reverts every
    overridden product. Idempotent — a product with no override is "unchanged".

    ``restore`` collects undo entries in ``bulk_plan_update.undo_batch``'s shape
    — the per-member revert is undoable through that one mechanism rather than a
    second restore path of its own.
    """
    restore = [] if restore is None else restore
    wanted = {c.strip() for c in product_codes} if product_codes else None

    overrides = (
        db.execute(
            select(EmployeePlanOverride)
            .where(EmployeePlanOverride.employee_id == employee.id)
            .order_by(EmployeePlanOverride.product_code)
        )
        .scalars()
        .all()
    )
    # Nothing to revert and nothing to report → skip the defaults JOIN entirely.
    if not overrides and not wanted:
        return []

    defaults, _baseline_cat = _defaults_and_baseline(db, employee)
    default_by_code = {code: plan for (code, plan) in defaults.values()}

    changes: list[CoverageChangeOut] = []
    for ov in overrides:
        if wanted is not None and ov.product_code not in wanted:
            continue
        default_plan = default_by_code.get(ov.product_code)
        before = override_snapshot(ov)
        restore.append(restore_entry(
            employee, product_id=ov.product_id, product_code=ov.product_code,
            before=restore_snapshot(ov), after=None,
        ))
        db.delete(ov)
        write_audit(
            db, user, action="revert_coverage_to_default",
            entity_type="employee_plan_override", entity_id=ov.id,
            before=before,
            # Record the destination state so the timeline can show "plan → default".
            after={"product_code": ov.product_code, "plan_code": default_plan, "declined": False},
            employee_id=employee.id,
        )
        changes.append(CoverageChangeOut(
            product_code=ov.product_code,
            outcome="reset_to_default",
            from_plan=_effective_from_override(ov, default_plan),
            to_plan=default_plan,
        ))
    # Report explicitly-requested products that had nothing to revert.
    if wanted is not None:
        touched = {c.product_code for c in changes}
        for code in sorted(wanted - touched):
            changes.append(CoverageChangeOut(
                product_code=code, outcome="unchanged",
                from_plan=default_by_code.get(code), to_plan=default_by_code.get(code),
                detail="No override to remove.",
            ))
    return changes


def revert_to_baseline(
    db: Session,
    employee: Employee,
    enrollment: Enrollment,
    user: CurrentUser,
    product_codes: list[str] | None = None,
    restore: list[dict[str, Any]] | None = None,
) -> list[CoverageChangeOut]:
    """Rewrite overrides to the coverage captured at window-open (baseline_snapshot).

    For each baselined product: if the baseline equals the cohort default the
    override is removed (stay sparse); otherwise it's written/updated to the
    baseline plan/tier/dependants, with the flex price tag re-resolved against the
    member's age band — identical to how projection and bulk apply write it.
    Overrides for products NOT in the snapshot (e.g. a product that entered the
    member's cohort after a re-match) are surfaced as ``skipped``, not silently
    left — so the broker knows the revert didn't cover them.
    """
    restore = [] if restore is None else restore
    snapshot = enrollment.baseline_snapshot or {}
    products: dict[str, Any] = snapshot.get("products", {}) if isinstance(snapshot, dict) else {}
    wanted = {c.strip() for c in product_codes} if product_codes else None

    defaults, baseline_cat = _defaults_and_baseline(db, employee)
    pid_by_code = {code: pid for pid, (code, _plan) in defaults.items()}
    overrides = load_overrides(db, employee.policy_year_id, [employee.id])
    # A snapshot tier id whose category was replaced by a re-upload cannot be
    # restored — read it as "no tier opinion". Writing the dead id back would pin
    # the member to a category that no longer exists, which is exactly what
    # `find_orphan_overrides` exists to flag.
    live_tiers = _live_category_ids(db, _snapshot_tier_ids(products))

    # Flex pricing + age + the policy year's flex config are only needed when a
    # non-default override is written; resolve them lazily so an idempotent revert
    # (all-default) issues no extra I/O. Config (source per product + drawdown rule)
    # comes from ``governing_flex_config`` — the SAME company-current setting the
    # benefit statement recomputes with — so the re-snapshotted tag matches what's
    # displayed (window-agnostic surfaces must agree).
    _price_ctx: dict[str, Any] = {}

    def _price_inputs() -> dict[str, Any]:
        if "loaded" not in _price_ctx:
            _price_ctx["pricing"] = get_pricing(db, employee.policy_year_id)
            _price_ctx["ref"] = reference_date(db, employee.policy_year_id)
            _price_ctx["age"] = employee_age(employee, _price_ctx["ref"])
            source_map, rule = governing_flex_config(db, employee.policy_year_id)
            _price_ctx["source_map"] = source_map
            _price_ctx["rule"] = rule
            _price_ctx["slip_idx"] = maybe_slip_index(
                db, employee.policy_year_id, source_map
            )
            _price_ctx["family_slip_idx"] = maybe_family_slip_index(
                db, employee.policy_year_id, source_map
            )
            # Baseline categories with compulsory dependant cover. Repricing
            # automatically includes every active eligible dependant.
            _price_ctx["compulsory_dep_cats"] = compulsory_dependant_category_ids(
                db, {c for c in baseline_cat.values() if c}
            )
            _price_ctx["loaded"] = True
        return _price_ctx

    changes: list[CoverageChangeOut] = []
    touched_pids: set[str] = set()
    for code, bp in products.items():
        if wanted is not None and code not in wanted:
            continue
        if not isinstance(bp, dict):
            continue
        pid = pid_by_code.get(code)
        if pid is None:
            changes.append(CoverageChangeOut(
                product_code=code, outcome="skipped", from_plan=None, to_plan=None,
                detail="Product is no longer in the member's cohort.",
            ))
            continue
        touched_pids.add(pid)
        default_plan = defaults[pid][1]
        base_tier = baseline_cat.get(pid)
        declined = bool(bp.get("declined"))
        plan_code = bp.get("plan_code")
        snap_tier = bp.get("tier_category_id")
        tier = snap_tier if snap_tier in live_tiers else None
        deps = bp.get("covered_dependant_ids")
        dep_option_ids = bp.get("dependant_option_ids")
        ov = overrides.get((employee.id, pid))
        from_plan = _effective_from_override(ov, default_plan)

        if is_sparse_default(
            declined=declined,
            plan_code=plan_code,
            tier_category_id=tier,
            covered_dependant_ids=deps,
            default_plan=default_plan,
            base_tier=base_tier,
            dependant_option_ids=dep_option_ids,
        ):
            if ov is not None:
                before = override_snapshot(ov)
                restore.append(restore_entry(
                    employee, product_id=pid, product_code=code,
                    before=restore_snapshot(ov), after=None,
                ))
                db.delete(ov)
                write_audit(
                    db, user, action="revert_coverage_to_baseline",
                    entity_type="employee_plan_override", entity_id=ov.id,
                    before=before,
                    after={"product_code": code, "plan_code": default_plan, "declined": False},
                    employee_id=employee.id,
                )
                changes.append(CoverageChangeOut(
                    product_code=code, outcome="reverted",
                    from_plan=from_plan, to_plan=default_plan,
                ))
            else:
                changes.append(CoverageChangeOut(
                    product_code=code, outcome="unchanged",
                    from_plan=from_plan, to_plan=default_plan,
                    detail="Already at baseline (cohort default).",
                ))
            continue

        ctx = _price_inputs()
        age_limits = dependant_age_limits(ctx["pricing"], pid)
        participation = effective_dependant_participation(
            ctx["pricing"],
            pid,
            tier_key(tier or base_tier, plan_code or default_plan),
            (
                "compulsory"
                if base_tier in ctx["compulsory_dep_cats"]
                else "voluntary"
            ),
        )
        if participation == "compulsory":
            dep_profiles = active_dependant_profiles(
                db,
                employee.id,
                age_limits=age_limits,
                ref=ctx["ref"],
            )
        elif participation == "voluntary":
            dep_profiles = covered_dependant_profiles(
                db,
                deps,
                age_limits=age_limits,
                ref=ctx["ref"],
            )
        else:
            dep_profiles = []
        stored_deps = deps if participation is not None else None
        stored_dep_option_ids = (
            dep_option_ids if participation is not None else None
        )
        spouse_count, child_count = profile_counts(dep_profiles)
        price = member_coverage_tag(
            source_map=ctx["source_map"],
            rule=ctx["rule"],
            pricing=ctx["pricing"],
            slip_idx=ctx["slip_idx"],
            family_slip_idx=ctx["family_slip_idx"],
            product_id=pid,
            age=ctx["age"],
            declined=declined,
            tier_category_id=tier or base_tier,
            plan_code=plan_code or default_plan,
            default_tier_category_id=base_tier,
            default_plan=default_plan,
            spouse_count=spouse_count,
            child_count=child_count,
            dep_profiles=dep_profiles,
            dep_option_ids=stored_dep_option_ids,
            factor=flex_proration.factor_of(employee),
        )
        restore_before = restore_snapshot(ov)
        row, before = upsert_override(
            db,
            employee_id=employee.id,
            policy_year_id=employee.policy_year_id,
            client_id=employee.client_id,
            product_id=pid,
            product_code=code,
            declined=declined,
            plan_code=plan_code,
            tier_category_id=tier,
            covered_dependant_ids=stored_deps,
            dependant_option_ids=stored_dep_option_ids,
            flex_price_tag=price,
            source=OverrideSource.manual_admin,
            source_ref=enrollment.id,
            modified_by=user.user_id,
        )
        db.flush()
        restore.append(restore_entry(
            employee, product_id=pid, product_code=code,
            before=restore_before, after=restore_snapshot(row),
        ))
        write_audit(
            db, user, action="revert_coverage_to_baseline",
            entity_type="employee_plan_override", entity_id=row.id,
            before=before, after=override_snapshot(row), employee_id=employee.id,
        )
        changes.append(CoverageChangeOut(
            product_code=code,
            outcome="reverted",
            from_plan=from_plan,
            to_plan=None if declined else (plan_code or default_plan),
            detail="Declined at baseline." if declined else None,
        ))

    # Overrides on products the snapshot never covered: the baseline has no opinion
    # on them (they entered the cohort after window-open), so leave them but say so.
    for (_emp_id, pid), ov in overrides.items():
        if pid in touched_pids:
            continue
        if wanted is not None and ov.product_code not in wanted:
            continue
        changes.append(CoverageChangeOut(
            product_code=ov.product_code, outcome="skipped",
            from_plan=_effective_from_override(ov, None), to_plan=None,
            detail="Not part of the enrolment-period baseline — left unchanged.",
        ))
    return changes
