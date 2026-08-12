"""Pydantic contracts for the insured-entity alias map.

An alias may stand for MORE than one registered entity, so the write contract
carries a ``canonicals`` list. Legacy single-value ``canonical`` is still
accepted (the reconciliation panel and old clients send it) and folded into the
list, so nothing that posts ``{alias, canonical}`` breaks.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

NAME_MAX = 255


def _clean(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise ValueError("Entity name cannot be blank.")
    return stripped


class _AliasBase(BaseModel):
    """Shared validation. Blank/whitespace-only is a 422, never a silent None —
    a validator returning None for a `str`-typed field hands the router a None
    it will crash on."""

    @field_validator("alias", check_fields=False)
    @classmethod
    def _strip_alias(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v)

    @field_validator("canonicals", check_fields=False)
    @classmethod
    def _strip_canonicals(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned: list[str] = []
        for name in v:
            name = _clean(name)
            if name not in cleaned:  # de-dup on raw spelling; normalized de-dup is the API's job
                cleaned.append(name)
        return cleaned


class EntityAliasIn(_AliasBase):
    # The spelling seen on the roster or slip that needs bridging.
    alias: str = Field(min_length=1, max_length=NAME_MAX)
    # Every registered spelling the alias stands for. `canonical` (legacy single
    # value) is folded in below, so a client may send either shape.
    canonicals: list[str] = Field(default_factory=list)
    canonical: str | None = Field(default=None, max_length=NAME_MAX)

    @model_validator(mode="after")
    def _fold_legacy(self) -> EntityAliasIn:
        if self.canonical:
            cleaned = _clean(self.canonical)
            if cleaned not in self.canonicals:
                self.canonicals = [cleaned, *self.canonicals]
        if not self.canonicals:
            raise ValueError("An alias must map to at least one entity.")
        return self


class EntityAliasPatch(_AliasBase):
    alias: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    # Full replacement of the entity list when supplied. Legacy single
    # `canonical` still accepted for backward compatibility.
    canonicals: list[str] | None = Field(default=None)
    canonical: str | None = Field(default=None, max_length=NAME_MAX)

    @model_validator(mode="after")
    def _fold_legacy(self) -> EntityAliasPatch:
        if self.canonical and self.canonicals is None:
            self.canonicals = [_clean(self.canonical)]
        if self.canonicals is not None and not self.canonicals:
            raise ValueError("An alias must map to at least one entity.")
        return self


class EntityAliasOut(BaseModel):
    id: str
    alias: str
    # First entity — kept for display and any pre-`canonicals` consumer.
    canonical: str
    # Every entity the alias stands for.
    canonicals: list[str]
    # normalize_entity(alias) — exposed so the UI can show why two spellings
    # that look different already compare equal without an alias.
    alias_normalized: str
