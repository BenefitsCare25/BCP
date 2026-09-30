"""Portfolio roster totals and benefit-year operational summaries."""
from datetime import date, datetime

from pydantic import BaseModel, Field


class CompanyYear(BaseModel):
    id: str
    year: int
    status: str
    start_date: date
    end_date: date


class CompanySummary(BaseModel):
    id: str
    name: str
    current_year: CompanyYear | None
    next_year: CompanyYear | None = None
    member_count: int
    dependant_count: int
    claims_to_review: int
    verification_pending: int
    insured_claims_to_review: int
    wallet_claims_to_review: int
    claims_with_insurer: int
    claims_overdue: int
    messages_awaiting_reply: int
    dependants_pending: int
    employees_unmatched: int
    matching_stale: bool
    underwriting_pending: int
    enrollment_open: bool
    enrollment_open_count: int = 0
    enrollment_closes_at: datetime | None
    enrollment_scheduled: int = 0
    enrollment_opens_at: datetime | None = None
    enrollment_overdue: int = 0


class FirmTotals(BaseModel):
    company_count: int
    member_count: int
    dependant_count: int
    claims_to_review: int
    verification_pending: int
    insured_claims_to_review: int
    wallet_claims_to_review: int
    claims_with_insurer: int
    claims_overdue: int
    messages_awaiting_reply: int
    dependants_pending: int
    employees_unmatched: int
    underwriting_pending: int
    windows_open: int


class DashboardSummary(BaseModel):
    firm: FirmTotals
    companies: list[CompanySummary]
    insurers: list[str] = Field(default_factory=list)
    work_by_year: list[CompanySummary] = Field(default_factory=list)
    business_date: date
