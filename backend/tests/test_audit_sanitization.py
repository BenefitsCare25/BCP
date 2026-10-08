"""Audit log must redact secret-like keys before persisting, stamp the row with
the right company and caller, and show read-only roles masked personal data."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.audit import _scrub, mask_personal_data, write_audit
from app.core.auth import CurrentUser, Role, get_current_user
from app.core.request_context import RequestIDMiddleware
from app.db.session import SessionLocal
from app.main import app
from app.models import AuditLog

COMPANY = "00000000-0000-0000-0000-0000000a0d01"
OTHER_COMPANY = "00000000-0000-0000-0000-0000000a0d02"


def test_top_level_api_key_redacted() -> None:
    out = _scrub({"api_key": "sk-real-secret", "model": "claude"})
    assert out == {"api_key": "[redacted]", "model": "claude"}


def test_nested_secret_redacted() -> None:
    payload = {"ai_meta": {"client_secret": "abc", "tokens": 50}}
    out = _scrub(payload)
    assert out == {"ai_meta": {"client_secret": "[redacted]", "tokens": 50}}


def test_lists_traversed() -> None:
    out = _scrub([{"password": "hi"}, {"safe": 1}])
    assert out == [{"password": "[redacted]"}, {"safe": 1}]


def test_case_insensitive_matches() -> None:
    out = _scrub({"Authorization": "Bearer xxx", "Bearer": "x"})
    assert out["Authorization"] == "[redacted]"
    assert out["Bearer"] == "[redacted]"


def test_token_counts_not_redacted() -> None:
    out = _scrub({"input_tokens": 100, "output_tokens": 50, "tokens": 150})
    assert out == {"input_tokens": 100, "output_tokens": 50, "tokens": 150}


def test_access_token_redacted() -> None:
    out = _scrub({"access_token": "eyJabc", "refresh_token": "rt"})
    assert out == {"access_token": "[redacted]", "refresh_token": "[redacted]"}


def test_non_secret_keys_untouched() -> None:
    payload = {"display_name": "X", "rule": {"and": []}}
    assert _scrub(payload) == payload


def test_service_account_fields_redacted() -> None:
    """Both AI credential surfaces: cleartext SA key and stored ciphertext."""
    out = _scrub(
        {
            "service_account_json": '{"type":"service_account","private_key":"..."}',
            "encrypted_service_account": "gAAAAAB...",
            "location": "asia-southeast1",
        }
    )
    assert out == {
        "service_account_json": "[redacted]",
        "encrypted_service_account": "[redacted]",
        "location": "asia-southeast1",
    }


# ── Resource company, caller and cross-tenant flag ───────────────────────────


class _Rows:
    """Stands in for a Session: `write_audit` only ever calls `add`."""

    def __init__(self) -> None:
        self.added: list[AuditLog] = []

    def add(self, row: AuditLog) -> None:
        self.added.append(row)


def _user(role: Role = "broker_admin", client_id: str | None = COMPANY) -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000a0dff",
        broker_firm_id="00000000-0000-0000-0000-0000000a0d10",
        client_id=client_id,
        role=role,
    )


def _write(user: CurrentUser, **kwargs: Any) -> AuditLog:
    rows = _Rows()
    write_audit(rows, user, "update", "employee", "e-1", **kwargs)  # type: ignore[arg-type]
    (row,) = rows.added
    return row


def test_audit_row_defaults_to_the_actors_company() -> None:
    row = _write(_user())
    assert row.client_id == COMPANY
    assert row.cross_tenant_access is False


def test_audit_row_can_be_stamped_with_the_resources_company() -> None:
    row = _write(_user("system_admin", client_id=COMPANY), client_id=OTHER_COMPANY)
    assert row.client_id == OTHER_COMPANY
    assert row.cross_tenant_access is True


def test_platform_row_is_not_cross_tenant() -> None:
    """A system admin's row about no company touches no tenant."""
    row = _write(_user("system_admin", client_id=COMPANY), client_id=None)
    assert row.client_id is None
    assert row.cross_tenant_access is False


def test_system_admin_without_a_company_is_not_cross_tenant() -> None:
    row = _write(_user("system_admin", client_id=None))
    assert row.client_id is None
    assert row.cross_tenant_access is False


def test_audit_row_outside_a_request_has_no_caller() -> None:
    row = _write(_user())
    assert row.ip_address is None
    assert row.user_agent is None


