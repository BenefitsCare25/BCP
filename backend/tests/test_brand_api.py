"""Brand settings API: permissions, validation, revisions, attribution
entitlement, company overrides, sender verification and image uploads.

`get_current_user` is overridden per role. Firm O is the platform owner (served
on "testserver", the test platform host); firm B is a broker firm served on
`firm-b.localhost`.
"""
from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy import select

from app.core.auth import CurrentUser, get_current_user
from app.db.session import SessionLocal
from app.main import app
from app.models import AuditLog, BrandProfile, BrokerFirm, Client, User
from app.models.platform import PlatformAccessGrant, PlatformAuditLog
from app.services.brand_assets import read_asset

OWNER_ID = "00000000-0000-0000-0000-00000000b0a1"
FIRM_B_ID = "00000000-0000-0000-0000-00000000b0b1"
CLIENT_O_ID = "00000000-0000-0000-0000-00000000b0a2"
CLIENT_B_ID = "00000000-0000-0000-0000-00000000b0b2"
CLIENT_B2_ID = "00000000-0000-0000-0000-00000000b0b3"
MASTER_ID = "00000000-0000-0000-0000-00000000b0c1"
B_HOST = "http://firm-b.localhost"
BRAND = "/api/v1/firm/brand"
VERIFY = f"/api/v1/platform/firms/{FIRM_B_ID}/brand/sender-verification"
# A renamed brand must name its own support email.
HELP = {"support_email": "help@harbour.example"}


@pytest.fixture(scope="module", autouse=True)
def _setup_db() -> Iterator[None]:
    with SessionLocal() as s:
        s.add(BrokerFirm(id=OWNER_ID, name="Owner Firm", slug="owner-firm", is_platform_owner=True))
        s.add(BrokerFirm(id=FIRM_B_ID, name="Firm B", slug="firm-b"))
        s.flush()
        s.add(Client(id=CLIENT_O_ID, name="Owner Co", broker_firm_id=OWNER_ID, slug="owner-co"))
        s.add(Client(id=CLIENT_B_ID, name="B Co", broker_firm_id=FIRM_B_ID, slug="bco"))
        s.add(Client(id=CLIENT_B2_ID, name="B Two", broker_firm_id=FIRM_B_ID, slug="btwo"))
        s.add(User(id=MASTER_ID, email="master@brand.test", broker_firm_id=None,
                   role="system_admin", status="active"))
        s.commit()
    yield


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_current_user, None)
    with SessionLocal() as s:
        s.query(BrandProfile).delete()
        s.query(PlatformAccessGrant).delete()
        s.get(BrokerFirm, FIRM_B_ID).allow_hide_attribution = False  # type: ignore[union-attr]
        s.commit()


def _as(role: str, *, firm: str | None = FIRM_B_ID, client_id: str | None = None,
        user_id: str = "brand-actor", base_url: str = B_HOST) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, broker_firm_id=firm, client_id=client_id, role=role,  # type: ignore[arg-type]
    )
    return TestClient(app, base_url=base_url)


def _firm_admin() -> TestClient:
    return _as("firm_admin")


def _master(client_id: str | None = CLIENT_B_ID, base_url: str = B_HOST) -> TestClient:
    return _as("system_admin", firm=None, client_id=client_id, user_id=MASTER_ID,
               base_url=base_url)


