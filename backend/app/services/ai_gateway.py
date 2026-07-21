"""AI gateway — wraps `generate_rule_via_ai` with cache + breaker + budget.

Every API caller routes through `generate_rule_for_category()` so spend
accounting, breaker semantics, and budget enforcement live in one place.
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from anthropic import AuthenticationError, PermissionDeniedError, RateLimitError
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.ai_config import load_ai_config
from app.models import AISpendLog, Client
from app.schemas.api import AttributeSchemaOut
from app.schemas.rule import RuleEnvelope
from app.services.ai_breaker import CircuitOpenError, get_breaker
from app.services.ai_cache import get_cache, make_key
from app.services.ai_extractor import (
    AINotConfiguredError,
    AIParseError,
    extract_flex_scheme_via_ai,
    extract_slip_structure_via_ai,
    generate_rule_via_ai,
    propose_derivation_rules_via_ai,
    recommend_schema_via_ai,
    render_slip_grid,
)
from app.services.claim_ai import (
    build_claim_review_prompt,
    extract_claim_document_via_ai,
    review_claim_via_ai,
    verify_claim_concern_via_ai,
)

logger = logging.getLogger(__name__)


PROMPT_VERSION = "rule_generation/v1"
DERIVATION_PROMPT_VERSION = "roster_derivation/v1"
RECOMMEND_PROMPT_VERSION = "schema_recommend/v1"
# v2: categories carry financial fields (rates / SI / tiers / earnings) so an
# AI-rescued sheet auto-populates like the deterministic path. The version is
# part of the cache key — bumping it prevents stale v1-shaped cached payloads.
SLIP_EXTRACT_PROMPT_VERSION = "slip_extract/v2"
FLEX_EXTRACT_PROMPT_VERSION = "flex_extract/v1"
CLAIM_EXTRACT_PROMPT_VERSION = "claim_extract/v1"
CLAIM_REVIEW_PROMPT_VERSION = "claim_review/v1"
CLAIM_VERIFY_PROMPT_VERSION = "claim_verify/v1"

# Per-model pricing — $/million tokens. Hardcoded list pricing; override
# per-deploy via INSPRO_AI_PRICE_<MODEL>_IN / _OUT env vars for negotiated
# rates. Keys are lowercased, hyphens preserved. Missing keys fall through
# to `_DEFAULT_PRICE` so an unknown model still gets logged with a non-zero
# (but indicative) cost.
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    # (input_per_million_usd, output_per_million_usd)
    # New sequential names (Azure Foundry deployment names use these)
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # Legacy date-based aliases kept for backwards compat
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-opus-4-7-20251214": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
_DEFAULT_PRICE: tuple[float, float] = (3.0, 15.0)


class AIBudgetExceededError(RuntimeError):
    """Raised when the client's monthly token budget would be exceeded."""


@dataclass(frozen=True)
class AICallResult:
    envelope: RuleEnvelope
    metadata: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True)
class DerivationProposalResult:
    proposals: list[dict[str, Any]]
    metadata: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True)
class SchemaRecommendationResult:
    attributes: list[dict[str, Any]]
    products: list[dict[str, Any]]
    metadata: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True)
class SlipExtractionResult:
    categories: list[dict[str, Any]]
    plans: list[dict[str, Any]]
    metadata: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True)
class FlexExtractionResult:
    scheme: dict[str, Any]
    metadata: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True)
class ClaimExtractionResult:
    document: dict[str, Any]  # {document_type, fields:[...]}
    metadata: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True)
class ClaimReviewAIResult:
    # {field_comparisons, rule_results, required_documents_check, summary, confidence}
    review: dict[str, Any]
    metadata: dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True)
class ClaimVerifyResult:
    verdict: str  # CONFIRMED | REFUTED | UNCERTAIN
    explanation: str
    metadata: dict[str, Any]
    cache_hit: bool


