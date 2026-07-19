"""Panel e-cards — broker CRUD, artwork, placements, policy-year assignment,
member-id resolution, the member card payload and portal/preview isolation.
"""
from __future__ import annotations

import os
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_panel_cards.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Dependant,
    Employee,
    MemberAccount,
    PanelCard,
    Plan,
    PolicyYear,
    PolicyYearCard,
    Product,
    ProductTerm,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.member_account import MEMBER_STATUS_ACTIVE  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    StatementEmployee,
)
from app.services.panel_cards import (  # noqa: E402
    build_member_cards,
    dependant_key,
    insurer_member_id,
    mask_nric,
    platform_dependant_id,
    platform_member_id,
)
from scripts.seed_demo import seed  # noqa: E402

PY_A = "00000000-0000-0000-0000-00000pcard01"
EMP_ALICE = "00000000-0000-0000-0000-00000pcard02"
ACC_ALICE = "00000000-0000-0000-0000-00000pcard03"
DEP_SPOUSE = "00000000-0000-0000-0000-00000pcard04"
PRODUCT_GCGP = "00000000-0000-0000-0000-00000pcard05"
CAT_GCGP = "00000000-0000-0000-0000-00000pcard06"

CLIENT_B_ID = "00000000-0000-0000-0000-00000pcard0b"
PY_B = "00000000-0000-0000-0000-00000pcard0c"


