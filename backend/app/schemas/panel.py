"""Pydantic schemas for panel clinic listings + the clinic locator."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.panel_clinic import CLINIC_TYPES, PANEL_COUNTRIES


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _validate_country(value: str) -> str:
    country = value.strip().upper()
    if country not in PANEL_COUNTRIES:
        raise ValueError(f"country must be one of {sorted(PANEL_COUNTRIES)}")
    return country


def _validate_clinic_type(value: str) -> str:
    clinic_type = value.strip().lower()
    if clinic_type not in CLINIC_TYPES:
        raise ValueError(f"clinic_type must be one of {sorted(CLINIC_TYPES)}")
    return clinic_type


class PanelListingIn(BaseModel):
    insurer: str = Field(min_length=1, max_length=64)
    panel_provider: str = Field(min_length=1, max_length=64)
    country: str
    clinic_type: str
    label: str | None = Field(default=None, max_length=255)

    @field_validator("insurer", "panel_provider")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        return _validate_country(v)

    @field_validator("clinic_type")
    @classmethod
    def _clinic_type(cls, v: str) -> str:
        return _validate_clinic_type(v)


class PanelListingUpdate(BaseModel):
    insurer: str | None = Field(default=None, min_length=1, max_length=64)
    panel_provider: str | None = Field(default=None, min_length=1, max_length=64)
    country: str | None = None
    clinic_type: str | None = None
    label: str | None = Field(default=None, max_length=255)

    # Same strip/blank rules as PanelListingIn — the PATCH handler re-builds a
    # PanelListingIn from the merged values, so anything accepted here must be
    # constructible there (else the handler 500s instead of 422ing).
    @field_validator("insurer", "panel_provider")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("country")
    @classmethod
    def _country(cls, v: str | None) -> str | None:
        return None if v is None else _validate_country(v)

    @field_validator("clinic_type")
    @classmethod
    def _clinic_type(cls, v: str | None) -> str | None:
        return None if v is None else _validate_clinic_type(v)


class PanelListingOut(_Base):
    id: str
    insurer: str
    panel_provider: str
    country: str
    clinic_type: str
    label: str | None = None
    display_label: str
    type_label: str
    clinic_count: int
    source_filename: str | None = None
    uploaded_at: datetime | None = None
    tagged_policy_year_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PanelUploadResult(BaseModel):
    listing: PanelListingOut
    rows_total: int
    imported: int
    skipped_no_name: int
    missing_coordinates: int


class PolicyYearPanelsIn(BaseModel):
    panel_listing_ids: list[str] = Field(max_length=200)


class PolicyYearPanelsOut(BaseModel):
    policy_year_id: str
    panel_listing_ids: list[str]


class ListingCompanyOut(BaseModel):
    """One company's enablement state for a listing — the checkbox row in the
    'Enable for companies' dialog."""

    client_id: str
    client_name: str
    # The policy year the enable action targets: active first, else the latest
    # non-archived year. None = company has no usable year (checkbox disabled).
    policy_year_id: str | None = None
    policy_year_label: str | None = None
    enabled: bool = False


class ListingCompaniesIn(BaseModel):
    """Full desired set — companies NOT listed are disabled (on their target
    year only; historical years keep their tags)."""

    client_ids: list[str] = Field(max_length=500)


# ── Clinic locator (portal + broker preview) ─────────────────────────────────


class ClinicOut(BaseModel):
    id: str
    name: str
    code: str | None = None
    zone: str | None = None
    area: str | None = None
    specialty: str | None = None
    doctor: str | None = None
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    hours: dict[str, Any] | None = None
    latitude: float | None = None
    longitude: float | None = None
    google_map_url: str | None = None
    clinic_type: str
    country: str
    type_label: str
    panel_label: str
    distance_km: float | None = None


class ClinicTypeFacet(BaseModel):
    clinic_type: str
    country: str
    label: str
    count: int


class ClinicFilters(BaseModel):
    clinic_types: list[ClinicTypeFacet]
    areas: list[str]


class SetupHistoryListing(BaseModel):
    """One clinic network enabled for a benefit year."""

    id: str
    display_label: str
    type_label: str
    country: str
    clinic_count: int


class SetupHistoryCard(BaseModel):
    """One e-card issued for a benefit year."""

    id: str
    card_name: str
    product_code: str
    product_name: str
    employee_member_id_source: str
    dependant_member_id_source: str
    service_labels: list[str] = Field(default_factory=list)
    remark_keys: list[str] = Field(default_factory=list)
    special_conditions: str | None = None


class SetupHistoryYear(BaseModel):
    """A benefit year's panel setup — what members saw (or will see) that year."""

    policy_year_id: str
    year: int
    status: str
    start_date: str
    end_date: str
    # The year the member portal currently reads (status == active).
    is_current: bool
    listings: list[SetupHistoryListing] = Field(default_factory=list)
    cards: list[SetupHistoryCard] = Field(default_factory=list)


class PanelSetupHistoryOut(BaseModel):
    years: list[SetupHistoryYear] = Field(default_factory=list)


class ClinicSearchOut(BaseModel):
    total: int
    offset: int
    limit: int
    # True when the caller supplied an origin (lat/lng) — items are then
    # nearest-first with distance_km populated.
    located: bool
    items: list[ClinicOut]
    filters: ClinicFilters