def month_start_utc(now: datetime | None = None) -> datetime:
    n = now or datetime.now(tz=UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_to_date_tokens(db: Session, client_id: str) -> int:
    start = month_start_utc()
    total = db.scalar(
        select(
            func.coalesce(
                func.sum(AISpendLog.input_tokens + AISpendLog.output_tokens),
                0,
            )
        ).where(
            and_(
                AISpendLog.client_id == client_id,
                AISpendLog.created_at >= start,
                AISpendLog.cache_hit.is_(False),
            )
        )
    )
    return int(total or 0)


def _check_budget(db: Session, client_id: str) -> None:
    """Raises AIBudgetExceededError if the client is at or over budget.

    Single-process check; under concurrent calls two requests can both
    pass and both spend. Mitigations: budget is a soft cap (slight over-run
    on bursts is acceptable), per-endpoint rate limits in the router bound
    concurrency, and breaker stops repeated requests when provider stalls.
    For a hard cap, an `UPDATE clients SET tokens=tokens-N WHERE tokens>=N`
    pattern in a future migration would make this atomic.
    """
    client = db.get(Client, client_id)
    budget = client.ai_monthly_token_budget if client else 0
    mtd = month_to_date_tokens(db, client_id)
    if budget > 0 and mtd >= budget:
        raise AIBudgetExceededError(
            f"Client AI budget reached ({mtd} / {budget} tokens this month). "
            "Increase the budget in Schema settings or wait until next month."
        )


_PRICE_ENV_TRANS = str.maketrans("-.", "__")


@lru_cache(maxsize=32)
def _price_for(model: str) -> tuple[float, float]:
    key = model.strip().lower()
    slug = key.upper().translate(_PRICE_ENV_TRANS)
    env_in = os.environ.get(f"INSPRO_AI_PRICE_{slug}_IN", "").strip()
    env_out = os.environ.get(f"INSPRO_AI_PRICE_{slug}_OUT", "").strip()
    default = _PRICE_TABLE.get(key, _DEFAULT_PRICE)
    if not (env_in or env_out):
        return default
    try:
        return (
            float(env_in) if env_in else default[0],
            float(env_out) if env_out else default[1],
        )
    except ValueError:
        logger.warning("Invalid INSPRO_AI_PRICE_* override for %s; using defaults", model)
        return default


def _estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_per_million, out_per_million = _price_for(model)
    return round(
        (input_tokens / 1_000_000) * in_per_million
        + (output_tokens / 1_000_000) * out_per_million,
        6,
    )


def _record_spend(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    operation: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_hit: bool,
) -> None:
    cost = _estimate_cost_usd(model, input_tokens, output_tokens) if not cache_hit else 0.0
    db.add(
        AISpendLog(
            client_id=client_id,
            policy_year_id=policy_year_id,
            operation=operation,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            # For cache hits we leave the on-the-wire spend at zero but the
            # operator can still see hit volume via cache_hit=True rows.
            cost_estimate_usd=cost,
            cache_hit=cache_hit,
        )
    )


def _run_cached_ai_call(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    operation: str,
    cfg: Any,
    cache_key: str,
    on_hit: Callable[[dict[str, Any]], Any],
    invoke: Callable[[], tuple[dict[str, Any], dict[str, Any], Any]],
) -> Any:
    """Shared cache / budget / breaker / spend plumbing for every gateway call.

    ``on_hit(cached_payload)`` rebuilds the result on a cache hit. ``invoke()``
    performs the live provider call and returns ``(cache_payload, metadata,
    live_result)``. The exception ladder below is the SINGLE source of truth for
    which faults trip the circuit breaker (genuine provider/network outages) and
    which don't — credential/config errors and our own parse bugs must not, or a
    single tenant's bad BYOK key would trip the global breaker for everyone.
    """
    cache = get_cache()
    cached = cache.get(cache_key)
    if cached is not None:
        # Log cache hits so admins see hit-rate; zero on-wire tokens.
        _record_spend(
            db, client_id=client_id, policy_year_id=policy_year_id,
            operation=operation, model=cfg.model,
            input_tokens=0, output_tokens=0, cache_hit=True,
        )
        return on_hit(cached)

    _check_budget(db, client_id)

    breaker = get_breaker()
    breaker.before_call()
    try:
        payload, metadata, result = invoke()
    except CircuitOpenError:
        raise
    except AINotConfiguredError:
        raise
    except AIParseError:
        # Our parser bug — don't trip the breaker; re-raise so the caller 502s.
        logger.exception("AI response parse failure (does not trip breaker)")
        raise
    except (AuthenticationError, PermissionDeniedError):
        logger.warning("AI provider rejected credentials for client %s", client_id)
        raise
    except RateLimitError:
        # Provider throttling (HTTP 429) is transient backpressure, not an
        # outage — re-raise so the caller degrades, but DON'T trip the breaker.
        # Tripping it here would take every AI feature down for the whole
        # cooldown on a low-quota account that throttles intermittently.
        logger.warning("AI provider throttled request for client %s (429)", client_id)
        raise
    except Exception:
        # Genuine provider/network failure — trip the breaker.
        breaker.record_failure()
        raise
    breaker.record_success()

    cache.set(cache_key, payload)
    _record_spend(
        db, client_id=client_id, policy_year_id=policy_year_id,
        operation=operation, model=cfg.model,
        input_tokens=int(metadata.get("input_tokens") or 0),
        output_tokens=int(metadata.get("output_tokens") or 0),
        cache_hit=False,
    )
    return result


def generate_rule_for_category(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    description: str,
    schema: list[AttributeSchemaOut],
    operation: str = "ai_suggest_rule",
) -> AICallResult:
    """Cached + breakered + budget-gated rule generation."""
    cfg = load_ai_config(db, client_id)
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Either configure a tenant key on "
            "/schema/ai-provider, or set AZURE_FOUNDRY_ENDPOINT + AZURE_FOUNDRY_API_KEY."
        )

    cache_key = make_key(
        PROMPT_VERSION,
        cfg.model,
        {
            "description": description.strip(),
            "schema": sorted(s.attribute_id for s in schema),
        },
    )

    def _on_hit(cached: dict[str, Any]) -> AICallResult:
        return AICallResult(
            envelope=RuleEnvelope(**cached["envelope"]),
            metadata={**cached["metadata"], "cache_hit": True},
            cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], AICallResult]:
        envelope, metadata = generate_rule_via_ai(description, schema, cfg)
        payload = {"envelope": envelope.model_dump(), "metadata": metadata}
        result = AICallResult(
            envelope=envelope, metadata={**metadata, "cache_hit": False}, cache_hit=False
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )


def propose_derivation_for_roster(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    columns: list[dict[str, Any]],
    targets: list[AttributeSchemaOut],
    operation: str = "ai_profile_roster",
) -> DerivationProposalResult:
    """Cached + breakered + budget-gated roster derivation-rule proposal.

    Mirrors `generate_rule_for_category`: same cache/breaker/budget/spend
    plumbing so roster profiling is governed by the existing AI controls.
    """
    cfg = load_ai_config(db, client_id)
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Either configure a tenant key on "
            "/schema/ai-provider, or set AZURE_FOUNDRY_ENDPOINT + AZURE_FOUNDRY_API_KEY."
        )

    cache_key = make_key(
        DERIVATION_PROMPT_VERSION,
        cfg.model,
        {
            # Cache on the column fingerprint (key + samples) and target set so
            # re-profiling an identical roster shape is free.
            "columns": [
                {"key": c["key"], "samples": sorted(c.get("samples", []))}
                for c in sorted(columns, key=lambda c: c["key"])
            ],
            "targets": sorted(t.attribute_id for t in targets),
        },
    )

    def _on_hit(cached: dict[str, Any]) -> DerivationProposalResult:
        return DerivationProposalResult(
            proposals=cached["proposals"],
            metadata={**cached["metadata"], "cache_hit": True},
            cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], DerivationProposalResult]:
        proposals, metadata = propose_derivation_rules_via_ai(columns, targets, cfg)
        payload = {"proposals": proposals, "metadata": metadata}
        result = DerivationProposalResult(
            proposals=proposals, metadata={**metadata, "cache_hit": False}, cache_hit=False
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )


def recommend_schema_for_slip(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    category_descriptions: list[str],
    product_candidates: list[dict[str, Any]],
    existing_attributes: list[AttributeSchemaOut],
    existing_product_codes: list[str],
    operation: str = "ai_recommend_config",
) -> SchemaRecommendationResult:
    """Cached + breakered + budget-gated slip-driven schema/product recommendation.

    Mirrors `generate_rule_for_category`: same cache/breaker/budget/spend
    plumbing so the recommendation is governed by the existing AI controls.
    """
    cfg = load_ai_config(db, client_id)
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Either configure a tenant key on "
            "/schema/ai-provider, or set AZURE_FOUNDRY_ENDPOINT + AZURE_FOUNDRY_API_KEY."
        )

    cache_key = make_key(
        RECOMMEND_PROMPT_VERSION,
        cfg.model,
        {
            "categories": sorted(set(category_descriptions)),
            # Fingerprint everything the prompt actually sends, not just the
            # codes/ids — sample categories and an attribute's type/enum values
            # all change the model's output, so omitting them serves stale hits.
            "product_candidates": [
                {"code": c["code"], "samples": sorted(c.get("sample_categories", []))}
                for c in sorted(product_candidates, key=lambda c: c["code"])
            ],
            "existing_attributes": [
                {"id": a.attribute_id, "type": a.data_type,
                 "enum": sorted(a.enum_values or [])}
                for a in sorted(existing_attributes, key=lambda a: a.attribute_id)
            ],
            "existing_products": sorted(existing_product_codes),
        },
    )
    def _on_hit(cached: dict[str, Any]) -> SchemaRecommendationResult:
        return SchemaRecommendationResult(
            attributes=cached["attributes"],
            products=cached["products"],
            metadata={**cached["metadata"], "cache_hit": True},
            cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], SchemaRecommendationResult]:
        raw, metadata = recommend_schema_via_ai(
            category_descriptions,
            product_candidates,
            existing_attributes,
            existing_product_codes,
            cfg,
        )
        payload = {
            "attributes": raw["attributes"], "products": raw["products"],
            "metadata": metadata,
        }
        result = SchemaRecommendationResult(
            attributes=raw["attributes"], products=raw["products"],
            metadata={**metadata, "cache_hit": False}, cache_hit=False,
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )


def extract_flex_scheme(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    text: str,
    images: list[dict[str, Any]],
    operation: str = "ai_extract_flex",
) -> FlexExtractionResult:
    """Cached + breakered + budget-gated AI extraction of a Flexible-Benefits scheme.

    ``images`` is a list of ``{"media_type", "data"(base64)}`` blocks (the source
    tables are frequently images). Mirrors `extract_product_structure_for_slip`:
    same cache/breaker/budget/spend plumbing so the call is governed by the
    existing AI controls.
    """
    cfg = load_ai_config(db, client_id)
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Configure a tenant key on "
            "/schema/ai-provider, or set AZURE_FOUNDRY_* env vars."
        )

    cache_key = make_key(
        FLEX_EXTRACT_PROMPT_VERSION,
        cfg.model,
        # Digest the FULL text (not a prefix) plus a per-image content hash so an
        # identical document is free, but documents that diverge anywhere — even
        # past the first pages — never collide onto the same cached scheme.
        {
            "text": hashlib.sha256(text.strip().encode("utf-8")).hexdigest(),
            "images": sorted(
                hashlib.sha256(i["data"].encode()).hexdigest()[:16] for i in images
            ),
        },
    )

    def _on_hit(cached: dict[str, Any]) -> FlexExtractionResult:
        return FlexExtractionResult(
            scheme=cached["scheme"],
            metadata={**cached["metadata"], "cache_hit": True},
            cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], FlexExtractionResult]:
        raw, metadata = extract_flex_scheme_via_ai(text, images, cfg)
        payload = {"scheme": raw["scheme"], "metadata": metadata}
        result = FlexExtractionResult(
            scheme=raw["scheme"], metadata={**metadata, "cache_hit": False}, cache_hit=False
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )


def extract_product_structure_for_slip(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    product_code: str,
    grid: list[list[Any]],
    operation: str = "ai_extract_slip",
) -> SlipExtractionResult:
    """Cached + breakered + budget-gated AI extraction of one product sheet.

    Mirrors `recommend_schema_for_slip`: same cache/breaker/budget/spend plumbing
    so the fallback is governed by the existing AI controls.
    """
    cfg = load_ai_config(db, client_id)
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Configure a tenant key on "
            "/schema/ai-provider, or set AZURE_FOUNDRY_* env vars."
        )

    cache_key = make_key(
        SLIP_EXTRACT_PROMPT_VERSION,
        cfg.model,
        # Key on the exact text the model receives (truncated/rendered), not the
        # raw grid — otherwise rows/cols beyond the prompt window force cache
        # misses for an identical request.
        {"product_code": product_code, "grid": render_slip_grid(grid)},
    )
    def _on_hit(cached: dict[str, Any]) -> SlipExtractionResult:
        return SlipExtractionResult(
            categories=cached["categories"], plans=cached["plans"],
            metadata={**cached["metadata"], "cache_hit": True}, cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], SlipExtractionResult]:
        raw, metadata = extract_slip_structure_via_ai(grid, product_code, cfg)
        payload = {
            "categories": raw["categories"], "plans": raw["plans"], "metadata": metadata,
        }
        result = SlipExtractionResult(
            categories=raw["categories"], plans=raw["plans"],
            metadata={**metadata, "cache_hit": False}, cache_hit=False,
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )


def _require_ai_config(db: Session, client_id: str) -> Any:
    cfg = load_ai_config(db, client_id)
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Configure a tenant key on "
            "/schema/ai-provider, or set AZURE_FOUNDRY_* env vars."
        )
    return cfg


def extract_claim_document(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    sha256: str,
    blocks: list[dict[str, Any]],
    file_name: str,
    operation: str = "ai_claim_extract",
) -> ClaimExtractionResult:
    """Cached + breakered + budget-gated field extraction of one claim document.

    Cache key = the document's SHA-256 + prompt version, NOT the blocks — a
    resubmitted receipt (same bytes) is a guaranteed cache hit.
    """
    cfg = _require_ai_config(db, client_id)
    cache_key = make_key(
        CLAIM_EXTRACT_PROMPT_VERSION, cfg.model, {"sha256": sha256}
    )

    def _on_hit(cached: dict[str, Any]) -> ClaimExtractionResult:
        return ClaimExtractionResult(
            document=cached["document"],
            metadata={**cached["metadata"], "cache_hit": True},
            cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], ClaimExtractionResult]:
        raw, metadata = extract_claim_document_via_ai(blocks, file_name, cfg)
        payload = {"document": raw, "metadata": metadata}
        result = ClaimExtractionResult(
            document=raw, metadata={**metadata, "cache_hit": False}, cache_hit=False
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )


def review_claim(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    claim_fields: dict[str, Any],
    documents: list[dict[str, Any]],
    field_maps: list[dict[str, Any]],
    ai_rules: list[str],
    required_documents: list[str],
    operation: str = "ai_claim_review",
) -> ClaimReviewAIResult:
    """Cached + breakered + budget-gated claim ↔ documents comparison.

    Cache key = digest of the exact user prompt the model receives, so an
    identical (claim form, extractions, maps, rules) rerun is free but any
    change anywhere invalidates.
    """
    cfg = _require_ai_config(db, client_id)
    prompt = build_claim_review_prompt(
        claim_fields, documents, field_maps, ai_rules, required_documents
    )
    cache_key = make_key(
        CLAIM_REVIEW_PROMPT_VERSION,
        cfg.model,
        {"prompt": hashlib.sha256(prompt.encode("utf-8")).hexdigest()},
    )

    def _on_hit(cached: dict[str, Any]) -> ClaimReviewAIResult:
        return ClaimReviewAIResult(
            review=cached["review"],
            metadata={**cached["metadata"], "cache_hit": True},
            cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], ClaimReviewAIResult]:
        raw, metadata = review_claim_via_ai(
            claim_fields, documents, field_maps, ai_rules, required_documents, cfg
        )
        payload = {"review": raw, "metadata": metadata}
        result = ClaimReviewAIResult(
            review=raw, metadata={**metadata, "cache_hit": False}, cache_hit=False
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )


def verify_claim_concern(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str | None,
    claim_id: str,
    question: str,
    doc_sha256: str,
    blocks: list[dict[str, Any]],
    operation: str = "ai_claim_vision_verify",
) -> ClaimVerifyResult:
    """Cached + breakered + budget-gated vision re-check of a single concern.

    Cache key includes the claim id (per the plan) so verifications are scoped
    per claim even when two claims share a receipt hash + question text.
    """
    cfg = _require_ai_config(db, client_id)
    cache_key = make_key(
        CLAIM_VERIFY_PROMPT_VERSION,
        cfg.model,
        {"claim_id": claim_id, "question": question, "sha256": doc_sha256},
    )

    def _on_hit(cached: dict[str, Any]) -> ClaimVerifyResult:
        return ClaimVerifyResult(
            verdict=cached["verdict"],
            explanation=cached["explanation"],
            metadata={**cached["metadata"], "cache_hit": True},
            cache_hit=True,
        )

    def _invoke() -> tuple[dict[str, Any], dict[str, Any], ClaimVerifyResult]:
        raw, metadata = verify_claim_concern_via_ai(question, blocks, cfg)
        payload = {
            "verdict": raw["verdict"], "explanation": raw["explanation"],
            "metadata": metadata,
        }
        result = ClaimVerifyResult(
            verdict=raw["verdict"], explanation=raw["explanation"],
            metadata={**metadata, "cache_hit": False}, cache_hit=False,
        )
        return payload, metadata, result

    return _run_cached_ai_call(
        db, client_id=client_id, policy_year_id=policy_year_id, operation=operation,
        cfg=cfg, cache_key=cache_key, on_hit=_on_hit, invoke=_invoke,
    )
