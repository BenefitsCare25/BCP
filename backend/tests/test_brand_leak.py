"""Brand isolation: one firm's brand never shows on another firm's host.

Firm A (`firm-a.localhost`) and firm B (`firm-b.localhost`) each have a brand
with images; A's company "acme" overrides it. Firm O is the platform owner
(served on "testserver") and has no brand, so it shows the default. Every
surface that carries a brand is checked: `/public/site`, the brand images, the
app shell (`index.html`) and the web manifest.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api.v1.public import brand_out
from app.core.spa import DEFAULT_MANIFEST_NAME, brand_index, mount_spa
from app.core.tenant_resolution import FirmResolutionMiddleware
from app.db.session import SessionLocal
from app.main import app
from app.models import BrandProfile, BrokerFirm, Client
from app.services.brand import DEFAULT_BRAND, Brand
from app.services.brand_assets import CleanAsset, clean_asset, read_asset, store_asset

OWNER_ID = "00000000-0000-0000-0000-00000000c0a1"
FIRM_A_ID = "00000000-0000-0000-0000-00000000c0b1"
FIRM_B_ID = "00000000-0000-0000-0000-00000000c0c1"
CLIENT_A_ID = "00000000-0000-0000-0000-00000000c0b2"
CLIENT_B_ID = "00000000-0000-0000-0000-00000000c0c2"
A_HOST = "http://firm-a.localhost"
B_HOST = "http://firm-b.localhost"
O_HOST = "http://testserver"
INDEX_SOURCE = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

A_STRINGS = ("Alpha Assurance", "Alpha", "#0a7d3b", "#123abc", "help@alpha.example",
             "Acme Alpha Portal", "#ff8800")
B_STRINGS = ("Bravo Benefits", "Bravo", "#5b2a86", "#e0b000", "help@bravo.example")

ASSETS: dict[str, CleanAsset] = {}


def _png(slot: str, size: tuple[int, int], color: tuple[int, int, int]) -> CleanAsset:
    out = BytesIO()
    Image.new("RGBA", size, (*color, 255)).save(out, format="PNG")
    return clean_asset(slot, out.getvalue())


@pytest.fixture(scope="module", autouse=True)
def _setup_db() -> Iterator[None]:
    ASSETS.update({
        "a_logo": _png("logo", (600, 200), (10, 125, 59)),
        "a_mark": _png("mark", (256, 256), (10, 125, 60)),
        "a_favicon": _png("favicon", (32, 32), (10, 125, 61)),
        "acme_logo": _png("logo", (400, 100), (255, 136, 0)),
        "b_mark": _png("mark", (512, 512), (91, 42, 134)),
        "b_favicon": _png("favicon", (48, 48), (91, 42, 135)),
    })
    for key, asset in ASSETS.items():
        store_asset(FIRM_A_ID if key.startswith(("a_", "acme")) else FIRM_B_ID, asset)
    with SessionLocal() as s:
        s.add(BrokerFirm(id=OWNER_ID, name="Owner", slug="owner", is_platform_owner=True))
        s.add(BrokerFirm(id=FIRM_A_ID, name="Firm A", slug="firm-a"))
        s.add(BrokerFirm(id=FIRM_B_ID, name="Firm B", slug="firm-b"))
        s.flush()
        s.add(Client(id=CLIENT_A_ID, name="Acme", broker_firm_id=FIRM_A_ID, slug="acme"))
        s.add(Client(id=CLIENT_B_ID, name="Beta", broker_firm_id=FIRM_B_ID, slug="beta"))
        s.flush()
        s.add(BrandProfile(
            broker_firm_id=FIRM_A_ID, scope_key="firm", product_name="Alpha Assurance",
            short_name="Alpha", primary_color="#0a7d3b", accent_color="#123abc",
            support_email="help@alpha.example",
            assets={"logo": ASSETS["a_logo"].meta(), "mark": ASSETS["a_mark"].meta(),
                    "favicon": ASSETS["a_favicon"].meta()},
        ))
        s.add(BrandProfile(
            broker_firm_id=FIRM_A_ID, client_id=CLIENT_A_ID, scope_key=CLIENT_A_ID,
            product_name="Acme Alpha Portal", primary_color="#ff8800",
            assets={"logo": ASSETS["acme_logo"].meta()},
        ))
        s.add(BrandProfile(
            broker_firm_id=FIRM_B_ID, scope_key="firm", product_name="Bravo Benefits",
            short_name="Bravo", primary_color="#5b2a86", accent_color="#e0b000",
            support_email="help@bravo.example",
            assets={"mark": ASSETS["b_mark"].meta(), "favicon": ASSETS["b_favicon"].meta()},
        ))
        s.commit()
    yield


def _a_ids() -> list[str]:
    return [a.id for k, a in ASSETS.items() if k.startswith(("a_", "acme"))]


def _b_ids() -> list[str]:
    return [a.id for k, a in ASSETS.items() if k.startswith("b_")]


def _assert_absent(text: str, needles: tuple[str, ...] | list[str]) -> None:
    for needle in needles:
        assert needle not in text, f"{needle!r} leaked"


# ── /public/site ──────────────────────────────────────────────────────────────
def _site(host: str, company: str | None = None) -> dict[str, object]:
    params = {"company": company} if company else None
    res = TestClient(app, base_url=host).get("/api/v1/public/site", params=params)
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "public, max-age=60"
    return res.json()  # type: ignore[no-any-return]


def test_public_site_shows_only_the_hosts_brand() -> None:
    b_site = _site(B_HOST)
    brand = b_site["brand"]
    assert isinstance(brand, dict)
    assert (brand["product_name"], brand["primary_color"]) == ("Bravo Benefits", "#5b2a86")
    assert brand["favicon_url"].endswith(ASSETS["b_favicon"].id)
    assert brand["logo_url"] is None  # B has no logo; A's is never borrowed
    assert brand["platform_name"] == "Inspro"
    _assert_absent(json.dumps(b_site), [*A_STRINGS, *_a_ids()])

    # A's company slug on B's host is not a company of B: B's firm brand.
    assert _site(B_HOST, "acme")["brand"] == brand
    assert _site(B_HOST, "beta")["brand"] == brand  # B's company, no override

    a_brand = _site(A_HOST)["brand"]
    acme = _site(A_HOST, "acme")["brand"]
    assert isinstance(a_brand, dict) and isinstance(acme, dict)
    assert a_brand["product_name"] == "Alpha Assurance"
    assert (acme["product_name"], acme["primary_color"]) == ("Acme Alpha Portal", "#ff8800")
    assert acme["logo_url"].endswith(ASSETS["acme_logo"].id)
    assert acme["accent_color"] == "#123abc"  # inherited from firm A
    assert _site(A_HOST, "beta")["brand"] == a_brand
    assert _site(A_HOST, "no-such-company")["brand"] == a_brand
    _assert_absent(json.dumps(_site(A_HOST, "acme")), [*B_STRINGS, *_b_ids()])


def test_a_host_without_a_brand_shows_the_default() -> None:
    owner = _site(O_HOST, "acme")
    assert owner["brand"] == brand_out(DEFAULT_BRAND).model_dump()
    _assert_absent(json.dumps(owner), [*A_STRINGS, *B_STRINGS, *_a_ids(), *_b_ids()])


# ── Brand images ──────────────────────────────────────────────────────────────
def _asset(host: str, asset_id: str) -> int:
    return TestClient(app, base_url=host).get(f"/api/v1/public/brand-assets/{asset_id}").status_code


def test_brand_images_are_served_only_on_their_firms_hosts() -> None:
    for asset_id in _a_ids():
        res = TestClient(app, base_url=A_HOST).get(f"/api/v1/public/brand-assets/{asset_id}")
        assert res.status_code == 200
        assert res.content == read_asset(FIRM_A_ID, asset_id)
        assert _asset(B_HOST, asset_id) == 404
        assert _asset(O_HOST, asset_id) == 404
    for asset_id in _b_ids():
        assert _asset(B_HOST, asset_id) == 200
        assert _asset(A_HOST, asset_id) == 404
    for bad in ("../" + _a_ids()[0], _a_ids()[0].upper(), "a" * 64 + ".svg", "x.png"):
        assert _asset(A_HOST, bad) == 404


def test_a_copied_asset_reference_never_reads_another_firms_bytes() -> None:
    """A brand row naming another firm's image id (copied or tampered) reads
    from its own firm's storage only, where the bytes do not exist."""
    stolen = ASSETS["a_logo"]
    with SessionLocal() as s:
        row = s.query(BrandProfile).filter_by(broker_firm_id=FIRM_B_ID, scope_key="firm").one()
        row.assets = {**(row.assets or {}), "logo": stolen.meta()}
        s.commit()
    try:
        assert _asset(B_HOST, stolen.id) == 404
    finally:
        with SessionLocal() as s:
            row = s.query(BrandProfile).filter_by(broker_firm_id=FIRM_B_ID, scope_key="firm").one()
            row.assets = {k: v for k, v in (row.assets or {}).items() if k != "logo"}
            s.commit()


