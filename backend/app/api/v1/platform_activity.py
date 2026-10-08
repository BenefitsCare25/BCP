"""Platform console: per-broker activity for the master admin.

Aggregates only — counts and timestamps, never a name, email, identifier or
claim detail — so the master admin reads them for every firm without an access
grant (operational metadata, like the firm list itself) and reading them writes
no audit row. Same gate as the rest of the console: `system_admin` on a
platform host.

Where each counter comes from:
- companies / portal enabled: `clients`.
- staff active / invited: firm `users` with a broker staff role. The firm-less
  master admin belongs to no firm and is never counted.
- HR users: active firm `users` with an HR role.
- members active: active `member_accounts` of the firm's companies.
- sign-ins: `auth_events` `login_success` rows, by surface (broker → staff,
  hr, portal → member). A staff sign-in that owes its authenticator is recorded
  once, when the second factor completes. Portal events carry only the
  company, so their firm comes from it.
- active sessions: the live row (unrotated, unrevoked, unexpired) of each
  `auth_sessions` family, seen within the broker idle timeout.
- claims submitted / open, enrolments submitted, AI tokens: the firm's own
  schema — `claims.submitted_at` (claims and LOG cases; open = live and not
  yet settled), `enrollments.submitted_at` and non-cache `ai_spend_log`
  tokens. A firm whose schema is missing or unreadable reports these as null.
- last activity: the latest sign-in, claim or enrolment submission, any time.

Periods are whole UTC days ending today, so a firm's daily series sums to its
period counters. The platform view is cached in-process per period.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.api.v1.platform import _utc
from app.core.auth import CurrentUser
from app.core.auth_events import EVENT_LOGIN_SUCCESS, OUTCOME_SUCCESS
from app.core.broker_auth import IDLE_MINUTES
from app.core.deps import require_system_admin
from app.core.hr_auth import HR_ROLES
from app.core.identity import BROKER_ROLES
from app.core.tenant_resolution import require_platform_host
from app.db.session import get_db
from app.db.tenancy import is_postgres, schema_exists, schema_for_firm
from app.models import AuthEvent, AuthSession, BrokerFirm, Claim, Client, MemberAccount, User
from app.models.ai_spend import AISpendLog
from app.models.claim import PENDING_STATUSES
from app.models.enrollment import Enrollment
from app.models.member_account import MEMBER_STATUS_ACTIVE
from app.models.user import USER_STATUS_ACTIVE, USER_STATUS_INVITED

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platform",
    tags=["platform"],
    dependencies=[Depends(require_platform_host), Depends(require_system_admin)],
)

PERIODS = frozenset({7, 30, 90})
CACHE_SECONDS = 120.0
_SURFACE_KEYS = {"broker": "staff", "hr": "hr", "portal": "member"}


# ── Shapes ────────────────────────────────────────────────────────────────────
class SignInCounts(BaseModel):
    staff: int = 0
    hr: int = 0
    member: int = 0


class ActivityCounters(BaseModel):
    companies: int = 0
    companies_portal_enabled: int = 0
    staff_active: int = 0
    staff_invited: int = 0
    hr_users: int = 0
    members_active: int = 0
    sign_ins: SignInCounts = Field(default_factory=SignInCounts)
    active_sessions: int = 0
    # Null: the firm's schema could not be read.
    claims_submitted: int | None = 0
    claims_open: int | None = 0
    enrolments_submitted: int | None = 0
    ai_tokens: int | None = 0


class FirmActivityOut(ActivityCounters):
    firm_id: str
    name: str
    slug: str | None
    status: str
    is_platform_owner: bool
    last_activity_at: dt.datetime | None


class ActivityTotals(ActivityCounters):
    """Every firm summed; firm-schema counters over the firms that could be read."""

    firms: int = 0
    # Firms with a sign-in, claim or enrolment submission in the period.
    firms_with_activity: int = 0
    firms_unavailable: int = 0


class DailyActivity(BaseModel):
    date: dt.date
    sign_ins: int
    claims_submitted: int | None
    enrolments_submitted: int | None


class PlatformActivityOut(BaseModel):
    generated_at: dt.datetime
    days: int
    totals: ActivityTotals
    firms: list[FirmActivityOut]
    daily: list[DailyActivity]


class FirmDailyActivityOut(BaseModel):
    firm_id: str
    days: int
    daily: list[DailyActivity]


# ── Period ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Period:
    days: int
    first_day: dt.date
    since: dt.datetime

    @classmethod
    def ending_today(cls, days: int) -> _Period:
        if days not in PERIODS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"days must be one of {', '.join(str(d) for d in sorted(PERIODS))}.",
            )
        first = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days - 1)
        return cls(days, first, dt.datetime.combine(first, dt.time.min, tzinfo=dt.UTC))

    def dates(self) -> list[dt.date]:
        return [self.first_day + dt.timedelta(days=n) for n in range(self.days)]


def _day(db: Session, column: Any) -> ColumnElement[Any]:
    """The UTC calendar day of a timestamp column."""
    if is_postgres(db):
        return func.date(func.timezone("UTC", column))
    return func.date(column)


def _day_counts(db: Session, stmt: Select[Any], **options: Any) -> dict[str, int]:
    """{'YYYY-MM-DD': count} from a (day, count) query."""
    rows = db.execute(stmt, execution_options=options).all()
    return {str(day)[:10]: int(count) for day, count in rows if day is not None}


# ── Control-plane counters (one grouped query each) ───────────────────────────
def _grouped(db: Session, stmt: Select[Any]) -> dict[str, int]:
    return {str(key): int(count) for key, count in db.execute(stmt).all() if key is not None}


def _event_firm() -> ColumnElement[Any]:
    # Portal events record the company only; staff and HR events the firm.
    return func.coalesce(AuthEvent.broker_firm_id, Client.broker_firm_id)


def _sign_in_events() -> Select[Any]:
    return (
        select()
        .select_from(AuthEvent)
        .outerjoin(Client, Client.id == AuthEvent.client_id)
        .where(
            AuthEvent.event_type == EVENT_LOGIN_SUCCESS,
            AuthEvent.outcome == OUTCOME_SUCCESS,
        )
    )


def _sign_ins(db: Session, since: dt.datetime) -> dict[str, SignInCounts]:
    firm = _event_firm()
    rows = db.execute(
        _sign_in_events()
        .add_columns(firm, AuthEvent.surface, func.count())
        .where(AuthEvent.occurred_at >= since)
        .group_by(firm, AuthEvent.surface)
    ).all()
    out: dict[str, SignInCounts] = {}
    for firm_id, surface, count in rows:
        key = _SURFACE_KEYS.get(surface)
        if firm_id is None or key is None:
            continue
        counts = out.setdefault(str(firm_id), SignInCounts())
        setattr(counts, key, getattr(counts, key) + int(count))
    return out


def _last_sign_in(db: Session) -> dict[str, dt.datetime]:
    firm = _event_firm()
    rows = db.execute(
        _sign_in_events().add_columns(firm, func.max(AuthEvent.occurred_at)).group_by(firm)
    ).all()
    return {str(firm_id): _utc(at) for firm_id, at in rows if firm_id is not None and at}


def _active_sessions(db: Session) -> dict[str, int]:
    now = dt.datetime.now(dt.UTC)
    firm = func.coalesce(AuthSession.broker_firm_id, Client.broker_firm_id)
    return _grouped(db, (
        select(firm, func.count())
        .select_from(AuthSession)
        .outerjoin(Client, Client.id == AuthSession.client_id)
        .where(
            AuthSession.revoked_at.is_(None),
            AuthSession.rotated_at.is_(None),
            AuthSession.expires_at > now,
            func.coalesce(AuthSession.last_seen_at, AuthSession.issued_at)
            >= now - dt.timedelta(minutes=IDLE_MINUTES),
        )
        .group_by(firm)
    ))


def _people(db: Session) -> dict[str, dict[str, int]]:
    """Per firm: staff active / invited and active HR users."""
    rows = db.execute(
        select(User.broker_firm_id, User.role, User.status, func.count())
        .where(User.broker_firm_id.is_not(None))
        .group_by(User.broker_firm_id, User.role, User.status)
    ).all()
    out: dict[str, dict[str, int]] = {}
    for firm_id, role, user_status, count in rows:
        counts = out.setdefault(str(firm_id), {})
        if role in BROKER_ROLES and user_status == USER_STATUS_ACTIVE:
            key = "staff_active"
        elif role in BROKER_ROLES and user_status == USER_STATUS_INVITED:
            key = "staff_invited"
        elif role in HR_ROLES and user_status == USER_STATUS_ACTIVE:
            key = "hr_users"
        else:
            continue
        counts[key] = counts.get(key, 0) + int(count)
    return out


@dataclass
class _Control:
    companies: dict[str, int]
    portal_enabled: dict[str, int]
    client_ids: dict[str, list[str]]
    people: dict[str, dict[str, int]]
    members: dict[str, int]
    sign_ins: dict[str, SignInCounts]
    sessions: dict[str, int]
    last_sign_in: dict[str, dt.datetime]


def _control(db: Session, period: _Period) -> _Control:
    client_ids: dict[str, list[str]] = {}
    for client_id, firm_id in db.execute(select(Client.id, Client.broker_firm_id)).all():
        client_ids.setdefault(str(firm_id), []).append(str(client_id))
    return _Control(
        companies={firm_id: len(ids) for firm_id, ids in client_ids.items()},
        portal_enabled=_grouped(db, (
            select(Client.broker_firm_id, func.count())
            .where(Client.portal_enabled.is_(True))
            .group_by(Client.broker_firm_id)
        )),
        client_ids=client_ids,
        people=_people(db),
        members=_grouped(db, (
            select(Client.broker_firm_id, func.count(MemberAccount.id))
            .join(Client, Client.id == MemberAccount.client_id)
            .where(MemberAccount.status == MEMBER_STATUS_ACTIVE)
            .group_by(Client.broker_firm_id)
        )),
        sign_ins=_sign_ins(db, period.since),
        sessions=_active_sessions(db),
        last_sign_in=_last_sign_in(db),
    )


# ── Firm-schema counters ──────────────────────────────────────────────────────
@dataclass
class _Tenant:
    claims_submitted: int = 0
    claims_open: int = 0
    enrolments_submitted: int = 0
    ai_tokens: int = 0
    last_submission_at: dt.datetime | None = None
    daily_claims: dict[str, int] = field(default_factory=dict)
    daily_enrolments: dict[str, int] = field(default_factory=dict)


def _latest(*values: dt.datetime | None) -> dt.datetime | None:
    present = [_utc(v) for v in values if v is not None]
    return max(present) if present else None


def _read_tenant(
    db: Session, client_ids: list[str], period: _Period, options: Mapping[str, Any]
) -> _Tenant:
    """The firm-schema counters for one firm's companies. COUNT/MAX/SUM only."""
    opts = dict(options)
    since = period.since
    claims = db.execute(select(
        func.count(case((Claim.submitted_at >= since, Claim.id))),
        func.count(case((Claim.status.in_(PENDING_STATUSES), Claim.id))),
        func.max(Claim.submitted_at),
    ).where(Claim.client_id.in_(client_ids)), execution_options=opts).one()
    enrolments = db.execute(select(
        func.count(case((Enrollment.submitted_at >= since, Enrollment.id))),
        func.max(Enrollment.submitted_at),
    ).where(Enrollment.client_id.in_(client_ids)), execution_options=opts).one()
    tokens = db.execute(select(
        func.coalesce(func.sum(AISpendLog.input_tokens + AISpendLog.output_tokens), 0)
    ).where(
        AISpendLog.client_id.in_(client_ids),
        AISpendLog.cache_hit.is_(False),
        AISpendLog.created_at >= since,
    ), execution_options=opts).scalar_one()
    claim_day = _day(db, Claim.submitted_at)
    enrolment_day = _day(db, Enrollment.submitted_at)
    return _Tenant(
        claims_submitted=int(claims[0]),
        claims_open=int(claims[1]),
        enrolments_submitted=int(enrolments[0]),
        ai_tokens=int(tokens or 0),
        last_submission_at=_latest(claims[2], enrolments[1]),
        daily_claims=_day_counts(db, select(claim_day, func.count()).where(
            Claim.client_id.in_(client_ids), Claim.submitted_at >= since,
        ).group_by(claim_day), **opts),
        daily_enrolments=_day_counts(db, select(enrolment_day, func.count()).where(
            Enrollment.client_id.in_(client_ids), Enrollment.submitted_at >= since,
        ).group_by(enrolment_day), **opts),
    )


