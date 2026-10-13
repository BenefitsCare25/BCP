"""File-level findings on an uploaded Employee Listing.

Row checks name rows; these concern the file as a whole — the period it says
it is for, the date its ages are worked out at, and limits the review could
not check because the setup has none.
"""
from __future__ import annotations

from datetime import date

from app.models import PolicyYear
from app.schemas.employee_listing import ListingNoticeOut
from app.services.coverage_limits import ProductLimits
from app.services.el_import.mapping import LabelSuggestion


def _day(value: date) -> str:
    return f"{value.day} {value:%b %Y}"


def _span(start: date, end: date) -> str:
    return f"{_day(start)} to {_day(end)}"


def listing_notices(
    year: PolicyYear | None,
    stated_period: tuple[date, date] | None,
    reference_date: date | None,
    decisions: list[LabelSuggestion],
    limits: dict[str, ProductLimits],
) -> list[ListingNoticeOut]:
    out: list[ListingNoticeOut] = []
    if year is not None and stated_period is not None and stated_period != (
        year.start_date, year.end_date
    ):
        out.append(ListingNoticeOut(
            code="period_mismatch", tone="warn",
            message=(
                f"The file states a period of insurance of {_span(*stated_period)}, but "
                f"this policy year is {_span(year.start_date, year.end_date)}. Check it "
                "is the right year's listing."
            ),
        ))
    if year is not None and reference_date is not None and reference_date != year.start_date:
        out.append(ListingNoticeOut(
            code="age_reference_mismatch", tone="warn",
            message=(
                f"Ages in the file are worked out as at {_day(reference_date)}; the "
                f"policy year starts {_day(year.start_date)}."
            ),
        ))
    with_dependants = sorted({
        d.choice.product_code for d in decisions
        if d.choice is not None and (d.stats.dependants or d.stats.tier_dependants)
    })
    unchecked = [
        code for code in with_dependants
        if (lim := limits.get(code)) is not None
        and not lim.spouse_age_limit and not lim.child_age_limit
    ]
    if unchecked:
        out.append(ListingNoticeOut(
            code="dependant_age_limits_missing", tone="info",
            message=(
                f"No spouse or child age limit is set for {', '.join(unchecked)}, so "
                "dependants' ages were not checked. Set them under Company & Benefits → "
                "product Eligibility."
            ),
        ))
    return out
