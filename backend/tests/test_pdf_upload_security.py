"""PDF dependency upgrades must preserve claim upload safety checks; uploads are
size-capped before parsing and malware scans are bounded in parallelism."""
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.core.settings import get_settings
from app.core.uploads import MULTIPART_OVERHEAD_BYTES, RequestSizeLimitMiddleware
from app.main import app
from app.services import file_security
from app.services.file_security import _inspect_pdf

_MIB = 1024 * 1024


def test_plain_pdf_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "receipt.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.write(path)

    _inspect_pdf(path)


@pytest.mark.parametrize("kind", ["encrypted", "scripted", "damaged"])
def test_unsafe_pdf_is_rejected(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "unsafe.pdf"
    if kind == "damaged":
        path.write_bytes(b"%PDF-1.4\ninvalid PDF document\n")
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        if kind == "encrypted":
            writer.encrypt("synthetic-test-password")
        else:
            writer.add_js("app.alert('synthetic test');")
        writer.write(path)

    with pytest.raises(HTTPException) as rejected:
        _inspect_pdf(path)
    assert rejected.value.status_code == 422
    assert rejected.value.detail["code"] == "unsafe_document"


# ── Request size cap ─────────────────────────────────────────────────────────


def test_declared_oversized_upload_is_refused_before_parsing() -> None:
    """The real app refuses on Content-Length alone, with the security headers
    every response carries."""
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/employees/upload",
            content=b"--x--",
            headers={
                "Content-Type": "multipart/form-data; boundary=x",
                "Content-Length": str(50 * _MIB + MULTIPART_OVERHEAD_BYTES + 1),
            },
        )

    assert res.status_code == 413
    assert res.json() == {"detail": "Uploads are limited to 50 MB per request."}
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in res.headers


@pytest.fixture
def body_probe() -> TestClient:
    probe = FastAPI()

    @probe.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    probe.add_middleware(RequestSizeLimitMiddleware, max_upload_bytes=_MIB)
    return TestClient(probe)


def _chunks(total: int) -> Iterator[bytes]:
    sent = 0
    while sent < total:
        piece = min(64 * 1024, total - sent)
        sent += piece
        yield b"x" * piece


def test_chunked_body_is_cut_off_at_the_cap(body_probe: TestClient) -> None:
    """A chunked body declares no length, so the cap is enforced while reading."""
    res = body_probe.post("/echo", content=_chunks(3 * _MIB))

    assert res.status_code == 413
    assert res.json() == {"detail": "Uploads are limited to 1 MB per request."}


def test_body_within_the_cap_is_untouched(body_probe: TestClient) -> None:
    within = _MIB + MULTIPART_OVERHEAD_BYTES
    assert body_probe.post("/echo", content=_chunks(within)).json() == {"received": within}
    assert body_probe.post("/echo", content=b"x" * 10).json() == {"received": 10}


# ── Scanner concurrency ──────────────────────────────────────────────────────


@pytest.fixture
def scanner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    settings = replace(get_settings(), document_scan_command="clamscan")
    monkeypatch.setattr(file_security, "get_settings", lambda: settings)
    monkeypatch.setattr(file_security, "_SCAN_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(file_security, "SCAN_SLOT_WAIT_SECONDS", 0.05)
    path = tmp_path / "receipt.pdf"
    path.write_bytes(b"%PDF-1.4 receipt")
    return path


def test_busy_scanner_answers_503_instead_of_spawning(
    scanner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no scanner may start while every slot is taken")

    monkeypatch.setattr(file_security.subprocess, "run", must_not_run)
    assert file_security._SCAN_SLOTS.acquire(timeout=1)
    try:
        with pytest.raises(HTTPException) as busy:
            file_security._malware_scan(scanner)
    finally:
        file_security._SCAN_SLOTS.release()

    assert busy.value.status_code == 503
    assert busy.value.detail == {
        "code": "document_scanner_busy",
        "message": "The document scanner is busy. Try again shortly.",
    }


def test_scanner_slot_is_released_after_every_outcome(
    scanner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = iter([
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.TimeoutExpired(cmd="clamscan", timeout=1),
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    ])

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(file_security.subprocess, "run", fake_run)
    file_security._malware_scan(scanner)  # clean
    file_security._malware_scan(scanner)  # timed out; scanning not required here
    with pytest.raises(HTTPException) as infected:
        file_security._malware_scan(scanner)
    assert infected.value.status_code == 422
    # With one slot, this only runs if all three earlier scans gave it back.
    file_security._malware_scan(scanner)


@pytest.mark.parametrize(
    ("raw", "expected"), [("", 2), ("3", 3), ("0", 1), ("999", 16), ("many", 2)]
)
def test_scan_concurrency_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv("INSPRO_SCAN_CONCURRENCY", raw)
    assert file_security._scan_concurrency() == expected
