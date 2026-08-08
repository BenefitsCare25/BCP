"""Underwriting report — one row per underwritten life + product (internal).

The broker's working record of every medical-underwriting case in a benefit
year: who is above a product's Non-Evidence Limit, which insurer the case sits
with, where it is in the workflow, and what the insurer decided. Layout follows
the firm's existing underwriting spreadsheet (demographic block → insurer /
product → status / decision), then appends the figures and notes that only the
platform holds, so the export can replace the hand-kept file rather than sit
beside it.

Grain is the CASE LINE, not the review: a life triggering on five AIA products
is five rows sharing one review's status and requirements — the same shape the
manual file uses, and what makes the sheet pivotable by product.

Amounts come from ``underwriting.case_amounts``, the same helper the queue
screen renders, so a case can never show one set of figures on screen and
another in the export. They are the line's own snapshot (``eligible_si`` as of
the last sync), NOT a live coverage recomputation — a stale line is repaired by
"Sync with coverage", never by a report quietly disagreeing with the queue.

Reviews whose lines have all been retired (an FCL raised past the member) are
cancelled rather than deleted, so they are emitted with the product columns
blank instead of vanishing from the broker's record.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Dependant,
    Employee,
    PolicyYear,
    Product,
    UnderwritingCase,
    UnderwritingReview,
)
from app.models.underwriting_case import (
    DECISION_LABELS,
    REVIEW_STATUS_LABELS,
    normalize_uw_status,
)
from app.services.insurer_listings import policy_period
from app.services.insurer_reports import (
    append_safe,
    as_date,
    autosize,
    bold_header,
    last_day_of_service,
    naive,
)
from app.services.product_insurer import insurer_map
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    DOB_KEYS,
    EMPLOYEE_ID_KEYS,
    NAME_KEYS,
    REL_KEYS,
    anb_from_attrs,
    first_value,
    mask_nric,
)
from app.services.underwriting import (
    case_amounts,
    free_cover_limits,
    nel_age_limits,
)

# The firm's spreadsheet layout, then the platform-only columns. "No. of
# Reminders Sent" is carried for layout parity but left BLANK: chaser emails
# aren't tracked anywhere in the system, and writing 0 would assert none were
# sent rather than admitting the platform doesn't know.
HEADER = [
    "Entity",
    "Staff ID",
    "Employee Name",
    "Identification No.",
    "Date of Hire",
    "Last Day of Service",
    "Dependant Name",
    "Dependant Relationship",
    "Dependant Identification No.",
    "Name of Insurer",
    "Product Type",
    "Product Name",
    "UW Status",
    "UW Decision",
    "UW Decision Date",
    "No. of Reminders Sent",
    # ── platform-only, for internal reconciliation ──────────────────────
    "Life Underwritten",
    "Date of Birth",
    "Age Next Birthday",
    "Free Cover Limit",
    "NEL Age Limit",
    "Eligible Sum Insured",
    "Guaranteed Sum Insured",
    "Sum Insured Pending U/W",
    "Last Accepted Sum Insured",
    "Requirements",
    "Case Remarks",
    "Case Opened",
    "Last Updated",
    "Policy Period",
]


def _ident(attrs: dict[str, Any], keys: tuple[str, ...], masked: bool) -> str:
    raw = first_value(attrs, keys)
    return mask_nric(raw) if masked else (raw or "")


def _stamp(*values: datetime | None) -> datetime | None:
    """Latest of the given timestamps as a tz-naive cell.

    NOT collapsed to a bare date: ``created_at``/``updated_at`` are stored
    tz-AWARE (UTC) on Postgres, so an evening-SGT edit lands on the previous
    UTC calendar day — a date column would report it silently off by one. The
    full timestamp at least makes the offset visible, and matches how every
    other report in the toolkit writes model timestamps (``naive``).
    """
    stamps = [naive(v) for v in values if v is not None]
    return max(stamps) if stamps else None


def _load(db: Session, model, ids: set[str]) -> dict[str, Any]:
    if not ids:
        return {}
    return {
        row.id: row
        for row in db.execute(select(model).where(model.id.in_(ids))).scalars().all()
    }


def build_underwriting_report(
    db: Session, py: PolicyYear, masked: bool = True
) -> Workbook:
    """Every underwriting case line in ``py``, across all insurers."""
    reviews = list(
        db.execute(
            select(UnderwritingReview).where(
                UnderwritingReview.policy_year_id == py.id
            )
        ).scalars().all()
    )
    cases = list(
        db.execute(
            select(UnderwritingCase).where(UnderwritingCase.policy_year_id == py.id)
        ).scalars().all()
    )

    reviews_by_id = {r.id: r for r in reviews}
    lines_by_review: dict[str, list[UnderwritingCase]] = {}
    for c in cases:
        # A line written before the insurer-grouped model (review_id NULL) is
        # still a real, decidable case — group it under "" so it prints with
        # blank workflow columns rather than dropping off the record.
        lines_by_review.setdefault(c.review_id or "", []).append(c)

    products = _load(db, Product, {c.product_id for c in cases})
    dep_ids = {r.dependant_id for r in reviews if r.dependant_id}
    dep_ids |= {c.dependant_id for c in cases if c.dependant_id}
    dependants = _load(db, Dependant, dep_ids)
    emp_ids = {r.employee_id for r in reviews if r.employee_id}
    emp_ids |= {c.employee_id for c in cases if c.employee_id}
    emp_ids |= {d.employee_id for d in dependants.values() if d.employee_id}
    employees = _load(db, Employee, emp_ids)

    fcl_by_product = free_cover_limits(db, py.id)
    age_by_product = nel_age_limits(db, py.id)
    insurer_by_product = insurer_map(db, py.id, products.values())
    period = policy_period(py)
    renewal = py.start_date

    def _subject(
        employee_id: str | None, dependant_id: str | None
    ) -> tuple[Employee | None, Dependant | None]:
        if dependant_id:
            dep = dependants.get(dependant_id)
            emp = employees.get(dep.employee_id) if dep and dep.employee_id else None
            return emp, dep
        return employees.get(employee_id or ""), None

    rows: list[tuple[tuple[Any, ...], list[object]]] = []

    def _emit(
        review: UnderwritingReview | None, case: UnderwritingCase | None
    ) -> None:
        source = case or review
        if source is None:
            return
        emp, dep = _subject(source.employee_id, source.dependant_id)
        # Whether the life is a dependant is decided by the CASE, not by whether
        # the dependant row loaded — an unresolvable id must print as a
        # dependant with blank details, never be relabelled an employee.
        is_dependant = bool(source.dependant_id)
        attrs = (emp.attribute_values or {}) if emp else {}
        dattrs = (dep.attribute_values or {}) if dep else {}
        life_attrs = dattrs if is_dependant else attrs
        product = products.get(case.product_id) if case else None
        meta = (product.product_metadata or {}) if product else {}
        # The insurer the review is OPENED WITH — the year's configured insurer
        # is only a fallback for a line with no review yet. Preferring it over a
        # blank ``review.insurer`` would print an insurer the workflow record
        # isn't filed under, and disagree with the queue screen until the next
        # sync re-keys the review.
        insurer = (
            review.insurer
            if review
            else (insurer_by_product.get(product.id, "") if product else "")
        )
        # The roster's own wording, not the classified bucket — this sheet is
        # reconciled against the insurer's file, which quotes what was submitted.
        relationship = (
            (first_value(dattrs, REL_KEYS) or "Dependant") if is_dependant else ""
        )

        guaranteed = pending = accepted = eligible = None
        decision = ""
        if case is not None:
            guaranteed, pending, accepted = case_amounts(case)
            eligible = case.eligible_si
            decision = DECISION_LABELS.get(
                normalize_uw_status(case.status), case.status
            )

        row: list[object] = [
            first_value(attrs, ("entity", "company", "subsidiary")) or "",
            (emp.staff_id if emp else "") or "",
            (emp.employee_name if emp else "") or "",
            _ident(attrs, EMPLOYEE_ID_KEYS, masked),
            as_date(first_value(attrs, ("date_of_hire", "hire_date"))),
            last_day_of_service(emp) if emp else None,
            (first_value(dattrs, NAME_KEYS) or "") if is_dependant else "",
            relationship,
            _ident(dattrs, DEPENDANT_ID_KEYS, masked) if is_dependant else "",
            insurer,
            (str(meta.get("report_code") or product.code) if product else ""),
            (product.display_name if product else ""),
            REVIEW_STATUS_LABELS.get(review.status, review.status) if review else "",
            decision,
            case.decided_on if case else None,
            "",  # No. of Reminders Sent — not tracked (see HEADER)
            "Dependant" if is_dependant else "Employee",
            as_date(first_value(life_attrs, DOB_KEYS)),
            anb_from_attrs(life_attrs, renewal) if renewal else None,
            fcl_by_product.get(case.product_id) if case else None,
            age_by_product.get(case.product_id) if case else None,
            eligible,
            guaranteed,
            pending,
            accepted,
            (review.requirements if review else "") or "",
            (case.remarks if case else "") or "",
            _stamp(review.created_at) if review else None,
            # The row joins review + case, so either edit is "activity": a
            # workflow move (the commonest broker action) only touches the
            # review, and reading the case alone would report it as untouched.
            _stamp(
                case.updated_at if case else None,
                review.updated_at if review else None,
            ),
            period,
        ]
        # Scan order: the household, then its dependants, then product — the
        # order a broker reads the manual file in.
        sort_key = (
            (emp.staff_id if emp else "") or "",
            (emp.employee_name if emp else "") or "",
            1 if is_dependant else 0,
            (first_value(dattrs, NAME_KEYS) or "") if is_dependant else "",
            insurer.lower(),
            str(row[10]),
        )
        rows.append((sort_key, row))

    for review in reviews:
        lines = lines_by_review.get(review.id, [])
        if not lines:
            # Cancelled/retired review — kept as the broker's record of work
            # already done with the insurer.
            _emit(review, None)
            continue
        for case in lines:
            _emit(review, case)
    for case in lines_by_review.get("", []):
        _emit(None, case)
    # Defensive: a line pointing at a review that no longer exists would
    # otherwise be silently dropped.
    for review_id, lines in lines_by_review.items():
        if review_id and review_id not in reviews_by_id:
            for case in lines:
                _emit(None, case)

    wb = Workbook()
    ws = wb.active
    ws.title = "Underwriting"
    append_safe(ws, HEADER)
    bold_header(ws)
    for _key, row in sorted(rows, key=lambda pair: pair[0]):
        append_safe(ws, row)
    autosize(ws)
    return wb
