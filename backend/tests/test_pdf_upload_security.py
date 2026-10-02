"""PDF dependency upgrades must preserve claim upload safety checks."""
from pathlib import Path

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from app.services.file_security import _inspect_pdf


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
