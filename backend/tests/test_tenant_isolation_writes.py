"""Company-scoped writes take the RESOURCE's company, never a stale selection.

The active company comes from the X-Inspro-Client header while the year comes
from the path or body (or from the record a path names). A system_admin passes
every tenant check, so a stale tab could stamp company A onto company B's year
or record — audit rows, AI spend, member and catalog rows. Such a request is
refused with 409 `client_selection_stale` and writes nothing.

Split from `test_tenant_isolation.py`, whose fixture data (company B's year,
roster, panel and card rows) this module reuses: its module-scoped `_setup_db`
is imported so it runs here too.
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.auth import CurrentUser, get_current_user
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Category,
    Dependant,
    Employee,
    EmployeeAttributeSchema,
    FlexScheme,
    MemberAccount,
    PanelCard,
    PanelListing,
    Plan,
    PolicyYear,
    PolicyYearCard,
    PolicyYearPanel,
    Product,
)
from tests.test_tenant_isolation import (
    _FAKE_XLSX,
    CARD_ASSIGNMENT_B,
    CARD_B,
    CAT_B,
    CLIENT_B_ID,
    DEP_B,
    EMP_B,
    PANEL_B,
    PLAN_B,
    PRODUCT_B_OWNED,
    PY_B,
    _setup_db,  # noqa: F401 — the module-scoped autouse setup runs here as well
    _user_a,
    _user_system_admin,
)


@pytest.fixture
def client_as_system_admin() -> TestClient:
    app.dependency_overrides[get_current_user] = _user_system_admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


_CARD = {"panel_card_id": CARD_B, "product_id": PRODUCT_B_OWNED}

_STALE_YEAR_REQUESTS = [
    ("POST", f"/api/v1/policy-years/{PY_B}/copy",
     {"json": {"start_date": "2029-01-01", "end_date": "2029-12-31"}}),
    ("POST", "/api/v1/plans",
     {"json": {"policy_year_id": PY_B, "product_id": PRODUCT_B_OWNED, "display_name": "x"}}),
    ("POST", "/api/v1/match-results/run", {"params": {"policy_year_id": PY_B}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/eligibility-mappings/propose", {}),
    ("POST", f"/api/v1/policy-years/{PY_B}/eligibility-mappings/ai-create-category",
     {"json": {"plan_id": PLAN_B, "eligibility_description": "All staff"}}),
    ("PUT", f"/api/v1/policy-years/{PY_B}/product-setups/GHS",
     {"json": {"answers": {}, "template_version": 1}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/product-setups/GHS/confirm",
     {"json": {"answers": {}, "template_version": 1}}),
    ("DELETE", f"/api/v1/policy-years/{PY_B}/products/GHS", {}),
    ("POST", f"/api/v1/policy-years/{PY_B}/recommend-config", {}),
    ("POST", f"/api/v1/policy-years/{PY_B}/apply-config",
     {"json": {"attributes": [], "products": [], "rerun_matching": False}}),
    ("POST", "/api/v1/categories", {"json": {"policy_year_id": PY_B, "display_name": "x"}}),
    ("POST", f"/api/v1/categories/{CAT_B}/confirm", {}),
    ("POST", "/api/v1/categories/bulk-confirm", {"params": {"policy_year_id": PY_B}}),
    ("POST", f"/api/v1/categories/{CAT_B}/ai-suggest", {}),
    ("POST", "/api/v1/placement-slips/parse",
     {"files": {"file": _FAKE_XLSX}, "data": {"policy_year_id": PY_B}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/adc/preview", {"files": {"file": _FAKE_XLSX}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/adc/apply", {"files": {"file": _FAKE_XLSX}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/employee-listing/preview",
     {"files": {"file": _FAKE_XLSX}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/employee-listing/apply",
     {"files": {"file": _FAKE_XLSX}, "data": {"mapping": "{}"}}),
    ("GET", f"/api/v1/policy-years/{PY_B}/product-terms", {}),
    ("GET", f"/api/v1/policy-years/{PY_B}/roster-readiness", {}),
    # Roster, portal rollout, flex, panel and card writes against B's year.
    ("DELETE", "/api/v1/employees", {"params": {"policy_year_id": PY_B}}),
    ("POST", "/api/v1/employees/upload",
     {"files": {"file": _FAKE_XLSX}, "data": {"policy_year_id": PY_B}}),
    ("POST", "/api/v1/dependants/auto-match", {"params": {"policy_year_id": PY_B}}),
    ("DELETE", "/api/v1/dependants", {"params": {"policy_year_id": PY_B}}),
    ("POST", "/api/v1/dependants/upload",
     {"files": {"file": _FAKE_XLSX}, "data": {"policy_year_id": PY_B}}),
    ("GET", "/api/v1/member-accounts/rollout", {"params": {"policy_year_id": PY_B}}),
    ("POST", "/api/v1/member-accounts/bulk-invite", {"json": {"policy_year_id": PY_B}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/flex-scheme/extract",
     {"files": {"files": _FAKE_XLSX}}),
    ("PUT", f"/api/v1/policy-years/{PY_B}/flex-scheme", {"json": {"scheme": {}}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/flex-scheme/suggest-matches", {}),
    ("POST", f"/api/v1/policy-years/{PY_B}/flex-scheme/confirm", {}),
    ("POST", f"/api/v1/policy-years/{PY_B}/flex-scheme/assign", {}),
    ("DELETE", f"/api/v1/policy-years/{PY_B}/flex-scheme", {}),
    ("PUT", f"/api/v1/policy-years/{PY_B}/panels", {"json": {"panel_listing_ids": []}}),
    ("POST", f"/api/v1/policy-years/{PY_B}/cards", {"json": _CARD}),
    ("PUT", f"/api/v1/policy-years/{PY_B}/cards/{CARD_ASSIGNMENT_B}", {"json": _CARD}),
    ("DELETE", f"/api/v1/policy-years/{PY_B}/cards/{CARD_ASSIGNMENT_B}", {}),
    # Writes to one of B's records: the record names its company.
    ("PATCH", f"/api/v1/employees/{EMP_B}", {"json": {"employee_name": "x"}}),
    ("PATCH", f"/api/v1/dependants/{DEP_B}", {"json": {"attribute_values": {}}}),
    ("POST", f"/api/v1/dependants/{DEP_B}/approval", {"json": {"action": "approve"}}),
    ("PATCH", f"/api/v1/panel-listings/{PANEL_B}", {"json": {"label": "x"}}),
    ("PATCH", f"/api/v1/panel-cards/{CARD_B}", {"json": {"name": "x"}}),
]


def _year_scoped_row_counts() -> tuple[int, ...]:
    from app.models import AuditLog, PlacementSlipRow, ProductSetup

    with SessionLocal() as session:
        return tuple(
            session.query(model).count()
            for model in (
                PolicyYear, Plan, Category, Product, Employee, Dependant, ProductSetup,
                PlacementSlipRow, MemberAccount, FlexScheme, PolicyYearPanel,
                PolicyYearCard, PanelListing, PanelCard, AuditLog,
            )
        )


@pytest.mark.parametrize(("method", "path", "kwargs"), _STALE_YEAR_REQUESTS)
def test_system_admin_cannot_act_on_another_companys_year(
    client_as_system_admin: TestClient, method: str, path: str, kwargs: dict
) -> None:
    before = _year_scoped_row_counts()
    res = client_as_system_admin.request(method, path, **kwargs)
    assert res.status_code == 409, (method, path, res.text)
    assert res.json()["detail"]["code"] == "client_selection_stale"
    assert _year_scoped_row_counts() == before


def test_system_admin_acts_on_the_selected_companys_year() -> None:
    """The guard compares companies; it does not stop a platform admin who has
    actually selected the year's company."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="00000000-0000-0000-0000-0000000000d0",
        broker_firm_id=None,
        client_id=CLIENT_B_ID,
        role="system_admin",
    )
    try:
        client = TestClient(app)
        assert client.get(f"/api/v1/policy-years/{PY_B}/product-terms").status_code == 200
        assert client.get(f"/api/v1/policy-years/{PY_B}/roster-readiness").status_code == 200
        assert client.get(
            "/api/v1/member-accounts/rollout", params={"policy_year_id": PY_B}
        ).status_code == 200
        renamed = client.patch(f"/api/v1/employees/{EMP_B}", json={"employee_name": "Bea Beta"})
        assert renamed.status_code == 200, renamed.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_matching_takes_the_years_company_not_the_actors() -> None:
    """`match_policy_year` scopes its catalog to the YEAR's company: run for an
    actor whose active company is A, B's roster still derives with B's own
    attribute schemas (and entity aliases), never A's."""
    from app.services.matching_engine import match_policy_year

    with SessionLocal() as session:
        schema = EmployeeAttributeSchema(
            client_id=CLIENT_B_ID,
            attribute_id="client_b_band",
            display_name="Client B band",
            data_type="text",
            derivation_rule={"op": "passthrough", "source": "grade"},
        )
        session.add(schema)
        session.commit()
        schema_id = schema.id
        try:
            match_policy_year(session, PY_B, _user_a())
            employee = session.get(Employee, EMP_B)
            assert employee is not None
            assert employee.derived_attribute_values.get("client_b_band") == 18
        finally:
            session.rollback()
            session.query(EmployeeAttributeSchema).filter_by(id=schema_id).delete()
            session.commit()


