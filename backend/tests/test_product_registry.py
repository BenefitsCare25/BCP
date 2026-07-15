"""Parity + behavior tests for the product registry.

The registry replaced five hand-synced maps. The literal snapshots below are
copies of those maps as they stood before the consolidation — if a registry
edit changes any derived map unintentionally, these tests catch it. Extending
the registry with a NEW product updates the snapshots too (deliberate edit).
"""
from __future__ import annotations

import pytest

from app.services import product_registry
from app.services.form_profiles import _CODE_PROFILE, infer_profile
from app.services.insurance_lines import _CODE_LINE, infer_line
from app.services.placement_slip_parser import (
    _KNOWN_PRODUCT_CODES,
    _derive_product_code,
)
from app.services.product_templates import _TEMPLATE_ALIASES

# ── Pre-consolidation snapshots ──────────────────────────────────────────────

_SNAPSHOT_KNOWN_CODES = frozenset(
    {
        "GTL", "GHS", "GMM", "SP", "GPA", "GBT", "WICA", "WICI", "GCGP",
        "GCSP", "GCI", "GD", "GDD", "GP", "OSI", "DENTAL", "GHS2", "GMM2",
        "GOSP", "GOGP", "IMP", "MATERNITY", "VISION", "WELLNESS", "GDI",
        "GTPD",
    }
)

_SNAPSHOT_CODE_PROFILE = {
    "GHS": "tiered_medical", "GHS2": "tiered_medical", "GMM": "tiered_medical",
    "GMM2": "tiered_medical", "IMP": "tiered_medical",
    "MATERNITY": "tiered_medical", "VISION": "tiered_medical",
    "WELLNESS": "tiered_medical", "SP": "outpatient", "GCGP": "outpatient",
    "GCSP": "outpatient", "GOSP": "outpatient", "GOGP": "outpatient",
    "GP": "outpatient", "OSI": "tiered_medical", "GD": "dental",
    "DENTAL": "dental", "GTL": "sum_assured", "GDD": "sum_assured",
    "GDI": "sum_assured", "GCI": "sum_assured", "GPA": "accident",
    "GTPD": "accident", "GBT": "travel", "WICA": "statutory",
    "WICI": "statutory",
}

_SNAPSHOT_CODE_LINE = {
    "GHS": "medical", "GHS2": "medical", "GMM": "medical", "GMM2": "medical",
    "SP": "medical", "GCGP": "medical", "GCSP": "medical", "OSI": "medical",
    "GD": "medical", "DENTAL": "medical", "GOSP": "medical", "GOGP": "medical",
    "GP": "medical", "IMP": "medical", "MATERNITY": "medical",
    "VISION": "medical", "WELLNESS": "medical", "GBT": "medical",
    "WICA": "medical", "WICI": "medical", "GTL": "life", "GDD": "life",
    "GCI": "life", "GDI": "life", "GTPD": "life", "GPA": "life",
}

_SNAPSHOT_TEMPLATE_ALIASES = {
    "DENTAL": "GD",
    "GOGP": "GCGP",
    "GOSP": "GCSP",
    "GHS2": "GHS",
    "GMM2": "GMM",
}


def test_known_codes_parity():
    assert product_registry.known_codes() == _SNAPSHOT_KNOWN_CODES
    assert _KNOWN_PRODUCT_CODES == _SNAPSHOT_KNOWN_CODES


def test_code_profile_parity():
    assert product_registry.code_profile_map() == _SNAPSHOT_CODE_PROFILE
    assert _CODE_PROFILE == _SNAPSHOT_CODE_PROFILE


def test_code_line_parity():
    assert product_registry.code_line_map() == _SNAPSHOT_CODE_LINE
    assert _CODE_LINE == _SNAPSHOT_CODE_LINE


def test_template_alias_parity():
    assert product_registry.template_alias_map() == _SNAPSHOT_TEMPLATE_ALIASES
    assert _TEMPLATE_ALIASES == _SNAPSHOT_TEMPLATE_ALIASES


def test_every_template_alias_targets_a_registry_entry():
    for alias, target in product_registry.template_alias_map().items():
        assert product_registry.get_entry(target) is not None, (alias, target)


