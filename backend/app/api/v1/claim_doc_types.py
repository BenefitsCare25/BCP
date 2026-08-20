"""Broker config: the claim document-type registry (aliases + key fields).

Per-client rows, lazily seeded from the in-code defaults on first read.
Edited on the broker claims page; consumed by intake classification and the
AI-review completeness check (which fall back to the defaults when a client
has no rows, so deleting everything can never break claims)."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_claim_configuration, require_client_id
from app.core.optimistic_lock import assert_collection_not_stale, assert_not_stale
from app.db.session import get_db
from app.models import ClaimDocType
from app.schemas.claims import (
    ClaimDocKeyField,
    ClaimDocTypeIn,
    ClaimDocTypeOut,
    ClaimDocTypeUpdateIn,
    ResetClaimDocTypesIn,
    UpdateClaimDocScopeAssignmentsIn,
)
from app.services.claim_doc_types import (
    DEFAULT_KEYS,
    client_doc_type_rows,
    definition_from_row,
    seed_default_doc_types,
)
from app.services.claim_intake import CLAIM_SCOPE_CODES, DOC_SLOT_LABELS

router = APIRouter(
    prefix="/claim-doc-types",
    tags=["claim-doc-types"],
    dependencies=[Depends(require_claim_configuration)],
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _out(row: ClaimDocType) -> ClaimDocTypeOut:
    # Read through the definition builder so legacy/hand-edited JSON shapes
    # render defensively instead of failing response validation.
    d = definition_from_row(row)
    return ClaimDocTypeOut(
        id=row.id,
        key=row.key,
        display=row.display,
        aliases=[str(a) for a in (row.aliases or []) if str(a).strip()],
        key_fields=[
            ClaimDocKeyField(
                name=kf.name, keywords=list(kf.tokens), optional=kf.optional
            )
            for kf in d.key_fields
        ],
        sector=d.sector,
        slot_key=row.slot_key,
        claim_scope_keys=list(d.claim_scope_keys),
        is_default=row.key in DEFAULT_KEYS,
        updated_at=row.updated_at,
    )


def _own_row(db: Session, doc_type_id: str, client_id: str) -> ClaimDocType:
    row = db.get(ClaimDocType, doc_type_id)
    if row is None and doc_type_id.startswith("default:"):
        key = doc_type_id.removeprefix("default:")
        rows = client_doc_type_rows(db, client_id)
        if not rows:
            rows = seed_default_doc_types(db, client_id)
        row = next((candidate for candidate in rows if candidate.key == key), None)
    if row is None or row.client_id != client_id:
        # Same not-403 convention as tenant scoping everywhere else.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document type not found")
    return row


def _validate_slot_key(slot_key: str | None) -> None:
    if slot_key is not None and slot_key not in DOC_SLOT_LABELS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{slot_key}' is not a recognised document slot.",
        )


def _clean_aliases(aliases: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in aliases:
        a = " ".join(a.split())
        if not a or len(a) > 128 or a.lower() in seen:
            continue
        seen.add(a.lower())
        out.append(a)
    return out


def _clean_claim_scope_keys(values: list[str]) -> list[str]:
    """Normalize and structurally validate configured intake targets."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = "".join(str(raw).split()).casefold()
        parts = key.split(":")
        valid = (
            len(key) <= 256
            and len(parts) == 3
            and parts[0] == "insured"
            and bool(parts[1])
            and parts[2] in CLAIM_SCOPE_CODES
        ) or (
            len(key) <= 256
            and len(parts) == 2
            and parts[0] == "flex"
            and bool(parts[1])
        )
        if not valid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"'{raw}' is not a recognised claim scope.",
            )
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _payload(body: ClaimDocTypeIn) -> dict[str, Any]:
    return {
        "display": body.display.strip(),
        "aliases": _clean_aliases(body.aliases),
        "key_fields": [
            {
                "name": kf.name.strip(),
                "keywords": [k.strip() for k in kf.keywords if k.strip()],
                "optional": kf.optional,
            }
            for kf in body.key_fields
            if kf.name.strip()
        ],
        "sector": body.sector,
        "slot_key": body.slot_key,
        "claim_scope_keys": _clean_claim_scope_keys(body.claim_scope_keys),
    }


def _ensure_seeded(db: Session, client_id: str) -> list[ClaimDocType]:
    """Return configured rows; callers render immutable defaults when absent."""
    return client_doc_type_rows(db, client_id)


def _assert_aliases_unambiguous(
    rows: list[ClaimDocType],
    *,
    aliases: list[str],
    sector: str | None,
    exclude_id: str | None,
) -> None:
    """Reject aliases that make two same-sector definitions order-dependent.

    Government/private invoice definitions may intentionally share aliases;
    marker fields and the claim's hospital sector disambiguate that pair.
    Two neutral definitions, or two definitions in the same sector, cannot be
    distinguished and `classify_document` would silently pick the first row.
    """
    wanted = {" ".join(alias.split()).casefold() for alias in aliases}
    for row in rows:
        if row.id == exclude_id or row.sector != sector:
            continue
        overlap = wanted & {
            " ".join(str(alias).split()).casefold() for alias in (row.aliases or [])
        }
        if overlap:
            alias = sorted(overlap)[0]
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "ambiguous_doc_type_alias",
                    "message": (
                        f'Alias "{alias}" is already used by "{row.display}". '
                        "Use a distinct alias so document placement is deterministic."
                    ),
                },
            )


@router.get("", response_model=list[ClaimDocTypeOut])
def list_claim_doc_types(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimDocTypeOut]:
    client_id = require_client_id(user)
    rows = _ensure_seeded(db, client_id)
    if not rows:
        rows = seed_default_doc_types(db, client_id)
        db.commit()
    return [_out(row) for row in rows]


