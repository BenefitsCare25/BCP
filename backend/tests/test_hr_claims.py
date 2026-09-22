"""Registration and response-shape regressions for delegated HR claims."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.api.v1 import hr_claims
from app.core.auth import CurrentUser
from app.core.deps import require_write_access
from app.main import app
from app.models.claim import ORIGIN_HR


def test_hr_claims_router_is_registered_outside_broker_write_gate() -> None:
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/hr/claims"
        and "GET" in route.methods
    )

    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
    assert require_write_access not in dependency_calls


def test_claim_output_uses_document_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = SimpleNamespace(
        id="claim-1",
        employee_id="employee-1",
        policy_year_id="year-1",
        reference_no="CLM-1",
        claim_kind="insured",
        claim_type="Outpatient",
        provider_name="Demo Clinic",
        invoice_number="INV-1",
        status="draft",
        incurred_date=None,
        amount_claimed=None,
        currency="SGD",
        created_by_user_id="user-1",
        intake_meta={
            "delegated_by_name": "Demo HR",
            "delegated_by_email": "hr@demo.test",
        },
        created_at=None,
        submitted_at=None,
    )
    document_slot = SimpleNamespace(
        key="invoice",
        display="Medical invoice",
        instructions="Upload the itemised invoice.",
    )
    monkeypatch.setattr(
        hr_claims,
        "setup_for_claim",
        lambda _db, _claim: SimpleNamespace(documents=[document_slot]),
    )
    monkeypatch.setattr(hr_claims, "claim_documents", lambda _db, _claim: [])

    class FakeSession:
        def get(self, _model: type[Any], _entity_id: str) -> SimpleNamespace:
            return SimpleNamespace(employee_name="Demo Employee")

    output = hr_claims._out(FakeSession(), claim)  # type: ignore[arg-type]

    assert output["doc_slots"] == [
        {
            "key": "invoice",
            "label": "Medical invoice",
            "instructions": "Upload the itemised invoice.",
        }
    ]
    assert output["submission_channel"] == "hr"
    assert output["submitted_by_name"] == "Demo HR"
    assert output["submitted_by_email"] == "hr@demo.test"
    assert output["can_add_evidence"] is True
    assert output["can_submit"] is True


def test_draft_records_hr_origin_and_submitter_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = hr_claims.DelegatedClaimIn(
        employee_id="employee-1",
        claim_kind="flex",
        flex_category_name="Wellness",
        claim_type="Wellness",
        incurred_date="2026-09-20",
        provider_name="Demo Provider",
        invoice_number="HR-DEMO-1",
        amount_claimed=25,
        currency="SGD",
    )
    employee = SimpleNamespace(id="employee-1", client_id="client-1")
    claim = SimpleNamespace(id="claim-1", intake_meta=None)

    monkeypatch.setattr(hr_claims, "_employee", lambda *_args: employee)
    monkeypatch.setattr(hr_claims, "lock_claim_command_key", lambda *_args: None)
    monkeypatch.setattr(hr_claims, "replayed_claim_for_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hr_claims, "create_claim", lambda *_args, **_kwargs: claim)
    monkeypatch.setattr(hr_claims, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hr_claims, "record_claim_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hr_claims, "_out", lambda _db, value: {"id": value.id})

    class FakeSession:
        def get(self, _model: type[Any], _entity_id: str) -> SimpleNamespace:
            return SimpleNamespace(display_name="Demo HR")

        def commit(self) -> None:
            pass

    user = CurrentUser(
        user_id="user-1",
        broker_firm_id="firm-1",
        client_id="client-1",
        role="client_hr",
        email="hr@demo.test",
    )
    request = Request({"type": "http", "method": "POST", "path": "/"})

    result = hr_claims.draft(request, body, "command-1", user, FakeSession())  # type: ignore[arg-type]

    assert result == {"id": "claim-1"}
    assert claim.origin == ORIGIN_HR
    assert claim.created_by_user_id == "user-1"
    assert claim.intake_meta == {
        "submission_channel": "hr",
        "delegated_by_user_id": "user-1",
        "delegated_by_name": "Demo HR",
        "delegated_by_email": "hr@demo.test",
    }


def test_hr_evidence_window_is_status_gated_not_portal_origin_gated() -> None:
    hr_claims._assert_evidence_mutable(SimpleNamespace(status="draft"))  # type: ignore[arg-type]
    hr_claims._assert_evidence_mutable(SimpleNamespace(status="needs_info"))  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as decided:
        hr_claims._assert_evidence_mutable(SimpleNamespace(status="approved"))  # type: ignore[arg-type]

    assert decided.value.status_code == 403
    assert decided.value.detail == "Evidence is retained after a decision."
