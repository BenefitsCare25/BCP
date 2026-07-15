"""Startup drift assertions — protect against parser / seed divergence."""
from __future__ import annotations

import pytest

from app.core import drift_checks


def test_product_codes_drift_check_passes_with_current_seed() -> None:
    drift_checks.assert_known_product_codes_seeded()


def test_role_patterns_drift_check_passes_with_current_seed() -> None:
    drift_checks.assert_role_patterns_match_enum()


def test_product_drift_detected_when_parser_adds_unknown_code(monkeypatch) -> None:
    from app.services import placement_slip_parser

    bogus = frozenset({*placement_slip_parser._KNOWN_PRODUCT_CODES, "ZZZZ_BOGUS"})
    monkeypatch.setattr(placement_slip_parser, "_KNOWN_PRODUCT_CODES", bogus)

    with pytest.raises(RuntimeError, match="ZZZZ_BOGUS"):
        drift_checks.assert_known_product_codes_seeded()


def test_role_drift_detected_when_pattern_emits_unknown_role(monkeypatch) -> None:
    from app.services import rule_generator

    patterns = [*rule_generator._ROLE_PATTERNS, (r"\bphantom\b", "PHANTOM_ROLE")]
    monkeypatch.setattr(rule_generator, "_ROLE_PATTERNS", patterns)

    with pytest.raises(RuntimeError, match="PHANTOM_ROLE"):
        drift_checks.assert_role_patterns_match_enum()
