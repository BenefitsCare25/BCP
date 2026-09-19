from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DocumentType = Literal[
    "Contractual Agreement",
    "Death Certificate",
    "Employment Pass",
    "Medical Bill",
    "Medical Certificate",
    "Medical Report",
    "MOM I-Report",
    "Salary Voucher",
    "Work Permit",
]
BenefitType = Literal[
    "Death",
    "Permanent Total Disablement",
    "Permanent Partial Disablement",
    "Temporary Disablement",
    "Medical",
    "Common Law",
    "Others",
]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PeriodIn(Input):
    id: UUID
    label: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    grace_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode="after")
    def dates(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("End date must be on or after start date")
        return self


class SettingsIn(Input):
    revision: int = Field(ge=0)
    enabled: bool
    periods: list[PeriodIn] = Field(max_length=100)


class IncidentIn(Input):
    id: UUID
    period_id: UUID
    employee_id: str | None = Field(default=None, max_length=36)
    employee_name: str = Field(min_length=1, max_length=255)
    staff_id: str = Field(min_length=1, max_length=128)
    incident_date: date
    report_number: str = Field(default="", max_length=128)
    remarks: str = Field(default="", max_length=4000)


class RevisionIn(Input):
    revision: int = Field(ge=1)


class TagIn(RevisionIn):
    doc_type: DocumentType
    document_date: date
    benefit_type: BenefitType
    provider: str = Field(default="", max_length=255)
    invoice_number: str = Field(default="", max_length=128)
    incurred_amount: Money | None = None
    related_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ActionIn(RevisionIn):
    status: Literal["submitted", "pending_insurer", "settled", "rejected"]
    settlement_amount: Money | None = None
    settlement_date: date | None = None
    insurer_reference: str = Field(default="", max_length=128)
    remarks: str = Field(default="", max_length=2000)


class PackIn(RevisionIn):
    id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=100)


class SentIn(RevisionIn):
    sent_on: date
    sent_reference: str = Field(min_length=1, max_length=255)
