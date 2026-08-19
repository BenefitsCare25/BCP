"""Single source of truth for product-type knowledge.

Consolidates what used to live in five hand-synced maps:

- ``placement_slip_parser._KNOWN_PRODUCT_CODES`` + its GHS sub-product aliases
- ``form_profiles._CODE_PROFILE``
- ``insurance_lines._CODE_LINE``
- ``product_templates._TEMPLATE_ALIASES``
- ``_PRODUCT_CODE_ALIASES`` (duplicated in ``placement_slips.py`` and
  ``recommendations.py``)

Those modules now *derive* their maps from here, so adding a product is one
``ProductEntry`` instead of five edits. This module is pure and DB-free (the
parser must classify sheets before a product is ever seeded); per-tenant
overrides ride ``Product.product_metadata`` and win via ``resolve_entry``.

Field types ``form_profile`` / ``line`` are plain strings here to keep the
dependency one-directional (``form_profiles`` and ``insurance_lines`` import
*us*); their Literal types still validate at the consuming edge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

LayoutFamily = Literal["si_based", "plan_tier", "travel", "named_person", "earnings"]

# ── Tier vocabulary ──────────────────────────────────────────────────────────
# Slips label family-composition tiers differently per client. Composite
# schemes (employee + dependants covered together) canonicalize onto the
# EO/ES/EC/EF keys already persisted in `plan_assignments.rate_tiers`.
# Dependant-only schemes (VDL "GHS - Dependants" SO/CO/FO/SC, Hartree
# Spouse/Child columns) price dependant cover *standalone* and must never be
# folded onto ES/EC/EF — they feed dependant pricing instead.


@dataclass(frozen=True)
class TierScheme:
    scheme_id: str
    member_scope: str  # "composite" | "dependant"
    token_map: dict[str, str]  # source header token → canonical tier key
    labels: dict[str, str]  # canonical tier key → display label


TIER_SCHEMES: dict[str, TierScheme] = {
    "eo_es_ec_ef": TierScheme(
        scheme_id="eo_es_ec_ef",
        member_scope="composite",
        token_map={"EO": "EO", "ES": "ES", "EC": "EC", "EF": "EF"},
        labels={
            "EO": "Employee Only",
            "ES": "Employee & Spouse",
            "EC": "Employee & Child(ren)",
            "EF": "Employee & Family",
        },
    ),
    "dependant_only": TierScheme(
        scheme_id="dependant_only",
        member_scope="dependant",
        token_map={"SO": "SO", "CO": "CO", "FO": "FO", "SC": "SC"},
        labels={
            "SO": "Spouse Only",
            "CO": "Child(ren) Only",
            "FO": "Family Only",
            "SC": "Spouse & Child(ren)",
        },
    ),
    "eo_spouse_child": TierScheme(
        scheme_id="eo_spouse_child",
        member_scope="dependant",
        token_map={"SPOUSE": "SO", "CHILD": "CO"},
        labels={"SO": "Spouse", "CO": "Child"},
    ),
}


def tier_scheme(scheme_id: str) -> TierScheme:
    return TIER_SCHEMES[scheme_id]


def tier_token_map() -> dict[str, str]:
    """Every header token any product may print above a member count → its
    canonical tier key (``"SPOUSE"`` → ``SO``). Upper-cased for lookup."""
    return {
        token.upper(): key
        for scheme in TIER_SCHEMES.values()
        for token, key in scheme.token_map.items()
    }


def tier_scope_map() -> dict[str, str]:
    """Canonical tier key → ``"composite"`` | ``"dependant"``.

    The distinction is load-bearing wherever counts are read: composite tiers
    (EO/ES/EC/EF) PARTITION the employees, so they sum to the headcount, while
    dependant tiers (SO/CO/SC/FO) count dependants alongside it. Adding them
    together inflates the headcount by the whole dependant population.
    """
    return {
        key: scheme.member_scope
        for scheme in TIER_SCHEMES.values()
        for key in scheme.token_map.values()
    }


def tier_order() -> list[str]:
    """Canonical tier keys in display order: composite tiers, then dependant."""
    seen: list[str] = []
    for scheme in sorted(
        TIER_SCHEMES.values(), key=lambda s: s.member_scope != "composite"
    ):
        for key in scheme.token_map.values():
            if key not in seen:
                seen.append(key)
    return seen


# ── Product entries ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductEntry:
    code: str
    name: str
    layout_family: LayoutFamily
    form_profile: str  # FormProfile literal value
    line: str  # InsuranceLine literal value
    rate_models: tuple[str, ...]  # rate_basis values this product may persist
    aliases: tuple[str, ...] = ()  # alternate codes meaning the same product
    sheet_tokens: dict[str, str] | None = None  # sheet-name token → compound code
    tier_schemes: tuple[str, ...] = ()
    has_dependants: bool = True
    supports_voluntary_age_bands: bool = False
    template_alias: str | None = None  # reuse this code's curated file template
    flags: frozenset[str] = frozenset()


_MEDICAL_TIERS = ("eo_es_ec_ef", "dependant_only", "eo_spouse_child")

_ENTRIES: tuple[ProductEntry, ...] = (
    # ── Life / sum-assured (SI-based slips) ─────────────────────────────────
    ProductEntry(
        code="GTL",
        name="Group Term Life",
        layout_family="si_based",
        form_profile="sum_assured",
        line="life",
        rate_models=("per_1000_si", "age_banded", "flat"),
        has_dependants=False,
        supports_voluntary_age_bands=True,
    ),
    ProductEntry(
        code="GCI",
        name="Group Critical Illness",
        layout_family="si_based",
        form_profile="sum_assured",
        line="life",
        rate_models=("per_1000_si", "age_banded", "flat"),
        has_dependants=False,
        supports_voluntary_age_bands=True,
    ),
    ProductEntry(
        code="GDD",
        name="Group Dread Disease",
        layout_family="si_based",
        form_profile="sum_assured",
        line="life",
        rate_models=("per_1000_si", "age_banded", "flat"),
        has_dependants=False,
    ),
    ProductEntry(
        code="GDI",
        name="Group Disability Income",
        layout_family="si_based",
        form_profile="sum_assured",
        line="life",
        rate_models=("per_1000_si", "age_banded", "flat"),
        has_dependants=False,
    ),
    # ── Accident (SI-based slips; Life tab) ─────────────────────────────────
    ProductEntry(
        code="GPA",
        name="Group Personal Accident",
        layout_family="si_based",
        form_profile="accident",
        line="life",
        rate_models=("per_1000_si", "flat", "age_banded"),
        # GPA slips carry "Spouse (Option N)" / "Child (Option N)" categories.
        flags=frozenset({"dependant_option_categories", "blended_product_rate"}),
    ),
    ProductEntry(
        code="GTPD",
        name="Group Total & Permanent Disability",
        layout_family="si_based",
        form_profile="accident",
        line="life",
        rate_models=("per_1000_si", "flat", "age_banded"),
        has_dependants=False,
    ),
    # ── Tiered medical ───────────────────────────────────────────────────────
    ProductEntry(
        code="GHS",
        name="Group Hospital & Surgical",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        sheet_tokens={
            # VDL splits GHS into per-population sheets.
            "LOCALS": "GHS-LOCALS",
            "SECONDEES": "GHS-SECONDEES",
            "DEPENDANTS": "GHS-DEPENDANTS",
        },
        tier_schemes=_MEDICAL_TIERS,
        flags=frozenset({"downgrade_upgrade_rows"}),
    ),
    ProductEntry(
        code="GHS2",
        name="Group Hospital & Surgical (Plan 2)",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
        template_alias="GHS",
        flags=frozenset({"downgrade_upgrade_rows"}),
    ),
    ProductEntry(
        code="GMM",
        name="Group Major Medical",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="GMM2",
        name="Group Major Medical (Plan 2)",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
        template_alias="GMM",
    ),
    ProductEntry(
        code="IMP",
        name="International Medical Plan",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="MATERNITY",
        name="Group Maternity",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="VISION",
        name="Group Vision Care",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="WELLNESS",
        name="Group Wellness",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    # ── Outpatient clinics (per-member rates, plan-scope rate rows) ─────────
    ProductEntry(
        code="SP",
        name="Group Outpatient Specialist",
        layout_family="plan_tier",
        form_profile="outpatient",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        flags=frozenset({"plan_scope_rows"}),
    ),
    ProductEntry(
        code="GCGP",
        name="Group Clinical General Practitioner",
        layout_family="plan_tier",
        form_profile="outpatient",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        flags=frozenset({"plan_scope_rows"}),
    ),
    ProductEntry(
        code="GCSP",
        name="Group Clinical Specialist",
        layout_family="plan_tier",
        form_profile="outpatient",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        flags=frozenset({"plan_scope_rows"}),
    ),
    ProductEntry(
        code="GOGP",
        name="Group Outpatient GP",
        layout_family="plan_tier",
        form_profile="outpatient",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        template_alias="GCGP",
        flags=frozenset({"plan_scope_rows"}),
    ),
    ProductEntry(
        code="GOSP",
        name="Group Outpatient Specialist (variant)",
        layout_family="plan_tier",
        form_profile="outpatient",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        template_alias="GCSP",
        flags=frozenset({"plan_scope_rows"}),
    ),
    ProductEntry(
        code="GP",
        name="Group Clinical GP",
        layout_family="plan_tier",
        form_profile="outpatient",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        flags=frozenset({"plan_scope_rows"}),
    ),
    # ── Dental ───────────────────────────────────────────────────────────────
    ProductEntry(
        code="GD",
        name="Group Dental",
        layout_family="plan_tier",
        form_profile="dental",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        flags=frozenset({"plan_scope_rows"}),
    ),
    ProductEntry(
        code="DENTAL",
        name="Group Dental (alternate code)",
        layout_family="plan_tier",
        form_profile="dental",
        line="medical",
        rate_models=("per_member", "tiered", "flat"),
        tier_schemes=_MEDICAL_TIERS,
        template_alias="GD",
        flags=frozenset({"plan_scope_rows"}),
    ),
    # ── Secondment (named persons, tiered rates) ────────────────────────────
    ProductEntry(
        code="OSI",
        name="Group Secondment Insurance",
        layout_family="named_person",
        form_profile="tiered_medical",
        line="general",
        rate_models=("tiered", "per_member"),
        tier_schemes=("eo_es_ec_ef",),
        has_dependants=False,
    ),
    # ── Travel ───────────────────────────────────────────────────────────────
    ProductEntry(
        code="GBT",
        name="Group Business Travel",
        layout_family="travel",
        form_profile="travel",
        line="general",
        rate_models=("annual_flat", "flat"),
        has_dependants=False,
        flags=frozenset({"text_premium"}),
    ),
    # ── Statutory ────────────────────────────────────────────────────────────
    ProductEntry(
        code="WICA",
        name="Work Injury Compensation",
        layout_family="earnings",
        form_profile="statutory",
        line="general",
        rate_models=("earnings_based",),
        aliases=("WICI",),  # the insurance product vs the Act it implements
        has_dependants=False,
        flags=frozenset({"per_entity_blocks"}),
    ),
)

REGISTRY: dict[str, ProductEntry] = {e.code: e for e in _ENTRIES}

# alias code → canonical entry (aliases inherit the entry's classification)
_ALIAS_TO_ENTRY: dict[str, ProductEntry] = {
    alias: e for e in _ENTRIES for alias in e.aliases
}

# sheet-name token → compound sub-product code (e.g. LOCALS → GHS-LOCALS)
_SHEET_TOKEN_ALIASES: dict[str, str] = {
    token: compound
    for e in _ENTRIES
    if e.sheet_tokens
    for token, compound in e.sheet_tokens.items()
}


# ── Lookup API ───────────────────────────────────────────────────────────────


def entries() -> tuple[ProductEntry, ...]:
    return _ENTRIES


def known_codes() -> frozenset[str]:
    """Every code token the slip parser should treat as a product code
    (canonical codes plus aliases such as WICI)."""
    return frozenset(REGISTRY) | frozenset(_ALIAS_TO_ENTRY)


def get_entry(code: str) -> ProductEntry | None:
    """Resolve a code (canonical, alias, or compound like ``GHS-LOCALS``) to
    its registry entry."""
    token = (code or "").strip().upper()
    if not token:
        return None
    entry = REGISTRY.get(token) or _ALIAS_TO_ENTRY.get(token)
    if entry is not None:
        return entry
    head = re.split(r"[-/]", token, maxsplit=1)[0].strip()
    return REGISTRY.get(head) or _ALIAS_TO_ENTRY.get(head)


def is_known(code: str) -> bool:
    return get_entry(code) is not None


def _norm_match(code: str) -> str:
    return (code or "").upper().replace(" ", "").replace("-", "")


_ALIAS_MATCH: dict[str, str] = {
    _norm_match(alias): _norm_match(e.code) for e in _ENTRIES for alias in e.aliases
}


def resolve_code(code: str) -> str:
    """Normalize a code for product matching (upper, no spaces/dashes) and map
    aliases to their canonical code (``WICI`` → ``WICA``). Unknown tokens pass
    through normalized."""
    n = _norm_match(code)
    return _ALIAS_MATCH.get(n, n)


def derive_product_code(
    sheet_name: str, known: frozenset[str] | None = None
) -> tuple[str, bool]:
    """Map a sheet name to a product code, returning ``(code, known)``.

    Handles the shapes seen in real slips:
    - ``<Insurer>-<Code>``   e.g. STM's ``GEL-GTL`` → GTL (suffix = code)
    - ``<Code> - <Variant>`` e.g. ``GCI - Additional`` → GCI (prefix = code)
    - sub-product tokens     e.g. ``GHS - Locals`` → GHS-LOCALS
    - bare/unknown names     → last part passthrough, ``known=False``
    """
    known_set = known if known is not None else known_codes()
    sn = (sheet_name or "").strip()
    parts = [p.strip() for p in re.split(r"[-/]", sn) if p.strip()]
    normalized = [re.sub(r"\s+", "_", p).upper() for p in parts]

    for piece in normalized:
        if piece in _SHEET_TOKEN_ALIASES:
            return _SHEET_TOKEN_ALIASES[piece], True

    # Prefer the first part if it matches a known code (handles `GCI - Additional`).
    if normalized and normalized[0] in known_set:
        return normalized[0], True
    # Otherwise prefer the last part (STM's insurer-prefix pattern).
    if normalized and normalized[-1] in known_set:
        return normalized[-1], True
    # Fallback: take the last part as-is.
    code = normalized[-1] if normalized else sn.upper()
    return code.rstrip("_"), False


def resolve_entry(code: str, product_metadata: dict[str, Any] | None = None) -> ProductEntry:
    """Registry entry for a code with per-tenant ``product_metadata`` overrides
    applied. Unknown codes get a generic plan_tier/tiered_medical entry (the
    historical default) — callers should surface ``is_known`` separately so
    unknowns are flagged for classification rather than silently trusted."""
    token = (code or "").strip().upper()
    entry = get_entry(token) or ProductEntry(
        code=token,
        name=token,
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    )
    meta = product_metadata or {}
    updates: dict[str, Any] = {}
    if meta.get("form_profile"):
        updates["form_profile"] = str(meta["form_profile"])
    if meta.get("line"):
        updates["line"] = str(meta["line"])
    if meta.get("layout_family") in (
        "si_based",
        "plan_tier",
        "travel",
        "named_person",
        "earnings",
    ):
        updates["layout_family"] = meta["layout_family"]
    if isinstance(meta.get("has_dependants"), bool):
        updates["has_dependants"] = meta["has_dependants"]
    return replace(entry, **updates) if updates else entry


# ── Derived maps for the legacy consumers ────────────────────────────────────


def code_profile_map() -> dict[str, str]:
    """code → form_profile, aliases included (feeds ``form_profiles._CODE_PROFILE``)."""
    out = {e.code: e.form_profile for e in _ENTRIES}
    out.update({alias: e.form_profile for e in _ENTRIES for alias in e.aliases})
    return out


def code_line_map() -> dict[str, str]:
    """code → insurance line, aliases included (feeds ``insurance_lines._CODE_LINE``)."""
    out = {e.code: e.line for e in _ENTRIES}
    out.update({alias: e.line for e in _ENTRIES for alias in e.aliases})
    return out


def template_alias_map() -> dict[str, str]:
    """code → sibling code whose curated file template it reuses."""
    return {e.code: e.template_alias for e in _ENTRIES if e.template_alias}
