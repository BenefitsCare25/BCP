"""Product setup templates — the forward counterpart to the placement-slip parser.

The parser reverse-engineers `Product → Plan` rows out of a *completed* slip.
These templates drive the *opposite* flow: a guided form that lets a broker
build a brand-new product configuration from the insurer's standard SME scheme.

A template is a hand-authored JSON file under ``app/templates/<code>.v<n>.json``
describing the *structure* of one product sheet — its header/eligibility field
labels, the selectable plans + premium tiers, the Schedule-of-Benefits line
items (number/name/sub-items), and the additional-arrangement labels.

A template is deliberately a **structural skeleton, not a value bank**: it holds
no scheme-specific values (no rates, premiums, benefit amounts, eligibility
wordings, insurer name, or cover text). Those are per-client data that come from
the uploaded placement slip (pre-fill) or broker input, with free-text fields
backed by *dynamic* suggestions read live from this client's prior setups — so
nothing scheme-specific is frozen in code.

Templates are static config, not tenant data — they are versioned by filename so
a structural revision becomes a new ``v2`` that never disturbs companies already
built on ``v1``. The registry loads + validates every file once at import.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services import product_registry
from app.services.form_profiles import (
    DEFAULT_PROFILE,
    BasisModel,
    RateModel,
    basis_model_for,
    rate_model_for,
    sections_for,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

FieldType = Literal[
    "text", "textarea", "choice", "number", "multichoice", "taglist"
]
# Schedule-of-Benefits line kinds. The first three are simple per-plan values
# (the original GHS shape). The rest drive richer renderers:
#   boolean - Yes/No feature flag (GCGP A-G).
#   copay   - per-visit / co-payment / per-year triple (outpatient panel rows).
#   list    - enumerated covered conditions (GDD/GCI dread-disease list).
#   scale   - event -> % of capital sum (GPA scale of compensation).
# `list` and `scale` reuse the existing `sub_items` array (no new answer shape);
# `boolean`/`copay` use the per-plan value + properties already present.
BenefitKind = Literal[
    "amount", "currency", "percent", "text", "days",
    "boolean", "copay", "list", "scale", "group",
]
ParticipationModel = Literal["standard", "extended", "eo_only"]


class TemplateField(BaseModel):
    """A single fill-in field in the header or eligibility section.

    Structure only — the label, input type, and (for ``multichoice``) the
    selectable options. The value is supplied per client (slip pre-fill or
    broker input); quick-pick suggestions are served live from prior setups,
    not from a hardcoded list.

    ``multichoice`` (fixed checkbox set) and ``taglist`` (free-text chips) both
    persist their value as a comma-joined string, so they slot into the same
    ``answers.<section>`` ``Record[str, str]`` as every other field — no schema
    change. ``options`` is only meaningful for ``multichoice``.
    """

    id: str
    label: str
    type: FieldType = "text"
    options: list[str] | None = None


class TemplateSubItem(BaseModel):
    """A sub-line under a benefit item — its key + name (+ optional value kind).

    Structure first: the broker fills per-plan values or they pre-fill from the
    slip's parsed Schedule of Benefits. A hand-authored file template MAY also
    carry a suggested default ``value``/``note`` (e.g. a carrier's standard
    benefit schedule that the slip references but doesn't reproduce, like GBT);
    it pre-fills every plan and stays editable. Slip-synthesized templates never
    set these — they remain structure-only.
    """

    key: str = ""
    name: str
    kind: BenefitKind = "amount"
    value: str = ""
    note: str | None = None


class TemplateBenefitItem(BaseModel):
    """One Schedule-of-Benefits line — its number/name/kind + sub-lines.

    Structure first (see ``TemplateSubItem``): per-plan values come from the
    broker or the slip pre-fill, but a hand-authored file template MAY carry a
    suggested default ``value``/``note`` that pre-fills every plan editable.
    """

    number: str
    name: str
    kind: BenefitKind = "amount"
    value: str = ""
    note: str | None = None
    sub_items: list[TemplateSubItem] = Field(default_factory=list)


class TemplatePlan(BaseModel):
    code: str
    label: str
    default_selected: bool = True


class TemplateTier(BaseModel):
    """A premium tier column (EO/ES/EC/EF). Empty list => product is untiered."""

    code: str
    label: str


class TemplateArrangement(BaseModel):
    id: str
    label: str
    default_enabled: bool = True


class ProductTemplate(BaseModel):
    code: str
    version: int
    display_name: str
    has_dependants: bool = False
    is_outpatient: bool = False
    participation_model: ParticipationModel = "standard"
    # Which product family this form is shaped for. Drives `sections`,
    # `profile_fields`, `basis_model` and `rate_model` below; defaults to
    # tiered_medical so existing hand-authored templates (GHS) keep their full
    # medical layout.
    form_profile: str = DEFAULT_PROFILE
    # Basis-of-Cover column shape + Rate-table shape. Filled from `form_profile`
    # when left None so a template file need not restate them, but a template can
    # override (e.g. a medical product priced per-member).
    basis_model: BasisModel | None = None
    rate_model: RateModel | None = None
    # Optional second column axis for the Schedule of Benefits (e.g. dental
    # "Panel" / "Non-Panel"). When set, the SOB renders one value column per axis
    # label instead of one per plan; values persist in each item's `properties`
    # keyed by the axis label.
    column_axis: list[str] = Field(default_factory=list)
    # Ordered section ids the frontend renders. Filled from `form_profile` when
    # left empty so a template file need not enumerate them.
    sections: list[str] = Field(default_factory=list)
    header_fields: list[TemplateField] = Field(default_factory=list)
    eligibility_fields: list[TemplateField] = Field(default_factory=list)
    plans: list[TemplatePlan] = Field(default_factory=list)
    tiers: list[TemplateTier] = Field(default_factory=list)
    benefit_items: list[TemplateBenefitItem] = Field(default_factory=list)
    additional_arrangements: list[TemplateArrangement] = Field(default_factory=list)
    # Server-owned claim-type vocabulary for explicit SoB limit mapping. Filled
    # by the tenant-scoped template endpoint; static template files omit it.
    claim_scopes: list[dict[str, str | None]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fill_profile_defaults(self) -> ProductTemplate:
        if not self.sections:
            self.sections = sections_for(self.form_profile)
        if self.basis_model is None:
            self.basis_model = basis_model_for(self.form_profile)
        if self.rate_model is None:
            self.rate_model = rate_model_for(self.form_profile)
        # Guarantee Office Address + Type of Administration on every header.
        ensure_standard_header_fields(self.header_fields)
        # Guarantee the standard eligibility fields on every product (hand-authored,
        # synthesized, or starter). Canonical set leads in a fixed order; any
        # template-declared extras trail it. (admin_basis is excluded — it lives in
        # the header now — so it never reappears here as a duplicate.)
        canonical = standard_eligibility_fields(self.has_dependants)
        canonical_ids = {f.id for f in canonical} | {"admin_basis"}
        extras = [f for f in self.eligibility_fields if f.id not in canonical_ids]
        self.eligibility_fields = canonical + extras
        return self


def ensure_standard_header_fields(fields: list[TemplateField]) -> list[TemplateField]:
    """Guarantee Office Address + Type of Administration on every product header.

    Address is placed just after Insured/Policyholder (its Excel position);
    Type of Administration trails the block. A pre-existing generic ``address``
    field is normalised to the "Office Address" label/textarea rather than
    duplicated. Mutates and returns the list.
    """
    # Coverage dates now live in ProductTerm and are filled directly from the
    # placement slip. Remove the legacy duplicate text field from every
    # hand-authored and synthesized template.
    fields[:] = [
        field for field in fields if field.id not in {"period_of_insurance", "period"}
    ]
    # Normalise an existing address field.
    for f in fields:
        if f.id == "address":
            f.label = "Office Address"
            f.type = "textarea"
    ids = {f.id for f in fields}
    if "address" not in ids:
        by_id = {f.id: i for i, f in enumerate(fields)}
        anchor = by_id.get("insured", by_id.get("policyholder", len(fields) - 1))
        fields.insert(
            anchor + 1,
            TemplateField(id="address", label="Office Address", type="textarea"),
        )
    if "admin_basis" not in ids:
        fields.append(
            TemplateField(id="admin_basis", label="Type of Administration", type="text")
        )
    return fields


def standard_eligibility_fields(has_dependants: bool) -> list[TemplateField]:
    """The eligibility fields every product's setup form must carry.

    Centralised here so the same set appears whether the template is hand-authored
    (``ghs.v1.json``), synthesized from a slip, or a blank starter — the
    ``ProductTemplate`` validator merges this list into every template.
    Dependant options are always present; the UI decides whether spouse/child
    age-limit fields are visible from the checked member-cover values.
    """
    _ = has_dependants
    member_options = ["Employee", "Spouse", "Child"]

    fields = [
        TemplateField(id="eligibility", label="Eligibility", type="textarea"),
        TemplateField(id="eligibility_date", label="Eligibility Date", type="text"),
        TemplateField(
            id="member_cover_eligibility",
            label="Member Cover Eligibility",
            type="multichoice",
            options=member_options,
        ),
        TemplateField(
            id="age_limit_no_underwriting",
            label="Age Limit for No Underwriting",
            type="text",
        ),
        TemplateField(id="last_entry_age", label="Last Entry Age", type="text"),
        TemplateField(
            id="employees_above_last_entry_age",
            label="Employees Above Last Entry Age",
            type="taglist",
        ),
        TemplateField(id="employee_age_limit", label="Employee Age Limit", type="text"),
    ]
    fields += [
        TemplateField(id="child_age_limit", label="Child Age Limit", type="text"),
        TemplateField(id="spouse_age_limit", label="Spouse Age Limit", type="text"),
    ]
    return fields


class ProductTemplateSummary(BaseModel):
    """Lightweight catalog entry for the product picker."""

    code: str
    version: int
    display_name: str
    has_dependants: bool
    is_outpatient: bool


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, ProductTemplate]:
    """Load + validate every template file, keeping the highest version per code."""
    registry: dict[str, ProductTemplate] = {}
    if not TEMPLATES_DIR.is_dir():
        logger.warning("Product templates dir missing: %s", TEMPLATES_DIR)
        return registry
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            tpl = ProductTemplate.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load product template %s", path.name)
            continue
        code = tpl.code.upper()
        existing = registry.get(code)
        if existing is None or tpl.version > existing.version:
            registry[code] = tpl
    return registry


def list_templates() -> list[ProductTemplateSummary]:
    return [
        ProductTemplateSummary(
            code=t.code,
            version=t.version,
            display_name=t.display_name,
            has_dependants=t.has_dependants,
            is_outpatient=t.is_outpatient,
        )
        for t in sorted(_load_registry().values(), key=lambda t: t.code)
    ]


# Codes that reuse another code's curated template (same product, alternate
# code, or an under-extracted variant that shares its sibling's structure). The
# resolved template keeps the *requested* code so confirm materializes the right
# Product. Avoids cloning near-identical JSON files (and the drift that invites).
_TEMPLATE_ALIASES: dict[str, str] = product_registry.template_alias_map()


def get_template(code: str) -> ProductTemplate | None:
    registry = _load_registry()
    key = code.upper()
    tpl = registry.get(key)
    if tpl is not None:
        return tpl
    alias = _TEMPLATE_ALIASES.get(key)
    if alias is not None:
        sibling = registry.get(alias)
        if sibling is not None:
            # Reuse the sibling's structure but keep the requested code so the
            # materialized Product/Plan rows carry the real product code.
            return sibling.model_copy(update={"code": key})
    return None
