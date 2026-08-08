"""AI-driven rule generation via Google Vertex AI (Gemini).

Routes a category description and the available employee attribute schema
through Gemini, forcing structured output via a tool call. Returns the same
RuleEnvelope shape that the deterministic generator emits, so downstream
consumers don't branch.
"""
from __future__ import annotations

from typing import Any

from anthropic.types import ToolUseBlock

from app.core.ai_config import AIConfig, load_ai_config
from app.schemas.api import AttributeSchemaOut
from app.schemas.rule import RuleEnvelope

# Provider call timeout — bound it explicitly so an AI call can't outlast any
# FastAPI request budget.
_PROVIDER_TIMEOUT_SECONDS = 30.0

# Roster profiling emits one rule per target attribute (often a dozen+), so it
# needs a larger output budget and more wall-clock than single-rule generation.
# Under-budgeting truncates the tool JSON mid-array → an unparseable payload.
_DERIVATION_MAX_TOKENS = 4096
_DERIVATION_TIMEOUT_SECONDS = 60.0
# Cap sample values sent per column to keep the prompt (and spend) bounded;
# high-cardinality / free-text columns get fewer — a handful is enough for the
# model to recognise the shape without shipping hundreds of names or salaries.
_AI_SAMPLES_PER_COLUMN = 20
_AI_SAMPLES_HIGH_CARDINALITY = 6
_HIGH_CARDINALITY_THRESHOLD = 60

SYSTEM_PROMPT = """You are an expert at converting insurance category eligibility \
descriptions into structured JSONLogic predicates.

You will receive:
1. A short text describing which employees a placement-slip category covers.
2. A list of employee attributes available in the schema, with their types and enum values.

Your job: emit a JSONLogic predicate selecting matching employees. Use ONLY the attributes provided.

JSONLogic shape:
- Comparison: {"=": ["attr", value]}, {"!=": [...]}, {">=": [...]}, {"<=": [...]}, {">": [...]}, {"<": [...]}
- Range: {"between": ["attr", lo, hi]}
- Set: {"in": ["attr", [v1, v2, ...]]}, {"not_in": [...]}
- Combinators: {"and": [c1, c2, ...]}, {"or": [c1, c2, ...]}, {"not": c}
- Empty AND ({"and": []}) means "match all employees" (use for "All Employees").

Rules:
- Prefer the most specific shape that's correct. Don't over-restrict.
- If the description is ambiguous or you can't map any clause to the schema, return rule=null with a low confidence score.
- Confidence should reflect how confident you are the rule is correct, NOT how literal the translation was. Cap at 0.85.
- human_readable: a one-line English summary of the rule, formatted for an admin to scan.
"""


TOOL_SCHEMA = {
    "name": "emit_rule",
    "description": "Emit the structured matching rule for a placement-slip category.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rule": {
                "description": "JSONLogic predicate, or null if no rule can be derived.",
                "type": ["object", "null"],
            },
            "human_readable": {
                "type": "string",
                "description": "One-line English summary of the rule.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 0.85,
                "description": "How confident the model is that this rule is correct (max 0.85).",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief one-sentence justification.",
            },
        },
        "required": ["rule", "human_readable", "confidence", "reasoning"],
    },
}


def _build_ai_client(cfg: AIConfig, *, timeout: float) -> Any:
    """Construct the model client from a tenant/env AI config.

    Vertex/Gemini is the only provider: returns the ``vertex_gemini`` adapter,
    which presents the Anthropic ``.messages.create(...)`` surface over
    google-genai so callers don't branch on provider.
    """
    if cfg.provider != "vertex":
        raise AINotConfiguredError(
            f"Unsupported AI provider {cfg.provider!r}; only 'vertex' (Gemini) "
            "is supported."
        )
    from app.services.vertex_gemini import build_gemini_client

    return build_gemini_client(cfg, timeout=timeout)


class AINotConfiguredError(RuntimeError):
    """Raised when no AI provider is configured. Caller should fall back gracefully."""


class AIParseError(RuntimeError):
    """Raised when the provider responded but the response was malformed.

    Separate from provider/network errors so the circuit breaker does NOT
    treat our own parser bugs as upstream outages.
    """


def _build_user_prompt(description: str, schema: list[AttributeSchemaOut]) -> str:
    schema_summary = []
    for attr in schema:
        line = f"- {attr.attribute_id} ({attr.data_type}): {attr.display_name}"
        if attr.enum_values:
            line += f" — values: {attr.enum_values}"
        if attr.description:
            line += f" — {attr.description}"
        schema_summary.append(line)
    return (
        "Available employee attributes:\n"
        + "\n".join(schema_summary)
        + "\n\n"
        + f"Category description:\n{description.strip()}\n\n"
        + "Call emit_rule with the structured output."
    )


