"""Bulk coverage changes — apply a SET of coverage changes to a population.

``evaluate`` runs the per-member validation for every change in the set and
returns a structured outcome; with ``apply=True`` it also writes the sparse
``EmployeePlanOverride`` rows and stamps each with the batch record id. Preview
reuses the same evaluation with ``apply=False`` so the dry-run can never diverge
from the real apply.

Five things the module is careful about, each of which was wrong before:

- **The population comes from ``services/member_query``**, not from a list of
  ids typed by a broker. One resolver serves the live headcount, the preview and
  the apply, so they cannot disagree about who is in scope.
- **The flex price tag is resolved on BOTH paths.** It used to be computed only
  when applying, which meant the dry run structurally could not show what the
  real run would write.
- **Every per-member input is loaded in ONE query.** The old loop did a
  ``db.get`` per id plus two dependant queries per member — roughly 7,000 round
  trips on a 2,300-life roster.
- **Nothing is written until the whole batch has been evaluated.** The rows are
  planned first, then the size / staleness / acknowledgement gates run, and only
  then does anything execute. A batch that trips a gate must leave NOTHING
  behind, and warnings can only be known once the rows exist — so evaluating and
  writing in one pass would mean deciding whether to write halfway through
  writing.
- **A multi-product batch is one transaction.** "GHS to Plan 2 and GTL to Plan B
  for these 300 people" applies whole or not at all; a run that moved one product
  and failed on the next is the state nobody can reason about afterwards. This
  module never commits — the caller owns the commit, and one fault rolls the
  whole set back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
from app.models.leave_election import LeaveAction, LeaveElection, LeaveElectionStatus
from app.schemas.enrollment import (
    BulkChangeGroup,
    BulkDependantAction,
    BulkImpact,
    BulkRowOutcome,
    BulkWarningBucket,
    CoverageChange,
)
from app.schemas.member_query import MemberQuery
from app.services import bulk_warnings as warn
from app.services import dual_coverage, flex_proration
from app.services.cohort_tiers import first_category_per_product, tier_key
from app.services.coverage_resolver import is_sparse_default, resolve_plan
from app.services.flex_pricing_resolver import (
    compulsory_dependant_category_ids,
    dependant_age_limits,
    dependant_profiles_of,
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
from app.services.member_query import (
    MAX_SELECTION,
    RosterIndex,
    Selection,
    resolve_selection,
    selection_digest,
)
from app.services.override_writer import (
    override_snapshot,
    restore_snapshot,
    upsert_override,
)

# A balance below half a cent is overdrawn — the same float-noise tolerance the
# enrollment wallet guard uses, so the two surfaces agree on the boundary.
_EPSILON = 0.005


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


class UnacknowledgedWarnings(Exception):
    """Apply was asked to run with a decision-needing warning outstanding.

    Also raised before any write: the broker is being asked to accept something,
    and half of the batch already being applied would make that a formality.
    """

    def __init__(self, buckets: list[BulkWarningBucket]) -> None:
        codes = ", ".join(b.code for b in buckets)
        super().__init__(f"Unacknowledged warnings: {codes}")
        self.buckets = buckets


@dataclass
class ResolvedChange:
    """One requested change with its product resolved (the router validates the
    product exists in the year and that the target plan is configured)."""

    change: CoverageChange
    product: Product


@dataclass
class BulkEvaluation:
    """The full result of one evaluation. ``rows`` is every row; the router pages
    it. ``groups``, ``counts`` and ``warnings`` are what the broker reads."""

    rows: list[BulkRowOutcome] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    groups: list[BulkChangeGroup] = field(default_factory=list)
    warnings: list[BulkWarningBucket] = field(default_factory=list)
    impact: BulkImpact = field(default_factory=BulkImpact)
    digest: str | None = None
    selection: Selection | None = None
    # Per written pair: what it looked like before and after. The undo source —
    # see ``undo_batch``. Empty on a preview.
    restore: list[dict[str, Any]] = field(default_factory=list)


# ── Flex pricing inputs, resolved once for the whole batch ──────────────────


@dataclass
class _FlexContext:
    """Everything ``member_coverage_tag`` needs, for EVERY product in the year.

    Year-level (pricing, source map, drawdown rule, slip indices) plus the
    per-(member, product) baseline category. It spans every product the selection
    is covered by, not only the ones being changed, because the overdraft check
    has to total a member's whole wallet draw — the products this batch does not
    touch still spend flex.
    """

    pricing: Any
    ref: date
    source_map: dict[str, Any]
    drawdown_rule: str
    slip_idx: Any
    family_slip_idx: Any
    baseline_cat: dict[tuple[str, str], str]
    compulsory_dep_cats: set[str]
    # Whether flex is configured for this year at all. Without it every changing
    # row of a company that does not run flex was reported "unpriced", which is
    # not a gap — the product simply has no price tag.
    configured: bool
    _age_limits: dict[str, dict[str, dict[str, int]] | None] = field(default_factory=dict)

    def age_limits(self, product_id: str) -> dict[str, dict[str, int]] | None:
        if product_id not in self._age_limits:
            self._age_limits[product_id] = dependant_age_limits(self.pricing, product_id)
        return self._age_limits[product_id]


def baseline_categories(
    db: Session, employees: list[Employee]
) -> dict[tuple[str, str], str]:
    """``{(employee_id, product_id): matched_category_id}`` across every product.

    Reuses the shared ``first_category_per_product`` selector so the price tag a
    bulk apply snapshots is keyed on the SAME baseline category the benefit
    statement later recomputes against (``summarize_employee``) — otherwise the
    two surfaces would show different flex spend for the same member.
    """
    all_ids = {
        m["category_id"]
        for e in employees
        for m in (e.matched_categories or [])
        if m.get("category_id")
    }
    if not all_ids:
        return {}
    prod_of: dict[str, str] = {
        cid: pid
        for cid, pid in db.execute(
            select(Category.id, Category.product_id).where(Category.id.in_(all_ids))
        ).all()
        if pid
    }
    out: dict[tuple[str, str], str] = {}
    for e in employees:
        for pid, cid in first_category_per_product(
            e.matched_categories or [], prod_of
        ).items():
            out[(e.id, pid)] = cid
    return out


def baseline_cat_by_product(
    db: Session, employees: list[Employee], product_id: str
) -> dict[str, str]:
    """``{employee_id: matched_category_id}`` for ONE product — the single-product
    view of ``baseline_categories``, for callers (the manual override editor)
    that price one member against one product."""
    return {
        emp_id: cid
        for (emp_id, pid), cid in baseline_categories(db, employees).items()
        if pid == product_id
    }


def _flex_context(
    db: Session, py: PolicyYear, employees: list[Employee]
) -> _FlexContext:
    source_map, drawdown_rule = governing_flex_config(db, py.id)
    pricing = get_pricing(db, py.id)
    slip_idx = maybe_slip_index(db, py.id, source_map)
    family_slip_idx = maybe_family_slip_index(db, py.id, source_map)
    baseline_cat = baseline_categories(db, employees)
    return _FlexContext(
        pricing=pricing,
        ref=reference_date(db, py.id),
        source_map=source_map,
        drawdown_rule=drawdown_rule,
        slip_idx=slip_idx,
        family_slip_idx=family_slip_idx,
        baseline_cat=baseline_cat,
        # Baseline categories with compulsory dependant cover. They auto-include
        # every active eligible dependant in the employee-wallet price.
        compulsory_dep_cats=compulsory_dependant_category_ids(
            db, set(baseline_cat.values())
        ),
        configured=bool(pricing is not None or slip_idx or family_slip_idx),
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
        if dep.status == DEPENDANT_STATUS_ACTIVE and dep.employee_id:
            active.setdefault(dep.employee_id, []).append(dep)
    return _Dependants(active, {d.id: d for d in rows})


def _load_leave(db: Session, py: PolicyYear, employee_ids: list[str]) -> dict[str, float]:
    """``{employee_id: signed flex impact}`` of each member's effective leave trade.

    The newest CONFIRMED row per member, matching ``latest_confirmed_leave`` —
    an older window's row is superseded, never summed. Batched because the
    overdraft check needs it for every selected member and the per-member
    selector is a query each.
    """
    if not employee_ids:
        return {}
    rows = db.execute(
        select(LeaveElection)
        .where(
            LeaveElection.policy_year_id == py.id,
            LeaveElection.employee_id.in_(employee_ids),
            LeaveElection.status == LeaveElectionStatus.confirmed,
        )
        .order_by(LeaveElection.created_at.desc())
    ).scalars()
    out: dict[str, float] = {}
    for row in rows:
        if row.employee_id in out:
            continue  # newest wins — the query is ordered
        if row.action == LeaveAction.none or not isinstance(
            row.flex_amount, (int, float)
        ):
            continue
        out[row.employee_id] = float(row.flex_amount)
    return out


def _covered_dependants(
    owned: list[Dependant], action: BulkDependantAction | None
) -> tuple[list[str] | None, str | None]:
    """Resolve the dependant-coverage list for one member. Returns (ids, error).

    ``None`` ids means "no dependant_action was requested" — leave whatever
    coverage the member already has untouched.
    """
    if action is None:
        return None, None
    owned_ids = {d.id for d in owned}
    if action.mode == "include_all":
        return sorted(owned_ids), None
    if action.mode == "exclude_all":
        return [], None
    missing = [d for d in action.dependant_ids if d not in owned_ids]
    if missing:
        return None, (
            "Dependants not owned by this member (or not active): "
            f"{', '.join(missing)}."
        )
    return list(action.dependant_ids), None


# ── One planned write ───────────────────────────────────────────────────────


@dataclass
class _Plan:
    """A change this batch WOULD make, held until every gate has passed."""

    employee: Employee
    product: Product
    change: CoverageChange
    ov: EmployeePlanOverride | None
    tier_category_id: str | None
    dep_ids: list[str] | None
    dep_options: dict[str, Any] | None
    clear_dependants: bool
    tag: float | None
    sparse: bool
    row: BulkRowOutcome
    # The tier quotes a cover figure that would not reduce to this member. Held
    # here rather than derived from the row: "no figure" and "no such figure"
    # look identical on the row, and only the first is worth reporting.
    financials_unresolved: bool = False


def evaluate(
    db: Session,
    py: PolicyYear,
    changes: list[ResolvedChange],
    query: MemberQuery,
    *,
    apply: bool,
    record_id: str | None = None,
    user: CurrentUser | None = None,
    index: RosterIndex | None = None,
    expected_digest: str | None = None,
    acknowledged: set[str] | None = None,
) -> BulkEvaluation:
    if not changes:
        raise ValueError("A bulk coverage change needs at least one change.")
    # The coverage filters (`current_plan_codes`, `coverage_state`) resolve
    # against ONE product's effective plan — a member can be on Plan 1 of GHS and
    # Plan 3 of GTL, so scoping them per change would make "everyone on Plan 1"
    # name two different populations inside one batch.
    selection = resolve_selection(
        db, py, query, product_id=changes[0].product.id, index=index
    )
    idx = selection.index
    assert idx is not None  # resolve_selection always attaches the index it used
    employees = selection.employees
    emp_ids = [e.id for e in employees]
    if len(employees) > MAX_SELECTION:
        raise SelectionTooLarge(len(employees), MAX_SELECTION)

    # Computed BEFORE anything is written: the writes mutate the override rows
    # this hashes, so a digest taken afterwards would fingerprint the state the
    # batch just created rather than the state it was approved against. Over
    # ``selection.matched`` (pre-exclusion) so unticking rows in the preview
    # doesn't invalidate the broker's own preview — see ``Selection.matched``.
    digest = selection_digest(
        [(c.product.code, c.product.id) for c in changes], idx, selection.matched
    )
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

    deps = _load_dependants(db, emp_ids)
    flex = _flex_context(db, py, employees)
    wctx = warn.build_context(
        db, py, {c.product.id: c.product.code for c in changes}, emp_ids
    )
    # Lives already doubled on the roster, so electing one of them here can
    # be flagged. Grouping only — the full detector's coverage enrichment is
    # not needed to answer "is this life on two payrolls". Skipped entirely
    # unless a change elects dependants: it is a whole-roster load, and every
    # plan-only batch would otherwise pay for a set nothing reads.
    if any(c.change.dependant_action is not None for c in changes):
        wctx.dual_covered_dependant_ids = dual_coverage.duplicated_dependant_ids(
            db, py
        )
    codes_by_employee: dict[str, set[str]] = {}
    plans: list[_Plan] = []

    for resolved in changes:
        _evaluate_change(
            resolved,
            employees=employees,
            idx=idx,
            deps=deps,
            flex=flex,
            wctx=wctx,
            rows=rows,
            plans=plans,
            codes_by_employee=codes_by_employee,
        )

    _flag_overdrafts(
        db, py, employees, plans, idx=idx, deps=deps, flex=flex,
        codes_by_employee=codes_by_employee,
    )

    buckets = warn.buckets(codes_by_employee)
    if apply:
        outstanding = [
            b
            for b in buckets
            if b.requires_ack and b.code not in (acknowledged or set())
        ]
        if outstanding:
            raise UnacknowledgedWarnings(outstanding)

    restore: list[dict[str, Any]] = []
    if apply:
        for planned in plans:
            restore.append(_execute(db, py, planned, record_id, user))
            planned.row.outcome = "applied"

    counts: dict[str, int] = {
        "applied" if apply else "would_apply": 0,
        "no_change": 0,
        "skipped": 0,
        "error": 0,
    }
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1

    changing = "applied" if apply else "would_apply"
    return BulkEvaluation(
        rows=rows,
        counts=counts,
        groups=_groups(rows, changing),
        warnings=buckets,
        impact=_impact(
            rows, changing, flex.configured,
            sum(1 for p in plans if p.financials_unresolved),
        ),
        digest=digest,
        selection=selection,
        restore=restore,
    )


def _evaluate_change(
    resolved: ResolvedChange,
    *,
    employees: list[Employee],
    idx: RosterIndex,
    deps: _Dependants,
    flex: _FlexContext,
    wctx: warn.WarningContext,
    rows: list[BulkRowOutcome],
    plans: list[_Plan],
    codes_by_employee: dict[str, set[str]],
) -> None:
    """Plan one product's change across the whole selection. Writes nothing."""
    product = resolved.product
    change = resolved.change
    age_limits = flex.age_limits(product.id)
    revert = change.action == "revert_to_default"
    declined_after = change.action == "decline"

    for emp in employees:
        if not idx.covers(emp.id, product.id):
            rows.append(
                _row(
                    emp, "skipped", "Member is not enrolled in this product.",
                    product_code=product.code,
                )
            )
            continue
        default_plan = idx.default_plan(emp.id, product.id)
        ov = idx.overrides.get((emp.id, product.id))
        # Effective "from" plan via the canonical resolver so the preview's
        # from-column agrees with the current-plan filter (a dependant-only
        # override keeps the cohort default plan, not a blank).
        current = resolve_plan(ov, default_plan)
        # A revert restores the cohort default whole — its plan, and no
        # dependant/option deviation. Pricing it any other way would disagree
        # with what the benefit statement recomputes once the override is gone
        # (``_member_flex_line`` with no override).
        to_plan = default_plan if revert else (None if declined_after else change.target_plan_code)

        electable = deps.active_by_employee.get(emp.id, [])
        dep_ids, dep_err = _covered_dependants(electable, change.dependant_action)
        if dep_err:
            rows.append(
                _row(
                    emp, "error", dep_err,
                    product_code=product.code,
                    from_plan=current.plan_code,
                    to_plan=to_plan,
                    declined_before=current.declined,
                    declined_after=declined_after,
                )
            )
            continue

        base_cat = flex.baseline_cat.get((emp.id, product.id))
        tier_index = wctx.tier_indexes.get(product.id)
        target_tier = (
            tier_index.tier_for_plan(base_cat, to_plan)
            if tier_index is not None and not declined_after
            else None
        )
        target_tier_id = (
            target_tier.tier_category_id if target_tier is not None else base_cat
        )
        tier_set = tier_index.sets.get(base_cat or "") if tier_index is not None else None
        detected_participation = (
            target_tier.dependant_participation if target_tier is not None else None
        ) or (tier_set.dependant_participation if tier_set is not None else None)
        participation = (
            None
            if declined_after
            else _dependant_participation(
                flex,
                product.id,
                base_cat=base_cat,
                tier_category_id=target_tier_id,
                plan_code=to_plan,
                detected=detected_participation,
            )
        )
        # Price against the effective dependant coverage: the new list when the
        # batch sets one, otherwise the override's existing coverage (untouched).
        # A revert drops the override, so it prices with none.
        clear_dependants = declined_after or participation is None
        if revert or clear_dependants:
            covered_for_price: list[str] | None = None
            dep_options: dict[str, Any] | None = None
        else:
            covered_for_price = (
                dep_ids if dep_ids is not None else (ov.covered_dependant_ids if ov else None)
            )
            # Elected dependant option LEVELS are tier-independent (attached to
            # every tier), so a plan change preserves the member's existing choice
            # — dropping it would silently unprice covered dependants. A decline
            # clears them along with the coverage.
            dep_options = None if declined_after else (ov.dependant_option_ids if ov else None)

        # A bulk set_plan back to the member's cohort default with no dependant
        # deviation needs no override — keep storage sparse (delete any stale
        # override) rather than materialize a redundant default-equal row, which
        # would pin the member off future category changes and re-price flex for
        # a no-op. Mirrors the enrollment-projection sparse rule. A revert is
        # sparse by definition: removing the override IS the change.
        sparse = revert or is_sparse_default(
            declined=declined_after,
            plan_code=to_plan,
            tier_category_id=target_tier_id,
            covered_dependant_ids=covered_for_price,
            default_plan=default_plan,
            base_tier=base_cat,
            dependant_option_ids=dep_options,
        )

        tag = _price_tag(
            flex, product.id, emp,
            declined=declined_after,
            base_cat=base_cat,
            tier_category_id=target_tier_id,
            to_plan=to_plan,
            default_plan=default_plan,
            covered=covered_for_price,
            dependants=deps.by_id,
            dep_options=dep_options,
            participation=participation,
            age_limits=age_limits,
        )

        if _unchanged(
            ov,
            sparse,
            declined_after,
            to_plan,
            target_tier_id,
            covered_for_price,
            dep_options,
            tag,
        ):
            rows.append(
                _row(
                    emp, "no_change", "Already on this coverage.",
                    product_code=product.code,
                    from_plan=current.plan_code,
                    to_plan=to_plan,
                    declined_before=current.declined,
                    declined_after=declined_after,
                    # Both sides are the same figure by definition — nothing
                    # about this member moves. Showing the stored tag on one
                    # side and the recomputed one on the other would render a
                    # no-op as a price movement.
                    tag_before=tag,
                    tag_after=tag,
                )
            )
            continue

        age = employee_age(emp, flex.ref) if flex.ref else None
        gst = wctx.gst_multipliers.get(product.id, 1.0)
        before_fig = warn.member_figures(
            warn.target_category(wctx, product.id, base_cat, current.plan_code),
            age, emp, gst,
        )
        after_fig = (
            warn.MemberFigures(None, None, False)
            if declined_after
            else warn.member_figures(
                warn.target_category(wctx, product.id, base_cat, to_plan), age, emp, gst
            )
        )
        # A tier that quotes cover but will not reduce to THIS member is the
        # only interesting gap. A reimbursement product quotes none by design —
        # counting those told the broker every one of 506 rows had "no
        # per-member figure", which reads as a data problem and is just the
        # shape of GP/SP/dental/hospital cover.
        unresolved = (
            (before_fig.quoted or after_fig.quoted)
            and not declined_after
            and (
                before_fig.sum_insured is None
                or after_fig.sum_insured is None
                or before_fig.annual_premium is None
                or after_fig.annual_premium is None
            )
            and not current.declined
        )

        row = _row(
            emp, "would_apply", None,
            product_code=product.code,
            from_plan=current.plan_code,
            to_plan=to_plan,
            declined_before=current.declined,
            declined_after=declined_after,
            tag_before=ov.flex_price_tag if ov else None,
            tag_after=tag,
            override_cleared=sparse,
            si_before=None if current.declined else before_fig.sum_insured,
            si_after=after_fig.sum_insured,
            premium_before=None if current.declined else before_fig.annual_premium,
            premium_after=after_fig.annual_premium,
        )
        row.warnings = warn.row_codes(
            wctx,
            product_id=product.id,
            employee=emp,
            baseline_category_id=base_cat,
            ov_source=ov.source if ov else None,
            action=change.action,
            target_plan_code=to_plan,
            sum_insured_after=after_fig.sum_insured,
            price_tag_after=tag,
            declined_after=declined_after,
            flex_configured=flex.configured,
            ineligible_dependants=_ineligible_count(
                covered_for_price, deps.by_id, age_limits, flex.ref
            ),
            # `dep_ids`, NOT `covered_for_price`: the latter falls back to the
            # override's EXISTING coverage, so a plain plan move for a member
            # who already covers a doubled life would raise an ack-required
            # warning and 409 the apply — the opposite of the rule this code
            # states (having one is the roster's problem; electing one here
            # is this batch's).
            covered_dependant_ids=dep_ids,
        )
        codes_by_employee.setdefault(emp.id, set()).update(row.warnings)
        rows.append(row)
        plans.append(
            _Plan(
                employee=emp, product=product, change=change, ov=ov,
                tier_category_id=target_tier_id,
                dep_ids=dep_ids, dep_options=dep_options,
                clear_dependants=clear_dependants, tag=tag,
                sparse=sparse, row=row, financials_unresolved=unresolved,
            )
        )


