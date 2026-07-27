"""Underwriting sync — Non-Evidence-Limit triggers + report amounts.

``refresh_underwriting_cases`` compares every member's (and covered
dependant's) eligible sum insured on lump-sum products against the product's
Non-Evidence Limit — the dollar free cover limit
(``ProductTerm.free_cover_limit``) and the age gate
(``ProductTerm.nel_age_limit``, ANB) — and keeps the review/case set in sync:

- SI trigger: eligible SI above max(FCL, the life's last covered SI) — only
  the amount above that threshold needs the insurer's medical underwriting.
- Age trigger: the life's ANB is at/above the age gate — a NEW hire has
  nothing guaranteed (the whole SI is underwritten); an EXISTING life keeps
  their last covered SI and only the increase is underwritten.

"Last covered SI" is the in-force amount from the previous benefit year for
the same person (matched by NRIC, falling back to staff id / dependant
name+relationship). With no previous year on record everyone is treated as
existing with unknown history (guaranteed falls back to the FCL — a first
platform year is a renewal book, not a cohort of new hires).

Case lines are grouped into one ``UnderwritingReview`` per (life, insurer) —
the broker opens ONE case with AIA covering every AIA product the life
triggered on. New excesses open a *pending* line (auto-covered at its
guaranteed SI), recorded decisions persist, and pending lines whose trigger
vanished are removed. Pure flush — the caller owns audit + commit (mirrors
matching).

Insurer listings read lines through ``load_cases`` / ``report_uw_amounts``:
no case → accepted = eligible, pending 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Dependant,
    Employee,
    PolicyYear,
    Product,
    ProductTerm,
    UnderwritingCase,
    UnderwritingReview,
)
from app.models.dependant import (
    DEPENDANT_STATUS_ACTIVE,
    DEPENDANT_STATUS_TERMINATED,
)
from app.models.underwriting_case import (
    DECIDED_UW_STATUSES,
    ReviewStatus,
    UnderwritingStatus,
    normalize_uw_status,
)
from app.services.flex_membership import classify_relationship
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    NAME_KEYS,
    REL_KEYS,
    anb_from_attrs,
    first_value,
    nric_from_attrs,
)

# Key: (subject_id, product_id) — subject is the employee OR dependant id.
CaseMap = dict[tuple[str, str], UnderwritingCase]

# Cross-year identity key for a life (NRIC-first; see _employee_key/_dep_key).
LifeKey = tuple


def load_cases(db: Session, policy_year_id: str) -> CaseMap:
    rows = db.execute(
        select(UnderwritingCase).where(
            UnderwritingCase.policy_year_id == policy_year_id
        )
    ).scalars().all()
    return {
        ((c.employee_id or c.dependant_id or ""), c.product_id): c
        for c in rows
        # Defensive: a case must name exactly one life; a subject-less row
        # would collapse onto the ("", product) key and clobber another.
        if c.employee_id or c.dependant_id
    }


def report_uw_amounts(
    eligible: float, fcl: float | None, case: UnderwritingCase | None
) -> tuple[float, float]:
    """(pending U/W, last accepted) for insurer listings — refresh-INDEPENDENT.

    A recorded insurer DECISION always wins: the accepted figure stands
    (capped at eligible), nothing pending. An undecided case is in force at
    its guaranteed SI with the excess pending (``postponed`` counts as
    undecided — the insurer deferred, the excess is still awaiting them).
    With no case at all the auto position is computed from the LIVE free
    cover limit + LIVE eligible, so the report is correct the moment an FCL
    or salary changes, without requiring a prior sync run.
    """
    if case is not None:
        status = normalize_uw_status(case.status)
        if status in DECIDED_UW_STATUSES:
            return 0.0, min(case.accepted_si, eligible)
        guaranteed = (
            case.guaranteed_si
            if case.guaranteed_si is not None
            # Legacy pending rows (pre review model) carried the FCL in
            # accepted_si.
            else case.accepted_si
        )
        guaranteed = min(max(guaranteed, 0.0), eligible)
        return eligible - guaranteed, guaranteed
    if fcl is None or eligible <= fcl:
        return 0.0, eligible
    return eligible - fcl, fcl


def free_cover_limits(db: Session, policy_year_id: str) -> dict[str, float]:
    """{product_id: FCL} for products with an explicit dollar limit."""
    rows = db.execute(
        select(ProductTerm.product_id, ProductTerm.free_cover_limit).where(
            ProductTerm.policy_year_id == policy_year_id,
            ProductTerm.free_cover_limit.isnot(None),
        )
    ).all()
    return {pid: float(fcl) for pid, fcl in rows}


def nel_age_limits(db: Session, policy_year_id: str) -> dict[str, int]:
    """{product_id: ANB age gate} for products with an explicit NEL age."""
    rows = db.execute(
        select(ProductTerm.product_id, ProductTerm.nel_age_limit).where(
            ProductTerm.policy_year_id == policy_year_id,
            ProductTerm.nel_age_limit.isnot(None),
        )
    ).all()
    return {pid: int(age) for pid, age in rows}


@dataclass
class RefreshResult:
    opened: int = 0
    updated: int = 0
    removed: int = 0
    open_cases: int = 0


@dataclass
class _Life:
    subject_id: str
    is_employee: bool
    key: LifeKey
    anb: int | None = None
    # True only when a previous benefit year exists and this life wasn't in it.
    new_life: bool = False
    # The employee whose coverage hydration surfaces this life (itself for an
    # employee, the sponsor for a dependant) — the unit a scoped run works in.
    sponsor_id: str | None = None


@dataclass
class _History:
    """Previous benefit year's positions, keyed by cross-year life identity."""

    known: bool = False  # False = no previous year on record
    present: set[LifeKey] = field(default_factory=set)
    # (life key, product_id) → in-force (accepted) SI last year.
    last_covered: dict[tuple[LifeKey, str], float] = field(default_factory=dict)