# ── App shell and manifest ────────────────────────────────────────────────────
@pytest.fixture
def shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, TestClient]]:
    if not INDEX_SOURCE.is_file():
        pytest.skip("frontend/index.html is not available")
    (tmp_path / "index.html").write_text(INDEX_SOURCE.read_text(encoding="utf-8"), "utf-8")
    (tmp_path / "site.webmanifest").write_text('{"name": "static file"}', "utf-8")
    monkeypatch.setenv("INSPRO_SPA_DIR", str(tmp_path))
    spa = FastAPI()
    spa.add_middleware(FirmResolutionMiddleware)
    assert mount_spa(spa, "/api/v1")
    yield {name: TestClient(spa, base_url=host)
           for name, host in (("a", A_HOST), ("b", B_HOST), ("owner", O_HOST))}


def test_app_shell_is_branded_for_the_host(shell: dict[str, TestClient]) -> None:
    source = INDEX_SOURCE.read_text(encoding="utf-8")
    owner = shell["owner"].get("/")
    assert owner.status_code == 200
    assert owner.text == source  # no brand: exactly the built file
    assert owner.headers["cache-control"] == "no-cache, must-revalidate"

    for path in ("/", "/portal/claims", "/index.html"):
        page = shell["b"].get(path)
        assert page.status_code == 200
        text = page.text
        assert "<title>Bravo Benefits</title>" in text
        assert '<meta name="theme-color" content="#5b2a86"' in text
        assert f'href="/api/v1/public/brand-assets/{ASSETS["b_favicon"].id}"' in text
        touch = f'rel="apple-touch-icon" href="/api/v1/public/brand-assets/{ASSETS["b_mark"].id}"'
        assert touch in text
        assert '<link rel="manifest" href="/site.webmanifest" />' in text
        assert "/favicon.ico" not in text
        _assert_absent(text, [*A_STRINGS, *_a_ids()])

    a_page = shell["a"].get("/hr/sign-in").text
    assert "<title>Alpha Assurance</title>" in a_page
    _assert_absent(a_page, [*B_STRINGS, *_b_ids()])


