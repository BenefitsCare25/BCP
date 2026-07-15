"""Test-wide fixtures and env setup.

We set `INSPRO_AI_KEY_ENCRYPTION_KEY` to a fixed (insecure) value before any
test module imports `app.*`. Production settings refuses to boot without it,
so every test file would otherwise need its own setdefault — this guarantees
one canonical key for the whole suite.
"""
from __future__ import annotations

import os

# `setdefault` so a CI job that needs to test the "missing master key" path
# can still override by exporting an empty string explicitly... no, setdefault
# would skip — use a real check.
if not os.environ.get("INSPRO_AI_KEY_ENCRYPTION_KEY", "").strip():
    # Fixed Fernet-shaped value; deterministic so encrypted rows persist
    # across pytest reloads within the same session.
    os.environ["INSPRO_AI_KEY_ENCRYPTION_KEY"] = (
        "qf6vWl5xnphSv6F-W0AYE_OvcOhKDjsBPiNzcfRkxF0="
    )

# Fixed portal JWT secret so member tokens stay verifiable across
# `clear_settings_cache()` calls (unset would mint a fresh ephemeral secret
# per cache generation and invalidate previously issued test tokens).
os.environ.setdefault(
    "INSPRO_PORTAL_JWT_SECRET",
    "test-portal-secret-0123456789abcdef0123456789abcdef",
)

# The portal OTP endpoints have tight per-IP limits (5/minute) that a test
# module exceeds in seconds; per-account cooldowns are covered by their own
# unit tests, so the SlowAPI layer is switched off suite-wide.
os.environ.setdefault("INSPRO_RATE_LIMIT_ENABLED", "0")
