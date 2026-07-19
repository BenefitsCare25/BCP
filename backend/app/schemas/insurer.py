"""Pydantic contracts for the insurer name catalog."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _clean_aliases(value: list[str] | None) -> list[str] | None:
    """Trim, drop blanks, and de-duplicate case-insensitively (keeping the
    first spelling given). None stays None so a PATCH can leave it untouched."""
    if value is None:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for raw in value:
        alias = str(raw).strip()
        if not alias or alias.lower() in seen:
            continue
        seen.add(alias.lower())
        out.append(alias)
    return out


class InsurerIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    legal_name: str | None = Field(default=None, max_length=255)
    aliases: list[str] | None = None
    notes: str | None = None

    @field_validator("name", "legal_name", "notes")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str] | None) -> list[str] | None:
        return _clean_aliases(v)


class InsurerPatch(BaseModel):
    """Partial update — only the fields present in the body are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    legal_name: str | None = Field(default=None, max_length=255)
    aliases: list[str] | None = None
    notes: str | None = None

    @field_validator("name", "legal_name", "notes")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str] | None) -> list[str] | None:
        return _clean_aliases(v)


class InsurerOut(BaseModel):
    id: str
    client_id: str | None
    name: str
    legal_name: str | None
    aliases: list[str]
    notes: str | None
    # True when this name is currently stored on at least one product — the UI
    # warns before deleting, since the string is what the reports group by.
    in_use: bool = False
