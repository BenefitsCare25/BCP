"""Cached foreign-exchange reference rates (ECB, via Frankfurter).

A CONTROL table (``public``), not a tenant one. A rate is a fact about the
market on a date — it belongs to no broker firm and no client, and every firm
that converts a USD receipt incurred on the same day must land on the SAME
figure. Copying it into each ``firm_<id>`` schema would give two firms two
answers for one question and multiply the outbound calls by the tenant count.
See ``db/tenancy.py::CONTROL_TABLES``.

One row per (base, quote, **requested** date). ``as_of_date`` is the date we
ASKED for — the date on the receipt — and ``rate_date`` is the date the upstream
actually served. They differ whenever the receipt falls on a weekend, a public
holiday, or a day the ECB had not yet published: there is no rate for a Sunday
and there never will be, so the nearest earlier publication is the answer, and
recording both is what lets a broker see which one was used.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid

SOURCE_FRANKFURTER = "frankfurter"


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint(
            "base_currency", "quote_currency", "as_of_date", name="uq_fx_rates_lookup"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # ISO 4217, upper-case. `rate` is quote-per-1-base: base=USD, quote=SGD,
    # rate=1.3512 means one US dollar buys 1.3512 Singapore dollars.
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SOURCE_FRANKFURTER
    )
    # When we pulled it. Drives re-fetch of a row that is not yet FINAL — see
    # `services/fx.py::_is_final`. A row whose `rate_date` equals its
    # `as_of_date` is never re-fetched: published ECB rates do not change.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