def _grant(scope: str) -> None:
    from datetime import UTC, datetime, timedelta

    with SessionLocal() as s:
        s.add(PlatformAccessGrant(
            user_id=MASTER_ID, broker_firm_id=FIRM_B_ID, scope=scope,
            reason="Brand set-up support for the broker",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        s.commit()


def _image(fmt: str, width: int, height: int, **kwargs: object) -> bytes:
    out = BytesIO()
    Image.new("RGBA", (width, height), (20, 90, 160, 255)).save(out, format=fmt, **kwargs)
    return out.getvalue()


def _upload(api: TestClient, slot: str, data: bytes, *, name: str = "image.png",
            kind: str = "image/png", scope: str = "firm"):  # type: ignore[no-untyped-def]
    return api.post(f"{BRAND}/assets/{slot}", params={"scope": scope},
                    files={"file": (name, data, kind)})


# ── Permissions ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("role", ["broker_admin", "broker_viewer"])
def test_brokers_who_do_not_own_the_firm_are_refused(role: str) -> None:
    api = _as(role)
    assert api.get(BRAND).status_code == 403
    assert api.put(BRAND, json={"revision": 0, "product_name": "X"}).status_code == 403
    assert api.get(f"{BRAND}/companies/{CLIENT_B_ID}").status_code == 403
    assert _upload(api, "logo", _image("PNG", 300, 100)).status_code == 403
    assert api.delete(f"{BRAND}/assets/logo").status_code == 403


def test_firm_admin_reads_and_writes_its_own_firms_brand() -> None:
    api = _firm_admin()
    empty = api.get(BRAND)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["settings"]["revision"] == 0
    assert body["settings"]["product_name"] is None
    assert body["effective"]["product_name"] == "Inspro"
    assert body["entitlements"] == {"allow_hide_attribution": False}
    assert body["email_from"] == {"address": None, "verified_at": None}

    saved = api.put(BRAND, json={
        "revision": 0, "product_name": "  Harbour Benefits ", "primary_color": "#1F4E79",
        **HELP,
    })
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["settings"]["product_name"] == "Harbour Benefits"
    assert out["settings"]["primary_color"] == "#1f4e79"
    assert out["settings"]["revision"] == 1
    assert out["effective"]["product_name"] == "Harbour Benefits"
    assert out["effective"]["primary_foreground"] == "#ffffff"
    assert out["effective"]["accent_color"] == "#c11a2b"

    with SessionLocal() as s:
        audit = s.scalars(select(AuditLog).where(AuditLog.entity_type == "brand_profile")).all()
        assert [(a.action, a.client_id) for a in audit][-1] == ("update", None)
        assert audit[-1].after["product_name"] == "Harbour Benefits"  # type: ignore[index]


def test_master_admin_needs_access_to_the_firm() -> None:
    assert _master().get(BRAND).status_code == 404
    assert _master().put(BRAND, json={"revision": 0, "product_name": "X"}).status_code == 404
    _grant("read")
    assert _master().get(BRAND).status_code == 200
    refused = _master().put(BRAND, json={"revision": 0, "product_name": "X"})
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "platform_access_read_only"
    _grant("write")
    saved = _master().put(
        BRAND, json={"revision": 0, "product_name": "Granted Name", **HELP}
    )
    assert saved.status_code == 200, saved.text
    with SessionLocal() as s:
        trail = s.scalars(select(PlatformAuditLog).where(
            PlatformAuditLog.action == "firm.brand.update",
            PlatformAuditLog.broker_firm_id == FIRM_B_ID,
        )).all()
        assert trail and trail[-1].detail["after"]["product_name"] == "Granted Name"  # type: ignore[index]
    # Standing access to the platform owner's firm.
    owner = _master(client_id=CLIENT_O_ID, base_url="http://testserver")
    assert owner.put(BRAND, json={"revision": 0, "short_name": "Owner"}).status_code == 200


# ── Validation ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("field,value", [
    ("primary_color", "red"),
    ("primary_color", "#12345"),
    ("accent_color", "#gggggg"),
    ("card_prefix", "A"),
    ("card_prefix", "TOOLONG99"),
    ("card_prefix", "AB-1"),
    ("support_email", "not-an-email"),
    ("email_reply_to", "a@@b.com"),
    ("support_phone", "call me"),
    ("support_phone", "+1"),
    ("product_name", "Line\nbreak"),
    ("product_name", "x" * 81),
    ("short_name", "y" * 31),
    ("show_platform_attribution", "maybe"),
    ("assets", {}),
])
def test_invalid_brand_values_are_refused(field: str, value: object) -> None:
    res = _firm_admin().put(BRAND, json={"revision": 0, field: value})
    assert res.status_code == 422, (field, value, res.text)


