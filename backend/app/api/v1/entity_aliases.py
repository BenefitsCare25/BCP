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

from fastapi import APIRouter, Depends, HTTPException, Response, status
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


def _canonicals(row: EntityAlias) -> list[str]:
    """The alias's entity list, falling back to the single `canonical` for a
    row written before the `canonicals` column existed."""
    stored = row.canonicals
    if isinstance(stored, list) and stored:
        return list(stored)
    return [row.canonical]


def _out(row: EntityAlias) -> EntityAliasOut:
    canonicals = _canonicals(row)
    return EntityAliasOut(
        id=row.id,
        alias=row.alias,
        canonical=canonicals[0],
        canonicals=canonicals,
        alias_normalized=row.alias_normalized,
    )


def _load_owned(alias_id: str, client_id: str, db: Session) -> EntityAlias:
    row = db.get(EntityAlias, alias_id)
    # 404 (not 403) on another tenant's row — never leak that it exists.
    if row is None or row.client_id != client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alias not found")
    return row


def _prepare(alias: str, canonicals: list[str]) -> tuple[str, list[str]]:
    """Validate an alias + its entity list; return (normalized alias, deduped
    raw canonicals).

    Rejects a canonical that normalizes to the alias itself (it would do
    nothing) and collapses canonicals that normalize to the same entity, keeping
    the first spelling. Chains are NOT rejected — `resolve_entities` is
    single-hop, so an A→B, B→C map resolves deterministically rather than
    looping.
    """
    norm_alias = normalize_entity(alias)
    if not norm_alias:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The alias must be a real name."
        )
    seen: set[str] = set()
    out: list[str] = []
    for c in canonicals:
        nc = normalize_entity(c)
        if not nc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{c!r} is not a real name."
            )
        if nc == norm_alias:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{alias!r} and {c!r} already compare equal — no alias needed.",
            )
        if nc in seen:
            continue
        seen.add(nc)
        out.append(c)
    if not out:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "An alias must map to at least one entity.",
        )
    return norm_alias, out


def _find_by_alias(
    norm_alias: str, client_id: str, db: Session
) -> EntityAlias | None:
    return db.execute(
        select(EntityAlias).where(
            EntityAlias.client_id == client_id,
            EntityAlias.alias_normalized == norm_alias,
        )
    ).scalar_one_or_none()


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
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EntityAliasOut:
    """Create the alias, or — when it already exists — MERGE the new entities
    into it. One row per alias (the unique constraint is unchanged), so adding a
    second entity to an existing alias appends rather than conflicts. This is
    what lets a roster spelling stand for several subsidiaries, and it makes the
    reconciliation panel's one-click "same entity" idempotent."""
    client_id = require_client_id(user)
    norm_alias, targets = _prepare(payload.alias, payload.canonicals)

    existing = _find_by_alias(norm_alias, client_id, db)
    if existing is not None:
        current = _canonicals(existing)
        seen = {normalize_entity(c) for c in current}
        merged = list(current)
        for c in targets:
            if normalize_entity(c) not in seen:
                seen.add(normalize_entity(c))
                merged.append(c)
        before = {"alias": existing.alias, "canonicals": current}
        existing.canonicals = merged
        existing.canonical = merged[0]
        db.flush()
        write_audit(
            db,
            user,
            action="update",
            entity_type="entity_alias",
            entity_id=existing.id,
            before=before,
            after={"alias": existing.alias, "canonicals": merged},
        )
        db.commit()
        db.refresh(existing)
        response.status_code = status.HTTP_200_OK
        return _out(existing)

    row = EntityAlias(
        client_id=client_id,
        alias=payload.alias,
        canonical=targets[0],
        canonicals=targets,
        alias_normalized=norm_alias,
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        user,
        action="create",
        entity_type="entity_alias",
        entity_id=row.id,
        after={"alias": payload.alias, "canonicals": targets},
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
    alias = payload.alias or row.alias
    # A supplied list REPLACES the alias's entities; omit it to keep them.
    raw_targets = (
        payload.canonicals if payload.canonicals is not None else _canonicals(row)
    )
    norm_alias, targets = _prepare(alias, raw_targets)

    clash = _find_by_alias(norm_alias, client_id, db)
    if clash is not None and clash.id != row.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{alias!r} is already an alias — edit that one to add entities.",
        )

    before = {"alias": row.alias, "canonicals": _canonicals(row)}
    row.alias, row.canonical, row.canonicals, row.alias_normalized = (
        alias,
        targets[0],
        targets,
        norm_alias,
    )
    db.flush()
    write_audit(
        db,
        user,
        action="update",
        entity_type="entity_alias",
        entity_id=row.id,
        before=before,
        after={"alias": alias, "canonicals": targets},
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
