"""Simple circuit breaker for AI provider calls.

States: `closed → open → half_open → closed`.
- `closed`: requests pass through; failures increment a sliding-window counter.
- `open`: requests fail fast with `CircuitOpenError` for `cooldown_seconds`.
- `half_open`: one trial request allowed; success closes the circuit, failure
  re-opens it for another cooldown.

Tunable thresholds (env vars):
- `INSPRO_AI_BREAKER_THRESHOLD` (default 5): errors-in-window to trip.
- `INSPRO_AI_BREAKER_WINDOW`    (default 60s): the sliding window.
- `INSPRO_AI_BREAKER_COOLDOWN`  (default 60s): how long `open` lasts.

Hand-rolled; no external dependency. Single-process state — if you run multiple
App Service instances they'll each have their own breaker which is fine.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from threading import Lock
from typing import Literal

# Lock is conservative: FastAPI on uvicorn runs single-threaded per worker
# today, so contention is impossible. Kept so the breaker stays safe if a
# future deploy switches to a threaded executor or `run_in_executor` for the
# AI call path.

logger = logging.getLogger(__name__)


BreakerState = Literal["closed", "open", "half_open"]


class CircuitOpenError(RuntimeError):
    """Raised when the breaker rejects a request without invoking the provider."""


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 5,
        window_seconds: float = 60.0,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.threshold = threshold
        self.window = window_seconds
        self.cooldown = cooldown_seconds
        self._failures: deque[float] = deque()
        self._state: BreakerState = "closed"
        self._opened_at: float | None = None
        self._lock = Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def before_call(self) -> None:
        """Raise if the circuit is open; otherwise allow the call to proceed."""
        with self._lock:
            self._maybe_half_open()
            if self._state == "open":
                raise CircuitOpenError("AI provider circuit is open — try again shortly.")

    def record_success(self) -> None:
        with self._lock:
            if self._state == "half_open":
                logger.info("AI breaker recovered — closing circuit")
            self._failures.clear()
            self._state = "closed"
            self._opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self.window:
                self._failures.popleft()
            if self._state == "half_open" or len(self._failures) >= self.threshold:
                logger.warning("AI breaker tripped (%d failures in window)", len(self._failures))
                self._state = "open"
                self._opened_at = now

    def _maybe_half_open(self) -> None:
        if self._state == "open" and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.cooldown:
                logger.info("AI breaker cooldown elapsed — half-open trial")
                self._state = "half_open"


_breaker_singleton: CircuitBreaker | None = None


def get_breaker() -> CircuitBreaker:
    global _breaker_singleton
    if _breaker_singleton is None:
        _breaker_singleton = CircuitBreaker(
            threshold=int(os.environ.get("INSPRO_AI_BREAKER_THRESHOLD", "5")),
            window_seconds=float(os.environ.get("INSPRO_AI_BREAKER_WINDOW", "60")),
            cooldown_seconds=float(os.environ.get("INSPRO_AI_BREAKER_COOLDOWN", "60")),
        )
    return _breaker_singleton


def reset_breaker_for_tests() -> None:
    global _breaker_singleton
    _breaker_singleton = CircuitBreaker(threshold=5, window_seconds=60.0, cooldown_seconds=60.0)
