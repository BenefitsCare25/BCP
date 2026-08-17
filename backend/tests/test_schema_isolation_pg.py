"""Schema-per-broker-firm physical isolation — Postgres only.

Skipped unless INSPRO_PG_TEST_URL points at a reachable Postgres (schemas
aren't a SQLite concept). Run e.g.:

    INSPRO_PG_TEST_URL=postgresql+psycopg://postgres:inspro@localhost:5433/inspro \
        uv run pytest tests/test_schema_isolation_pg.py

Verifies that two firms' operational rows land in separate schemas and that a
session bound to one firm cannot see the other's data even with no app-layer
filter.
"""
from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("INSPRO_PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL, reason="INSPRO_PG_TEST_URL not set — Postgres-only test"
)

FIRM_A = "11111111-1111-1111-1111-111111111111"
FIRM_B = "22222222-2222-2222-2222-222222222222"
CLI_A = "1111aaaa-1111-1111-1111-111111111111"
CLI_B = "2222bbbb-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(PG_URL)
    import app.models  # noqa: F401  — registers every table on Base.metadata
    from app.db.base import Base
    from app.db.tenancy import schema_for_firm

    # Without the `app.models` import above, `Base.metadata` is still empty here
    # (the tests import models inside their own bodies, i.e. after this fixture
    # runs), so create_all made nothing and every test died on NoSuchTableError.

    # Clean slate for the firms under test.
    with engine.begin() as c:
        for fid in (FIRM_A, FIRM_B):
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema_for_firm(fid)}" CASCADE'))
    Base.metadata.create_all(engine)  # control + (public) tenant tables
    yield engine
    with engine.begin() as c:
        for fid in (FIRM_A, FIRM_B):
            c.execute(text(f'DROP SCHEMA IF EXISTS "{schema_for_firm(fid)}" CASCADE'))
    engine.dispose()


def test_claim_id_is_nullable_in_every_schema(pg_engine) -> None:
    """A question's message has no claim, so `claim_messages.claim_id` must be
    nullable in EVERY schema — not just `public`.

    Scope, precisely: this builds schemas from the MODELS
    (`Base.metadata.create_all` + `provision_firm_schema`), so what it guards is
    that provisioning carries the nullable column into a firm schema — model and
    provisioning agreeing. It does NOT exercise the migration.

    The migration path is the other half and cannot be asserted here: a
    nullability change reaches only the schema Alembic ran against, which is why
    `a3f7c9d21b48` walks the firm schemas itself. That walk was verified by
    running the chain against a real Postgres with a firm schema present
    (`ALTER … DROP NOT NULL` applied to both, downgrade restoring both, and the
    downgrade refusing while claim-less messages exist). Re-run that by hand
    when touching it — an upgrade-from-real-data test needs a seeded database
    this module deliberately does not build.
    """
    from app.db.tenancy import provision_firm_schema, schema_for_firm
    from app.models import BrokerFirm

    with sessionmaker(bind=pg_engine)() as s:
        for fid, name in ((FIRM_A, "Firm A"), (FIRM_B, "Firm B")):
            if s.get(BrokerFirm, fid) is None:
                s.add(BrokerFirm(id=fid, name=name))
        s.commit()
    for fid in (FIRM_A, FIRM_B):
        provision_firm_schema(pg_engine, fid)

    schemas = ["public", *(schema_for_firm(f) for f in (FIRM_A, FIRM_B))]
    with pg_engine.connect() as c:
        for schema in schemas:
            nullable = c.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'claim_messages' "
                    "AND column_name = 'claim_id'"
                ),
                {"s": schema},
            ).scalar()
            assert nullable == "YES", f"{schema}.claim_messages.claim_id is NOT NULL"


