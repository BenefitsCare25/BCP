"""Broker config: the claim document-type registry (aliases + key fields).

Per-client rows, lazily seeded from the in-code defaults on first read.
Edited on the broker claims page; consumed by intake classification and the
AI-review completeness check (which fall back to the defaults when a client
has no rows, so deleting everything can never break claims)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_client_id
from app.db.session import get_db
from app.models import ClaimDocType
from app.schemas.claims import ClaimDocKeyField, ClaimDocTypeIn, ClaimDocTypeOut
from app.services.claim_doc_types import (
    DEFAULT_KEYS,
    client_doc_type_rows,
    definition_from_row,
    seed_default_doc_types,
)
from app.services.claim_intake import DOC_SLOT_LABELS

router = APIRouter(prefix="/claim-doc-types", tags=["claim-doc-types"])

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
            ClaimDocKeyField(name=kf.name, keywords=list(kf.tokens))
            for kf in d.key_fields
        ],
        sector=d.sector,
        slot_key=row.slot_key,
        is_default=row.key in DEFAULT_KEYS,
    )


def _own_row(db: Session, doc_type_id: str, client_id: str) -> ClaimDocType:
    row = db.get(ClaimDocType, doc_type_id)
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


def _payload(body: ClaimDocTypeIn) -> dict:
    return {
        "display": body.display.strip(),
        "aliases": _clean_aliases(body.aliases),
        "key_fields": [
            {
                "name": kf.name.strip(),
                "keywords": [k.strip() for k in kf.keywords if k.strip()],
            }
            for kf in body.key_fields
            if kf.name.strip()
        ],
        "sector": body.sector,
        "slot_key": body.slot_key,
    }


@router.get("", response_model=list[ClaimDocTypeOut])
def list_claim_doc_types(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimDocTypeOut]:
    client_id = require_client_id(user)
    rows = client_doc_type_rows(db, client_id)
    if not rows:
        rows = seed_default_doc_types(db, client_id)
        db.commit()
    return [_out(r) for r in rows]


@router.post("", response_model=ClaimDocTypeOut, status_code=status.HTTP_201_CREATED)
def create_claim_doc_type(
    body: ClaimDocTypeIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimDocTypeOut:
    client_id = require_client_id(user)
    _validate_slot_key(body.slot_key)
    data = _payload(body)
    key = _SLUG_RE.sub("_", data["display"].lower()).strip("_")[:64]
    if not key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a document type name."
        )
    existing = {r.key for r in client_doc_type_rows(db, client_id)}
    if key in existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_doc_type",
                "message": f'A document type named "{data["display"]}" already exists.',
            },
        )
    row = ClaimDocType(client_id=client_id, key=key, **data)
    db.add(row)
    db.flush()
    write_audit(db, user, "claim_doc_type.created", "claim_doc_type", row.id, after=data)
    db.commit()
    return _out(row)


@router.put("/{doc_type_id}", response_model=ClaimDocTypeOut)
def update_claim_doc_type(
    doc_type_id: str,
    body: ClaimDocTypeIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimDocTypeOut:
    client_id = require_client_id(user)
    row = _own_row(db, doc_type_id, client_id)
    _validate_slot_key(body.slot_key)
    data = _payload(body)
    before = {
        "display": row.display,
        "aliases": row.aliases,
        "key_fields": row.key_fields,
        "sector": row.sector,
        "slot_key": row.slot_key,
    }
    for field, value in data.items():
        setattr(row, field, value)
    write_audit(
        db, user, "claim_doc_type.updated", "claim_doc_type", row.id,
        before=before, after=data,
    )
    db.commit()
    return _out(row)


@router.delete("/{doc_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_claim_doc_type(
    doc_type_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    client_id = require_client_id(user)
    row = _own_row(db, doc_type_id, client_id)
    write_audit(
        db, user, "claim_doc_type.deleted", "claim_doc_type", row.id,
        before={"key": row.key, "display": row.display},
    )
    db.delete(row)
    db.commit()


@router.post("/reset", response_model=list[ClaimDocTypeOut])
def reset_claim_doc_types(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimDocTypeOut]:
    """Discard the client's customisations and restore the seeded defaults."""
    client_id = require_client_id(user)
    for row in client_doc_type_rows(db, client_id):
        db.delete(row)
    db.flush()
    rows = seed_default_doc_types(db, client_id)
    write_audit(db, user, "claim_doc_type.reset", "claim_doc_type", None)
    db.commit()
    return [_out(r) for r in rows]