def test_manifest_is_generated_per_host(shell: dict[str, TestClient]) -> None:
    owner = shell["owner"].get("/site.webmanifest")
    assert owner.status_code == 200
    assert owner.headers["cache-control"] == "no-cache"
    assert owner.headers["content-type"].startswith("application/manifest+json")
    assert owner.json() == {
        "name": DEFAULT_MANIFEST_NAME,
        "short_name": "Inspro",
        "icons": [
            {"src": "/icon-192.png", "type": "image/png", "sizes": "192x192"},
            {"src": "/icon-512.png", "type": "image/png", "sizes": "512x512"},
        ],
        "theme_color": "#c11a2b",
        "background_color": "#faf7f7",
        "display": "standalone",
        "start_url": "/",
    }

    b = shell["b"].get("/site.webmanifest")
    manifest = b.json()
    assert (manifest["name"], manifest["short_name"]) == ("Bravo Benefits", "Bravo")
    assert manifest["theme_color"] == "#5b2a86"
    assert manifest["icons"] == [{
        "src": f"/api/v1/public/brand-assets/{ASSETS['b_mark'].id}",
        "type": "image/png", "sizes": "512x512",
    }]
    _assert_absent(b.text, [*A_STRINGS, *_a_ids()])
    a = shell["a"].get("/site.webmanifest")
    assert a.json()["name"] == "Alpha Assurance"
    _assert_absent(a.text, [*B_STRINGS, *_b_ids()])


def test_shell_values_are_escaped() -> None:
    markup = "<head><title>Inspro</title><meta name=\"theme-color\" content=\"#c11a2b\" /></head>"
    hostile = Brand(product_name='</title><script>alert("x")</script>\\1', firm_id="f")
    out = brand_index(markup, hostile)
    assert "<script>" not in out
    assert "&lt;/title&gt;&lt;script&gt;" in out
    assert out.count("<title>") == 1 and "\\1" in out


def test_a_custom_palette_paints_the_first_frame() -> None:
    markup = '<html lang="en"><head><title>Inspro</title></head></html>'
    branded = brand_index(markup, Brand(primary_color="#5b2a86", firm_id="f"))
    assert '<html lang="en" data-brand="custom" style="--brand-primary:#5b2a86;' in branded
    assert "--brand-primary-foreground:#ffffff" in branded
    # A firm brand that keeps the default colours leaves <html> as built.
    named = brand_index(markup, Brand(product_name="Named", firm_id="f"))
    assert '<html lang="en">' in named and "data-brand" not in named
