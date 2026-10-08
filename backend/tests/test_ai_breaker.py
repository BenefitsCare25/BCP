"""AI circuit breaker state machine + one breaker per credential source."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.services import ai_breaker
from app.services.ai_breaker import (
    PLATFORM_SCOPE,
    CircuitBreaker,
    CircuitOpenError,
    breaker_scope,
    get_breaker,
)


def test_breaker_scope_follows_the_credential_source() -> None:
    assert breaker_scope("byok", "client-1") == "byok:client-1"
    assert breaker_scope("byok", "client-2") == "byok:client-2"
    # The environment credential stands in for the shared platform key.
    assert breaker_scope("platform", "client-1") == PLATFORM_SCOPE
    assert breaker_scope("env", "client-1") == PLATFORM_SCOPE
    assert breaker_scope("env", None) == PLATFORM_SCOPE


def test_one_company_key_cannot_open_the_breaker_for_everyone() -> None:
    ai_breaker.reset_breaker_for_tests()
    company = get_breaker(breaker_scope("byok", "client-1"))
    for _ in range(company.threshold):
        company.record_failure()

    assert company.state == "open"
    assert get_breaker(breaker_scope("byok", "client-2")).state == "closed"
    assert get_breaker().state == "closed"  # the platform key's breaker
    assert get_breaker(breaker_scope("byok", "client-1")) is company
    ai_breaker.reset_breaker_for_tests()
    assert get_breaker(breaker_scope("byok", "client-1")).state == "closed"


def test_closed_initially_allows_calls() -> None:
    b = CircuitBreaker(threshold=3, window_seconds=10, cooldown_seconds=10)
    assert b.state == "closed"
    b.before_call()  # no raise


def test_trips_open_after_threshold_failures() -> None:
    b = CircuitBreaker(threshold=3, window_seconds=10, cooldown_seconds=10)
    for _ in range(3):
        b.record_failure()
    assert b.state == "open"
    with pytest.raises(CircuitOpenError):
        b.before_call()


def test_failures_outside_window_are_dropped() -> None:
    b = CircuitBreaker(threshold=3, window_seconds=0.05, cooldown_seconds=10)
    b.record_failure()
    b.record_failure()
    time.sleep(0.1)
    b.record_failure()  # only one in the current window
    assert b.state == "closed"


def test_half_open_then_close_on_success() -> None:
    b = CircuitBreaker(threshold=2, window_seconds=10, cooldown_seconds=0.05)
    b.record_failure()
    b.record_failure()
    assert b.state == "open"
    time.sleep(0.1)
    assert b.state == "half_open"
    b.before_call()  # half-open trial allowed
    b.record_success()
    assert b.state == "closed"


def test_half_open_then_reopen_on_failure() -> None:
    b = CircuitBreaker(threshold=2, window_seconds=10, cooldown_seconds=0.05)
    b.record_failure()
    b.record_failure()
    time.sleep(0.1)
    assert b.state == "half_open"
    b.record_failure()
    assert b.state == "open"


# ── Half-open admits one trial at a time ──────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Drive the breaker's clock by hand: these tests are about who gets through
    at a given moment, which real sleeps can only approximate."""
    fake = _Clock()
    monkeypatch.setattr(ai_breaker, "time", SimpleNamespace(monotonic=fake))
    return fake


def _cooled_down(clock: _Clock) -> CircuitBreaker:
    b = CircuitBreaker(threshold=2, window_seconds=10, cooldown_seconds=30)
    b.record_failure()
    b.record_failure()
    clock.now += 30
    assert b.state == "half_open"
    return b


def test_half_open_lets_exactly_one_trial_through(clock: _Clock) -> None:
    b = _cooled_down(clock)
    b.before_call()  # the trial
    for _ in range(3):
        # Concurrent callers would each be one more call against a provider
        # that has not shown it recovered.
        with pytest.raises(CircuitOpenError):
            b.before_call()
    assert b.state == "half_open"

    b.record_success()
    assert b.state == "closed"
    b.before_call()
    b.before_call()  # closed again: no single-trial limit


def test_a_failed_trial_reopens_for_a_full_cooldown(clock: _Clock) -> None:
    b = _cooled_down(clock)
    b.before_call()
    b.record_failure()
    assert b.state == "open"
    clock.now += 29
    with pytest.raises(CircuitOpenError):
        b.before_call()
    clock.now += 1
    b.before_call()  # the next trial
    with pytest.raises(CircuitOpenError):
        b.before_call()


def test_an_unreported_trial_frees_its_slot_after_a_cooldown(clock: _Clock) -> None:
    """The gateway records nothing for a fault that says nothing about the
    provider (a parse error, a throttle, an exhausted local slot). A trial that
    ends that way must not hold the circuit shut for good."""
    b = _cooled_down(clock)
    b.before_call()
    clock.now += 29
    with pytest.raises(CircuitOpenError):
        b.before_call()
    clock.now += 1
    b.before_call()  # the lapsed slot goes to this caller
    with pytest.raises(CircuitOpenError):
        b.before_call()
    b.record_success()
    assert b.state == "closed"
