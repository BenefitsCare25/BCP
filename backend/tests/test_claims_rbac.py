from dataclasses import replace
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


@pytest.mark.parametrize("role", ["system_admin", "firm_admin", "broker_admin"])
def test_admins_can_access_claim_review_and_configuration(role: str) -> None:
    admin = _user(role)

    assert require_claim_access(_request("GET"), admin) is admin
    assert require_claim_access(_request("POST"), admin) is admin
    assert require_claim_configuration(admin) is admin


def test_broker_viewer_claim_access_is_read_only() -> None:
    viewer = _user("broker_viewer")

    assert require_claim_access(_request("GET"), viewer) is viewer
    with pytest.raises(HTTPException) as exc:
        require_claim_access(_request("POST"), viewer)

    assert exc.value.status_code == 403


def test_read_grant_claim_access_is_read_only() -> None:
    """A master admin in a firm reached through a `read` grant reads claims
    but changes nothing."""
    admin = replace(_user("system_admin"), platform_access="read")
    claims = SimpleNamespace(path="/api/v1/claims/claim-1")

    assert require_claim_access(SimpleNamespace(method="GET", url=claims), admin) is admin
    with pytest.raises(HTTPException) as exc:
        require_claim_access(SimpleNamespace(method="POST", url=claims), admin)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "platform_access_read_only"
