"""Roster movement endpoints — upload the member listing, preview, apply.

Upload → preview (dry-run diff) → apply. There is no movement template and no
``Action`` column: the movements are derived from the listing itself
(`services/adc.py`). Operational writes (like roster upload): no
activation-editable lock, tenant-scoped via the policy year.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user, require_client_id
from app.core.rate_limit import limiter
from app.core.uploads import WORKBOOK_SUFFIXES, saved_upload
from app.db.session import get_db
from app.schemas.adc import AdcApplyResult, AdcPreview
from app.services.adc import StaleListingPreview, apply_listing, preview_listing

router = APIRouter(prefix="/policy-years", tags=["adc"])


@router.post("/{policy_year_id}/adc/preview", response_model=AdcPreview)
@limiter.limit("20/minute")
async def preview_listing_upload(
    request: Request,
    policy_year_id: str,
    file: Annotated[UploadFile, File()],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdcPreview:
    """Diff an uploaded member listing against the roster. No mutation."""
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        # Off the event loop — see `apply_listing_upload` below.
        return await run_in_threadpool(
            preview_listing, db, policy_year_id, client_id, tmp_path
        )


@router.post("/{policy_year_id}/adc/apply", response_model=AdcApplyResult)
@limiter.limit("10/minute")
async def apply_listing_upload(
    request: Request,
    policy_year_id: str,
    file: Annotated[UploadFile, File()],
    # Defaults to FALSE, and the default is the safety property: a caller that
    # forgets the flag can add and change, never end anyone's cover.
    terminate_missing: Annotated[bool, Form()] = False,
    missing_digest: Annotated[str | None, Form()] = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdcApplyResult:
    """Apply the listing: insert Adds, merge Changes, soft-terminate rows
    carrying a past leaving date, and — only with ``terminate_missing`` —
    those absent from the file. Then re-match + re-assign flex. Per-row audited.
    """
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        try:
            # A whole-roster apply is minutes of BLOCKING work (per-row inserts
            # + audit, then re-match + re-flex), and this is an `async def`
            # endpoint, so running it inline would hold the event loop. The
            # gunicorn UvicornWorker heartbeats FROM that loop: a held loop
            # stops beating, the arbiter reads the worker as hung and SIGKILLs
            # it at `--timeout`, the connection dies with no response, and the
            # browser shows an error with no message. That is exactly how a
            # 4,806-member apply failed in prod on 2026-08-12 — twice, at 30s
            # each. In a thread the loop keeps beating and the request simply
            # takes as long as it takes.
            return await run_in_threadpool(
                lambda: apply_listing(
                    db, user, policy_year_id, client_id, tmp_path,
                    terminate_missing=terminate_missing,
                    expected_missing_digest=missing_digest,
                    source_filename=file.filename,
                )
            )
        except StaleListingPreview as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"code": "stale_listing_preview", "message": str(exc)},
            ) from exc