def _ineligible_count(
    covered: list[str] | None,
    dependants: dict[str, Dependant],
    age_limits: dict[str, dict[str, int]] | None,
    ref: date | None,
) -> int:
    """How many of the dependants being covered sit outside the product's age
    window — the difference between profiling them with the limits and without,
    so the eligibility rule stays the one ``dependant_profiles_of`` applies."""
    if not covered or not age_limits or ref is None:
        return 0
    rows = [d for d in (dependants.get(i) for i in covered if i) if d is not None]
    if not rows:
        return 0
    everyone = dependant_profiles_of(rows, age_limits=None, ref=ref)
    eligible = dependant_profiles_of(rows, age_limits=age_limits, ref=ref)
    return max(0, len(everyone) - len(eligible))


def _flag_overdrafts(
    db: Session,
    py: PolicyYear,
    employees: list[Employee],
    plans: list[_Plan],
    *,
    idx: RosterIndex,
    deps: _Dependants,
    flex: _FlexContext,
    codes_by_employee: dict[str, set[str]],
) -> None:
    """Mark members whose coverage AFTER the batch draws more flex than they hold.

    This is the one check that cannot be made row by row: a wallet is a whole-
    member total, so it needs every product's spend — including the products this
    batch does not touch, which keep drawing exactly what they draw today. Their
    tags are recomputed rather than read off the stored override, because a
    member sitting on their cohort default has no override and therefore no
    stored tag, yet still spends.
    """
    if not flex.configured:
        return
    changed_by_employee: dict[str, dict[str, _Plan]] = {}
    for planned in plans:
        changed_by_employee.setdefault(planned.employee.id, {})[
            planned.product.id
        ] = planned
    if not changed_by_employee:
        return

    wallets = {
        e.id: e.flex_wallet_amount
        for e in employees
        if isinstance(e.flex_wallet_amount, (int, float))
    }
    at_risk = [e for e in employees if e.id in wallets and e.id in changed_by_employee]
    if not at_risk:
        return
    leave = _load_leave(db, py, [e.id for e in at_risk])

    for emp in at_risk:
        changed = changed_by_employee[emp.id]
        total = 0.0
        for product_id in idx.defaults.get(emp.id, {}):
            on_product = changed.get(product_id)
            if on_product is not None:
                if on_product.change.action == "decline":
                    continue  # declined coverage costs no flex
                tag = on_product.tag
            else:
                ov = idx.overrides.get((emp.id, product_id))
                if ov is not None and ov.declined:
                    continue
                base_cat = flex.baseline_cat.get((emp.id, product_id))
                tier_category_id = (
                    ov.tier_category_id
                    if ov is not None and ov.tier_category_id
                    else base_cat
                )
                plan_code = (
                    (ov.plan_code if ov else None)
                    or idx.default_plan(emp.id, product_id)
                )
                tag = _price_tag(
                    flex, product_id, emp,
                    declined=False,
                    base_cat=base_cat,
                    tier_category_id=tier_category_id,
                    to_plan=plan_code,
                    default_plan=idx.default_plan(emp.id, product_id),
                    covered=ov.covered_dependant_ids if ov else None,
                    dependants=deps.by_id,
                    dep_options=ov.dependant_option_ids if ov else None,
                    participation=_dependant_participation(
                        flex,
                        product_id,
                        base_cat=base_cat,
                        tier_category_id=tier_category_id,
                        plan_code=plan_code,
                    ),
                    age_limits=flex.age_limits(product_id),
                )
            total += tag or 0.0
        balance = round(wallets[emp.id] - total + leave.get(emp.id, 0.0), 2)
        if balance < -_EPSILON:
            codes_by_employee.setdefault(emp.id, set()).add(warn.FLEX_OVERDRAFT)
            for planned in changed.values():
                if warn.FLEX_OVERDRAFT not in planned.row.warnings:
                    planned.row.warnings.append(warn.FLEX_OVERDRAFT)


