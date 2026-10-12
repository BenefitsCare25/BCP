"""The flex-pricing bag: dependant modes, family schemes, the non-persisted
GST / dependant-age stamps, the bag loader, and the age-band / tier-key lookups
every price-tag resolver shares.
"""
from __future__ import annotations

from typing import Any, TypeGuard

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FlexPricing, FlexScheme
from app.models.enrollment_window import FlexPriceSource
from app.services.roster_attributes import band_for_age


class DependantMode:
    """How a product prices coverage for a member's dependants (set per product in
    the pricing bag ``products[pid].dependant.mode``)."""

    none = "none"  # no dependant pricing (default — preserves prior behavior)
    family_group = "family_group"  # composition tier (EO/ES/EC/EF or EO/SO/CO/SC)
    per_pax = "per_pax"  # a per-dependant rate times covered count (e.g. GCGP)
    # Slip dependant OPTION rows that stick to the employee's elected plan
    # (GPA "Spouse (Option N)" ↔ "Manager (Option N)"): each covered dependant
    # draws the matching option row's slip rate — age-banded where the slip
    # prices dependants by age band (GTL/GCI voluntary rates).
    slip_options = "slip_options"


class DependantParticipation:
    """Whether a plan offers dependant cover and how dependants enrol."""

    none = "none"
    compulsory = "compulsory"
    voluntary = "voluntary"


# Canonical, scheme-agnostic family roles. Employee-Only is the absence of a role
# (no covered dependants) and always costs $0. Two label schemes map onto these,
# chosen per product; only the display labels differ.
FAMILY_SCHEMES: dict[str, dict[str, str]] = {
    "ec_es_ef": {"none": "EO", "spouse": "ES", "child": "EC", "both": "EF"},
    "so_co_sc": {"none": "EO", "spouse": "SO", "child": "CO", "both": "SC"},
}
DEFAULT_FAMILY_SCHEME = "ec_es_ef"
FAMILY_ROLES = ("spouse", "child", "both")


# A product's flex price tag is derived FROM THE PLACEMENT SLIP by default (the
# parsed premium); a broker opts a product into the portal matrix per window. So a
# product absent from a window's ``flex_price_source`` map resolves to "slip".
DEFAULT_FLEX_SOURCE = FlexPriceSource.slip


def _uses_slip(source_map: dict[str, Any] | None) -> bool:
    """Whether the slip index is needed: true when the map is empty (every product
    defaults to the slip source) or explicitly marks any product "slip". Only an
    explicit all-manual map skips the slip query."""
    if not source_map:
        return True
    return any(v == FlexPriceSource.slip for v in source_map.values())


