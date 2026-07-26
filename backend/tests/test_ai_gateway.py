"""AI gateway — cache + breaker + budget integration tests."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

TEST_DB = Path(__file__).parent / "_test_ai_gateway.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
# Force the gateway to think AI is configured during tests.
os.environ.setdefault("INSPRO_AI_PROVIDER", "vertex")
os.environ.setdefault("VERTEX_PROJECT", "test-project")

from sqlalchemy import select  # noqa: E402

from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    AISpendLog,
    Client,
    PlatformAISetting,
)
from app.models.platform_ai_settings import SINGLETON_ID  # noqa: E402
from app.schemas.api import AttributeSchemaOut  # noqa: E402
from app.schemas.rule import RuleEnvelope  # noqa: E402
from app.services import ai_breaker, ai_cache  # noqa: E402
from app.services.ai_gateway import (  # noqa: E402
    AIBudgetExceededError,
    AIPlatformBudgetExceededError,
    _concurrency_state,
    _slot,
    generate_rule_for_category,
    month_to_date_tokens,
    platform_month_to_date_tokens,
    record_platform_usage,
)
from scripts.seed_demo import seed  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_singletons():
    ai_cache.reset_cache_for_tests()
    ai_breaker.reset_breaker_for_tests()


def _schema() -> list[AttributeSchemaOut]:
    return [
        AttributeSchemaOut(
            id="x",
            client_id=None,
            attribute_id="grade",
            display_name="Grade",
            data_type="integer",
            is_required=False,
            is_pii=False,
        )
    ]


def _fake_envelope_meta(input_tokens: int = 100, output_tokens: int = 50):
    env = RuleEnvelope(
        rule={">=": ["grade", "15"]},
        human_readable="Grade 15+",
        confidence=0.8,
        needs_review=True,
    )
    meta = {
        "provider": "anthropic",
        "model": "claude-test",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning": "test",
    }
    return env, meta


def test_first_call_invokes_provider_logs_spend() -> None:
    db = SessionLocal()
    try:
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as m:
            result = generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Grade 15 and above",
                schema=_schema(),
            )
        db.commit()
        assert m.call_count == 1
        assert result.cache_hit is False
        q = select(AISpendLog).where(AISpendLog.client_id == DEMO_CLIENT_ID)
        rows = db.execute(q).scalars().all()
        assert len(rows) == 1
        assert rows[0].input_tokens == 100 and rows[0].output_tokens == 50
        assert rows[0].cache_hit is False
    finally:
        db.close()


def test_second_identical_call_hits_cache_no_provider_call() -> None:
    db = SessionLocal()
    try:
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as m:
            generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Grade 15 and above",
                schema=_schema(),
            )
            result2 = generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Grade 15 and above",
                schema=_schema(),
            )
        db.commit()
        assert m.call_count == 1
        assert result2.cache_hit is True
        rows = list(
            db.execute(
                select(AISpendLog).where(AISpendLog.client_id == DEMO_CLIENT_ID)
            ).scalars()
        )
        cache_hits = [r for r in rows if r.cache_hit]
        assert len(cache_hits) >= 1
    finally:
        db.close()


def test_budget_exceeded_blocks_call() -> None:
    db = SessionLocal()
    try:
        client = db.get(Client, DEMO_CLIENT_ID)
        client.ai_monthly_token_budget = 50
        db.add(
            AISpendLog(
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                operation="ai_suggest_rule",
                model="claude-test",
                input_tokens=100,
                output_tokens=0,
                cost_estimate_usd=0.0,
                cache_hit=False,
            )
        )
        db.commit()
        with pytest.raises(AIBudgetExceededError):
            generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Different description not in cache",
                schema=_schema(),
            )
        client.ai_monthly_token_budget = 100_000
        db.commit()
    finally:
        db.close()


def _add_spend(db, tokens: int) -> None:
    db.add(
        AISpendLog(
            client_id=DEMO_CLIENT_ID,
            policy_year_id=None,
            operation="ai_suggest_rule",
            model="claude-test",
            input_tokens=tokens,
            output_tokens=0,
            cost_estimate_usd=0.0,
            cache_hit=False,
        )
    )
    db.commit()


def test_platform_usage_counter_accumulates() -> None:
    """record_platform_usage bumps the shared counter; MTD reads it back."""
    db = SessionLocal()
    try:
        before = platform_month_to_date_tokens(db)
        record_platform_usage(db, 40)
        record_platform_usage(db, 60)
        db.commit()
        assert platform_month_to_date_tokens(db) == before + 100
        record_platform_usage(db, 0)  # no-op
        db.commit()
        assert platform_month_to_date_tokens(db) == before + 100
    finally:
        db.close()


def test_platform_cap_blocks_call_via_env(monkeypatch) -> None:
    """The platform-wide cap trips even when the tenant is under its own budget."""
    db = SessionLocal()
    try:
        client = db.get(Client, DEMO_CLIENT_ID)
        client.ai_monthly_token_budget = 1_000_000  # tenant well under
        record_platform_usage(db, 500)
        db.commit()
        current = platform_month_to_date_tokens(db)
        assert current > 0
        # Cap at the current platform total → the next call is at/over the cap.
        monkeypatch.setenv("INSPRO_AI_PLATFORM_MONTHLY_TOKEN_CAP", str(current))
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as m:
            with pytest.raises(AIPlatformBudgetExceededError):
                generate_rule_for_category(
                    db,
                    client_id=DEMO_CLIENT_ID,
                    policy_year_id=None,
                    description="Unique description for platform cap env test",
                    schema=_schema(),
                )
        assert m.call_count == 0  # blocked before the provider call
        client.ai_monthly_token_budget = 100_000
        db.commit()
    finally:
        db.close()


def test_platform_cap_from_db_row_overrides_env(monkeypatch) -> None:
    """A stored platform cap is honored (and wins over env), proving DB source."""
    db = SessionLocal()
    try:
        client = db.get(Client, DEMO_CLIENT_ID)
        client.ai_monthly_token_budget = 1_000_000
        current = platform_month_to_date_tokens(db)
        # Env says "huge" (no block); the DB row says "already at cap" → block.
        monkeypatch.setenv(
            "INSPRO_AI_PLATFORM_MONTHLY_TOKEN_CAP", str(current + 10_000_000)
        )
        row = PlatformAISetting(
            id=SINGLETON_ID, platform_monthly_token_cap=max(current, 1)
        )
        db.add(row)
        db.commit()
        try:
            with patch(
                "app.services.ai_gateway.generate_rule_via_ai",
                return_value=_fake_envelope_meta(),
            ):
                with pytest.raises(AIPlatformBudgetExceededError):
                    generate_rule_for_category(
                        db,
                        client_id=DEMO_CLIENT_ID,
                        policy_year_id=None,
                        description="Unique description for platform cap db test",
                        schema=_schema(),
                    )
        finally:
            db.delete(db.get(PlatformAISetting, SINGLETON_ID))
            client.ai_monthly_token_budget = 100_000
            db.commit()
    finally:
        db.close()


def test_platform_error_is_budget_error_subclass() -> None:
    """Existing `except AIBudgetExceededError` handlers must catch the platform one."""
    assert issubclass(AIPlatformBudgetExceededError, AIBudgetExceededError)


def test_default_budget_applies_when_client_zero(monkeypatch) -> None:
    """A zero tenant budget falls back to the fleet-wide default cap."""
    db = SessionLocal()
    try:
        client = db.get(Client, DEMO_CLIENT_ID)
        client.ai_monthly_token_budget = 0  # "unlimited" historically
        db.commit()
        _add_spend(db, 25)
        mtd = month_to_date_tokens(db, DEMO_CLIENT_ID)
        assert mtd > 0
        monkeypatch.setenv("INSPRO_AI_DEFAULT_MONTHLY_TOKEN_BUDGET", str(mtd))
        with pytest.raises(AIBudgetExceededError):
            generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Unique description for default budget test",
                schema=_schema(),
            )
        client.ai_monthly_token_budget = 100_000
        db.commit()
    finally:
        db.close()


def test_concurrency_slot_bounds_and_defaults() -> None:
    """limit<=0 → unbounded; limit>0 → at most `limit` holders at once."""
    with _slot(0):  # no-op, and must not raise
        pass

    with _slot(2), _slot(2):
        # Both slots taken; a third caller must not get in.
        assert (
            _concurrency_state["sem"].acquire(blocking=False) is False
        ), "third concurrent call should hit backpressure"
    # Released on exit, so the next caller proceeds.
    with _slot(2):
        pass


def test_concurrency_slot_gives_up_rather_than_pinning_resources(monkeypatch) -> None:
    """A saturated process must degrade, not queue forever.

    A waiting thread can still hold a pooled DB connection, so an unbounded wait
    let a burst park every connection and starve unrelated requests. The timeout
    raises `AICapacityError`, which subclasses `AIBudgetExceededError` so the
    existing degradation paths already handle it.
    """
    import app.services.ai_gateway as G

    monkeypatch.setattr(G, "_AI_SLOT_WAIT_SECONDS", 0.05)
    with _slot(1):
        with pytest.raises(AIBudgetExceededError):
            with _slot(1):
                pass


def test_clean_session_releases_connection_before_queueing() -> None:
    """The budget check leaves the session clean, so the connection is returned
    before we block — that is what keeps a burst from exhausting the pool."""
    db = SessionLocal()
    try:
        db.execute(select(Client).limit(1)).all()  # opens a transaction
        assert db.in_transaction()
        with _slot(1, db):
            assert not db.in_transaction(), "connection should be released while queueing"
    finally:
        db.close()


def test_provider_failure_increments_breaker() -> None:
    db = SessionLocal()
    try:
        breaker = ai_breaker.get_breaker()
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            side_effect=RuntimeError("simulated provider down"),
        ):
            for _ in range(breaker.threshold):
                with pytest.raises(RuntimeError):
                    generate_rule_for_category(
                        db,
                        client_id=DEMO_CLIENT_ID,
                        policy_year_id=None,
                        description="Some unique description to bypass cache",
                        schema=_schema(),
                    )
        assert breaker.state == "open"
    finally:
        db.close()


def test_credential_error_does_not_trip_breaker() -> None:
    """A tenant pasting a bad BYOK key would otherwise burn 5 attempts and
    open the breaker for every other tenant.

    We construct a real `AuthenticationError` instance using its public
    constructor; the SDK's `__init__` is `(message, *, response, body)`.
    """
    import httpx
    from anthropic import AuthenticationError

    db = SessionLocal()
    try:
        breaker = ai_breaker.get_breaker()
        fake_response = httpx.Response(
            status_code=401,
            request=httpx.Request("POST", "http://x/"),
        )
        creds_err = AuthenticationError(
            message="invalid x-api-key",
            response=fake_response,
            body=None,
        )
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            side_effect=creds_err,
        ):
            for i in range(breaker.threshold + 2):
                with pytest.raises(AuthenticationError):
                    generate_rule_for_category(
                        db,
                        client_id=DEMO_CLIENT_ID,
                        policy_year_id=None,
                        description=f"unique-cred-test-{i}",
                        schema=_schema(),
                    )
        assert breaker.state == "closed"
    finally:
        db.close()


def test_month_to_date_excludes_cache_hits() -> None:
    db = SessionLocal()
    try:
        db.add(
            AISpendLog(
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                operation="ai_suggest_rule",
                model="claude-test",
                input_tokens=999,
                output_tokens=0,
                cost_estimate_usd=0.0,
                cache_hit=True,  # cache hit should NOT count toward budget
            )
        )
        db.commit()
        mtd = month_to_date_tokens(db, DEMO_CLIENT_ID)
        # The cache-hit row contributes 0; other prior tests in this module
        # may have added rows. Just assert the cache-hit didn't add 999.
        assert mtd < 999
    finally:
        db.close()
