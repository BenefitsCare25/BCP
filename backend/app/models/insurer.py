"""Insurer name catalog — the vocabulary behind every insurer field.

This is a *name registry*, not a foreign-key target. ``Product.insurer`` (and
``PanelListing.insurer`` / ``PanelCard.insurer``) keep storing the canonical
short ``name`` as a string, because that string is already a join key across
several subsystems: the insurer-reports module groups products by
``Product.insurer`` case-insensitively, and the roster's ``"<Insurer> Member
ID"`` columns land in ``attribute_values["insurer_member_ids"]`` keyed by that
same name. Turning it into an FK would silently orphan all of them.

So the catalog's job is to make the *typed* value consistent: it supplies the
dropdown options, records the full legal entity name for reference, and keeps
the aliases a broker might see on a placement slip ("GE", "TMLS", "NTUC
Income") attached to one canonical entry.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class Insurer(Base, TimestampMixin):
    __tablename__ = "insurers"
    # One row per canonical name per tenant. Global library rows (client_id
    # NULL) are exempt — SQL treats NULLs as distinct, so a client may shadow a
    # library name with its own entry. Same shape as Product / PanelListing.
    __table_args__ = (
        UniqueConstraint("client_id", "name", name="uq_insurer_client_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The canonical short name. This is what gets written to Product.insurer,
    # so it must stay stable — renaming it strands existing products and roster
    # member-id keys that still carry the old spelling.
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Full registered entity name (MAS-licensed), for reference on the catalog
    # page. Never used as a lookup key.
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Other spellings seen on placement slips / rosters ("GE", "NTUC Income").
    # Used to catch duplicate entries at create time.
    aliases: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)

    @property
    def alias_list(self) -> list[str]:
        """Aliases as a clean list — the column tolerates legacy junk."""
        raw = self.aliases
        if not isinstance(raw, list):
            return []
        return [str(a).strip() for a in raw if str(a).strip()]
