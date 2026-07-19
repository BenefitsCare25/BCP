"""Pydantic contracts for the insurer name catalog."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# The canonical name is capped at 64, not the column's 128, because it is
# copied verbatim into `panel_listings.insurer` and `panel_cards.insurer` —
# both `String(64)`. Allowing a longer catalog entry would let the dropdown
# offer a name those forms structurally cannot save.
NAME_MAX = 64


class _InsurerBase(BaseModel):
    """Shared fields + validators for create and patch.

    `name` is declared by each subclass (required vs optional), so its
    validator uses ``check_fields=False``; keeping the body here means a
    validation fix can't land on one verb and miss the other.
    """

    legal_name: str | None = Field(default=None, max_length=255)
    aliases: list[str] | None = None
    notes: str | None = None

    @field_validator("name", check_fields=False)
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        """Blank/whitespace-only is a 422, never a silent None.

        `min_length=1` alone does not cover this — "   " is three characters —
        and a validator that returned None here would hand the router a None
        for a `str`-typed field (Pydantic does not re-check the annotation
        after an 'after' validator), crashing it on `.strip()`.
        """
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("Name cannot be blank.")
        return stripped

    @field_validator("legal_name", "notes")
    @classmethod
    def _strip_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, v: list[str] | None) -> list[str] | None:
        """Trim, drop blanks, and de-duplicate case-insensitively (keeping the
        first spelling given). None stays None so a PATCH can leave it
        untouched."""
        if v is None:
            return None
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            alias = str(raw).strip()
            if not alias or alias.lower() in seen:
                continue
            seen.add(alias.lower())
            out.append(alias)
        return out


class InsurerIn(_InsurerBase):
    name: str = Field(min_length=1, max_length=NAME_MAX)


class InsurerPatch(_InsurerBase):
    """Partial update — only the fields present in the body are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)


class InsurerOut(BaseModel):
    id: str
    client_id: str | None
    name: str
    legal_name: str | None
    aliases: list[str]
    notes: str | None
    # True when this name is currently stored on a product, panel listing, or
    # panel card — the UI warns before deleting, since the string is what the
    # reports and card renderer group by.
    in_use: bool = False
