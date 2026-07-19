"""Pydantic schemas for panel clinic e-cards (broker config + member render)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.panel_card import (
    CARD_FACES,
    CARD_REMARK_KEYS,
    CARD_SERVICES,
    MEMBER_ID_SOURCES,
)

# Placement keys the renderer knows how to bind. A placement naming anything
# else is rejected at write time, so the portal can never render a field the
# resolver has no value for.
PLACEMENT_FIELD_KEYS: tuple[str, ...] = (
    "member_name",
    "member_id",
    "staff_id",
    "email",
    "nric_masked",
    "company_name",
    "policy_number",
    "product_name",
    "plan_name",
    # The coverage window — the product's own period when it overrides the
    # benefit year, else the year's span. One pair only: separate
    # "benefit_year_*" keys would resolve to these same two values.
    "effective_date",
    "expiry_date",
    "insurer",
    "panel_provider",
    "card_name",
    "special_conditions",
    "dependant_name",
    "relationship",
    *(f"remark_{key}" for key in CARD_REMARK_KEYS),
)

PLACEMENT_ALIGNMENTS: tuple[str, ...] = ("left", "center", "right")

MAX_PLACEMENT_FIELDS = 40


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PlacementField(BaseModel):
    """One positioned text field on the card artwork.

    Geometry is FRACTIONAL (0-1) relative to the artwork box: `x`/`y` are the
    anchor point, `size` is the font size as a fraction of artwork HEIGHT and
    `max_width` a fraction of artwork WIDTH. Storing fractions keeps one record
    valid for the responsive web card and any raster export size.
    """

    key: str
    face: str = "front"
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    size: float = Field(default=0.05, gt=0, le=0.5)
    weight: int = Field(default=500, ge=100, le=900)
    align: str = "left"
    color: str = Field(default="#111111", max_length=32)
    uppercase: bool = False
    # None = no wrapping constraint (single line, grows from the anchor).
    max_width: float | None = Field(default=None, gt=0, le=1)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        key = v.strip()
        if key not in PLACEMENT_FIELD_KEYS:
            raise ValueError(f"unknown placement key: {key!r}")
        return key

    @field_validator("face")
    @classmethod
    def _face(cls, v: str) -> str:
        face = v.strip().lower()
        if face not in CARD_FACES:
            raise ValueError(f"face must be one of {list(CARD_FACES)}")
        return face

    @field_validator("align")
    @classmethod
    def _align(cls, v: str) -> str:
        align = v.strip().lower()
        if align not in PLACEMENT_ALIGNMENTS:
            raise ValueError(f"align must be one of {list(PLACEMENT_ALIGNMENTS)}")
        return align


class CardPlacements(BaseModel):
    fields: list[PlacementField] = Field(
        default_factory=list, max_length=MAX_PLACEMENT_FIELDS
    )


class PanelCardIn(BaseModel):
    insurer: str = Field(min_length=1, max_length=64)
    panel_provider: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("insurer", "panel_provider", "name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class PanelCardUpdate(BaseModel):
    insurer: str | None = Field(default=None, min_length=1, max_length=64)
    panel_provider: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)

    # Same rules as PanelCardIn — the PATCH handler re-builds a PanelCardIn
    # from the merged values, so anything accepted here must construct there.
    @field_validator("insurer", "panel_provider", "name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class PanelCardOut(_Base):
    id: str
    insurer: str
    panel_provider: str
    name: str
    display_label: str
    has_front: bool
    has_back: bool
    aspect_ratio: float | None = None
    placements: CardPlacements = Field(default_factory=CardPlacements)
    assigned_policy_year_ids: list[str] = Field(default_factory=list)
    uploaded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ── Policy-year assignment ───────────────────────────────────────────────────


def _validate_source(value: str) -> str:
    source = value.strip().lower()
    if source not in MEMBER_ID_SOURCES:
        raise ValueError(f"member id source must be one of {list(MEMBER_ID_SOURCES)}")
    return source


class PolicyYearCardIn(BaseModel):
    panel_card_id: str
    product_id: str
    employee_member_id_source: str = "insurer_member_id"
    dependant_member_id_source: str = "insurer_member_id"
    services: dict[str, bool] = Field(default_factory=dict)
    remarks: dict[str, str] = Field(default_factory=dict)
    special_conditions: str | None = Field(default=None, max_length=2000)
    show_future_cards: bool = False

    @field_validator("employee_member_id_source", "dependant_member_id_source")
    @classmethod
    def _source(cls, v: str) -> str:
        return _validate_source(v)

    @field_validator("services")
    @classmethod
    def _services(cls, v: dict[str, bool]) -> dict[str, bool]:
        unknown = set(v) - set(CARD_SERVICES)
        if unknown:
            raise ValueError(f"unknown service keys: {sorted(unknown)}")
        return v

    @field_validator("remarks")
    @classmethod
    def _remarks(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - set(CARD_REMARK_KEYS)
        if unknown:
            raise ValueError(f"unknown remark keys: {sorted(unknown)}")
        trimmed = {k: (val or "").strip()[:1000] for k, val in v.items()}
        return {k: val for k, val in trimmed.items() if val}


class PolicyYearCardOut(_Base):
    id: str
    policy_year_id: str
    panel_card_id: str
    card_name: str
    product_id: str
    product_code: str
    product_name: str
    employee_member_id_source: str
    dependant_member_id_source: str
    services: dict[str, bool] = Field(default_factory=dict)
    remarks: dict[str, str] = Field(default_factory=dict)
    special_conditions: str | None = None
    show_future_cards: bool = False
    created_at: datetime
    updated_at: datetime


# ── Member render payload ────────────────────────────────────────────────────


class CardServiceOut(BaseModel):
    key: str
    label: str


class MemberCardOut(BaseModel):
    """One rendered card: artwork reference + placements + resolved values.

    `values` is keyed by placement key, so the renderer is a pure join —
    it never reaches for member data itself.
    """

    card_id: str
    assignment_id: str
    holder_type: str  # "employee" | "dependant"
    holder_id: str
    holder_name: str | None = None
    product_code: str
    product_name: str
    card_name: str
    aspect_ratio: float | None = None
    has_front: bool
    has_back: bool
    placements: CardPlacements
    values: dict[str, str] = Field(default_factory=dict)
    services: list[CardServiceOut] = Field(default_factory=list)
    remarks: dict[str, str] = Field(default_factory=dict)
    special_conditions: str | None = None


class MemberCardsOut(BaseModel):
    items: list[MemberCardOut] = Field(default_factory=list)


class CardFieldOption(BaseModel):
    key: str
    label: str


class CardOptionsOut(BaseModel):
    """Vocabulary for the placement editor + assignment form — so the frontend
    never hardcodes keys the backend validates."""

    placement_keys: list[CardFieldOption]
    member_id_sources: list[CardFieldOption]
    services: list[CardFieldOption]
    remark_keys: list[CardFieldOption]
