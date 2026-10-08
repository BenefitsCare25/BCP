"""Simple circuit breaker for AI provider calls.

States: `closed → open → half_open → closed`.
- `closed`: requests pass through; failures increment a sliding-window counter.
- `open`: requests fail fast with `CircuitOpenError` for `cooldown_seconds`.
- `half_open`: exactly one trial request at a time; every other caller is
  refused as if open. Success closes the circuit, failure re-opens it for
  another cooldown. A trial that reports neither (the gateway records nothing
  for a fault that says nothing about the provider — a parse error, a throttle)
  frees its slot after one cooldown, so the next caller becomes the trial.

Tunable thresholds (env vars):
- `INSPRO_AI_BREAKER_THRESHOLD` (default 5): errors-in-window to trip.
- `INSPRO_AI_BREAKER_WINDOW`    (default 60s): the sliding window.
- `INSPRO_AI_BREAKER_COOLDOWN`  (default 60s): how long `open` lasts.

One breaker per CREDENTIAL SOURCE (`breaker_scope`): the shared platform key
(also used for the environment credential) has one, and every company on its
own BYOK key has its own. A company whose key keeps failing opens only its own
breaker; everyone on the platform key is unaffected, and vice versa.

Hand-rolled; no external dependency. Single-process state — if you run multiple
App Service instances they'll each have their own breakers which is fine.
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
        # When the half-open trial now in flight was let through; None = none.
        self._trial_started_at: float | None = None
        self._lock = Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def before_call(self) -> None:
        """Raise unless this call may proceed: always while closed, never while
        open, and while half-open only as the single trial."""
        with self._lock:
            self._maybe_half_open()
            if self._state == "closed":
                return
            if self._state == "half_open":
                now = time.monotonic()
                trial = self._trial_started_at
                if trial is None or now - trial >= self.cooldown:
                    self._trial_started_at = now
                    return
            raise CircuitOpenError("AI provider circuit is open — try again shortly.")

    def record_success(self) -> None:
        with self._lock:
            if self._state == "half_open":
                logger.info("AI breaker recovered — closing circuit")
            self._failures.clear()
            self._state = "closed"
            self._opened_at = None
            self._trial_started_at = None

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
                self._trial_started_at = None

    def _maybe_half_open(self) -> None:
        if self._state == "open" and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.cooldown:
                logger.info("AI breaker cooldown elapsed — half-open trial")
                self._state = "half_open"
                self._trial_started_at = None


PLATFORM_SCOPE = "platform"

_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = Lock()


def breaker_scope(source: str, client_id: str | None) -> str:
    """The breaker a call counts against: its company's own BYOK key, or the
    shared platform key (which the environment credential stands in for)."""
    if source == "byok" and client_id:
        return f"byok:{client_id}"
    return PLATFORM_SCOPE


def get_breaker(scope: str = PLATFORM_SCOPE) -> CircuitBreaker:
    with _breakers_lock:
        breaker = _breakers.get(scope)
        if breaker is None:
            breaker = CircuitBreaker(
                threshold=int(os.environ.get("INSPRO_AI_BREAKER_THRESHOLD", "5")),
                window_seconds=float(os.environ.get("INSPRO_AI_BREAKER_WINDOW", "60")),
                cooldown_seconds=float(os.environ.get("INSPRO_AI_BREAKER_COOLDOWN", "60")),
            )
            _breakers[scope] = breaker
        return breaker


def reset_breaker_for_tests() -> None:
    with _breakers_lock:
        _breakers.clear()
