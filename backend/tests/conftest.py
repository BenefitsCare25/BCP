"""Test-wide fixtures and env setup.

We set `INSPRO_AI_KEY_ENCRYPTION_KEY` to a fixed (insecure) value before any
test module imports `app.*`. Production settings refuses to boot without it,
so every test file would otherwise need its own setdefault — this guarantees
one canonical key for the whole suite.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

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

# No live currency lookups from the suite. `services/fx.py` reaches out to
# Frankfurter, and a test that quietly does so is a test whose result depends on
# somebody else's uptime — it would pass on a laptop, hang for ~10s per foreign
# claim behind a proxy, and fail in an offline CI runner. Off here means every
# foreign claim in the suite lands `unavailable`, which is exactly the degraded
# path the flag exists to model, so the ordinary tests exercise it for free.
# `tests/test_claim_fx.py` turns it back on against a stubbed transport.
os.environ.setdefault("INSPRO_FX_ENABLED", "0")

# ── One database for the suite, reset between modules ────────────────────────
# 47 test modules each set INSPRO_DATABASE_URL at their own import time, but
# `app/db/session.py` binds `engine` ONCE at first import. So whichever module
# pytest imported first silently owned the database for the whole run and every
# other module's setting was a no-op — they were all sharing one DB without
# saying so. Isolation therefore depended on collection order, which differs
# between platforms: the suite passed on Windows and failed on Linux CI with
# another module's employees (FX-*) showing up in the match-results tests.
#
# Pin the path here, before any test module is imported, and give each module a
# freshly-created schema below. Modules keep their own `_setup_db` fixtures
# (create_all + seed); those now run against an empty database whatever the order.
_SUITE_DB = Path(__file__).parent / "_test_suite.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{_SUITE_DB}"

# Import app.db.session HERE, while the env var above is still the one in
# effect. Binding the engine from conftest is what makes the pin stick: test
# modules reassign INSPRO_DATABASE_URL at their own import time, and the
# alphabetically-first one would otherwise win the race and own the database.
# Those reassignments are now no-ops — which is what they always were, just
# non-deterministically so.
import app.db.session  # noqa: E402,F401


@pytest.fixture(scope="module", autouse=True)
def _reset_module_db():
    """Drop and recreate every table before each test module runs.

    Autouse + module-scoped, declared in conftest so it is set up before a
    module's own `_setup_db`. This is what makes the suite order-independent.
    """
    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def pytest_sessionstart(session):
    """Start from no database files at all.

    Clears the suite DB (so a stale file can't mask a seeding bug) and any
    leftover per-module `_test_*.db` from before the pinning above — those are
    now vestigial, but each module still tries to `unlink()` its own path in
    `_setup_db`, which on Windows raises PermissionError if the file lingers.
    """
    for stale in Path(__file__).parent.glob("_test_*.db*"):
        try:
            stale.unlink()
        except OSError:
            # Locked by another process — the per-module unlink will report it.
            pass
