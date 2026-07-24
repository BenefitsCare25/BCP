"""RFC 6238 TOTP + recovery codes — stdlib only (no extra dependency).

Secrets are base32 strings; at rest they are Fernet-encrypted via
`core.crypto`. Verification returns the matched 30-second step so the caller
can persist it and reject replay of the same step (`AuthMfa.last_used_step`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

_STEP_SECONDS = 30
_DIGITS = 6
_SECRET_BYTES = 20  # 160-bit, per RFC 4226 recommendation


def generate_secret() -> str:
    """A fresh base32 TOTP secret (no padding, uppercase)."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, account: str, issuer: str = "Inspro") -> str:
    """otpauth:// URI for QR enrolment."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={quote(issuer)}&digits={_DIGITS}&period={_STEP_SECONDS}"
    )


def current_step(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // _STEP_SECONDS)


def _hotp(secret_b32: str, counter: int) -> str:
    # Restore base32 padding (b32decode needs a multiple of 8).
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**_DIGITS)).zfill(_DIGITS)


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    at: float | None = None,
    window: int = 1,
    after_step: int | None = None,
) -> int | None:
    """Return the matched step if `code` is valid, else None.

    Checks ±`window` steps to tolerate clock skew. `after_step` enforces the
    replay guard: a step ≤ the last-used step is rejected even if the code is
    arithmetically valid.
    """
    code = (code or "").strip()
    if not code.isdigit() or len(code) != _DIGITS:
        return None
    base = current_step(at)
    for delta in range(-window, window + 1):
        step = base + delta
        if step < 0:
            continue
        if after_step is not None and step <= after_step:
            continue
        if hmac.compare_digest(_hotp(secret_b32, step), code):
            return step
    return None


def _recovery_code() -> str:
    return "-".join(secrets.token_hex(2) for _ in range(3))


def generate_recovery_codes(n: int = 10) -> list[str]:
    """Human-friendly single-use recovery codes (shown once, then hashed)."""
    return [_recovery_code() for _ in range(n)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
