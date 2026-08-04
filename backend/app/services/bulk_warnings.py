"""Warnings raised by a bulk coverage change — "is everything about this batch
covered?".

A bulk change is applied to a population the broker has described with a rule, so
nobody reads the rows one by one. These are the facts that would have been
obvious member by member and are invisible in aggregate: a target plan the
member's cohort does not offer, a member mid-enrolment whose confirm will
overwrite this, coverage the member themselves chose, a wallet the batch
overdraws.

Two rules shape the whole module, and both were decided deliberately:

- **A warning never blocks; an UNACKNOWLEDGED warning does.** Brokers legitimately
  make changes that break cohort rules — a slip typo, a negotiated exception, a
  correction after a bad re-match. Refusing them outright would just push the work
  back to editing members one at a time. So apply returns 409
  ``unacknowledged_warnings`` with the codes, and the broker accepts them once;
  the acceptance is stored on the batch, so a year later the record says they were
  told.
- **Buckets count MEMBERS, not rows.** One member yields one row per product in
  the change set. "4 members are outside their cohort" is the fact a broker acts
  on; "6 rows" is an artefact of how many products the batch happens to touch.

Everything here is BATCHED. The per-member equivalents exist
(``electable_tiers_for_employee``, ``flex_price_summary``) and each costs several
queries, which at 2,000 members is the whole runtime of the batch — so the cohort
tiers come from one index per product (``tier_index_for_product``) and the open
enrolments from one join.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, PolicyYear
from app.models.enrollment import Enrollment
from app.models.enrollment_window import EnrollmentWindow, WindowStatus
from app.schemas.enrollment import BulkWarningBucket
from app.services.cohort_tiers import ProductTierIndex, tier_index_for_product
from app.services.plan_hydration import apply_gst_to_financials, member_financials
from app.services.product_terms import product_gst_multipliers
from app.services.roster_attributes import anb_from_attrs
from app.services.underwriting import free_cover_limits, nel_age_limits

# Codes. Kept as constants because they cross the wire twice — the preview
# reports them and the apply request echoes the accepted ones back.
OUTSIDE_COHORT = "outside_cohort"
OPEN_ENROLLMENT = "open_enrollment"
ENROLLMENT_CONFIRMED = "enrollment_confirmed"
FLEX_OVERDRAFT = "flex_overdraft"
DEPENDANT_INELIGIBLE = "dependant_ineligible"
UNPRICED = "unpriced"
UNDERWRITING_TRIGGERED = "underwriting_triggered"


@dataclass(frozen=True)
class WarningSpec:
    severity: str  # "warn" — needs a decision; "info" — worth knowing
    requires_ack: bool
    message: str


WARNING_SPECS: dict[str, WarningSpec] = {
    OUTSIDE_COHORT: WarningSpec(
        "warn",
        True,
        "The target plan is not one their cohort offers. Their coverage will be "
        "set anyway, but it will not match any tier the placement slip defines "
        "for them.",
    ),
    OPEN_ENROLLMENT: WarningSpec(
        "warn",
        True,
        "In an open enrolment period. Confirming (or the period closing) projects "
        "their elections over this change.",
    ),
    ENROLLMENT_CONFIRMED: WarningSpec(
        "warn",
        True,
        "Their current coverage came from a confirmed benefits selection — this "
        "overwrites what was chosen.",
    ),
    FLEX_OVERDRAFT: WarningSpec(
        "warn",
        True,
        "The resulting coverage draws more flex than their wallet holds.",
    ),
    DEPENDANT_INELIGIBLE: WarningSpec(
        "warn",
        True,
        "A dependant being covered is outside the product's age window.",
    ),
    UNPRICED: WarningSpec(
        "info",
        False,
        "No flex price could be resolved for the new coverage, so it draws "
        "nothing from the wallet.",
    ),
    UNDERWRITING_TRIGGERED: WarningSpec(
        "info",
        False,
        "Above the free cover limit or the non-evidence age — underwriting will "
        "be required. The queue is re-synced when this is applied.",
    ),
}

# ``no_change`` is deliberately NOT a warning: it is already its own row outcome
# and its own count, and reporting it in both places would have a broker reading
# the same 21 members twice under different words.


def ack_required(codes: set[str]) -> set[str]:
    """Of ``codes``, those a broker must accept before apply will run."""
    return {c for c in codes if WARNING_SPECS[c].requires_ack}


def buckets(
    by_employee: dict[str, set[str]],
) -> list[BulkWarningBucket]:
    """Collapse per-member codes into the summary the broker reads.

    Ordered warn-before-info, then by headcount — the ones needing a decision
    have to be at the top of the list, not sorted under a bigger info bucket.
    """
    counts: dict[str, int] = {}
    for codes in by_employee.values():
        for code in codes:
            counts[code] = counts.get(code, 0) + 1
    out = [
        BulkWarningBucket(
            code=code,
            severity=WARNING_SPECS[code].severity,
            requires_ack=WARNING_SPECS[code].requires_ack,
            count=n,
            message=WARNING_SPECS[code].message,
        )
        for code, n in counts.items()
        if code in WARNING_SPECS
    ]
    out.sort(key=lambda b: (0 if b.severity == "warn" else 1, -b.count, b.code))
    return out


@dataclass
class WarningContext:
    """Everything the per-row checks need, loaded once for the whole batch."""

    year_start: date
    # Members with an enrolment sitting in a window that is OPEN right now.
    open_enrollment_ids: set[str] = field(default_factory=set)
    # product_id -> the cohort tier index (see ``ProductTierIndex``).
    tier_indexes: dict[str, ProductTierIndex] = field(default_factory=dict)
    free_cover_limits: dict[str, float] = field(default_factory=dict)
    nel_age_limits: dict[str, int] = field(default_factory=dict)
    # Per-product GST gross-up for PREMIUMS. Stored figures are GST-exclusive
    # and every member-facing surface grosses them (see the GST section of
    # CLAUDE.md), so a raw premium here would sit 9% below the same movement on
    # the member's benefit statement and enrolment page.
    gst_multipliers: dict[str, float] = field(default_factory=dict)

    def underwriting_gated(self, product_id: str) -> bool:
        return (
            product_id in self.free_cover_limits or product_id in self.nel_age_limits
        )


def build_context(
    db: Session,
    py: PolicyYear,
    products: dict[str, str],
    employee_ids: list[str],
) -> WarningContext:
    """``products`` is ``{product_id: product_code}`` for the batch's changes."""
    ctx = WarningContext(
        year_start=py.start_date,
        free_cover_limits=free_cover_limits(db, py.id),
        nel_age_limits=nel_age_limits(db, py.id),
        # The product's EXPLICIT opinion only — the flex-scheme default is a
        # wallet concept, not a premium one.
        gst_multipliers=product_gst_multipliers(db, py.id),
    )
    for product_id, code in products.items():
        ctx.tier_indexes[product_id] = tier_index_for_product(db, py.id, product_id, code)
    if employee_ids:
        ctx.open_enrollment_ids = {
            row[0]
            for row in db.execute(
                select(Enrollment.employee_id)
                .join(EnrollmentWindow, Enrollment.window_id == EnrollmentWindow.id)
                .where(
                    Enrollment.policy_year_id == py.id,
                    Enrollment.employee_id.in_(employee_ids),
                    EnrollmentWindow.status == WindowStatus.open,
                )
            ).all()
        }
    return ctx


