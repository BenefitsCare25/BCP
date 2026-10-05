"""Identifiers resolve by the product's explicit entity mapping, never by list order."""

import pytest

from app.models import Claim, ProductTerm
from app.services.claim_placement import placement_cells
from app.services.policy_numbers import placement_policy_numbers, resolve_policy_number


def test_entity_specific_number_precedes_explicit_default():
    term = ProductTerm(policy_number_mappings=[
        {"entity": None, "policy_number": "DEFAULT"},
        {"entity": "Acme Pte Ltd, Singapore Branch", "policy_number": "BRANCH/2026/001"},
    ])
    resolved = resolve_policy_number(term, " acme  pte ltd, singapore branch ")
    assert resolved.number == "BRANCH/2026/001"
    assert resolve_policy_number(term, "Other").number == "DEFAULT"
    assert resolve_policy_number(term, None).number == "DEFAULT"
    assert "Acme Pte Ltd, Singapore Branch: BRANCH/2026/001" in placement_policy_numbers(term)


@pytest.mark.parametrize("source", ["ONE, TWO", "ONE / TWO", "ONE; TWO", "ONE\nTWO"])
def test_ambiguous_legacy_policy_never_becomes_member_policy(source):
    term = ProductTerm(policy_number=source)
    assert resolve_policy_number(term, "A").number is None
    assert resolve_policy_number(term, "A").status == "Needs review"
    assert placement_policy_numbers(term) == "Needs review"


@pytest.mark.parametrize("source", ["TBA", "TBC", "Pending", "", None])
def test_unissued_number_is_reported_as_unassigned(source):
    assert resolve_policy_number(ProductTerm(policy_number=source), "A").status == "Not assigned"


def test_entity_mapping_never_falls_back_to_legacy_scalar():
    term = ProductTerm(policy_number="UNSAFE", policy_number_mappings=[
        {"entity": "A", "policy_number": "ONE"},
    ])
    assert resolve_policy_number(term, "B").number is None
    assert resolve_policy_number(term, "B").status == "Entity not assigned"
    assert resolve_policy_number(term, None).status == "Entity missing"


def test_ambiguous_filing_snapshot_is_preserved_and_flagged():
    snapshot = {"product_code": "GHS", "policy_number": "ONE, TWO"}
    claim = Claim(claim_kind="insured", product_code="GHS", intake_meta={
        "placement_snapshot": snapshot,
    })
    cells = placement_cells(None, claim)
    assert cells[0] is None
    assert cells[3] == "At filing"
    assert cells[4] == "Needs review: legacy filing snapshot"
    assert claim.intake_meta["placement_snapshot"] == snapshot
