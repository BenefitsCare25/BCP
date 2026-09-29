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
        token_map={
            "EO": "EO", "ES": "ES", "EC": "EC", "EF": "EF",
            # Spelled-out forms ("Employee Only", "Employee & Spouse").
            "EMPLOYEE ONLY": "EO", "EMPLOYEE SPOUSE": "ES",
            "EMPLOYEE CHILD": "EC", "EMPLOYEE FAMILY": "EF",
        },
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
        token_map={
            "SO": "SO", "CO": "CO", "FO": "FO", "SC": "SC",
            "SPOUSE ONLY": "SO", "CHILD ONLY": "CO", "FAMILY ONLY": "FO",
            "SPOUSE CHILD": "SC",
        },
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


# Words a slip wraps around a tier name that carry no meaning of their own:
# "Per Spouse", "No. of Child(ren)", "EO Premium", "Spouse & Child".
_TIER_FILLER = frozenset(
    {"PER", "EACH", "NO", "NOS", "NUMBER", "OF", "THE", "RATE", "PREMIUM", "AND"}
)
# Plural / bracketed-plural fragments: "Child(ren)" splits to CHILD + REN.
_TIER_PLURALS = {"CHILDREN": "CHILD", "CHILDS": "CHILD", "SPOUSES": "SPOUSE"}
_TIER_FRAGMENTS = frozenset({"REN", "S"})


def canonical_tier(label: object) -> str | None:
    """Canonical tier key for a slip's own tier label, or None.

    Slips word the same tier many ways ("Spouse", "Per Spouse", "Spouse Only",
    "No. of Child(ren)"). Matching is on the label's WORDS, with filler and
    plural fragments removed, against the registry's tier vocabulary — the
    whole phrase first, then the phrase with its filler words dropped — so a new
    wording needs no parser change, and a label naming no tier ("Employees",
    "Total") stays None rather than being guessed. Labels that qualify a tier
    ("Child (Age 1-25)") are the caller's call: see ``leading_tier``.
    """
    words = [
        _TIER_PLURALS.get(w, w)
        for w in re.findall(r"[A-Z]+", str(label or "").upper())
        if w not in _TIER_FRAGMENTS
    ]
    if not words:
        return None
    tokens = tier_token_map()
    core = [w for w in words if w not in _TIER_FILLER]
    for phrase in (" ".join(words), " ".join(core)):
        if phrase in tokens:
            return tokens[phrase]
    return None


def leading_tier(label: object) -> str | None:
    """Canonical tier key named by a label's LEADING word, or None.

    A count sub-header often qualifies its tier ("Child (Age 1-25)", "Spouse
    (legal)"): the whole phrase names no tier, but its first word does. Only
    for cells already known to be tier sub-headers — elsewhere a leading tier
    word ("Spouse of employee") is prose.
    """
    word = re.match(r"[A-Z]+", str(label or "").strip().upper())
    if word is None:
        return None
    return tier_token_map().get(_TIER_PLURALS.get(word.group(0), word.group(0)))


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