def test_two_firms_are_physically_isolated(pg_engine) -> None:
    from app.db.tenancy import provision_firm_schema, schema_for_firm, set_search_path
    from app.models import BrokerFirm, Client, PolicyYear
    from app.models.policy_year import PolicyYearStatus

    Session = sessionmaker(bind=pg_engine, expire_on_commit=False)

    with Session() as s:
        for fid, nm in [(FIRM_A, "A"), (FIRM_B, "B")]:
            if not s.get(BrokerFirm, fid):
                s.add(BrokerFirm(id=fid, name=nm))
        s.flush()
        for cid, fid in [(CLI_A, FIRM_A), (CLI_B, FIRM_B)]:
            if not s.get(Client, cid):
                s.add(Client(id=cid, name=cid, broker_firm_id=fid))
        s.commit()

    assert provision_firm_schema(pg_engine, FIRM_A)
    assert provision_firm_schema(pg_engine, FIRM_B)

    for fid, cid, yr in [(FIRM_A, CLI_A, 2031), (FIRM_B, CLI_B, 2032)]:
        with Session() as s:
            set_search_path(s, fid)
            s.add(PolicyYear(
                client_id=cid, year=yr,
                start_date=date(yr, 1, 1), end_date=date(yr, 12, 31),
                status=PolicyYearStatus.draft,
            ))
            s.commit()

    # A session bound to firm A sees only firm A's rows — no app filter applied.
    with Session() as s:
        set_search_path(s, FIRM_A)
        a_years = s.execute(text("SELECT year FROM policy_years ORDER BY year")).scalars().all()
    with Session() as s:
        set_search_path(s, FIRM_B)
        b_years = s.execute(text("SELECT year FROM policy_years ORDER BY year")).scalars().all()

    assert a_years == [2031]
    assert b_years == [2032]

    sa, sb = schema_for_firm(FIRM_A), schema_for_firm(FIRM_B)
    with pg_engine.connect() as c:
        assert c.execute(text(f'SELECT count(*) FROM "{sa}".policy_years')).scalar() == 1
        assert c.execute(text(f'SELECT count(*) FROM "{sb}".policy_years')).scalar() == 1


def test_set_search_path_resets_to_public(pg_engine) -> None:
    """A None firm resets the path to public so pooled connections don't leak
    a previous tenant's schema."""
    from app.db.tenancy import schema_for_firm, set_search_path

    Session = sessionmaker(bind=pg_engine)
    with Session() as s:
        set_search_path(s, FIRM_A)
        assert schema_for_firm(FIRM_A) in s.execute(text("SHOW search_path")).scalar()
        set_search_path(s, None)
        assert schema_for_firm(FIRM_A) not in s.execute(text("SHOW search_path")).scalar()


def test_new_tenant_audit_log_is_append_only(pg_engine) -> None:
    """Provisioning after Alembic must install the same trigger as migration."""
    from sqlalchemy.exc import DBAPIError

    from app.db.tenancy import provision_firm_schema, set_search_path
    from app.models import AuditLog, BrokerFirm, Client

    Session = sessionmaker(bind=pg_engine, expire_on_commit=False)
    with Session() as s:
        if s.get(BrokerFirm, FIRM_A) is None:
            s.add(BrokerFirm(id=FIRM_A, name="Firm A"))
            s.flush()
        if s.get(Client, CLI_A) is None:
            s.add(Client(id=CLI_A, name="Client A", broker_firm_id=FIRM_A))
        s.commit()
    provision_firm_schema(pg_engine, FIRM_A)

    with Session() as s:
        set_search_path(s, FIRM_A)
        row = AuditLog(
            client_id=CLI_A,
            action="claim.view",
            entity_type="claim",
            entity_id="audit-probe",
            cross_tenant_access=False,
        )
        s.add(row)
        s.commit()
        audit_id = row.id

    with Session() as s:
        set_search_path(s, FIRM_A)
        with pytest.raises(DBAPIError, match="audit_log is append-only"):
            s.execute(
                text("UPDATE audit_log SET action='tampered' WHERE id=:id"),
                {"id": audit_id},
            )
            s.commit()


