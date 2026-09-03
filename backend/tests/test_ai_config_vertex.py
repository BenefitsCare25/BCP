"""Vertex (Gemini) BYOK provider — config resolution, residency guard, adapter.

The adapter translation is exercised with a fake ``google.genai`` types module +
client so these tests don't need live GCP credentials or network.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.ai_config import (
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_MODEL,
    _load_vertex_from_env,
    _vertex_from_secret,
    assert_vertex_location_writable,
    assert_vertex_residency,
    pack_vertex_secret,
)
from app.services import vertex_gemini as vg

_SA_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "inspro-ai",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
        "client_email": "inspro-vertex@inspro-ai.iam.gserviceaccount.com",
    }
)


# ── Residency guard ───────────────────────────────────────────────────────────


def test_residency_allows_singapore():
    assert_vertex_residency("asia-southeast1")  # no raise


def test_residency_refuses_non_approved_in_prod(monkeypatch):
    monkeypatch.setenv("INSPRO_ENV", "prod")
    with pytest.raises(RuntimeError, match="approved residency"):
        assert_vertex_residency("us-central1")


def test_write_boundary_refuses_non_singapore_outside_prod(monkeypatch):
    """The WRITE guard is env-independent — that's the whole point of it.

    `assert_vertex_residency` only warns in dev/staging so a local checkout
    pointed at another region still boots. A stored credential is different: it
    ships to prod unchanged, so accepting `us-central1` in dev plants a prod
    outage (and a residency breach if it ever served a call).
    """
    monkeypatch.setenv("INSPRO_ENV", "dev")
    assert_vertex_residency("us-central1")  # read path: warns, does not raise
    with pytest.raises(ValueError, match="not permitted"):
        assert_vertex_location_writable("us-central1")


def test_write_boundary_returns_canonical_location():
    """It must RETURN the canonical form, not merely accept a sloppy one.

    Vertex resource paths are case-sensitive (`projects/…/locations/<loc>/…`),
    so a validator that lowercases only for its comparison while the caller
    persists the raw string stores a location every later call rejects.
    """
    assert assert_vertex_location_writable("asia-southeast1") == "asia-southeast1"
    assert assert_vertex_location_writable("  ASIA-SOUTHEAST1  ") == "asia-southeast1"


def test_write_boundary_refuses_global_location():
    # Vertex's "global" location routes to any geography — the exact case the
    # residency guarantee exists to prevent.
    with pytest.raises(ValueError, match="not permitted"):
        assert_vertex_location_writable("global")


def test_default_model_is_a_gemini_flash_model():
    # The default is what every company runs on absent an explicit choice, and
    # it must stay in the Gemini family: `_family_price` keys the cost estimate
    # off the `gemini-` prefix, and a non-Gemini default would silently price
    # the whole fleet at Claude rates.
    assert DEFAULT_VERTEX_MODEL.startswith("gemini-")
    assert "flash" in DEFAULT_VERTEX_MODEL


def test_residency_only_warns_in_dev(monkeypatch):
    monkeypatch.setenv("INSPRO_ENV", "dev")
    assert_vertex_residency("global")  # no raise in dev


# ── Env + BYOK resolution ─────────────────────────────────────────────────────


def test_load_vertex_from_env(monkeypatch):
    monkeypatch.setenv("VERTEX_PROJECT", "inspro-ai")
    monkeypatch.delenv("VERTEX_LOCATION", raising=False)
    monkeypatch.delenv("VERTEX_MODEL", raising=False)
    cfg = _load_vertex_from_env()
    assert cfg is not None
    assert cfg.provider == "vertex"
    assert cfg.gcp_project == "inspro-ai"
    assert cfg.gcp_location == DEFAULT_VERTEX_LOCATION
    assert cfg.model == DEFAULT_VERTEX_MODEL
    assert cfg.api_key == ""  # env mode → ADC, no stored key


def test_load_vertex_from_env_requires_project(monkeypatch):
    monkeypatch.delenv("VERTEX_PROJECT", raising=False)
    assert _load_vertex_from_env() is None


@pytest.mark.parametrize("source", ["byok", "platform"])
def test_vertex_from_secret_round_trip(source):
    """One builder serves both stored sources; only `source` differs."""
    secret = pack_vertex_secret("inspro-ai", _SA_JSON)
    cfg = _vertex_from_secret(
        "asia-southeast1", "gemini-2.5-flash", secret, source, "client-1"
    )
    assert cfg is not None
    assert cfg.gcp_project == "inspro-ai"
    assert cfg.gcp_location == "asia-southeast1"
    assert cfg.api_key == _SA_JSON  # the SA JSON is the credential
    assert cfg.source == source


def test_vertex_from_secret_malformed_returns_none():
    assert (
        _vertex_from_secret(
            "asia-southeast1", "gemini-2.5-flash", "not-json", "byok", "client-1"
        )
        is None
    )


# ── Schema conversion ─────────────────────────────────────────────────────────


def test_convert_schema_handles_null_union():
    schema = {
        "type": "object",
        "properties": {
            "claim_value": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "status": {"type": "string", "enum": ["MATCH", "MISMATCH"]},
        },
        "required": ["confidence"],
    }
    out = vg._convert_schema(schema)
    assert out["type"] == "OBJECT"
    assert out["properties"]["claim_value"] == {"type": "STRING", "nullable": True}
    assert out["properties"]["confidence"]["type"] == "NUMBER"
    assert out["properties"]["status"]["enum"] == ["MATCH", "MISMATCH"]
    assert out["required"] == ["confidence"]


def test_convert_schema_array_items():
    out = vg._convert_schema({"type": "array", "items": {"type": "string"}})
    assert out["type"] == "ARRAY"
    assert out["items"] == {"type": "STRING"}


def test_convert_schema_open_map_additional_properties():
    # The slip extractor's rate_tiers is an open map — must survive conversion
    # onto Gemini's additional_properties or the field is un-fillable on Vertex.
    schema = {
        "type": "object",
        "additionalProperties": {
            "type": "object",
            "properties": {
                "rate": {"type": "number"},
                "premium": {"type": "number"},
            },
        },
    }
    out = vg._convert_schema(schema)
    assert out["type"] == "OBJECT"
    assert "additional_properties" in out
    ap = out["additional_properties"]
    assert ap["type"] == "OBJECT"
    assert ap["properties"]["rate"]["type"] == "NUMBER"


def test_convert_schema_roundtrips_through_gemini_schema():
    # The converted dict must actually validate as a google-genai Schema (this
    # is what FunctionDeclaration.parameters coerces it to).
    from google.genai import types

    schema = {
        "type": "object",
        "properties": {
            "rate_tiers": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {"rate": {"type": "number"}},
                },
            },
            "note": {"type": ["string", "null"]},
        },
        "required": ["rate_tiers"],
    }
    coerced = types.Schema(**vg._convert_schema(schema))
    assert coerced.type == types.Type.OBJECT
    assert coerced.properties["rate_tiers"].additional_properties is not None


# ── Adapter response synthesis ────────────────────────────────────────────────


def test_synth_response_function_call_to_tool_use():
    fake = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=42,
            candidates_token_count=7,
            thoughts_token_count=0,
        ),
        candidates=[
            SimpleNamespace(
                finish_reason=SimpleNamespace(name="STOP"),
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            function_call=SimpleNamespace(args={"document_type": "receipt"}),
                            text=None,
                        )
                    ]
                ),
            )
        ],
    )
    resp = vg._synth_response(fake, "emit_document_fields")
    from anthropic.types import ToolUseBlock

    assert resp.stop_reason == "tool_use"
    assert isinstance(resp.content[0], ToolUseBlock)
    assert resp.content[0].input == {"document_type": "receipt"}
    assert resp.content[0].name == "emit_document_fields"
    assert resp.usage.input_tokens == 42
    assert resp.usage.output_tokens == 7


def test_synth_response_counts_thinking_tokens_as_output():
    # Gemini 2.5 Flash bills thoughts_token_count as output — the adapter must
    # fold it in or cost/budget under-count.
    fake = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=23,
            candidates_token_count=6,
            thoughts_token_count=52,
        ),
        candidates=[
            SimpleNamespace(
                finish_reason=SimpleNamespace(name="STOP"),
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            function_call=SimpleNamespace(args={"ok": True}),
                            text=None,
                        )
                    ]
                ),
            )
        ],
    )
    resp = vg._synth_response(fake, "emit")
    assert resp.usage.input_tokens == 23
    assert resp.usage.output_tokens == 58  # 6 visible + 52 thinking


def test_synth_response_marks_max_tokens():
    fake = SimpleNamespace(
        usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1),
        candidates=[
            SimpleNamespace(
                finish_reason=SimpleNamespace(name="MAX_TOKENS"),
                content=SimpleNamespace(parts=[]),
            )
        ],
    )
    resp = vg._synth_response(fake, None)
    assert resp.stop_reason == "max_tokens"


# ── End-to-end adapter create() with fakes ────────────────────────────────────


class _FakeTypes:
    """Minimal stand-in for google.genai.types used by the adapter."""

    @staticmethod
    def Content(role, parts):
        return SimpleNamespace(role=role, parts=parts)

    class Part:
        @staticmethod
        def from_text(text):
            return SimpleNamespace(kind="text", text=text)

        @staticmethod
        def from_bytes(data, mime_type):
            return SimpleNamespace(kind="bytes", data=data, mime_type=mime_type)

    @staticmethod
    def FunctionDeclaration(name, description, parameters):
        return SimpleNamespace(name=name, description=description, parameters=parameters)

    @staticmethod
    def Tool(function_declarations):
        return SimpleNamespace(function_declarations=function_declarations)

    @staticmethod
    def ToolConfig(function_calling_config):
        return SimpleNamespace(function_calling_config=function_calling_config)

    @staticmethod
    def FunctionCallingConfig(mode, allowed_function_names):
        return SimpleNamespace(mode=mode, allowed_function_names=allowed_function_names)

    @staticmethod
    def GenerateContentConfig(**kwargs):
        return SimpleNamespace(**kwargs)


class _FakeErrors:
    class APIError(Exception):
        pass


def test_adapter_create_forces_tool_and_returns_input():
    captured = {}

    def _generate_content(*, model, contents, config):
        captured["model"] = model
        captured["config"] = config
        return SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=10, candidates_token_count=3
            ),
            candidates=[
                SimpleNamespace(
                    finish_reason=SimpleNamespace(name="STOP"),
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                function_call=SimpleNamespace(args={"verdict": "CONFIRMED"}),
                                text=None,
                            )
                        ]
                    ),
                )
            ],
        )

    fake_client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))
    adapter = vg.GeminiClient(_client=fake_client, _types=_FakeTypes, _errors=_FakeErrors)

    resp = adapter.messages.create(
        model="gemini-2.5-flash",
        max_tokens=1024,
        system="you verify claims",
        tools=[
            {
                "name": "emit_verdict",
                "description": "verdict",
                "input_schema": {
                    "type": "object",
                    "properties": {"verdict": {"type": "string"}},
                    "required": ["verdict"],
                },
            }
        ],
        tool_choice={"type": "tool", "name": "emit_verdict"},
        messages=[{"role": "user", "content": "is this real?"}],
    )
    assert resp.content[0].input == {"verdict": "CONFIRMED"}
    # forced-function config threaded through
    assert captured["config"].system_instruction == "you verify claims"
    assert captured["config"].tool_config.function_calling_config.mode == "ANY"


def test_adapter_translates_429_to_ratelimit():
    from anthropic import RateLimitError

    err = _FakeErrors.APIError("quota")
    err.code = 429

    def _boom_coded(*, model, contents, config):
        raise err

    fake_client = SimpleNamespace(models=SimpleNamespace(generate_content=_boom_coded))
    adapter = vg.GeminiClient(_client=fake_client, _types=_FakeTypes, _errors=_FakeErrors)
    with pytest.raises(RateLimitError):
        adapter.messages.create(
            model="gemini-2.5-flash",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )


def test_vertex_from_secret_refuses_non_resident_location_in_prod(monkeypatch):
    """Fail-closed, but DEGRADE — never raise out of the resolution path.

    The platform key is fleet-wide, so raising here would 500 /system/ai-status
    and every AI path for every company at once.
    """
    monkeypatch.setenv("INSPRO_ENV", "prod")
    secret = pack_vertex_secret("inspro-ai", _SA_JSON)
    assert (
        _vertex_from_secret(
            "us-central1", "gemini-2.5-flash", secret, "platform", "the platform key"
        )
        is None
    )


@pytest.mark.parametrize(
    ("capacity_mode", "request_type"),
    [
        ("standard_paygo", "shared"),
        ("provisioned_throughput", "dedicated"),
    ],
)
def test_build_client_routes_capacity_mode(monkeypatch, capacity_mode, request_type):
    """The UI setting must reach Google's capacity-routing request header."""
    from google import genai

    captured = {}

    def _client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(genai, "Client", _client)
    cfg = vg.AIConfig(
        api_key="",
        model="gemini-3.5-flash",
        base_url=None,
        provider="vertex",
        gcp_project="inspro-ai",
        gcp_location="asia-southeast1",
        capacity_mode=capacity_mode,
    )

    vg.build_gemini_client(cfg, timeout=12.5)

    options = captured["http_options"]
    assert options.headers[vg._CAPACITY_HEADER] == request_type
    assert options.timeout == 12_500


def test_build_client_rejects_unknown_capacity_mode(monkeypatch):
    cfg = vg.AIConfig(
        api_key="",
        model="gemini-3.5-flash",
        base_url=None,
        provider="vertex",
        gcp_project="inspro-ai",
        gcp_location="asia-southeast1",
        capacity_mode="stale-ui-value",
    )

    with pytest.raises(ValueError, match="Unsupported Vertex capacity mode"):
        vg.build_gemini_client(cfg)
