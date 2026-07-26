"""Derive the tenant subdomain label (`clients.slug`) from a client's name.

`resolve_tenant_context` looks a tenant up by `clients.slug`, so a client with a
NULL slug is unreachable on `{slug}.hr.<base>` / `{slug}.portal.<base>` — the HR
surface and portal credential login simply 404 for that company. Nothing used to
populate the column, so every client was in exactly that state; this module is
the single writer.

Generation is best-effort and always yields a VALID, non-reserved label, because
a client must never be created without one. Admins can still override it with an
explicit slug (validated the same way).
"""
from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy_host import RESERVED_SLUGS, SlugError, validate_slug
from app.models import Client

_NON_LABEL = re.compile(r"[^a-z0-9]+")
_MAX_LABEL = 63
# Leave room for the "-2"/"-abc123" disambiguating suffix.
_BASE_BUDGET = 48


def slugify_client_name(name: str) -> str:
    """A DNS-label candidate from a company name ("CDL Pte Ltd" -> "cdl-pte-ltd").

    Never returns an empty or reserved label — a name that slugifies to nothing
    (e.g. all-CJK) falls back to a random label rather than failing the create.
    """
    base = _NON_LABEL.sub("-", (name or "").strip().lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)[:_BASE_BUDGET].strip("-")
    if not base or base in RESERVED_SLUGS:
        base = f"c-{secrets.token_hex(3)}" if not base else f"{base}-co"
    return base[:_MAX_LABEL]


def generate_unique_slug(
    db: Session, name: str, *, exclude_id: str | None = None
) -> str:
    """A slug for `name` that no OTHER client already holds.

    Collisions get a numeric suffix, then a random one — two companies called
    "Acme" in different broker firms are both legitimate, and the slug namespace
    is global (it fronts a subdomain).
    """
    base = slugify_client_name(name)

    def taken(candidate: str) -> bool:
        stmt = select(Client.id).where(Client.slug == candidate)
        if exclude_id:
            stmt = stmt.where(Client.id != exclude_id)
        return db.execute(stmt.limit(1)).scalar_one_or_none() is not None

    if not taken(base):
        return base
    for n in range(2, 100):
        candidate = f"{base[: _MAX_LABEL - len(str(n)) - 1]}-{n}"
        if not taken(candidate):
            return candidate
    while True:  # pragma: no cover - astronomically unlikely
        candidate = f"{base[:_BASE_BUDGET]}-{secrets.token_hex(3)}"
        if not taken(candidate):
            return candidate


def assign_slug(
    db: Session, client: Client, requested: str | None = None
) -> str:
    """Set `client.slug`, from an explicit request or derived from the name.

    Raises `SlugError` when an explicitly requested slug is malformed, reserved
    or already taken — an admin typo must fail loudly rather than silently
    routing one tenant's subdomain at another.
    """
    if requested:
        slug = validate_slug(requested)
        clash = db.execute(
            select(Client.id).where(Client.slug == slug, Client.id != client.id).limit(1)
        ).scalar_one_or_none()
        if clash is not None:
            raise SlugError(f"'{slug}' is already used by another company.")
    else:
        slug = generate_unique_slug(db, client.name, exclude_id=client.id)
    client.slug = slug
    return slug
