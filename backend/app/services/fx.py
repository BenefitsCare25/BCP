"""Foreign-exchange rates for claims, from the ECB reference set via Frankfurter.

A member may incur a bill in any of `claim_intake.ALLOWED_CURRENCIES`, but every
limit, wallet and utilization bucket in this product is denominated in the
POLICY currency (SGD). A claim in another currency therefore has to be converted
before it can be compared to anything, and this module is the only place that
conversion is sourced.

Four rules make the figure defensible rather than merely plausible:

- **The rate is the one that applied on the RECEIPT date**, never today's. A
  claim submitted three months late must convert at the rate that was true when
  the treatment was paid for, or the member's reimbursement drifts with the
  market between incurring the cost and getting round to filing.
- **The upstream publishes on business days only, so the rate served may be
  EARLIER than the date asked for.** Frankfurter answers a Sunday (or a holiday,
  or a day not yet published) with the nearest preceding publication. That is
  the right answer and the only one available — free, no-key data has no
  intraday or weekend series — so we take it and record `rate_date` alongside
  `as_of_date` so a broker can see exactly which day was used.
- **Rates are cached in `fx_rates` and a published one is never re-fetched.**
  ECB reference rates are immutable once published, so a row whose `rate_date`
  equals its `as_of_date` is final forever. A row served from an earlier date
  is provisional (the real one may publish later that day) and is re-fetched
  until the date is old enough that nothing more will ever be published for it.
- **Failure is a stated outcome, not an exception.** `quote()` returns None and
  the claim proceeds unconverted, flagged for a broker. A currency API outage
  must never stop a member filing a claim.

The cache is a CONTROL table shared by every firm — see `models/fx_rate.py`.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.core.settings import get_settings
from app.db.base import new_uuid
from app.models.fx_rate import SOURCE_FRANKFURTER, FxRate

logger = logging.getLogger(__name__)

# The currency every limit, wallet, premium and utilization bucket is stated in.
# Kept here rather than imported from `flex_membership` (which reads the same
# INSPRO_DEFAULT_CURRENCY env for the flex wallet) so this module stays a leaf:
# it is imported by the claim, review and utilization layers, and a back-edge
# into the flex services would make that a cycle.
POLICY_CURRENCY = "SGD"

# The ECB series starts here; asking for anything earlier 404s.
_ECB_EPOCH = date(1999, 1, 4)

# A provisional row (served from a date earlier than the one asked for) stops
# being provisional once nothing further can be published for that date. Five
# days clears the longest ordinary run of non-publication — a weekend either
# side of a two-day public holiday — without waiting so long that a genuinely
# stale figure sticks.
_FINAL_AFTER_DAYS = 5
# How long a provisional row is trusted before we ask again.
_PROVISIONAL_TTL = timedelta(hours=6)

# Consecutive upstream failures before we stop trying, and for how long. Without
# this, an outage costs every concurrent submit the full retry budget in a
# threadpool worker — the slow path becomes the common path and the pool starves.
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_SECONDS = 120.0

_breaker_lock = threading.Lock()
_breaker: dict[str, float] = {"failures": 0.0, "open_until": 0.0}


@dataclass(frozen=True)
class FxQuote:
    """A conversion actually performed, with everything needed to justify it."""

    base: str
    quote: str
    rate: float
    # The date asked for (the receipt date) and the date the rate is FROM. Equal
    # on an ordinary business day; `rate_date` is earlier across a weekend, a
    # holiday, or a same-day receipt whose rate has not published yet.
    as_of_date: date
    rate_date: date
    source: str

    @property
    def stale(self) -> bool:
        """True when no rate existed for the receipt date itself."""
        return self.rate_date != self.as_of_date

    def convert(self, amount: float) -> float:
        """`amount` in `base`, expressed in `quote`, rounded to the cent.

        Rounded because it becomes money: it is compared against a limit, summed
        into a utilization bucket and shown to the member as the figure they are
        being asked to accept. Carrying float noise into all three would let the
        three disagree in the last decimal place.
        """
        return round(amount * self.rate, 2)


def reset_breaker() -> None:
    """Clear the outage breaker. For tests and for the broker's manual retry —
    an assessor who clicks "retry conversion" is telling us to try again now."""
    with _breaker_lock:
        _breaker["failures"] = 0.0
        _breaker["open_until"] = 0.0


def _breaker_open() -> bool:
    with _breaker_lock:
        return time.monotonic() < _breaker["open_until"]


def _record_failure() -> None:
    with _breaker_lock:
        _breaker["failures"] += 1
        if _breaker["failures"] >= _BREAKER_THRESHOLD:
            _breaker["open_until"] = time.monotonic() + _BREAKER_COOLDOWN_SECONDS
            logger.warning(
                "FX upstream failed %d times consecutively — pausing lookups for %.0fs",
                int(_breaker["failures"]),
                _BREAKER_COOLDOWN_SECONDS,
            )


def _record_success() -> None:
    with _breaker_lock:
        _breaker["failures"] = 0.0
        _breaker["open_until"] = 0.0


def _aware(value: datetime) -> datetime:
    """A stored timestamp as UTC-aware. SQLite serializes `DateTime(timezone=True)`
    without the offset, so a read-back is naive and subtracting an aware `now()`
    from it raises — on one dialect only, which is the worst place to find out."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_final(row: FxRate, *, now: date) -> bool:
    """Whether a cached row can never be improved on.

    True when the upstream served the exact date asked for (published rates are
    immutable), or when the date is old enough that no further publication for
    it is possible — which is what makes a weekend date final, since `rate_date`
    can never equal a Saturday.
    """
    if row.rate_date == row.as_of_date:
        return True
    return (now - row.as_of_date) >= timedelta(days=_FINAL_AFTER_DAYS)


