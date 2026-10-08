"""Middleware regression tests — security headers, request-ID, CORS.

These prove the wiring in app/main.py adds the headers, propagates inbound
request IDs (with sanitisation), and emits them back to the caller.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_middleware.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from app.core import security_headers, tenant_resolution  # noqa: E402
from app.core.downloads import attachment_header  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.core.security_headers import SecurityHeadersMiddleware  # noqa: E402
from app.core.settings import _resolve_env, get_settings  # noqa: E402
from app.core.tenant_resolution import (  # noqa: E402
    FirmResolutionMiddleware,
    invalidate_firm_cache,
    platform_owner_firm,
    request_firm,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BrokerFirm  # noqa: E402
from app.models.platform import TenantDomain  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_security_headers_present(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    headers = {k.lower(): v for k, v in res.headers.items()}
    assert "content-security-policy" in headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "frame-src 'self' blob:" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "permissions-policy" in headers
    assert "referrer-policy" in headers


def test_request_id_round_trips_when_provided(client: TestClient) -> None:
    res = client.get("/health", headers={"X-Request-ID": "test-abc-123"})
    assert res.headers["x-request-id"] == "test-abc-123"


def test_request_id_generated_when_missing(client: TestClient) -> None:
    res = client.get("/health")
    rid = res.headers.get("x-request-id")
    assert rid is not None
    # Generated IDs are 32-char hex (uuid4 without dashes).
    assert len(rid) == 32
    assert all(c in "0123456789abcdef" for c in rid)


def test_malicious_request_id_rejected(client: TestClient) -> None:
    """Header-injection attempts (control chars, oversized values, etc.)
    are dropped and replaced with a fresh UUID."""
    res = client.get(
        "/health",
        headers={"X-Request-ID": "evil\r\nInjected-Header: yes"},
    )
    rid = res.headers["x-request-id"]
    # Must NOT echo the malicious string.
    assert "\r" not in rid
    assert "\n" not in rid
    assert ":" not in rid
    assert rid != "evil\r\nInjected-Header: yes"


def test_oversized_request_id_dropped(client: TestClient) -> None:
    too_long = "x" * 250
    res = client.get("/health", headers={"X-Request-ID": too_long})
    assert res.headers["x-request-id"] != too_long
    assert len(res.headers["x-request-id"]) <= 200


def test_readiness_pings_database(client: TestClient) -> None:
    res = client.get("/readiness")
    assert res.status_code == 200
    assert res.json() == {
        "status": "ready",
        "database": "ok",
        "redis": "not-required",
    }


def _assert_security_headers(res) -> None:
    assert "default-src 'self'" in res.headers["content-security-policy"]
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"


def test_not_found_carries_security_headers(client: TestClient) -> None:
    res = client.get("/api/v1/no-such-endpoint")
    assert res.status_code == 404
    _assert_security_headers(res)


def test_cors_preflight_carries_security_headers(client: TestClient) -> None:
    """CORS answers a preflight itself, so the headers must wrap it."""
    res = client.options(
        "/api/v1/policy-years",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"
    _assert_security_headers(res)


def test_rate_limited_response_carries_security_and_cors_headers(client: TestClient) -> None:
    """The default limit is enforced by SlowAPIMiddleware, outside the app. CORS
    wraps it, so the browser sees a 429 it can read rather than a CORS failure."""
    origin = {"Origin": "http://localhost:5173"}
    limiter.reset()
    limiter.enabled = True
    try:
        statuses: list[int] = []
        res = client.get("/health", headers=origin)
        while res.status_code != 429 and len(statuses) < 500:
            statuses.append(res.status_code)
            res = client.get("/health", headers=origin)
    finally:
        limiter.enabled = False
        limiter.reset()

    assert res.status_code == 429
    assert set(statuses) == {200}
    _assert_security_headers(res)
    assert res.headers["x-request-id"]
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert res.headers["access-control-allow-credentials"] == "true"


def test_cors_grants_only_the_configured_origins(client: TestClient) -> None:
    """The allow-list is the validated `settings.cors_origins`, nothing else."""
    assert "http://localhost:5173" in get_settings().cors_origins
    res = client.options(
        "/api/v1/policy-years",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 400
    assert "access-control-allow-origin" not in res.headers
    simple = client.get("/health", headers={"Origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in simple.headers


_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nSUBMITTED-KEY-MATERIAL\n-----END PRIVATE KEY-----\n"


def test_validation_errors_never_echo_submitted_values(client: TestClient) -> None:
    """FastAPI's default 422 copies each failing field's submitted value into
    `detail[].input` — for a model-level check the whole body — which on the
    credential endpoints is a private key. Same status and shape, no values."""
    foreign_endpoint = json.dumps({
        "type": "service_account",
        "project_id": "inspro-test",
        "private_key": _PRIVATE_KEY,
        "client_email": "svc@inspro-test.iam.gserviceaccount.com",
        "token_uri": "http://169.254.169.254/computeMetadata/v1/token",
    })
    refused = client.put(
        "/api/v1/ai-config", json={"provider": "vertex", "api_key": foreign_endpoint}
    )
    too_short = client.put(
        "/api/v1/ai-config", json={"provider": "vertex", "api_key": "s3cret!"}
    )

    for res, secret in ((refused, "SUBMITTED-KEY-MATERIAL"), (too_short, "s3cret!")):
        assert res.status_code == 422, res.text
        assert secret not in res.text
        errors = res.json()["detail"]
        assert errors and all(
            {"type", "loc", "msg"} <= set(error) and not {"input", "ctx"} & set(error)
            for error in errors
        )
    # The reason still reaches the caller.
    assert "token_uri" in refused.json()["detail"][0]["msg"]
    assert too_short.json()["detail"][0]["loc"] == ["body", "api_key"]


def test_unhandled_error_is_a_generic_500_with_security_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Starlette answers an unhandled exception in `ServerErrorMiddleware`,
    outside every middleware, so the handler itself carries the headers — and
    never the exception text."""
    from app.main import create_app

    # No SPA bundle, so its catch-all cannot shadow the probe route.
    monkeypatch.setenv("INSPRO_SPA_DIR", str(tmp_path))
    probe_app = create_app()

    def explode() -> None:
        raise RuntimeError("internal detail that must not leak")

    probe_app.add_api_route("/__probe/explode", explode)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        res = TestClient(probe_app, raise_server_exceptions=False).get("/__probe/explode")

    assert res.status_code == 500
    assert res.json() == {"detail": "Internal server error."}
    assert "internal detail" not in res.text
    _assert_security_headers(res)
    logged = [r for r in caplog.records if r.name == "app.main" and r.exc_info]
    assert logged and "GET /__probe/explode" in logged[0].getMessage()


