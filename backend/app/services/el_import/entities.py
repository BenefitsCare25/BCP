"""Reconcile a listing's Entity spellings with the entities that gate matching.

Categories restricted to a legal entity only match employees whose Entity
resolves to it. Listings spell entities freely — WUXI lists two entities in
five spellings, one with a typo on 126 rows — so the review shows each
spelling, whether the matcher's own gate (`entity_vocab.gating_entities`,
alias-resolved) accepts it, and for one it rejects, the gate entity it most
likely means (the reconciliation panel's rule). The broker ticks a link to
create the same alias the panel would; neither name is rewritten.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.models import EntityAlias
from app.schemas.employee_listing import ListingEntityOut
from app.services.el_workbook import ElWorkbook
from app.services.el_workbook.values import text
from app.services.entity_alias_store import alias_canonicals, merge_alias
from app.services.entity_vocab import closest_entity, gating_entities
from app.services.matching_engine import (
    EntityAliases,
    entity_alias_map,
    normalize_entity,
    resolve_entities,
)


def _gate_spellings(
    db: Session, client_id: str, policy_year_id: str, aliases: EntityAliases
) -> dict[str, str]:
    """{resolved gate entity: a spelling that resolves to it directly}.

    The gate keeps the spelling the slip used, which may itself be an alias
    ("CSO"). A link must point at the entity's own spelling: aliases resolve a
    single hop, so linking to "CSO" would resolve to "cso" and miss the gate.
    """
    gate = gating_entities(db, policy_year_id, aliases)
    out = {norm: raw for norm, raw in gate.items() if normalize_entity(raw) == norm}
    missing = set(gate) - set(out)
    if missing:
        for row in db.execute(
            select(EntityAlias).where(EntityAlias.client_id == client_id)
        ).scalars():
            for spelling in alias_canonicals(row):
                if (norm := normalize_entity(spelling)) in missing:
                    out.setdefault(norm, spelling)
    for norm in missing - set(out):
        out[norm] = norm
    return out


def listing_entities(
    db: Session, client_id: str, policy_year_id: str, workbook: ElWorkbook
) -> list[ListingEntityOut]:
    """Each Entity spelling on the listing with its headcount and resolution.
    Empty when the listing has no Entity column or nothing gates by entity."""
    if not _spellings(workbook):
        return []
    aliases = entity_alias_map(db, client_id)
    return _entity_rows(workbook, aliases, _gate_spellings(db, client_id, policy_year_id, aliases))


def _spellings(workbook: ElWorkbook) -> Counter[str]:
    return Counter(
        value for emp in workbook.employees if (value := text(emp.fields.get("entity")))
    )


def _entity_rows(
    workbook: ElWorkbook, aliases: EntityAliases, gate: dict[str, str]
) -> list[ListingEntityOut]:
    spellings = _spellings(workbook)
    if not spellings or not gate:
        return []
    out: list[ListingEntityOut] = []
    for spelling, employees in spellings.most_common():
        resolved = sorted(resolve_entities(spelling, aliases) & set(gate))
        if resolved:
            out.append(ListingEntityOut(
                spelling=spelling, employees=employees,
                status="matched" if normalize_entity(spelling) in gate else "aliased",
                entity=gate[resolved[0]],
            ))
        else:
            out.append(ListingEntityOut(
                spelling=spelling, employees=employees, status="unmatched",
                suggestion=closest_entity(spelling, sorted(gate.values())),
            ))
    return out


def link_entities(
    db: Session, user: CurrentUser, client_id: str, policy_year_id: str,
    workbook: ElWorkbook, links: dict[str, str],
) -> int:
    """Create an alias for each unmatched listing spelling the broker linked
    to a gate entity. Links naming anything else are ignored. Returns how many
    aliases were written."""
    if not links:
        return 0
    aliases = entity_alias_map(db, client_id)
    gate = _gate_spellings(db, client_id, policy_year_id, aliases)
    unmatched = {
        e.spelling for e in _entity_rows(workbook, aliases, gate) if e.status == "unmatched"
    }
    written = 0
    for spelling, target in sorted(links.items()):
        canonical = gate.get(normalize_entity(target))
        if spelling not in unmatched or canonical is None:
            continue
        merge_alias(db, user, client_id, spelling, [canonical])
        written += 1
    return written