# ── Master-admin access grants on Postgres (schema routing) ─────────────────
# Skipped unless INSPRO_PG_TEST_URL names a disposable Postgres, e.g.
#   INSPRO_PG_TEST_URL=postgresql+psycopg://postgres:...@127.0.0.1:55432/iso_tenancy
_PG_URL = os.environ.get("INSPRO_PG_TEST_URL")
_PG_OWNER = "7a0e0000-0000-4000-8000-0000000000a1"
_PG_OTHER = "7a0e0000-0000-4000-8000-0000000000a2"
_PG_OWNER_CLIENT = "7a0e0000-0000-4000-8000-0000000000b1"
_PG_OTHER_CLIENT = "7a0e0000-0000-4000-8000-0000000000b2"
_PG_ADMIN = "7a0e0000-0000-4000-8000-0000000000c1"


def _public_is_current(engine: Any) -> bool:
    """True when every public table matches today's models (no missing column)."""
    import app.models  # noqa: F401 — registers every table on Base.metadata
    from app.db.base import Base

    inspector = inspect(engine)
    existing = set(inspector.get_table_names(schema="public"))
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(table.name, schema="public")}
        if not {c.name for c in table.columns} <= columns:
            return False
    return True


def _current_public(engine: Any) -> None:
    """Bring the disposable database's public schema up to today's models.

    `create_all` adds missing tables but never columns, so a database built
    before the multi-broker control plane is rebuilt from scratch (with the
    firm schemas that reference it).
    """
    from app.db.base import Base

    if not _public_is_current(engine):
        with engine.begin() as conn:
            firm_schemas = conn.execute(
                text("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'firm\\_%'")
            ).scalars().all()
            for schema in firm_schemas:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)


