"""Regression coverage for claims-AI calibration and production dependencies."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import settings as settings_module
from app.core.settings import _resolve_redis_url, get_settings
from app.main import create_app
from app.services import ai_cache
from app.services.claims_ai_confidence import confidence_threshold
from app.services.claims_ai_evaluation import (
    EvaluationCase,
    build_confidence_profile,
    load_evaluation_cases,
)
from app.services.claims_review.verdict import compute_verdict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
GOLD_DATASET = BACKEND_ROOT / "evals" / "claims_ai" / "gold.jsonl"


def test_gold_dataset_meets_false_accept_policy() -> None:
    cases, dataset_hash = load_evaluation_cases(GOLD_DATASET)
    profile = build_confidence_profile(cases, dataset_sha256=dataset_hash)

    assert len(cases) == 40
    assert profile["generated_at"].endswith("+08:00")
    assert profile["dataset_sha256"] == dataset_hash
    for flow in ("intake", "review"):
        metrics = profile["flows"][flow]["metrics"]
        assert metrics["false_accept_rate"] <= 0.05
        assert metrics["auto_accept_coverage"] > 0


def test_calibration_fails_closed_when_no_threshold_is_safe() -> None:
    cases = [
        EvaluationCase("i1", "intake", 1.0, False, "m", "receipt", "extract"),
        EvaluationCase("r1", "review", 1.0, False, "m", "gp", "verdict"),
    ]

    with pytest.raises(ValueError, match="No intake confidence threshold"):
        build_confidence_profile(cases, dataset_sha256="0" * 64)


def test_runtime_thresholds_use_document_and_claim_type_scopes() -> None:
    assert confidence_threshold(
        "intake", model="gemini-3.5-flash", document_type="discharge summary"
    ) == 0.76
    assert confidence_threshold(
        "review", model="gemini-3.5-flash", document_type="GP"
    ) == 0.71
    assert confidence_threshold(
        "review", model="gemini-3.5-flash", document_type="SP"
    ) == 0.70


def test_verdict_applies_claim_type_threshold() -> None:
    gp_verdict, gp_reasons = compute_verdict(
        [], [], [], 0.705, model="gemini-3.5-flash", claim_type="GP"
    )
    sp_verdict, sp_reasons = compute_verdict(
        [], [], [], 0.705, model="gemini-3.5-flash", claim_type="SP"
    )

    assert gp_verdict == "flagged"
    assert "calibrated threshold 0.71" in gp_reasons[0]
    assert sp_verdict == "clean"
    assert sp_reasons == []


def test_production_requires_valid_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSPRO_REDIS_URL", raising=False)
    with pytest.raises(RuntimeError, match="INSPRO_REDIS_URL must be set"):
        _resolve_redis_url("prod")

    monkeypatch.setenv("INSPRO_REDIS_URL", "https://cache.example")
    with pytest.raises(RuntimeError, match="redis:// or rediss://"):
        _resolve_redis_url("prod")


def test_redis_readiness_detects_failure_and_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(failing=True)

    def ping() -> bool:
        if client.failing:
            raise ConnectionError("offline")
        return True

    client.ping = ping
    fake_redis = SimpleNamespace(
        Redis=SimpleNamespace(from_url=lambda *_args, **_kwargs: client)
    )
    monkeypatch.setitem(sys.modules, "redis", fake_redis)

    cache = ai_cache.RedisAICache("redis://cache.example:6379/0")
    assert cache.kind == "redis-degraded"
    assert cache.ready() is False
    client.failing = False
    assert cache.ready() is True
    assert cache.kind == "redis"


def test_readiness_returns_503_when_shared_cache_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = replace(get_settings(), redis_url="redis://cache.example:6379/0")
    monkeypatch.setattr(settings_module, "get_settings", lambda: configured)
    monkeypatch.setattr(
        ai_cache,
        "get_cache",
        lambda: SimpleNamespace(ready=lambda: False, kind="redis-degraded"),
    )

    with TestClient(create_app()) as client:
        response = client.get("/readiness")

    assert response.status_code == 503
    assert "Redis is unavailable" in response.json()["detail"]
