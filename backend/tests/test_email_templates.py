"""Template validation, tenant isolation and non-delivering real-data preparation."""

from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.email_templates import router
from app.core.auth import CurrentUser, get_current_user
from app.core.settings import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    AuthCredential,
    BrokerFirm,
    Client,
    Employee,
    MemberAccount,
    PolicyYear,
    User,
    UserClientAccess,
)
from app.models.email_template import EmailPreparation, EmailTemplateVersion
from app.schemas.email_templates import BrandingContent, TemplateContent
from app.services.email_template_content import render, valid_email, validate_content

BASE = {
    "title": "Benefits briefing",
    "audience": "employee",
    "purpose": "general",
    "subject": "News from {{company_name}}",
    "body": "Hello {{recipient_name}},\n\nYour staff ID is {{staff_id}}.",
}


@pytest.fixture
def setup():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([BrokerFirm(id="f1", name="Firm 1"), BrokerFirm(id="f2", name="Firm 2")])
        db.flush()
        db.add_all(
            [
                Client(
                    id="c1",
                    name="ACME",
                    legal_name="ACME Pte Ltd",
                    broker_firm_id="f1",
                    slug="acme",
                ),
                Client(id="c2", name="Other", broker_firm_id="f2", slug="other"),
                Client(id="c3", name="Sibling", broker_firm_id="f1", slug="sibling"),
                User(
                    id="operator",
                    email="operator@example.invalid",
                    role="broker_admin",
                    broker_firm_id="f1",
                ),
            ]
        )
        db.flush()
        for ident, client in (("year", "c1"), ("other-year", "c2"), ("older-year", "c1")):
            db.add(
                PolicyYear(
                    id=ident,
                    client_id=client,
                    year=2026,
                    start_date=date(2025 if ident == "older-year" else 2026, 1, 1),
                    end_date=date(2026, 12, 31),
                )
            )
        db.flush()
        for ident, name, email, state in (
            ("alice", "Alice Example", "alice@example.invalid", "active"),
            ("bob", "Bob Example", "bob@example.invalid", "active"),
            ("shared1", "Shared One", "shared@example.invalid", "active"),
            ("shared2", "Shared Two", "shared@example.invalid", "active"),
            ("missing", "Missing Email", "", "active"),
            ("leaver", "Leaver Example", "leaver@example.invalid", "terminated"),
        ):
            db.add(
                Employee(
                    id=ident,
                    client_id="c1",
                    policy_year_id="year",
                    staff_id=ident,
                    employee_name=name,
                    attribute_values={"email": email},
                    status=state,
                )
            )
        db.add(
            Employee(
                id="outside",
                client_id="c2",
                policy_year_id="other-year",
                staff_id="outside",
                employee_name="Other Company Person",
                attribute_values={"email": "outside@example.invalid"},
            )
        )
        db.add(
            MemberAccount(
                id="a1",
                client_id="c1",
                staff_id="alice",
                email="alice@example.invalid",
                system_login_id="EM-ALICE",
                status="invited",
            )
        )
        db.commit()
    actors = [
        CurrentUser(user_id="operator", broker_firm_id="f1", client_id="c1", role="broker_admin")
    ]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: actors[0]

    def session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client, factory, actors
    engine.dispose()