def _employee_key(emp: Employee) -> LifeKey:
    nric = nric_from_attrs(emp.attribute_values or {})
    if nric:
        return ("e", nric)
    return ("e", "staff", (emp.staff_id or "").strip().lower())


def _dep_key(dep: Dependant, emp_key: LifeKey | None) -> LifeKey:
    nric = nric_from_attrs(dep.attribute_values or {}, DEPENDANT_ID_KEYS)
    if nric:
        return ("d", nric)
    attrs = dep.attribute_values or {}
    name = (first_value(attrs, NAME_KEYS) or "").strip().lower()
    role = classify_relationship(first_value(attrs, REL_KEYS)) or ""
    return ("d", emp_key, name, role)


def _year_lives(
    db: Session, py: PolicyYear, only_employee_ids: set[str] | None = None
) -> tuple[dict[str, _Life], dict[str, LifeKey]]:
    """All lives of a policy year: {subject_id: _Life} + {employee_id: key}.

    ``only_employee_ids`` narrows to specific households (their dependants come
    along) for the scoped per-member re-sync.

    Dependant statuses mirror ``insurer_listings._dep_reportable``: ACTIVE plus
    in-period leavers. A mid-year terminated dependant still has eligible SI on
    the listing, so omitting them here would leave their case with no matching
    life — and the sync's cleanup would delete an in-flight underwriting record.
    """
    lives: dict[str, _Life] = {}
    emp_keys: dict[str, LifeKey] = {}
    ref = py.start_date
    emp_stmt = select(Employee).where(Employee.policy_year_id == py.id)
    if only_employee_ids is not None:
        emp_stmt = emp_stmt.where(Employee.id.in_(only_employee_ids))
    employees = db.execute(emp_stmt).scalars().all()
    for emp in employees:
        key = _employee_key(emp)
        emp_keys[emp.id] = key
        lives[emp.id] = _Life(
            subject_id=emp.id,
            is_employee=True,
            key=key,
            anb=anb_from_attrs(emp.attribute_values, ref),
            sponsor_id=emp.id,
        )
    dep_stmt = select(Dependant).where(
        Dependant.policy_year_id == py.id,
        Dependant.status.in_(
            [DEPENDANT_STATUS_ACTIVE, DEPENDANT_STATUS_TERMINATED]
        ),
    )
    if only_employee_ids is not None:
        dep_stmt = dep_stmt.where(Dependant.employee_id.in_(only_employee_ids))
    for dep in db.execute(dep_stmt).scalars().all():
        key = _dep_key(dep, emp_keys.get(dep.employee_id or ""))
        lives[dep.id] = _Life(
            subject_id=dep.id,
            is_employee=False,
            key=key,
            anb=anb_from_attrs(dep.attribute_values, ref),
            sponsor_id=dep.employee_id,
        )
    return lives, emp_keys


