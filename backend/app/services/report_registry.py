"""Single source of truth for Reports Center report types + their retention.

Two retention modes drive the whole versioning feature:

- ``versioned`` — a growing immutable series (insurer employee/dependant
  listings, benefit selection). The two listings also carry a membership
  ``manifest`` so a movement (adds/deletions/changes) report can diff versions.
- ``latest`` — one retained copy per benefit year; regenerating supersedes it
  (placement-stage config documents).

Reports left off this registry (internal coverage reports, claims register,
readiness) stay live-only — generated on demand, never retained.

This module is pure config + a builder dispatch; the versioning service
(``report_versions.py``) and the frontend (via ``GET /report-registry``) both
read it so the classification lives in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.models import PolicyYear
from app.models.report_version import MODE_LATEST, MODE_VERSIONED

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class ReportSpec:
    report_type: str
    label: str
    mode: str  # MODE_VERSIONED | MODE_LATEST
    scope: str | None  # "insurer" | "window" | None
    fmt: str  # "xlsx" | "docx"
    has_movement: bool


REGISTRY: dict[str, ReportSpec] = {
    "employee_listing": ReportSpec(
        "employee_listing", "Employee Listing for Insurer",
        MODE_VERSIONED, "insurer", "xlsx", True,
    ),
    "dependant_listing": ReportSpec(
        "dependant_listing", "Dependant Listing for Insurer",
        MODE_VERSIONED, "insurer", "xlsx", True,
    ),
    "benefit_selection": ReportSpec(
        "benefit_selection", "Benefit Selection & Leave",
        MODE_VERSIONED, "window", "xlsx", False,
    ),
    "fact_find": ReportSpec(
        "fact_find", "Fact-Find Form", MODE_LATEST, None, "docx", False,
    ),
    "quotation_slip": ReportSpec(
        "quotation_slip", "Quotation Slip", MODE_LATEST, None, "xlsx", False,
    ),
    "placement_slip": ReportSpec(
        "placement_slip", "Placement Slip", MODE_LATEST, None, "xlsx", False,
    ),
}


def spec_for(report_type: str) -> ReportSpec:
    try:
        return REGISTRY[report_type]
    except KeyError:
        raise KeyError(f"Unknown report_type {report_type!r}") from None


def mime_for(fmt: str) -> str:
    return _DOCX_MIME if fmt == "docx" else _XLSX_MIME


def scope_key_for(spec: ReportSpec, params: dict) -> str | None:
    """Series discriminator: normalized insurer / window id / None."""
    if spec.scope == "insurer":
        return ((params.get("insurer") or "").strip().lower()) or None
    if spec.scope == "window":
        return params.get("window_id") or None
    return None


def _xlsx_bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_report_bytes(
    db: Session, py: PolicyYear, report_type: str, params: dict
) -> bytes:
    """Produce the report artifact bytes by dispatching to the existing live
    builder. The single place that reconciles their divergent signatures.

    Imports are local so the pure registry metadata stays import-light and to
    avoid an import cycle through the service layer.
    """
    from app.services.fact_find_render import generate as _factfind_generate
    from app.services.insurer_listings import (
        build_dependant_listing,
        build_employee_listing,
    )
    from app.services.insurer_reports import build_benefit_selection_workbook
    from app.services.placement_slip_export import (
        build_placement_slip_workbook,
        build_quotation_slip_workbook,
    )

    masked = bool(params.get("masked", True))
    if report_type == "employee_listing":
        return _xlsx_bytes(build_employee_listing(db, py, params["insurer"], masked=masked))
    if report_type == "dependant_listing":
        return _xlsx_bytes(build_dependant_listing(db, py, params["insurer"], masked=masked))
    if report_type == "benefit_selection":
        return _xlsx_bytes(
            build_benefit_selection_workbook(
                db, py, masked=masked, window_id=params.get("window_id")
            )
        )
    if report_type == "quotation_slip":
        return _xlsx_bytes(build_quotation_slip_workbook(db, py))
    if report_type == "placement_slip":
        return _xlsx_bytes(build_placement_slip_workbook(db, py))
    if report_type == "fact_find":
        docx_bytes, _notes = _factfind_generate(db, py)
        return docx_bytes
    raise KeyError(f"No builder for report_type {report_type!r}")
