"""No-login HR download of a prepared batch of enrolment forms.

- GET  /public/hr-download         link state (``X-Inspro-Link`` header)
- POST /public/hr-download/pin     check the PIN; prepares THIS browser
- POST /public/hr-download/start   start the fixed 10-minute window
- GET  /public/hr-download/file    the ZIP (cookie only), while the window lasts

Opening the link changes nothing, so mail scanners and previews cannot use it
up. The PIN, sent to HR separately by the broker, prepares the browser; the
deliberate Start tap claims the link for that browser, atomically, and fixes
the deadline in the database.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.cookie_auth import require_same_origin
from app.core.downloads import attachment_header
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.core.tenant_resolution import require_client_surface, require_live_host_firm
from app.db.session import get_db
from app.models import Client, HrDownloadBatch, HrDownloadGrant
from app.schemas.qr_enrolment import HrDownloadOut, HrPinIn
from app.services.qr_enrolment import batches as svc
from app.services.qr_enrolment.access import (
    link_not_found,
    presented_token,
    route_host_firm,
    write_public_audit,
)

router = APIRouter(
    prefix="/public/hr-download",
    tags=["public-hr-download"],
    dependencies=[Depends(require_client_surface), Depends(require_live_host_firm)],
)

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _out(
    db: Session, grant: HrDownloadGrant, batch: HrDownloadBatch, state: str,
    attempts_left: int | None = None,
) -> HrDownloadOut:
    client = db.get(Client, batch.client_id)
    return HrDownloadOut(
        state=state,  # type: ignore[arg-type]
        company_name=(client.legal_name or client.name) if client else "",
        label=batch.label,
        file_count=batch.file_count,
        size_bytes=batch.size_bytes,
        server_time=svc.now_utc(),
        expires_at=grant.expires_at if state in ("active", "in_use") else None,
        access_seconds=svc.ACCESS_SECONDS,
        attempts_left=(
            attempts_left if attempts_left is not None
            else max(0, svc.MAX_PIN_FAILURES - grant.pin_failures)
        ),
        downloads_left=max(0, svc.MAX_DOWNLOADS - grant.downloads),
    )


def _set_cookie(response: Response, value: str, max_age: int) -> None:
    response.set_cookie(
        svc.COOKIE_NAME, value, max_age=max_age, path=svc.COOKIE_PATH,
        httponly=True, secure=get_settings().env != "dev", samesite="strict",
    )


def _load(
    request: Request, db: Session
) -> tuple[HrDownloadGrant, HrDownloadBatch, dict[str, object] | None]:
    token = presented_token(request)
    firm_id = route_host_firm(request, db)
    grant, batch = svc.resolve_grant(db, token, firm_id)
    claims = svc.read_cookie(request.cookies.get(svc.COOKIE_NAME), grant.id)
    return grant, batch, claims


def _refuse(state: str) -> HTTPException:
    messages = {
        "expired": "This link has expired. Ask your broker for a new link and PIN.",
        "locked": "Too many wrong PINs. Ask your broker for a new link and PIN.",
        "revoked": "This link is no longer active. Ask your broker for a new link and PIN.",
        "in_use": "This link was already started in another browser.",
    }
    return HTTPException(status.HTTP_409_CONFLICT, {
        "code": f"link_{state}", "message": messages.get(state, "This link cannot be used."),
        "state": state,
    })


@router.get("", response_model=HrDownloadOut)
@limiter.limit("60/minute")
def link_state(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> HrDownloadOut:
    grant, batch, claims = _load(request, db)
    out = _out(db, grant, batch, svc.visitor_state(grant, batch, claims))
    db.commit()
    response.headers.update(_NO_STORE)
    return out


@router.post("/pin", response_model=HrDownloadOut)
@limiter.limit("10/minute")
def enter_pin(
    body: HrPinIn, request: Request, response: Response, db: Session = Depends(get_db)
) -> HrDownloadOut:
    require_same_origin(request)
    grant, batch, claims = _load(request, db)
    grant = svc.lock_grant(db, grant)
    state = svc.visitor_state(grant, batch, claims)
    if state not in ("ready", "verified"):
        raise _refuse(state)
    left = svc.check_pin(db, grant, body.pin)
    if left is not None:
        write_public_audit(
            db, client_id=grant.client_id, action="hr_download.pin_failed",
            entity_type="hr_download_grant", entity_id=grant.id,
            after={"attempts_left": left},
        )
        db.commit()
        if left == 0:
            raise _refuse("locked")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, {
            "code": "wrong_pin", "message": "That PIN is not correct.", "attempts_left": left,
        })
    now = svc.now_utc()
    _set_cookie(
        response, svc.session_cookie(grant.id, svc.new_session(), now),
        svc.VERIFY_MINUTES * 60,
    )
    write_public_audit(
        db, client_id=grant.client_id, action="hr_download.pin_verified",
        entity_type="hr_download_grant", entity_id=grant.id,
    )
    db.commit()
    response.headers.update(_NO_STORE)
    return _out(db, grant, batch, "verified")


@router.post("/start", response_model=HrDownloadOut)
@limiter.limit("20/minute")
def start_download(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> HrDownloadOut:
    require_same_origin(request)
    grant, batch, claims = _load(request, db)
    state = svc.visitor_state(grant, batch, claims)
    if state not in ("verified", "active") or claims is None:
        if state == "ready":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, {
                "code": "pin_required", "message": "Enter the PIN first.",
            })
        raise _refuse(state)
    if not svc.activate(db, grant, str(claims["n"])):
        db.commit()
        raise _refuse("in_use")
    write_public_audit(
        db, client_id=grant.client_id, action="hr_download.started",
        entity_type="hr_download_grant", entity_id=grant.id,
        after={"expires_at": str(grant.expires_at), "files": batch.file_count},
    )
    db.commit()
    seconds = int(svc.ACCESS_SECONDS + timedelta(minutes=1).total_seconds())
    _set_cookie(response, request.cookies[svc.COOKIE_NAME], seconds)
    response.headers.update(_NO_STORE)
    return _out(db, grant, batch, "active")


@router.get("/file")
@limiter.limit("20/minute")
def download_file(request: Request, db: Session = Depends(get_db)) -> Response:
    """Cookie only — a plain navigation, so the browser saves the file itself."""
    raw = request.cookies.get(svc.COOKIE_NAME)
    gid = svc.cookie_grant_id(raw)
    firm_id = route_host_firm(request, db)
    if not gid:
        raise link_not_found()
    grant, batch = svc.grant_by_id(db, gid, firm_id)
    claims = svc.read_cookie(raw, grant.id)
    state = svc.visitor_state(grant, batch, claims)
    if state != "active":
        raise _refuse(state if state != "verified" else "ready")
    if not svc.claim_download(db, grant):
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "download_limit",
            "message": "This link has reached its download limit. Ask your broker for a new "
            "link and PIN if you need the files again.",
        })
    content = svc.download_bytes(db, batch)
    write_public_audit(
        db, client_id=grant.client_id, action="hr_download.file_sent",
        entity_type="hr_download_grant", entity_id=grant.id,
        after={"files": batch.file_count, "bytes": len(content)},
    )
    db.commit()
    client = db.get(Client, batch.client_id)
    company = (client.name if client else "Company").replace("/", "-")
    chunk = 1024 * 1024
    return StreamingResponse(
        (content[i:i + chunk] for i in range(0, len(content), chunk)),
        media_type="application/zip",
        headers={
            "Content-Disposition": attachment_header(f"{company} enrolment forms.zip"),
            "Content-Length": str(len(content)),
            **_NO_STORE,
        },
    )