def _previous_policy_year(db: Session, py: PolicyYear) -> PolicyYear | None:
    if py.start_date is None:
        return None
    return db.execute(
        select(PolicyYear)
        .where(
            PolicyYear.client_id == py.client_id,
            PolicyYear.id != py.id,
            PolicyYear.start_date < py.start_date,
        )
        .order_by(PolicyYear.start_date.desc())
    ).scalars().first()


def _load_history(
    db: Session, py: PolicyYear, wanted_keys: set[LifeKey] | None = None
) -> _History:
    """Last year's in-force SI per (life, product) — the "last covered sum
    assured" that shapes both the SI threshold and the age-trigger guarantee.

    ``wanted_keys`` limits the (expensive) previous-year hydration to the lives
    the caller is actually re-syncing.
    """
    from app.services.insurer_listings import eligible_amounts

    prev = _previous_policy_year(db, py)
    if prev is None:
        return _History(known=False)
    lives, _ = _year_lives(db, prev)
    present = {life.key for life in lives.values()}
    if not present:
        # A previous policy year exists but carries no roster (config-only /
        # not yet uploaded). Treating that as "known" would mark EVERY current
        # life a new hire, and the age gate would then underwrite their whole
        # sum insured instead of just the increase. Unknown history is the
        # safe read.
        return _History(known=False)
    history = _History(known=True, present=present)
    if wanted_keys is not None:
        # A dependant's amounts only surface when their sponsor is hydrated, so
        # scope last year's hydration by the employee behind each wanted life.
        prev_scope = {
            life.sponsor_id
            for life in lives.values()
            if life.key in wanted_keys and life.sponsor_id
        }
        if not prev_scope:
            return history
    else:
        prev_scope = None
    prev_fcl = free_cover_limits(db, prev.id)
    prev_cases = load_cases(db, prev.id)
    for (subject_id, product_id, _is_emp), eligible in eligible_amounts(
        db, prev, prev_scope
    ).items():
        life = lives.get(subject_id)
        if life is None:
            continue
        _pending, accepted = report_uw_amounts(
            eligible, prev_fcl.get(product_id), prev_cases.get((subject_id, product_id))
        )
        history.last_covered[(life.key, product_id)] = accepted
    return history


def _guaranteed_for(
    eligible: float,
    fcl: float | None,
    age_limit: int | None,
    life: _Life,
    last_covered: float | None,
) -> float | None:
    """The auto-covered SI for a life on one product, or None when nothing
    needs underwriting (no gate applies / eligible within the threshold)."""
    over_age = (
        age_limit is not None and life.anb is not None and life.anb >= age_limit
    )
    if over_age:
        if life.new_life:
            guaranteed = 0.0
        elif last_covered is not None:
            guaranteed = min(last_covered, eligible)
        else:
            # Existing life, unknown history (first platform year): fall back
            # to the FCL — broker can correct per case.
            guaranteed = min(fcl, eligible) if fcl is not None else 0.0
    elif fcl is not None:
        guaranteed = min(max(fcl, last_covered or 0.0), eligible)
    else:
        return None
    return guaranteed if eligible > guaranteed else None


