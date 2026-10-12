"""Request/response models for the guided product-setup draft endpoints.

Served by `api/v1/product_setups`; `SetupSaveIn` and `FieldSuggestions` are also
used by the draft helpers in `services/product_setup_drafts`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SetupOut(BaseModel):
    id: str
    policy_year_id: str
    product_code: str
    template_version: int
    answers: dict[str, Any]
    status: str
    origin: str = "manual"
    confirmed_at: datetime | None = None
    materialized_product_id: str | None = None
    updated_at: datetime


class SetupSaveIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    template_version: int = 1
    # Proceed even though renaming/removing a benefit line would strand existing
    # claims that reference it by name (409 `orphaned_benefit_keys` otherwise).
    acknowledge: bool = False
    expected_updated_at: datetime | None = None


class ConfirmResult(BaseModel):
    product_id: str
    product_code: str
    plans_created: int
    plans_updated: int
    plans_removed: int
    categories_created: int
    categories_removed: int
    rematched: bool = False
    employees_matched: int | None = None


class FieldSuggestions(BaseModel):
    """Values used before for this client+product, to back the form's free-text
    suggestion pickers. Keyed by field id within each section."""

    header: dict[str, list[str]] = Field(default_factory=dict)
    eligibility: dict[str, list[str]] = Field(default_factory=dict)
    participation: list[str] = Field(default_factory=list)
    cover_description: list[str] = Field(default_factory=list)