def generate_rule_via_ai(
    description: str,
    schema: list[AttributeSchemaOut],
    config: AIConfig | None = None,
) -> tuple[RuleEnvelope, dict[str, Any]]:
    """Generate a rule for a single category description via Claude.

    Returns the envelope plus a metadata dict (tokens, model, provider) so the
    caller can record AI spend and provenance.

    Raises:
        AINotConfiguredError: no provider credentials in env.
        AIParseError: provider returned a response we couldn't parse — should
            NOT trip the circuit breaker.
        Other Exception: provider/network failure — does trip the breaker.
    """
    cfg = config or load_ai_config()
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Set INSPRO_AI_PROVIDER=vertex + "
            "VERTEX_PROJECT (Google ADC for local dev), or configure a tenant "
            "BYOK key (service-account JSON) on the AI provider settings page."
        )

    client = _build_ai_client(cfg, timeout=_PROVIDER_TIMEOUT_SECONDS)

    response = client.messages.create(
        model=cfg.model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "emit_rule"},
        messages=[{"role": "user", "content": _build_user_prompt(description, schema)}],
    )

    tool_use = next(
        (b for b in response.content if isinstance(b, ToolUseBlock)),
        None,
    )
    if tool_use is None:
        raise AIParseError("AI did not return a tool_use block")

    raw_payload = tool_use.input
    if not isinstance(raw_payload, dict):
        raise AIParseError(
            f"AI tool_use payload is not a dict (got {type(raw_payload).__name__})"
        )
    payload: dict[str, Any] = raw_payload

    try:
        envelope = RuleEnvelope(
            rule=payload.get("rule"),
            human_readable=str(payload.get("human_readable", "(no summary)")),
            confidence=float(payload.get("confidence", 0.0)),
            needs_review=True,  # AI output always needs admin review per brief §9.3
        )
    except (TypeError, ValueError) as exc:
        raise AIParseError(f"AI payload failed validation: {exc}") from exc

    metadata = {
        "provider": cfg.provider,
        "model": cfg.model,
        "input_tokens": getattr(response.usage, "input_tokens", None),
        "output_tokens": getattr(response.usage, "output_tokens", None),
        "reasoning": str(payload.get("reasoning", "")),
    }
    return envelope, metadata


# ── Roster derivation-rule proposal ──────────────────────────────────────────

DERIVATION_SYSTEM_PROMPT = """You are an expert at configuring attribute-derivation \
rules for an insurance employee roster.

You will receive:
1. Target attributes the system needs to derive (id, type, enum values, description).
2. The roster's raw columns, each with a sample of its distinct values.

For each target attribute, decide which raw column (if any) it derives from and \
emit a derivation rule using ONLY these three ops:

- regex_extract: pull a capture group out of a source column.
  {"op":"regex_extract","source":"<column>","pattern":"<regex with ONE group>","group":1,"cast":"int"|"float"|null}
- regex_case: first-matching-pattern wins, mapping to a literal value (use for enums).
  {"op":"regex_case","source":"<column>","cases":[{"pattern":"<regex>","value":"<ENUM>"},...],"default":null}
- passthrough: copy a raw column through unchanged.
  {"op":"passthrough","source":"<column>"}

Rules:
- Patterns are Python regex, applied case-insensitively. Keep them simple and robust.
- For enum targets, map ONLY to the provided enum values; cover the sample values you see.
- For integer/float targets use regex_extract with the matching cast.
- If NO column can produce the attribute, set mappable=false, rule=null, and say why \
in reasoning (e.g. "no column contains occupation information"). Do NOT invent a source.
- confidence reflects how sure you are the rule is correct (cap 0.85).
"""

DERIVATION_TOOL_SCHEMA = {
    "name": "emit_derivation_rules",
    "description": "Emit a derivation-rule proposal for each target attribute.",
    "input_schema": {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "attribute_id": {"type": "string"},
                        "source": {
                            "type": ["string", "null"],
                            "description": "Raw column the rule reads from, or null if unmappable.",
                        },
                        "derivation_rule": {
                            "type": ["object", "null"],
                            "description": "regex_extract / regex_case / passthrough spec, or null.",
                        },
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 0.85},
                        "mappable": {"type": "boolean"},
                        "reasoning": {"type": "string"},
                    },
                    "required": [
                        "attribute_id",
                        "source",
                        "derivation_rule",
                        "confidence",
                        "mappable",
                        "reasoning",
                    ],
                },
            }
        },
        "required": ["proposals"],
    },
}


def _build_derivation_prompt(
    columns: list[dict[str, Any]], targets: list[AttributeSchemaOut]
) -> str:
    target_lines = []
    for t in targets:
        line = f"- {t.attribute_id} ({t.data_type})"
        if t.enum_values:
            line += f" — allowed values: {t.enum_values}"
        if t.description:
            line += f" — {t.description}"
        target_lines.append(line)

    column_lines = []
    for c in columns:
        # Trim sample volume per column — fewer for high-cardinality columns
        # whose individual values (names, salaries) don't help infer a rule.
        cap = (
            _AI_SAMPLES_HIGH_CARDINALITY
            if c.get("distinct_count", 0) > _HIGH_CARDINALITY_THRESHOLD
            else _AI_SAMPLES_PER_COLUMN
        )
        samples = ", ".join(repr(s) for s in c["samples"][:cap])
        column_lines.append(
            f"- {c['key']} ({c['distinct_count']} distinct, {c['total']} filled): {samples}"
        )

    return (
        "Target attributes to derive:\n"
        + "\n".join(target_lines)
        + "\n\nRaw roster columns and sample values:\n"
        + "\n".join(column_lines)
        + "\n\nCall emit_derivation_rules with one proposal per target attribute."
    )