def _is_age(v: object) -> TypeGuard[int]:
    """A valid age bound: a non-negative int that is NOT a bool (``bool`` is an
    ``int`` subclass, so a JSON ``true`` would otherwise slip through as 1)."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


# Reserved top-level key stamped onto the pricing bag by ``get_pricing`` (never
# persisted): {"default": <flex-scheme multiplier>, "products": {pid: multiplier}}.
# ``products`` holds ONLY products with an explicit ProductTerm GST opinion — an
# explicit "off" is present as 1.0 so it can override ``default`` (the flex-scheme
# fallback). Raw configured/extracted amounts are GST-EXCLUSIVE; the multiplier
# grosses up the resolved tags at the output boundaries (_tier_charge / dependant
# tags) for flex, and the premium via ``product_premium_multiplier``.
_GST_KEY = "__gst__"
# Non-persisted stamp carrying the flex-scheme-level dependant age-limit default
# (`meta.dependant_age_limits`) so every resolver reads it without new plumbing —
# the same pattern as `_GST_KEY`. Applied by `get_pricing`.
_DEP_AGE_KEY = "__dep_age__"


def gst_multiplier_for(pricing: dict[str, Any] | None, product_id: str) -> float:
    """The gross-up factor for one product's FLEX price tag — the product's own
    explicit GST opinion (ProductTerm, incl. an explicit "off" = 1.0) when set,
    else the flex-scheme default, else 1.0. Use this for flex wallet tags only;
    the insurance PREMIUM uses ``product_premium_multiplier`` (no scheme fallback)."""
    g = (pricing or {}).get(_GST_KEY)
    if not isinstance(g, dict):
        return 1.0
    products = g.get("products")
    m = (products or {}).get(product_id) if isinstance(products, dict) else None
    if m is None:  # no product-level opinion → inherit the flex-scheme default
        m = g.get("default")
    return float(m) if isinstance(m, (int, float)) and m > 0 else 1.0


def product_premium_multiplier(pricing: dict[str, Any] | None, product_id: str) -> float:
    """The gross-up factor for a product's own PREMIUM display (benefit statement,
    enrollment option financials) — the product's explicit GST opinion ONLY, never
    the flex-scheme default (that default governs flex wallet tags, not the
    insurance premium). 1.0 when the product has no explicit opinion."""
    g = (pricing or {}).get(_GST_KEY)
    if not isinstance(g, dict):
        return 1.0
    products = g.get("products")
    m = (products or {}).get(product_id) if isinstance(products, dict) else None
    return float(m) if isinstance(m, (int, float)) and m > 0 else 1.0


def _gross(amount: float | None, multiplier: float) -> float | None:
    """Apply a GST gross-up to a resolved amount (None passes through)."""
    if amount is None or multiplier == 1.0:
        return amount
    return round(amount * multiplier, 2)


def _flex_scheme_meta(db: Session, policy_year_id: str) -> dict[str, Any]:
    """The flex scheme's ``meta`` block ({} when no scheme/meta). Loaded once and
    shared by both pricing stamps so a single FlexScheme query serves the bag."""
    scheme = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    meta = (scheme.scheme or {}).get("meta") if scheme is not None else None
    return meta if isinstance(meta, dict) else {}


def _gst_stamp(
    db: Session, policy_year_id: str, meta: dict[str, Any]
) -> dict[str, Any] | None:
    """The GST block for ``_GST_KEY`` — None when nothing is configured. ``meta`` is
    the pre-loaded flex scheme meta (see ``_flex_scheme_meta``)."""
    from app.services.product_terms import gst_multiplier, product_gst_multipliers

    products = product_gst_multipliers(db, policy_year_id)
    default = gst_multiplier(bool(meta.get("gst_included")), meta.get("gst_rate"))
    if not products and default == 1.0:
        return None
    return {"default": default, "products": products}


def _dep_age_stamp(meta: dict[str, Any]) -> dict[str, dict[str, int]] | None:
    """The scheme-level dependant age-limit default (``meta.dependant_age_limits``)
    for the ``_DEP_AGE_KEY`` stamp — None when nothing valid is configured.
    Sanitized to ``{role: {min?, max?}}`` (valid ages only) so a malformed bag can
    never reach the resolver. Pure function of the pre-loaded scheme meta."""
    cfg = meta.get("dependant_age_limits")
    if not isinstance(cfg, dict):
        return None
    out: dict[str, dict[str, int]] = {}
    for role in DEFAULT_DEPENDANT_AGE_LIMITS:
        win = cfg.get(role)
        if isinstance(win, dict):
            w = {b: win[b] for b in ("min", "max") if _is_age(win.get(b))}
            if w:
                out[role] = w
    return out or None


def stamp_pricing(
    db: Session, policy_year_id: str, pricing: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Attach the non-persisted ``__gst__`` and ``__dep_age__`` stamps to a raw
    pricing bag so every resolution helper can gross up its output and resolve the
    scheme-level dependant age default without new plumbing. Loads the flex scheme
    once for both stamps. Never mutates ``pricing`` in place — a copy is made only
    when a stamp actually applies. Returns None when no pricing and no stamps."""
    meta = _flex_scheme_meta(db, policy_year_id)
    gst = _gst_stamp(db, policy_year_id, meta)
    if gst is not None:
        pricing = dict(pricing or {})
        pricing[_GST_KEY] = gst
    dep_age = _dep_age_stamp(meta)
    if dep_age is not None:
        pricing = dict(pricing or {})
        pricing[_DEP_AGE_KEY] = dep_age
    return pricing