def target_category(
    ctx: WarningContext,
    product_id: str,
    baseline_category_id: str | None,
    plan_code: str | None,
) -> Category | None:
    """The cohort tier a target plan code resolves to for this member's cohort."""
    index = ctx.tier_indexes.get(product_id)
    if index is None:
        return None
    return index.category_for_plan(baseline_category_id, plan_code)


@dataclass(frozen=True)
class MemberFigures:
    sum_insured: float | None
    annual_premium: float | None
    # Does this tier QUOTE a cover figure at all? A reimbursement product
    # (GP/SP/dental/hospital) has none by design — its entitlement is the
    # schedule of benefits — so a missing figure there is the product's shape,
    # not a gap. Only a tier that states a basis or a sum insured and still
    # won't reduce to this member (no salary on file, a relative basis) is
    # worth reporting as unresolved.
    quoted: bool


def member_figures(
    category: Category | None,
    age: int | None,
    employee: Employee,
    gst_multiplier: float = 1.0,
) -> MemberFigures:
    """Cover and premium for ONE member on one tier.

    Always through ``member_financials`` with the member's own age and roster
    attributes: a category's stored ``sum_insured`` is a cohort aggregate
    (headcount x basis), and a salary-multiple basis has no figure at all until
    it meets this member's salary. Unresolvable → ``None``, which renders "—".
    """
    if category is None or not isinstance(category.plan_assignments, dict):
        return MemberFigures(None, None, False)
    pa = category.plan_assignments
    quoted = pa.get("basis") is not None or pa.get("sum_insured") is not None
    fin = member_financials(pa, age, employee.attribute_values)
    if fin is None:
        return MemberFigures(None, None, quoted)
    # Through the shared helper, so the gross-up rule (premiums scale, cover
    # amounts don't) has one implementation.
    fin = apply_gst_to_financials(fin, gst_multiplier)
    return MemberFigures(fin.sum_insured, fin.annual_premium, quoted)


