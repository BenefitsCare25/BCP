"""AI response cache.

Keys are `(prompt_version, model, sha256(input))`. 24-hour TTL by default so
sensitive extracted claim data has a bounded residual lifetime after deletion.
In-memory `cachetools.TTLCache` is the default impl; Redis is used when
`INSPRO_REDIS_URL` is set in the environment so prod gets shared cache
across App Service instances.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Protocol

from cachetools import TTLCache

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MAX_ENTRIES = 10_000
REDIS_SOCKET_TIMEOUT_SECONDS = 3.0


def make_key(prompt_version: str, model: str, payload: dict[str, Any]) -> str:
    """Stable cache key — JSON serialisation is sort_keys'd so dict order
    doesn't fragment the cache.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"ai:{prompt_version}:{model}:{digest}"


class AICache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any]) -> None: ...
    def ready(self) -> bool: ...
    @property
    def kind(self) -> str: ...


class InMemoryAICache:
    """Per-process cache. Lost on restart — acceptable for dev + single-replica."""

    def __init__(self, maxsize: int = DEFAULT_MAX_ENTRIES, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, key: str) -> dict[str, Any] | None:
        return self._cache.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._cache[key] = value

    def ready(self) -> bool:
        return True

    @property
    def kind(self) -> str:
        return "memory"


class RedisAICache:
    """Shared cache across replicas. Falls back to in-memory if Redis is unreachable.

    PINGs Redis on init so misconfiguration surfaces at boot rather than
    silently degrading every request. The `kind` property reflects degraded
    state ("redis-degraded") so operators can spot it on `/system/ai-status`.

    Degradation SELF-HEALS: while degraded, a re-ping is attempted at most
    every `REPROBE_SECONDS` on access, so a transient Redis blip (deploy,
    failover) doesn't permanently lose cross-replica sharing until restart.
    """

    REPROBE_SECONDS = 60.0

    def __init__(self, url: str, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        # Imported lazily so test runs that never touch Redis don't need it.
        import redis

        self._redis = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            retry_on_timeout=False,
        )
        self._ttl = ttl
        self._fallback = InMemoryAICache()
        self._degraded = False
        self._degraded_since = 0.0
        self._last_probe = 0.0
        try:
            self._redis.ping()
        except Exception:
            logger.exception(
                "Redis ping failed at init — cache is degraded (falling back to "
                "in-memory). Check INSPRO_REDIS_URL and network reachability."
            )
            self._mark_degraded()

    def _mark_degraded(self) -> None:
        import time

        self._degraded = True
        now = time.monotonic()
        self._degraded_since = now
        self._last_probe = now

    def _maybe_recover(self) -> bool:
        """While degraded, re-ping at most once per REPROBE_SECONDS.
        Returns True when Redis is usable again."""
        import time

        now = time.monotonic()
        if now - self._last_probe < self.REPROBE_SECONDS:
            return False
        self._last_probe = now
        try:
            self._redis.ping()
        except Exception:
            logger.warning(
                "Redis still unreachable (degraded for %.0fs) — continuing "
                "on the in-memory fallback.", now - self._degraded_since,
            )
            return False
        self._degraded = False
        logger.info("Redis reachable again — AI cache restored to shared mode.")
        return True

    def get(self, key: str) -> dict[str, Any] | None:
        if self._degraded and not self._maybe_recover():
            return self._fallback.get(key)
        try:
            raw = self._redis.get(key)
        except Exception:
            logger.exception("Redis GET failed; falling back to in-memory")
            self._mark_degraded()
            return self._fallback.get(key)
        if not isinstance(raw, (str, bytes, bytearray)):
            return None
        try:
            decoded: object = json.loads(raw)
            return decoded if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            logger.warning("Discarding malformed cache entry for %s", key)
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        if self._degraded and not self._maybe_recover():
            self._fallback.set(key, value)
            return
        try:
            self._redis.setex(key, self._ttl, json.dumps(value, separators=(",", ":")))
        except Exception:
            logger.exception("Redis SET failed; falling back to in-memory")
            self._mark_degraded()
            self._fallback.set(key, value)

    def ready(self) -> bool:
        try:
            self._redis.ping()
        except Exception:
            self._mark_degraded()
            return False
        if self._degraded:
            self._degraded = False
            logger.info("Redis readiness probe recovered the shared AI cache.")
        return True

    @property
    def kind(self) -> str:
        return "redis-degraded" if self._degraded else "redis"


_cache_singleton: AICache | None = None


def get_cache() -> AICache:
    global _cache_singleton
    if _cache_singleton is None:
        from app.core.settings import get_settings

        url = get_settings().redis_url
        _cache_singleton = RedisAICache(url) if url else InMemoryAICache()
        logger.info("AI cache initialised (%s)", _cache_singleton.kind)
    return _cache_singleton


def reset_cache_for_tests() -> None:
    """Tests replace the singleton between cases for isolation."""
    global _cache_singleton
    _cache_singleton = InMemoryAICache()


def is_warm(key: str) -> bool:
    """Cheap probe for monitoring — does NOT load the value."""
    return get_cache().get(key) is not None


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "AICache",
    "InMemoryAICache",
    "RedisAICache",
    "get_cache",
    "is_warm",
    "make_key",
    "reset_cache_for_tests",
]