def create(client, content=None, scope="company"):
    response = client.post(f"/email-templates?scope={scope}", json={"content": content or BASE})
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, row, scope="company"):
    response = client.post(
        f"/email-templates/{row['key']}/publish?scope={scope}", json={"revision": row["revision"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_custom_templates_and_immutable_versions(setup):
    client, factory, _ = setup
    row = publish(client, create(client))
    first_version = row["published_version"]
    changed = client.put(
        f"/email-templates/{row['key']}",
        json={
            "content": BASE | {"title": "Renamed", "body": "New copy"},
            "revision": row["revision"],
        },
    ).json()
    assert changed["published_content"]["body"] == BASE["body"]
    assert changed["has_changes"]
    published = publish(client, changed)
    assert published["published_version"] != first_version
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(EmailTemplateVersion)) == 2
    stale = client.put(f"/email-templates/{row['key']}", json={"content": BASE, "revision": 1})
    assert stale.status_code == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "Hello {{unknown}}"),
        ("subject", "Hello {recipient_name}"),
        ("body", "{{password}}"),
        ("body", "{{activation_url}}"),
        ("subject", "Hello\r\nBcc: other@example.invalid"),
        ("body", "<script>alert(1)</script>"),
        ("body", "[Open](javascript:alert(1))"),
        ("body", "{{hr_role_label}}"),
        ("body", "{{recipient_name.__class__}}"),
        ("body", "[Open](https://example.invalid/{{recipient_email}})"),
    ],
)
def test_invalid_content_cannot_publish(setup, field, value):
    client, _, _ = setup
    row = create(client, BASE | {field: value})
    response = client.post(f"/email-templates/{row['key']}/publish", json={"revision": 1})
    assert response.status_code == 422
    assert field in response.json()["detail"]["fields"]