def propose_derivation_rules_via_ai(
    columns: list[dict[str, Any]],
    targets: list[AttributeSchemaOut],
    config: AIConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask the model to propose a derivation rule per target attribute.

    `columns` is the roster profile (each: key, samples, distinct_count, total).
    Returns (proposals, metadata). Proposals are raw dicts — the caller must
    validate each rule (compile + run on the sample) before trusting it.
    """
    cfg = config or load_ai_config()
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Set INSPRO_AI_PROVIDER=vertex + "
            "VERTEX_PROJECT (Google ADC for local dev), or configure a tenant "
            "BYOK key (service-account JSON) on the AI provider settings page."
        )

    client = _build_ai_client(cfg, timeout=_DERIVATION_TIMEOUT_SECONDS)

    response = client.messages.create(
        model=cfg.model,
        max_tokens=_DERIVATION_MAX_TOKENS,
        system=DERIVATION_SYSTEM_PROMPT,
        tools=[DERIVATION_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "emit_derivation_rules"},
        messages=[{"role": "user", "content": _build_derivation_prompt(columns, targets)}],
    )

    # A truncated tool call (hit the output cap mid-array) yields invalid/partial
    # JSON — surface it explicitly rather than as a generic "missing proposals".
    if response.stop_reason == "max_tokens":
        raise AIParseError(
            "AI response truncated (max_tokens) — too many target attributes for one pass."
        )

    tool_use = next((b for b in response.content if isinstance(b, ToolUseBlock)), None)
    if tool_use is None:
        raise AIParseError("AI did not return a tool_use block")
    raw_payload = tool_use.input
    if not isinstance(raw_payload, dict) or not isinstance(raw_payload.get("proposals"), list):
        raise AIParseError("AI derivation payload missing 'proposals' list")

    proposals = [p for p in raw_payload["proposals"] if isinstance(p, dict)]
    metadata = {
        "provider": cfg.provider,
        "model": cfg.model,
        "input_tokens": getattr(response.usage, "input_tokens", None),
        "output_tokens": getattr(response.usage, "output_tokens", None),
    }
    return proposals, metadata


# ── Slip-driven schema / product recommendation ──────────────────────────────

# This pass can emit a dozen+ attributes and several products, so it needs a
# generous output budget and wall-clock — under-budgeting truncates the tool
# JSON mid-array into an unparseable payload.
_RECOMMEND_MAX_TOKENS = 4096
_RECOMMEND_TIMEOUT_SECONDS = 60.0
# Cap distinct category descriptions sent — enough to infer the attribute
# surface without shipping every row of a large multi-product slip.
_MAX_CATEGORY_DESCRIPTIONS = 120
# Allowed attribute data types the model may propose (mirrors the seed schema).
RECOMMEND_DATA_TYPES = ("string", "integer", "decimal", "boolean", "enum", "date")

RECOMMEND_SYSTEM_PROMPT = """You are an expert at configuring an insurance \
group-benefits platform for a new corporate client.

You will receive:
1. Eligibility category descriptions extracted from the client's placement slip \
(free text describing which employees each benefit category covers).
2. Product codes detected on the slip that are NOT yet in the catalog, each with \
a few sample category descriptions.
3. The employee attributes already configured, and the product codes already in \
the catalog (so you don't duplicate them).

Two jobs:

A) Recommend the employee attributes needed to evaluate these eligibility \
categories as structured matching rules. For each attribute give:
- attribute_id: snake_case identifier (e.g. "job_grade", "pass_type").
- display_name: human label.
- data_type: ONE of string | integer | decimal | boolean | enum | date.
- enum_values: REQUIRED non-empty list when data_type is enum (cover the values \
the descriptions imply, e.g. ["WP","SP","EP"]); null otherwise.
- is_pii: true if the attribute holds personally identifiable / sensitive data \
(e.g. NRIC/FIN, passport number, full name, date of birth, home address, exact \
salary); false for structural fields like grade, pass type, or class.
- description: one line on what it captures.
- reasoning: which description(s) imply this attribute.
Recommend the standard Singapore group-insurance attributes when the \
descriptions imply them (grade/job grade, pass type WP/SP/EP, employee class \
such as BARGAINABLE, occupation/role, salary, age, geography). Including an \
attribute that already exists is fine — duplicates are removed downstream. Do \
NOT invent attributes the descriptions don't imply.

B) For EACH detected product code missing from the catalog, propose catalog \
metadata:
- code: echo the given code EXACTLY.
- display_name: expand the abbreviation (e.g. GHS -> "Group Hospital & Surgical").
- insurer: null unless clearly named in the descriptions.
- participation_model: standard | extended | eo_only (default standard).
- has_dependants: true for medical/hospital/dental plans that can cover family.
- is_outpatient: true for outpatient/GP/specialist/clinic plans.
- reasoning: brief justification.
Only propose products for the codes provided — never invent product codes.

