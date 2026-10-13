"""Write an insured-entity alias: create it, or merge entities into it.

Shared by the alias API and the Employee Listing import, which links a
listing's spelling of an entity to the slip's registered name on apply. One
row per normalized alias, so linking a second entity to the same spelling
appends rather than conflicts. Audited; the caller commits.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.models import EntityAlias
from app.services.matching_engine import normalize_entity


def alias_canonicals(row: EntityAlias) -> list[str]:
    """The alias's entity list, falling back to the single `canonical` for a
    row written before the `canonicals` column existed."""
    stored = row.canonicals
    if isinstance(stored, list) and stored:
        return list(stored)
    return [row.canonical]


def find_alias(db: Session, client_id: str, norm_alias: str) -> EntityAlias | None:
    return db.execute(
        select(EntityAlias).where(
            EntityAlias.client_id == client_id,
            EntityAlias.alias_normalized == norm_alias,
        )
    ).scalar_one_or_none()


def merge_alias(
    db: Session, user: CurrentUser, client_id: str, alias: str, targets: list[str]
) -> tuple[EntityAlias, bool]:
    """Create ``alias`` → ``targets`` or merge the targets into the existing
    alias. ``targets`` must already be validated and de-duplicated. Returns the
    row and whether it was created."""
    norm_alias = normalize_entity(alias)
    existing = find_alias(db, client_id, norm_alias)
    if existing is not None:
        current = alias_canonicals(existing)
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
            db, user, action="update", entity_type="entity_alias", entity_id=existing.id,
            before=before, after={"alias": existing.alias, "canonicals": merged},
        )
        return existing, False
    row = EntityAlias(
        client_id=client_id, alias=alias, canonical=targets[0], canonicals=targets,
        alias_normalized=norm_alias,
    )
    db.add(row)
    db.flush()
    write_audit(
        db, user, action="create", entity_type="entity_alias", entity_id=row.id,
        after={"alias": alias, "canonicals": targets},
    )
    return row, True
