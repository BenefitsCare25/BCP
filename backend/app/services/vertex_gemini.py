"""Vertex AI (Gemini) provider adapter.

Presents the *Anthropic Messages* surface (``client.messages.create(...)``
returning ``.content`` blocks + ``.usage`` + ``.stop_reason``) over the
``google-genai`` SDK, so every existing call site in ``ai_extractor`` /
``claim_ai`` works unchanged when ``cfg.provider == "vertex"``.

Two translations live here and nowhere else:

1. **Request** — Anthropic content blocks (text / base64 image / base64 PDF)
   → Gemini ``types.Part``; an Anthropic *tool* (forced via ``tool_choice``)
   → a Gemini forced function call (``FunctionCallingConfig(mode="ANY")``);
   the ``input_schema`` JSON-Schema → Gemini's OpenAPI-subset ``Schema``.
2. **Response** — Gemini's ``function_call.args`` → a synthetic
   ``anthropic.types.ToolUseBlock`` (so callers' ``isinstance(b, ToolUseBlock)``
   + ``.input`` keep working); token counts → a ``.usage`` shim.

Provider errors are re-raised as the Anthropic exception types the gateway's
breaker ladder already special-cases (429 → ``RateLimitError``; 401/403 →
``AuthenticationError`` / ``PermissionDeniedError``) so a throttled or
mis-credentialed tenant doesn't trip the global circuit breaker.

The ``google-genai`` / ``google-auth`` imports are lazy — the module only loads
them when a vertex config is actually used, mirroring the bedrock extra.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from anthropic import (
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from anthropic.types import ToolUseBlock

from app.core.ai_config import AIConfig

logger = logging.getLogger(__name__)

# JSON-Schema primitive → Gemini Schema Type enum name.
_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}

_OAUTH_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


# ── Response shims (duck-typed to the anthropic Message surface) ───────────────


@dataclass(frozen=True)
class _Usage:
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class _TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class _Response:
    content: list[Any]
    stop_reason: str
    usage: _Usage
    role: str = "assistant"
    type: str = "message"


# ── Schema / content translation ──────────────────────────────────────────────


def _convert_schema(node: Any) -> dict[str, Any]:
    """JSON-Schema (Anthropic ``input_schema``) → Gemini OpenAPI-subset dict.

    Handles the union-with-null idiom (``{"type": ["string", "null"]}`` →
    ``{"type": "STRING", "nullable": true}``) the review schema uses, recurses
    object properties / array items, and — critically — maps open maps
    (``additionalProperties``, e.g. the slip extractor's ``rate_tiers``) and
    ``anyOf`` onto the fields Gemini's ``Schema`` supports (``additional_properties``
    / ``any_of``); dropping them silently would make those fields un-fillable
    on the Vertex path. Truly unsupported keywords ($ref) are still dropped.
    """
    if not isinstance(node, dict):
        return {}
    out: dict[str, Any] = {}
    raw_type = node.get("type")
    nullable = False
    if isinstance(raw_type, list):
        nullable = "null" in raw_type
        non_null = [t for t in raw_type if t != "null"]
        raw_type = non_null[0] if non_null else None
    if raw_type:
        out["type"] = _TYPE_MAP.get(raw_type, "STRING")
    if nullable:
        out["nullable"] = True
    if node.get("description"):
        out["description"] = node["description"]
    if "enum" in node:
        out["enum"] = [str(e) for e in node["enum"]]
    for bound in ("minimum", "maximum"):
        if bound in node:
            out[bound] = node[bound]
    if "anyOf" in node and isinstance(node["anyOf"], list):
        out["any_of"] = [_convert_schema(s) for s in node["anyOf"]]
    if out.get("type") == "OBJECT":
        props = node.get("properties") or {}
        out["properties"] = {k: _convert_schema(v) for k, v in props.items()}
        if node.get("required"):
            out["required"] = list(node["required"])
        # Open map (no fixed properties) — carry the value schema so Gemini can
        # emit arbitrary keys (e.g. rate_tiers keyed by tier code).
        addl = node.get("additionalProperties")
        if isinstance(addl, dict):
            out["additional_properties"] = _convert_schema(addl)
        elif isinstance(addl, bool):
            out["additional_properties"] = addl
    if out.get("type") == "ARRAY" and "items" in node:
        out["items"] = _convert_schema(node["items"])
    return out


def _content_to_parts(content: Any, types_mod: Any) -> list[Any]:
    """Anthropic message ``content`` (str | list of blocks) → Gemini parts."""
    if isinstance(content, str):
        return [types_mod.Part.from_text(text=content)]
    parts: list[Any] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(types_mod.Part.from_text(text=block.get("text", "")))
        elif btype in ("image", "document"):
            source = block.get("source") or {}
            if source.get("type") != "base64":
                continue
            data = base64.b64decode(source["data"])
            parts.append(
                types_mod.Part.from_bytes(
                    data=data, mime_type=source["media_type"]
                )
            )
    return parts


def _role(anthropic_role: str) -> str:
    return "model" if anthropic_role == "assistant" else "user"


# ── Error translation ─────────────────────────────────────────────────────────


def _translate_error(exc: Exception, code: int | None) -> Exception:
    """Map a google-genai APIError to the anthropic type the gateway expects.

    Only 429 / 401 / 403 need special handling (the breaker ladder). Anything
    else is returned unchanged so the gateway trips the breaker on a genuine
    provider/network fault.
    """
    if code is None:
        return exc
    request = httpx.Request("POST", "https://aiplatform.googleapis.com")
    response = httpx.Response(code, request=request)
    message = str(exc)
    if code == 429:
        return RateLimitError(message, response=response, body=None)
    if code == 401:
        return AuthenticationError(message, response=response, body=None)
    if code == 403:
        return PermissionDeniedError(message, response=response, body=None)
    return exc


# ── The client ────────────────────────────────────────────────────────────────


def _build_credentials(service_account_json: str, auth_mod: Any) -> Any:
    """google-auth service-account credentials from a stored BYOK key string."""
    info = json.loads(service_account_json)
    return auth_mod.Credentials.from_service_account_info(info, scopes=_OAUTH_SCOPES)


@dataclass
class _Messages:
    """The ``.messages`` namespace exposing ``.create(...)``."""

    client: Any
    types_mod: Any
    errors_mod: Any

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        **_ignored: Any,
    ) -> _Response:
        types_mod = self.types_mod
        contents = [
            types_mod.Content(
                role=_role(m.get("role", "user")),
                parts=_content_to_parts(m.get("content", ""), types_mod),
            )
            for m in messages
        ]

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": 0,
        }
        if system:
            config_kwargs["system_instruction"] = system

        forced_name: str | None = None
        if tools:
            declarations = [
                types_mod.FunctionDeclaration(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=_convert_schema(tool["input_schema"]),
                )
                for tool in tools
            ]
            config_kwargs["tools"] = [
                types_mod.Tool(function_declarations=declarations)
            ]
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
                forced_name = tool_choice.get("name")
                config_kwargs["tool_config"] = types_mod.ToolConfig(
                    function_calling_config=types_mod.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=[forced_name] if forced_name else None,
                    )
                )

        config = types_mod.GenerateContentConfig(**config_kwargs)
        try:
            response = self.client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except self.errors_mod.APIError as exc:
            raise _translate_error(exc, getattr(exc, "code", None)) from exc

        return _synth_response(response, forced_name)


@dataclass
class GeminiClient:
    """Duck-typed stand-in for ``Anthropic`` — exposes ``.messages.create``."""

    messages: _Messages = field(init=False)
    _client: Any = None
    _types: Any = None
    _errors: Any = None

    def __post_init__(self) -> None:
        self.messages = _Messages(self._client, self._types, self._errors)


def _synth_response(response: Any, forced_name: str | None) -> _Response:
    usage = getattr(response, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", None)
    # Gemini 2.5 "thinking" models emit reasoning tokens in a separate
    # ``thoughts_token_count`` — Google BILLS these as output, so they must be
    # folded into output_tokens or both cost AND budget under-count (often by
    # 5-10x on short completions). ``candidates_token_count`` alone is only the
    # visible answer.
    output_tokens: int | None = None
    if usage is not None:
        candidates = getattr(usage, "candidates_token_count", None) or 0
        thoughts = getattr(usage, "thoughts_token_count", None) or 0
        output_tokens = candidates + thoughts

    finish_name: str | None = None
    fn_args: dict[str, Any] | None = None
    text_chunks: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        cand = candidates[0]
        finish = getattr(cand, "finish_reason", None)
        finish_name = getattr(finish, "name", None) or (
            str(finish) if finish is not None else None
        )
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            call = getattr(part, "function_call", None)
            if call is not None:
                fn_args = dict(call.args) if call.args else {}
            elif getattr(part, "text", None):
                text_chunks.append(part.text)

    truncated = (finish_name or "").upper() == "MAX_TOKENS"
    if fn_args is not None:
        block = ToolUseBlock(
            id="vertex_tool_call",
            name=forced_name or "",
            input=fn_args,
            type="tool_use",
        )
        stop_reason = "max_tokens" if truncated else "tool_use"
        return _Response(
            content=[block],
            stop_reason=stop_reason,
            usage=_Usage(input_tokens, output_tokens),
        )
    stop_reason = "max_tokens" if truncated else "end_turn"
    return _Response(
        content=[_TextBlock(text="".join(text_chunks))],
        stop_reason=stop_reason,
        usage=_Usage(input_tokens, output_tokens),
    )


def build_gemini_client(cfg: AIConfig, *, timeout: float | None = None) -> GeminiClient:
    """Construct the Vertex Gemini adapter from a tenant/env AI config.

    In BYOK mode ``cfg.api_key`` carries the service-account JSON, so explicit
    credentials are built from it. In env mode it's empty and google-genai
    resolves credentials via the standard ADC chain. ``timeout`` (seconds) is
    applied as the HTTP request timeout so long vision extractions don't get
    cut short.
    """
    try:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types as genai_types
    except ImportError as exc:  # pragma: no cover - dep present in prod image
        raise RuntimeError(
            "provider='vertex' requires the Google SDK: uv add google-genai."
        ) from exc

    credentials = None
    if cfg.api_key:
        try:
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "provider='vertex' BYOK requires google-auth: uv add google-auth."
            ) from exc
        credentials = _build_credentials(cfg.api_key, service_account)

    client_kwargs: dict[str, Any] = {
        "vertexai": True,
        "project": cfg.gcp_project,
        "location": cfg.gcp_location,
        "credentials": credentials,
    }
    if timeout is not None:
        # google-genai HttpOptions.timeout is in milliseconds.
        client_kwargs["http_options"] = genai_types.HttpOptions(
            timeout=int(timeout * 1000)
        )
    client = genai.Client(**client_kwargs)
    return GeminiClient(_client=client, _types=genai_types, _errors=genai_errors)