def adopt_orphan_cases(db: Session, policy_year_id: str) -> int:
    """Attach pre-review-model case rows to a review. Flushes, never commits.

    ``review_id`` was added nullable with no data backfill (a data migration
    can't reach the per-firm Postgres schemas anyway), so cases written before
    the insurer-grouped model have none — and the queue, which reads reviews,
    would show nothing at all until something happened to trigger a full sync.
    This is the cheap self-healing path: group the orphans exactly as the sync
    would, touching no amounts. Returns the number adopted.
    """
    orphans = list(
        db.execute(
            select(UnderwritingCase).where(
                UnderwritingCase.policy_year_id == policy_year_id,
                UnderwritingCase.review_id.is_(None),
            )
        ).scalars().all()
    )
    if not orphans:
        return 0
    insurer_by_product = {
        p.id: (p.insurer or "").strip()
        for p in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in orphans})
            )
        ).scalars()
    }
    reviews = {
        ((r.employee_id or r.dependant_id or ""), r.insurer): r
        for r in db.execute(
            select(UnderwritingReview).where(
                UnderwritingReview.policy_year_id == policy_year_id
            )
        ).scalars().all()
        if r.employee_id or r.dependant_id
    }
    adopted = 0
    for case in orphans:
        subject = case.employee_id or case.dependant_id
        if not subject:
            continue  # subject-less row: not a life, nothing to review
        insurer = insurer_by_product.get(case.product_id, "")
        review = reviews.get((subject, insurer))
        if review is None:
            review = UnderwritingReview(
                client_id=case.client_id,
                policy_year_id=policy_year_id,
                insurer=insurer,
                employee_id=case.employee_id,
                dependant_id=case.dependant_id,
                status=(
                    ReviewStatus.completed
                    if normalize_uw_status(case.status) in DECIDED_UW_STATUSES
                    else ReviewStatus.pending_requirements
                ),
            )
            db.add(review)
            db.flush([review])
            reviews[(subject, insurer)] = review
        case.review_id = review.id
        adopted += 1
    db.flush()
    return adopted


