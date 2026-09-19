"""WICA is broker-owned, tenant-isolated, and never a portal claim."""

import asyncio
from dataclasses import replace
from datetime import date
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user
from app.core.storage import LocalStorage
from app.db.session import SessionLocal
from app.main import app
from app.models import AuditLog, Claim, ClaimNotification, Client


@pytest.fixture()
def ctx(monkeypatch, tmp_path):
    from app.api.v1 import wica
    from scripts.seed_demo import seed

    seed()
    ids = [str(uuid4()), str(uuid4())]
    with SessionLocal() as db:
        for i in ids:
            db.add(
                Client(id=i, name="WICA test", slug=f"wica-{i}", broker_firm_id=DEMO_BROKER_FIRM_ID)
            )
        db.commit()
    user = CurrentUser(
        user_id="wica-broker",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=ids[0],
        role="broker_admin",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(wica, "get_storage", lambda: LocalStorage(tmp_path))
    monkeypatch.setattr(wica, "today", lambda: date(2026, 9, 19))
    with TestClient(app) as client:
        yield client, user, ids
    app.dependency_overrides.clear()


def setup(client):
    period = {
        "id": str(uuid4()),
        "label": "2026",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "grace_days": None,
    }
    res = client.put(
        "/api/v1/wica/settings", json={"revision": 0, "enabled": True, "periods": [period]}
    )
    assert res.status_code == 200, res.text
    return period


@pytest.mark.parametrize("with_period", [False, True])
def test_delete_company_cleans_unused_wica_configuration(ctx, with_period):
    from app.models import WicaPeriod, WicaSettings

    client, _, ids = ctx
    if with_period:
        setup(client)
    else:
        response = client.put(
            "/api/v1/wica/settings", json={"revision": 0, "enabled": False, "periods": []}
        )
        assert response.status_code == 200
    response = client.delete(f"/api/v1/admin/clients/{ids[0]}")
    assert response.status_code == 204, response.text
    with SessionLocal() as db:
        assert db.get(Client, ids[0]) is None
        assert db.get(WicaSettings, ids[0]) is None
        assert db.scalar(select(WicaPeriod.id).where(WicaPeriod.client_id == ids[0])) is None
        assert db.get(Client, ids[1]) is not None


def test_delete_company_refuses_retained_wica_incident(ctx):
    from app.models import WicaIncident, WicaSettings

    client, _, ids = ctx
    row, _ = new_incident(client)
    response = client.delete(f"/api/v1/admin/clients/{ids[0]}")
    assert response.status_code == 409, response.text
    assert "retained WICA incidents" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.get(Client, ids[0]) is not None
        assert db.get(WicaIncident, row["id"]) is not None
        assert db.get(WicaSettings, ids[0]) is not None


def test_upload_blocking_work_never_runs_on_event_loop(ctx, monkeypatch):
    from app.api.v1 import wica

    client, _, _ = ctx
    row, _ = new_incident(client)
    stages = []

    def off_loop(name, original):
        def checked(*args, **kwargs):
            with pytest.raises(RuntimeError, match="no running event loop"):
                asyncio.get_running_loop()
            stages.append(name)
            return original(*args, **kwargs)

        return checked

    for name in ("incident", "enabled", "advance", "detail"):
        monkeypatch.setattr(wica.svc, name, off_loop(name, getattr(wica.svc, name)))
    monkeypatch.setattr(
        wica, "scan_quarantined_document", off_loop("scan", wica.scan_quarantined_document)
    )
    storage = wica.get_storage()
    monkeypatch.setattr(storage, "save", off_loop("save", storage.save))
    monkeypatch.setattr(wica, "get_storage", lambda: storage)
    result = upload(client, row)
    assert len(result["documents"]) == 1
    assert {"incident", "enabled", "advance", "detail", "scan", "save"}.issubset(stages)


def new_incident(client, period=None):
    period = period or setup(client)
    body = {
        "id": str(uuid4()),
        "period_id": period["id"],
        "employee_name": "Test Employee",
        "staff_id": "W001",
        "incident_date": "2026-08-10",
    }
    res = client.post("/api/v1/wica/incidents", json=body)
    assert res.status_code == 201, res.text
    return res.json(), body


def upload(client, row, name="medical.pdf", content=b"%PDF-1.4 test"):
    response = client.post(
        f"/api/v1/wica/incidents/{row['id']}/documents",
        data={"revision": row["revision"], "document_id": str(uuid4())},
        files={"file": (name, content, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def tag(client, row, doc=None, benefit="Medical", related=None):
    doc = doc or row["documents"][-1]
    response = client.patch(
        f"/api/v1/wica/incidents/{row['id']}/documents/{doc['id']}",
        json={
            "revision": row["revision"],
            "doc_type": "Medical Bill",
            "document_date": "2026-08-10",
            "benefit_type": benefit,
            "related_ids": related or [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def action(client, row, document_id, target, **extra):
    return client.post(
        f"/api/v1/wica/incidents/{row['id']}/documents/{document_id}/status",
        json={"revision": row["revision"], "status": target, **extra},
    )


def test_full_broker_flow_pack_snapshot_and_portal_separation(ctx):
    client, _, _ = ctx
    with SessionLocal() as db:
        claims_before = db.scalar(select(func.count()).select_from(Claim))
        notifications_before = db.scalar(select(func.count()).select_from(ClaimNotification))
    row, _ = new_incident(client)
    row = upload(client, row, "support.pdf")
    row = tag(client, row, benefit="Others")
    support = row["documents"][0]
    assert support["claim_id"] is None and support["status"] == "supporting"
    row = upload(client, row)
    claim_doc = next(d for d in row["documents"] if d["file_name"] == "medical.pdf")
    row = tag(client, row, claim_doc, related=[support["id"]])
    doc = next(d for d in row["documents"] if d["id"] == claim_doc["id"])
    claim_id = doc["claim_id"]
    assert claim_id.startswith("WICA-")
    row = tag(client, row, doc, related=[support["id"]])
    assert next(d for d in row["documents"] if d["id"] == doc["id"])["claim_id"] == claim_id
    base = f"/api/v1/wica/incidents/{row['id']}"
    pack_body = {"id": str(uuid4()), "revision": row["revision"], "document_ids": [doc["id"]]}
    assert client.post(base + "/packs", json=pack_body).status_code == 409
    response = action(client, row, doc["id"], "pending_insurer")
    assert response.status_code == 200, response.text
    row = response.json()
    pack_body["revision"] = row["revision"]
    response = client.post(base + "/packs", json=pack_body)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["packs"][0]["document_count"] == 2
    download = client.get(base + f"/packs/{pack_body['id']}/download")
    assert download.status_code == 200 and download.headers["cache-control"] == "no-store"
    with ZipFile(BytesIO(download.content)) as archive:
        assert len(archive.namelist()) == 3
        assert "manifest.json" in archive.namelist()
    sent = client.post(
        base + f"/packs/{pack_body['id']}/sent",
        json={
            "revision": row["revision"],
            "sent_on": "2026-08-11",
            "sent_reference": "Insurer external portal ref TEST",
        },
    )
    assert sent.status_code == 200
    row = sent.json()
    settled = action(
        client, row, doc["id"], "settled", settlement_amount="123.45", settlement_date="2026-09-01"
    )
    assert settled.status_code == 200, settled.text
    row = settled.json()
    assert action(client, row, doc["id"], "submitted").status_code == 409
    assert client.get(base + f"/packs/{pack_body['id']}/download").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Claim)) == claims_before
        assert (
            db.scalar(select(func.count()).select_from(ClaimNotification)) == notifications_before
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "wica.pack_downloaded")
            )
            >= 2
        )


@pytest.mark.parametrize("role", ["hr_admin", "hr_viewer"])
def test_hr_cannot_access_wica(ctx, role):
    client, user, _ = ctx
    app.dependency_overrides[get_current_user] = lambda: replace(user, role=role)
    assert client.get("/api/v1/wica/settings").status_code == 403
    assert client.get("/api/v1/wica/incidents").status_code == 403


def test_viewer_cannot_write(ctx):
    client, user, _ = ctx
    row, body = new_incident(client)
    app.dependency_overrides[get_current_user] = lambda: replace(user, role="broker_viewer")
    assert client.get("/api/v1/wica/incidents").status_code == 200
    assert client.post("/api/v1/wica/incidents", json=body).status_code == 403
    assert client.post(f"/api/v1/wica/incidents/{row['id']}/packs", json={}).status_code == 403


@pytest.mark.parametrize("role", ["broker_admin", "system_admin"])
def test_cross_company_scoped_even_for_system_admin(ctx, role):
    client, user, ids = ctx
    row, _ = new_incident(client)
    row = upload(client, row)
    doc = row["documents"][0]
    app.dependency_overrides[get_current_user] = lambda: replace(user, client_id=ids[1], role=role)
    assert client.get("/api/v1/wica/incidents").json()["items"] == []
    base = f"/api/v1/wica/incidents/{row['id']}"
    assert client.get(base).status_code == 404
    assert client.get(base + f"/documents/{doc['id']}/download").status_code == 404
    assert (
        client.post(
            base + "/packs", json={"id": str(uuid4()), "revision": 2, "document_ids": [doc["id"]]}
        ).status_code
        == 404
    )


def test_stale_writes_and_related_document_scope(ctx):
    client, _, _ = ctx
    period = setup(client)
    row, _ = new_incident(client, period)
    other, _ = new_incident(client, period)
    other = tag(client, upload(client, other), benefit="Others")
    row = upload(client, row)
    base = f"/api/v1/wica/incidents/{row['id']}"
    doc = row["documents"][0]
    payload = {
        "revision": 1,
        "doc_type": "Medical Bill",
        "document_date": "2026-08-10",
        "benefit_type": "Medical",
    }
    assert client.patch(base + f"/documents/{doc['id']}", json=payload).status_code == 409
    payload.update(revision=row["revision"], related_ids=[other["documents"][0]["id"]])
    assert client.patch(base + f"/documents/{doc['id']}", json=payload).status_code == 404
    assert client.get(base).json()["revision"] == row["revision"]


def test_settings_dates_lock_and_create_retry(ctx):
    client, _, _ = ctx
    period = setup(client)
    row, body = new_incident(client, period)
    assert client.post("/api/v1/wica/incidents", json=body).json()["id"] == row["id"]
    body["staff_id"] = "changed"
    assert client.post("/api/v1/wica/incidents", json=body).status_code == 409
    changed = {**period, "label": "changed"}
    assert (
        client.put(
            "/api/v1/wica/settings", json={"revision": 1, "enabled": True, "periods": [changed]}
        ).status_code
        == 409
    )
    assert (
        client.put(
            "/api/v1/wica/settings", json={"revision": 0, "enabled": True, "periods": [period]}
        ).status_code
        == 409
    )
    overlap = {**period, "id": str(uuid4())}
    assert (
        client.put(
            "/api/v1/wica/settings",
            json={"revision": 1, "enabled": True, "periods": [period, overlap]},
        ).status_code
        == 422
    )
    body.update(id=str(uuid4()), incident_date="2027-01-01")
    assert client.post("/api/v1/wica/incidents", json=body).status_code == 422


def test_upload_signature_scanner_failure_and_removal(ctx, monkeypatch):
    from fastapi import HTTPException

    from app.api.v1 import wica

    client, _, _ = ctx
    row, _ = new_incident(client)
    base = f"/api/v1/wica/incidents/{row['id']}"
    response = client.post(
        base + "/documents",
        data={"revision": 1, "document_id": str(uuid4())},
        files={"file": ("fake.pdf", b"<script>bad</script>")},
    )
    assert response.status_code == 415

    def unavailable(path):
        raise HTTPException(503, "Scanner unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(wica, "scan_quarantined_document", unavailable)
        response = client.post(
            base + "/documents",
            data={"revision": 1, "document_id": str(uuid4())},
            files={"file": ("file.pdf", b"%PDF-1.4")},
        )
        assert response.status_code == 503
    row = upload(client, row)
    doc = row["documents"][0]
    assert client.get(base + f"/documents/{doc['id']}/download").status_code == 200
    response = client.post(
        base + f"/documents/{doc['id']}/remove", json={"revision": row["revision"]}
    )
    assert response.status_code == 200 and response.json()["documents"] == []
    assert client.get(base + f"/documents/{doc['id']}/download").status_code == 404


def test_disabled_feature_blocks_intake_not_existing_records(ctx):
    client, _, _ = ctx
    period = setup(client)
    row, body = new_incident(client, period)
    assert (
        client.put(
            "/api/v1/wica/settings", json={"revision": 1, "enabled": False, "periods": [period]}
        ).status_code
        == 200
    )
    body["id"] = str(uuid4())
    assert client.post("/api/v1/wica/incidents", json=body).status_code == 409
    assert client.get(f"/api/v1/wica/incidents/{row['id']}").status_code == 200


def test_repeated_upload_reuses_document_and_no_second_claim(ctx):
    client, _, _ = ctx
    row, _ = new_incident(client)
    row = upload(client, row)
    row = tag(client, row)
    claim_id = row["documents"][0]["claim_id"]
    row = upload(client, row)
    assert len(row["documents"]) == 1
    assert row["documents"][0]["claim_id"] == claim_id


def test_invalid_outcomes_and_tag_lock(ctx):
    client, _, _ = ctx
    row, _ = new_incident(client)
    row = tag(client, upload(client, row))
    doc_id = row["documents"][0]["id"]
    assert (
        action(
            client, row, doc_id, "settled", settlement_amount="1.00", settlement_date="2026-08-11"
        ).status_code
        == 409
    )
    assert action(client, row, doc_id, "rejected").status_code == 422
    row = action(client, row, doc_id, "pending_insurer").json()
    assert action(client, row, doc_id, "settled").status_code == 422
    assert (
        action(
            client, row, doc_id, "settled", settlement_amount="-1", settlement_date="2026-08-11"
        ).status_code
        == 422
    )
    assert (
        action(
            client, row, doc_id, "settled", settlement_amount="1.001", settlement_date="2026-08-11"
        ).status_code
        == 422
    )
    assert (
        action(
            client, row, doc_id, "settled", settlement_amount="1", settlement_date="2027-08-11"
        ).status_code
        == 422
    )
    base = f"/api/v1/wica/incidents/{row['id']}/documents/{doc_id}"
    payload = {
        "revision": row["revision"],
        "doc_type": "Medical Bill",
        "document_date": "2026-08-10",
        "benefit_type": "Medical",
    }
    assert client.patch(base, json=payload).status_code == 409
    row = action(client, row, doc_id, "submitted").json()
    payload.update(revision=row["revision"], benefit_type="Others")
    assert client.patch(base, json=payload).status_code == 409
    assert client.post(base + "/remove", json={"revision": row["revision"]}).status_code == 409


def test_pack_integrity_failure_and_unknown_upload_type(ctx, monkeypatch):
    from app.api.v1 import wica

    client, _, _ = ctx
    row, _ = new_incident(client)
    base = f"/api/v1/wica/incidents/{row['id']}"
    response = client.post(
        base + "/documents",
        data={"revision": 1, "document_id": str(uuid4())},
        files={"file": ("data.html", b"test")},
    )
    assert response.status_code == 415
    row = tag(client, upload(client, row))
    doc_id = row["documents"][0]["id"]
    row = action(client, row, doc_id, "pending_insurer").json()
    pack = {"id": str(uuid4()), "revision": row["revision"], "document_ids": [doc_id]}
    assert client.post(base + "/packs", json=pack).status_code == 201

    class TamperedStorage:
        def read(self, path):
            return b"tampered"

    monkeypatch.setattr(wica, "get_storage", TamperedStorage)
    assert client.get(base + f"/packs/{pack['id']}/download").status_code == 409


def test_new_company_defaults_disabled_and_no_portal_routes(ctx):
    client, _, _ = ctx
    assert client.get("/api/v1/wica/settings").json() == {
        "enabled": False,
        "revision": 0,
        "periods": [],
    }
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not any("wica" in path and ("portal" in path or "hr" in path) for path in paths)