def test_real_preview_is_read_only_and_escapes_values(setup):
    client, factory, _ = setup
    with factory() as db:
        employee = db.get(Employee, "alice")
        employee.employee_name = "Alice <script>bad()</script> **admin**"
        db.commit()
    catalog = client.get("/email-templates").json()
    content = next(row for row in catalog["items"] if row["key"] == "employee_invitation")[
        "content"
    ]
    response = client.post(
        "/email-templates/preview",
        json={"content": content, "recipient_id": "alice", "policy_year_id": "year"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["data_source"] == "real"
    assert data["values"]["company_name"] == "ACME Pte Ltd"
    assert data["recipient_email"] == "alice@example.invalid"
    assert "<script>bad" not in data["html"]
    assert "&lt;script&gt;" in data["html"]
    assert "<strong>admin</strong>" not in data["html"]
    assert not data["activation_link_generated"]
    assert "token=" not in data["html"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(MemberAccount)) == 1
        account = db.get(MemberAccount, "a1")
        assert account.password_hash is None and account.invite_sent_at is None


def test_cross_company_preview_and_roster_are_rejected(setup):
    client, _, actors = setup
    assert (
        client.post(
            "/email-templates/preview",
            json={"content": BASE, "recipient_id": "outside", "policy_year_id": "year"},
        ).status_code
        == 404
    )
    assert client.get("/email-templates/recipients?policy_year_id=other-year").status_code == 404
    private = create(client)
    actors[0] = replace(actors[0], broker_firm_id="f2", client_id="c2")
    assert private["key"] not in str(client.get("/email-templates").json())
    assert (
        client.put(
            f"/email-templates/{private['key']}", json={"content": BASE, "revision": 1}
        ).status_code
        == 404
    )


def test_company_override_and_draft_fallback(setup):
    client, _, actors = setup
    default = publish(client, create(client, scope="firm"), scope="firm")
    override = client.put(
        f"/email-templates/{default['key']}",
        json={"content": BASE | {"subject": "Company-specific"}, "revision": 0},
    ).json()
    assert override["published_content"]["subject"] == BASE["subject"]
    publish(client, override)
    actors[0] = replace(actors[0], client_id="c3")
    sibling = next(
        row
        for row in client.get("/email-templates").json()["items"]
        if row["key"] == default["key"]
    )
    assert sibling["published_content"]["subject"] == BASE["subject"]


def test_recipient_eligibility_checks_across_years(setup):
    client, factory, _ = setup
    with factory() as db:
        db.add(
            Employee(
                id="old",
                client_id="c1",
                policy_year_id="older-year",
                staff_id="colleague",
                employee_name="Colleague",
                attribute_values={"email": "bob@example.invalid"},
            )
        )
        db.commit()
    response = client.get("/email-templates/recipients?policy_year_id=year&limit=2")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert response.json()["eligible_ids"] == ["alice"]
    all_rows = client.get("/email-templates/recipients?policy_year_id=year").json()["items"]
    assert "shared" in next(row for row in all_rows if row["id"] == "bob")["reason"]


def test_preparation_is_idempotent_and_never_queues_or_provisions(setup):
    client, factory, _ = setup
    row = publish(client, create(client))
    body = {
        "template_key": row["key"],
        "policy_year_id": "year",
        "recipient_ids": ["alice", "bob", "missing"],
    }
    review = client.post("/email-templates/review", json=body).json()
    assert review["eligible_count"] == 2 and review["excluded_count"] == 1
    request = body | {"request_key": str(uuid4()), "review_token": review["review_token"]}
    first = client.post("/email-templates/prepare", json=request)
    assert first.status_code == 201, first.text
    second = client.post("/email-templates/prepare", json=request)
    assert second.status_code == 201 and first.json()["id"] == second.json()["id"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(EmailPreparation)) == 1
        assert db.scalar(select(func.count()).select_from(MemberAccount)) == 1
        saved = db.scalar(select(EmailPreparation))
        assert saved.status == "prepared"
        assert {r["id"] for r in saved.recipients} == {"alice", "bob"}
        assert "review_token" not in saved.snapshot
    assert client.post("/email-templates/send", json=request).status_code == 503
    assert client.post("/email-templates/test", json={}).status_code == 503


def test_prepare_rechecks_recipient_and_template_version(setup):
    client, factory, _ = setup
    row = publish(client, create(client))
    body = {"template_key": row["key"], "policy_year_id": "year", "recipient_ids": ["alice"]}
    review = client.post("/email-templates/review", json=body).json()
    with factory() as db:
        employee = db.get(Employee, "alice")
        employee.attribute_values = {"email": "changed@example.invalid"}
        db.commit()
    response = client.post(
        "/email-templates/prepare",
        json=body | {"request_key": str(uuid4()), "review_token": review["review_token"]},
    )
    assert response.status_code == 409


def test_viewer_preview_allowed_mutation_and_hr_identity_denied(setup):
    client, _, actors = setup
    actors[0] = replace(actors[0], role="broker_viewer")
    assert client.get("/email-templates").status_code == 200
    assert client.post("/email-templates/preview", json={"content": BASE}).status_code == 200
    assert client.post("/email-templates", json={"content": BASE}).status_code == 403
    assert client.get("/email-templates/recipients?audience=hr").status_code == 403
    actors[0] = replace(actors[0], role="client_hr")
    assert client.get("/email-templates").status_code == 403


def test_removal_is_system_admin_only_and_restores_default(setup):
    client, _, actors = setup
    key = "employee_invitation"
    content = next(
        row for row in client.get("/email-templates").json()["items"] if row["key"] == key
    )["content"]
    client.put(
        f"/email-templates/{key}",
        json={"content": content | {"title": "Custom title"}, "revision": 0},
    )
    assert client.delete(f"/email-templates/{key}").status_code == 403
    actors[0] = replace(actors[0], role="system_admin")
    assert client.delete(f"/email-templates/{key}").status_code == 204
    restored = next(
        row for row in client.get("/email-templates").json()["items"] if row["key"] == key
    )
    assert restored["source"] == "builtin" and restored["revision"] == 0


def test_branding_validation_and_conflict(setup):
    client, _, _ = setup
    content = BrandingContent().model_dump()
    assert (
        client.put(
            "/email-templates/branding", json={"content": content | {"support_email": "bad"}}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/email-templates/branding",
            json={"content": content | {"logo_url": "javascript:evil()"}},
        ).status_code
        == 422
    )
    assert client.put("/email-templates/branding", json={"content": content}).status_code == 200
    assert (
        client.put(
            "/email-templates/branding", json={"content": content, "revision": 0}
        ).status_code
        == 409
    )


def test_safe_formatted_content_and_button_pair():
    content = TemplateContent(
        **(
            BASE
            | {
                "body": "**Notice**\n\n- Hello {{recipient_name}}\n- [Open](https://example.invalid)"
            }
        )
    )
    output = render(content, BrandingContent(), {"recipient_name": "<img src=x onerror=bad>"})
    assert "<strong>Notice</strong>" in output["html"]
    assert "<ul>" in output["html"] and 'href="https://example.invalid"' in output["html"]
    assert "<img src=x" not in output["html"]
    assert "button_label" in validate_content(content.model_copy(update={"button_label": "Open"}))


def test_resolved_data_validation_blocks_missing_id_and_header_injection(setup):
    client, factory, _ = setup
    content = BASE | {
        "subject": "Hello {{recipient_name}}",
        "body": "Sign in as {{login_identifier}}",
    }
    response = client.post(
        "/email-templates/preview",
        json={"content": content, "recipient_id": "bob", "policy_year_id": "year"},
    ).json()
    assert not response["valid"] and "login_identifier" in response["errors"]["body"]
    with factory() as db:
        db.get(Employee, "alice").employee_name = "Alice\r\nBcc: victim@example.invalid"
        db.commit()
    row = publish(client, create(client, content))
    response = client.post(
        "/email-templates/preview",
        json={"content": content, "recipient_id": "alice", "policy_year_id": "year"},
    ).json()
    assert not response["valid"] and "single line" in response["errors"]["subject"]
    review = client.post(
        "/email-templates/review",
        json={
            "template_key": row["key"],
            "policy_year_id": "year",
            "recipient_ids": ["alice", "bob"],
        },
    ).json()
    assert review["eligible_count"] == 0


def test_saved_preparation_is_scoped_and_keeps_published_content(setup):
    client, _, actors = setup
    row = publish(client, create(client))
    body = {"template_key": row["key"], "policy_year_id": "year", "recipient_ids": ["bob"]}
    review = client.post("/email-templates/review", json=body).json()
    request = body | {"request_key": str(uuid4()), "review_token": review["review_token"]}
    saved = client.post("/email-templates/prepare", json=request).json()
    changed = client.put(
        f"/email-templates/{row['key']}",
        json={"content": BASE | {"body": "Replacement"}, "revision": row["revision"]},
    ).json()
    publish(client, changed)
    detail = client.get(f"/email-templates/preparations/{saved['id']}").json()
    assert detail["content"]["body"] == BASE["body"]
    assert detail["recipients"] == [{"id": "bob", "email": "bob@example.invalid"}]
    assert (
        client.post(
            "/email-templates/prepare", json=request | {"request_key": str(uuid4())}
        ).status_code
        == 409
    )
    actors[0] = replace(actors[0], client_id="c3")
    assert client.get(f"/email-templates/preparations/{saved['id']}").status_code == 404


def test_hr_real_preview_and_preparation_do_not_change_credentials(setup):
    client, factory, actors = setup
    with factory() as db:
        db.add(
            User(
                id="hr",
                role="client_hr",
                status="invited",
                email="hr@example.invalid",
                display_name="HR Example",
                broker_firm_id="f1",
            )
        )
        db.flush()
        db.add_all(
            [
                AuthCredential(
                    id="cred",
                    user_id="hr",
                    broker_firm_id="f1",
                    hr_login_id="HR-TEST",
                    password_hash="unchanged-test-hash",
                ),
                UserClientAccess(user_id="hr", client_id="c1"),
            ]
        )
        db.commit()
    content = next(
        row
        for row in client.get("/email-templates").json()["items"]
        if row["key"] == "hr_invitation"
    )["content"]
    content["body"] += "\n\nVisit {{portal_url}}."
    row = publish(client, create(client, content))
    preview = client.post(
        "/email-templates/preview", json={"content": content, "recipient_id": "hr"}
    ).json()
    assert preview["values"]["recipient_name"] == "HR Example"
    assert preview["values"]["hr_role_label"] == "HR Officer"
    assert "/hr/sign-in" in preview["values"]["portal_url"]
    body = {"template_key": row["key"], "recipient_ids": ["hr"]}
    review = client.post("/email-templates/review", json=body).json()
    assert review["eligible_count"] == 1
    saved = client.post(
        "/email-templates/prepare",
        json=body | {"request_key": str(uuid4()), "review_token": review["review_token"]},
    ).json()
    sample = client.post("/email-templates/preview", json={"content": content}).json()
    detail = client.get(f"/email-templates/preparations/{saved['id']}").json()
    assert detail["preview"] == {
        key: sample[key] for key in ("subject", "preheader", "html", "text")
    }
    with factory() as db:
        assert db.get(AuthCredential, "cred").password_hash == "unchanged-test-hash"
        assert db.get(User, "hr").status == "invited"
    actors[0] = replace(actors[0], role="broker_viewer")
    assert client.get(f"/email-templates/preparations/{saved['id']}").status_code == 403


@pytest.mark.parametrize("tenant_mode", ["header", "subdomain"])
def test_saved_preview_retains_company_values_after_company_changes(
    setup, monkeypatch, tenant_mode
):
    client, factory, _ = setup
    settings = replace(
        get_settings(),
        tenant_mode=tenant_mode,
        base_domain="benefits.example.com",
        frontend_origin="https://benefits.example.com",
    )
    # Company links resolve through the firm's public origin (tenant_resolution).
    monkeypatch.setattr("app.core.tenant_resolution.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.member_invite.get_settings", lambda: settings)
    content = BASE | {
        "body": "Hello {{recipient_name}} at {{company_name}}. Visit {{portal_url}}.",
        "button_label": "Open portal",
        "button_url": "{{portal_url}}",
    }
    row = publish(client, create(client, content))
    preview = client.post("/email-templates/preview", json={"content": content}).json()
    assert "acme" in preview["values"]["portal_url"]
    selection = {"template_key": row["key"], "policy_year_id": "year", "recipient_ids": ["bob"]}
    review = client.post("/email-templates/review", json=selection).json()
    request = selection | {"request_key": str(uuid4()), "review_token": review["review_token"]}
    response = client.post("/email-templates/prepare", json=request)
    assert response.status_code == 201, response.text
    saved_id = response.json()["id"]
    with factory() as db:
        company = db.get(Client, "c1")
        company.legal_name, company.slug = "Changed company", "changed"
        db.commit()
    detail = client.get(f"/email-templates/preparations/{saved_id}").json()
    assert detail["preview"] == {
        key: preview[key] for key in ("subject", "preheader", "html", "text")
    }
    assert "Alex Tan (sample)" in detail["preview"]["text"]
    assert "Bob Example" not in detail["preview"]["text"]
    assert client.post("/email-templates/prepare", json=request).status_code == 409


def test_legacy_saved_preview_uses_company_portal_and_saved_name(setup):
    client, factory, _ = setup
    content = BASE | {
        "body": "Visit {{portal_url}} for {{company_name}}.",
        "button_label": "Open portal",
        "button_url": "{{portal_url}}",
    }
    row = publish(client, create(client, content))
    selection = {"template_key": row["key"], "policy_year_id": "year", "recipient_ids": ["bob"]}
    review = client.post("/email-templates/review", json=selection).json()
    saved_id = client.post(
        "/email-templates/prepare",
        json=selection | {"request_key": str(uuid4()), "review_token": review["review_token"]},
    ).json()["id"]
    with factory() as db:
        saved = db.get(EmailPreparation, saved_id)
        saved.snapshot = {
            key: value for key, value in saved.snapshot.items() if key != "company_values"
        }
        db.get(Client, "c1").legal_name = "Changed company"
        db.commit()
    preview = client.post("/email-templates/preview", json={"content": content}).json()
    detail = client.get(f"/email-templates/preparations/{saved_id}").json()
    assert preview["values"]["portal_url"] in detail["preview"]["text"]
    assert "ACME Pte Ltd" in detail["preview"]["text"]
    assert "Changed company" not in detail["preview"]["text"]
    assert "https://example.invalid" not in detail["preview"]["html"]


def test_portal_address_change_requires_recipient_review_again(setup):
    client, factory, _ = setup
    row = publish(client, create(client, BASE | {"body": "Visit {{portal_url}}."}))
    selection = {"template_key": row["key"], "policy_year_id": "year", "recipient_ids": ["bob"]}
    review = client.post("/email-templates/review", json=selection).json()
    with factory() as db:
        db.get(Client, "c1").slug = "changed"
        db.commit()
    response = client.post(
        "/email-templates/prepare",
        json=selection | {"request_key": str(uuid4()), "review_token": review["review_token"]},
    )
    assert response.status_code == 409


def test_tampered_review_and_branding_reset_permissions(setup):
    client, _, actors = setup
    row = publish(client, create(client))
    body = {"template_key": row["key"], "policy_year_id": "year", "recipient_ids": ["alice"]}
    assert (
        client.post(
            "/email-templates/prepare",
            json=body | {"request_key": str(uuid4()), "review_token": "tampered"},
        ).status_code
        == 409
    )
    branding = BrandingContent().model_dump()
    assert (
        client.put(
            "/email-templates/branding?scope=firm",
            json={"content": branding | {"sender_display_name": "Firm"}},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/email-templates/branding",
            json={"content": branding | {"sender_display_name": "Company"}},
        ).status_code
        == 200
    )
    assert client.delete("/email-templates/branding").status_code == 403
    actors[0] = replace(actors[0], role="system_admin")
    assert client.delete("/email-templates/branding").status_code == 204
    assert (
        client.get("/email-templates/branding").json()["content"]["sender_display_name"] == "Firm"
    )


@pytest.mark.parametrize(
    "email",
    [
        ".alex@example.com",
        "alex.@example.com",
        "alex@a.-bad.com",
        "alex@a..com",
        "a" * 65 + "@example.com",
    ],
)
def test_invalid_addresses_are_not_eligible(email):
    assert not valid_email(email)


def test_valid_addresses_allow_plus_and_subdomains():
    assert valid_email("alex+benefits@hr.example.com")


def test_formatting_wraps_placeholders_without_interpreting_recipient_text():
    content = TemplateContent(
        title="Preview",
        subject="Hello",
        preheader="For {{recipient_name}}",
        body="**{{recipient_name}}**\n\n*{{staff_id}}*",
    )
    output = render(
        content, BrandingContent(), {"recipient_name": "**Admin** <script>", "staff_id": "S1"}
    )
    assert "<strong>**Admin** &lt;script&gt;</strong>" in output["html"]
    assert "<em>S1</em>" in output["html"]
    assert "<strong>Admin</strong>" not in output["html"]
    assert output["text"].startswith("**Admin** <script>\n\nS1")


def test_unsaved_branding_follows_the_resolved_brand(setup):
    from app.models.brand import BrandProfile

    client, factory, _ = setup
    builtin = client.get("/email-templates/branding").json()
    assert builtin["source"] == "builtin"
    assert builtin["content"] == BrandingContent().model_dump()
    with factory() as db:
        db.add_all([
            BrandProfile(broker_firm_id="f1", scope_key="firm",
                         email_sender_name="Firm One Benefits", support_email="help@f1.test"),
            BrandProfile(broker_firm_id="f1", client_id="c1", scope_key="c1",
                         email_sender_name="ACME Benefits"),
            BrandProfile(broker_firm_id="f2", scope_key="firm",
                         email_sender_name="Other Firm", support_email="help@f2.test"),
        ])
        db.commit()
    company = client.get("/email-templates/branding").json()["content"]
    assert (company["sender_display_name"], company["support_email"]) == (
        "ACME Benefits", "help@f1.test",
    )
    firm = client.get("/email-templates/branding?scope=firm").json()["content"]
    assert firm["sender_display_name"] == "Firm One Benefits"
    # A save that omits a field takes it from the brand; the stored row then wins.
    saved = client.put("/email-templates/branding", json={"content": {"footer": "Regards"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["content"] == {
        "sender_display_name": "ACME Benefits", "support_email": "help@f1.test",
        "footer": "Regards", "logo_url": "",
    }
    with factory() as db:
        row = db.query(BrandProfile).filter_by(scope_key="c1").one()
        row.email_sender_name = "Renamed"
        db.commit()
    stored = client.get("/email-templates/branding").json()["content"]
    assert stored["sender_display_name"] == "ACME Benefits"