def _row(
    emp: Employee,
    outcome: str,
    reason: str | None,
    *,
    product_code: str | None = None,
    from_plan: str | None = None,
    to_plan: str | None = None,
    declined_before: bool = False,
    declined_after: bool = False,
    tag_before: float | None = None,
    tag_after: float | None = None,
    override_cleared: bool = False,
    si_before: float | None = None,
    si_after: float | None = None,
    premium_before: float | None = None,
    premium_after: float | None = None,
) -> BulkRowOutcome:
    return BulkRowOutcome(
        employee_id=emp.id,
        staff_id=emp.staff_id,
        employee_name=emp.employee_name,
        outcome=outcome,
        reason=reason,
        product_code=product_code,
        from_plan=from_plan,
        to_plan=to_plan,
        declined_before=declined_before,
        declined_after=declined_after,
        flex_price_tag_before=tag_before,
        flex_price_tag_after=tag_after,
        override_cleared=override_cleared,
        sum_insured_before=si_before,
        sum_insured_after=si_after,
        annual_premium_before=premium_before,
        annual_premium_after=premium_after,
    )


def _unchanged(
    ov: EmployeePlanOverride | None,
    sparse: bool,
    declined: bool,
    to_plan: str | None,
    tier_category_id: str | None,
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
        None if declined else tier_category_id,
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


def _dependant_participation(
    flex: _FlexContext,
    product_id: str,
    *,
    base_cat: str | None,
    tier_category_id: str | None,
    plan_code: str | None,
    detected: str | None = None,
) -> str | None:
    return effective_dependant_participation(
        flex.pricing,
        product_id,
        tier_key(tier_category_id, plan_code),
        detected
        or ("compulsory" if base_cat in flex.compulsory_dep_cats else "voluntary"),
    )


def _price_tag(
    flex: _FlexContext,
    product_id: str,
    emp: Employee,
    *,
    declined: bool,
    base_cat: str | None,
    tier_category_id: str | None,
    to_plan: str | None,
    default_plan: str | None,
    covered: list[str] | None,
    dependants: dict[str, Dependant],
    dep_options: dict[str, Any] | None,
    participation: str | None,
    age_limits: dict[str, dict[str, int]] | None,
) -> float | None:
    """The flex price tag this coverage draws.

    The selected plan is first resolved to its cohort tier, including sibling
    categories.  Pricing and the stored override therefore use the same exact
    tier key that ``summarize_employee`` will resolve during a later recompute.

    Dependants are resolved BY ID out of the batch-wide map, not from the
    member's electable list: the ids being priced come from the override's
    existing coverage as often as from a new dependant action, and the two sets
    are not the same (see ``_Dependants``).
    """
    if participation == "compulsory":
        dep_rows = [
            dep for dep in dependants.values() if dep.employee_id == emp.id
        ]
    elif participation == "voluntary":
        dep_rows = [
            dep
            for dep in (dependants.get(i) for i in (covered or []) if i)
            if dep is not None
        ]
    else:
        dep_rows = []
    dep_profiles = dependant_profiles_of(dep_rows, age_limits=age_limits, ref=flex.ref)
    spouse_count, child_count = profile_counts(dep_profiles)
    return member_coverage_tag(
        source_map=flex.source_map,
        rule=flex.drawdown_rule,
        pricing=flex.pricing,
        slip_idx=flex.slip_idx,
        family_slip_idx=flex.family_slip_idx,
        product_id=product_id,
        age=employee_age(emp, flex.ref) if flex.ref else None,
        declined=declined,
        tier_category_id=tier_category_id,
        plan_code=to_plan,
        default_tier_category_id=base_cat,
        default_plan=default_plan,
        spouse_count=spouse_count,
        child_count=child_count,
        dep_profiles=dep_profiles,
        dep_option_ids=dep_options,
        factor=flex_proration.factor_of(emp),
    )


def _groups(rows: list[BulkRowOutcome], changing: str) -> list[BulkChangeGroup]:
    buckets: dict[tuple[str | None, str | None, str | None, bool, bool], int] = {}
    for r in rows:
        if r.outcome != changing:
            continue
        # A revert and a plain move to the same plan are different operations —
        # one removes the override, the other writes it — so they must not
        # collapse into one "PLAN 1 → PLAN 2" line.
        key = (r.product_code, r.from_plan, r.to_plan, r.declined_after, r.override_cleared)
        buckets[key] = buckets.get(key, 0) + 1
    return [
        BulkChangeGroup(
            product_code=p, from_plan=f, to_plan=t, declined_after=d,
            reverted=cleared, count=n,
        )
        for (p, f, t, d, cleared), n in sorted(buckets.items(), key=lambda kv: -kv[1])
    ]


def _impact(
    rows: list[BulkRowOutcome],
    changing: str,
    flex_configured: bool,
    unresolved: int,
) -> BulkImpact:
    impact = BulkImpact()
    impact.financials_unresolved = unresolved
    for r in rows:
        if r.outcome != changing:
            continue
        impact.members_changing += 1
        # "Unpriced" means the tag could not be RESOLVED for coverage that draws
        # flex. A declined row has no tag by definition, and a company that does
        # not run flex has no price tags at all — counting either would tell the
        # broker a whole batch was unpriced when nothing was wrong.
        if flex_configured and r.flex_price_tag_after is None and not r.declined_after:
            impact.unpriced += 1
        impact.flex_price_tag_before += r.flex_price_tag_before or 0.0
        impact.flex_price_tag_after += r.flex_price_tag_after or 0.0
        # Cover and premium only total where BOTH sides resolved to this member.
        # A one-sided delta would read as the whole movement while silently
        # treating the unresolved side as zero. Declined cover is a real ZERO on
        # its side, not an unresolved figure — dropping a decline out of the
        # totals would report a batch that ends everyone's cover as no change in
        # premium at all.
        si_before = 0.0 if r.declined_before else r.sum_insured_before
        si_after = 0.0 if r.declined_after else r.sum_insured_after
        prem_before = 0.0 if r.declined_before else r.annual_premium_before
        prem_after = 0.0 if r.declined_after else r.annual_premium_after
        if None not in (si_before, si_after, prem_before, prem_after):
            impact.sum_insured_delta += (si_after or 0.0) - (si_before or 0.0)
            impact.annual_premium_delta += (prem_after or 0.0) - (prem_before or 0.0)
    impact.flex_price_tag_delta = round(
        impact.flex_price_tag_after - impact.flex_price_tag_before, 2
    )
    impact.flex_price_tag_before = round(impact.flex_price_tag_before, 2)
    impact.flex_price_tag_after = round(impact.flex_price_tag_after, 2)
    impact.sum_insured_delta = round(impact.sum_insured_delta, 2)
    impact.annual_premium_delta = round(impact.annual_premium_delta, 2)
    return impact


# ── Writing ─────────────────────────────────────────────────────────────────


def _execute(
    db: Session,
    py: PolicyYear,
    planned: _Plan,
    record_id: str | None,
    user: CurrentUser | None,
) -> dict[str, Any]:
    emp, product = planned.employee, planned.product
    before = restore_snapshot(planned.ov)
    if planned.sparse:
        _clear_override(db, emp, planned.ov, user)
        after = None
    else:
        after = _write_override(db, emp, py, planned, record_id, user)
    return {
        "employee_id": emp.id,
        "product_id": product.id,
        "product_code": product.code,
        "staff_id": emp.staff_id,
        "employee_name": emp.employee_name,
        "before": before,
        "after": after,
    }


def _clear_override(
    db: Session,
    emp: Employee,
    ov: EmployeePlanOverride | None,
    user: CurrentUser | None,
) -> None:
    """The bulk target equals the member's cohort default (or the change is an
    explicit revert) — remove any existing override so coverage reverts to (and
    stays) the sparse default. No-op when there's no override to clear."""
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
    planned: _Plan,
    record_id: str | None,
    user: CurrentUser | None,
) -> dict[str, Any] | None:
    # No dependant action normally preserves existing coverage. A target that
    # offers no dependant cover is the exception: clear both dependant fields.
    row, before = upsert_override(
        db,
        employee_id=emp.id,
        policy_year_id=py.id,
        client_id=emp.client_id,
        product_id=planned.product.id,
        product_code=planned.product.code,
        declined=planned.change.action == "decline",
        plan_code=planned.change.target_plan_code,
        # A bare plan code is stored with its exact sibling category when that
        # mapping is unambiguous in this member's cohort. Later recomputation
        # therefore uses the same category::plan pricing key as this write.
        tier_category_id=planned.tier_category_id,
        dependant_option_ids=planned.dep_options,
        flex_price_tag=planned.tag,
        source=OverrideSource.bulk_update,
        source_ref=record_id,
        modified_by=user.user_id if user else None,
        **(
            {"covered_dependant_ids": None}
            if planned.clear_dependants
            else {"covered_dependant_ids": planned.dep_ids}
            if planned.dep_ids is not None
            else {}
        ),
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
    return restore_snapshot(row)


# ── Undo ────────────────────────────────────────────────────────────────────


@dataclass
class UndoOutcome:
    rows: list[BulkRowOutcome] = field(default_factory=list)
    superseded: list[BulkRowOutcome] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    employee_ids: set[str] = field(default_factory=set)


def undo_batch(
    db: Session,
    py: PolicyYear,
    restore: list[dict[str, Any]],
    *,
    record_id: str,
    user: CurrentUser | None,
) -> UndoOutcome:
    """Put back what a batch replaced. Flushes; the caller commits.

    A pair whose override no longer matches what the batch LEFT is reported
    ``superseded`` and skipped — somebody has moved that member since, and
    restoring the old value would silently discard their work. That comparison
    is the whole safety of undo, so it is a full field-for-field match of the
    stored ``after`` snapshot, not a plan-code check.
    """
    out = UndoOutcome()
    if not restore:
        out.counts = {"applied": 0, "skipped": 0, "error": 0}
        return out

    product_ids = {e.get("product_id") for e in restore if e.get("product_id")}
    products = {
        p.id: p
        for p in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars()
    }
    employees = {
        e.id: e
        for e in db.execute(
            select(Employee).where(
                Employee.id.in_({e.get("employee_id") for e in restore})
            )
        ).scalars()
    }
    overrides = {
        (o.employee_id, o.product_id): o
        for o in db.execute(
            select(EmployeePlanOverride).where(
                EmployeePlanOverride.policy_year_id == py.id,
                EmployeePlanOverride.employee_id.in_(list(employees)),
            )
        ).scalars()
    }

    for entry in restore:
        emp = employees.get(entry.get("employee_id") or "")
        product = products.get(entry.get("product_id") or "")
        if emp is None or product is None:
            out.rows.append(
                BulkRowOutcome(
                    employee_id=entry.get("employee_id"),
                    staff_id=entry.get("staff_id"),
                    employee_name=entry.get("employee_name"),
                    product_code=entry.get("product_code"),
                    outcome="error",
                    reason="The member or product no longer exists in this benefit year.",
                )
            )
            continue
        ov = overrides.get((emp.id, product.id))
        before, after = entry.get("before"), entry.get("after")
        if restore_snapshot(ov) != after:
            out.superseded.append(
                _row(
                    emp, "skipped",
                    "Coverage has changed since this batch — left as it is.",
                    product_code=product.code,
                    from_plan=ov.plan_code if ov else None,
                    declined_before=bool(ov.declined) if ov else False,
                )
            )
            continue

        if before is None:
            _clear_override(db, emp, ov, user)
        else:
            _restore_override(db, emp, py, product, before, record_id, user)
        out.employee_ids.add(emp.id)
        out.rows.append(
            _row(
                emp, "applied", None,
                product_code=product.code,
                from_plan=(after or {}).get("plan_code"),
                to_plan=(before or {}).get("plan_code"),
                declined_before=bool((after or {}).get("declined")),
                declined_after=bool((before or {}).get("declined")),
                tag_before=(after or {}).get("flex_price_tag"),
                tag_after=(before or {}).get("flex_price_tag"),
                override_cleared=before is None,
            )
        )

    rows = out.rows + out.superseded
    out.counts = {"applied": 0, "skipped": 0, "error": 0}
    for r in rows:
        out.counts[r.outcome] = out.counts.get(r.outcome, 0) + 1
    return out


def _restore_override(
    db: Session,
    emp: Employee,
    py: PolicyYear,
    product: Product,
    snapshot: dict[str, Any],
    record_id: str,
    user: CurrentUser | None,
) -> None:
    """Write a stored snapshot back onto the (employee, product) override.

    The snapshot's OWN ``source`` and ``source_ref`` are restored, not this undo
    batch's: coverage that came from a confirmed enrolment has to read as
    enrolment coverage again, or the member's history would say a bulk tool
    chose it.
    """
    row, before = upsert_override(
        db,
        employee_id=emp.id,
        policy_year_id=py.id,
        client_id=emp.client_id,
        product_id=product.id,
        product_code=product.code,
        declined=bool(snapshot.get("declined")),
        plan_code=snapshot.get("plan_code"),
        tier_category_id=snapshot.get("tier_category_id"),
        covered_dependant_ids=snapshot.get("covered_dependant_ids"),
        dependant_option_ids=snapshot.get("dependant_option_ids"),
        flex_price_tag=snapshot.get("flex_price_tag"),
        source=snapshot.get("source") or OverrideSource.bulk_update,
        source_ref=snapshot.get("source_ref"),
        modified_by=user.user_id if user else None,
    )
    if user is not None:
        db.flush()
        write_audit(
            db, user, action="bulk_plan_override_undone",
            entity_type="employee_plan_override", entity_id=row.id,
            before=before, after=override_snapshot(row), employee_id=emp.id,
        )
