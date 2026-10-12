"""Write-boundary shape validation for a flex-pricing bag."""
from __future__ import annotations

from itertools import pairwise
from typing import Any

from app.services.flex_pricing.bag import (
    FAMILY_ROLES,
    FAMILY_SCHEMES,
    DependantMode,
    DependantParticipation,
    _is_age,
)


def validate_pricing_shape(pricing: dict[str, Any]) -> list[str]:
    """Write-boundary shape check for a pricing bag (empty list == valid)."""
    errs: list[str] = []
    products = pricing.get("products", {})
    if not isinstance(products, dict):
        return ["'products' must be an object keyed by product_id."]
    for pid, block in products.items():
        if not isinstance(block, dict):
            errs.append(f"product '{pid}': must be an object.")
            continue
        bands = block.get("age_bands", [])
        if not isinstance(bands, list):
            errs.append(f"product '{pid}': 'age_bands' must be a list.")
        else:
            labels: set[str] = set()
            for b in bands:
                if not isinstance(b, dict) or not str(b.get("label") or "").strip():
                    errs.append(f"product '{pid}': each age band needs a label.")
                    continue
                lo, hi = b.get("min"), b.get("max")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
                    errs.append(f"product '{pid}': age band '{b['label']}' min > max.")
                if b["label"] in labels:
                    errs.append(f"product '{pid}': duplicate age band '{b['label']}'.")
                labels.add(b["label"])
        tags = block.get("price_tags", {})
        if not isinstance(tags, dict):
            errs.append(f"product '{pid}': 'price_tags' must be an object.")
            continue
        for key, row in tags.items():
            if not isinstance(row, dict):
                errs.append(f"product '{pid}': price row '{key}' must be an object.")
                continue
            for label, amount in row.items():
                if amount is not None and (
                    not isinstance(amount, (int, float)) or amount < 0
                ):
                    errs.append(
                        f"product '{pid}': price for '{key}'/'{label}' must be ≥ 0."
                    )
        rates = block.get("voluntary_rates")
        if rates is not None:
            if not isinstance(rates, list):
                errs.append(f"product '{pid}': 'voluntary_rates' must be a list.")
            else:
                if not rates:
                    errs.append(
                        f"product '{pid}': 'voluntary_rates' needs at least one band."
                    )
                rate_labels: set[str] = set()
                valid_ranges: list[tuple[str, int | None, int | None]] = []
                for rate in rates:
                    if not isinstance(rate, dict):
                        errs.append(
                            f"product '{pid}': each voluntary rate must be an object."
                        )
                        continue
                    label = str(rate.get("label") or "").strip()
                    amount = rate.get("rate")
                    lo, hi = rate.get("min"), rate.get("max")
                    if not label:
                        errs.append(
                            f"product '{pid}': each voluntary rate needs a label."
                        )
                    elif label in rate_labels:
                        errs.append(
                            f"product '{pid}': duplicate voluntary rate '{label}'."
                        )
                    rate_labels.add(label)
                    if (
                        isinstance(amount, bool)
                        or not isinstance(amount, (int, float))
                        or amount < 0
                    ):
                        errs.append(
                            f"product '{pid}': voluntary rate '{label}' must be non-negative."
                        )
                    if lo is not None and not _is_age(lo):
                        errs.append(
                            f"product '{pid}': voluntary rate '{label}' min is invalid."
                        )
                    if hi is not None and not _is_age(hi):
                        errs.append(
                            f"product '{pid}': voluntary rate '{label}' max is invalid."
                        )
                    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
                        errs.append(
                            f"product '{pid}': voluntary rate '{label}' min > max."
                        )
                    if (
                        (lo is None or _is_age(lo))
                        and (hi is None or _is_age(hi))
                        and not (
                            isinstance(lo, int)
                            and isinstance(hi, int)
                            and lo > hi
                        )
                    ):
                        valid_ranges.append((label, lo, hi))
                valid_ranges.sort(key=lambda band: band[1] if band[1] is not None else -1)
                for previous, current in pairwise(valid_ranges):
                    previous_label, _previous_min, previous_max = previous
                    current_label, current_min, _current_max = current
                    if (
                        previous_max is None
                        or current_min is None
                        or current_min <= previous_max
                    ):
                        errs.append(
                            f"product '{pid}': voluntary rates '{previous_label}' and "
                            f"'{current_label}' overlap."
                        )
                    elif current_min > previous_max + 1:
                        errs.append(
                            f"product '{pid}': voluntary rates leave ages "
                            f"{previous_max + 1}-{current_min - 1} uncovered."
                        )
        rates_by_tier = block.get("voluntary_rates_by_tier")
        if rates_by_tier is not None and not isinstance(rates_by_tier, dict):
            errs.append(
                f"product '{pid}': 'voluntary_rates_by_tier' must be an object."
            )
        elif isinstance(rates_by_tier, dict):
            for key, tier_rates in rates_by_tier.items():
                if not isinstance(key, str) or not key:
                    errs.append(
                        f"product '{pid}': voluntary rate tier keys must be non-empty strings."
                    )
                    continue
                nested = validate_pricing_shape(
                    {"products": {pid: {"voluntary_rates": tier_rates}}}
                )
                errs.extend(f"{error} (tier '{key}')" for error in nested)
        if "dependant" in block:
            errs.extend(_validate_dependant_block(pid, block["dependant"]))
    return errs


