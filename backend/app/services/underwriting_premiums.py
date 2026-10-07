"""Validate insurer premium confirmations against the figures being reported."""

from math import isfinite

from app.models import UnderwritingCase
from app.models.underwriting_case import normalize_uw_status


def premium_confirmation_is_current(
    case: UnderwritingCase | None, *, eligible: float | None, accepted: float | None,
) -> bool:
    if case is None or eligible is None or accepted is None:
        return False
    details = case.report_details or {}
    net = details.get("annual_premium_net")
    currency = details.get("premium_currency")
    return (
        isinstance(net, (int, float)) and not isinstance(net, bool)
        and isfinite(net) and net >= 0
        and isinstance(currency, str) and len(currency) == 3
        and currency.isascii() and currency.isalpha() and currency.isupper()
        and details.get("premium_eligible_si") == eligible == case.eligible_si
        and details.get("premium_accepted_si") == accepted == case.accepted_si
        and details.get("premium_status") == normalize_uw_status(case.status)
    )
