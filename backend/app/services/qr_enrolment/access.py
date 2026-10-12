"""Capability tokens, PINs, public-link audit rows and link building.

Tokens are 32 random bytes; only their SHA-256 is stored and looked up. The
employee entry token is also kept encrypted so an authorised broker can
download the SAME QR again. Links carry the token in the URL fragment, so it
never reaches server or proxy logs; the page sends it in a request header.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.request_context import get_client_ip, get_request_id, get_user_agent
from app.core.tenant_resolution import (
    FirmOriginUnavailable,
    firm_origin_unavailable_error,
    public_origin,
    request_firm,
)
from app.db.tenancy import set_search_path
from app.models import AuditLog
from app.models.platform import DOMAIN_SURFACE_CLIENT

LINK_HEADER = "X-Inspro-Link"
ENTRY_PATH = "/enrol"
HR_DOWNLOAD_PATH = "/hr-download"
_TOKEN_MIN, _TOKEN_MAX = 32, 64


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def presented_token(request: Request) -> str:
    """The capability the page sent, or 404 — an absent, malformed or unknown
    link all answer the same way."""
    raw = (request.headers.get(LINK_HEADER) or "").strip()
    if not (_TOKEN_MIN <= len(raw) <= _TOKEN_MAX) or not all(
        c.isalnum() or c in "-_" for c in raw
    ):
        raise link_not_found()
    return raw


def link_not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, {
        "code": "link_not_found",
        "message": "This link is not valid. Ask for a new one.",
    })


def route_host_firm(request: Request, db: Session) -> str | None:
    """Point the session at the host's firm before any tenant read. The host
    selects the firm; the token then has to exist inside it."""
    firm = request_firm(request)
    firm_id = firm.firm_id if firm is not None else None
    set_search_path(db, firm_id)
    return firm_id


def link_url(db: Session, firm_id: str, path: str, token: str) -> str:
    try:
        origin = public_origin(db, firm_id, DOMAIN_SURFACE_CLIENT)
    except FirmOriginUnavailable:
        raise firm_origin_unavailable_error() from None
    return f"{origin}{path}#{token}"


def write_public_audit(
    db: Session,
    *,
    client_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    after: dict[str, object] | None = None,
    employee_id: str | None = None,
) -> None:
    """An audit row for an action taken through a public link. The actor is the
    link holder, never a named person (`actor_type` ``public``)."""
    db.add(AuditLog(
        client_id=client_id,
        actor_type="public",
        employee_id=employee_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=get_request_id(),
        ip_address=(get_client_ip() or "")[:64] or None,
        user_agent=(get_user_agent() or "")[:512] or None,
        after=after or None,
    ))
