"""Report bundles — one click produces a whole submission, as a .zip.

The incumbent platform generates a named, parameterised report SET: pressing
"New Report" on its Employee Listing tab yields three files, and on the Insurer
tab five. Ours were per-file, so assembling one submission meant five downloads
and remembering which five.

**Deliberately NOT a job queue.** The incumbent's `Report Ready` state, its
Active/Archived lifecycle and its async generation exist because generation is
slow there; they are not features a broker asked for. Every builder here runs in
well under a request, so a bundle is streamed live. That also means a bundle can
never go stale — there is nothing stored to go stale.

Retention is unchanged and stays per-file: `report_versions` already keeps the
versioned series that matter (the insurer listings and their movement diffs),
and a zip of them would be a second, coarser copy of the same bytes.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.models import PolicyYear


@dataclass(frozen=True)
class BundleMember:
    """One file in a bundle.

    ``build`` takes (db, py, masked, insurer) and returns a Workbook. Members
    that do not vary by insurer simply ignore the argument — a uniform
    signature keeps the runner from needing a per-member branch.
    """

    filename: str
    build: Callable[..., Workbook]
    # True when the member must be produced once PER INSURER, with the insurer
    # name folded into its filename.
    per_insurer: bool = False


@dataclass(frozen=True)
class BundleSpec:
    key: str
    label: str
    description: str
    members: list[BundleMember] = field(default_factory=list)
    # A bundle whose members are all per-insurer needs an insurer to run. Set so
    # the endpoint can 400 with a useful message instead of producing an empty
    # zip, which reads as "there is nothing to submit".
    requires_insurer: bool = False


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _employee_listing(db, py, masked, insurer):
    from app.services.insurer_listings import build_employee_listing

    return build_employee_listing(db, py, insurer, masked=masked)


def _dependant_listing(db, py, masked, insurer):
    from app.services.insurer_listings import build_dependant_listing

    return build_dependant_listing(db, py, insurer, masked=masked)


def _benefit_selection(db, py, masked, insurer):
    from app.services.insurer_reports import build_benefit_selection_workbook

    return build_benefit_selection_workbook(db, py, masked=masked)


def _built_in_employees(db, py, masked, insurer):
    from app.services.built_in_listings import build_built_in_employee_listing

    return build_built_in_employee_listing(db, py, masked=masked)


def _built_in_dependants(db, py, masked, insurer):
    from app.services.built_in_listings import build_built_in_dependant_listing

    return build_built_in_dependant_listing(db, py, masked=masked)


def _portal_access(db, py, masked, insurer):
    from app.services.portal_access_report import build_portal_access_workbook

    return build_portal_access_workbook(db, py)


def _all_claims(db, py, masked, insurer):
    from app.services.claims_reports import build_insurance_claims_workbook

    return build_insurance_claims_workbook(db, py, scope="all", masked=masked)


def _inpatient_claims(db, py, masked, insurer):
    from app.services.claims_reports import build_insurance_claims_workbook

    return build_insurance_claims_workbook(db, py, scope="inpatient", masked=masked)


def _outpatient_claims(db, py, masked, insurer):
    from app.services.claims_reports import build_insurance_claims_workbook

    return build_insurance_claims_workbook(db, py, scope="outpatient", masked=masked)


def _employee_claims(db, py, masked, insurer):
    from app.services.claims_reports import build_employee_claims_workbook

    return build_employee_claims_workbook(db, py, masked=masked)


def _utilisation(db, py, masked, insurer):
    from app.services.flex_ledger import build_utilisation_workbook

    return build_utilisation_workbook(db, py, masked=masked)


def _utilisation_summary(db, py, masked, insurer):
    from app.services.flex_ledger import build_utilisation_summary_workbook

    return build_utilisation_summary_workbook(db, py, masked=masked)


def _leaver_summary(db, py, masked, insurer):
    from app.services.leaver_reports import build_leaver_summary_workbook

    return build_leaver_summary_workbook(db, py, masked=masked)


def _leaver_details(db, py, masked, insurer):
    from app.services.leaver_reports import build_leaver_details_workbook

    return build_leaver_details_workbook(db, py, masked=masked)


BUNDLES: dict[str, BundleSpec] = {
    "member-listing": BundleSpec(
        key="member-listing",
        label="Member Listing",
        description=(
            "The full roster: employees, dependants and their portal accounts."
        ),
        members=[
            BundleMember("built-in-employee-listing-report.xlsx", _built_in_employees),
            BundleMember("built-in-dependant-listing-report.xlsx", _built_in_dependants),
            BundleMember("portal-access-report.xlsx", _portal_access),
        ],
    ),
    "insurer-submission": BundleSpec(
        key="insurer-submission",
        label="Insurer Submission",
        description=(
            "Everything one insurer receives: the employee and dependant "
            "listings, plus the benefit-selection and leave record."
        ),
        requires_insurer=True,
        members=[
            BundleMember(
                "built-in-employee-listing-for-insurer-report.xlsx",
                _employee_listing,
                per_insurer=True,
            ),
            BundleMember(
                "built-in-dependant-listing-for-insurer-report.xlsx",
                _dependant_listing,
                per_insurer=True,
            ),
            BundleMember(
                "benefit-selection-status-with-buy-sell-leave-report.xlsx",
                _benefit_selection,
            ),
        ],
    ),
    "insurance-claims": BundleSpec(
        key="insurance-claims",
        label="Insurance Claims",
        description="The year's claims: all, split by setting, and per member.",
        members=[
            BundleMember("all-insurance-claims-in-benefit-year.xlsx", _all_claims),
            BundleMember("inpatient-claims-in-benefit-year.xlsx", _inpatient_claims),
            BundleMember("outpatient-claims-in-benefit-year.xlsx", _outpatient_claims),
            BundleMember("employee-claims-in-benefit-year.xlsx", _employee_claims),
        ],
    ),
    "wallet-utilisation": BundleSpec(
        key="wallet-utilisation",
        label="Wallet Utilisation",
        description="The flex ledger and each member's wallet position.",
        members=[
            BundleMember("utilisation-report.xlsx", _utilisation),
            BundleMember("utilisation-summary-report.xlsx", _utilisation_summary),
        ],
    ),
    "leavers": BundleSpec(
        key="leavers",
        label="Leavers",
        description="Leavers' final wallet position and their claims.",
        members=[
            BundleMember("leaver-summary-report.xlsx", _leaver_summary),
            BundleMember("leaver-details-report.xlsx", _leaver_details),
        ],
    ),
}


def spec_for(key: str) -> BundleSpec | None:
    return BUNDLES.get((key or "").strip().lower())


def build_bundle(
    db: Session,
    py: PolicyYear,
    spec: BundleSpec,
    *,
    masked: bool = True,
    insurer: str | None = None,
) -> bytes:
    """Render every member of the bundle into one zip.

    Members are written in declaration order and each workbook is saved to a
    buffer before being added — openpyxl cannot stream into a zip entry, and
    holding one workbook at a time is what keeps a 650-row roster's five sheets
    inside a normal request's memory.
    """
    buf = BytesIO()
    stamp = f"{date.today():%Y%m%d}"
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        for member in spec.members:
            wb = member.build(db, py, masked, insurer)
            name = member.filename
            if member.per_insurer and insurer:
                stem, _, ext = name.rpartition(".")
                name = f"{stem}-{_slug(insurer)}.{ext}"
            stem, _, ext = name.rpartition(".")
            inner = BytesIO()
            wb.save(inner)
            zf.writestr(f"{stem}-{stamp}.{ext}", inner.getvalue())
    return buf.getvalue()


def bundle_filename(
    spec: BundleSpec, insurer: str | None = None, today: date | None = None
) -> str:
    parts = [spec.key]
    if spec.requires_insurer and insurer:
        parts.append(_slug(insurer))
    parts.append(f"{(today or date.today()):%Y%m%d}")
    return "-".join(parts) + ".zip"
