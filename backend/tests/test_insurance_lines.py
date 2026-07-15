"""Insurance-line inference — the Medical / Life / Flex tab dimension.

The line is an *independent* axis from the form profile: GPA is configured on
the Life tab while keeping its accident-shaped form.
"""
from __future__ import annotations

from app.services.form_profiles import infer_profile
from app.services.insurance_lines import _CODE_LINE, infer_line


def test_infer_line_by_code() -> None:
    assert infer_line("GHS") == "medical"
    assert infer_line("GMM2") == "medical"
    assert infer_line("DENTAL") == "medical"
    assert infer_line("GTL") == "life"
    assert infer_line("GCI") == "life"
    assert infer_line("GTPD") == "life"
    # Unknown code falls back to the default line (medical).
    assert infer_line("ZZZ") == "medical"


def test_infer_line_override() -> None:
    # A valid override wins over inference (custom product on a chosen tab).
    assert infer_line("GHS", override="flex") == "flex"
    assert infer_line("GTL", override="medical") == "medical"
    # Garbage override is ignored, falling back to inference.
    assert infer_line("GTL", override="nonsense") == "life"
    assert infer_line("GHS", override=None) == "medical"


def test_gpa_dual_axis_independent() -> None:
    # GPA keeps the accident form structure but lives on the Life tab.
    assert infer_profile("GPA") == "accident"
    assert infer_line("GPA") == "life"


def test_gp_is_medical_outpatient() -> None:
    # GP = "Group Clinical General Practitioner" — outpatient medical, not life
    # (corrected 2026-06-02). Both the tab line and the form profile move.
    assert infer_line("GP") == "medical"
    assert infer_profile("GP") == "outpatient"


def test_case_and_whitespace_insensitive() -> None:
    assert infer_line(" ghs ") == "medical"
    assert infer_line("gtl") == "life"


def test_new_codes_classified() -> None:
    # Every requested new code is classified (no silent default).
    medical = {"GHS2", "GMM2", "GOSP", "GOGP", "IMP", "MATERNITY", "VISION", "WELLNESS"}
    life = {"GDI", "GTPD"}
    for code in medical:
        assert _CODE_LINE[code] == "medical"
    for code in life:
        assert _CODE_LINE[code] == "life"
