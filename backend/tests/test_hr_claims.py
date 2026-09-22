"""Registration and response-shape regressions for delegated HR claims."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.api.v1 import hr_claims
from app.core.deps import require_write_access
from app.main import app


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
        status="draft",
        incurred_date=None,
        amount_claimed=None,
        currency="SGD",
        created_by_user_id="user-1",
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
