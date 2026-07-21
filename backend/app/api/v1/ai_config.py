"""BYOK AI provider endpoints — per-tenant encrypted endpoint + API key.

Gated to `broker_admin`. `system_admin` is intentionally excluded; if/when a
platform-operator surface is needed, it'll get its own router rather than
muddle the "which tenant am I configuring" semantics here.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
)
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.ai_config import (
    DEFAULT_BEDROCK_REGION,
    DEFAULT_MODEL,
    assert_bedrock_residency,
    normalize_foundry_endpoint,
    pack_bedrock_secret,
)
from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.crypto import encrypt_secret, fingerprint
from app.core.deps import require_broker_admin, require_client_id
from app.db.session import get_db
from app.models import AISpendLog, ClientAIConfig
from app.schemas.api import (
    AIConfigOut,
    AIConfigTestPayload,
    AIConfigTestResult,
    AIConfigUpsert,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-config", tags=["ai-config"])

_TEST_PROMPT = "ping"
_TEST_TIMEOUT_SECONDS = 5.0
# Bedrock's first call cold-loads boto3 + builds an AWS session + SigV4-signs
# before the request leaves, so it needs more headroom than a direct HTTP call.
_BEDROCK_TEST_TIMEOUT_SECONDS = 20.0
_TEST_MAX_TOKENS = 1


def _mask_for_fingerprint(fp: str) -> str:
    return "••••" + fp[-4:]


@router.get("", responses={204: {"description": "No BYOK configured"}})
def get_ai_config(
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> Response:
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=AIConfigOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.put("", response_model=AIConfigOut)
def put_ai_config(
    payload: AIConfigUpsert,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> AIConfigOut:
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )
    before: dict[str, Any] | None = None
    if row is None:
        row = ClientAIConfig(client_id=client_id)
        db.add(row)
        action = "create"
    else:
        before = {
            "provider": row.provider,
            "endpoint": row.endpoint,
            "model": row.model,
            "key_fingerprint": row.key_fingerprint,
            "key_masked": _mask_for_fingerprint(row.key_fingerprint),
        }
        action = "update"

    row.provider = payload.provider
    if payload.provider == "bedrock":
        # Store region in `endpoint`, the inference-profile id in `model`, and
        # the AWS credential PAIR packed into the encrypted secret. The
        # fingerprint keys on the secret access key (the sensitive half).
        region = (payload.endpoint or "").strip() or DEFAULT_BEDROCK_REGION
        profile = (payload.model or "").strip()
        access_key_id = (payload.aws_access_key_id or "").strip()
        try:
            assert_bedrock_residency(profile, region)
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        row.endpoint = region
        row.model = profile
        row.encrypted_api_key = encrypt_secret(
            pack_bedrock_secret(access_key_id, payload.api_key)
        )
        row.key_fingerprint = fingerprint(payload.api_key)
    else:
        row.endpoint = payload.endpoint
        row.model = payload.model
        row.encrypted_api_key = encrypt_secret(payload.api_key)
        row.key_fingerprint = fingerprint(payload.api_key)
    # New key — clear stale validation status; the user can re-test.
    row.last_validated_at = None
    row.last_validation_error = None
    db.flush()

    after = {
        "provider": row.provider,
        "endpoint": row.endpoint,
        "model": row.model,
        "key_fingerprint": row.key_fingerprint,
        "key_masked": _mask_for_fingerprint(row.key_fingerprint),
    }
    write_audit(
        db,
        user,
        action=action,
        entity_type="client_ai_config",
        entity_id=row.id,
        before=before,
        after=after,
    )
    db.commit()
    db.refresh(row)
    return AIConfigOut.model_validate(row)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_config(
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> None:
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )
    if row is None:
        return
    snapshot = {
        "provider": row.provider,
        "endpoint": row.endpoint,
        "model": row.model,
        "key_fingerprint": row.key_fingerprint,
    }
    db.delete(row)
    write_audit(
        db,
        user,
        action="delete",
        entity_type="client_ai_config",
        entity_id=row.id,
        before=snapshot,
    )
    db.commit()


@router.post("/test", response_model=AIConfigTestResult)
def test_ai_config(
    payload: AIConfigTestPayload | None = None,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> AIConfigTestResult:
    """Make a minimal Anthropic call against the supplied draft or the stored row.

    Drafts are validated in-memory and never written. Results for the stored
    row update ``last_validated_at`` / ``last_validation_error`` so the UI
    can show a freshness indicator.

    Per-call: ``max_tokens=1, max_retries=0, timeout=5`` so the button can't
    hang the browser. One ``AISpendLog`` row is written either way.
    """
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )

    eff_provider = (payload.provider if payload and payload.provider else None) or (
        row.provider if row else None
    )
    if eff_provider == "bedrock":
        return _run_bedrock_test(db, client_id, payload, row)

    # Resolve effective config: draft fields override stored, but a stored
    # key is reused when the draft doesn't include one.
    p_provider = payload.provider if payload else None
    p_endpoint = payload.endpoint if payload else None
    p_model = payload.model if payload else None
    draft_provider = p_provider or (row.provider if row else None)
    draft_endpoint = p_endpoint or (row.endpoint if row else None)
    draft_model = p_model or (row.model if row else None) or DEFAULT_MODEL
    draft_key: str | None = None
    if payload and payload.api_key:
        draft_key = payload.api_key
    elif row is not None:
        from app.core.crypto import decrypt_secret

        draft_key = decrypt_secret(row.encrypted_api_key)

    if not draft_provider or not draft_key:
        return AIConfigTestResult(
            ok=False,
            error=(
                "No configuration to test. Save a config first, "
                "or include api_key in the request."
            ),
            latency_ms=0,
        )

    client_kwargs: dict[str, Any] = {
        "api_key": draft_key,
        "timeout": _TEST_TIMEOUT_SECONDS,
        "max_retries": 0,
    }
    if draft_provider == "azure_foundry":
        if not draft_endpoint:
            return AIConfigTestResult(
                ok=False,
                error="endpoint is required for provider='azure_foundry'.",
                latency_ms=0,
            )
        try:
            resolved_url = normalize_foundry_endpoint(draft_endpoint)
        except ValueError as exc:
            return AIConfigTestResult(ok=False, error=str(exc), latency_ms=0)
        logger.info(
            "Azure Foundry test — resolved base_url: %s  model: %s",
            resolved_url, draft_model,
        )
        client_kwargs["base_url"] = resolved_url

    started = time.perf_counter()
    error: str | None = None
    try:
        client = Anthropic(**client_kwargs)
        client.messages.create(
            model=draft_model,
            max_tokens=_TEST_MAX_TOKENS,
            messages=[{"role": "user", "content": _TEST_PROMPT}],
        )
    except (AuthenticationError, PermissionDeniedError) as exc:
        base = client_kwargs.get("base_url", "")
        error = f"Credentials rejected by provider: {exc.__class__.__name__} (called: {base})"
    except BadRequestError as exc:
        # Most commonly: unknown model name.
        error = f"Bad request: {exc.message if hasattr(exc, 'message') else str(exc)[:200]}"
    except APITimeoutError:
        error = f"Provider did not respond within {int(_TEST_TIMEOUT_SECONDS)}s."
    except APIConnectionError as exc:
        error = f"Could not reach provider: {str(exc)[:200]}"
    except APIStatusError as exc:
        error = f"Provider returned {exc.status_code}: {str(exc)[:200]}"
    except Exception as exc:
        logger.exception("Unexpected error during BYOK test for client %s", client_id)
        error = f"Unexpected error: {exc.__class__.__name__}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    # Update the stored row's freshness only when we tested the stored config
    # (not a draft) so an unsaved draft test doesn't pollute history.
    is_stored_test = row is not None and (payload is None or payload.api_key is None)
    if is_stored_test and row is not None:
        row.last_validated_at = datetime.now(tz=UTC) if error is None else row.last_validated_at
        row.last_validation_error = error
        db.flush()

    db.add(
        AISpendLog(
            client_id=client_id,
            operation="validate_ai_config",
            model=draft_model,
            input_tokens=0,
            output_tokens=0,
            cost_estimate_usd=0.0,
            cache_hit=False,
        )
    )
    db.commit()
    return AIConfigTestResult(ok=error is None, error=error, latency_ms=latency_ms)


def _run_bedrock_test(
    db: Session,
    client_id: str,
    payload: AIConfigTestPayload | None,
    row: ClientAIConfig | None,
) -> AIConfigTestResult:
    """Minimal Bedrock InvokeModel against a draft or the stored BYOK row.

    Bedrock keeps two secrets (access key id + secret), packed as JSON in the
    stored row, so it can't share the single-``api_key`` path above. Region is
    the stored ``endpoint``; the model is the inference-profile id.
    """
    region = (
        (payload.endpoint if payload else None)
        or (row.endpoint if row else None)
        or DEFAULT_BEDROCK_REGION
    ).strip()
    profile = (
        (payload.model if payload else None) or (row.model if row else None) or ""
    ).strip()

    access_key_id: str | None = None
    secret: str | None = None
    if payload and payload.api_key and payload.aws_access_key_id:
        access_key_id = payload.aws_access_key_id.strip()
        secret = payload.api_key
    elif row is not None:
        from app.core.crypto import decrypt_secret

        try:
            creds = json.loads(decrypt_secret(row.encrypted_api_key))
            access_key_id = str(creds["access_key_id"])
            secret = str(creds["secret_access_key"])
        except Exception:
            return AIConfigTestResult(
                ok=False,
                error="Stored Bedrock credentials are unreadable.",
                latency_ms=0,
            )

    if not profile or not access_key_id or not secret:
        return AIConfigTestResult(
            ok=False,
            error="Enter region, inference-profile id, access key id and secret to test.",
            latency_ms=0,
        )
    try:
        assert_bedrock_residency(profile, region)
    except RuntimeError as exc:
        return AIConfigTestResult(ok=False, error=str(exc), latency_ms=0)

    from anthropic import AnthropicBedrock

    started = time.perf_counter()
    error: str | None = None
    try:
        client = AnthropicBedrock(
            aws_region=region,
            aws_access_key=access_key_id,
            aws_secret_key=secret,
            timeout=_BEDROCK_TEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
        client.messages.create(
            model=profile,
            max_tokens=_TEST_MAX_TOKENS,
            messages=[{"role": "user", "content": _TEST_PROMPT}],
        )
    except (AuthenticationError, PermissionDeniedError) as exc:
        error = f"AWS credentials rejected: {exc.__class__.__name__}"
    except BadRequestError as exc:
        error = f"Bad request: {exc.message if hasattr(exc, 'message') else str(exc)[:200]}"
    except APITimeoutError:
        error = f"Bedrock did not respond within {int(_BEDROCK_TEST_TIMEOUT_SECONDS)}s."
    except APIConnectionError as exc:
        error = f"Could not reach Bedrock: {str(exc)[:200]}"
    except APIStatusError as exc:
        error = f"Bedrock returned {exc.status_code}: {str(exc)[:200]}"
    except Exception as exc:
        logger.exception("Unexpected error during Bedrock BYOK test for client %s", client_id)
        error = f"Unexpected error: {exc.__class__.__name__}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    is_stored_test = row is not None and (payload is None or payload.api_key is None)
    if is_stored_test and row is not None:
        row.last_validated_at = datetime.now(tz=UTC) if error is None else row.last_validated_at
        row.last_validation_error = error
        db.flush()

    db.add(
        AISpendLog(
            client_id=client_id,
            operation="validate_ai_config",
            model=profile,
            input_tokens=0,
            output_tokens=0,
            cost_estimate_usd=0.0,
            cache_hit=False,
        )
    )
    db.commit()
    return AIConfigTestResult(ok=error is None, error=error, latency_ms=latency_ms)