def _tenant(
    db: Session, firm_id: str, client_ids: list[str], period: _Period
) -> _Tenant | None:
    """One firm's schema counters, or None when its schema cannot be read.

    On Postgres every statement names the firm's schema explicitly
    (`schema_translate_map`), inside a SAVEPOINT so a failure is contained to
    this firm. SQLite has one schema: the company filter alone scopes it.
    """
    options: dict[str, Any] = {}
    if is_postgres(db):
        schema = schema_for_firm(firm_id)
        if not schema_exists(db.connection(), schema):
            logger.warning("Activity: firm %s has no schema %s", firm_id, schema)
            return None
        options["schema_translate_map"] = {None: schema}
    if not client_ids:
        return _Tenant()
    if not options:
        return _read_tenant(db, client_ids, period, options)
    try:
        with db.begin_nested():
            return _read_tenant(db, client_ids, period, options)
    except SQLAlchemyError:
        logger.exception("Activity: could not read firm %s's schema", firm_id)
        return None


# ── Assembly ──────────────────────────────────────────────────────────────────
def _firm_activity(firm: BrokerFirm, control: _Control, tenant: _Tenant | None) -> FirmActivityOut:
    people = control.people.get(firm.id, {})
    return FirmActivityOut(
        firm_id=firm.id,
        name=firm.name,
        slug=firm.slug,
        status=firm.status,
        is_platform_owner=firm.is_platform_owner,
        companies=control.companies.get(firm.id, 0),
        companies_portal_enabled=control.portal_enabled.get(firm.id, 0),
        staff_active=people.get("staff_active", 0),
        staff_invited=people.get("staff_invited", 0),
        hr_users=people.get("hr_users", 0),
        members_active=control.members.get(firm.id, 0),
        sign_ins=control.sign_ins.get(firm.id, SignInCounts()),
        active_sessions=control.sessions.get(firm.id, 0),
        claims_submitted=tenant.claims_submitted if tenant else None,
        claims_open=tenant.claims_open if tenant else None,
        enrolments_submitted=tenant.enrolments_submitted if tenant else None,
        ai_tokens=tenant.ai_tokens if tenant else None,
        last_activity_at=_latest(
            control.last_sign_in.get(firm.id), tenant.last_submission_at if tenant else None
        ),
    )


