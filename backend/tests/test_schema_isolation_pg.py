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
    from app.db.base import Base
    from app.db.tenancy import schema_for_firm

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