def _cached(db: Session, base: str, quote: str, on: date) -> FxRate | None:
    return db.execute(
        select(FxRate).where(
            FxRate.base_currency == base,
            FxRate.quote_currency == quote,
            FxRate.as_of_date == on,
        )
    ).scalar_one_or_none()


def _insert_ignoring_conflicts(db: Session, values: dict[str, object]) -> None:
    """INSERT the cache row, doing nothing if someone already inserted it.

    **Not a try/except around a plain insert, and not a SAVEPOINT.** Two members
    submitting foreign claims for the same day race to insert the same
    (base, quote, date), and on Postgres a unique violation aborts the WHOLE
    transaction — which here is the caller's claim submission, so the loser of a
    race over a *cache row* would lose a perfectly good claim with it. A
    savepoint would contain that, but `db/session.py` installs no pysqlite
    workaround, so SAVEPOINT semantics are unreliable on the dev dialect — the
    one place the behaviour would be developed against.

    `ON CONFLICT DO NOTHING` sidesteps both: it raises nothing to contain, and
    both dialects in use support it. The loser of the race simply keeps the
    winner's row, which is the same market fact.
    """
    stmt: Any
    if db.get_bind().dialect.name == "postgresql":
        stmt = pg_insert(FxRate).values(**values).on_conflict_do_nothing(
            index_elements=["base_currency", "quote_currency", "as_of_date"]
        )
    else:
        stmt = sqlite_insert(FxRate).values(**values).on_conflict_do_nothing(
            index_elements=["base_currency", "quote_currency", "as_of_date"]
        )
    db.execute(stmt)


def _store(
    db: Session, *, base: str, quote: str, on: date, rate: float, rate_date: date
) -> None:
    """Write the fetched rate into the cache.

    **Does not commit** — the caller owns the transaction, the same convention
    every service here follows. The read-only quote endpoints commit explicitly
    for exactly this reason; without that the row they just fetched is discarded
    and the cache never warms.
    """
    existing = _cached(db, base, quote, on)
    fetched = datetime.now(UTC)
    if existing is not None:
        existing.rate = rate
        existing.rate_date = rate_date
        existing.source = SOURCE_FRANKFURTER
        existing.fetched_at = fetched
        return
    _insert_ignoring_conflicts(
        db,
        {
            "id": new_uuid(),
            "base_currency": base,
            "quote_currency": quote,
            "as_of_date": on,
            "rate_date": rate_date,
            "rate": rate,
            "source": SOURCE_FRANKFURTER,
            "fetched_at": fetched,
        },
    )


def _parse(payload: object, quote: str) -> tuple[float, date] | None:
    """(rate, rate_date) from a Frankfurter response, or None if unusable.

    Defensive rather than trusting: this is third-party JSON on the path of a
    money figure, and a missing symbol or a zero rate must read as "no rate"
    rather than converting a claim to nothing.
    """
    if not isinstance(payload, dict):
        return None
    rates = payload.get("rates")
    served = payload.get("date")
    if not isinstance(rates, dict) or not isinstance(served, str):
        return None
    raw = rates.get(quote)
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        return None
    rate = float(raw)
    if not (rate > 0) or rate != rate or rate in (float("inf"), float("-inf")):
        return None
    try:
        rate_date = date.fromisoformat(served)
    except ValueError:
        return None
    return rate, rate_date