def get_pricing(db: Session, policy_year_id: str) -> dict[str, Any] | None:
    """The policy year's flex-pricing bag (``{"products": {...}}``) or None, carrying
    the ``__gst__`` / ``__dep_age__`` stamps (see ``stamp_pricing``) — it may be a
    stamp-only bag when no pricing row exists but GST / dependant limits are set."""
    row = db.execute(
        select(FlexPricing).where(FlexPricing.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    pricing = dict(row.pricing or {}) if row else None
    return stamp_pricing(db, policy_year_id, pricing)


def _product_block(
    pricing: dict[str, Any] | None, product_id: str
) -> dict[str, Any] | None:
    products = (pricing or {}).get("products")
    if not isinstance(products, dict):
        return None
    block = products.get(product_id)
    return block if isinstance(block, dict) else None


def _open_band(age_bands: Any) -> dict[str, Any] | None:
    """The band with no bounds at all (the editor's "All ages"), which applies
    whether or not the member's age is known."""
    bands = age_bands if isinstance(age_bands, list) else []
    return next(
        (
            b for b in bands
            if isinstance(b, dict) and b.get("min") is None and b.get("max") is None
        ),
        None,
    )


def age_band_label(age_bands: list[Any], age: int | None) -> str | None:
    """Label of the band containing ``age`` (first match wins), or None. Band
    selection is shared with the life voluntary-rate bands via ``band_for_age`` so
    the price-tag band and the premium band can't diverge. With no age, only an
    all-ages band can price the member — a fixed plan price needs no DOB."""
    band = band_for_age(age_bands, age) if age is not None else _open_band(age_bands)
    if band is None:
        return None
    label = band.get("label")
    return str(label) if label is not None else None


def _plan_of_key(key: str) -> str:
    """The plan_code half of a ``cat::plan`` tier key (empty when none)."""
    return key.split("::", 1)[1] if "::" in key else ""


def _unambiguous_by_plan[ValueT](
    by_key: dict[str, ValueT], key: str
) -> ValueT | None:
    """The single value in ``by_key`` whose key shares ``key``'s plan_code, or None
    when zero or more than one match.

    The category half of a tier key can legitimately differ across baseline-
    selection paths (config grid picks the cohort's compulsory category; a member
    may be matched to a sibling), so an unambiguous plan_code is still safe to
    price. Ambiguous plan_codes (two tiers, same code, different value) return None
    rather than guess. This fallback is limited to slip-derived and dependant
    sub-config maps; broker price-tag overrides require an exact category tier.
    """
    plan = _plan_of_key(key)
    if not plan:
        return None
    matches = [v for k, v in by_key.items() if _plan_of_key(k) == plan]
    return matches[0] if len(matches) == 1 else None


def _tier_row[ValueT](
    by_key: dict[str, ValueT] | None, key: str
) -> ValueT | None:
    """A slip/sub-config value by exact tier, then unambiguous plan code.

    Broker price-tag overrides deliberately do not use this helper because they
    are category-specific unless explicitly fanned out by the editor.
    """
    if not isinstance(by_key, dict):
        return None
    if key in by_key:
        return by_key[key]
    return _unambiguous_by_plan(by_key, key)


# Default dependant eligibility age window per role, applied when a product sets
# none. A dependant outside their role's window is NOT covered (no premium, no flex).
# Bounds are in age-NEXT-birthday terms relative to the renewal date (the
# canonical convention for every extracted/configured age limit).
DEFAULT_DEPENDANT_AGE_LIMITS: dict[str, dict[str, int]] = {
    "spouse": {"min": 18, "max": 70},
    "child": {"min": 0, "max": 25},
}


def _dependant_block(
    pricing: dict[str, Any] | None, product_id: str
) -> dict[str, Any]:
    block = _product_block(pricing, product_id) or {}
    dep = block.get("dependant")
    return dep if isinstance(dep, dict) else {}
