"""Symmetric encryption for tenant-scoped secrets (BYOK API keys, future webhooks).

Why this module exists separately from `settings.py`: the master key needs to
be readable by ORM-level code that runs before request scope (e.g. a
background job decrypting credentials). Coupling to FastAPI's `get_settings()`
would force those callers through HTTP-shaped infra.

Master keys come from env:

- ``INSPRO_AI_KEY_ENCRYPTION_KEY``           — current (required)
- ``INSPRO_AI_KEY_ENCRYPTION_KEY_PREVIOUS``  — optional, for rotation

Both must be valid 32-byte urlsafe-base64 strings (i.e. ``Fernet`` keys). On
read we use ``MultiFernet([current, previous])`` so old rows decrypt cleanly
while every new encryption uses ``current``. Operationally:

1. Generate a new key offline (``Fernet.generate_key()``).
2. Deploy with ``INSPRO_AI_KEY_ENCRYPTION_KEY_PREVIOUS`` = old,
   ``INSPRO_AI_KEY_ENCRYPTION_KEY`` = new.
3. Wait until every BYOK row has been re-encrypted (PUT bumps it
   opportunistically; or write a one-off re-encrypt script).
4. Remove the previous key.

The cache is keyed by both env vars together so a test that monkeypatches the
key sees the new value once it calls ``reset_crypto_cache_for_tests()``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)

_ENV_CURRENT = "INSPRO_AI_KEY_ENCRYPTION_KEY"
_ENV_PREVIOUS = "INSPRO_AI_KEY_ENCRYPTION_KEY_PREVIOUS"
# Cap: a leaked secret should not be reconstructible from the masked tail.
_MASK_TAIL_CHARS = 4


class MasterKeyError(RuntimeError):
    """Raised when the master encryption key is missing or malformed."""


@dataclass(frozen=True)
class _Keys:
    current: Fernet
    multi: MultiFernet


def _parse(name: str, value: str) -> Fernet:
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, InvalidToken) as exc:
        raise MasterKeyError(
            f"{name} is not a valid Fernet key (expected 32-byte urlsafe-base64). "
            f"Generate one with: python -c \"from cryptography.fernet import Fernet; "
            f"print(Fernet.generate_key().decode())\""
        ) from exc


@lru_cache(maxsize=1)
def _load_keys() -> _Keys:
    current_raw = os.environ.get(_ENV_CURRENT, "").strip()
    if not current_raw:
        # Bake a fresh suggestion into the error so the operator can paste it.
        suggested = Fernet.generate_key().decode("ascii")
        raise MasterKeyError(
            f"{_ENV_CURRENT} is required for BYOK encryption. "
            f"Add this to your .env (or Key Vault for prod):\n"
            f"  {_ENV_CURRENT}={suggested}"
        )
    current = _parse(_ENV_CURRENT, current_raw)
    keys: list[Fernet] = [current]

    previous_raw = os.environ.get(_ENV_PREVIOUS, "").strip()
    if previous_raw:
        keys.append(_parse(_ENV_PREVIOUS, previous_raw))

    return _Keys(current=current, multi=MultiFernet(keys))


def validate_master_key() -> None:
    """Boot-time check — call from `settings.get_settings()` so a missing or
    malformed master key kills the process before any request lands.
    """
    _load_keys()


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt under the current key only."""
    return _load_keys().current.encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt; transparently tries previous key for rotation overlap."""
    return _load_keys().multi.decrypt(ciphertext).decode("utf-8")


def needs_reencryption(ciphertext: bytes) -> bool:
    """True if the row was encrypted with the previous key.

    Lets the PUT endpoint opportunistically re-write under the current key.
    Returns False on any decrypt failure — callers handle that separately.
    """
    keys = _load_keys()
    try:
        # Try current first; if it accepts the token, no rotation needed.
        keys.current.decrypt(ciphertext)
        return False
    except InvalidToken:
        try:
            keys.multi.decrypt(ciphertext)
            return True
        except InvalidToken:
            return False


def fingerprint(plaintext: str) -> str:
    """First 16 hex chars of sha256 — a stable, non-reversible identifier.

    Lets us detect "did the key change?" in audit rows without storing or
    decrypting the cleartext.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:16]


def mask_tail(plaintext: str) -> str:
    """Render a key for UI display: ``••••XXXX``.

    Returns ``••••`` for empty / very-short strings so leaking a 4-char API
    token can't reveal the whole thing.
    """
    if len(plaintext) <= _MASK_TAIL_CHARS:
        return "•" * 4
    return "•" * 4 + plaintext[-_MASK_TAIL_CHARS:]


def generate_master_key() -> str:
    """Return a fresh Fernet key string. Used by the boot-time error message
    and by tests that need a stable per-process master."""
    return Fernet.generate_key().decode("ascii")


def reset_crypto_cache_for_tests() -> None:
    """Drop the cached `_Keys` so a test can monkeypatch the env vars and
    have the next call pick them up."""
    _load_keys.cache_clear()


# `secrets.compare_digest` is re-exported as a convenience for callers
# comparing fingerprints — we don't want to leak whether a fingerprint
# matched via timing.
constant_time_eq = secrets.compare_digest

__all__ = [
    "MasterKeyError",
    "constant_time_eq",
    "decrypt_secret",
    "encrypt_secret",
    "fingerprint",
    "generate_master_key",
    "mask_tail",
    "needs_reencryption",
    "reset_crypto_cache_for_tests",
    "validate_master_key",
]
