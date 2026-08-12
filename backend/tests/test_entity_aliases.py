"""Insured-entity alias map — multi-entity aliases.

An alias may stand for MORE than one registered entity: a single roster
spelling ("STMICROELECTRONICS PTE LTD") covering several insured subsidiaries
("… AMK", "… TPY"). These tests cover the create-or-merge API and the
reconciliation vocabulary once such an alias exists.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.auth import (
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.db.session import SessionLocal
from app.main import app
from app.models import Category, Employee, EntityAlias, PolicyYear
from app.models.category import CategoryStatus, SourceKind
from app.models.policy_year import PolicyYearStatus
from app.services.entity_vocab import entity_vocabulary
from scripts.seed_demo import seed

STM = "STMICROELECTRONICS PTE LTD"
STM_AMK = "STMicroelectronics Pte Ltd AMK"
STM_TPY = "STMicroelectronics Pte Ltd TPY"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _seed():
    seed()
    yield


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def _clear_aliases():
    """Each test starts with no aliases for the demo client."""
    with SessionLocal() as db:
        db.query(EntityAlias).filter(EntityAlias.client_id == DEMO_CLIENT_ID).delete()
        db.commit()
    yield


# ── API: create, merge, edit ────────────────────────────────────────────────

def test_create_alias_with_multiple_entities(client: TestClient) -> None:
    res = client.post(
        "/api/v1/entity-aliases",
        json={"alias": STM, "canonicals": [STM_AMK, STM_TPY]},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["canonicals"] == [STM_AMK, STM_TPY]
    assert body["canonical"] == STM_AMK  # first, for display / legacy readers


def test_posting_same_alias_merges_new_entity(client: TestClient) -> None:
    """The reported bug: adding a SECOND entity to an existing alias must
    append, not 409. Posting the same alias merges the new entity in."""
    first = client.post(
        "/api/v1/entity-aliases", json={"alias": STM, "canonical": STM_AMK}
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/entity-aliases", json={"alias": STM, "canonical": STM_TPY}
    )
    assert second.status_code == 200  # merged, not created
    assert second.json()["canonicals"] == [STM_AMK, STM_TPY]

    # One row, not two — the alias stays unique.
    rows = client.get("/api/v1/entity-aliases").json()
    stm_rows = [r for r in rows if r["alias"] == STM]
    assert len(stm_rows) == 1
    assert stm_rows[0]["canonicals"] == [STM_AMK, STM_TPY]


def test_merge_is_idempotent(client: TestClient) -> None:
    client.post("/api/v1/entity-aliases", json={"alias": STM, "canonical": STM_AMK})
    again = client.post(
        "/api/v1/entity-aliases", json={"alias": STM, "canonical": STM_AMK}
    )
    assert again.status_code == 200
    assert again.json()["canonicals"] == [STM_AMK]


def test_edit_replaces_the_entity_list(client: TestClient) -> None:
    created = client.post(
        "/api/v1/entity-aliases",
        json={"alias": STM, "canonicals": [STM_AMK, STM_TPY]},
    ).json()
    res = client.patch(
        f"/api/v1/entity-aliases/{created['id']}",
        json={"canonicals": [STM_AMK]},
    )
    assert res.status_code == 200
    assert res.json()["canonicals"] == [STM_AMK]


def test_self_mapping_rejected(client: TestClient) -> None:
    res = client.post(
        "/api/v1/entity-aliases",
        json={"alias": "Acme Pte Ltd", "canonicals": ["Acme Pte. Ltd."]},
    )
    assert res.status_code == 422


def test_empty_entity_list_rejected(client: TestClient) -> None:
    res = client.post(
        "/api/v1/entity-aliases", json={"alias": STM, "canonicals": []}
    )
    assert res.status_code == 422


def test_legacy_canonical_is_stripped(client: TestClient) -> None:
    """A legacy single `canonical` with surrounding whitespace is stripped, the
    same as list entries — no space-padded spelling reaches storage."""
    res = client.post(
        "/api/v1/entity-aliases",
        json={"alias": "CSO", "canonical": "  City Serviced Offices Pte Ltd  "},
    )
    assert res.status_code == 201
    assert res.json()["canonical"] == "City Serviced Offices Pte Ltd"


def test_duplicate_entities_collapse(client: TestClient) -> None:
    res = client.post(
        "/api/v1/entity-aliases",
        json={"alias": STM, "canonicals": [STM_AMK, "STMICROELECTRONICS PTE LTD AMK"]},
    )
    assert res.status_code == 201
    # Both spellings normalize to the same entity → one target.
    assert len(res.json()["canonicals"]) == 1


# ── Vocabulary: reconciliation with a multi-entity alias ─────────────────────

def _seed_stm_policy_year(db) -> PolicyYear:
    py = PolicyYear(
        id="stm-py-2031",
        client_id=DEMO_CLIENT_ID,
        year=2031,
        start_date=date(2031, 1, 1),
        end_date=date(2031, 12, 31),
        status=PolicyYearStatus.draft,
    )
    db.add(py)
    db.flush()
    for cid, insured in (("stm-cat-amk", STM_AMK), ("stm-cat-tpy", STM_TPY)):
        db.add(
            Category(
                id=cid,
                policy_year_id=py.id,
                priority=0,
                display_name="All Employees",
                raw_description="All Employees",
                source=SourceKind.system_generated.value,
                status=CategoryStatus.confirmed.value,
                human_modified=False,
                plan_assignments={"insured": [insured]},
            )
        )
    for i in range(2):
        db.add(
            Employee(
                id=f"stm-emp-{i}",
                client_id=DEMO_CLIENT_ID,
                policy_year_id=py.id,
                staff_id=f"STM{i}",
                employee_name=f"Person {i}",
                attribute_values={"entity": STM},
                derived_attribute_values={},
                source="csv_import",
                status="active",
            )
        )
    db.flush()
    return py


def test_vocab_unreconciled_without_alias() -> None:
    """Without an alias, the generic roster spelling matches neither category,
    so both AMK and TPY surface as needing reconciliation, each suggesting the
    roster spelling."""
    with SessionLocal() as db:
        py = _seed_stm_policy_year(db)
        vocab = entity_vocabulary(db, py)

        known_values = {k.value for k in vocab.known}
        assert STM_AMK in known_values and STM_TPY in known_values
        assert all(
            k.suggestion == STM for k in vocab.known if k.value in {STM_AMK, STM_TPY}
        )
        db.rollback()


def test_vocab_known_dedups_multi_entity_config_alias() -> None:
    """A CATEGORY insured on an alias that expands to several entities lists that
    one config name ONCE, not once per entity it stands for."""
    with SessionLocal() as db:
        py = PolicyYear(
            id="stm-py-dedup",
            client_id=DEMO_CLIENT_ID,
            year=2032,
            start_date=date(2032, 1, 1),
            end_date=date(2032, 12, 31),
            status=PolicyYearStatus.draft,
        )
        db.add(py)
        db.flush()
        # Category insured on the alias spelling itself.
        db.add(
            Category(
                id="stm-dedup-cat",
                policy_year_id=py.id,
                priority=0,
                display_name="All Employees",
                raw_description="All Employees",
                source=SourceKind.system_generated.value,
                status=CategoryStatus.confirmed.value,
                human_modified=False,
                plan_assignments={"insured": [STM]},
            )
        )
        # An employee of an UNRELATED entity, so STM is config-only (unreconciled).
        db.add(
            Employee(
                id="stm-dedup-emp",
                client_id=DEMO_CLIENT_ID,
                policy_year_id=py.id,
                staff_id="OTH1",
                employee_name="Other",
                attribute_values={"entity": "Unrelated Co Pte Ltd"},
                derived_attribute_values={},
                source="csv_import",
                status="active",
            )
        )
        db.add(
            EntityAlias(
                client_id=DEMO_CLIENT_ID,
                alias=STM,
                canonical=STM_AMK,
                canonicals=[STM_AMK, STM_TPY],
                alias_normalized="stmicroelectronics pte ltd",
            )
        )
        db.flush()

        vocab = entity_vocabulary(db, py)
        stm_known = [k for k in vocab.known if k.value == STM]
        assert len(stm_known) == 1
        db.rollback()


def test_vocab_reconciled_with_multi_entity_alias() -> None:
    """With one alias mapping the generic spelling to BOTH subsidiaries, the
    roster row reads as claimed and nothing remains to reconcile."""
    with SessionLocal() as db:
        py = _seed_stm_policy_year(db)
        db.add(
            EntityAlias(
                client_id=DEMO_CLIENT_ID,
                alias=STM,
                canonical=STM_AMK,
                canonicals=[STM_AMK, STM_TPY],
                alias_normalized="stmicroelectronics pte ltd",
            )
        )
        db.flush()

        vocab = entity_vocabulary(db, py)
        stm_row = next(r for r in vocab.roster if r.value == STM)
        assert stm_row.claimed is True
        assert stm_row.count == 2
        assert not [k for k in vocab.known if k.value in {STM_AMK, STM_TPY}]
        db.rollback()