def _drop_pg_fixture(engine: Any) -> None:
    from app.db.tenancy import schema_for_firm

    with engine.begin() as conn:
        for firm_id in (_PG_OWNER, _PG_OTHER):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_for_firm(firm_id)}" CASCADE'))
        conn.execute(text("DELETE FROM platform_access_grants WHERE user_id = :u"),
                     {"u": _PG_ADMIN})
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": _PG_ADMIN})
        conn.execute(text("DELETE FROM clients WHERE id IN (:a, :b)"),
                     {"a": _PG_OWNER_CLIENT, "b": _PG_OTHER_CLIENT})
        conn.execute(text("DELETE FROM broker_firms WHERE id IN (:a, :b)"),
                     {"a": _PG_OWNER, "b": _PG_OTHER})


def _seed_pg_firms(engine: Any) -> None:
    from sqlalchemy.orm import sessionmaker

    from app.db.tenancy import provision_firm_schema
    from app.models import BrokerFirm, Client, User

    with sessionmaker(bind=engine)() as s:
        has_owner = s.query(BrokerFirm.id).filter(BrokerFirm.is_platform_owner.is_(True)).first()
        s.add_all([
            BrokerFirm(id=_PG_OWNER, name="PG Owner", slug="pg-grant-owner",
                       is_platform_owner=has_owner is None),
            BrokerFirm(id=_PG_OTHER, name="PG Other", slug="pg-grant-other"),
        ])
        s.flush()
        s.add_all([
            Client(id=_PG_OWNER_CLIENT, name="PG Owner Co", broker_firm_id=_PG_OWNER),
            Client(id=_PG_OTHER_CLIENT, name="PG Other Co", broker_firm_id=_PG_OTHER),
            User(id=_PG_ADMIN, email="pg.grant.master@inspro.test", broker_firm_id=None,
                 role="system_admin", status="active"),
        ])
        s.commit()
    for firm_id in (_PG_OWNER, _PG_OTHER):
        provision_firm_schema(engine, firm_id)