_SUMMED = (
    "companies", "companies_portal_enabled", "staff_active", "staff_invited", "hr_users",
    "members_active", "active_sessions", "claims_submitted", "claims_open",
    "enrolments_submitted", "ai_tokens",
)


def _totals(firms: Iterable[FirmActivityOut]) -> ActivityTotals:
    totals = ActivityTotals()
    for firm in firms:
        totals.firms += 1
        for name in _SUMMED:
            value = getattr(firm, name)
            if value is not None:
                setattr(totals, name, getattr(totals, name) + value)
        for surface in ("staff", "hr", "member"):
            setattr(totals.sign_ins, surface,
                    getattr(totals.sign_ins, surface) + getattr(firm.sign_ins, surface))
        if firm.claims_submitted is None:
            totals.firms_unavailable += 1
        signed_in = firm.sign_ins.staff + firm.sign_ins.hr + firm.sign_ins.member
        if signed_in or firm.claims_submitted or firm.enrolments_submitted:
            totals.firms_with_activity += 1
    return totals


def _daily_sign_ins(db: Session, period: _Period, firm_id: str | None) -> dict[str, int]:
    firm = _event_firm()
    day = _day(db, AuthEvent.occurred_at)
    scope = firm == firm_id if firm_id is not None else firm.is_not(None)
    return _day_counts(db, _sign_in_events().add_columns(day, func.count()).where(
        AuthEvent.occurred_at >= period.since, scope,
    ).group_by(day))


