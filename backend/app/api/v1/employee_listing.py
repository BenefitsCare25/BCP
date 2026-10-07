"""Company Employee Listing import: preview the mapping, then apply it.

The listing is the company's own register (employees, dependants and per-product
cover columns). Preview reads it and suggests every decision; apply writes the
members, records each person's listed cover and re-matches. Tenant-scoped via
the policy year, like the member-listing sync it shares its movement engine with.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user, require_client_id
from app.core.rate_limit import limiter
from app.core.uploads import WORKBOOK_SUFFIXES, saved_upload
from app.db.session import get_db
from app.schemas.employee_listing import ListingApplyOut, ListingMappingIn, ListingPreviewOut
from app.services.adc import StaleListingPreview, TerminationBlockedByDroppedRows
from app.services.el_import import service

router = APIRouter(prefix="/policy-years", tags=["employee-listing"])


def _mapping(raw: str | None) -> ListingMappingIn | None:
    if not raw:
        return None
    if len(raw) > 200_000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Mapping is too large.")
    try:
        return ListingMappingIn.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Listing mapping is not valid."
        ) from exc


@router.post("/{policy_year_id}/employee-listing/preview", response_model=ListingPreviewOut)
@limiter.limit("20/minute")
async def preview_employee_listing(
    request: Request,
    policy_year_id: str,
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str | None, Form()] = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ListingPreviewOut:
    """Read the listing and suggest its mapping. No mutation."""
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    decisions = _mapping(mapping)
    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        try:
            return await run_in_threadpool(
                lambda: service.preview(db, client_id, policy_year_id, tmp_path, decisions)
            )
        except service.NotEmployeeListing as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {"code": "not_employee_listing", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/{policy_year_id}/employee-listing/apply", response_model=ListingApplyOut)
@limiter.limit("10/minute")
async def apply_employee_listing(
    request: Request,
    policy_year_id: str,
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str, Form()],
    # Off by default: a caller that omits it can add and change, never end cover.
    terminate_missing: Annotated[bool, Form()] = False,
    missing_digest: Annotated[str | None, Form()] = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ListingApplyOut:
    """Commit members, listed cover, mapping and joiner rules together, then
    re-match best-effort after the commit."""
    client_id = require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)
    decisions = _mapping(mapping) or ListingMappingIn()
    async with saved_upload(file, WORKBOOK_SUFFIXES) as tmp_path:
        try:
            # Whole-roster work blocks for minutes; keep it off the event loop
            # (see adc.apply_listing_upload).
            return await run_in_threadpool(
                lambda: service.apply(
                    db, user, client_id, policy_year_id, tmp_path, decisions,
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
        except TerminationBlockedByDroppedRows as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {"code": "termination_blocked_dropped_rows", "message": str(exc)},
            ) from exc
        except service.UnresolvedListingMapping as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {"code": "unresolved_listing_mapping", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