def _probe(*routes: Route) -> TestClient:
    return TestClient(
        Starlette(routes=list(routes), middleware=[Middleware(SecurityHeadersMiddleware)])
    )


@pytest.mark.parametrize(
    ("raw_env", "hsts"),
    [("production", True), ("prod", True), ("stg", True), ("staging", True), ("dev", False)],
)
def test_hsts_follows_the_resolved_environment(
    monkeypatch: pytest.MonkeyPatch, raw_env: str, hsts: bool
) -> None:
    """HSTS keys off the settings resolver, which maps "production" → "prod"
    and "stg" → "staging"; the raw variable left HSTS off for both."""
    # The dev settings are read before the variable changes: a full staging or
    # prod configuration (Entra, secrets) is not what this test is about.
    dev_settings = get_settings()
    monkeypatch.setenv("INSPRO_ENV", raw_env)
    resolved = replace(dev_settings, env=_resolve_env())
    monkeypatch.setattr(security_headers, "get_settings", lambda: resolved)

    res = _probe(Route("/", lambda request: JSONResponse({}))).get("/")

    assert ("strict-transport-security" in res.headers) is hsts


def test_downloads_and_session_cookies_are_never_stored() -> None:
    def download(request: Request) -> Response:
        return Response(b"x", headers={"Content-Disposition": attachment_header("forms.xlsx")})

    def sign_in(request: Request) -> Response:
        response = JSONResponse({"access_token": "t"})
        response.set_cookie("refresh", "r", httponly=True)
        return response

    def card(request: Request) -> Response:
        return Response(b"x", headers={
            "Content-Disposition": attachment_header("card.png"),
            "Cache-Control": "private, max-age=300",
        })

    probe = _probe(
        Route("/download", download),
        Route("/sign-in", sign_in, methods=["POST"]),
        Route("/card", card),
        Route("/plain", lambda request: JSONResponse({})),
    )

    assert probe.get("/download").headers["cache-control"] == "no-store"
    assert probe.post("/sign-in").headers["cache-control"] == "no-store"
    # An endpoint's own cache policy is never overridden.
    assert probe.get("/card").headers["cache-control"] == "private, max-age=300"
    assert "cache-control" not in probe.get("/plain").headers


# ── Host → firm resolution ───────────────────────────────────────────────────

_FRONT_DOOR_ID = "0f3c6a52-8a1e-4c55-9f53-2b7d1e6c9a10"


async def _firm_echo(request: Request) -> JSONResponse:
    ctx = request_firm(request)
    return JSONResponse({
        "url": str(request.url),
        "host": request.headers.get("host"),
        "firm": ctx.firm_id if ctx else None,
        "platform": ctx.is_platform_host if ctx else None,
    })


def _firm_probe(host: str = "testserver") -> TestClient:
    return TestClient(
        Starlette(
            routes=[Route("/api/v1/probe", _firm_echo), Route("/page", _firm_echo)],
            middleware=[Middleware(FirmResolutionMiddleware)],
        ),
        base_url=f"http://{host}",
    )