def test_infer_functions_still_resolve():
    assert infer_profile("GTL") == "sum_assured"
    assert infer_profile("wica") == "statutory"
    assert infer_profile("UNKNOWN") == "tiered_medical"
    assert infer_line("GPA") == "life"
    assert infer_line("gbt") == "medical"
    assert infer_line("UNKNOWN") == "medical"


# ── Sheet-name derivation over every variant seen in the reference slips ─────


@pytest.mark.parametrize(
    ("sheet_name", "expected", "known"),
    [
        # CDL
        ("GTL", "GTL", True),
        ("GCI - Additional", "GCI", True),
        ("GHS", "GHS", True),
        ("GMM", "GMM", True),
        ("GCGP", "GCGP", True),
        ("GCSP", "GCSP", True),
        ("GD", "GD", True),
        ("GPA", "GPA", True),
        ("GBT", "GBT", True),
        ("OSI", "OSI", True),
        ("WICI", "WICI", True),
        # CBRE Group
        ("GDD ", "GDD", True),
        ("Dental", "DENTAL", True),
        # STM — insurer prefixes (incl. the leading-space Chubb sheet)
        ("GEL-GTL", "GTL", True),
        ("GEL-GHS", "GHS", True),
        ("GEL-SP", "SP", True),
        ("Zurich-GPA", "GPA", True),
        (" Chubb -GBT", "GBT", True),
        ("Allianz-WICI", "WICI", True),
        # VDL — GHS split sheets + trailing-space GTL
        ("GTL ", "GTL", True),
        ("GHS - Locals", "GHS-LOCALS", True),
        ("GHS - Secondees", "GHS-SECONDEES", True),
        ("GHS - Dependants", "GHS-DEPENDANTS", True),
        ("WICA", "WICA", True),
        # Hartree
        ("GP", "GP", True),
        # Unknowns pass through (spaces → underscores), flagged unknown
        ("Renewal Overall Premium", "RENEWAL_OVERALL_PREMIUM", False),
        ("GXYZ", "GXYZ", False),
    ],
)
def test_derive_product_code(sheet_name: str, expected: str, known: bool):
    code, is_known = product_registry.derive_product_code(sheet_name)
    assert code == expected
    assert is_known is known
    # the parser's shim keeps the historic str-returning signature
    assert _derive_product_code(sheet_name) == expected


def test_resolve_code_aliases():
    assert product_registry.resolve_code("WICI") == "WICA"
    assert product_registry.resolve_code("wici") == "WICA"
    assert product_registry.resolve_code("GHS-LOCALS") == "GHSLOCALS"
    assert product_registry.resolve_code("GTL") == "GTL"


def test_get_entry_resolves_alias_and_compound_codes():
    assert product_registry.get_entry("WICI") is product_registry.get_entry("WICA")
    assert product_registry.get_entry("GHS-LOCALS") is product_registry.get_entry("GHS")
    assert product_registry.get_entry("nope") is None
    assert product_registry.get_entry("") is None


def test_resolve_entry_applies_metadata_overrides():
    base = product_registry.resolve_entry("GHS")
    assert base.form_profile == "tiered_medical"
    over = product_registry.resolve_entry(
        "GHS", {"form_profile": "outpatient", "line": "flex", "has_dependants": False}
    )
    assert over.form_profile == "outpatient"
    assert over.line == "flex"
    assert over.has_dependants is False
    # unknown codes get the generic default entry, overridable the same way
    unk = product_registry.resolve_entry("GXYZ", {"layout_family": "si_based"})
    assert unk.code == "GXYZ"
    assert unk.layout_family == "si_based"
    assert not product_registry.is_known("GXYZ")


def test_tier_schemes_dependant_only_never_maps_to_composite_keys():
    composite = product_registry.tier_scheme("eo_es_ec_ef")
    assert set(composite.token_map.values()) == {"EO", "ES", "EC", "EF"}
    for scheme_id in ("dependant_only", "eo_spouse_child"):
        scheme = product_registry.tier_scheme(scheme_id)
        assert scheme.member_scope == "dependant"
        # dependant-only tiers must never canonicalize onto employee-composite keys
        assert not (set(scheme.token_map.values()) & {"ES", "EC", "EF"})
