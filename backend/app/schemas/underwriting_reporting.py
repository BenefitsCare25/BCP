"""Optional insurer correspondence and explicit individual annual pricing."""

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UnderwritingReportDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    last_standard_accepted_si: float | None = Field(default=None, ge=0)
    health_loading: str | None = Field(default=None, max_length=200)
    residency_loading: str | None = Field(default=None, max_length=200)
    new_member_letter_date: date | None = None
    new_insurer_letter_date: date | None = None
    renewal_member_letter_date: date | None = None
    renewal_insurer_letter_date: date | None = None
    annual_premium_net: float | None = Field(default=None, ge=0)
    premium_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def currency_for_premium(self) -> Self:
        if self.annual_premium_net is not None and not self.premium_currency:
            raise ValueError("An insurer-confirmed annual premium requires its currency.")
        return self


REPORT_DETAIL_COLUMNS = (
    ("Last Standard Accepted SI", "last_standard_accepted_si"),
    ("Health Loading", "health_loading"),
    ("Residency Loading", "residency_loading"),
    ("New Letter to Member", "new_member_letter_date"),
    ("New Letter to Insurer", "new_insurer_letter_date"),
    ("Renewal Letter to Member", "renewal_member_letter_date"),
    ("Renewal Letter to Insurer", "renewal_insurer_letter_date"),
)