def row_codes(
    ctx: WarningContext,
    *,
    product_id: str,
    employee: Employee,
    baseline_category_id: str | None,
    ov_source: str | None,
    action: str,
    target_plan_code: str | None,
    sum_insured_after: float | None,
    price_tag_after: float | None,
    declined_after: bool,
    flex_configured: bool,
    ineligible_dependants: int,
) -> list[str]:
    """Every warning for one (member, product) row except the flex overdraft,
    which is a whole-member total and is resolved after all the rows exist."""
    codes: list[str] = []

    if action == "set_plan":
        index = ctx.tier_indexes.get(product_id)
        electable = (
            index.plan_codes_for(baseline_category_id) if index is not None else None
        )
        # ``None`` means the member's cohort could not be resolved at all — that
        # is a matching gap, not evidence that the plan is unavailable to them,
        # and reporting it here would put every unmatched member in a bucket
        # about cohort rules.
        if electable is not None and target_plan_code not in electable:
            codes.append(OUTSIDE_COHORT)

    if employee.id in ctx.open_enrollment_ids:
        codes.append(OPEN_ENROLLMENT)

    # The override's own provenance, which is exactly what "the member chose
    # this" means: the enrolment projection is the only writer of that source.
    if ov_source == "enrollment":
        codes.append(ENROLLMENT_CONFIRMED)

    if ineligible_dependants:
        codes.append(DEPENDANT_INELIGIBLE)

    if flex_configured and not declined_after and price_tag_after is None:
        codes.append(UNPRICED)

    if not declined_after and ctx.underwriting_gated(product_id):
        fcl = ctx.free_cover_limits.get(product_id)
        nel_age = ctx.nel_age_limits.get(product_id)
        anb = anb_from_attrs(
            {**(employee.attribute_values or {}), **(employee.derived_attribute_values or {})},
            ctx.year_start,
        )
        over_fcl = (
            fcl is not None
            and sum_insured_after is not None
            and sum_insured_after > fcl
        )
        over_age = nel_age is not None and anb is not None and anb >= nel_age
        if over_fcl or over_age:
            codes.append(UNDERWRITING_TRIGGERED)

    return codes


__all__ = [
    "DEPENDANT_INELIGIBLE",
    "ENROLLMENT_CONFIRMED",
    "FLEX_OVERDRAFT",
    "OPEN_ENROLLMENT",
    "OUTSIDE_COHORT",
    "UNDERWRITING_TRIGGERED",
    "UNPRICED",
    "WARNING_SPECS",
    "WarningContext",
    "ack_required",
    "buckets",
    "build_context",
    "member_figures",
    "row_codes",
    "target_category",
]
