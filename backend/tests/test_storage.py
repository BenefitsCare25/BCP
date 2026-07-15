"""LocalStorage backend: hashing, confinement, round-trip."""
from __future__ import annotations

import hashlib
import io

import pytest

from app.core.storage import LocalStorage, document_path


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path)


def test_save_read_delete_roundtrip(storage: LocalStorage):
    payload = b"%PDF-1.4 receipt bytes" * 100
    blob = storage.save(io.BytesIO(payload), "firm/client/claim/c1/d1.pdf")
    assert blob.size_bytes == len(payload)
    assert blob.sha256 == hashlib.sha256(payload).hexdigest()
    assert storage.read(blob.path) == payload
    storage.delete(blob.path)
    with pytest.raises(FileNotFoundError):
        storage.read(blob.path)


def test_delete_missing_is_noop(storage: LocalStorage):
    storage.delete("firm/client/claim/none/gone.pdf")  # must not raise


def test_identical_bytes_same_hash(storage: LocalStorage):
    a = storage.save(io.BytesIO(b"same"), "x/a.pdf")
    b = storage.save(io.BytesIO(b"same"), "x/b.pdf")
    assert a.sha256 == b.sha256


def test_path_traversal_rejected(storage: LocalStorage):
    with pytest.raises(ValueError):
        storage.read("../../etc/passwd")
    with pytest.raises(ValueError):
        storage.save(io.BytesIO(b"x"), "../escape.pdf")


def test_document_path_shape():
    path = document_path("firm1", "client1", "claim", "c1", "d1", ".pdf")
    assert path == "firm1/client1/claim/c1/d1.pdf"
    assert document_path(None, "c", "claim", "e", "d", ".png").startswith("nofirm/")
