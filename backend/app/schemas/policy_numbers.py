"""Explicit product/year policy identifiers, optionally scoped to a legal entity."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PolicyNumberAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None is an explicit instruction to use this number for all covered entities.
    entity: str | None = Field(default=None, max_length=500)
    policy_number: str = Field(min_length=1, max_length=64)

    @field_validator("entity", "policy_number", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def assignment_models(value: list[dict[str, Any]] | None) -> list[PolicyNumberAssignment] | None:
    if value is None:
        return None
    return [PolicyNumberAssignment.model_validate(item) for item in value]
