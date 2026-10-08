"""AI cache — key stability, tenant scoping, purge + TTLCache behaviour."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.services import ai_cache
from app.services.ai_cache import InMemoryAICache, cache_scope, make_key


def test_make_key_is_stable_across_dict_order() -> None:
    a = make_key("v1", "claude-x", {"a": 1, "b": 2}, scope="platform")
    b = make_key("v1", "claude-x", {"b": 2, "a": 1}, scope="platform")
    assert a == b


def test_make_key_differs_with_model_or_version() -> None:
    base = make_key("v1", "claude-x", {"a": 1}, scope="platform")
    assert base != make_key("v2", "claude-x", {"a": 1}, scope="platform")
    assert base != make_key("v1", "claude-y", {"a": 1}, scope="platform")


def test_every_key_is_prefixed_with_its_tenant_scope() -> None:
    company = make_key("v1", "m", {"a": 1}, scope=cache_scope("client-1"))
    firm = make_key("v1", "m", {"a": 1}, scope=cache_scope(None, "firm-1"))
    platform = make_key("v1", "m", {"a": 1}, scope=cache_scope(None))

    assert company.startswith("ai:c:client-1:v1:m:")
    assert firm.startswith("ai:f:firm-1:v1:m:")
    assert platform.startswith("ai:platform:v1:m:")
    # Same input, different tenant: never the same entry.
    assert len({company, firm, platform}) == 3
    assert company != make_key("v1", "m", {"a": 1}, scope=cache_scope("client-2"))


def test_in_memory_cache_roundtrip() -> None:
    c = InMemoryAICache(maxsize=10, ttl=10)
    c.set("k", {"rule": None, "human_readable": "hi", "confidence": 0.5})
    assert c.get("k") == {"rule": None, "human_readable": "hi", "confidence": 0.5}


def test_in_memory_cache_miss_returns_none() -> None:
    c = InMemoryAICache(maxsize=10, ttl=10)
    assert c.get("absent") is None


def test_purge_client_drops_only_that_company() -> None:
    ai_cache.reset_cache_for_tests()
    cache = ai_cache.get_cache()
    mine = make_key("v1", "m", {"a": 1}, scope=cache_scope("client-1"))
    mine_other_op = make_key("v2", "m2", {"b": 2}, scope=cache_scope("client-1"))
    neighbour = make_key("v1", "m", {"a": 1}, scope=cache_scope("client-10"))
    for key in (mine, mine_other_op, neighbour):
        cache.set(key, {"value": key})

    assert ai_cache.purge_client("client-1") == 2
    assert cache.get(mine) is None and cache.get(mine_other_op) is None
    # "client-10" shares the prefix characters but not the scope segment.
    assert cache.get(neighbour) == {"value": neighbour}
    with pytest.raises(ValueError):
        ai_cache.purge_client("")


class _FakeRedis:
    def __init__(self, keys: list[str]) -> None:
        self.keys = set(keys)
        self.patterns: list[str] = []

    def ping(self) -> bool:
        return True

    def scan_iter(self, *, match: str, count: int) -> list[str]:
        self.patterns.append(match)
        prefix = match.rstrip("*").replace("\\", "")
        return [key for key in sorted(self.keys) if key.startswith(prefix)]

    def delete(self, *keys: str) -> int:
        removed = len(self.keys & set(keys))
        self.keys -= set(keys)
        return removed


def test_redis_purge_scans_the_scope_prefix_and_escapes_globs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = ["ai:c:client-1:v1:m:aa", "ai:c:client-1:v2:m:bb", "ai:c:client-2:v1:m:cc"]
    fake = _FakeRedis(keys)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *_a, **_k: fake)),
    )
    cache = ai_cache.RedisAICache("redis://cache.example:6379/0")

    assert cache.purge_prefix("ai:c:client-1:") == 2
    assert fake.keys == {"ai:c:client-2:v1:m:cc"}
    assert cache.purge_prefix("ai:c:we[ird]*:") == 0
    assert fake.patterns[-1] == "ai:c:we\\[ird\\]\\*:*"


def test_redis_purge_fails_loudly_while_redis_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline() -> bool:
        raise ConnectionError("offline")

    client = SimpleNamespace(ping=offline)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *_a, **_k: client)),
    )
    cache = ai_cache.RedisAICache("redis://cache.example:6379/0")
    cache.set("ai:c:client-1:v1:m:aa", {"x": 1})  # lands in the local fallback

    # A purge that skipped the shared store would claim success while other
    # replicas could still serve the company's results.
    with pytest.raises(RuntimeError, match="not purged"):
        cache.purge_prefix("ai:c:client-1:")
    assert cache.get("ai:c:client-1:v1:m:aa") is None