def refresh_underwriting_cases(
    db: Session,
    policy_year: PolicyYear,
    employee_ids: set[str] | None = None,
) -> RefreshResult:
    """Sync reviews + case lines with resolved coverage. Flushes, never commits.

    ``employee_ids`` limits the run to specific households (their dependants
    included): coverage hydration, history and the stale-case cleanup all scope
    to them, so a per-member trigger (one enrollment confirm) costs one
    household rather than two whole-roster hydrations. None = the whole year.
    """
    # Local import: insurer_listings imports load_cases/report_uw_amounts here.
    from app.services.insurer_listings import eligible_amounts

    fcl_by_product = free_cover_limits(db, policy_year.id)
    age_by_product = nel_age_limits(db, policy_year.id)
    cases = load_cases(db, policy_year.id)
    reviews = {
        ((r.employee_id or r.dependant_id or ""), r.insurer): r
        for r in db.execute(
            select(UnderwritingReview).where(
                UnderwritingReview.policy_year_id == policy_year.id
            )
        ).scalars().all()
        if r.employee_id or r.dependant_id
    }
    result = RefreshResult()

    gated = bool(fcl_by_product or age_by_product)
    eligibles = eligible_amounts(db, policy_year, employee_ids) if gated else {}
    lives: dict[str, _Life] = {}
    if eligibles:
        lives, _ = _year_lives(db, policy_year, employee_ids)
        history = _load_history(
            db, policy_year, {life.key for life in lives.values()}
        )
        if history.known:
            for life in lives.values():
                life.new_life = life.key not in history.present
    else:
        history = _History(known=False)
    # Subjects this run is allowed to retire a case for. An unscoped run owns
    # every case in the year; a scoped one must leave other households alone
    # (their coverage wasn't recomputed, so "not seen" proves nothing).
    in_scope: set[str] | None = None
    if employee_ids is not None:
        in_scope = set(lives) if lives else set()
        if not lives:
            scoped, _ = _year_lives(db, policy_year, employee_ids)
            in_scope = set(scoped)

    insurer_by_product: dict[str, str] = {}
    product_ids = {pid for (_s, pid, _e) in eligibles} | {
        c.product_id for c in cases.values()
    }
    if product_ids:
        insurer_by_product = {
            p.id: (p.insurer or "").strip()
            for p in db.execute(
                select(Product).where(Product.id.in_(product_ids))
            ).scalars()
        }

    def review_for(life_subject: str, is_employee: bool, insurer: str) -> UnderwritingReview:
        review = reviews.get((life_subject, insurer))
        if review is None:
            review = UnderwritingReview(
                client_id=policy_year.client_id,
                policy_year_id=policy_year.id,
                insurer=insurer,
                employee_id=life_subject if is_employee else None,
                dependant_id=None if is_employee else life_subject,
                status=ReviewStatus.pending_requirements,
            )
            db.add(review)
            db.flush([review])
            reviews[(life_subject, insurer)] = review
        return review

    seen: set[tuple[str, str]] = set()
    for (subject_id, product_id, is_employee), eligible in eligibles.items():
        life = lives.get(subject_id)
        if life is None:
            continue
        last = history.last_covered.get((life.key, product_id))
        guaranteed = _guaranteed_for(
            eligible,
            fcl_by_product.get(product_id),
            age_by_product.get(product_id),
            life,
            last,
        )
        if guaranteed is None:
            continue
        seen.add((subject_id, product_id))
        insurer = insurer_by_product.get(product_id, "")
        review = review_for(subject_id, is_employee, insurer)
        case = cases.get((subject_id, product_id))
        if case is None:
            db.add(UnderwritingCase(
                client_id=policy_year.client_id,
                policy_year_id=policy_year.id,
                review_id=review.id,
                product_id=product_id,
                employee_id=subject_id if is_employee else None,
                dependant_id=None if is_employee else subject_id,
                eligible_si=eligible,
                guaranteed_si=guaranteed,
                accepted_si=guaranteed,
                status=UnderwritingStatus.pending,
            ))
            result.opened += 1
            # A fresh excess reopens a closed-out review — the insurer must
            # look at this life again.
            if review.status in (ReviewStatus.completed, ReviewStatus.cancelled):
                review.status = ReviewStatus.pending_requirements
        else:
            changed = case.review_id != review.id
            case.review_id = review.id
            if normalize_uw_status(case.status) not in DECIDED_UW_STATUSES:
                if case.eligible_si != eligible:
                    case.eligible_si = eligible
                    changed = True
                # An undecided line's auto-covered amount tracks the computed
                # guarantee (FCL moves, salary moves, history resolves) unless
                # the broker pinned it. A decided line keeps the insurer's
                # figure (report_uw_amounts caps it at read time).
                if not case.guaranteed_overridden and (
                    case.guaranteed_si != guaranteed
                    or case.accepted_si != guaranteed
                ):
                    case.guaranteed_si = guaranteed
                    case.accepted_si = guaranteed
                    changed = True
            if changed:
                result.updated += 1

    # Undecided lines whose life no longer exceeds any gate are moot; decided
    # lines stay as history (report_uw_amounts caps them so they can't
    # overstate). Legacy lines (review_id None) get adopted into a review.
    for key, case in list(cases.items()):
        if key in seen:
            continue
        if in_scope is not None and key[0] not in in_scope:
            continue  # another household — this run didn't evaluate them
        if normalize_uw_status(case.status) not in DECIDED_UW_STATUSES:
            db.delete(case)
            del cases[key]
            result.removed += 1
        elif case.review_id is None:
            subject_id, product_id = key
            case.review_id = review_for(
                subject_id,
                case.employee_id is not None,
                insurer_by_product.get(product_id, ""),
            ).id

    # Reviews left with no lines are moot. Delete explicitly — the review→case
    # FK cascade doesn't exist on upgraded databases.
    #
    # BUT never discard broker work: a review carries requirements text and a
    # workflow position the broker typed. Raising an FCL past a member (which
    # retires their pending lines) would otherwise silently delete the notes of
    # a case that's mid-conversation with the insurer. Those are CANCELLED
    # instead, and `review_for` reopens them if the excess returns.
    db.flush()
    live_review_ids = set(
        db.execute(
            select(UnderwritingCase.review_id).where(
                UnderwritingCase.policy_year_id == policy_year.id,
                UnderwritingCase.review_id.isnot(None),
            )
        ).scalars().all()
    )
    for review in list(reviews.values()):
        if review.id in live_review_ids:
            continue
        subject = review.employee_id or review.dependant_id or ""
        if in_scope is not None and subject not in in_scope:
            continue
        touched = bool(review.requirements) or review.modified_by is not None
        if touched:
            review.status = ReviewStatus.cancelled
        else:
            db.delete(review)

    db.flush()
    result.open_cases = len(seen)
    return result