def _validate_dependant_block(pid: str, dep: object) -> list[str]:
    """Shape check for a product's ``dependant`` config (empty list == valid)."""
    if not isinstance(dep, dict):
        return [f"product '{pid}': 'dependant' must be an object."]
    errs: list[str] = []
    mode = dep.get("mode")
    if mode is not None and mode not in (
        DependantMode.none, DependantMode.family_group, DependantMode.per_pax
    ):
        errs.append(f"product '{pid}': dependant mode '{mode}' is not valid.")
    modes = dep.get("modes")
    if modes is not None and not isinstance(modes, dict):
        errs.append(f"product '{pid}': dependant 'modes' must be an object.")
    elif isinstance(modes, dict):
        for key, tier_mode in modes.items():
            if not isinstance(key, str) or not key:
                errs.append(
                    f"product '{pid}': dependant mode tier keys must be non-empty strings."
                )
            if tier_mode not in (
                DependantMode.none,
                DependantMode.family_group,
                DependantMode.per_pax,
            ):
                errs.append(
                    f"product '{pid}': dependant mode '{tier_mode}' for '{key}' "
                    "is not valid."
                )
    participation = dep.get("participation")
    if participation is not None and not isinstance(participation, dict):
        errs.append(f"product '{pid}': dependant 'participation' must be an object.")
    elif isinstance(participation, dict):
        for key, value in participation.items():
            if not isinstance(key, str) or not key:
                errs.append(
                    f"product '{pid}': dependant participation tier keys must be "
                    "non-empty strings."
                )
            if value not in (
                DependantParticipation.none,
                DependantParticipation.compulsory,
                DependantParticipation.voluntary,
            ):
                errs.append(
                    f"product '{pid}': dependant participation '{value}' for "
                    f"'{key}' is not valid."
                )
    scheme = dep.get("scheme")
    if scheme is not None and scheme not in FAMILY_SCHEMES:
        errs.append(f"product '{pid}': dependant scheme '{scheme}' is not valid.")
    # family_tags + per_pax are keyed by tier_key (per plan), so each value is a
    # per-tier row: {role: amount} for family, {flat: amount} for per_pax.
    tags = dep.get("family_tags")
    if tags is not None and not isinstance(tags, dict):
        errs.append(f"product '{pid}': 'family_tags' must be an object.")
    elif isinstance(tags, dict):
        for key, row in tags.items():
            if not isinstance(row, dict):
                errs.append(f"product '{pid}': family_tags '{key}' must be an object.")
                continue
            for role, amount in row.items():
                if role not in FAMILY_ROLES:
                    errs.append(f"product '{pid}': family_tags role '{role}' is not valid.")
                elif amount is not None and (
                    not isinstance(amount, (int, float)) or amount < 0
                ):
                    errs.append(f"product '{pid}': family_tags '{key}/{role}' must be ≥ 0.")
    pp = dep.get("per_pax")
    if pp is not None and not isinstance(pp, dict):
        errs.append(f"product '{pid}': 'per_pax' must be an object.")
    elif isinstance(pp, dict):
        for key, row in pp.items():
            if not isinstance(row, dict):
                errs.append(f"product '{pid}': per_pax '{key}' must be an object.")
                continue
            flat = row.get("flat")
            if flat is not None and (not isinstance(flat, (int, float)) or flat < 0):
                errs.append(f"product '{pid}': per_pax '{key}' flat must be ≥ 0.")
    limits = dep.get("age_limits")
    if limits is not None and not isinstance(limits, dict):
        errs.append(f"product '{pid}': 'age_limits' must be an object.")
    elif isinstance(limits, dict):
        for role, win in limits.items():
            if role not in ("spouse", "child"):
                errs.append(f"product '{pid}': age_limits role '{role}' is not valid.")
                continue
            if not isinstance(win, dict):
                errs.append(f"product '{pid}': age_limits '{role}' must be an object.")
                continue
            lo, hi = win.get("min"), win.get("max")
            for bound, name in ((lo, "min"), (hi, "max")):
                if bound is not None and not _is_age(bound):
                    errs.append(f"product '{pid}': age_limits '{role}.{name}' must be ≥ 0.")
            if _is_age(lo) and _is_age(hi) and lo > hi:
                errs.append(f"product '{pid}': age_limits '{role}' min > max.")
    return errs
