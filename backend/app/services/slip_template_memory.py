"""Per-tenant memory of broker-corrected placement-slip column mappings.

Bridges the pure parser (which knows nothing about the DB) to stored overrides:
``make_resolver`` returns a fingerprint -> roles-dict callback the parser calls
during extraction, and ``save_profile`` upserts a broker's correction so the
next upload of the same template reuses it.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.slip_template_profile import SlipTemplateProfile
from app.services.placement_slip_parser import ProfileResolver, roles_from_dict


def make_resolver(db: Session, client_id: str) -> ProfileResolver:
    """A fingerprint -> stored-roles resolver scoped to one tenant.

    Returns the saved column mapping (as a dict) for a matching template, or
    None. Lookups are cached per-call so repeated sheets don't re-query.
    """
    cache: dict[str, dict[str, Any] | None] = {}

    def resolve(fingerprint: str) -> dict[str, Any] | None:
        if fingerprint in cache:
            return cache[fingerprint]
        row = db.execute(
            select(SlipTemplateProfile).where(
                SlipTemplateProfile.client_id == client_id,
                SlipTemplateProfile.fingerprint == fingerprint,
            )
        ).scalar_one_or_none()
        roles = row.roles if row else None
        cache[fingerprint] = roles
        return roles

    return resolve


# Keys the broker may set in a correction; everything else is ignored so a
# malformed payload can't smuggle arbitrary data into the parser.
_ALLOWED_ROLE_KEYS = frozenset(
    {"name_col", "key_col", "value_col", "allow_letter_keys", "name_first"}
)


def normalize_roles(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + canonicalize a broker-supplied column mapping.

    Round-trips through ``roles_from_dict`` so stored overrides always carry the
    same shape the parser consumes, and rejects unknown keys.
    """
    cleaned = {k: v for k, v in raw.items() if k in _ALLOWED_ROLE_KEYS}
    from app.services.placement_slip_parser import roles_to_dict

    return roles_to_dict(roles_from_dict(cleaned))


def save_profile(
    db: Session,
    *,
    client_id: str,
    fingerprint: str,
    product_code: str,
    roles: dict[str, Any],
    insurer: str | None = None,
    sheet_label: str | None = None,
    created_by: str | None = None,
) -> SlipTemplateProfile:
    """Upsert a tenant's column-mapping override for a template fingerprint."""
    row = db.execute(
        select(SlipTemplateProfile).where(
            SlipTemplateProfile.client_id == client_id,
            SlipTemplateProfile.fingerprint == fingerprint,
        )
    ).scalar_one_or_none()
    cleaned = normalize_roles(roles)
    if row is None:
        row = SlipTemplateProfile(
            client_id=client_id,
            fingerprint=fingerprint,
            product_code=product_code,
            insurer=insurer,
            sheet_label=sheet_label,
            roles=cleaned,
            created_by=created_by,
        )
        db.add(row)
    else:
        row.product_code = product_code
        row.insurer = insurer
        row.sheet_label = sheet_label
        row.roles = cleaned
        row.created_by = created_by
    db.flush()
    return row
