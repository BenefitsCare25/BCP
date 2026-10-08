"""Retained policy versions, platform scope, role gates and file integrity."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.v1 import ai_policies
from app.core.auth import (
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    DEMO_USER_ID,
    CurrentUser,
    get_current_user,
)
from app.core.storage import LocalStorage
from app.db.session import SessionLocal
from app.main import app
from app.models import AIPolicyVersion, AuditLog
from scripts.seed_demo import seed

PDF = b"%PDF-1.4\nPolicy document fixture\n%%EOF"


@pytest.fixture()
def ctx(monkeypatch, tmp_path):
    seed()
    with SessionLocal() as db:
        db.execute(delete(AIPolicyVersion))
        db.commit()
    user = CurrentUser(DEMO_USER_ID, DEMO_BROKER_FIRM_ID, DEMO_CLIENT_ID, "system_admin")
    identity = {"user": user}
    app.dependency_overrides[get_current_user] = lambda: identity["user"]
    storage = LocalStorage(tmp_path)
    monkeypatch.setattr(ai_policies, "get_storage", lambda: storage)
    with TestClient(app) as client:
        yield client, identity, storage
    app.dependency_overrides.clear()


def upload(client, **data):
    return client.post("/api/v1/ai-policies", data={"title": "AI usage policy", **data},
                       files={"file": ("policy.pdf", PDF, "application/pdf")})


def test_versions_publish_and_files_survive_new_requests(ctx):
    client, identity, _ = ctx
    first = upload(client, review_due="2027-09-28")
    assert first.status_code == 201, first.text
    v1 = first.json()
    assert v1["version"] == 1 and v1["status"] == "draft"
    assert "storage_path" not in v1 and "sha256" not in v1
    assert client.get(f"/api/v1/ai-policies/{v1['id']}/download").content == PDF
    publish = client.post(f"/api/v1/ai-policies/{v1['id']}/publish")
    assert publish.status_code == 200, publish.text
    assert publish.json()["published_by"] and publish.json()["published_at"]
    second = upload(client, previous_id=v1["id"], title="Cannot rename a saved policy").json()
    assert second["version"] == 2 and second["policy_id"] == v1["policy_id"]
    assert second["title"] == v1["title"]
    assert client.get("/api/v1/ai-policies").json()["items"][0]
    assert client.post(f"/api/v1/ai-policies/{second['id']}/publish").status_code == 200
    current = client.get("/api/v1/ai-policies").json()["items"]
    assert [row["id"] for row in current] == [second["id"]]
    history = client.get("/api/v1/ai-policies?include_archived=true").json()["items"]
    assert len(history) == 2
    old = next(row for row in history if row["id"] == v1["id"])
    assert old["status"] == "archived" and old["archived_at"]
    assert client.post(f"/api/v1/ai-policies/{v1['id']}/publish").status_code == 409
    identity["user"] = replace(identity["user"], role="broker_viewer", client_id="other-company")
    assert client.get("/api/v1/ai-policies").json()["items"][0]["id"] == second["id"]
    download = client.get(f"/api/v1/ai-policies/{v1['id']}/download")
    assert download.content == PDF
    assert download.headers["cache-control"] == "private, no-store"
    assert "attachment" in download.headers["content-disposition"]
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id).where(
            AuditLog.entity_id == second["id"], AuditLog.action == "ai_policy.published",
        ))


@pytest.mark.parametrize("role", ["firm_admin", "broker_admin", "broker_viewer"])
def test_brokers_read_published_versions_but_cannot_modify_or_see_drafts(ctx, role):
    client, identity, _ = ctx
    draft = upload(client).json()
    visible = upload(client, title="Public platform policy").json()
    client.post(f"/api/v1/ai-policies/{visible['id']}/publish")
    identity["user"] = replace(identity["user"], role=role)
    listing = client.get("/api/v1/ai-policies?include_archived=true").json()
    assert [row["id"] for row in listing["items"]] == [visible["id"]]
    assert client.get(f"/api/v1/ai-policies/{draft['id']}/download").status_code == 404
    assert upload(client).status_code == 403
    assert client.post(f"/api/v1/ai-policies/{draft['id']}/publish").status_code == 403
    assert client.post(f"/api/v1/ai-policies/{visible['id']}/archive").status_code == 403


@pytest.mark.parametrize("role", ["client_admin", "client_hr"])
def test_client_roles_cannot_access_the_platform_policy_library(ctx, role):
    client, identity, _ = ctx
    row = upload(client).json()
    client.post(f"/api/v1/ai-policies/{row['id']}/publish")
    identity["user"] = replace(identity["user"], role=role)
    assert client.get("/api/v1/ai-policies").status_code == 403
    assert client.get(f"/api/v1/ai-policies/{row['id']}/download").status_code == 403
    assert upload(client).status_code == 403


def test_invalid_uploads_and_missing_parent_are_rejected(ctx, monkeypatch):
    client, _, _ = ctx
    assert upload(client, title="   ").status_code == 422
    assert upload(client, category="Invalid").status_code == 422
    assert upload(client, previous_id="missing").status_code == 404
    assert client.post("/api/v1/ai-policies", data={"title": "Policy"}, files={
        "file": ("policy.pdf", b"not a PDF", "application/pdf"),
    }).status_code == 415
    assert client.post("/api/v1/ai-policies", data={"title": "Policy"}, files={
        "file": ("policy.html", PDF, "application/pdf"),
    }).status_code == 415
    monkeypatch.setattr(ai_policies, "MAX_POLICY_BYTES", 8)
    assert upload(client).status_code == 413
    assert client.get("/api/v1/ai-policies").json()["items"] == []


def test_integrity_failure_and_missing_file_fail_closed(ctx):
    client, _, storage = ctx
    row = upload(client).json()
    with SessionLocal() as db:
        path = db.get(AIPolicyVersion, row["id"]).storage_path
    storage._full(path).write_bytes(b"tampered")
    assert client.get(f"/api/v1/ai-policies/{row['id']}/download").status_code == 409
    storage.delete(path)
    assert client.get(f"/api/v1/ai-policies/{row['id']}/download").status_code == 503


def test_archive_retains_file_and_never_exposes_unpublished_drafts(ctx):
    client, identity, _ = ctx
    row = upload(client).json()
    assert client.post(f"/api/v1/ai-policies/{row['id']}/archive").status_code == 200
    assert client.get(f"/api/v1/ai-policies/{row['id']}/download").content == PDF
    assert client.get("/api/v1/ai-policies").json()["items"] == []
    identity["user"] = replace(identity["user"], role="broker_viewer")
    assert client.get("/api/v1/ai-policies?include_archived=true").json()["items"] == []
    assert client.get(f"/api/v1/ai-policies/{row['id']}/download").status_code == 404


def test_failed_storage_save_leaves_no_record_or_partial_file(ctx, monkeypatch):
    client, _, storage = ctx
    original = storage.save

    def fail(stream, path):
        original(stream, path)
        raise OSError("unavailable")

    monkeypatch.setattr(storage, "save", fail)
    assert upload(client).status_code == 503
    assert client.get("/api/v1/ai-policies").json()["items"] == []
    assert list(storage.root.rglob("*.pdf")) == []
