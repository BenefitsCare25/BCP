"""The business day.

Deadlines in this product are calendar dates a member reads on a screen — the
last day of a claim grace period, the day a leaver's portal access ends. The
servers run in UTC, so ``date.today()`` and ``datetime.now(UTC).date()`` both
roll over at 8am Singapore time: a member checking on the final morning of
their window would be told it closed the previous night.

Fixed offset rather than ``ZoneInfo("Asia/Singapore")``: Singapore has observed
no DST since 1935 and its offset is permanently +08:00, so there is nothing for
a timezone database to tell us — and ``zoneinfo`` needs the ``tzdata`` package
on Windows, which every developer here runs.

Use ``today()`` for anything a member is shown or bound by. Timestamps stay
UTC-aware (``datetime.now(UTC)``) — this is about which DATE a moment falls on,
not about how instants are stored.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

BUSINESS_TZ = timezone(timedelta(hours=8), "Asia/Singapore")


def now() -> datetime:
    """The current instant, as an aware datetime in business time."""
    return datetime.now(tz=BUSINESS_TZ)


def today() -> date:
    """The current business DATE."""
    return now().date()