@pytest.mark.skipif(not _PG_URL, reason="INSPRO_PG_TEST_URL not set — Postgres-only test")
def test_access_grant_routes_and_audits_in_the_firms_own_schema_pg() -> None:
    """A grant opens the firm's schema to the master admin, its creation and
    revocation land in THAT firm's audit_log (never public's), and once revoked
    the firm's companies are out of reach again."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api.v1.platform import GrantCreate, create_access_grant, revoke_access_grant
    from app.core.auth import Principal, _build_current_user, _route_to_firm
    from app.core.identity import assert_client_accessible
    from app.db.tenancy import schema_for_firm

    engine = create_engine(str(_PG_URL))
    try:
        _current_public(engine)
        _drop_pg_fixture(engine)
        _seed_pg_firms(engine)
        actor = CurrentUser(user_id=_PG_ADMIN, broker_firm_id=None, client_id=None,
                            role="system_admin")
        principal = Principal(user_id=_PG_ADMIN, broker_firm_id=None, role="system_admin")
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as db:
            assert assert_client_accessible(
                role="system_admin", broker_firm_id=None, user_id=_PG_ADMIN,
                client_id=_PG_OTHER_CLIENT, db=db,
            ) is None
            grant = create_access_grant(GrantCreate(
                broker_firm_id=_PG_OTHER, reason="Postgres routing check for grants",
                scope="read", hours=1,
            ), user=actor, db=db)
        with Session() as db:
            current = _build_current_user(principal, _PG_OTHER_CLIENT, db, read_only=True)
            assert (current.client_id, current.platform_access) == (_PG_OTHER_CLIENT, "read")
            _route_to_firm(db, current)
            assert schema_for_firm(_PG_OTHER) in str(db.execute(text("SHOW search_path")).scalar())
            revoke_access_grant(grant.id, user=actor, db=db)
        with engine.connect() as conn:
            firm_rows = conn.execute(text(
                f'SELECT action FROM "{schema_for_firm(_PG_OTHER)}".audit_log '
                "WHERE entity_id = :g ORDER BY action"
            ), {"g": grant.id}).scalars().all()
            public_rows = conn.execute(text(
                "SELECT count(*) FROM public.audit_log WHERE entity_id = :g"
            ), {"g": grant.id}).scalar()
            platform_rows = conn.execute(text(
                "SELECT action FROM public.platform_audit_log WHERE entity_id = :g ORDER BY action"
            ), {"g": grant.id}).scalars().all()
        assert firm_rows == ["platform_access.create", "platform_access.revoke"]
        assert public_rows == 0
        assert platform_rows == ["access_grant.create", "access_grant.revoke"]
        with Session() as db:
            stale = _build_current_user(principal, _PG_OTHER_CLIENT, db, read_only=True)
            assert stale.client_id != _PG_OTHER_CLIENT
    finally:
        _drop_pg_fixture(engine)
        engine.dispose()