def test_values_are_normalised_and_blank_clears() -> None:
    api = _firm_admin()
    res = api.put(BRAND, json={
        "revision": 0, "card_prefix": "hb1", "accent_color": "#ABCDEF",
        "support_email": " Help@Harbour.Example ", "support_phone": "+65 6123 4567",
        "short_name": "Harbour",
    })
    assert res.status_code == 200, res.text
    settings = res.json()["settings"]
    assert (settings["card_prefix"], settings["accent_color"]) == ("HB1", "#abcdef")
    assert settings["support_email"] == "help@harbour.example"
    assert res.json()["effective"]["accent_foreground"] == "#000000"
    cleared = api.put(BRAND, json={"revision": 1, "short_name": "   "})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["settings"]["short_name"] is None
    assert cleared.json()["settings"]["card_prefix"] == "HB1"  # fields not sent are kept
    assert cleared.json()["effective"]["short_name"] == "Inspro"


# ── Revisions ─────────────────────────────────────────────────────────────────
def test_stale_revisions_are_refused() -> None:
    api = _firm_admin()
    assert api.put(BRAND, json={"revision": 0, "product_name": "One", **HELP}).status_code == 200
    stale = api.put(BRAND, json={"revision": 0, "product_name": "Two"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "brand_revision_stale"
    assert stale.json()["detail"]["revision"] == 1
    assert api.put(BRAND, json={"revision": 5, "product_name": "Two"}).status_code == 409
    fresh = api.put(BRAND, json={"revision": 1, "product_name": "Two"})
    assert fresh.json()["settings"]["revision"] == 2
    # An unchanged save is not a new revision.
    same = api.put(BRAND, json={"revision": 2, "product_name": "Two"})
    assert same.json()["settings"]["revision"] == 2


# ── Attribution ───────────────────────────────────────────────────────────────
def test_hiding_attribution_needs_the_entitlement() -> None:
    api = _firm_admin()
    refused = api.put(BRAND, json={"revision": 0, "show_platform_attribution": False})
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "attribution_entitlement_required"
    with SessionLocal() as s:
        s.get(BrokerFirm, FIRM_B_ID).allow_hide_attribution = True  # type: ignore[union-attr]
        s.commit()
    hidden = api.put(BRAND, json={"revision": 0, "show_platform_attribution": False})
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["effective"]["show_platform_attribution"] is False
    assert hidden.json()["entitlements"] == {"allow_hide_attribution": True}
    # Revoking the entitlement brings attribution back; the row may still be saved.
    with SessionLocal() as s:
        s.get(BrokerFirm, FIRM_B_ID).allow_hide_attribution = False  # type: ignore[union-attr]
        s.commit()
    kept = api.put(BRAND, json={
        "revision": 1, "show_platform_attribution": False, "product_name": "Still Saves",
        **HELP,
    })
    assert kept.status_code == 200, kept.text
    assert kept.json()["effective"]["show_platform_attribution"] is True


# ── Company overrides ─────────────────────────────────────────────────────────
def test_company_overrides_layer_on_the_firm_brand() -> None:
    api = _firm_admin()
    api.put(BRAND, json={
        "revision": 0, "product_name": "Firm Name", "primary_color": "#111111", **HELP,
    })
    url = f"{BRAND}/companies/{CLIENT_B_ID}"
    assert api.get(f"{BRAND}/companies/{CLIENT_O_ID}").status_code == 404
    assert api.put(f"{BRAND}/companies/{CLIENT_O_ID}", json={
        "revision": 0, "product_name": "x",
    }).status_code == 404
    firm_only = (
        ("email_from_address", "a@b.example"), ("show_platform_attribution", True),
        ("card_prefix", "ABC"),
    )
    for field, value in firm_only:
        refused = api.put(url, json={"revision": 0, field: value})
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "brand_firm_only_field"

    saved = api.put(url, json={"revision": 0, "product_name": "B Co Benefits"})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["client_id"] == CLIENT_B_ID
    assert out["effective"]["product_name"] == "B Co Benefits"
    assert out["effective"]["primary_color"] == "#111111"
    assert out["inherited"]["product_name"] == "Firm Name"
    other = api.get(f"{BRAND}/companies/{CLIENT_B2_ID}").json()
    assert other["effective"]["product_name"] == "Firm Name"
    assert other["settings"]["revision"] == 0

    assert api.delete(url, params={"revision": 0}).status_code == 409
    removed = api.delete(url, params={"revision": 1})
    assert removed.status_code == 200, removed.text
    assert removed.json()["settings"]["revision"] == 0
    assert removed.json()["effective"]["product_name"] == "Firm Name"
    with SessionLocal() as s:
        actions = s.scalars(select(AuditLog.action).where(
            AuditLog.entity_type == "brand_profile", AuditLog.client_id == CLIENT_B_ID,
        )).all()
        assert actions == ["update", "delete"]


# ── Sender verification ───────────────────────────────────────────────────────
def test_sender_verification_follows_the_current_address() -> None:
    saved = _firm_admin().put(BRAND, json={
        "revision": 0, "email_from_address": "Benefits@Harbour.Example",
    })
    assert saved.json()["email_from"] == {
        "address": "benefits@harbour.example", "verified_at": None,
    }

    def platform() -> TestClient:
        return _master(client_id=None, base_url="http://testserver")

    mismatch = platform().post(VERIFY, json={"address": "other@harbour.example"})
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "sender_address_mismatch"
    pending = platform().get(VERIFY)
    assert pending.json() == {"address": "benefits@harbour.example", "verified_at": None}
    verified = platform().post(VERIFY, json={"address": "benefits@harbour.example"})
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified_at"] is not None
    assert _firm_admin().get(BRAND).json()["email_from"]["verified_at"] is not None

    # Re-sending the same address keeps the verification; a new one clears it.
    same = _firm_admin().put(BRAND, json={
        "revision": 1, "email_from_address": "benefits@harbour.example",
    })
    assert same.json()["email_from"]["verified_at"] is not None
    changed = _firm_admin().put(BRAND, json={
        "revision": 1, "email_from_address": "mail@harbour.example",
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["email_from"]["verified_at"] is None

    platform().post(VERIFY, json={"address": "mail@harbour.example"})
    cleared = platform().delete(VERIFY)
    assert cleared.status_code == 200
    assert cleared.json() == {"address": "mail@harbour.example", "verified_at": None}
    with SessionLocal() as s:
        actions = s.scalars(select(PlatformAuditLog.action).where(
            PlatformAuditLog.action.like("firm.brand.sender_%")
        )).all()
        assert sorted(actions) == [
            "firm.brand.sender_unverify", "firm.brand.sender_verify", "firm.brand.sender_verify",
        ]
        firm_trail = s.scalars(select(AuditLog.action).where(
            AuditLog.action.like("brand.sender_%")
        )).all()
        assert len(firm_trail) == 3


def test_sender_verification_is_master_admin_on_platform_hosts_only() -> None:
    _firm_admin().put(BRAND, json={"revision": 0, "email_from_address": "a@harbour.example"})
    body = {"address": "a@harbour.example"}
    firm_admin = _as("firm_admin", base_url="http://testserver")
    assert firm_admin.post(VERIFY, json=body).status_code == 403
    assert _master(client_id=None, base_url=B_HOST).post(VERIFY, json=body).status_code == 404
    missing = _master(client_id=None, base_url="http://testserver").post(
        "/api/v1/platform/firms/no-such-firm/brand/sender-verification", json=body
    )
    assert missing.status_code == 404


# ── Images ────────────────────────────────────────────────────────────────────
def test_images_are_validated_reencoded_and_served() -> None:
    api = _firm_admin()
    info = PngInfo()
    info.add_text("Comment", "secret-metadata")
    logo = _image("PNG", 600, 200, pnginfo=info) + b"<html><script>alert(1)</script></html>"
    res = _upload(api, "logo", logo)
    assert res.status_code == 200, res.text
    meta = res.json()["settings"]["assets"]["logo"]
    assert (meta["content_type"], meta["width"], meta["height"]) == ("image/png", 600, 200)
    assert meta["url"] == f"/api/v1/public/brand-assets/{meta['id']}"
    assert res.json()["effective"]["logo_url"] == meta["url"]
    assert res.json()["settings"]["revision"] == 1
    stored = read_asset(FIRM_B_ID, meta["id"])
    assert b"script" not in stored and b"secret-metadata" not in stored
    assert Image.open(BytesIO(stored)).size == (600, 200)

    served = TestClient(app, base_url=B_HOST).get(meta["url"])
    assert served.status_code == 200
    assert served.content == stored
    assert served.headers["content-type"] == "image/png"
    assert served.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert served.headers["x-content-type-options"] == "nosniff"

    favicon = _upload(api, "favicon", _image("ICO", 32, 32), name="f.ico", kind="image/x-icon")
    assert favicon.status_code == 200, favicon.text
    assert favicon.json()["settings"]["assets"]["favicon"]["content_type"] == "image/x-icon"
    mark = _upload(api, "mark", _image("WEBP", 256, 256), name="m.webp", kind="image/webp")
    assert mark.json()["effective"]["mark_url"].endswith(".webp")

    removed = api.delete(f"{BRAND}/assets/logo")
    assert removed.status_code == 200
    assert "logo" not in removed.json()["settings"]["assets"]
    assert removed.json()["effective"]["logo_url"] is None
    assert TestClient(app, base_url=B_HOST).get(meta["url"]).status_code == 404


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><script/></svg>'


@pytest.mark.parametrize("slot,data,name,kind,code", [
    ("logo", SVG, "logo.svg", "image/svg+xml", 422),
    ("logo", SVG, "logo.png", "image/png", 422),  # renamed SVG
    ("logo", _image("GIF", 300, 100), "logo.png", "image/png", 422),  # renamed GIF
    ("logo", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "logo.png", "image/png", 422),  # truncated
    ("logo", _image("ICO", 64, 64), "logo.ico", "image/x-icon", 422),  # ICO: favicon only
    ("logo", _image("PNG", 1300, 200), "wide.png", "image/png", 422),
    ("logo", _image("PNG", 600, 40), "short.png", "image/png", 422),
    ("mark", _image("PNG", 200, 100), "mark.png", "image/png", 422),
    ("mark", _image("PNG", 32, 32), "mark.png", "image/png", 422),
    ("favicon", _image("PNG", 600, 600), "fav.png", "image/png", 422),
    ("logo", b"\x89PNG" + b"\x00" * (512 * 1024 + 10), "big.png", "image/png", 413),
], ids=[
    "svg", "renamed-svg", "renamed-gif", "truncated-png", "ico-logo", "too-wide", "too-short",
    "mark-not-square", "mark-too-small", "favicon-too-large", "oversized",
])
def test_unacceptable_images_are_refused(
    slot: str, data: bytes, name: str, kind: str, code: int
) -> None:
    res = _upload(_firm_admin(), slot, data, name=name, kind=kind)
    assert res.status_code == code, res.text
    assert _firm_admin().get(BRAND).json()["settings"]["assets"] == {}


def test_company_images_and_scopes() -> None:
    api = _firm_admin()
    res = _upload(api, "logo", _image("PNG", 400, 100), scope=CLIENT_B_ID)
    assert res.status_code == 200, res.text
    assert res.json()["client_id"] == CLIENT_B_ID
    assert res.json()["effective"]["logo_url"] is not None
    assert res.json()["inherited"]["logo_url"] is None
    assert _upload(api, "logo", _image("PNG", 400, 100), scope=CLIENT_O_ID).status_code == 404
    assert _upload(api, "banner", _image("PNG", 400, 100)).status_code == 422
    cleared = api.delete(f"{BRAND}/assets/logo", params={"scope": CLIENT_B_ID})
    assert cleared.json()["effective"]["logo_url"] is None


# ── Rebranded support email ───────────────────────────────────────────────────
def test_a_renamed_brand_needs_its_own_support_email() -> None:
    api = _firm_admin()
    refused = api.put(BRAND, json={"revision": 0, "product_name": "Harbour Benefits"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "brand_support_email_required"
    # Colours alone keep the platform name, so the platform helpdesk still fits.
    assert api.put(BRAND, json={"revision": 0, "primary_color": "#123456"}).status_code == 200
    # A company renamed under an unrenamed firm needs one too.
    url = f"{BRAND}/companies/{CLIENT_B_ID}"
    company = api.put(url, json={"revision": 0, "product_name": "B Co Benefits"})
    assert company.json()["detail"]["code"] == "brand_support_email_required"
    named = api.put(url, json={
        "revision": 0, "product_name": "B Co Benefits", "support_email": "hr@bco.example",
    })
    assert named.status_code == 200, named.text