def _series(
    period: _Period,
    sign_ins: Mapping[str, int],
    tenants: list[_Tenant],
    *,
    unavailable: bool = False,
) -> list[DailyActivity]:
    """One zero-filled entry per day; tenant values null when `unavailable`."""
    out = []
    for day in period.dates():
        key = day.isoformat()
        out.append(DailyActivity(
            date=day,
            sign_ins=sign_ins.get(key, 0),
            claims_submitted=None if unavailable else sum(
                t.daily_claims.get(key, 0) for t in tenants
            ),
            enrolments_submitted=None if unavailable else sum(
                t.daily_enrolments.get(key, 0) for t in tenants
            ),
        ))
    return out


def _platform_activity(db: Session, period: _Period) -> PlatformActivityOut:
    control = _control(db, period)
    firms = db.execute(
        select(BrokerFirm).order_by(BrokerFirm.is_platform_owner.desc(), BrokerFirm.name)
    ).scalars().all()
    rows: list[FirmActivityOut] = []
    readable: list[_Tenant] = []
    for firm in firms:
        tenant = _tenant(db, firm.id, control.client_ids.get(firm.id, []), period)
        if tenant is not None:
            readable.append(tenant)
        rows.append(_firm_activity(firm, control, tenant))
    return PlatformActivityOut(
        generated_at=dt.datetime.now(dt.UTC),
        days=period.days,
        totals=_totals(rows),
        firms=rows,
        daily=_series(period, _daily_sign_ins(db, period, None), readable),
    )


# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict[int, tuple[float, PlatformActivityOut]] = {}
_cache_lock = threading.Lock()


def clear_activity_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(days: int) -> PlatformActivityOut | None:
    with _cache_lock:
        hit = _cache.get(days)
    if hit is None or time.monotonic() - hit[0] > CACHE_SECONDS:
        return None
    return hit[1]


def _store(result: PlatformActivityOut) -> None:
    with _cache_lock:
        _cache[result.days] = (time.monotonic(), result)


# ── Routes ────────────────────────────────────────────────────────────────────
@router.get("/activity", response_model=PlatformActivityOut)
def platform_activity(
    days: int = Query(30),
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PlatformActivityOut:
    """Every firm's activity counters for the last `days` (7, 30 or 90) UTC
    days, platform totals and a daily platform trend. Cached for two minutes."""
    period = _Period.ending_today(days)
    result = _cached(period.days)
    if result is None:
        result = _platform_activity(db, period)
        _store(result)
    return result


@router.get("/firms/{firm_id}/activity", response_model=FirmDailyActivityOut)
def firm_activity(
    firm_id: str,
    days: int = Query(30),
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> FirmDailyActivityOut:
    """One firm's daily sign-ins, claim and enrolment submissions, zero-filled."""
    period = _Period.ending_today(days)
    firm = db.get(BrokerFirm, firm_id)
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    client_ids = list(db.execute(
        select(Client.id).where(Client.broker_firm_id == firm.id)
    ).scalars().all())
    tenant = _tenant(db, firm.id, client_ids, period)
    return FirmDailyActivityOut(
        firm_id=firm.id,
        days=period.days,
        daily=_series(
            period, _daily_sign_ins(db, period, firm.id),
            [tenant] if tenant is not None else [], unavailable=tenant is None,
        ),
    )