@router.post("", response_model=ClaimDocTypeOut, status_code=status.HTTP_201_CREATED)
def create_claim_doc_type(
    body: ClaimDocTypeIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimDocTypeOut:
    client_id = require_client_id(user)
    _validate_slot_key(body.slot_key)
    data = _payload(body)
    base = _SLUG_RE.sub("_", data["display"].lower()).strip("_")[:64]
    if not base:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a document type name."
        )
    existing = client_doc_type_rows(db, client_id)
    if not existing:
        existing = seed_default_doc_types(db, client_id)
    _assert_aliases_unambiguous(
        existing,
        aliases=data["aliases"],
        sector=data["sector"],
        exclude_id=None,
    )
    # A true duplicate is the same DISPLAY (case-insensitive), not merely the
    # same slug — "Referral Memo" and "Referral-Memo" are distinct types that
    # happen to slugify alike, so they must both be creatable.
    if any(r.display.strip().lower() == data["display"].lower() for r in existing):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_doc_type",
                "message": f'A document type named "{data["display"]}" already exists.',
            },
        )
    # Derive a unique key: suffix -2/-3… when the slug is already taken.
    taken = {r.key for r in existing}
    key = base
    n = 2
    while key in taken:
        suffix = f"_{n}"
        key = f"{base[: 64 - len(suffix)]}{suffix}"
        n += 1
    row = ClaimDocType(client_id=client_id, key=key, **data)
    db.add(row)
    db.flush()
    write_audit(db, user, "claim_doc_type.created", "claim_doc_type", row.id, after=data)
    db.commit()
    return _out(row)


@router.post("/scope-assignments", response_model=list[ClaimDocTypeOut])
def update_claim_doc_scope_assignments(
    body: UpdateClaimDocScopeAssignmentsIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimDocTypeOut]:
    """Atomically replace document mappings for a claim-scope duplication.

    One mapping spans several document-type rows. Saving them in one
    transaction prevents a partially duplicated claim setup when a stale row
    or validation error is encountered halfway through.
    """
    client_id = require_client_id(user)
    if len({assignment.id for assignment in body.assignments}) != len(
        body.assignments
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Each document type may appear only once.",
        )

    rows = [
        _own_row(db, assignment.id, client_id)
        for assignment in body.assignments
    ]
    now = datetime.now(UTC)
    for row, assignment in zip(rows, body.assignments, strict=True):
        assert_not_stale(
            expected=assignment.expected_updated_at,
            actual=row.updated_at,
            label=f'"{row.display}" document mapping',
        )
        scopes = _clean_claim_scope_keys(assignment.claim_scope_keys)
        before = {"claim_scope_keys": row.claim_scope_keys}
        row.claim_scope_keys = scopes
        row.updated_at = now
        write_audit(
            db,
            user,
            "claim_doc_type.scope_assignments_updated",
            "claim_doc_type",
            row.id,
            before=before,
            after={"claim_scope_keys": scopes},
        )
    db.commit()
    return [_out(row) for row in rows]


@router.put("/{doc_type_id}", response_model=ClaimDocTypeOut)
def update_claim_doc_type(
    doc_type_id: str,
    body: ClaimDocTypeUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimDocTypeOut:
    client_id = require_client_id(user)
    row = _own_row(db, doc_type_id, client_id)
    assert_not_stale(
        expected=body.expected_updated_at,
        actual=row.updated_at,
        label="This document type",
    )
    _validate_slot_key(body.slot_key)
    data = _payload(body)
    if "claim_scope_keys" not in body.model_fields_set:
        # Backward-compatible for older broker clients: omission means leave
        # routing untouched, while an explicit [] deliberately clears it.
        data["claim_scope_keys"] = row.claim_scope_keys
    _assert_aliases_unambiguous(
        client_doc_type_rows(db, client_id),
        aliases=data["aliases"],
        sector=data["sector"],
        exclude_id=row.id,
    )
    before = {
        "display": row.display,
        "aliases": row.aliases,
        "key_fields": row.key_fields,
        "sector": row.sector,
        "slot_key": row.slot_key,
        "claim_scope_keys": row.claim_scope_keys,
    }
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(UTC)
    write_audit(
        db, user, "claim_doc_type.updated", "claim_doc_type", row.id,
        before=before, after=data,
    )
    db.commit()
    return _out(row)


@router.delete("/{doc_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_claim_doc_type(
    doc_type_id: str,
    expected_updated_at: datetime | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    client_id = require_client_id(user)
    row = _own_row(db, doc_type_id, client_id)
    assert_not_stale(
        expected=expected_updated_at,
        actual=row.updated_at,
        label="This document type",
    )
    write_audit(
        db, user, "claim_doc_type.deleted", "claim_doc_type", row.id,
        before={"key": row.key, "display": row.display},
    )
    db.delete(row)
    db.commit()


@router.post("/reset", response_model=list[ClaimDocTypeOut])
def reset_claim_doc_types(
    body: ResetClaimDocTypesIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimDocTypeOut]:
    """Discard the client's customisations and restore the seeded defaults."""
    client_id = require_client_id(user)
    existing = client_doc_type_rows(db, client_id)
    assert_collection_not_stale(
        rows=list(existing),
        expected_versions=body.expected_versions,
        label="Document-type settings",
    )
    for row in existing:
        db.delete(row)
    db.flush()
    rows = seed_default_doc_types(db, client_id)
    write_audit(db, user, "claim_doc_type.reset", "claim_doc_type", None)
    db.commit()
    return [_out(r) for r in rows]
