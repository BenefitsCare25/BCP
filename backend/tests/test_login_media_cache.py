"""Only versioned public login scenery gets long-lived caching."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.spa import _cache_headers, mount_spa


@pytest.fixture
def media_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    (tmp_path / "index.html").write_text("<html>Sign in</html>", encoding="utf-8")
    directory = tmp_path / "broker" / "login"
    directory.mkdir(parents=True)
    (directory / "marina-bay-morning-v2.mp4").write_bytes(b"0123456789")
    monkeypatch.setenv("INSPRO_SPA_DIR", str(tmp_path))
    app = FastAPI()
    assert mount_spa(app, "/api/v1")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("period", ["sunrise", "morning", "afternoon", "sunset", "night"])
@pytest.mark.parametrize("extension", ["mp4", "webp"])
def test_versioned_public_login_media_is_immutable(period: str, extension: str) -> None:
    assert _cache_headers(f"broker/login/marina-bay-{period}-v2.{extension}") == {
        "Cache-Control": "public, max-age=31536000, immutable",
    }


@pytest.mark.parametrize("path", [
    "broker/login/marina-bay-v1.mp4",
    "broker/login/marina-bay-morning.mp4",
    "broker/login/marina-bay-morning-v2.webp.json",
    "broker/login/marina-bay-morning-v2.mp4/private",
    "uploads/marina-bay-morning-v2.mp4",
    "portal/login/scene-seedance-light.mp4",
])
def test_other_public_files_keep_the_existing_cache_policy(path: str) -> None:
    assert _cache_headers(path) == {"Cache-Control": "public, max-age=3600"}


def test_video_range_download_retains_immutable_caching(media_client: TestClient) -> None:
    response = media_client.get(
        "/broker/login/marina-bay-morning-v2.mp4", headers={"Range": "bytes=2-5"},
    )
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_missing_video_fallback_does_not_cache_the_app_shell(media_client: TestClient) -> None:
    response = media_client.get("/broker/login/marina-bay-night-v2.mp4")
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
