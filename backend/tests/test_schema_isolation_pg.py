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
