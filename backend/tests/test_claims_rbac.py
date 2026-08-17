from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.auth import CurrentUser
from app.core.deps import require_claim_access, require_claim_configuration


def _request(method: str):
    return SimpleNamespace(method=method)


def _user(role: str) -> CurrentUser:
    return CurrentUser(
        user_id=f"user-{role}",
        broker_firm_id=None if role == "system_admin" else "firm-1",
        client_id="client-1",
        role=role,  # type: ignore[arg-type]
    )


def test_system_admin_can_access_claim_review_and_configuration() -> None:
    admin = _user("system_admin")

    assert require_claim_access(_request("GET"), admin) is admin
    assert require_claim_access(_request("POST"), admin) is admin
    assert require_claim_configuration(admin) is admin


def test_broker_viewer_claim_access_is_read_only() -> None:
    viewer = _user("broker_viewer")

    assert require_claim_access(_request("GET"), viewer) is viewer
    with pytest.raises(HTTPException) as exc:
        require_claim_access(_request("POST"), viewer)

    assert exc.value.status_code == 403
