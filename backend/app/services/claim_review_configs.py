"""Per-claim-type AI review rule setup — resolution + in-code defaults.

The broker configures the AI claim review PER CLAIM TYPE per company
(``claim_review_configs`` rows, edited on the Claims page "Review rules"
tab): the claim-form ↔ document field maps, the AI-judged business rules
(each with a severity — only a CRITICAL failure can flag a claim; warning/
info failures surface to the broker without auto-flagging), and optionally
the required-document families (empty keeps the automatic slot/sub-type
derivation in ``claims_review/field_maps.py``).

A claim type with no enabled row resolves to the in-code defaults built from
``field_maps.FIELD_MAPS`` + ``field_maps.AI_RULES`` (severity ``critical``,
preserving the pre-config behavior), so the review never depends on config
existing. There is deliberately NO lazy seeding — absence of a row IS the
default, and the UI shows a "Default" badge for such claim types.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, ClaimReviewConfig
from app.models.claim import CLAIM_KIND_FLEX, CLAIM_KIND_INSURED
from app.services.claims_review.field_maps import AI_RULES, FIELD_MAPS

logger = logging.getLogger(__name__)

SEVERITIES = ("critical", "warning", "info")
SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO = SEVERITIES

MATCH_MODES = ("fuzzy", "exact", "numeric")

# Size bounds, mirroring the write-side schema caps. Applied when READING too,
# so a hand-edited / legacy row can neither blow up the review prompt nor make
# the config surface unserializable (see ClaimReviewConfigOut).
MAX_FIELD_MAPS = 30
MAX_AI_RULES = 60
MAX_REQUIRED_DOCS = 15
MAX_PORTAL_FIELD_CHARS = 64
MAX_DOCUMENT_FIELD_CHARS = 128
MAX_RULE_CHARS = 2000
MAX_CATEGORY_CHARS = 64
MAX_ID_CHARS = 64
MAX_REQUIRED_DOC_CHARS = 200

# Categories for the built-in rules, in AI_RULES order.
_DEFAULT_RULE_CATEGORIES = (
    "authenticity",
    "identity",
    "third-party payment",
    "dates",
    "treatment setting",
    "diagnosis",
)


@dataclass(frozen=True)
class AIRule:
    id: str
    rule: str
    category: str
    severity: str  # one of SEVERITIES


@dataclass(frozen=True)
class ReviewConfig:
    """The resolved review configuration one pipeline run works from."""

    field_maps: tuple[dict[str, Any], ...]
    ai_rules: tuple[AIRule, ...]
    # Extra document families required ON TOP of the automatic slot/sub-type
    # derivation (never instead of it — see comparison.compare_claim).
    required_documents: tuple[str, ...] | None
    # Provenance — None/None = the in-code defaults.
    config_id: str | None = None
    config_label: str | None = None

    @property
    def vision_fields(self) -> frozenset[str]:
        """Fields worth spending a vision re-check on when the text pass
        disagrees. Purely a cost/accuracy control."""
        return frozenset(
            str(m.get("portal_field"))
            for m in self.field_maps
            if m.get("verify_with_vision")
        )

    @property
    def evidence_fields(self) -> frozenset[str]:
        """Fields whose MISSING_IN_PDF ("the claim states it, no document
        shows it") must FLAG rather than be papered over by confidence.

        Deliberately INDEPENDENT of ``vision_fields``: turning off a vision
        re-check is a spend decision and must never silently switch off the
        unsubstantiated-value guard (the 2026-07 production audit hardening).
        """
        return frozenset(
            str(m.get("portal_field"))
            for m in self.field_maps
            if m.get("require_evidence")
        )


DEFAULT_AI_RULES: tuple[AIRule, ...] = tuple(
    AIRule(
        id=f"rule_{i + 1}",
        rule=text,
        category=(
            _DEFAULT_RULE_CATEGORIES[i]
            if i < len(_DEFAULT_RULE_CATEGORIES)
            else "general"
        ),
        severity=SEVERITY_CRITICAL,
    )
    for i, text in enumerate(AI_RULES)
)


def default_review_config() -> ReviewConfig:
    # Normalized through the same reader as stored rows so every consumer sees
    # one shape (notably `require_evidence`, which the in-code maps predate).
    return ReviewConfig(
        field_maps=tuple(
            m for m in (_field_map_from_dict(dict(raw)) for raw in FIELD_MAPS) if m
        ),
        ai_rules=DEFAULT_AI_RULES,
        required_documents=None,
    )


def _norm_key(value: str | None) -> str:
    """Canonical comparison key for a claim type / rule text — inner
    whitespace collapsed, casefolded."""
    return " ".join(str(value or "").split()).casefold()


def type_key(claim_kind: str | None, claim_key: str | None) -> str:
    """The claim type's identity, as the frontend must join on it.

    Served on BOTH sides of the join (``ReviewClaimTypeOut.key`` and
    ``ClaimReviewConfigOut.key``) so the UI never recomputes it. It used to be
    mirrored in TypeScript, but ``casefold()`` has no exact JS equivalent
    (``"ß".casefold() == "ss"``, ``"ß".toLowerCase() == "ß"``), and a key that
    drifts is silent: the configured claim type renders as "Default" while its
    rules are live, and "Customize" then 409s ``duplicate_claim_type``.
    """
    return f"{claim_kind or ''}:{_norm_key(claim_key)}"


# The severity tag `rendered_rules` prefixes onto each rule for the prompt.
# It is PROMPT MARKUP, not part of the broker's rule text — strip it before
# anything stores or displays what the model echoed back.
_SEVERITY_PREFIX_RE = re.compile(
    r"^\s*\[(?:" + "|".join(SEVERITIES) + r")\]\s*", re.IGNORECASE
)


def strip_severity_prefix(text: str) -> str:
    return _SEVERITY_PREFIX_RE.sub("", str(text or "")).strip()


def rule_from_dict(raw: Any, index: int) -> AIRule | None:
    """Defensive reading — JSON rows can carry legacy/hand-edited shapes."""
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("rule") or "").strip()[:MAX_RULE_CHARS]
    if not text:
        return None
    severity = str(raw.get("severity") or "").strip().lower()
    if severity not in SEVERITIES:
        # Fail-safe: an unknown severity must never quietly downgrade a rule.
        severity = SEVERITY_CRITICAL
    category = str(raw.get("category") or "general").strip()[:MAX_CATEGORY_CHARS]
    return AIRule(
        id=str(raw.get("id") or f"rule_{index + 1}")[:MAX_ID_CHARS],
        rule=text,
        category=category or "general",
        severity=severity,
    )


def _field_map_from_dict(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    portal = str(raw.get("portal_field") or "").strip()[:MAX_PORTAL_FIELD_CHARS]
    document = str(raw.get("document_field") or "").strip()[:MAX_DOCUMENT_FIELD_CHARS]
    if not portal or not document:
        return None
    mode = str(raw.get("mode") or "fuzzy").strip().lower()
    if mode not in MATCH_MODES:
        mode = "fuzzy"
    vision = bool(raw.get("verify_with_vision"))
    # `require_evidence` was split out of `verify_with_vision` (they used to be
    # one flag). An ABSENT key means the row predates the split — mirror the
    # vision flag so those rows keep their original substantiation behaviour;
    # an explicit False stays False.
    evidence = raw.get("require_evidence")
    out: dict[str, Any] = {
        "portal_field": portal,
        "document_field": document,
        "mode": mode,
        "verify_with_vision": vision,
        "require_evidence": vision if evidence is None else bool(evidence),
    }
    if mode == "numeric":
        try:
            out["tolerance"] = max(0.0, float(raw.get("tolerance") or 0.0))
        except (TypeError, ValueError):
            out["tolerance"] = 0.0
    return out


def config_from_row(row: ClaimReviewConfig) -> ReviewConfig:
    """Build the runtime config from a stored row, defensively.

    Note the deliberate ASYMMETRY between the two lists:

    - ``field_maps`` empty means the row is CORRUPT (the API enforces at least
      one), so fall back to the defaults rather than run a review that compares
      nothing — and log it, because the row will keep rendering as configured.
    - ``ai_rules`` empty is a LEGITIMATE choice (field comparisons only), so it
      is honoured as-is. The UI marks such a setup so the absence of the
      built-in fraud rules can't pass unnoticed.
    """
    field_maps = tuple(
        m for m in (_field_map_from_dict(r) for r in row.field_maps or []) if m
    )[:MAX_FIELD_MAPS]
    if not field_maps:
        logger.warning(
            "Review config %s (%s/%s) has no usable field maps — falling back "
            "to the in-code defaults.", row.id, row.claim_kind, row.claim_key,
        )
    ai_rules = tuple(
        r
        for r in (rule_from_dict(raw, i) for i, raw in enumerate(row.ai_rules or []))
        if r
    )[:MAX_AI_RULES]
    required = tuple(
        s
        for s in (
            str(d).strip()[:MAX_REQUIRED_DOC_CHARS] for d in row.required_documents or []
        )
        if s
    )[:MAX_REQUIRED_DOCS]
    return ReviewConfig(
        field_maps=field_maps or default_review_config().field_maps,
        ai_rules=ai_rules,
        required_documents=required or None,
        config_id=row.id,
        config_label=row.display_label,
    )


def claim_key_for(claim: Claim) -> tuple[str, str]:
    """The claim's config identity — (claim_kind, key)."""
    if claim.claim_kind == CLAIM_KIND_FLEX:
        return CLAIM_KIND_FLEX, claim.flex_category_name or ""
    return CLAIM_KIND_INSURED, claim.product_code or ""


def config_rows(db: Session, client_id: str) -> list[ClaimReviewConfig]:
    return list(
        db.execute(
            select(ClaimReviewConfig)
            .where(ClaimReviewConfig.client_id == client_id)
            .order_by(ClaimReviewConfig.claim_kind, ClaimReviewConfig.display_label)
        ).scalars()
    )


def find_config_row(
    db: Session, client_id: str, claim_kind: str, claim_key: str
) -> ClaimReviewConfig | None:
    """Exact (kind, key) match, key compared normalized/casefolded — flex
    category names come from free-text scheme config."""
    wanted = _norm_key(claim_key)
    if not wanted:
        return None
    rows = db.execute(
        select(ClaimReviewConfig).where(
            ClaimReviewConfig.client_id == client_id,
            ClaimReviewConfig.claim_kind == claim_kind,
        )
    ).scalars()
    return next((r for r in rows if _norm_key(r.claim_key) == wanted), None)


def resolve_review_config(db: Session, claim: Claim) -> ReviewConfig:
    """The configuration this claim's review runs with. Zero-config (no row,
    or the row is disabled) always resolves to the in-code defaults."""
    kind, key = claim_key_for(claim)
    row = find_config_row(db, claim.client_id, kind, key)
    if row is None or not row.enabled:
        return default_review_config()
    return config_from_row(row)


def rendered_rules(config: ReviewConfig) -> list[str]:
    """The severity-tagged strings sent to the AI (and echoed back in
    ``rule_results.rule``) — the tag rides the prompt so the review call's
    cache fingerprint naturally includes the configuration."""
    return [f"[{r.severity.upper()}] {r.rule}" for r in config.ai_rules]


# A containment match needs this much overlapping text to be believable.
# Configured rules are sentences; without a floor, a short rule would match
# an unrelated rule's echoed text (and an EMPTY echo matches everything,
# since "" is a substring of every string).
_MIN_CONTAINMENT_CHARS = 24


def _match_rule(
    echoed: str,
    by_rendered: dict[str, AIRule],
    by_text: dict[str, AIRule],
) -> AIRule | None:
    """Which configured rule the model is reporting on, or None."""
    if not echoed:
        # Nothing to attribute. MUST NOT fall through to containment — an
        # empty string is contained in every rule, so the first configured
        # rule would lend this result its severity, and a warning-severity
        # rule would downgrade an unattributable FAIL into a warning.
        return None
    exact = by_rendered.get(echoed) or by_text.get(echoed)
    if exact is not None:
        return exact
    # Containment fallback — the model sometimes trims or re-prefixes. Take
    # the LONGEST qualifying overlap so the most specific rule wins.
    candidates = [
        (len(k), r)
        for k, r in by_text.items()
        if len(k) >= _MIN_CONTAINMENT_CHARS
        and len(echoed) >= _MIN_CONTAINMENT_CHARS
        and (k in echoed or echoed in k)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


def attribute_rule_results(
    config: ReviewConfig, rule_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Map the AI's echoed rule text back to the configured rules and apply
    severity semantics: a failed WARNING/INFO rule becomes a ``warning``
    result (surfaced, never auto-flags); a failed CRITICAL rule stays
    ``fail``. An unmatched failed rule stays ``fail`` too — text drift must
    never silently downgrade a fraud rule.

    Also normalizes the stored ``rule`` text: the `[CRITICAL]`-style tag is
    prompt markup, so it is stripped before the result reaches the review row
    (and from there the broker's rule-check panel and the flagged reasons).
    """
    by_rendered = {
        _norm_key(s): r
        for s, r in zip(rendered_rules(config), config.ai_rules, strict=True)
    }
    by_text = {_norm_key(r.rule): r for r in config.ai_rules}

    out: list[dict[str, Any]] = []
    for result in rule_results:
        echoed_raw = str(result.get("rule") or "")
        rule = _match_rule(_norm_key(echoed_raw), by_rendered, by_text)
        entry = dict(result)
        if rule is not None:
            # Store the broker's own wording, not the model's echo of it.
            entry["rule"] = rule.rule
            entry["rule_id"] = rule.id
            entry["category"] = rule.category
            entry["severity"] = rule.severity
            if entry.get("status") == "fail" and rule.severity != SEVERITY_CRITICAL:
                entry["status"] = "warning"
        else:
            entry["rule"] = strip_severity_prefix(echoed_raw)
            entry["severity"] = SEVERITY_CRITICAL
        out.append(entry)
    return out