def _png_bytes(width: int = 1012, height: int = 638) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), (200, 30, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _user_a() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY_A,
                client_id=DEMO_CLIENT_ID,
                year=2031,
                start_date=date(2031, 1, 1),
                end_date=date(2031, 12, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.add(
            Client(id=CLIENT_B_ID, name="Card Co B", broker_firm_id=DEMO_BROKER_FIRM_ID)
        )
        session.flush()
        session.add(
            PolicyYear(
                id=PY_B,
                client_id=CLIENT_B_ID,
                year=2031,
                start_date=date(2031, 1, 1),
                end_date=date(2031, 12, 31),
                status=PolicyYearStatus.draft,
            )
        )
        session.add(
            Product(
                id=PRODUCT_GCGP,
                client_id=DEMO_CLIENT_ID,
                code="GCGP",
                display_name="Group Clinical General Practitioner",
                insurer="AIA",
                has_dependants=True,
            )
        )
        session.flush()
        # A real plan + category so `build_member_statement` yields an actual
        # GCGP coverage line. Without this the employee is unmatched, the
        # statement is empty, and every card assertion passes vacuously.
        session.add(
            Plan(
                product_id=PRODUCT_GCGP,
                policy_year_id=PY_A,
                code="Plan A",
                display_name="GP Plan A",
                cover_description="Panel GP",
            )
        )
        session.add(
            Category(
                id=CAT_GCGP,
                policy_year_id=PY_A,
                product_id=PRODUCT_GCGP,
                priority=1,
                display_name="All Staff and Eligible Dependants",
                raw_description="All Staff and Eligible Dependants",
                rule_human_readable="all staff",
                plan_assignments={"plan_code": "Plan A"},
                source=SourceKind.system_generated.value,
                status=CategoryStatus.needs_review.value,
                human_modified=False,
            )
        )
        session.add(
            MemberAccount(
                id=ACC_ALICE,
                client_id=DEMO_CLIENT_ID,
                email="alice@cards.test",
                staff_id="CARD-1",
                status=MEMBER_STATUS_ACTIVE,
            )
        )
        session.add(
            Employee(
                id=EMP_ALICE,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_A,
                staff_id="CARD-1",
                employee_name="Alice Tan",
                member_account_id=ACC_ALICE,
                national_id_normalized="S1234567D",
                attribute_values={"insurer_member_ids": {"AIA": "2427617201"}},
                derived_attribute_values={},
                matched_category_id=CAT_GCGP,
                match_method="rule",
                match_confidence=0.9,
                matched_categories=[
                    {
                        "category_id": CAT_GCGP,
                        "product_code": "GCGP",
                        "method": "rule",
                        "confidence": 0.9,
                    }
                ],
                source="csv_import",
                status="active",
            )
        )
        session.flush()
        session.add(
            Dependant(
                id=DEP_SPOUSE,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_A,
                employee_id=EMP_ALICE,
                national_id_normalized="S7654321J",
                attribute_values={
                    "dependant_name": "Bob Tan",
                    "relationship": "Spouse",
                    "insurer_member_ids": {"AIA": "2427617202"},
                },
                status="active",
            )
        )
        session.add(
            ProductTerm(
                policy_year_id=PY_A,
                product_id=PRODUCT_GCGP,
                policy_number="G-99887766",
            )
        )
        session.commit()
    yield
    # These modules share one SQLite file (the engine binds to whichever
    # module imports first), so everything this fixture created must be
    # removed — a stray ACTIVE policy year for the demo client changes what
    # other modules' "current year" lookups resolve to.
    with SessionLocal() as session:
        session.query(PolicyYearCard).delete()
        session.query(PanelCard).delete()
        session.query(ProductTerm).filter(
            ProductTerm.product_id == PRODUCT_GCGP
        ).delete()
        session.query(Dependant).filter(Dependant.id == DEP_SPOUSE).delete()
        session.query(Employee).filter(Employee.id == EMP_ALICE).delete()
        session.query(MemberAccount).filter(MemberAccount.id == ACC_ALICE).delete()
        session.query(Category).filter(Category.id == CAT_GCGP).delete()
        session.query(Plan).filter(Plan.policy_year_id == PY_A).delete()
        session.query(Product).filter(Product.id == PRODUCT_GCGP).delete()
        for year_id in (PY_A, PY_B):
            year = session.get(PolicyYear, year_id)
            if year is not None:
                session.delete(year)
        client_b = session.get(Client, CLIENT_B_ID)
        if client_b is not None:
            session.delete(client_b)
        session.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture()
def client_as_a():
    app.dependency_overrides[get_current_user] = _user_a
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def member_client():
    token, _ = issue_member_token(ACC_ALICE, DEMO_CLIENT_ID)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


def _create_card(client: TestClient, name: str) -> dict:
    res = client.post(
        "/api/v1/panel-cards",
        json={"insurer": "AIA Singapore", "panel_provider": "Parkway Shenton", "name": name},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _upload_front(client: TestClient, card_id: str) -> dict:
    res = client.post(
        f"/api/v1/panel-cards/{card_id}/artwork/front",
        files={"file": ("card.png", _png_bytes(), "image/png")},
    )
    assert res.status_code == 200, res.text
    return res.json()


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_platform_member_id_is_stable_across_policy_years() -> None:
    # Keyed on (client, staff id) — NOT the Employee row, which is recreated
    # every renewal. A card number that changed yearly would be useless.
    first = platform_member_id(DEMO_CLIENT_ID, "CARD-1")
    assert first == platform_member_id(DEMO_CLIENT_ID, "CARD-1")
    assert first != platform_member_id(DEMO_CLIENT_ID, "CARD-2")
    assert first != platform_member_id(CLIENT_B_ID, "CARD-1")
    assert first.startswith("INS-")


def test_mask_nric_keeps_only_last_four() -> None:
    assert mask_nric("S1234567D") == "*****567D"
    assert mask_nric(None) == ""
    assert mask_nric("  ") == ""


def test_insurer_member_id_tolerates_casing_and_single_entry() -> None:
    attrs = {"insurer_member_ids": {"AIA": "242761"}}
    assert insurer_member_id(attrs, "AIA") == "242761"
    assert insurer_member_id(attrs, "aia") == "242761"
    # Sole id wins even when the roster header names a different insurer.
    assert insurer_member_id(attrs, "Zurich") == "242761"
    two = {"insurer_member_ids": {"AIA": "1", "Zurich": "2"}}
    assert insurer_member_id(two, "Great Eastern") == ""
    assert insurer_member_id({}, "AIA") == ""


# ── Broker CRUD + artwork ────────────────────────────────────────────────────


def test_create_list_and_duplicate_card(client_as_a: TestClient) -> None:
    card = _create_card(client_as_a, "AIA Parkway Shenton")
    assert card["has_front"] is False
    assert card["placements"] == {"fields": []}

    dupe = client_as_a.post(
        "/api/v1/panel-cards",
        json={
            "insurer": "AIA Singapore",
            "panel_provider": "Parkway Shenton",
            "name": "AIA Parkway Shenton",
        },
    )
    assert dupe.status_code == 409
    assert dupe.json()["detail"]["code"] == "duplicate_panel_card"

    listed = client_as_a.get("/api/v1/panel-cards")
    assert listed.status_code == 200
    assert any(c["id"] == card["id"] for c in listed.json())


def test_artwork_upload_stamps_aspect_ratio_and_serves_bytes(
    client_as_a: TestClient,
) -> None:
    card = _create_card(client_as_a, "Aspect Card")
    updated = _upload_front(client_as_a, card["id"])
    assert updated["has_front"] is True
    assert updated["aspect_ratio"] == pytest.approx(1012 / 638, rel=1e-4)

    served = client_as_a.get(f"/api/v1/panel-cards/{card['id']}/artwork/front")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content[:4] == b"\x89PNG"

    missing = client_as_a.get(f"/api/v1/panel-cards/{card['id']}/artwork/back")
    assert missing.status_code == 404


def test_artwork_rejects_non_image_and_bad_face(client_as_a: TestClient) -> None:
    card = _create_card(client_as_a, "Reject Card")
    bad_type = client_as_a.post(
        f"/api/v1/panel-cards/{card['id']}/artwork/front",
        files={"file": ("card.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert bad_type.status_code == 415

    not_an_image = client_as_a.post(
        f"/api/v1/panel-cards/{card['id']}/artwork/front",
        files={"file": ("card.png", b"definitely not a png", "image/png")},
    )
    assert not_an_image.status_code == 422

    bad_face = client_as_a.post(
        f"/api/v1/panel-cards/{card['id']}/artwork/side",
        files={"file": ("card.png", _png_bytes(), "image/png")},
    )
    assert bad_face.status_code == 422


def test_placements_roundtrip_and_reject_unknown_key(client_as_a: TestClient) -> None:
    card = _create_card(client_as_a, "Placement Card")
    ok = client_as_a.put(
        f"/api/v1/panel-cards/{card['id']}/placements",
        json={
            "fields": [
                {"key": "member_name", "face": "front", "x": 0.08, "y": 0.62},
                {
                    "key": "member_id",
                    "face": "front",
                    "x": 0.08,
                    "y": 0.72,
                    "size": 0.06,
                    "align": "left",
                },
            ]
        },
    )
    assert ok.status_code == 200, ok.text
    fields = ok.json()["placements"]["fields"]
    assert [f["key"] for f in fields] == ["member_name", "member_id"]

    bad = client_as_a.put(
        f"/api/v1/panel-cards/{card['id']}/placements",
        json={"fields": [{"key": "salary", "x": 0.1, "y": 0.1}]},
    )
    assert bad.status_code == 422

    out_of_bounds = client_as_a.put(
        f"/api/v1/panel-cards/{card['id']}/placements",
        json={"fields": [{"key": "member_id", "x": 1.4, "y": 0.1}]},
    )
    assert out_of_bounds.status_code == 422


def test_delete_card_removes_it(client_as_a: TestClient) -> None:
    card = _create_card(client_as_a, "Doomed Card")
    _upload_front(client_as_a, card["id"])
    assert client_as_a.delete(f"/api/v1/panel-cards/{card['id']}").status_code == 204
    listed = client_as_a.get("/api/v1/panel-cards").json()
    assert all(c["id"] != card["id"] for c in listed)


def test_card_options_expose_vocabulary(client_as_a: TestClient) -> None:
    res = client_as_a.get("/api/v1/panel-cards/options")
    assert res.status_code == 200
    body = res.json()
    keys = {o["key"] for o in body["placement_keys"]}
    assert {"member_id", "member_name", "remark_ae"} <= keys
    assert {o["key"] for o in body["services"]} == {
        "gp",
        "xray_lab",
        "tcm",
        "dental",
        "specialist",
        "health_screening",
    }
    assert {o["key"] for o in body["member_id_sources"]} >= {
        "insurer_member_id",
        "platform_id",
    }


# ── Policy-year assignment ───────────────────────────────────────────────────


def test_assignment_requires_artwork_then_succeeds(client_as_a: TestClient) -> None:
    card = _create_card(client_as_a, "Assignable Card")
    body = {
        "panel_card_id": card["id"],
        "product_id": PRODUCT_GCGP,
        "employee_member_id_source": "insurer_member_id",
        "dependant_member_id_source": "insurer_member_id",
        "services": {"gp": True, "dental": False},
        "remarks": {"gp": "Present this card at any panel GP."},
        "show_future_cards": False,
    }
    blocked = client_as_a.post(f"/api/v1/policy-years/{PY_A}/cards", json=body)
    assert blocked.status_code == 422
    assert blocked.json()["detail"]["code"] == "card_has_no_artwork"

    _upload_front(client_as_a, card["id"])
    created = client_as_a.post(f"/api/v1/policy-years/{PY_A}/cards", json=body)
    assert created.status_code == 201, created.text
    assignment = created.json()
    assert assignment["product_code"] == "GCGP"
    assert assignment["services"] == {"gp": True, "dental": False}

    dupe = client_as_a.post(f"/api/v1/policy-years/{PY_A}/cards", json=body)
    assert dupe.status_code == 409
    assert dupe.json()["detail"]["code"] == "duplicate_product_card"

    listed = client_as_a.get(f"/api/v1/policy-years/{PY_A}/cards")
    assert listed.status_code == 200
    assert [a["id"] for a in listed.json()] == [assignment["id"]]

    deleted = client_as_a.delete(
        f"/api/v1/policy-years/{PY_A}/cards/{assignment['id']}"
    )
    assert deleted.status_code == 204
    assert client_as_a.get(f"/api/v1/policy-years/{PY_A}/cards").json() == []


def test_assignment_rejects_unknown_service_and_remark_keys(
    client_as_a: TestClient,
) -> None:
    card = _create_card(client_as_a, "Strict Card")
    _upload_front(client_as_a, card["id"])
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_A}/cards",
        json={
            "panel_card_id": card["id"],
            "product_id": PRODUCT_GCGP,
            "services": {"acupuncture": True},
        },
    )
    assert res.status_code == 422


def test_assignment_cross_tenant_year_404(client_as_a: TestClient) -> None:
    card = _create_card(client_as_a, "Cross Tenant Card")
    _upload_front(client_as_a, card["id"])
    res = client_as_a.post(
        f"/api/v1/policy-years/{PY_B}/cards",
        json={"panel_card_id": card["id"], "product_id": PRODUCT_GCGP},
    )
    assert res.status_code == 404
    assert client_as_a.get(f"/api/v1/policy-years/{PY_B}/cards").status_code == 404


# ── Member card resolution ───────────────────────────────────────────────────


def _statement(with_dependant: bool) -> BenefitStatementOut:
    return BenefitStatementOut(
        employee=StatementEmployee(
            id=EMP_ALICE, staff_id="CARD-1", employee_name="Alice Tan"
        ),
        policy_year_id=PY_A,
        is_matched=True,
        coverage=[
            CoverageLine(
                product_code="GCGP",
                product_name="Group Clinical GP",
                plan_code="Plan A",
                covers_dependants=with_dependant,
                covered_dependants=(
                    [
                        DependantSummary(
                            id=DEP_SPOUSE, name="Bob Tan", relationship="Spouse"
                        )
                    ]
                    if with_dependant
                    else []
                ),
            )
        ],
    )


def _assign(client: TestClient, **overrides) -> dict:
    card = _create_card(client, overrides.pop("card_name", "Member Card"))
    _upload_front(client, card["id"])
    client.put(
        f"/api/v1/panel-cards/{card['id']}/placements",
        json={"fields": [{"key": "member_id", "x": 0.1, "y": 0.7}]},
    )
    body = {
        "panel_card_id": card["id"],
        "product_id": PRODUCT_GCGP,
        "services": {"gp": True},
        "remarks": {"gp": "Show at panel GP"},
        "special_conditions": "Co-pay $5 per visit",
        **overrides,
    }
    res = client.post(f"/api/v1/policy-years/{PY_A}/cards", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _clear_assignments(client: TestClient) -> None:
    for assignment in client.get(f"/api/v1/policy-years/{PY_A}/cards").json():
        client.delete(f"/api/v1/policy-years/{PY_A}/cards/{assignment['id']}")


def test_build_member_cards_resolves_values(client_as_a: TestClient) -> None:
    _clear_assignments(client_as_a)
    _assign(client_as_a, card_name="Values Card")
    with SessionLocal() as session:
        employee = session.get(Employee, EMP_ALICE)
        cards = build_member_cards(session, employee, _statement(with_dependant=True))
    assert [c.holder_type for c in cards] == ["employee", "dependant"]

    employee_card = cards[0]
    assert employee_card.values["member_id"] == "2427617201"
    assert employee_card.values["member_name"] == "Alice Tan"
    assert employee_card.values["policy_number"] == "G-99887766"
    assert employee_card.values["nric_masked"] == "*****567D"
    assert employee_card.values["effective_date"] == "2031-01-01"
    assert employee_card.values["remark_gp"] == "Show at panel GP"
    assert [s.key for s in employee_card.services] == ["gp"]
    assert employee_card.special_conditions == "Co-pay $5 per visit"

    dependant_card = cards[1]
    assert dependant_card.values["member_id"] == "2427617202"
    assert dependant_card.values["dependant_name"] == "Bob Tan"
    assert dependant_card.values["relationship"] == "Spouse"


def test_platform_id_source_suffixes_dependants(client_as_a: TestClient) -> None:
    _clear_assignments(client_as_a)
    _assign(
        client_as_a,
        card_name="Platform Card",
        employee_member_id_source="platform_id",
        dependant_member_id_source="platform_id",
    )
    with SessionLocal() as session:
        employee = session.get(Employee, EMP_ALICE)
        cards = build_member_cards(session, employee, _statement(with_dependant=True))
    base = platform_member_id(DEMO_CLIENT_ID, "CARD-1")
    assert cards[0].values["member_id"] == base
    # The dependant's number derives from the DEPENDANT's own identity, not its
    # position, so it survives another dependant being added or dropped.
    assert cards[1].values["member_id"] == platform_dependant_id(
        DEMO_CLIENT_ID, "CARD-1", dependant_key("S7654321J", "Bob Tan", "Spouse")
    )
    assert cards[1].values["member_id"].startswith(f"{base}-")


def test_dependant_platform_id_survives_a_new_dependant() -> None:
    """Regression: a positional suffix silently renumbered every dependant when
    the covered set changed between renewals."""
    spouse = platform_dependant_id(
        DEMO_CLIENT_ID, "CARD-1", dependant_key("S7654321J", "Bob Tan", "Spouse")
    )
    # A child with no NRIC would sort FIRST under the old content-ordered index
    # and push the spouse from -01 to -02.
    child = platform_dependant_id(
        DEMO_CLIENT_ID, "CARD-1", dependant_key(None, "Cara Tan", "Child")
    )
    assert spouse != child
    assert spouse == platform_dependant_id(
        DEMO_CLIENT_ID, "CARD-1", dependant_key("S7654321J", "Bob Tan", "Spouse")
    )


def test_dependant_falls_back_to_employee_insurer_member_id(
    client_as_a: TestClient,
) -> None:
    """Rosters usually carry the insurer's number on the employee row only — a
    dependant card must not print a blank Member ID."""
    _clear_assignments(client_as_a)
    _assign(client_as_a, card_name="Fallback Card")
    with SessionLocal() as session:
        dependant = session.get(Dependant, DEP_SPOUSE)
        original = dependant.attribute_values
        dependant.attribute_values = {
            k: v for k, v in original.items() if k != "insurer_member_ids"
        }
        session.commit()
        try:
            employee = session.get(Employee, EMP_ALICE)
            cards = build_member_cards(
                session, employee, _statement(with_dependant=True)
            )
            assert cards[1].holder_type == "dependant"
            assert cards[1].values["member_id"] == "2427617201"
        finally:
            session.get(Dependant, DEP_SPOUSE).attribute_values = original
            session.commit()


def test_no_card_without_coverage_for_the_product(client_as_a: TestClient) -> None:
    _clear_assignments(client_as_a)
    _assign(client_as_a, card_name="Uncovered Card")
    empty = BenefitStatementOut(
        employee=StatementEmployee(
            id=EMP_ALICE, staff_id="CARD-1", employee_name="Alice Tan"
        ),
        policy_year_id=PY_A,
        is_matched=True,
        coverage=[],
    )
    with SessionLocal() as session:
        employee = session.get(Employee, EMP_ALICE)
        assert build_member_cards(session, employee, empty) == []


def test_dependant_card_only_when_covered(client_as_a: TestClient) -> None:
    _clear_assignments(client_as_a)
    _assign(client_as_a, card_name="Employee Only Card")
    with SessionLocal() as session:
        employee = session.get(Employee, EMP_ALICE)
        cards = build_member_cards(session, employee, _statement(with_dependant=False))
    assert [c.holder_type for c in cards] == ["employee"]


# ── Portal + preview ─────────────────────────────────────────────────────────


def test_portal_cards_and_preview_agree(
    client_as_a: TestClient, member_client: TestClient
) -> None:
    _clear_assignments(client_as_a)
    _assign(client_as_a, card_name="Portal Card")

    portal = member_client.get("/api/v1/portal/cards")
    assert portal.status_code == 200, portal.text
    preview = client_as_a.get(
        f"/api/v1/employees/{EMP_ALICE}/portal-preview/cards"
    )
    assert preview.status_code == 200
    # Guard the guard: this comparison is worthless unless the member actually
    # HOLDS a card. An unmatched employee yields an empty statement, and two
    # empty lists would compare equal no matter how far the surfaces diverged.
    items = portal.json()["items"]
    assert len(items) >= 1, "fixture must produce real coverage"
    assert items[0]["values"]["member_id"] == "2427617201"
    # The broker preview must show exactly what the member sees.
    assert portal.json() == preview.json()


def test_preview_matches_portal_email_fallback(
    client_as_a: TestClient, member_client: TestClient
) -> None:
    """The roster has no email column, so both surfaces must fall back to the
    member's portal-account email — the preview used to print a blank."""
    _clear_assignments(client_as_a)
    _assign(client_as_a, card_name="Email Card", employee_member_id_source="email")

    portal = member_client.get("/api/v1/portal/cards").json()["items"]
    preview = client_as_a.get(
        f"/api/v1/employees/{EMP_ALICE}/portal-preview/cards"
    ).json()["items"]
    assert len(portal) >= 1
    assert portal[0]["values"]["email"] == "alice@cards.test"
    assert portal[0]["values"]["member_id"] == "alice@cards.test"
    assert portal == preview


def test_portal_card_artwork_requires_assignment(
    client_as_a: TestClient, member_client: TestClient
) -> None:
    _clear_assignments(client_as_a)
    assignment = _assign(client_as_a, card_name="Artwork Card")
    card_id = assignment["panel_card_id"]

    served = member_client.get(f"/api/v1/portal/cards/{card_id}/artwork/front")
    assert served.status_code == 200
    assert served.content[:4] == b"\x89PNG"

    # An existing library card that is NOT assigned to the member's year is
    # invisible to them — 404, not 403, so the member surface can't be used to
    # enumerate the card library.
    unassigned = _create_card(client_as_a, "Unassigned Card")
    _upload_front(client_as_a, unassigned["id"])
    blocked = member_client.get(
        f"/api/v1/portal/cards/{unassigned['id']}/artwork/front"
    )
    assert blocked.status_code == 404

    assert (
        member_client.get(f"/api/v1/portal/cards/{card_id}/artwork/side").status_code
        == 404
    )


def test_setup_history_lists_years_with_their_selections(
    client_as_a: TestClient,
) -> None:
    _clear_assignments(client_as_a)
    assignment = _assign(client_as_a, card_name="History Card")

    res = client_as_a.get("/api/v1/panel-setup/history")
    assert res.status_code == 200, res.text
    years = res.json()["years"]
    entry = next(y for y in years if y["policy_year_id"] == PY_A)
    assert entry["year"] == 2031
    assert entry["is_current"] is True
    cards = entry["cards"]
    assert [c["id"] for c in cards] == [assignment["id"]]
    assert cards[0]["product_code"] == "GCGP"
    assert cards[0]["service_labels"] == ["GP"]
    assert cards[0]["remark_keys"] == ["gp"]
    assert cards[0]["special_conditions"] == "Co-pay $5 per visit"

    # Another company's years must never appear in this company's history.
    assert all(y["policy_year_id"] != PY_B for y in years)


def test_setup_history_reflects_withdrawn_cards(client_as_a: TestClient) -> None:
    _clear_assignments(client_as_a)
    res = client_as_a.get("/api/v1/panel-setup/history")
    entry = next(y for y in res.json()["years"] if y["policy_year_id"] == PY_A)
    assert entry["cards"] == []


def test_portal_cards_requires_member_token() -> None:
    with TestClient(app) as anon:
        assert anon.get("/api/v1/portal/cards").status_code == 401
