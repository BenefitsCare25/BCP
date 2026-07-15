"""AI cache — key stability + TTLCache behaviour."""
from __future__ import annotations

from app.services.ai_cache import InMemoryAICache, make_key


def test_make_key_is_stable_across_dict_order() -> None:
    a = make_key("v1", "claude-x", {"a": 1, "b": 2})
    b = make_key("v1", "claude-x", {"b": 2, "a": 1})
    assert a == b


def test_make_key_differs_with_model_or_version() -> None:
    base = make_key("v1", "claude-x", {"a": 1})
    assert base != make_key("v2", "claude-x", {"a": 1})
    assert base != make_key("v1", "claude-y", {"a": 1})


def test_in_memory_cache_roundtrip() -> None:
    c = InMemoryAICache(maxsize=10, ttl=10)
    c.set("k", {"rule": None, "human_readable": "hi", "confidence": 0.5})
    assert c.get("k") == {"rule": None, "human_readable": "hi", "confidence": 0.5}


def test_in_memory_cache_miss_returns_none() -> None:
    c = InMemoryAICache(maxsize=10, ttl=10)
    assert c.get("absent") is None
