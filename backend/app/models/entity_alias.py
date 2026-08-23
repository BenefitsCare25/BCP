"""Insured-entity alias map — bridges spellings the normalizer can't.

``matching_engine.normalize_entity`` folds punctuation and corporate suffixes
("Pte. Ltd." == "Pte Ltd"), but an abbreviation or rebrand ("CSO" vs "City
Serviced Offices Pte Ltd") needs an explicit mapping. One row = one alias
spelling → the SET of canonical spellings it stands for, per client. Matching
resolves BOTH sides (a category's ``plan_assignments.insured`` list and the
roster's ``entity`` attribute) through this map after normalization, so either
side may carry the alias spelling.

**An alias may stand for more than one entity.** A single roster spelling
("STMICROELECTRONICS PTE LTD") can cover several registered subsidiaries
("… AMK", "… TPY"), each a separate insured block on the slip. So the targets
live in ``canonicals`` (a JSON list) and the alias resolves to the *union* of
them: an employee carrying the alias matches every category insured on any one
of its entities. ``canonical`` is kept as ``canonicals[0]`` — the display
spelling and the fallback any pre-``canonicals`` row still reads through.

Like the insurer catalog, this is a *name-level* helper, not a foreign key:
category and roster strings stay free text; the alias map only changes how
they compare.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid
from app.db.migration_helpers import json_variant


class EntityAlias(Base, TimestampMixin):
    __tablename__ = "entity_aliases"
    # One mapping per alias spelling per client, compared in NORMALIZED form so
    # "Acme Pte. Ltd." can't claim a second mapping alongside "Acme Pte Ltd".
    # Note the normalizer tokenizes on punctuation rather than deleting it, so
    # "C.S.O." ("c s o") and "CSO" ("cso") are genuinely distinct and each needs
    # its own row — they may point at the same canonical name.
    __table_args__ = (
        UniqueConstraint(
            "client_id", "alias_normalized", name="uq_entity_alias_client_alias"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The spelling as the broker sees it (slip or roster) — display only.
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    # The FIRST canonical spelling — kept as ``canonicals[0]`` so the column
    # stays populated for display and for any reader predating ``canonicals``.
    canonical: Mapped[str] = mapped_column(String(255), nullable=False)
    # Every registered spelling this alias stands for. NULL on rows written
    # before the column existed; readers fall back to ``[canonical]`` then, so
    # no data migration is required in the per-firm schemas.
    canonicals: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    # normalize_entity(alias), persisted so the unique constraint enforces one
    # mapping per normalized alias.
    alias_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
