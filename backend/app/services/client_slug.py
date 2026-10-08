"""Derive the company alias (`clients.slug`) from a client's name.

`resolve_tenant_context` looks a company up by `clients.slug` within the broker
firm the request's host names, so a client with a NULL slug is unreachable on
the HR surface and the portal credential login — they simply 404 for that
company. Nothing used to populate the column, so every client was in exactly
that state; this module is the single writer.

**Aliases are unique per broker firm, not globally** (`uq_clients_firm_slug`).
The host already names the firm, so two brokers may each have an "acme";
uniqueness checks, collision suffixes and error messages are all scoped to the
company's own firm, and never reveal another firm's companies.

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
ALIAS_TAKEN = "Another company in this firm already uses that alias."


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


def _taken(db: Session, broker_firm_id: str, slug: str, exclude_id: str | None) -> bool:
    """Whether another company of the same firm already holds `slug`."""
    stmt = select(Client.id).where(Client.broker_firm_id == broker_firm_id, Client.slug == slug)
    if exclude_id:
        stmt = stmt.where(Client.id != exclude_id)
    return db.execute(stmt.limit(1)).scalar_one_or_none() is not None


def generate_unique_slug(
    db: Session, name: str, *, broker_firm_id: str, exclude_id: str | None = None
) -> str:
    """A slug for `name` that no OTHER company of `broker_firm_id` holds.

    Collisions within the firm get a numeric suffix, then a random one. Another
    firm's companies never count: the same alias there is a different company
    on a different host.
    """
    base = slugify_client_name(name)
    if not _taken(db, broker_firm_id, base, exclude_id):
        return base
    for n in range(2, 100):
        candidate = f"{base[: _MAX_LABEL - len(str(n)) - 1]}-{n}"
        if not _taken(db, broker_firm_id, candidate, exclude_id):
            return candidate
    while True:  # pragma: no cover - astronomically unlikely
        candidate = f"{base[:_BASE_BUDGET]}-{secrets.token_hex(3)}"
        if not _taken(db, broker_firm_id, candidate, exclude_id):
            return candidate


def assign_slug(
    db: Session, client: Client, requested: str | None = None
) -> str:
    """Set `client.slug`, from an explicit request or derived from the name.

    `client.broker_firm_id` must already be set: uniqueness is per firm. Raises
    `SlugError` when an explicitly requested slug is malformed, reserved or
    already taken within the firm — an admin typo must fail loudly rather than
    silently routing one company's portal at another.
    """
    if requested:
        slug = validate_slug(requested)
        if _taken(db, client.broker_firm_id, slug, client.id):
            raise SlugError(ALIAS_TAKEN)
    else:
        slug = generate_unique_slug(
            db, client.name, broker_firm_id=client.broker_firm_id, exclude_id=client.id
        )
    client.slug = slug
    return slug
