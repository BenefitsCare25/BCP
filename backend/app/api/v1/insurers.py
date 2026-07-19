"""Insurer name catalog — CRUD over the vocabulary behind every insurer field.

The catalog is a *name registry*, not a foreign-key target (see
``app/models/insurer.py``). Nothing downstream resolves through it: products
keep storing the canonical name as a string. That has two consequences the
handlers below lean on:

* Renaming or deleting an entry cannot break a product, a report, or a roster
  member-id key — it only changes what the dropdown offers. So neither is
  blocked; ``in_use`` is surfaced instead so the UI can warn.
* Duplicate *names* are the real hazard, because two spellings of one insurer
  split its report into two. Create/rename therefore check the incoming name
  against existing names **and their aliases**.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    load_editable_global,
    require_client_id,
    tenant_or_global,
)
from app.db.session import get_db
from app.models import Insurer, PanelCard, PanelListing, Product
from app.schemas.insurer import InsurerIn, InsurerOut, InsurerPatch

router = APIRouter(tags=["insurers"])


def _visible(user: CurrentUser, db: Session) -> list[Insurer]:
    """Library rows (client_id NULL) plus the active client's own entries."""
    return list(
        db.execute(
            select(Insurer)
            .where(tenant_or_global(Insurer.client_id, user.client_id))
            .order_by(Insurer.name)
        )
        .scalars()
        .all()
    )


def _names_in_use(user: CurrentUser, db: Session) -> set[str]:
    """Lowercased insurer strings currently stored anywhere the name is a join
    key. Comparison is case-insensitive because that is how the reports module
    groups, so what the UI flags as in-use is what actually feeds a report.

    All three consumers must be covered: a name used only by a panel listing or
    an e-card is still load-bearing (the card renderer and clinic locator key
    off it), so reporting it as unused would make the delete dialog lie."""
    names: set[str] = set()
    for column, client_column in (
        (Product.insurer, Product.client_id),
        (PanelListing.insurer, PanelListing.client_id),
        (PanelCard.insurer, PanelCard.client_id),
    ):
        rows = db.execute(
            select(column).where(
                tenant_or_global(client_column, user.client_id),
                column.is_not(None),
            )
        ).scalars()
        names.update((v or "").strip().lower() for v in rows if (v or "").strip())
    return names


def _out(row: Insurer, in_use: set[str]) -> InsurerOut:
    return InsurerOut(
        id=row.id,
        client_id=row.client_id,
        name=row.name,
        legal_name=row.legal_name,
        aliases=row.alias_list,
        notes=row.notes,
        in_use=row.name.strip().lower() in in_use,
    )


def _assert_name_free(
    name: str, rows: list[Insurer], *, exclude_id: str | None = None
) -> None:
    """409 when ``name`` collides with a visible entry's name or one of its
    aliases. The alias arm is the point of the check: a broker adding "GE" when
    "Great Eastern" already lists GE as an alias would otherwise split that
    insurer's products across two report groups."""
    target = name.strip().lower()
    for row in rows:
        if row.id == exclude_id:
            continue
        if row.name.strip().lower() == target:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Insurer {row.name!r} already exists.",
            )
        for alias in row.alias_list:
            if alias.strip().lower() == target:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{name!r} is already listed as an alias of {row.name!r}. "
                    "Edit that entry instead of adding a second one.",
                )


def _load_editable(insurer_id: str, user: CurrentUser, db: Session) -> Insurer:
    return load_editable_global(Insurer, insurer_id, user, db, "Insurer")


@router.get("/schemas/insurers", response_model=list[InsurerOut])
def list_insurers(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[InsurerOut]:
    in_use = _names_in_use(user, db)
    return [_out(row, in_use) for row in _visible(user, db)]


@router.post(
    "/schemas/insurers",
    response_model=InsurerOut,
    status_code=status.HTTP_201_CREATED,
)
def create_insurer(
    payload: InsurerIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InsurerOut:
    client_id = require_client_id(user)
    _assert_name_free(payload.name, _visible(user, db))
    row = Insurer(
        client_id=client_id,
        name=payload.name,
        legal_name=payload.legal_name,
        aliases=payload.aliases or None,
        notes=payload.notes,
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        user,
        action="create",
        entity_type="insurer",
        entity_id=row.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(row)
    return _out(row, _names_in_use(user, db))


@router.patch("/schemas/insurers/{insurer_id}", response_model=InsurerOut)
def update_insurer(
    insurer_id: str,
    payload: InsurerPatch,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InsurerOut:
    row = _load_editable(insurer_id, user, db)
    patch = payload.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name is required")
    if "name" in patch:
        _assert_name_free(patch["name"], _visible(user, db), exclude_id=row.id)
    before: dict[str, object] = {}
    for key, value in patch.items():
        before[key] = getattr(row, key)
        # An emptied alias list is stored as NULL, matching how create writes it.
        if key == "aliases" and not value:
            value = None
        setattr(row, key, value)
    db.flush()
    write_audit(
        db,
        user,
        action="update",
        entity_type="insurer",
        entity_id=row.id,
        before=before,
        after=patch,
    )
    db.commit()
    db.refresh(row)
    return _out(row, _names_in_use(user, db))


@router.delete(
    "/schemas/insurers/{insurer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_insurer(
    insurer_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Removes the entry from the dropdown. Products already carrying the name
    keep it (and keep reporting under it) — the catalog holds no references."""
    row = _load_editable(insurer_id, user, db)
    snapshot = {"name": row.name, "legal_name": row.legal_name}
    db.delete(row)
    write_audit(
        db,
        user,
        action="delete",
        entity_type="insurer",
        entity_id=insurer_id,
        before=snapshot,
    )
    db.commit()
