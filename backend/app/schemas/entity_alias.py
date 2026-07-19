"""Pydantic contracts for the insured-entity alias map."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

NAME_MAX = 255


class _AliasBase(BaseModel):
    """Shared validation. Blank/whitespace-only is a 422, never a silent None —
    a validator returning None for a `str`-typed field hands the router a None
    it will crash on."""

    @field_validator("alias", "canonical", check_fields=False)
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("Entity name cannot be blank.")
        return stripped


class EntityAliasIn(_AliasBase):
    # The spelling seen on the roster or slip that needs bridging.
    alias: str = Field(min_length=1, max_length=NAME_MAX)
    # The spelling it should compare equal to.
    canonical: str = Field(min_length=1, max_length=NAME_MAX)


class EntityAliasPatch(_AliasBase):
    alias: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    canonical: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)


class EntityAliasOut(BaseModel):
    id: str
    alias: str
    canonical: str
    # normalize_entity(alias) — exposed so the UI can show why two spellings
    # that look different already compare equal without an alias.
    alias_normalized: str