Be precise and conservative. Prefer correctness over coverage."""

RECOMMEND_TOOL_SCHEMA = {
    "name": "emit_recommendations",
    "description": "Emit recommended employee attributes and product-catalog entries.",
    "input_schema": {
        "type": "object",
        "properties": {
            "attributes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "attribute_id": {"type": "string"},
                        "display_name": {"type": "string"},
                        "data_type": {"type": "string", "enum": list(RECOMMEND_DATA_TYPES)},
                        "enum_values": {"type": ["array", "null"], "items": {"type": "string"}},
                        "is_pii": {"type": "boolean"},
                        "description": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["attribute_id", "display_name", "data_type", "reasoning"],
                },
            },
            "products": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "display_name": {"type": "string"},
                        "insurer": {"type": ["string", "null"]},
                        "participation_model": {
                            "type": "string",
                            "enum": ["standard", "extended", "eo_only"],
                        },
                        "has_dependants": {"type": "boolean"},
                        "is_outpatient": {"type": "boolean"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["code", "display_name", "has_dependants", "is_outpatient"],
                },
            },
        },
        "required": ["attributes", "products"],
    },
}


def _build_recommend_prompt(
    category_descriptions: list[str],
    product_candidates: list[dict[str, Any]],
    existing_attributes: list[AttributeSchemaOut],
    existing_product_codes: list[str],
) -> str:
    cats = category_descriptions[:_MAX_CATEGORY_DESCRIPTIONS]
    cat_block = "\n".join(f"- {c}" for c in cats) or "(none)"

    existing_attr_block = (
        "\n".join(
            f"- {a.attribute_id} ({a.data_type})"
            + (f" — values: {a.enum_values}" if a.enum_values else "")
            for a in existing_attributes
        )
        or "(none)"
    )

    if product_candidates:
        cand_lines = []
        for c in product_candidates:
            samples = "; ".join(c.get("sample_categories", [])[:5])
            cand_lines.append(f"- {c['code']}: {samples}" if samples else f"- {c['code']}")
        cand_block = "\n".join(cand_lines)
    else:
        cand_block = "(none — every detected product already exists)"

    existing_codes_block = ", ".join(sorted(existing_product_codes)) or "(none)"

    return (
        "Eligibility category descriptions from the placement slip:\n"
        + cat_block
        + "\n\nDetected product codes MISSING from the catalog (propose metadata "
        "for each):\n"
        + cand_block
        + "\n\nEmployee attributes already configured:\n"
        + existing_attr_block
        + "\n\nProduct codes already in the catalog (do not re-propose):\n"
        + existing_codes_block
        + "\n\nCall emit_recommendations with the structured output."
    )


def recommend_schema_via_ai(
    category_descriptions: list[str],
    product_candidates: list[dict[str, Any]],
    existing_attributes: list[AttributeSchemaOut],
    existing_product_codes: list[str],
    config: AIConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recommend employee attributes + product-catalog entries from a slip.

    Returns ``(payload, metadata)`` where payload has ``attributes`` and
    ``products`` lists of raw dicts — the caller validates/de-dupes them.
    """
    cfg = config or load_ai_config()
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Set INSPRO_AI_PROVIDER=vertex + "
            "VERTEX_PROJECT (Google ADC for local dev), or configure a tenant "
            "BYOK key (service-account JSON) on the AI provider settings page."
        )

    client = _build_ai_client(cfg, timeout=_RECOMMEND_TIMEOUT_SECONDS)

    response = client.messages.create(
        model=cfg.model,
        max_tokens=_RECOMMEND_MAX_TOKENS,
        system=RECOMMEND_SYSTEM_PROMPT,
        tools=[RECOMMEND_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "emit_recommendations"},
        messages=[
            {
                "role": "user",
                "content": _build_recommend_prompt(
                    category_descriptions,
                    product_candidates,
                    existing_attributes,
                    existing_product_codes,
                ),
            }
        ],
    )

    if response.stop_reason == "max_tokens":
        raise AIParseError(
            "AI response truncated (max_tokens) — too many categories for one pass."
        )

    tool_use = next((b for b in response.content if isinstance(b, ToolUseBlock)), None)
    if tool_use is None:
        raise AIParseError("AI did not return a tool_use block")
    raw_payload = tool_use.input
    if not isinstance(raw_payload, dict):
        raise AIParseError("AI recommendation payload is not a dict")

    payload = {
        "attributes": [
            a for a in (raw_payload.get("attributes") or []) if isinstance(a, dict)
        ],
        "products": [
            p for p in (raw_payload.get("products") or []) if isinstance(p, dict)
        ],
    }
    metadata = {
        "provider": cfg.provider,
        "model": cfg.model,
        "input_tokens": getattr(response.usage, "input_tokens", None),
        "output_tokens": getattr(response.usage, "output_tokens", None),
    }
    return payload, metadata


# ── Slip structure extraction (fallback for layouts the parser can't read) ────

_SLIP_EXTRACT_MAX_TOKENS = 4096
_SLIP_EXTRACT_TIMEOUT_SECONDS = 90.0
_SLIP_GRID_MAX_ROWS = 120
_SLIP_GRID_MAX_COLS = 16

