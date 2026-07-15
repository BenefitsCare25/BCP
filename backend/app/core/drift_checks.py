"""Startup drift assertions.

Catches the two hand-maintained code/data drift risks called out in the build
state: `_KNOWN_PRODUCT_CODES` (parser) vs `PRODUCT_CATALOG` (seed), and the
`_ROLE_PATTERNS` regex targets (parser) vs the `role` attribute enum_values
(seed). If either drifts, a new product slip or executive title silently maps
to None — much harder to diagnose later than a startup crash.
"""
from __future__ import annotations


def assert_known_product_codes_seeded() -> None:
    """Parser product codes must be a subset of seeded catalog + aliases."""
    from app.services.placement_slip_parser import _KNOWN_PRODUCT_CODES
    from scripts.seed_demo import PRODUCT_CATALOG, PRODUCT_CODE_ALIASES

    seeded = {p["code"] for p in PRODUCT_CATALOG} | set(PRODUCT_CODE_ALIASES.keys())
    missing = set(_KNOWN_PRODUCT_CODES) - seeded
    if missing:
        raise RuntimeError(
            f"Drift: parser knows product codes {sorted(missing)} but seed_demo "
            f"PRODUCT_CATALOG (or PRODUCT_CODE_ALIASES) doesn't list them. Add them "
            f"to scripts/seed_demo.py or remove from placement_slip_parser."
        )


def assert_insurance_lines_seeded() -> None:
    """Every code with an explicit insurance line must be a seeded catalog code
    (or alias), so the parser can resolve it and the line tab routing holds."""
    from app.services.insurance_lines import _CODE_LINE
    from scripts.seed_demo import PRODUCT_CATALOG, PRODUCT_CODE_ALIASES

    seeded = {p["code"] for p in PRODUCT_CATALOG} | set(PRODUCT_CODE_ALIASES.keys())
    missing = set(_CODE_LINE) - seeded
    if missing:
        raise RuntimeError(
            f"Drift: insurance_lines._CODE_LINE classifies codes {sorted(missing)} "
            f"that aren't in seed_demo PRODUCT_CATALOG (or PRODUCT_CODE_ALIASES). "
            f"Add them to scripts/seed_demo.py or remove from insurance_lines."
        )


def assert_role_patterns_match_enum() -> None:
    """Each `_ROLE_PATTERNS` value must appear in the `role` attribute enum_values."""
    from app.services.rule_generator import _ROLE_PATTERNS
    from scripts.seed_demo import SINGAPORE_ATTRIBUTES

    role_attr = next((a for a in SINGAPORE_ATTRIBUTES if a["attribute_id"] == "role"), None)
    if role_attr is None:
        raise RuntimeError("Drift: `role` attribute missing from SINGAPORE_ATTRIBUTES seed.")
    seeded_roles = set(role_attr["enum_values"])
    pattern_roles = {role for _, role in _ROLE_PATTERNS}
    missing = pattern_roles - seeded_roles
    if missing:
        raise RuntimeError(
            f"Drift: rule_generator._ROLE_PATTERNS emits roles {sorted(missing)} that "
            f"aren't in the seeded `role` enum_values. Add to SINGAPORE_ATTRIBUTES or "
            f"remove from _ROLE_PATTERNS."
        )


def run_all() -> None:
    assert_known_product_codes_seeded()
    assert_insurance_lines_seeded()
    assert_role_patterns_match_enum()
