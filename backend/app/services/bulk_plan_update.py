"""Bulk plan update — reassign (or decline) one product's plan for many members.

``evaluate`` runs the per-employee validation and returns a structured outcome;
with ``apply=True`` it also writes the sparse ``EmployeePlanOverride`` rows and
stamps each with the batch record id. Preview reuses the same evaluation with
``apply=False`` so the dry-run can never diverge from the real apply.

Three things the module is careful about, each of which was wrong before:

- **The population comes from ``services/member_query``**, not from a list of
  ids typed by a broker. One resolver serves the live headcount, the preview and
  the apply, so they cannot disagree about who is in scope.
- **The flex price tag is resolved on BOTH paths.** It used to be computed only
  when applying, which meant the dry run structurally could not show what the
  real run would write.
- **Every per-member input is loaded in ONE query.** The old loop did a
  ``db.get`` per id plus two dependant queries per member — roughly 7,000 round
  trips on a 2,300-life roster.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
from app.models.dependant import DEPENDANT_STATUS_ACTIVE
from app.models.employee_plan_override import OverrideSource
from app.schemas.enrollment import (
    BulkChangeGroup,
    BulkImpact,
    BulkPlanUpdateRequest,
    BulkRowOutcome,
)
from app.services.cohort_tiers import first_category_per_product
from app.services.coverage_resolver import is_sparse_default, resolve_plan
from app.services.flex_pricing_resolver import (
    compulsory_dependant_category_ids,
    dependant_age_limits,
    dependant_profiles_of,
    employee_age,
    get_pricing,
    governing_flex_config,
    maybe_family_slip_index,
    maybe_slip_index,
    member_coverage_tag,
    profile_counts,
    reference_date,
)
from app.services.member_query import (
    MAX_SELECTION,
    RosterIndex,
    Selection,
    resolve_selection,
    selection_digest,
)
from app.services.override_writer import override_snapshot, upsert_override


class SelectionTooLarge(Exception):
    """More members matched than one run may touch."""

    def __init__(self, selected: int, limit: int) -> None:
        super().__init__(f"{selected} members selected; limit is {limit}.")
        self.selected = selected
        self.limit = limit


class SelectionChanged(Exception):
    """The population (or its coverage) moved between preview and apply.

    Raised BEFORE any write, so an apply that trips it leaves nothing behind.
    """

    def __init__(self, digest: str) -> None:
        super().__init__("Selection changed since the preview.")
        self.digest = digest


@dataclass
class BulkEvaluation:
    """The full result of one evaluation. ``rows`` is every row; the router pages
    it. ``groups`` and ``counts`` are what the broker actually reads."""

    rows: list[BulkRowOutcome] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    groups: list[BulkChangeGroup] = field(default_factory=list)
    impact: BulkImpact = field(default_factory=BulkImpact)
    digest: str | None = None
    selection: Selection | None = None


@dataclass
class _PriceContext:
    """Flex inputs resolved once for the whole batch."""

    pricing: Any
    ref: Any
    source_map: dict[str, Any]
    drawdown_rule: str
    slip_idx: Any
    family_slip_idx: Any
    baseline_cat: dict[str, str]
    compulsory_dep_cats: set[str]


def _price_context(
    db: Session, py: PolicyYear, employees: list[Employee], product_id: str
) -> _PriceContext:
    baseline_cat = baseline_cat_by_product(db, employees, product_id)
    source_map, drawdown_rule = governing_flex_config(db, py.id)
    return _PriceContext(
        pricing=get_pricing(db, py.id),
        ref=reference_date(db, py.id),
        source_map=source_map,
        drawdown_rule=drawdown_rule,
        slip_idx=maybe_slip_index(db, py.id, source_map),
        family_slip_idx=maybe_family_slip_index(db, py.id, source_map),
        baseline_cat=baseline_cat,
        # Baseline categories with compulsory (employer-funded) dependant cover —
        # their dependants draw no member flex (same exemption as the statement).
        compulsory_dep_cats=compulsory_dependant_category_ids(
            db, set(baseline_cat.values())
        ),
    )


@dataclass
class _Dependants:
    """Every selected member's dependants, loaded in ONE query and split two ways.

    The split is load-bearing, and the two halves answer different questions:

    - ``active_by_employee`` is what a ``dependant_action`` may ELECT.
      ``include_all`` must not sweep in a portal self-add sitting at
      ``pending_approval``, which the benefit statement and flex resolution both
      exclude.
    - ``by_id`` is what PRICES an already-covered dependant, and it deliberately
      spans every status. An override's ``covered_dependant_ids`` is existing
      broker-recorded coverage; dropping a since-terminated dependant from the
      price tag here would silently reprice cover that is still on the override,
      which is a repricing decision this tool is not making.

    This must be loaded even when the request carries NO ``dependant_action``:
    the price tag for a plain plan move is resolved against the override's
    EXISTING covered dependants, so loading only for the action priced every
    such member as if they covered nobody.
    """

    active_by_employee: dict[str, list[Dependant]]
    by_id: dict[str, Dependant]


def _load_dependants(db: Session, employee_ids: list[str]) -> _Dependants:
    if not employee_ids:
        return _Dependants({}, {})
    rows = list(
        db.execute(
            select(Dependant).where(Dependant.employee_id.in_(employee_ids))
        ).scalars()
    )
    active: dict[str, list[Dependant]] = {}
    for dep in rows:
        if dep.status == DEPENDANT_STATUS_ACTIVE:
            active.setdefault(dep.employee_id, []).append(dep)
    return _Dependants(active, {d.id: d for d in rows})


def _covered_dependants(
    owned: list[Dependant], req: BulkPlanUpdateRequest
) -> tuple[list[str] | None, str | None]:
    """Resolve the dependant-coverage list for one member. Returns (ids, error).

    ``None`` ids means "no dependant_action was requested" — leave whatever
    coverage the member already has untouched.
    """
    da = req.dependant_action
    if da is None:
        return None, None
    owned_ids = {d.id for d in owned}
    if da.mode == "include_all":
        return sorted(owned_ids), None
    if da.mode == "exclude_all":
        return [], None
    missing = [d for d in da.dependant_ids if d not in owned_ids]
    if missing:
        return None, (
            "Dependants not owned by this member (or not active): "
            f"{', '.join(missing)}."
        )
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
    index: RosterIndex | None = None,
    expected_digest: str | None = None,
) -> BulkEvaluation:
    selection = resolve_selection(
        db, py, req.selector, product_id=product.id, index=index
    )
    idx = selection.index
    assert idx is not None  # resolve_selection always attaches the index it used
    employees = selection.employees
    emp_ids = [e.id for e in employees]
    if len(employees) > MAX_SELECTION:
        raise SelectionTooLarge(len(employees), MAX_SELECTION)

    # Computed BEFORE the loop: the loop mutates the override rows this hashes,
    # so a digest taken afterwards would fingerprint the state the batch just
    # created rather than the state it was approved against. Over
    # ``selection.matched`` (pre-exclusion) so unticking rows in the preview
    # doesn't invalidate the broker's own preview — see ``Selection.matched``.
    digest = selection_digest(product.code, idx, selection.matched, product.id)
    if expected_digest is not None and expected_digest != digest:
        raise SelectionChanged(digest)

    rows: list[BulkRowOutcome] = [
        BulkRowOutcome(
            employee_id=ref.value if ref.kind == "employee_id" else None,
            staff_id=ref.value if ref.kind == "staff_id" else None,
            outcome="error",
            reason=ref.reason,
        )
        for ref in selection.unresolved
    ]

    overrides = idx.overrides
    applied_label = "applied" if apply else "would_apply"
    deps = _load_dependants(db, emp_ids)
    price = _price_context(db, py, employees, product.id)
    age_limits = dependant_age_limits(price.pricing, product.id)

    for emp in employees:
        if not idx.covers(emp.id, product.id):
            rows.append(_row(emp, "skipped", "Member is not enrolled in this product."))
            continue
        default_plan = idx.default_plan(emp.id, product.id)
        ov = overrides.get((emp.id, product.id))
        # Effective "from" plan via the canonical resolver so the preview's
        # from-column agrees with the current-plan filter (a dependant-only
        # override keeps the cohort default plan, not a blank).
        current = resolve_plan(ov, default_plan)
        declined_after = req.action == "decline"
        to_plan = None if declined_after else req.target_plan_code

        electable = deps.active_by_employee.get(emp.id, [])
        dep_ids, dep_err = _covered_dependants(electable, req)
        if dep_err:
            rows.append(
                _row(
                    emp,
                    "error",
                    dep_err,
                    from_plan=current.plan_code,
                    to_plan=to_plan,
                    declined_before=current.declined,
                    declined_after=declined_after,
                )
            )
            continue

        base_cat = price.baseline_cat.get(emp.id)
        # Price against the effective dependant coverage: the new list when the
        # batch sets one, otherwise the override's existing coverage (untouched).
        covered_for_price = (
            dep_ids if dep_ids is not None else (ov.covered_dependant_ids if ov else None)
        )
        # Elected dependant option LEVELS are tier-independent (attached to every
        # tier), so a plan change preserves the member's existing choice —
        # dropping it would silently unprice covered dependants. A decline clears
        # them along with the coverage.
        dep_options = None if declined_after else (ov.dependant_option_ids if ov else None)

        # A bulk set_plan back to the member's cohort default with no dependant
        # deviation needs no override — keep storage sparse (delete any stale
        # override) rather than materialize a redundant default-equal row, which
        # would pin the member off future category changes and re-price flex for
        # a no-op. Mirrors the enrollment-projection sparse rule.
        sparse = is_sparse_default(
            declined=declined_after,
            plan_code=to_plan,
            tier_category_id=None,
            covered_dependant_ids=covered_for_price,
            default_plan=default_plan,
            base_tier=base_cat,
            dependant_option_ids=dep_options,
        )

        tag = _price_tag(
            price,
            product.id,
            emp,
            declined=declined_after,
            base_cat=base_cat,
            to_plan=to_plan,
            default_plan=default_plan,
            covered=covered_for_price,
            dependants=deps.by_id,
            dep_options=dep_options,
            age_limits=age_limits,
        )

        if _unchanged(
            ov, sparse, declined_after, to_plan, covered_for_price, dep_options, tag
        ):
            rows.append(
                _row(
                    emp,
                    "no_change",
                    "Already on this coverage.",
                    from_plan=current.plan_code,
                    to_plan=to_plan,
                    declined_before=current.declined,
                    declined_after=declined_after,
                    tag_before=ov.flex_price_tag if ov else None,
                    tag_after=ov.flex_price_tag if ov else None,
                )
            )
            continue

        if apply:
            if sparse:
                _clear_override(db, emp, ov, user)
            else:
                _write_override(
                    db, emp, py, product, req, dep_ids, record_id, user, tag,
                    dep_options,
                )
        rows.append(
            _row(
                emp,
                applied_label,
                None,
                from_plan=current.plan_code,
                to_plan=to_plan,
                declined_before=current.declined,
                declined_after=declined_after,
                tag_before=ov.flex_price_tag if ov else None,
                tag_after=None if sparse else tag,
                override_cleared=sparse,
            )
        )

    counts: dict[str, int] = {applied_label: 0, "no_change": 0, "skipped": 0, "error": 0}
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1

    return BulkEvaluation(
        rows=rows,
        counts=counts,
        groups=_groups(rows, applied_label),
        impact=_impact(rows, applied_label),
        digest=digest,
        selection=selection,
    )


def _row(
    emp: Employee,
    outcome: str,
    reason: str | None,
    *,
    from_plan: str | None = None,
    to_plan: str | None = None,
    declined_before: bool = False,
    declined_after: bool = False,
    tag_before: float | None = None,
    tag_after: float | None = None,
    override_cleared: bool = False,
) -> BulkRowOutcome:
    return BulkRowOutcome(
        employee_id=emp.id,
        staff_id=emp.staff_id,
        employee_name=emp.employee_name,
        outcome=outcome,
        reason=reason,
        from_plan=from_plan,
        to_plan=to_plan,
        declined_before=declined_before,
        declined_after=declined_after,
        flex_price_tag_before=tag_before,
        flex_price_tag_after=tag_after,
        override_cleared=override_cleared,
    )


def _unchanged(
    ov: EmployeePlanOverride | None,
    sparse: bool,
    declined: bool,
    to_plan: str | None,
    covered: list[str] | None,
    dep_options: dict[str, Any] | None,
    tag: float | None,
) -> bool:
    """Would this member's coverage AND its storage shape come out identical?

    Both halves matter. Effective coverage identical but an override present
    where the target is the sparse default is still a change — removing the
    override is what makes the member responsive to future category changes
    again, so it must be applied and reported, not swallowed as a no-op.

    The comparison is field-for-field against exactly what ``upsert_override``
    would set (including the flex price tag: a repriced tag is a real write).
    ``covered_dependant_ids`` is compared without normalizing ``[]`` to ``None``
    — an explicit empty list means "cover no dependants" and differs from having
    no opinion.
    """
    if sparse:
        return ov is None
    if ov is None:
        return False
    target = (
        declined,
        None if declined else to_plan,
        None,  # tier_category_id — a bulk update never elects a cohort tier
        covered,
        None if declined else dep_options,
        None if declined else tag,
    )
    current = (
        bool(ov.declined),
        ov.plan_code,
        ov.tier_category_id,
        ov.covered_dependant_ids,
        ov.dependant_option_ids,
        ov.flex_price_tag,
    )
    return target == current


def _price_tag(
    price: _PriceContext,
    product_id: str,
    emp: Employee,
    *,
    declined: bool,
    base_cat: str | None,
    to_plan: str | None,
    default_plan: str | None,
    covered: list[str] | None,
    dependants: dict[str, Dependant],
    dep_options: dict[str, Any] | None,
    age_limits: dict[str, dict[str, int]] | None,
) -> float | None:
    """The flex price tag this row would write.

    A bulk update carries no cohort tier, so it prices against the member's
    baseline category — the same key ``summarize_employee`` resolves to for a
    tier-less override, so a bulk-applied tag matches the benefit statement's
    later recompute.

    Dependants are resolved BY ID out of the batch-wide map, not from the
    member's electable list: the ids being priced come from the override's
    existing coverage as often as from a new dependant action, and the two sets
    are not the same (see ``_Dependants``).
    """
    dep_rows = [
        dep
        for dep in (dependants.get(i) for i in (covered or []) if i)
        if dep is not None
    ]
    dep_profiles = dependant_profiles_of(
        dep_rows, age_limits=age_limits, ref=price.ref
    )
    spouse_count, child_count = profile_counts(dep_profiles)
    return member_coverage_tag(
        source_map=price.source_map,
        rule=price.drawdown_rule,
        pricing=price.pricing,
        slip_idx=price.slip_idx,
        family_slip_idx=price.family_slip_idx,
        product_id=product_id,
        age=employee_age(emp, price.ref) if price.ref else None,
        declined=declined,
        tier_category_id=base_cat,
        plan_code=to_plan,
        default_tier_category_id=base_cat,
        default_plan=default_plan,
        spouse_count=spouse_count,
        child_count=child_count,
        dep_profiles=dep_profiles,
        dep_option_ids=dep_options,
        dependants_compulsory=base_cat in price.compulsory_dep_cats,
    )


def _groups(rows: list[BulkRowOutcome], applied_label: str) -> list[BulkChangeGroup]:
    buckets: dict[tuple[str | None, str | None, bool], int] = {}
    for r in rows:
        if r.outcome != applied_label:
            continue
        key = (r.from_plan, r.to_plan, r.declined_after)
        buckets[key] = buckets.get(key, 0) + 1
    return [
        BulkChangeGroup(from_plan=f, to_plan=t, declined_after=d, count=n)
        for (f, t, d), n in sorted(buckets.items(), key=lambda kv: -kv[1])
    ]


def _impact(rows: list[BulkRowOutcome], applied_label: str) -> BulkImpact:
    impact = BulkImpact()
    for r in rows:
        if r.outcome != applied_label:
            continue
        impact.members_changing += 1
        # "Unpriced" means the tag could not be RESOLVED. A declined row has no
        # tag by definition, and a cleared override deliberately writes none —
        # counting either would tell the broker a whole revert-to-default batch
        # was unpriced.
        if (
            r.flex_price_tag_after is None
            and not r.declined_after
            and not r.override_cleared
        ):
            impact.unpriced += 1
        impact.flex_price_tag_before += r.flex_price_tag_before or 0.0
        impact.flex_price_tag_after += r.flex_price_tag_after or 0.0
    impact.flex_price_tag_delta = round(
        impact.flex_price_tag_after - impact.flex_price_tag_before, 2
    )
    impact.flex_price_tag_before = round(impact.flex_price_tag_before, 2)
    impact.flex_price_tag_after = round(impact.flex_price_tag_after, 2)
    return impact


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
    dependant_option_ids: dict[str, Any] | None,
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
