"""Category/reference filters and historical conversation tenant boundaries."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.auth import CurrentUser, get_current_user
from app.db.session import SessionLocal, get_db
from app.main import app
from app.models import AuditLog, Claim, ClaimMessage, Employee, MemberEnquiry, PolicyYear
from app.models.client import BrokerFirm, Client
from app.services.claim_messages import claim_subject


@pytest.fixture
def queue():
    with SessionLocal() as db:
        db.add(BrokerFirm(id="context-firm", name="Test firm"))
        db.flush()
        for client_id in ("context-client", "context-other"):
            db.add(Client(id=client_id, name=client_id, broker_firm_id="context-firm"))
        db.flush()
        for year_id, client_id, year in (
            ("current", "context-client", 2026),
            ("historic", "context-client", 2025),
            ("foreign", "context-other", 2026),
        ):
            db.add(
                PolicyYear(
                    id=year_id,
                    client_id=client_id,
                    year=year,
                    start_date=date(year, 1, 1),
                    end_date=date(year, 12, 31),
                )
            )
            db.flush()
            db.add(
                Employee(
                    id=f"employee-{year_id}",
                    client_id=client_id,
                    policy_year_id=year_id,
                    staff_id=year_id,
                    employee_name="Case Owner",
                    attribute_values={},
                    derived_attribute_values={},
                    source="csv_import",
                    status="active",
                )
            )
        db.flush()
        for claim_id, year_id, kind, code in (
            ("hospital", "current", "insured", " gHs2 "),
            ("clinic", "current", "insured", "GP"),
            ("wallet", "current", "flex", None),
            ("unmapped", "current", "insured", None),
            ("previous", "historic", "insured", "GHS"),
            ("private", "foreign", "insured", "GHS"),
        ):
            client_id = "context-other" if year_id == "foreign" else "context-client"
            db.add(
                Claim(
                    id=claim_id,
                    client_id=client_id,
                    policy_year_id=year_id,
                    employee_id=f"employee-{year_id}",
                    claim_kind=kind,
                    product_code=code,
                    claim_type="Treatment",
                    reference_no=f"REF-{claim_id}",
                    incurred_date=date(2025 if year_id == "historic" else 2026, 3, 1),
                    amount_claimed=42,
                    status="paid" if year_id == "historic" else "submitted",
                )
            )
            db.flush()
            db.add(
                ClaimMessage(
                    client_id=client_id,
                    claim_id=claim_id,
                    author_type="member",
                    subject="Existing thread",
                    body="Help please",
                )
            )
        db.add(
            MemberEnquiry(
                id="question",
                client_id="context-client",
                policy_year_id="current",
                employee_id="employee-current",
                topic="coverage",
                subject="A question",
                status="open",
            )
        )
        db.flush()
        db.add(
            ClaimMessage(
                client_id="context-client",
                enquiry_id="question",
                author_type="member",
                subject="Question",
                body="Help please",
            )
        )
        db.flush()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="context-user",
            broker_firm_id="context-firm",
            client_id="context-client",
            role="broker_admin",
        )
        try:
            with TestClient(app) as client:
                yield client, db
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            db.rollback()


@pytest.mark.parametrize(
    "category,expected",
    [
        ("inpatient", "hospital"),
        ("outpatient", "clinic"),
        ("flex", "wallet"),
        ("other", "unmapped"),
    ],
)
def test_existing_threads_category_counts_and_labels(queue, category, expected):
    client, db = queue
    response = client.get(
        "/api/v1/conversations",
        params={"policy_year_id": "current", "category": category, "limit": 1},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == data["unread_total"] == 1
    subject = data["items"][0]["subject"]
    assert subject["id"] == expected
    assert subject["claim_category"] == category
    assert subject["reference_no"] == f"REF-{expected}"
    assert subject["policy_year_label"] == "2026-01-01 to 2026-12-31"
    assert claim_subject(db.get(Claim, expected)).claim_category == category


def test_reference_search_is_case_insensitive_literal_and_page_counts_match(queue):
    client, _ = queue
    base = {"policy_year_id": "current", "q": "ref-HOSPITAL"}
    response = client.get("/api/v1/conversations", params=base).json()
    assert response["total"] == 1
    assert response["items"][0]["subject"]["id"] == "hospital"
    assert client.get("/api/v1/conversations", params={**base, "q": "%"}).json()["total"] == 0
    page = client.get("/api/v1/conversations", params={**base, "offset": 1}).json()
    assert page["total"] == page["unread_total"] == 1
    assert page["items"] == []


def test_linked_enquiry_inherits_claim_filters_and_reference_search(queue):
    client, db = queue
    question = db.get(MemberEnquiry, "question")
    question.about_claim_id = "hospital"
    db.flush()
    db.expire(question, ["about_claim"])
    params = {
        "policy_year_id": "current",
        "category": "inpatient",
        "status": "submitted",
        "incurred_from": "2026-03-01",
        "incurred_to": "2026-03-01",
        "q": "ref-HOSPITAL",
    }
    response = client.get("/api/v1/conversations", params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == result["unread_total"] == 2
    assert {item["subject"]["id"] for item in result["items"]} == {"hospital", "question"}
    linked = next(item for item in result["items"] if item["subject"]["kind"] == "enquiry")
    assert linked["subject"]["about_claim"]["claim_category"] == "inpatient"
    question.about_claim_id = "private"
    db.flush()
    db.expire(question, ["about_claim"])
    response = client.get("/api/v1/conversations", params={**params, "q": "ref-private"})
    assert response.json()["total"] == 0


def test_all_years_include_processed_claims_but_never_another_company(queue):
    client, _ = queue
    base = {"policy_year_id": "current"}
    current = client.get("/api/v1/conversations", params=base).json()
    assert current["total"] == 5
    response = client.get("/api/v1/conversations", params={**base, "all_years": "true"}).json()
    assert response["total"] == 6
    assert {r["subject"]["id"] for r in response["items"]} == {
        "hospital",
        "clinic",
        "wallet",
        "unmapped",
        "question",
        "previous",
    }
    historical = client.get(
        "/api/v1/conversations",
        params={
            **base,
            "all_years": "true",
            "status": "paid",
            "incurred_from": "2025-03-01",
            "incurred_to": "2025-03-01",
        },
    ).json()
    assert historical["total"] == 1
    assert historical["items"][0]["subject"]["policy_year_id"] == "historic"
    assert (
        client.get(
            "/api/v1/conversations", params={"policy_year_id": "foreign", "all_years": "true"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"category": "invalid"},
        {"status": "invalid"},
        {"incurred_from": "2026-04-01", "incurred_to": "2026-03-01"},
    ],
)
def test_conversation_filters_are_validated(queue, extra):
    client, _ = queue
    assert (
        client.get(
            "/api/v1/conversations", params={"policy_year_id": "current", **extra}
        ).status_code
        == 422
    )


def test_hr_audit_listing_cannot_read_medical_intake_baselines(queue):
    client, db = queue
    db.add(
        AuditLog(
            client_id="context-client",
            action="claim.intake_suggested",
            entity_type="employee",
            employee_id="employee-current",
            after={"readings": {"diagnosis": "Private medical reading"}},
        )
    )
    db.flush()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="hr-user",
        broker_firm_id="context-firm",
        client_id="context-client",
        role="client_hr",
    )
    response = client.get("/api/v1/audit-log")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    assert "Private medical reading" not in response.text


@pytest.mark.parametrize("role", ["client_hr", "client_admin"])
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/conversations?policy_year_id=current", None),
        ("GET", "/enquiries/question", None),
        ("GET", "/enquiries/question/messages", None),
        ("POST", "/enquiries/question/messages", {"subject": "Test", "body": "Test"}),
        ("POST", "/enquiries/question/messages/read", {}),
        ("POST", "/enquiries/question/status", {"status": "closed"}),
        ("GET", "/audit-log/claim-workload?from_date=2026-01-01&to_date=2026-12-31", None),
    ],
)
def test_client_roles_cannot_access_broker_claim_conversations_or_workload(
    queue, role, method, path, payload
):
    client, db = queue
    before_messages = db.query(ClaimMessage).count()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="client-role-user",
        broker_firm_id="context-firm",
        client_id="context-client",
        role=role,
    )
    response = client.request(method, f"/api/v1{path}", json=payload)
    assert response.status_code == 403, response.text
    assert db.query(ClaimMessage).count() == before_messages
    assert db.get(MemberEnquiry, "question").status == "open"
