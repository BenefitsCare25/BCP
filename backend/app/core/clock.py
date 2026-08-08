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

**One calendar, both directions.** ``business_date()`` reads a stored instant
as the day a Singapore reader would name it; ``stamp_for_day()`` writes a
stated day into an instant that reads back the same. Subtracting a date
produced by one convention from a date produced by the other is the bug this
module exists to prevent, and it is silent — the figure is simply wrong by a
day, for part of the day.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

BUSINESS_TZ = timezone(timedelta(hours=8), "Asia/Singapore")


def now() -> datetime:
    """The current instant, as an aware datetime in business time."""
    return datetime.now(tz=BUSINESS_TZ)


def today() -> date:
    """The current business DATE."""
    return now().date()


def business_date(value: datetime) -> date:
    """The business DATE a stored instant falls on.

    Timestamps are stored UTC (and SQLite serializes them naive, with no
    offset), so ``value.date()`` on one is a UTC calendar date — a day behind
    the day a Singapore reader would name it, for every instant between 16:00
    UTC and midnight. Subtracting such a date from ``today()`` mixes two
    calendars, and the elapsed-day figure is then wrong by a day for a third of
    the clock.

    Use this wherever a stored instant has to become a date someone reads or
    counts from. Keep plain ``.date()`` for arithmetic that stays wholly in UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BUSINESS_TZ).date()


def stamp_for_day(day: date) -> datetime:
    """A STATED calendar date widened into an unambiguous instant.

    Some columns are timestamps that sometimes carry a date a person typed
    (``Claim.sent_to_insurer_at`` — a broker recording "we sent it on the 20th"
    days later). Widening such a date with the current wall-clock time makes the
    stored instant's calendar date depend on what time of day it was keyed: a
    date entered at 23:00 UTC reads as the NEXT day to any reader in Singapore,
    and as the stated day to a reader in UTC.

    Noon UTC is the fix. It is the same calendar date in every zone from UTC-11
    to UTC+11, so the stored value names one day and only one day no matter who
    reads it, in which timezone, on which dialect. The time component is
    meaningless either way — only the date was ever stated.
    """
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)

