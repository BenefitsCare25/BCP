"""ADC (Additions / Deletions / Changes) roster movement endpoints.

Template download → preview (dry-run diff) → apply. Operational writes (like
roster upload): no activation-editable lock, tenant-scoped via the policy year.
"""
from __future__ import annotations

from io import BytesIO
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user, require_client_id
from app.core.rate_limit import limiter
from app.core.uploads import WORKBOOK_SUFFIXES, saved_upload
from app.db.session import get_db
from app.schemas.adc import AdcApplyResult, AdcPreview
from app.services.adc import apply_adc, build_adc_template_workbook, preview_adc

router = APIRouter(prefix="/policy-years", tags=["adc"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{policy_year_id}/adc/template")
def download_adc_template(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """The current active roster round-tripped into an ADC template (.xlsx).

    Carries full NRIC (own-tenant working file used to resolve Change/Delete
    rows); the download is not audited here as it exposes no more than the
    broker already sees in the roster — but see apply for per-movement audit.
    """
    assert_policy_year_for_user(policy_year_id, user, db)
    wb = build_adc_template_workbook(db, policy_year_id)
    buf = BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="adc-template.xlsx"'},
    )


@router.post("/{policy_year_id}/adc/preview", response_model=AdcPreview)
@limiter.limit("20/minute")
async def preview_adc_upload(
    request: Request,
    policy_year_id: str,
    file: Annotated[UploadFile, File()],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdcPreview:
    """Classify + validate + diff a movement file. No mutation."""
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        return preview_adc(db, policy_year_id, client_id, tmp_path)


@router.post("/{policy_year_id}/adc/apply", response_model=AdcApplyResult)
@limiter.limit("10/minute")
async def apply_adc_upload(
    request: Request,
    policy_year_id: str,
    file: Annotated[UploadFile, File()],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdcApplyResult:
    """Apply the movement file: insert Adds, merge Changes, soft-terminate
    Deletes, then re-match + re-assign flex. Per-row audited."""
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        return apply_adc(db, user, policy_year_id, client_id, tmp_path)