@pytest.fixture
def hosted_firms(monkeypatch: pytest.MonkeyPatch):
    """The platform owner on the platform host, an active and a suspended broker
    on their own domains, all resolved as production would."""
    with SessionLocal() as db:
        owner = platform_owner_firm(db)
        assert owner is not None and owner.is_platform_owner  # seed() marks the demo firm
        active = BrokerFirm(name="Middleware active", slug="mw-active")
        suspended = BrokerFirm(name="Middleware suspended", slug="mw-suspended", status="suspended")
        db.add_all([active, suspended])
        db.flush()
        db.add_all([
            TenantDomain(broker_firm_id=active.id, hostname="active.mw.test", status="active"),
            TenantDomain(
                broker_firm_id=suspended.id, hostname="suspended.mw.test", status="active",
            ),
        ])
        db.commit()
        ids = {"owner": owner.id, "active": active.id, "suspended": suspended.id}
    settings = replace(get_settings(), env="prod", platform_hosts=("platform.mw.test",))
    monkeypatch.setattr(tenant_resolution, "get_settings", lambda: settings)
    try:
        yield ids
    finally:
        with SessionLocal() as db:
            added = [ids["active"], ids["suspended"]]
            db.query(TenantDomain).filter(
                TenantDomain.broker_firm_id.in_(added)
            ).delete(synchronize_session=False)
            db.query(BrokerFirm).filter(BrokerFirm.id.in_(added)).delete(
                synchronize_session=False
            )
            db.get(BrokerFirm, ids["owner"]).status = "active"
            db.commit()


def test_production_refuses_unknown_hosts_as_json_for_the_api_and_html_otherwise(hosted_firms):
    probe = _firm_probe("unknown.mw.test")
    api = probe.get("/api/v1/probe")
    assert api.status_code == 404
    assert api.json() == {
        "detail": {"code": "unknown_site", "message": "This site is not available."}
    }
    page = probe.get("/page")
    assert page.status_code == 404
    assert page.headers["content-type"].startswith("text/html")
    assert "This site is not available." in page.text
    assert page.headers["cache-control"] == "no-store"


def test_suspended_firm_is_refused_on_its_host_only(hosted_firms):
    refused = _firm_probe("suspended.mw.test").get("/api/v1/probe")
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "firm_suspended"
    served = _firm_probe("active.mw.test").get("/api/v1/probe")
    assert served.status_code == 200
    assert served.json()["firm"] == hosted_firms["active"]

    # The platform host keeps serving even when the owner itself is suspended:
    # it is where the master admin lifts a suspension.
    with SessionLocal() as db:
        db.get(BrokerFirm, hosted_firms["owner"]).status = "suspended"
        db.commit()
    invalidate_firm_cache()
    platform = _firm_probe("platform.mw.test").get("/api/v1/probe")
    assert platform.status_code == 200
    assert platform.json()["firm"] == hosted_firms["owner"]
    assert platform.json()["platform"] is True


def test_front_door_requires_its_id_and_takes_the_host_from_the_edge(
    hosted_firms, monkeypatch: pytest.MonkeyPatch
):
    settings = replace(
        get_settings(), env="prod", platform_hosts=("platform.mw.test",),
        front_door_id=_FRONT_DOOR_ID,
    )
    monkeypatch.setattr(tenant_resolution, "get_settings", lambda: settings)
    probe = _firm_probe("origin-app.azurewebsites.test")
    edge = {"X-Inspro-Edge-Host": "Active.MW.test"}

    for fdid in (None, "11111111-2222-3333-4444-555555555555"):
        headers = {**edge, **({"X-Azure-FDID": fdid} if fdid else {})}
        refused = probe.get("/api/v1/probe", headers=headers)
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "edge_required"

    served = probe.get(
        "/api/v1/probe?x=1",
        headers={**edge, "X-Azure-FDID": _FRONT_DOOR_ID.upper(), "X-Forwarded-Host": "evil.test"},
    )
    assert served.status_code == 200, served.text
    body = served.json()
    # The edge-set header picked the firm, and everything downstream sees the
    # public host the browser used — never the origin's or a forwarded one.
    assert body["firm"] == hosted_firms["active"]
    assert body["host"] == "active.mw.test"
    assert body["url"] == "http://active.mw.test/api/v1/probe?x=1"

    missing = probe.get("/api/v1/probe", headers={"X-Azure-FDID": _FRONT_DOOR_ID})
    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "edge_host_missing"


def test_client_ip_trusts_front_doors_socket_address_only_with_its_id(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core import settings as settings_module
    from app.core.request_context import client_ip

    def request(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw, "client": ("10.0.0.9", 443)})

    socket = {"X-Azure-SocketIP": "203.0.113.7", "X-Forwarded-For": "198.51.100.1"}
    # No profile configured: the header is ignored entirely.
    assert client_ip(request({**socket, "X-Azure-FDID": _FRONT_DOOR_ID})) == "10.0.0.9"

    settings = replace(get_settings(), front_door_id=_FRONT_DOOR_ID)
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    assert client_ip(request({**socket, "X-Azure-FDID": _FRONT_DOOR_ID})) == "203.0.113.7"
    # Another profile's id, or none, proves nothing.
    other = "11111111-2222-3333-4444-555555555555"
    assert client_ip(request({**socket, "X-Azure-FDID": other})) == "10.0.0.9"
    assert client_ip(request(socket)) == "10.0.0.9"
    # A malformed address falls back to the peer.
    bad = {"X-Azure-SocketIP": "not-an-ip", "X-Azure-FDID": _FRONT_DOOR_ID}
    assert client_ip(request(bad)) == "10.0.0.9"
