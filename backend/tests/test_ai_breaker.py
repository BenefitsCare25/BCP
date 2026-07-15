"""AI circuit breaker state machine."""
from __future__ import annotations

import time

import pytest

from app.services.ai_breaker import CircuitBreaker, CircuitOpenError


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
