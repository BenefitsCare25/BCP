"""LocalStorage backend: hashing, confinement, round-trip, tenant key scope."""
from __future__ import annotations

import hashlib
import io
import logging
from types import SimpleNamespace

import pytest

from app.core.storage import (
    LocalStorage,
    StorageScopeError,
    assert_key_in_scope,
    company_firm_id,
    document_path,
)


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


@pytest.mark.parametrize("firm", ["", "nofirm"])
def test_document_path_requires_the_owning_firm(firm: str):
    """A firm-less writer (system admin) used to file blobs under `nofirm/`,
    outside every firm. Keys now come from the resource's own firm."""
    with pytest.raises(ValueError, match="broker firm"):
        document_path(firm, "client1", "claim", "c1", "d1", ".pdf")


@pytest.mark.parametrize(
    "segments",
    [
        ("firm/x", "client1", "claim", "c1", "d1"),
        ("firm1", "..", "claim", "c1", "d1"),
        ("firm1", "client1", "claim", "c1/../../x", "d1"),
        ("firm1", "client1", "claim", "c1", "a\\b"),
    ],
)
def test_document_path_rejects_ambiguous_segments(segments: tuple[str, ...]):
    with pytest.raises(ValueError, match="segment"):
        document_path(*segments, ".pdf")


def test_key_scope_accepts_own_and_legacy_keys():
    assert_key_in_scope("firm1/client1/claim/c1/d1.pdf", "firm1", "client1")
    # Blobs written before keys followed the resource still resolve — for the
    # company named in the key, under the legacy firm-less segment.
    assert_key_in_scope("nofirm/client1/claim/c1/d1.pdf", "firm1", "client1")


@pytest.mark.parametrize(
    "key",
    [
        "firm2/client1/claim/c1/d1.pdf",  # another firm
        "firm1/client2/claim/c1/d1.pdf",  # another company of the same firm
        "nofirm/client2/claim/c1/d1.pdf",  # legacy key of another company
        "firm1/client1/../../firm2/client9/x.pdf",
        "firm1/client1//x.pdf",
        "firm1\\client1\\x.pdf",
        "firm1/client1",
        "/firm1/client1/claim/x.pdf",
    ],
)
def test_key_scope_refuses_other_tenants_and_traversal(key: str):
    with pytest.raises(StorageScopeError):
        assert_key_in_scope(key, "firm1", "client1")


def test_key_scope_needs_a_firm_and_company():
    with pytest.raises(ValueError, match="firm and company"):
        assert_key_in_scope("nofirm/client1/claim/c1/d1.pdf", "", "client1")


def test_company_firm_id_reads_the_company_not_the_actor():
    companies = {"client1": SimpleNamespace(broker_firm_id="firm1")}
    db = SimpleNamespace(get=lambda _model, client_id: companies.get(client_id))

    assert company_firm_id(db, "client1") == "firm1"  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not registered"):
        company_firm_id(db, "missing")  # type: ignore[arg-type]


def test_a_refused_download_key_is_logged_as_a_scope_violation(caplog):
    """Download endpoints answer a refused key as "not found", so the log line is
    the only record that a row reached outside its tenant."""
    from app.services.claims import assert_document_scope

    assert_document_scope("firm1/client1/claim/c1/d1.pdf", "firm1", "client1")
    assert_document_scope("nofirm/client1/claim/c1/d1.pdf", "firm1", "client1")
    assert not caplog.records

    with caplog.at_level(logging.ERROR, logger="app.services.claims"):
        with pytest.raises(StorageScopeError):
            assert_document_scope("firm2/client1/claim/c1/d1.pdf", "firm1", "client1")
    (record,) = caplog.records
    assert record.error_code == "storage_scope_violation"  # type: ignore[attr-defined]


def test_portal_card_artwork_is_read_where_the_broker_surface_files_it():
    """`panel_cards._artwork_scope` files a company card under its company and a
    library card under its firm's library; the member read checks the same."""
    from app.api.v1.portal import _card_artwork_scope

    companies = {
        "client1": SimpleNamespace(broker_firm_id="firm1"),
        "client2": SimpleNamespace(broker_firm_id="firm2"),
    }
    db = SimpleNamespace(get=lambda _model, client_id: companies.get(client_id))
    company_card = SimpleNamespace(client_id="client1")
    library_card = SimpleNamespace(client_id=None)

    assert _card_artwork_scope(db, company_card, "client1") == ("firm1", "client1")
    assert _card_artwork_scope(db, library_card, "client2") == ("firm2", "library")