SLIP_EXTRACT_SYSTEM_PROMPT = """You read Singapore group-insurance placement-slip \
worksheets and extract their structure.

A sheet describes ONE product. It contains:
- A "Basis of Cover" table listing eligibility CATEGORIES (grade bands / staff \
classes), each tied to a PLAN code (e.g. "1", "A", "1A"). The plan code may be \
embedded in the category text ("Plan A: Hay Job Grade 16 and above").
- A "Schedule of Benefits" listing benefit line items and their values, either as \
one shared schedule or one column per plan.

Extract:
- categories: each {category (the eligibility text, no "Plan X:" prefix), \
plan_code, insured, participation}.
  participation = "compulsory" if all eligible employees must be covered (the \
default for most group products — GTI, GHS, GLife, GPA, etc.); "voluntary" if \
employees can opt in or out. Look for words like "Compulsory", "Mandatory", \
"Voluntary", "Optional", "C", "V" in the Basis of Cover table. If not stated, \
default to "compulsory" for group products.
- plans: each {code, display_name, cover_description, items:[{number, name, value, \
note}]}. If all plans share one schedule, emit one plan whose code matches the \
categories' codes (or repeat the schedule per code).
- Financial data per category, when the sheet states it (in the Basis of Cover \
table and the "Rate :" section): num_employees (headcount), basis (verbatim \
basis-of-cover text, e.g. "36 x basic monthly salary" or a flat sum), sum_insured \
(number), premium_rate (number), annual_premium (number), rate_basis — one of \
"per_1000_si" (rate per S$1,000 sum insured), "per_member" (rate per insured \
member), "tiered" (rates per family-composition tier), "flat", "annual_flat" \
(one policy-level premium), "earnings_based" (rate x estimated annual earnings) \
— plus estimated_annual_earnings (statutory WICA-style products) and \
dependant_rate (when a separate Dependents rate row exists). For tiered rate \
tables emit rate_tiers: an object keyed by tier (EO=employee only, ES=employee \
& spouse, EC=employee & children, EF=employee & family; SO/CO/FO/SC for \
standalone spouse-only / children-only / family dependant tiers) with values \
{"rate": number, "premium": number}.

Rules:
- Use ONLY codes that actually appear; never invent plans.
- Every category's plan_code MUST match a plan code you emit.
- Keep benefit values verbatim (e.g. "S$300/day", "As charged", "O.K").
- NEVER invent numbers: omit any financial field the sheet does not state. Strip \
currency symbols and thousands separators from numeric fields.
- If the sheet has no usable structure, return empty lists.
"""

SLIP_EXTRACT_TOOL_SCHEMA: dict[str, Any] = {
    "name": "emit_slip_structure",
    "description": "Emit the categories and plans (with Schedule of Benefits) for one product sheet.",
    "input_schema": {
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "plan_code": {"type": "string"},
                        "insured": {"type": "string"},
                        "participation": {"type": "string"},
                        # Financial fields — emitted only when the sheet states
                        # them (see the system prompt's "never invent" rule).
                        "num_employees": {"type": "number"},
                        "basis": {"type": "string"},
                        "sum_insured": {"type": "number"},
                        "premium_rate": {"type": "number"},
                        "annual_premium": {"type": "number"},
                        "rate_basis": {
                            "type": "string",
                            "enum": [
                                "per_1000_si", "per_member", "tiered",
                                "flat", "annual_flat", "earnings_based",
                            ],
                        },
                        "estimated_annual_earnings": {"type": "number"},
                        "dependant_rate": {"type": "number"},
                        "rate_tiers": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "rate": {"type": "number"},
                                    "premium": {"type": "number"},
                                },
                            },
                        },
                    },
                    "required": ["category", "plan_code"],
                },
            },
            "plans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "display_name": {"type": "string"},
                        "cover_description": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "number": {"type": "string"},
                                    "name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "note": {"type": "string"},
                                },
                                "required": ["name"],
                            },
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        "required": ["categories", "plans"],
    },
}


def render_slip_grid(grid: list[list[Any]]) -> str:
    """Render a sheet grid as compact tab-separated text, bounded for token cost."""
    lines: list[str] = []
    for r, row in enumerate(grid[:_SLIP_GRID_MAX_ROWS]):
        cells = [
            "" if c is None else str(c).replace("\t", " ").replace("\n", " ").strip()
            for c in (row or [])[:_SLIP_GRID_MAX_COLS]
        ]
        if any(cells):
            lines.append(f"r{r}\t" + "\t".join(cells))
    return "\n".join(lines)


