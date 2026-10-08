"""AI gateway — cache + breaker + budget integration tests."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
)

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
from app.services.ai_breaker import CircuitOpenError, breaker_scope  # noqa: E402
from app.services.ai_gateway import (  # noqa: E402
    AIBudgetExceededError,
    AICapacityError,
    AIPlatformBudgetExceededError,
    _concurrency_state,
    _slot,
    generate_rule_for_category,
    month_to_date_tokens,
    platform_month_to_date_tokens,
    record_platform_usage,
)
from app.services.platform_ai_settings import PlatformAILimits  # noqa: E402
from app.services.vertex_gemini import CredentialRefreshError  # noqa: E402
from scripts.seed_demo import DEMO_CLIENT_2_ID, seed  # noqa: E402


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


def test_rule_cache_changes_with_company_context() -> None:
    """Roster vocabulary and sibling-plan context are part of the prompt.

    A cached recommendation for one roster shape must not survive a job-grade
    change or be reused for a different product's sibling categories.
    """

    db = SessionLocal()
    try:
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as provider:
            generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Managers",
                schema=_schema(),
                context={"employee_attributes": [{"attribute_id": "grade", "values": [1]}]},
            )
            generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Managers",
                schema=_schema(),
                context={"employee_attributes": [{"attribute_id": "grade", "values": [2]}]},
            )

        assert provider.call_count == 2
    finally:
        db.close()


def test_rule_cache_changes_with_schema_values() -> None:
    db = SessionLocal()
    try:
        first = _schema()
        second = _schema()
        first[0].enum_values = ["M1"]
        second[0].enum_values = ["M2"]
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as provider:
            generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Managers in a unique cache-value test",
                schema=first,
            )
            generate_rule_for_category(
                db,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=None,
                description="Managers in a unique cache-value test",
                schema=second,
            )

        assert provider.call_count == 2
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


def _status_error(error_type: type[APIStatusError], status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "http://vertex.test/")
    return error_type(
        "provider said no", response=httpx.Response(status_code, request=request), body=None
    )


def _connection_error() -> APIConnectionError:
    return APIConnectionError(
        message="vertex unreachable", request=httpx.Request("POST", "http://vertex.test/")
    )


def _rule_call(db, description: str, client_id: str = DEMO_CLIENT_ID):
    return generate_rule_for_category(
        db,
        client_id=client_id,
        policy_year_id=None,
        description=description,
        schema=_schema(),
    )


@pytest.mark.parametrize(
    "make_error",
    [
        _connection_error,
        lambda: _status_error(InternalServerError, 503),
        lambda: APITimeoutError(request=httpx.Request("POST", "http://vertex.test/")),
        lambda: TimeoutError("socket timed out"),
    ],
)
def test_provider_failure_increments_breaker(make_error) -> None:
    db = SessionLocal()
    try:
        breaker = ai_breaker.get_breaker()
        error = make_error()
        with patch("app.services.ai_gateway.generate_rule_via_ai", side_effect=error):
            for i in range(breaker.threshold):
                with pytest.raises(type(error)):
                    _rule_call(db, f"Some unique description to bypass cache {i}")
        assert breaker.state == "open"
    finally:
        db.close()


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: _status_error(BadRequestError, 400),  # the request was rejected
        lambda: _status_error(NotFoundError, 404),  # model not enabled for the project
        lambda: ValueError("The service-account key is not valid JSON."),  # malformed key
        lambda: CredentialRefreshError(
            message="token refresh", request=httpx.Request("POST", "http://t/")
        ),
        lambda: RuntimeError("an application bug"),
    ],
)
def test_caller_side_failures_never_trip_breaker(make_error) -> None:
    """Only the provider failing counts: these fail the same way on every
    retry, and counting them let one bad request or key open the circuit."""
    db = SessionLocal()
    try:
        breaker = ai_breaker.get_breaker()
        error = make_error()
        with patch("app.services.ai_gateway.generate_rule_via_ai", side_effect=error):
            for i in range(breaker.threshold + 2):
                with pytest.raises(type(error)):
                    _rule_call(db, f"caller-side failure {type(error).__name__} {i}")
        assert breaker.state == "closed"
    finally:
        db.close()


def test_capacity_wait_does_not_trip_breaker(monkeypatch) -> None:
    """A saturated worker pool is our own backpressure, not a provider outage:
    counting it opened the breaker for every tenant during a busy burst."""
    import app.services.ai_gateway as G

    monkeypatch.setattr(G, "_AI_SLOT_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(
        G, "resolve_platform_ai_limits", lambda _db: PlatformAILimits(0, 0, 1)
    )
    db = SessionLocal()
    try:
        breaker = ai_breaker.get_breaker()
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as provider, _slot(1):
            for i in range(breaker.threshold + 2):
                with pytest.raises(AICapacityError):
                    _rule_call(db, f"capacity wait {i}")
        assert provider.call_count == 0
        assert breaker.state == "closed"
    finally:
        db.close()


def test_byok_company_failures_open_only_its_own_breaker(monkeypatch) -> None:
    """One company's broken key must not take AI down for everyone else."""
    import app.services.ai_gateway as G

    real_load = G.load_ai_config

    def demo_on_byok(db, client_id=None):
        cfg = real_load(db, client_id)
        return replace(cfg, source="byok") if client_id == DEMO_CLIENT_ID else cfg

    monkeypatch.setattr(G, "load_ai_config", demo_on_byok)
    db = SessionLocal()
    try:
        company = ai_breaker.get_breaker(breaker_scope("byok", DEMO_CLIENT_ID))
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai", side_effect=_connection_error()
        ):
            for i in range(company.threshold):
                with pytest.raises(APIConnectionError):
                    _rule_call(db, f"byok outage {i}")
        assert company.state == "open"
        assert ai_breaker.get_breaker().state == "closed"

        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as provider:
            # A company on the platform key is unaffected...
            _rule_call(db, "platform company still served", client_id=DEMO_CLIENT_2_ID)
            # ...while the failing company fails fast without a provider call.
            with pytest.raises(CircuitOpenError):
                _rule_call(db, "byok company fails fast")
        assert provider.call_count == 1
    finally:
        db.rollback()
        db.close()