def dependant_count_from_tiers(tier_counts: object) -> int | None:
    """Exact dependant lives stated by spouse/child/dependant tier columns.

    Composite EO/ES/EC/EF tiers partition employees by household shape; they
    cannot reveal the number of dependant lives (an EC household may have one
    child or several). Only dependant-scoped tiers are therefore additive.
    """
    if not isinstance(tier_counts, dict):
        return None
    scopes = tier_scope_map()
    values: list[int] = []
    for key, value in tier_counts.items():
        if scopes.get(str(key)) != "dependant" or isinstance(value, bool):
            continue
        try:
            count = int(float(value))
        except (TypeError, ValueError):
            continue
        if count >= 0:
            values.append(count)
    return sum(values) if values else None


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
    # Member portal "What care do you need?" grouping. None = not a care route
    # (GTL is never shown to members; unknown products land under Other cover).
    care_route: str | None = None


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
        care_route="protection",
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
        care_route="protection",
        rate_models=("per_1000_si", "age_banded", "flat"),
        has_dependants=False,
    ),
    ProductEntry(
        code="GDI",
        name="Group Disability Income",
        layout_family="si_based",
        form_profile="sum_assured",
        line="life",
        care_route="protection",
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
        care_route="protection",
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
        care_route="protection",
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
        care_route="hospital",
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
        care_route="hospital",
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
        care_route="hospital",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="GMM2",
        name="Group Major Medical (Plan 2)",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        care_route="hospital",
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
        care_route="international",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="MATERNITY",
        name="Group Maternity",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        care_route="maternity",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="VISION",
        name="Group Vision Care",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        care_route="vision",
        rate_models=("tiered", "per_member"),
        tier_schemes=_MEDICAL_TIERS,
    ),
    ProductEntry(
        code="WELLNESS",
        name="Group Wellness",
        layout_family="plan_tier",
        form_profile="tiered_medical",
        line="medical",
        care_route="wellness",
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
        care_route="specialist",
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
        care_route="gp",
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
        care_route="specialist",
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
        care_route="gp",
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
        care_route="specialist",
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
        care_route="gp",
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
        care_route="dental",
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
        care_route="dental",
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
        care_route="posting",
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
        care_route="travel",
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
        care_route="work-injury",
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


# ── Product variants ─────────────────────────────────────────────────────────
# A variant is a second policy of the same product TYPE for one client — GHS
# placed separately per legal entity, or a second GHS with another insurer. It
# is its own Product row (own categories, plans, insurer, rates) whose code is
# ``<BASE>-<LABEL>`` ("GHS-VTS"). The base is always the leading code, which is
# exactly what ``get_entry`` already resolves compound codes by, so a variant
# inherits every type-level behaviour (form, line, rates, claims) from its base.
VARIANT_SEP = "-"
_VARIANT_LABEL_MAX = 24


def base_code(code: str) -> str:
    """The product-type code behind any code: ``GHS-VTS`` → ``GHS``,
    ``WICI`` → ``WICA``. An unknown code is returned upper-cased unchanged."""
    token = (code or "").strip().upper()
    entry = get_entry(token)
    return entry.code if entry is not None else token


def variant_label_of(code: str) -> str | None:
    """The variant part of a code (``GHS-VTS`` → ``VTS``), or None for a base
    code or alias."""
    token = (code or "").strip().upper()
    base = base_code(token)
    head, sep, rest = token.partition(VARIANT_SEP)
    if not sep or head != base or not rest:
        return None
    return rest


def variant_fields(code: str, metadata: dict[str, Any] | None) -> dict[str, str | None]:
    """``{base_code, variant_label}`` for API payloads — both None on a base
    product. The stored metadata keeps the broker's own label spelling; the
    code is the fallback for rows created before it was recorded."""
    meta = metadata if isinstance(metadata, dict) else {}
    label = str(meta.get("variant_label") or "").strip() or variant_label_of(code)
    if not label:
        return {"base_code": None, "variant_label": None}
    return {"base_code": str(meta.get("base_code") or base_code(code)), "variant_label": label}


def variant_code(base: str, label: str) -> str:
    """The code for ``label``'s variant of ``base``: alphanumerics only, bounded,
    so it is safe in a URL path, a sheet title and the (client, code) key."""
    slug = re.sub(r"[^A-Z0-9]+", "", (label or "").upper())[:_VARIANT_LABEL_MAX]
    return f"{base.strip().upper()}{VARIANT_SEP}{slug}" if slug else base.strip().upper()


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
    # A parenthesised qualifier names WHICH policy of the product the sheet is
    # ("GHS (VTS)", "GTL (Directors)") — never part of the code itself.
    sn = _PAREN.sub(" ", sheet_name or "").strip() or (sheet_name or "").strip()
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
    # A code followed by free words ("GHS Locals", "GTL_Directors").
    lead = re.match(r"[A-Za-z0-9]+", sn)
    if lead and lead.group(0).upper() in known_set:
        return lead.group(0).upper(), True
    # Fallback: take the last part as-is.
    code = normalized[-1] if normalized else sn.upper()
    return code.rstrip("_"), False


_PAREN = re.compile(r"\(([^)]*)\)")


def sheet_qualifier(sheet_name: str, code: str) -> str:
    """What a sheet name says beyond its product code — the variant's label.

    ``GHS (VTS)`` → ``VTS``; ``GHS - Locals`` → ``Locals``; ``GEL-GTL`` →
    ``GEL``; a bare ``GHS`` → "". Parenthesised text wins when present, since
    it is how slips name the policy a sheet belongs to.
    """
    name = (sheet_name or "").strip()
    inner = [m.strip() for m in _PAREN.findall(name) if m.strip()]
    if inner:
        return " ".join(inner)
    base = base_code(code)
    words = [
        w for w in re.split(r"[-/_\s]+", name)
        if w and w.upper() not in {code.upper(), base}
    ]
    return " ".join(words)


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