def test_audit_row_captures_the_caller_without_a_request_argument() -> None:
    """`RequestIDMiddleware` captures the peer and agent, so a writer deep in a
    service (here a sync endpoint, run in a worker thread) still records them."""
    rows = _Rows()

    def endpoint(request: Request) -> PlainTextResponse:
        write_audit(rows, _user(), "export", "policy_year", "py-1")  # type: ignore[arg-type]
        return PlainTextResponse("ok")

    probe = Starlette(
        routes=[Route("/", endpoint)], middleware=[Middleware(RequestIDMiddleware)]
    )
    with TestClient(probe) as client:
        client.get("/", headers={"User-Agent": "audit-probe/1.0", "X-Request-ID": "req-9"})

    (row,) = rows.added
    assert row.ip_address == "testclient"
    assert row.user_agent == "audit-probe/1.0"
    assert row.request_id == "req-9"


# ── Viewer masking ───────────────────────────────────────────────────────────


def test_personal_fields_are_masked_and_identifiers_keep_their_shape() -> None:
    payload = {
        "attribute_values": {
            "id_no": "S1234567D",
            "date_of_birth": "1990-01-01",
            "Mobile Phone": "+6591234567",
            "emailAddress": "amy@example.test",
            "bank_account_no": "123-456",
            "salary": 5200,
            "grade": 7,
        },
        "dependants": [{"name": "Ben", "dependant_id_no": "T1234567J", "dob": ""}],
        "note": "Corrected s1234567d to S7654321E",
        "employee_id": "00000000-0000-0000-0000-0000000a0e01",
        "nric": None,
    }
    assert mask_personal_data(payload) == {
        "attribute_values": {
            "id_no": "S******7D",
            "date_of_birth": "[masked]",
            "Mobile Phone": "[masked]",
            "emailAddress": "[masked]",
            "bank_account_no": "[masked]",
            "salary": "[masked]",
            "grade": 7,
        },
        "dependants": [{"name": "Ben", "dependant_id_no": "T******7J", "dob": ""}],
        "note": "Corrected S******7D to S******1E",
        "employee_id": "00000000-0000-0000-0000-0000000a0e01",
        "nric": None,
    }


def test_masking_handles_missing_payloads() -> None:
    assert mask_personal_data(None) is None
    assert mask_personal_data({}) == {}


_PERSONAL_BEFORE = {"attribute_values": {"id_no": "S1234567D", "mobile": "+6591234567"}}
_PERSONAL_AFTER = {"attribute_values": {"id_no": "S7654321E", "mobile": "+6598765432"}}


@pytest.fixture
def feed() -> Iterator[tuple[TestClient, dict[str, CurrentUser]]]:
    with SessionLocal() as db:
        db.query(AuditLog).delete()
        db.add_all([
            AuditLog(
                client_id=COMPANY, user_id="u-1", action="update", entity_type="employee",
                entity_id="e-1", before=_PERSONAL_BEFORE, after=_PERSONAL_AFTER,
            ),
            AuditLog(client_id=COMPANY, user_id="u-1", action="create", entity_type="user"),
            AuditLog(
                client_id=COMPANY, user_id="u-1", action="create", entity_type="invitation"
            ),
            AuditLog(
                client_id=COMPANY, user_id="u-1", action="update", entity_type="broker_firm"
            ),
            AuditLog(
                client_id=OTHER_COMPANY, user_id="u-1", action="delete",
                entity_type="employee", entity_id="e-2",
            ),
        ])
        db.commit()
    active = {"user": _user()}
    app.dependency_overrides[get_current_user] = lambda: active["user"]
    try:
        with TestClient(app) as client:
            yield client, active
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        with SessionLocal() as db:
            db.query(AuditLog).delete()
            db.commit()


def _items(client: TestClient) -> list[dict[str, Any]]:
    res = client.get("/api/v1/audit-log")
    assert res.status_code == 200, res.text
    return list(res.json()["items"])


def test_viewer_feed_masks_personal_data(
    feed: tuple[TestClient, dict[str, CurrentUser]],
) -> None:
    client, active = feed
    active["user"] = _user("broker_viewer")
    (entry,) = _items(client)
    assert entry["before"] == {"attribute_values": {"id_no": "S******7D", "mobile": "[masked]"}}
    assert entry["after"] == {"attribute_values": {"id_no": "S******1E", "mobile": "[masked]"}}


def test_company_feed_excludes_platform_records(
    feed: tuple[TestClient, dict[str, CurrentUser]],
) -> None:
    """Firm users, invitations and the firm are not company activity, and a
    write-capable broker still sees the payload as recorded."""
    client, _ = feed
    (entry,) = _items(client)
    assert entry["entity_type"] == "employee"
    assert entry["before"] == _PERSONAL_BEFORE
    filtered = client.get("/api/v1/audit-log", params={"entity_type": "user"})
    assert filtered.json() == {"total": 0, "items": []}


def test_system_admin_feed_includes_platform_records(
    feed: tuple[TestClient, dict[str, CurrentUser]],
) -> None:
    client, active = feed
    active["user"] = _user("system_admin")
    entity_types = sorted(entry["entity_type"] for entry in _items(client))
    assert entity_types == ["broker_firm", "employee", "employee", "invitation", "user"]
