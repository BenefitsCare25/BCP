"""AWS Bedrock provider resolution + Singapore/APAC residency guard."""
from __future__ import annotations

import pytest

from app.core import ai_config

_AI_ENV_KEYS = (
    "INSPRO_AI_PROVIDER",
    "AWS_BEDROCK_REGION",
    "AWS_BEDROCK_MODEL",
    "AZURE_FOUNDRY_ENDPOINT",
    "AZURE_FOUNDRY_API_KEY",
    "AZURE_FOUNDRY_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "INSPRO_ENV",
)

_APAC_MODEL = "apac.anthropic.claude-sonnet-4-5-20250929-v1:0"
_GLOBAL_MODEL = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"


def _clear_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _AI_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_bedrock_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("INSPRO_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_BEDROCK_MODEL", _APAC_MODEL)

    cfg = ai_config.load_ai_config()

    assert cfg is not None
    assert cfg.provider == "bedrock"
    assert cfg.aws_region == "ap-southeast-1"  # default when unset
    assert cfg.api_key == ""  # auth comes from the AWS credential chain
    assert cfg.base_url is None
    assert cfg.model == _APAC_MODEL


def test_bedrock_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("INSPRO_AI_PROVIDER", "bedrock")

    assert ai_config.load_ai_config() is None


def test_bedrock_prod_rejects_global_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("INSPRO_ENV", "prod")
    monkeypatch.setenv("INSPRO_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_BEDROCK_MODEL", _GLOBAL_MODEL)

    with pytest.raises(RuntimeError, match="global"):
        ai_config.load_ai_config()


def test_bedrock_prod_rejects_out_of_region(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("INSPRO_ENV", "prod")
    monkeypatch.setenv("INSPRO_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_BEDROCK_REGION", "us-east-1")
    monkeypatch.setenv("AWS_BEDROCK_MODEL", _APAC_MODEL)

    with pytest.raises(RuntimeError, match="residency"):
        ai_config.load_ai_config()


def test_bedrock_prod_allows_apac_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("INSPRO_ENV", "prod")
    monkeypatch.setenv("INSPRO_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_BEDROCK_MODEL", _APAC_MODEL)

    cfg = ai_config.load_ai_config()

    assert cfg is not None and cfg.provider == "bedrock"


def test_bedrock_dev_allows_global_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)  # INSPRO_ENV unset -> dev
    monkeypatch.setenv("INSPRO_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_BEDROCK_MODEL", _GLOBAL_MODEL)

    cfg = ai_config.load_ai_config()  # dev only warns, never raises

    assert cfg is not None and cfg.provider == "bedrock"


def test_byok_bedrock_resolution() -> None:
    from types import SimpleNamespace

    from app.core.ai_config import _byok_bedrock, pack_bedrock_secret

    row = SimpleNamespace(endpoint="ap-southeast-1", model=_APAC_MODEL)
    blob = pack_bedrock_secret("AKIAEXAMPLE", "secret-xyz")

    cfg = _byok_bedrock(row, blob, "client-1")

    assert cfg is not None
    assert cfg.provider == "bedrock"
    assert cfg.source == "byok"
    assert cfg.aws_region == "ap-southeast-1"
    assert cfg.aws_access_key_id == "AKIAEXAMPLE"
    assert cfg.api_key == "secret-xyz"  # the secret access key
    assert cfg.model == _APAC_MODEL


def test_byok_bedrock_malformed_creds_returns_none() -> None:
    from types import SimpleNamespace

    from app.core.ai_config import _byok_bedrock

    row = SimpleNamespace(endpoint="ap-southeast-1", model=_APAC_MODEL)

    assert _byok_bedrock(row, "not-json", "client-1") is None


def test_build_client_bedrock_byok_passes_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic

    captured: dict[str, object] = {}

    class FakeBedrock:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "AnthropicBedrock", FakeBedrock, raising=False)
    from app.services.ai_extractor import _build_anthropic_client

    cfg = ai_config.AIConfig(
        api_key="the-secret",
        model=_APAC_MODEL,
        base_url=None,
        provider="bedrock",
        aws_region="ap-southeast-1",
        aws_access_key_id="AKIA",
    )
    _build_anthropic_client(cfg, timeout=30.0)

    assert captured["aws_access_key"] == "AKIA"
    assert captured["aws_secret_key"] == "the-secret"


def test_build_client_uses_bedrock_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic

    captured: dict[str, object] = {}

    class FakeBedrock:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "AnthropicBedrock", FakeBedrock, raising=False)
    from app.services.ai_extractor import _build_anthropic_client

    cfg = ai_config.AIConfig(
        api_key="",
        model=_APAC_MODEL,
        base_url=None,
        provider="bedrock",
        aws_region="ap-southeast-1",
    )
    client = _build_anthropic_client(cfg, timeout=30.0)

    assert isinstance(client, FakeBedrock)
    assert captured["aws_region"] == "ap-southeast-1"
