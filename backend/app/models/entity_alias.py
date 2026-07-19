"""Insured-entity alias map — bridges spellings the normalizer can't.

``matching_engine.normalize_entity`` folds punctuation and corporate suffixes
("Pte. Ltd." == "Pte Ltd"), but an abbreviation or rebrand ("CSO" vs "City
Serviced Offices Pte Ltd") needs an explicit mapping. One row = one alias
spelling → the canonical spelling, per client. Matching resolves BOTH sides
(a category's ``plan_assignments.insured`` list and the roster's ``entity``
attribute) through this map after normalization, so either side may carry the
alias spelling.

Like the insurer catalog, this is a *name-level* helper, not a foreign key:
category and roster strings stay free text; the alias map only changes how
they compare.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


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
    # The spelling it should compare equal to.
    canonical: Mapped[str] = mapped_column(String(255), nullable=False)
    # normalize_entity(alias), persisted so the unique constraint enforces one
    # mapping per normalized alias.
    alias_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
