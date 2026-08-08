"""Single source of truth for Reports Center report types + their retention.

Two retention modes drive the whole versioning feature:

- ``versioned`` — a growing immutable series (the insurer submission, benefit
  selection). The insurer artifacts also carry a membership ``manifest`` so a
  movement (adds/deletions/changes) report can diff versions.
- ``latest`` — one retained copy per benefit year; regenerating supersedes it
  (placement-stage config documents).

Reports left off this registry (internal coverage reports, claims register,
readiness) stay live-only — generated on demand, never retained.

**Retention happens ON DOWNLOAD, not on a separate "Save version" click**
(2026-08-08). The download already builds the report fresh from the benefit
year; retaining what it produced makes the archive complete BY CONSTRUCTION —
every version that exists is a file that actually left the building. Retention
that has to be remembered is retention that records only the submissions
somebody thought to press a button for, and nothing at all about the ones they
did not. ``RETAINED_ON_DOWNLOAD`` is the map from a live download to the series
it appends to; a report absent from it downloads live and is audited, but keeps
no bytes.

Which reports retain is not "all of them": it is the ones a THIRD PARTY acts on.
An insurer submission is a claim about the client's book that someone else bills
against, so being able to reproduce it months later is the point. The internal
registers (member register, leavers, underwriting) are working documents — the
audit row saying who pulled one is the whole record they need, and retaining
every pull would bank NRIC-bearing blobs for nothing.

This module is pure config + a builder dispatch; the versioning service
(``report_versions.py``) and the frontend (via ``GET /report-registry``) both
read it so the classification lives in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

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
    # The artifact a broker actually SENDS: one workbook, three sheets. The two
    # per-file listings below predate it and stay registered so their history
    # remains readable (the History drawer merges them in) — nothing appends to
    # them any more, because retaining a sheet of a workbook alongside the
    # workbook would record one submission as three.
    "insurer_submission": ReportSpec(
        "insurer_submission", "Insurer Submission",
        MODE_VERSIONED, "insurer", "xlsx", True,
    ),
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


# Live download → the series its bytes are retained into. A download whose key
# is absent here is audited and streamed, never stored. Keyed by the WORKBOOK
# key where the download is a composite (hyphenated, `report_workbooks.WORKBOOKS`)
# and by the endpoint's own report type otherwise.
RETAINED_ON_DOWNLOAD: dict[str, str] = {
    "insurer-submission": "insurer_submission",
    "benefit_selection": "benefit_selection",
}

# How many versions a series keeps. Two years of monthly submissions, which is
# past any renewal a broker reconciles against. Nothing pruned before this
# existed, so a series grew a blob per data change forever.
RETENTION_KEEP = 24

# Series a broker can still READ but nothing writes to any more. Merged into the
# History drawer beside the live series so retiring a report type cannot orphan
# the record of what was submitted under it.
SUPERSEDED_TYPES: dict[str, tuple[str, ...]] = {
    "insurer_submission": ("employee_listing", "dependant_listing"),
}


def spec_for(report_type: str) -> ReportSpec:
    try:
        return REGISTRY[report_type]
    except KeyError:
        raise KeyError(f"Unknown report_type {report_type!r}") from None


def retained_type_for(download_key: str) -> str | None:
    """The series a live download appends to, or None when it only logs."""
    return RETAINED_ON_DOWNLOAD.get(download_key)


def mime_for(fmt: str) -> str:
    return _DOCX_MIME if fmt == "docx" else _XLSX_MIME


def scope_key_for(spec: ReportSpec, params: dict[str, Any]) -> str | None:
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
    db: Session, py: PolicyYear, report_type: str, params: dict[str, Any]
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
    if report_type == "insurer_submission":
        # Built through the composer, so the retained bytes are byte-identical
        # to what the download streamed — a retained copy assembled a second way
        # is a copy of something nobody was sent.
        from app.services.report_workbooks import (
            BuildContext,
            build_workbook,
        )
        from app.services.report_workbooks import spec_for as workbook_spec_for

        wb_spec = workbook_spec_for("insurer-submission")
        if wb_spec is None:  # pragma: no cover - registry drift
            raise KeyError("Workbook 'insurer-submission' is not registered")
        return _xlsx_bytes(
            build_workbook(
                db, py, wb_spec,
                BuildContext(masked=masked, insurer=params["insurer"]),
            )
        )
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
