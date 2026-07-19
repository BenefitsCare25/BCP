"""Insured-entity alias map — CRUD.

Bridges spellings `normalize_entity` can't fold. It exists because the two
sides of the entity gate serve different masters: `plan_assignments.insured`
is reproduced VERBATIM on the exported placement slip (a legal document, so it
must keep the registered name), while matching has to equal the roster's
spelling. Rewriting `insured` to make matching work would corrupt the slip —
so the legal name stays and an alias bridges it.

Per-client by construction: entity names are a client's own subsidiaries,
never a shared library, so there is no global (NULL client_id) tier here —
unlike the insurer catalog.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_client_id
from app.db.session import get_db
from app.models import EntityAlias
from app.schemas.entity_alias import EntityAliasIn, EntityAliasOut, EntityAliasPatch
from app.services.matching_engine import normalize_entity

router = APIRouter(tags=["entity-aliases"])


def _out(row: EntityAlias) -> EntityAliasOut:
    return EntityAliasOut(
        id=row.id,
        alias=row.alias,
        canonical=row.canonical,
        alias_normalized=row.alias_normalized,
    )


def _load_owned(alias_id: str, client_id: str, db: Session) -> EntityAlias:
    row = db.get(EntityAlias, alias_id)
    # 404 (not 403) on another tenant's row — never leak that it exists.
    if row is None or row.client_id != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alias not found")
    return row


def _assert_usable(alias: str, canonical: str, client_id: str, db: Session,
                   *, exclude_id: str | None = None) -> str:
    """Validate the pair and return the normalized alias.

    Rejects a self-mapping (normalizes to the same thing, so it would do
    nothing) and a duplicate alias. Chains are NOT rejected — `resolve_entity`
    is single-hop, so an A→B, B→C map resolves deterministically rather than
    looping — but the alias side must be unique or the map is ambiguous.
    """
    norm_alias = normalize_entity(alias)
    norm_canon = normalize_entity(canonical)
    if not norm_alias or not norm_canon:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Both the alias and the entity it maps to must be real names.",
        )
    if norm_alias == norm_canon:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{alias!r} and {canonical!r} already compare equal — no alias needed.",
        )
    clash = db.execute(
        select(EntityAlias).where(
            EntityAlias.client_id == client_id,
            EntityAlias.alias_normalized == norm_alias,
        )
    ).scalar_one_or_none()
    if clash is not None and clash.id != exclude_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{alias!r} is already mapped to {clash.canonical!r}.",
        )
    return norm_alias


@router.get("/entity-aliases", response_model=list[EntityAliasOut])
def list_entity_aliases(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EntityAliasOut]:
    client_id = require_client_id(user)
    rows = db.execute(
        select(EntityAlias)
        .where(EntityAlias.client_id == client_id)
        .order_by(EntityAlias.alias)
    ).scalars()
    return [_out(r) for r in rows]


@router.post(
    "/entity-aliases",
    response_model=EntityAliasOut,
    status_code=status.HTTP_201_CREATED,
)
def create_entity_alias(
    payload: EntityAliasIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EntityAliasOut:
    client_id = require_client_id(user)
    norm = _assert_usable(payload.alias, payload.canonical, client_id, db)
    row = EntityAlias(
        client_id=client_id,
        alias=payload.alias,
        canonical=payload.canonical,
        alias_normalized=norm,
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        user,
        action="create",
        entity_type="entity_alias",
        entity_id=row.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch("/entity-aliases/{alias_id}", response_model=EntityAliasOut)
def update_entity_alias(
    alias_id: str,
    payload: EntityAliasPatch,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EntityAliasOut:
    client_id = require_client_id(user)
    row = _load_owned(alias_id, client_id, db)
    patch = payload.model_dump(exclude_unset=True)
    alias = patch.get("alias", row.alias)
    canonical = patch.get("canonical", row.canonical)
    norm = _assert_usable(alias, canonical, client_id, db, exclude_id=row.id)
    before = {"alias": row.alias, "canonical": row.canonical}
    row.alias, row.canonical, row.alias_normalized = alias, canonical, norm
    db.flush()
    write_audit(
        db,
        user,
        action="update",
        entity_type="entity_alias",
        entity_id=row.id,
        before=before,
        after=patch,
    )
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete(
    "/entity-aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_entity_alias(
    alias_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Removing an alias un-bridges the two spellings — employees matched only
    through it become unmatched on the next run. Nothing is rewritten."""
    client_id = require_client_id(user)
    row = _load_owned(alias_id, client_id, db)
    before = {"alias": row.alias, "canonical": row.canonical}
    db.delete(row)
    write_audit(
        db,
        user,
        action="delete",
        entity_type="entity_alias",
        entity_id=alias_id,
        before=before,
    )
    db.commit()