def test_tenant_notification_leases_skip_locked(pg_engine) -> None:
    """Two workers must lease different outbox rows without waiting."""
    from app.db.tenancy import provision_firm_schema, set_search_path
    from app.models import BrokerFirm, ClaimNotification, Client

    Session = sessionmaker(bind=pg_engine, expire_on_commit=False)
    with Session() as s:
        if s.get(BrokerFirm, FIRM_A) is None:
            s.add(BrokerFirm(id=FIRM_A, name="Firm A"))
            s.flush()
        if s.get(Client, CLI_A) is None:
            s.add(Client(id=CLI_A, name="Client A", broker_firm_id=FIRM_A))
        s.commit()
    provision_firm_schema(pg_engine, FIRM_A)
    with Session() as s:
        set_search_path(s, FIRM_A)
        for suffix in ("one", "two"):
            s.add(
                ClaimNotification(
                    client_id=CLI_A,
                    claim_id=f"claim-{suffix}",
                    source_message_id=f"message-{suffix}",
                    recipient_email="test@example.invalid",
                    available_at=s.execute(text("SELECT now()")).scalar_one(),
                )
            )
        s.commit()

    first = Session()
    second = Session()
    try:
        set_search_path(first, FIRM_A)
        set_search_path(second, FIRM_A)
        leased_first = first.execute(
            text(
                "SELECT id FROM claim_notifications WHERE status='queued' "
                "ORDER BY created_at LIMIT 1 FOR UPDATE"
            )
        ).scalar_one()
        leased_second = second.execute(
            text(
                "SELECT id FROM claim_notifications WHERE status='queued' "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
        ).scalar_one()
        assert leased_second != leased_first
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def test_tenant_routing_survives_commit(pg_engine) -> None:
    """A read AFTER commit must still resolve to the firm schema.

    Regression: `SET search_path` binds to a CONNECTION, but a Session releases
    its connection at commit() and the pool's checkin hook resets the path to
    public. Every handler that read back after committing (db.refresh / db.get /
    a follow-up query — 50+ sites) therefore hit public, where tenant tables are
    empty: `POST /policy-years` 500'd in prod with "Could not refresh instance".
    SQLite has no schemas, so the whole suite was blind to it.
    """
    from app.db.tenancy import provision_firm_schema, schema_for_firm, set_search_path
    from app.models import PolicyYear
    from app.models.policy_year import PolicyYearStatus

    provision_firm_schema(pg_engine, FIRM_A)
    Session = sessionmaker(bind=pg_engine, expire_on_commit=False)

    with Session() as s:
        set_search_path(s, FIRM_A)
        py = PolicyYear(
            client_id=CLI_A,
            year=2041,
            start_date=date(2041, 1, 1),
            end_date=date(2041, 12, 31),
            status=PolicyYearStatus.draft,
        )
        s.add(py)
        s.commit()

        # The exact failing line from create_policy_year.
        s.refresh(py)
        assert py.year == 2041

        # ...and any other post-commit read, by PK or by query.
        assert s.get(PolicyYear, py.id) is not None
        assert schema_for_firm(FIRM_A) in s.execute(text("SHOW search_path")).scalar()

    # The row really is in the firm schema, not public.
    with pg_engine.connect() as c:
        sa = schema_for_firm(FIRM_A)
        assert c.execute(
            text(f"SELECT count(*) FROM \"{sa}\".policy_years WHERE year = 2041")
        ).scalar() == 1


def test_the_portal_sign_in_gate_reads_the_FIRM_schema(pg_engine) -> None:
    """A leaver's sign-in refusal must resolve against the firm's own roster.

    Regression, and the shape this file exists for. `_issue_member_login` reads
    `policy_years`, `employees` and `claims` — all TENANT tables — but the
    portal AUTH endpoints have no member token yet, so `get_current_member`
    (the only other `set_search_path` call on this surface) has not run. Until
    the leaver gate they touched control tables only, which live in `public` on
    every deployment, so nothing noticed.

    Unrouted, every one of those names resolves against `public`, which holds no
    tenant rows: the access check comes back `unknown` and the gate silently
    signs in every member whose access has ended — the session-terminating half
    of the feature, dead in prod and green on SQLite. `require_portal_tenant`
    now routes the schema; this asserts it, from both sides.
    """
    from datetime import timedelta

    from app.db.tenancy import provision_firm_schema, schema_for_firm, set_search_path
    from app.models import Employee, PolicyYear
    from app.models.employee import EMPLOYEE_STATUS_TERMINATED
    from app.models.policy_year import PolicyYearStatus
    from app.services.member_access import access_for_account

    provision_firm_schema(pg_engine, FIRM_A)
    Session = sessionmaker(bind=pg_engine, expire_on_commit=False)
    today = date(2042, 6, 1)

    with Session() as s:
        set_search_path(s, FIRM_A)
        year = PolicyYear(
            client_id=CLI_A, year=2042,
            start_date=date(2042, 1, 1), end_date=date(2042, 12, 31),
            status=PolicyYearStatus.active, leaver_access_days=10,
        )
        s.add(year)
        s.flush()
        s.add(
            Employee(
                client_id=CLI_A, policy_year_id=year.id, staff_id="PG-1",
                status=EMPLOYEE_STATUS_TERMINATED,
                terminated_effective=today - timedelta(days=200),
                member_account_id="acc-pg-leaver", attribute_values={},
            )
        )
        s.commit()

        assert access_for_account(
            s, member_account_id="acc-pg-leaver", client_id=CLI_A,
            staff_id="PG-1", today=today,
        ).state == "ended"

    # And the failure mode itself: with the path back at `public` the same call
    # finds nothing and reports `unknown`, which would let the member in. This
    # is what shipped before `require_portal_tenant` routed the schema.
    with Session() as s:
        set_search_path(s, None)
        assert access_for_account(
            s, member_account_id="acc-pg-leaver", client_id=CLI_A,
            staff_id="PG-1", today=today,
        ).state == "unknown"

    with pg_engine.connect() as c:
        sa = schema_for_firm(FIRM_A)
        assert c.execute(
            text(f'SELECT count(*) FROM "{sa}".employees WHERE staff_id = \'PG-1\'')
        ).scalar() == 1
