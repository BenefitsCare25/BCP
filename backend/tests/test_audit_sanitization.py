"""Audit log must redact secret-like keys before persisting."""
from __future__ import annotations

from app.core.audit import _scrub


def test_top_level_api_key_redacted() -> None:
    out = _scrub({"api_key": "sk-real-secret", "model": "claude"})
    assert out == {"api_key": "[redacted]", "model": "claude"}


def test_nested_secret_redacted() -> None:
    payload = {"ai_meta": {"client_secret": "abc", "tokens": 50}}
    out = _scrub(payload)
    assert out == {"ai_meta": {"client_secret": "[redacted]", "tokens": 50}}


def test_lists_traversed() -> None:
    out = _scrub([{"password": "hi"}, {"safe": 1}])
    assert out == [{"password": "[redacted]"}, {"safe": 1}]


def test_case_insensitive_matches() -> None:
    out = _scrub({"Authorization": "Bearer xxx", "Bearer": "x"})
    assert out["Authorization"] == "[redacted]"
    assert out["Bearer"] == "[redacted]"


def test_token_counts_not_redacted() -> None:
    out = _scrub({"input_tokens": 100, "output_tokens": 50, "tokens": 150})
    assert out == {"input_tokens": 100, "output_tokens": 50, "tokens": 150}


def test_access_token_redacted() -> None:
    out = _scrub({"access_token": "eyJabc", "refresh_token": "rt"})
    assert out == {"access_token": "[redacted]", "refresh_token": "[redacted]"}


def test_non_secret_keys_untouched() -> None:
    payload = {"display_name": "X", "rule": {"and": []}}
    assert _scrub(payload) == payload


def test_service_account_fields_redacted() -> None:
    """Both AI credential surfaces: cleartext SA key and stored ciphertext."""
    out = _scrub(
        {
            "service_account_json": '{"type":"service_account","private_key":"..."}',
            "encrypted_service_account": "gAAAAAB...",
            "location": "asia-southeast1",
        }
    )
    assert out == {
        "service_account_json": "[redacted]",
        "encrypted_service_account": "[redacted]",
        "location": "asia-southeast1",
    }