def _fetch(base: str, quote: str, on: date) -> tuple[float, date] | None:
    """One rate from the upstream, with retries. None on any failure.

    Attempt budget is `1 + fx_max_retries` (three calls by default). The backoff
    is deliberately tiny: this runs inside a member's submit, and a member
    watching a spinner is a worse outcome than a claim a broker converts by
    hand — which is exactly what the None path arranges.
    """
    settings = get_settings()
    if not settings.fx_enabled:
        return None
    if _breaker_open():
        logger.info("FX lookup skipped (breaker open) for %s→%s on %s", base, quote, on)
        return None

    url = f"{settings.fx_api_url.rstrip('/')}/{on.isoformat()}"
    params = {"base": base, "symbols": quote}
    attempts = max(1, settings.fx_max_retries + 1)
    last: Exception | str | None = None
    for attempt in range(attempts):
        if attempt:
            # 0.2s, then 0.5s. Enough to clear a blip, short enough that the
            # worst case stays inside a request a person is waiting on.
            time.sleep(0.2 if attempt == 1 else 0.5)
        try:
            resp = httpx.get(
                url,
                params=params,
                timeout=settings.fx_timeout_seconds,
                # **Load-bearing.** httpx does NOT follow redirects by default,
                # and this vendor moves its host: `api.frankfurter.app` now
                # 301s to `api.frankfurter.dev/v1`. Without this, every real
                # call returns a 301, `raise_for_status` throws, and the whole
                # feature degrades to "no rate ever" — silently, because that
                # is a legitimate outcome here. A stubbed test cannot catch it;
                # only pointing at the live host can.
                follow_redirects=True,
            )
            resp.raise_for_status()
            parsed = _parse(resp.json(), quote)
            if parsed is None:
                # A 200 we cannot read is a bad answer, not a transport fault —
                # retrying will produce the same bytes, so stop.
                last = "unparseable response"
                break
            _record_success()
            return parsed
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
    _record_failure()
    logger.warning(
        "FX rate unavailable for %s→%s on %s after %d attempt(s): %s",
        base, quote, on, attempts, last,
    )
    return None


def quote(db: Session, currency: str, on: date) -> FxQuote | None:
    """The rate to convert `currency` into the policy currency on `on`.

    None means "no rate" — the currency is unsupported, the date is out of
    range, or the upstream could not be reached. Callers must treat that as a
    claim to be converted by a person, never as a reason to refuse the claim.

    Returns None for the policy currency itself: there is nothing to convert,
    and manufacturing a 1.0 quote would put an `fx_rate` on a domestic claim and
    invite a reader to think one was applied.
    """
    base = (currency or "").strip().upper()
    if not base or base == POLICY_CURRENCY:
        return None

    # A receipt cannot be dated after today for FX purposes — the future has no
    # published rate, and asking for one gets the latest rate silently labelled
    # with today's date. A future incurred date is separately flagged to the
    # broker by the review rules; here we simply price it at today.
    today = business_today()
    as_of = min(on, today)
    if as_of < _ECB_EPOCH:
        logger.info("FX lookup refused: %s predates the reference series", as_of)
        return None

    row = _cached(db, base, POLICY_CURRENCY, as_of)
    if row is not None and _is_final(row, now=today):
        return FxQuote(base, POLICY_CURRENCY, row.rate, as_of, row.rate_date, row.source)

    if row is not None and (datetime.now(UTC) - _aware(row.fetched_at)) < _PROVISIONAL_TTL:
        # Provisional but recently checked — reuse rather than re-ask. Also what
        # keeps a breaker-open period cheap: the row answers without a call.
        return FxQuote(base, POLICY_CURRENCY, row.rate, as_of, row.rate_date, row.source)

    fetched = _fetch(base, POLICY_CURRENCY, as_of)
    if fetched is None:
        # Fall back to a provisional row we already hold rather than reporting
        # "unavailable" — a rate from the right window beats no rate at all, and
        # it is already labelled with the date it came from.
        if row is not None:
            return FxQuote(
                base, POLICY_CURRENCY, row.rate, as_of, row.rate_date, row.source
            )
        return None

    rate, rate_date = fetched
    _store(db, base=base, quote=POLICY_CURRENCY, on=as_of, rate=rate, rate_date=rate_date)
    return FxQuote(base, POLICY_CURRENCY, rate, as_of, rate_date, SOURCE_FRANKFURTER)