def extract_slip_structure_via_ai(
    grid: list[list[Any]],
    product_code: str,
    config: AIConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract categories + plans (with SOB) from one product sheet grid.

    Returns ``(payload, metadata)`` where payload has ``categories`` and ``plans``
    lists of raw dicts — the caller validates them into the canonical shapes.
    """
    cfg = config or load_ai_config()
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Set INSPRO_AI_PROVIDER=vertex + "
            "VERTEX_PROJECT (Google ADC for local dev), or configure a tenant "
            "BYOK key (service-account JSON) on the AI provider settings page."
        )

    client = _build_ai_client(cfg, timeout=_SLIP_EXTRACT_TIMEOUT_SECONDS)

    prompt = (
        f"Product code: {product_code}\n\n"
        f"Worksheet grid (tab-separated, row-prefixed):\n{render_slip_grid(grid)}"
    )
    response = client.messages.create(
        model=cfg.model,
        max_tokens=_SLIP_EXTRACT_MAX_TOKENS,
        system=SLIP_EXTRACT_SYSTEM_PROMPT,
        tools=[SLIP_EXTRACT_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "emit_slip_structure"},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        raise AIParseError("AI slip extraction truncated (max_tokens).")
    tool_use = next((b for b in response.content if isinstance(b, ToolUseBlock)), None)
    if tool_use is None:
        raise AIParseError("AI did not return a tool_use block")
    raw = tool_use.input
    if not isinstance(raw, dict):
        raise AIParseError("AI slip payload is not a dict")
    payload = {
        "categories": [c for c in (raw.get("categories") or []) if isinstance(c, dict)],
        "plans": [p for p in (raw.get("plans") or []) if isinstance(p, dict)],
    }
    metadata = {
        "provider": cfg.provider,
        "model": cfg.model,
        "input_tokens": getattr(response.usage, "input_tokens", None),
        "output_tokens": getattr(response.usage, "output_tokens", None),
    }
    return payload, metadata


# ── Flexible-Benefits scheme extraction (vision-capable) ──────────────────────

# A multi-tier scheme with per-category sub-limits is a large structured output;
# under-budgeting truncates the tool JSON mid-array into an unparseable payload.
_FLEX_EXTRACT_MAX_TOKENS = 8192
_FLEX_EXTRACT_TIMEOUT_SECONDS = 120.0
# Cap images sent to bound vision token cost (source docs rarely exceed this).
_FLEX_MAX_IMAGES = 8
# Canonical family-status enum — MUST match the seeded `family_status` attribute
# (scripts/seed_demo.py) so later per-employee computation joins cleanly.
FLEX_FAMILY_STATUS_CODES = ("S", "M", "M1C", "M2C", "M3C")

FLEX_EXTRACT_SYSTEM_PROMPT = """You read corporate "Flexible Benefits" (flexi-benefits) \
documents — HR handbooks, benefit summaries, emails, slides — and extract a normalized \
configuration. These documents vary widely across companies and countries; your job is to \
map whatever shape you see onto ONE structure.

A Flexible Benefits scheme is a reimbursement / spending-account benefit. Extract these \
parameter groups:

1. EMPLOYEE FAMILY STATUS — the tiers that drive the wallet size. Map the document's wording \
to these canonical codes: S=single/unmarried, M=married/spouse only, M1C=married + 1 child, \
M2C=married + 2 children, M3C=married + 3 or more children. If a document says "Married + \
children (regardless of number)" with one amount, emit a single M row (do NOT invent M1C/M2C).

2. FLEXI BENEFIT LIMIT + COUNTRY/CURRENCY — the monetary cap, which is currency- and often \
COUNTRY-specific. A single document may cover SEVERAL countries (e.g. Thailand THB, Vietnam \
VND, Indonesia IDR), each with its own currency and limit table. Emit ONE tier per (country, \
eligibility band): set tier.country (e.g. "Thailand") and tier.currency (ISO 4217) on each. \
Each tier's wallet is EITHER:\n\
  (a) a per-family-status limit table — limits[] with one row per family_status (e.g. Single \
1100, Married 1450, Married+1 child 1800); OR\n\
  (b) a FLAT annual cap not keyed to family status — set tier.system_cap to that number (e.g. \
10000) and leave limits EMPTY.\n\
CRITICAL: NEVER invent a placeholder family-status row (e.g. "Single = 0") just to fill the \
table. If the document gives one flat annual limit (like "Annual reimbursement limit up to \
SGD 10,000"), that is case (b): use tier.system_cap and an EMPTY limits[]. Keep amounts as \
numbers (1100, 10000). If the whole document is one country, set meta.currency as the default \
and you may omit per-tier currency.

3. EMPLOYEE TYPE — eligibility. Capture the verbatim eligibility text in employee_type.raw \
(e.g. "Confirmed confidential staff, Job Grade 8-17"). Parse job-grade bands into \
job_grade_min/max when present, and set confirmed_only/confidential_status when stated. ONE \
scheme may have MULTIPLE tiers (e.g. JG8-17 and JG18+, or one per country) — emit one tier per \
band, each with its own country/currency/limits/cost_sharing/benefit_categories. Country tiers \
are routed at runtime by the employee's nationality.

4. BENEFIT STATEMENTS — what's claimable. For each tier, list benefit_categories (Medical, \
Dental, Optical, Childcare, Routine Outpatient, Vision Care, etc) with claimable=true/false and \
any per-category sub_limit (a number) + note (verbatim rule, e.g. "100% up to USD 175 per \
procedure"). Capture cost_sharing (e.g. employer 80 / employee 20, with exceptions like \
"100% at government polyclinics" in exceptions[]).

5. ELIGIBILITY & PRORATION — capture in `eligibility`: entitlement_start (when cover begins — \
"date_of_hire", "policy_year_start", or "confirmation_date"), and proration (basis \
"months_served" when the limit is pro-rated by months served, "days_served" when by days, \
"none" when the full annual limit applies regardless; applies_to "leavers" when the document \
says only employees LEAVING service are pro-rated, "joiners" for entrants only, "both" when it \
covers both or does not say; leaver_recovery=true if a shortfall is stated as recovered. The \
clause is usually ONE sentence under the limit table and decides the member's real limit). \
Put tax/lapse notes in meta. \
If the document states the scheme's effective/commencement period, set meta.effective_start / \
meta.effective_end as ISO dates (YYYY-MM-DD); leave them null when not stated.

6. DEPENDANTS — capture in `dependant_def` the eligibility AND age + documentation rules: \
spouse {eligible, age_limit if any, documentation e.g. ["marriage_cert"]}; child {eligible, \
age_limit (e.g. 19), tertiary_age_limit (e.g. 25 for full-time tertiary students), conditions \
e.g. ["unmarried","non_working"], documentation e.g. ["tertiary_proof_yearly"]}; and \
verification.children_required=true if children must be verified for claims.

Rules:
- Use ONLY information present in the document. Never invent tiers, amounts, countries, or ages.
- If the document shows tables as images, read them carefully.
- confidence reflects how sure you are the extraction is correct (cap 0.85).
"""

FLEX_EXTRACT_TOOL_SCHEMA: dict[str, Any] = {
    "name": "emit_flex_scheme",
    "description": (
        "Emit the normalized Flexible-Benefits scheme: meta, eligibility tiers "
        "(each with employee-type rule, limit table, cost-sharing, benefit "
        "categories), and dependant definition."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "meta": {
                "type": "object",
                "properties": {
                    "scheme_name": {"type": "string"},
                    "currency": {
                        "type": ["string", "null"],
                        "description": (
                            "Default ISO 4217 currency (e.g. SGD). Tiers may override "
                            "per country; omit when every tier sets its own currency."
                        ),
                    },
                    "system_cap": {
                        "type": ["number", "null"],
                        "description": "Flat scheme-wide cap when there is no per-tier limit table.",
                    },
                    "tax_treatment": {"type": ["string", "null"]},
                    "lapse_proration": {"type": ["string", "null"]},
                    "effective_start": {
                        "type": ["string", "null"],
                        "description": (
                            "Scheme effective/commencement start date as ISO YYYY-MM-DD, "
                            "only when the document states one."
                        ),
                    },
                    "effective_end": {
                        "type": ["string", "null"],
                        "description": (
                            "Scheme effective end date as ISO YYYY-MM-DD, only when the "
                            "document states one."
                        ),
                    },
                },
            },
            "tiers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "country": {
                            "type": ["string", "null"],
                            "description": "Country this tier applies to (matched by employee nationality at runtime).",
                        },
                        "currency": {
                            "type": ["string", "null"],
                            "description": "ISO 4217 currency for this tier; falls back to meta.currency.",
                        },
                        "employee_type": {
                            "type": "object",
                            "properties": {
                                "raw": {
                                    "type": "string",
                                    "description": "Verbatim eligibility text.",
                                },
                                "job_grade_min": {"type": ["integer", "null"]},
                                "job_grade_max": {"type": ["integer", "null"]},
                                "confirmed_only": {"type": ["boolean", "null"]},
                                "confidential_status": {"type": ["string", "null"]},
                            },
                            "required": ["raw"],
                        },
                        "system_cap": {
                            "type": ["number", "null"],
                            "description": (
                                "Flat annual cap for this tier when the limit is NOT keyed "
                                "to family status (then leave limits empty)."
                            ),
                        },
                        "limits": {
                            "type": "array",
                            "description": (
                                "Per family-status limit rows. Leave EMPTY when a flat "
                                "tier.system_cap applies instead — never add a 0 placeholder."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "family_status": {
                                        "type": "string",
                                        "enum": list(FLEX_FAMILY_STATUS_CODES),
                                    },
                                    "amount": {"type": "number"},
                                },
                                "required": ["family_status", "amount"],
                            },
                        },
                        "cost_sharing": {
                            "type": ["object", "null"],
                            "properties": {
                                "employer_pct": {"type": "number", "minimum": 0, "maximum": 100},
                                "employee_pct": {"type": "number", "minimum": 0, "maximum": 100},
                                "exceptions": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        "benefit_categories": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "claimable": {"type": "boolean"},
                                    "sub_limit": {"type": ["number", "null"]},
                                    "note": {"type": ["string", "null"]},
                                },
                                "required": ["name", "claimable"],
                            },
                        },
                    },
                    "required": ["name", "employee_type", "limits", "benefit_categories"],
                },
            },
            "eligibility": {
                "type": ["object", "null"],
                "properties": {
                    "entitlement_start": {
                        "type": ["string", "null"],
                        "enum": ["date_of_hire", "policy_year_start", "confirmation_date", None],
                        "description": "When cover begins for an employee.",
                    },
                    "proration": {
                        "type": ["object", "null"],
                        "properties": {
                            "basis": {
                                "type": ["string", "null"],
                                "enum": ["months_served", "days_served", "none", None],
                                "description": (
                                    "How the annual limit is scaled to the period the "
                                    "member was actually covered."
                                ),
                            },
                            "applies_to": {
                                "type": ["string", "null"],
                                "enum": ["leavers", "joiners", "both", None],
                                "description": (
                                    "Which end of the year the pro-ration applies to, "
                                    "per the document's own wording."
                                ),
                            },
                            "leaver_recovery": {
                                "type": ["boolean", "null"],
                                "description": "Any shortfall recovered from leavers.",
                            },
                        },
                    },
                },
            },
            "dependant_def": {
                "type": ["object", "null"],
                "properties": {
                    "spouse": {
                        "type": ["object", "null"],
                        "properties": {
                            "eligible": {"type": "boolean"},
                            "age_limit": {"type": ["integer", "null"]},
                            "documentation": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "child": {
                        "type": ["object", "null"],
                        "properties": {
                            "eligible": {"type": "boolean"},
                            "age_limit": {
                                "type": ["integer", "null"],
                                "description": "Standard max age (e.g. 19).",
                            },
                            "tertiary_age_limit": {
                                "type": ["integer", "null"],
                                "description": "Max age for full-time tertiary students (e.g. 25).",
                            },
                            "conditions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "e.g. unmarried, non_working.",
                            },
                            "documentation": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "verification": {
                        "type": ["object", "null"],
                        "properties": {
                            "children_required": {"type": "boolean"},
                        },
                    },
                    "note": {"type": ["string", "null"]},
                },
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 0.85},
            "reasoning": {"type": "string"},
        },
        "required": ["meta", "tiers"],
    },
}


def _build_flex_prompt(text: str) -> str:
    body = text.strip()
    doc_block = (
        f"Document text:\n{body}\n\n"
        if body
        else "The document content is in the attached image(s).\n\n"
    )
    return (
        doc_block
        + "Call emit_flex_scheme with the normalized Flexible-Benefits scheme."
    )


def extract_flex_scheme_via_ai(
    text: str,
    images: list[dict[str, Any]],
    config: AIConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract a normalized Flexible-Benefits scheme from a document.

    ``text`` is any extracted text; ``images`` is a list of
    ``{"media_type": "image/png", "data": <base64 str>}`` blocks (vision) — the
    source tables are frequently images. Returns ``(payload, metadata)`` where
    ``payload`` has a ``scheme`` dict the caller persists; the caller validates it.
    """
    cfg = config or load_ai_config()
    if cfg is None:
        raise AINotConfiguredError(
            "AI provider not configured. Set INSPRO_AI_PROVIDER=vertex + "
            "VERTEX_PROJECT (Google ADC for local dev), or configure a tenant "
            "BYOK key (service-account JSON) on the AI provider settings page."
        )

    client = _build_ai_client(cfg, timeout=_FLEX_EXTRACT_TIMEOUT_SECONDS)

    content: list[dict[str, Any]] = []
    for img in images[:_FLEX_MAX_IMAGES]:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],
                    "data": img["data"],
                },
            }
        )
    content.append({"type": "text", "text": _build_flex_prompt(text)})

    response = client.messages.create(
        model=cfg.model,
        max_tokens=_FLEX_EXTRACT_MAX_TOKENS,
        system=FLEX_EXTRACT_SYSTEM_PROMPT,
        tools=[FLEX_EXTRACT_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "emit_flex_scheme"},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "max_tokens":
        raise AIParseError("AI flex extraction truncated (max_tokens).")
    tool_use = next((b for b in response.content if isinstance(b, ToolUseBlock)), None)
    if tool_use is None:
        raise AIParseError("AI did not return a tool_use block")
    raw = tool_use.input
    if not isinstance(raw, dict):
        raise AIParseError("AI flex payload is not a dict")

    # Backfill required sub-fields so a tier the model emitted without `limits`
    # or `benefit_categories` can't crash readers that assume the lists exist.
    tiers: list[dict[str, Any]] = []
    for t in raw.get("tiers") or []:
        if not isinstance(t, dict):
            continue
        tier = dict(t)
        tier["employee_type"] = (
            tier.get("employee_type") if isinstance(tier.get("employee_type"), dict) else {}
        )
        tier["limits"] = tier.get("limits") if isinstance(tier.get("limits"), list) else []
        tier["benefit_categories"] = (
            tier.get("benefit_categories")
            if isinstance(tier.get("benefit_categories"), list)
            else []
        )
        tiers.append(tier)

    scheme = {
        "meta": raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
        "tiers": tiers,
        "eligibility": (
            raw.get("eligibility") if isinstance(raw.get("eligibility"), dict) else None
        ),
        "dependant_def": (
            raw.get("dependant_def") if isinstance(raw.get("dependant_def"), dict) else None
        ),
    }
    metadata = {
        "provider": cfg.provider,
        "model": cfg.model,
        "input_tokens": getattr(response.usage, "input_tokens", None),
        "output_tokens": getattr(response.usage, "output_tokens", None),
        "confidence": raw.get("confidence"),
        "reasoning": str(raw.get("reasoning", "")),
    }
    return {"scheme": scheme}, metadata
