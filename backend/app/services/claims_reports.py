"""Insurance-claims reports — the servicing view of a claim's whole life.

Four sheets, ONE builder each side:

- **Insurance claims** (all / inpatient / outpatient). The split is a FILTER,
  not three reports: writing three would guarantee that a column added for the
  insurer appears on two of them. `_claim_rows` produces the superset and each
  scope drops the columns that cannot apply to it.
- **Employee claims in benefit year** — flex AND insurance together, per
  employee. The only sheet where a member's whole year of claiming is on one
  page, which is what a client asks for at renewal.

Everything the servicing columns need beyond the claim row is derived
(`services/claim_settlement.py`): document-receipt dates come from the
documents, and the three SLA counters from the dates. Nothing here reads a
stored counter, because a stored counter for an unpaid claim is wrong the
morning after it is written.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Dependant, Employee, PolicyYear
from app.models.claim import (
    CASE_TYPE_LOG,
    CLAIM_KIND_FLEX,
    CLAIM_STATUS_DRAFT,
    CLAIM_STATUS_NEEDS_INFO,
    CLAIM_STATUS_PAID,
    CLAIM_STATUS_SENT_TO_INSURER,
    HOSPITAL_TYPE_GOVERNMENT,
)
from app.services.claim_intake import CATEGORY_INPATIENT, claim_profile_for
from app.services.claim_settlement import (
    days_over_deadline,
    document_dates,
    insurer_days,
    servicer_days,
)
from app.services.claims import dependant_display_name
from app.services.insurer_reports import (
    append_safe,
    autosize,
    bold_header,
    naive,
)
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    EMPLOYEE_ID_KEYS,
    REL_KEYS,
    first_value,
    mask_nric,
)

SCOPE_ALL = "all"
SCOPE_INPATIENT = "inpatient"
SCOPE_OUTPATIENT = "outpatient"
CLAIM_SCOPES = frozenset({SCOPE_ALL, SCOPE_INPATIENT, SCOPE_OUTPATIENT})

_ENTITY_KEYS = ("entity", "company", "subsidiary")

# Broker vocabulary → the label an insurer's own ledger uses. `needs_info` is
# reported as "Pending Documents": that IS what the state means to anyone
# outside this system, and it is the reason we never minted a second status for
# it (see models/claim.py).
_STATUS_LABELS: dict[str, str] = {
    CLAIM_STATUS_NEEDS_INFO: "Pending Documents",
    CLAIM_STATUS_SENT_TO_INSURER: "Pending Insurer Approval - Sent",
    CLAIM_STATUS_PAID: "Paid",
}


def normalize_scope(value: str | None) -> str:
    wanted = (value or SCOPE_ALL).strip().lower()
    return wanted if wanted in CLAIM_SCOPES else SCOPE_ALL


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status) or status.replace("_", " ").title()


def _is_inpatient(claim: Claim) -> bool:
    """Whether the claim draws on an inpatient product.

    Resolved through the product's own intake profile — the same classification
    the claim form and the AI review use — rather than a local product-code
    list, which would be a fourth place to remember when a product is added.

    Keyed on the PRODUCT, not the sub-type: a pre-/post-hospitalisation consult
    is billed by a specialist clinic but is an inpatient benefit
    (`claim_intake._target_settings`), so a sub-type test would file it under
    outpatient and understate the hospitalisation book.
    """
    return claim_profile_for(claim.product_code).category == CATEGORY_INPATIENT


def _hospital_label(claim: Claim) -> str:
    if not claim.hospital_type:
        return ""
    return (
        "Government"
        if claim.hospital_type == HOSPITAL_TYPE_GOVERNMENT
        else "Private/Overseas"
    )


@dataclass(frozen=True)
class _Row:
    claim: Claim
    employee: Employee
    claimant: str
    claimant_nric: str
    relation: str


def _claim_rows(
    db: Session, py: PolicyYear, *, masked: bool
) -> tuple[list[_Row], dict[str, object]]:
    rows = list(
        db.execute(
            select(Claim, Employee)
            .join(Employee, Claim.employee_id == Employee.id)
            .where(
                Claim.policy_year_id == py.id,
                # A draft is member work-in-progress, not a claim. It has no
                # reference number either — it was never submitted.
                Claim.status != CLAIM_STATUS_DRAFT,
            )
            .order_by(
                Claim.submitted_at.desc().nullslast(),
                Claim.created_at.desc(),
            )
        ).all()
    )
    claims = [c for c, _ in rows]
    # ONE dependant load. The rows carry both facts this report needs — the
    # display name and the attributes behind the NRIC and relationship — so
    # fetching names separately would be a second pass over the same table.
    deps = {
        d.id: d
        for d in db.execute(
            select(Dependant).where(
                Dependant.id.in_([c.dependant_id for c in claims if c.dependant_id]
                                 or [""])
            )
        ).scalars()
    }

    out: list[_Row] = []
    for claim, employee in rows:
        if claim.dependant_id:
            dep = deps.get(claim.dependant_id)
            claimant = dependant_display_name(dep) or ""
            dattrs = (dep.attribute_values or {}) if dep else {}
            raw_id = first_value(dattrs, DEPENDANT_ID_KEYS)
            # The roster's own wording ("Spouse", "Child"), falling back to a
            # generic label rather than blank: a claim WITH a dependant_id is
            # never the employee's own, and an empty Relation cell reads as if
            # it were.
            relation = first_value(dattrs, REL_KEYS) or "Dependant"
        else:
            claimant = employee.employee_name or employee.staff_id
            raw_id = first_value(employee.attribute_values or {}, EMPLOYEE_ID_KEYS)
            relation = "Self"
        out.append(
            _Row(
                claim=claim,
                employee=employee,
                claimant=claimant,
                claimant_nric=(mask_nric(raw_id) if masked else (raw_id or "")),
                relation=relation,
            )
        )
    return out, document_dates(db, [c.id for c in claims])


def _header(scope: str) -> list[str]:
    header = [
        "Insured Entity",
        "Employee Name",
        "Identification No.",
        "Claimant Name",
        "Claimant's Identification No.",
        "Reference No.",
        "Claim Type",
        "Sub Claim Type",
        "Provider",
    ]
    # Sector and admission dates are inpatient facts. On an outpatient-only
    # sheet they are three columns that are blank on every row, which reads as
    # missing data rather than as inapplicable.
    if scope != SCOPE_OUTPATIENT:
        header += ["Hospital Type", "LOG"]
    if scope != SCOPE_INPATIENT:
        header.append("Referral Letter")
    header += ["Incurred Date"]
    if scope != SCOPE_OUTPATIENT:
        header += ["Admission Date", "Discharge Date"]
    header += [
        "Diagnosis",
        "Doctor",
        "Currency",
        "Incurred Amt",
        "Payable Amt",
        "Paid Amt",
        "TAX",
        "CPF",
        "Status",
        "Submission Date",
        "First Document Receive Date",
        "Final Document Receive Date",
        "Verified Date",
        "No. of days for Tracking Servicer",
        "Date Sent to Insurer",
        "Deadline Date for Insurer",
        "No. of days for Tracking Insurer",
        "Payment Date",
        "Days Over Deadline",
        "Employee Remark",
        "Admin Remark",
    ]
    return header


def _flag(value: bool | None) -> str:
    """Tri-state to a cell. NULL is BLANK, not "No" — "we have not assessed the
    tax treatment" and "this is not taxable" are different answers and a
    payroll team acts differently on each."""
    if value is None:
        return ""
    return "Yes" if value else "No"


def build_insurance_claims_workbook(
    db: Session,
    py: PolicyYear,
    *,
    scope: str = SCOPE_ALL,
    masked: bool = True,
) -> Workbook:
    """Insurance claims in the benefit year, optionally narrowed by setting."""
    wanted = normalize_scope(scope)
    rows, doc_dates = _claim_rows(db, py, masked=masked)

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    header = _header(wanted)
    append_safe(ws, header)
    bold_header(ws)

    for row in rows:
        claim = row.claim
        # Flex claims are funded by the member's own wallet — there is no
        # insurer, so they have no place on an insurance-claims sheet. They are
        # on the employee-claims sheet instead.
        if claim.claim_kind == CLAIM_KIND_FLEX:
            continue
        inpatient = _is_inpatient(claim)
        if wanted == SCOPE_INPATIENT and not inpatient:
            continue
        if wanted == SCOPE_OUTPATIENT and inpatient:
            continue

        dates = doc_dates.get(claim.id)
        attrs = row.employee.attribute_values or {}
        cells: list[object] = [
            first_value(attrs, _ENTITY_KEYS) or "",
            row.employee.employee_name or "",
            (
                mask_nric(first_value(attrs, EMPLOYEE_ID_KEYS))
                if masked
                else (first_value(attrs, EMPLOYEE_ID_KEYS) or "")
            ),
            row.claimant,
            row.claimant_nric,
            claim.reference_no or "",
            "Inpatient Benefits" if inpatient else "Outpatient Benefits",
            claim.sub_type or "",
            claim.provider_name or "",
        ]
        if wanted != SCOPE_OUTPATIENT:
            cells += [
                _hospital_label(claim),
                "Yes" if claim.case_type == CASE_TYPE_LOG else "No",
            ]
        if wanted != SCOPE_INPATIENT:
            cells.append("Yes" if claim.referral_document_id else "No")
        cells.append(claim.incurred_date)
        if wanted != SCOPE_OUTPATIENT:
            cells += [claim.admission_date, claim.discharge_date]
        cells += [
            claim.diagnosis or "",
            claim.doctor_name or "",
            claim.currency,
            claim.amount_claimed,
            claim.amount_approved,
            claim.payment_amount,
            _flag(claim.taxable),
            _flag(claim.cpf_claimable),
            status_label(claim.status),
            naive(claim.submitted_at),
            naive(getattr(dates, "first", None)),
            naive(getattr(dates, "final", None)),
            # "Verified" IS our decision: the moment an assessor accepted the
            # claim. A separate column would be a second name for one event.
            naive(claim.decided_at),
            servicer_days(claim, dates),
            naive(claim.sent_to_insurer_at),
            claim.insurer_deadline_on,
            insurer_days(claim),
            claim.paid_on,
            days_over_deadline(claim),
            claim.remarks or "",
            claim.admin_remarks or "",
        ]
        append_safe(ws, cells)

    autosize(ws)
    return wb


EMPLOYEE_CLAIMS_HEADER = [
    "Entity",
    "Staff ID",
    "Employee Name",
    "Category",
    "Claimant Name",
    "Relation",
    "Reference No.",
    "Claim Category",
    "Claim Type",
    "Claim Sub-Type",
    "TAX",
    "CPF",
    "Incurred Date",
    "Service Provider",
    "Incurred Currency",
    "Incurred Amt",
    "Converted Currency",
    "Converted Incurred Amt",
    "Payment Amt",
    "Status",
]


def build_employee_claims_workbook(
    db: Session, py: PolicyYear, *, masked: bool = True
) -> Workbook:
    """Every claim in the year — flex AND insurance — one row each.

    The only sheet that puts a member's whole year of claiming on one page.
    `Claim Category` is what separates the two funding sources, so a reader can
    total the insurer's book and the wallet's separately without needing two
    files.
    """
    rows, _ = _claim_rows(db, py, masked=masked)

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    append_safe(ws, EMPLOYEE_CLAIMS_HEADER)
    bold_header(ws)

    for row in rows:
        claim = row.claim
        is_flex = claim.claim_kind == CLAIM_KIND_FLEX
        attrs = row.employee.attribute_values or {}
        append_safe(ws, [
            first_value(attrs, _ENTITY_KEYS) or "",
            row.employee.staff_id,
            row.employee.employee_name or "",
            first_value(attrs, ("category",)) or "",
            row.claimant,
            row.relation,
            claim.reference_no or "",
            "Flexible Benefits" if is_flex else "Insurance",
            (claim.flex_category_name if is_flex else claim.claim_type) or "",
            claim.sub_type or "",
            _flag(claim.taxable),
            _flag(claim.cpf_claimable),
            claim.incurred_date,
            claim.provider_name or "",
            claim.currency,
            claim.amount_claimed,
            claim.currency,
            claim.amount_converted if claim.amount_converted is not None
            else claim.amount_claimed,
            # What actually moved: the insurer's payment when there is one,
            # otherwise what we approved. A flex claim has no insurer leg, so
            # `amount_approved` IS its payment.
            claim.payment_amount if claim.payment_amount is not None
            else claim.amount_approved,
            status_label(claim.status),
        ])

    autosize(ws)
    return wb


def claims_report_filename(kind: str, today: date | None = None) -> str:
    return f"{kind}-{(today or date.today()):%Y%m%d}.xlsx"