def test_cache_entries_never_cross_companies_or_credential_sources(monkeypatch) -> None:
    import app.services.ai_gateway as G

    db = SessionLocal()
    try:
        with patch(
            "app.services.ai_gateway.generate_rule_via_ai",
            return_value=_fake_envelope_meta(),
        ) as provider:
            _rule_call(db, "Identical description in two companies")
            _rule_call(db, "Identical description in two companies", DEMO_CLIENT_2_ID)
            assert provider.call_count == 2

            real_load = G.load_ai_config
            monkeypatch.setattr(
                G,
                "load_ai_config",
                lambda session, client_id=None: replace(
                    real_load(session, client_id), source="byok"
                ),
            )
            # Same company and input, now on its own key: no platform-key hit.
            _rule_call(db, "Identical description in two companies")
            assert provider.call_count == 3
        db.rollback()
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


# ── Concurrency limit is a PLATFORM number, split across gunicorn workers ─────
# It used to be enforced by a per-process semaphore, so N workers silently
# allowed N x the configured limit against one shared Vertex quota.
def test_per_process_limit_divides_across_workers(monkeypatch):
    from app.services.ai_gateway import _per_process_limit

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    assert _per_process_limit(8) == 2
    assert _per_process_limit(4) == 1
    # Below the worker count every worker still floors at 1 — documented limit.
    assert _per_process_limit(2) == 1
    # 0 stays unbounded rather than becoming a 1-wide bottleneck.
    assert _per_process_limit(0) == 0


def test_per_process_limit_defaults_to_single_worker(monkeypatch):
    from app.services.ai_gateway import _per_process_limit

    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    assert _per_process_limit(6) == 6
    monkeypatch.setenv("WEB_CONCURRENCY", "not-a-number")
    assert _per_process_limit(6) == 6
