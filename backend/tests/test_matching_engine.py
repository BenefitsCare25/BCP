"""Unit tests for the three-tier matching engine (brief §8.7)."""
from __future__ import annotations

from typing import Any

from app.models.category import Category
from app.models.employee import Employee
from app.services.matching_engine import (
    CONTAINMENT_CONFIDENCE,
    FUZZY_THRESHOLD,
    _build_exact_lookup,
    canonicalize_category_name,
    category_insured_entities,
    jaccard,
    match_one,
    rule_specificity,
    tokenize,
)


def _emp(
    category: str | None = None,
    derived: dict[str, Any] | None = None,
    entity: str | None = None,
) -> Employee:
    attrs: dict[str, Any] = {}
    if category:
        attrs["category"] = category
    if entity:
        attrs["entity"] = entity
    return Employee(
        id="emp-1",
        client_id="c",
        policy_year_id="py",
        staff_id="S1",
        employee_name="Test User",
        attribute_values=attrs,
        derived_attribute_values=derived or {},
    )


def _cat(
    cid: str,
    display_name: str,
    *,
    priority: int = 0,
    rule: dict[str, Any] | None = None,
    confidence: float | None = None,
    insured: str | None = None,
) -> Category:
    return Category(
        id=cid,
        policy_year_id="py",
        product_id=None,
        priority=priority,
        display_name=display_name,
        raw_description=display_name,
        matching_rule=rule,
        confidence=confidence,
        source="system_generated",
        status="needs_review",
        plan_assignments={"insured": insured} if insured else None,
    )


def _match(emp: Employee, cats: list[Category]):
    """Build the pre-computed indexes match_policy_year would build, then call match_one."""
    by_priority = sorted(cats, key=lambda c: c.priority)
    exact = {c.display_name.strip().lower(): c for c in cats if c.display_name}
    tokens = {c.id: tokenize(c.display_name) for c in cats}
    return match_one(emp, by_priority, exact, tokens)


def test_tokenize_keeps_numbers() -> None:
    assert tokenize("18 and above") == {"18", "and", "above"}


def test_tokenize_handles_punctuation_and_case() -> None:
    assert tokenize("Grade 15 (Married, 2-Child)") == {"grade", "15", "married", "2", "child"}


def test_jaccard_basic() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"a", "c"}) == 1 / 3
    assert jaccard(set(), {"a"}) == 0.0


def test_exact_name_wins_over_fuzzy() -> None:
    cats = [
        _cat("c1", "Grade 18 Married plus 1 child"),
        _cat("c2", "Grade 18 Married plus 2 child"),  # would beat fuzzily
    ]
    out = _match(_emp("Grade 18 Married plus 1 child"), cats)
    assert out.category_id == "c1"
    assert out.method == "exact_name"
    assert out.confidence == 1.0


def test_exact_name_is_case_insensitive() -> None:
    cats = [_cat("c1", "Grade 18 MARRIED plus 1 child")]
    out = _match(_emp("grade 18 married plus 1 child"), cats)
    assert out.category_id == "c1"
    assert out.method == "exact_name"


def test_fuzzy_matches_above_threshold() -> None:
    cats = [_cat("c1", "Grade 18 Married 2 child"), _cat("c2", "Single Grade 5")]
    # "Grade 18 Married 1 child" vs "Grade 18 Married 2 child"
    # Tokens: {grade,18,married,1,child} vs {grade,18,married,2,child}
    # Intersection: {grade,18,married,child} (4); union: 6 → 0.667 > 0.6
    out = _match(_emp("Grade 18 Married 1 child"), cats)
    assert out.method == "fuzzy_name"
    assert out.category_id == "c1"
    assert out.confidence is not None and out.confidence >= FUZZY_THRESHOLD


def test_fuzzy_below_threshold_falls_through_to_rule() -> None:
    rule = {"=": ["grade", "20"]}
    cats = [
        _cat("c1", "Completely Different Text", rule=rule, confidence=0.9),
    ]
    emp = _emp("Grade 20 Single", derived={"grade": 20})
    out = _match(emp, cats)
    assert out.method == "rule"
    assert out.category_id == "c1"
    assert out.confidence == 0.9


def test_fuzzy_tiebreak_prefers_lower_priority() -> None:
    # Two categories tie on Jaccard but differ on priority.
    cats = [
        _cat("c1", "Grade 18 Married 1 child", priority=10),
        _cat("c2", "Grade 18 Married 1 child", priority=5),
    ]
    # Use an inexact employee phrase so we fall to fuzzy (not exact).
    out = _match(_emp("Grade 18 Married 1 child plus"), cats)
    assert out.method == "fuzzy_name"
    assert out.category_id == "c2"


def test_rule_iterates_in_priority_order() -> None:
    rule_a = {"=": ["pass", "EP"]}
    rule_b = {"=": ["pass", "EP"]}
    cats = [
        _cat("low", "Cat Low", priority=10, rule=rule_a, confidence=0.5),
        _cat("hi", "Cat High", priority=1, rule=rule_b, confidence=0.9),
    ]
    emp = _emp(category=None, derived={"pass": "EP"})
    out = _match(emp, cats)
    # priority=1 is evaluated first → "hi" wins.
    assert out.category_id == "hi"
    assert out.method == "rule"
    assert out.confidence == 0.9


def test_rule_skips_categories_with_no_rule() -> None:
    cats = [
        _cat("c1", "No Rule Cat", priority=1),
        _cat("c2", "Has Rule", priority=2, rule={"=": ["pass", "WP"]}, confidence=0.7),
    ]
    out = _match(_emp(category=None, derived={"pass": "WP"}), cats)
    assert out.category_id == "c2"


def test_rule_uses_derived_over_raw() -> None:
    rule = {"=": ["family_status", "M2C"]}
    cats = [_cat("c1", "Married Two Kids", rule=rule, confidence=0.8)]
    # raw category is the unparseable concat; derived is the structured field.
    emp = _emp(
        "17 Married plus 2 child",
        derived={"family_status": "M2C", "grade": 17},
    )
    out = _match(emp, cats)
    assert out.method == "rule"
    assert out.category_id == "c1"


def test_no_category_field_no_exact_no_fuzzy() -> None:
    cats = [_cat("c1", "Anything", rule={"=": ["pass", "EP"]}, confidence=0.5)]
    emp = _emp(category=None, derived={"pass": "EP"})
    out = _match(emp, cats)
    assert out.method == "rule"


def _match_canon(emp: Employee, cats: list[Category]):
    """Like `_match`, but tokens come from the canonicalized display_name —
    mirrors what `_build_product_indices` feeds in production."""
    by_priority = sorted(cats, key=lambda c: c.priority)
    exact = {c.display_name.strip().lower(): c for c in cats if c.display_name}
    tokens = {c.id: tokenize(canonicalize_category_name(c.display_name)) for c in cats}
    return match_one(emp, by_priority, exact, tokens)


def test_canonicalize_strips_job_category_and_dependant_tail() -> None:
    assert (
        canonicalize_category_name(
            "Manager (Job category: E5 to E6, EE to EF) / All Eligible Dependants"
        )
        == "Manager"
    )
    assert (
        canonicalize_category_name("EVP and Above (Job category: A7 to A9)")
        == "EVP and Above"
    )
    # No qualifier → unchanged.
    assert canonicalize_category_name("Manager") == "Manager"


def test_fuzzy_matches_once_job_category_suffix_is_stripped() -> None:
    # Raw label drowns the tier name in code-map tokens (Jaccard ~0.29 < 0.6);
    # after canonicalization the tier name matches the roster value exactly.
    cats = [
        _cat("c1", "Executive to AM & Secretary (Job category: E1 to E4, EA to ED)"),
    ]
    out = _match_canon(_emp("Executive to AM & Secretary"), cats)
    assert out.category_id == "c1"
    assert out.method == "fuzzy_name"
    assert out.confidence is not None and out.confidence >= FUZZY_THRESHOLD


def test_containment_matches_grouped_label() -> None:
    # "Manager" is a strict subset of a grouped tier — Jaccard misses it, the
    # containment fallback catches it at the dedicated confidence.
    cats = [_cat("c1", "Manager, Executive to AM and Secretary (Job category: E1)")]
    out = _match_canon(_emp("Manager"), cats)
    assert out.category_id == "c1"
    assert out.method == "fuzzy_name"
    assert out.confidence == CONTAINMENT_CONFIDENCE


def test_rule_beats_containment_when_both_match() -> None:
    # Containment is the LAST tier: a real rule match must win over a loose
    # name-subset match, even though "Manager" is contained in the grouped label.
    cats = [
        _cat("grouped", "Manager, Executive to AM and Secretary"),
        _cat("ruled", "Senior Band", rule={"=": ["pass", "EP"]}, confidence=0.9),
    ]
    out = _match_canon(_emp("Manager", derived={"pass": "EP"}), cats)
    assert out.category_id == "ruled"
    assert out.method == "rule"


def test_employee_label_is_canonicalized_for_matching() -> None:
    # A parenthetical-laden employee category matches the clean tier name (both
    # sides are canonicalized before tokenizing).
    cats = [_cat("c1", "Manager")]
    out = _match_canon(_emp("Manager (Grade E5)"), cats)
    assert out.category_id == "c1"
    assert out.method == "fuzzy_name"


def test_containment_prefers_most_specific_then_falls_to_rule() -> None:
    # A bare connective label can't be "contained" in everything: "and" alone
    # has no meaningful token, so it must fall through to rule evaluation.
    cats = [
        _cat("grouped", "Senior Team and Leadership"),
        _cat("ruled", "Whatever", rule={"=": ["pass", "EP"]}, confidence=0.6),
    ]
    out = _match_canon(_emp("and", derived={"pass": "EP"}), cats)
    assert out.method == "rule"
    assert out.category_id == "ruled"


def test_no_matches_at_all_returns_none() -> None:
    cats = [_cat("c1", "Foo Bar Baz", rule={"=": ["pass", "EP"]}, confidence=0.5)]
    out = _match(_emp("Hello World", derived={"pass": "WP"}), cats)
    assert out.category_id is None
    assert out.method is None
    assert out.confidence is None


def test_rule_specificity_counts_leaf_conditions() -> None:
    assert rule_specificity({">=": ["grade", 18]}) == 1
    assert rule_specificity(
        {"and": [{">=": ["grade", 18]}, {"in": ["pass", ["WP", "SP"]]}]}
    ) == 2
    assert rule_specificity({"and": []}) == 0  # "All Employees" catch-all
    assert rule_specificity(
        {"or": [{"=": ["a", 1]}, {"=": ["b", 2]}, {"not": {"=": ["c", 3]}}]}
    ) == 3
    assert rule_specificity(None) == 0


def test_most_specific_rule_wins_over_looser_lower_priority() -> None:
    # The local rule (grade only) has the LOWER priority number so it's
    # evaluated first, but the foreign-worker rule (grade + pass) is more
    # specific and must win — otherwise FWs get shadowed onto local plans.
    local = _cat("local", "Grade 18+", priority=1,
                 rule={">=": ["grade", 18]}, confidence=0.85)
    fw = _cat("fw", "FW WP/SP Grade 18+", priority=4,
              rule={"and": [{">=": ["grade", 18]}, {"in": ["pass", ["WP", "SP"]]}]},
              confidence=0.75)
    out = _match(_emp(derived={"grade": 20, "pass": "WP"}), [local, fw])
    assert out.category_id == "fw"


def test_local_employee_still_matches_looser_rule() -> None:
    # A non-FW employee matches only the local rule (the specific one fails).
    local = _cat("local", "Grade 18+", priority=1,
                 rule={">=": ["grade", 18]}, confidence=0.85)
    fw = _cat("fw", "FW WP/SP Grade 18+", priority=4,
              rule={"and": [{">=": ["grade", 18]}, {"in": ["pass", ["WP", "SP"]]}]},
              confidence=0.75)
    out = _match(_emp(derived={"grade": 20, "pass": "CITIZEN"}), [local, fw])
    assert out.category_id == "local"


def test_confirmed_status_beats_more_specific_needs_review() -> None:
    # status_rank is primary: a confirmed (looser) rule still beats a more
    # specific needs_review one, preserving the confirmed-precedence invariant.
    confirmed_loose = _cat("conf", "Grade 18+", priority=5,
                           rule={">=": ["grade", 18]}, confidence=0.85)
    confirmed_loose.status = "confirmed"
    specific_review = _cat("rev", "FW WP/SP 18+", priority=1,
                           rule={"and": [{">=": ["grade", 18]},
                                         {"in": ["pass", ["WP", "SP"]]}]},
                           confidence=0.75)
    out = _match(_emp(derived={"grade": 20, "pass": "WP"}),
                 [confirmed_loose, specific_review])
    assert out.category_id == "conf"


def test_empty_category_list_returns_none() -> None:
    out = _match(_emp("anything"), [])
    assert out.category_id is None


# ── Insured-entity gate (multi-subsidiary schemes, e.g. WICA) ────────────────


def test_entity_gate_routes_same_named_categories_per_subsidiary() -> None:
    """WICA-style: one 'Non-Manual Staffs' category per insured entity — each
    employee lands in their OWN entity's category, in both directions."""
    cats = [
        _cat("cdl", "Non-Manual Staffs", priority=0,
             insured="City Developments Limited"),
        _cat("legrove", "Non-Manual Staffs", priority=1,
             insured="Le Grove Management Pte Ltd"),
    ]
    out = _match(
        _emp("Non-Manual Staffs", entity="Le Grove Management Pte Ltd"), cats
    )
    assert out.category_id == "legrove"
    out = _match(
        _emp("Non-Manual Staffs", entity="City Developments Limited"), cats
    )
    assert out.category_id == "cdl"


def test_entity_gate_blank_sides_are_wildcards() -> None:
    # Employee without an Entity column still matches a restricted category.
    cats = [_cat("c1", "Managers", insured="City Developments Limited")]
    assert _match(_emp("Managers"), cats).category_id == "c1"
    # Category without insured matches any entity.
    cats = [_cat("c1", "Managers")]
    out = _match(_emp("Managers", entity="Le Grove Management Pte Ltd"), cats)
    assert out.category_id == "c1"


def test_entity_gate_normalizes_punctuation_and_suffixes() -> None:
    """Roster 'CityNexus Pte. Ltd.' must equal slip 'CityNexus Pte Ltd', and
    'Limited' must equal 'Ltd' — comma-separated insured lists split."""
    cats = [
        _cat(
            "c1",
            "All Employees",
            insured=(
                "City Developments Ltd, City Serviced Offices Pte Ltd, "
                "CityNexus Pte Ltd"
            ),
        )
    ]
    out = _match(_emp("All Employees", entity="CityNexus Pte. Ltd."), cats)
    assert out.category_id == "c1"
    out = _match(_emp("All Employees", entity="City Developments Limited"), cats)
    assert out.category_id == "c1"


def test_entity_outside_every_insured_list_matches_nothing() -> None:
    """CDL's WICA slip omits Krungthep Rimnam (Thai entity) — its employees
    must not match ANY WICA category, in any tier (exact/fuzzy/rule/containment)."""
    rule = {"and": []}  # vacuously-true catch-all
    cats = [
        _cat("cdl", "Non-Manual Staffs", rule=rule,
             insured="City Developments Limited"),
        _cat("legrove", "Non-Manual Staffs", rule=rule,
             insured="Le Grove Management Pte Ltd"),
    ]
    out = _match(
        _emp("Non-Manual Staffs", entity="Krungthep Rimnam Limited"), cats
    )
    assert out.category_id is None


def test_entity_gate_applies_to_rule_tier() -> None:
    rule = {">=": ["grade", 10]}
    cats = [
        _cat("cdl", "Alpha", rule=rule, confidence=0.85,
             insured="City Developments Limited"),
        _cat("legrove", "Beta", rule=rule, confidence=0.85,
             insured="Le Grove Management Pte Ltd"),
    ]
    out = _match(
        _emp(derived={"grade": 12}, entity="Le Grove Management Pte Ltd"), cats
    )
    assert out.category_id == "legrove"
    assert out.method == "rule"


def test_entity_gate_accepts_token_list() -> None:
    """`insured` is stored as a LIST of entity tokens (the picker's shape). A
    legacy comma-joined string keeps working — see `insured_names`."""
    cats = [
        _cat(
            "c1",
            "All Employees",
            insured=["City Developments Ltd", "CityNexus Pte Ltd"],
        )
    ]
    assert _match(_emp("All Employees", entity="CityNexus Pte. Ltd."), cats).category_id == "c1"
    assert _match(_emp("All Employees", entity="Le Grove Pte Ltd"), cats).category_id is None


def test_entity_token_keeps_comma_inside_legal_name() -> None:
    """A registered name containing a comma is ONE entity when stored as a
    token. The comma-string form is what splits it — the bug the list fixes."""
    name = "Acme Pte Ltd, Singapore Branch"
    cats = [_cat("c1", "All Employees", insured=[name])]
    assert _match(_emp("All Employees", entity=name), cats).category_id == "c1"

    # Same name in the legacy string form splits on the comma and no longer
    # matches the whole entity — documents why storage moved to tokens.
    legacy = [_cat("c2", "All Employees", insured=name)]
    assert _match(_emp("All Employees", entity=name), legacy).category_id is None


def test_insured_names_shapes() -> None:
    from app.services.matching_engine import insured_names

    assert insured_names(["A", " B ", ""]) == ["A", "B"]
    assert insured_names("A, B") == ["A", "B"]
    assert insured_names(None) == []
    assert insured_names("") == []


def test_alias_bridges_either_side() -> None:
    """`resolve_entities` applies the alias map to BOTH sides, so the
    abbreviation may sit on the roster or on the category."""
    from app.services.matching_engine import resolve_entities

    aliases = {"cso": frozenset({"city serviced offices pte ltd"})}

    # Roster carries the abbreviation, category the legal name.
    cats = [_cat("c1", "All Employees", insured=["City Serviced Offices Pte Ltd"])]
    assert _match(_emp("All Employees", entity="CSO"), cats).category_id is None
    idx = {c.id: category_insured_entities(c, aliases) for c in cats}
    out = match_one(
        _emp("All Employees", entity="CSO"),
        cats,
        _build_exact_lookup(cats),
        {c.id: tokenize(canonicalize_category_name(c.display_name)) for c in cats},
        idx,
        entity_aliases=aliases,
    )
    assert out.category_id == "c1"

    # And the mirror: category carries the abbreviation, roster the legal name.
    cats2 = [_cat("c2", "All Employees", insured=["CSO"])]
    idx2 = {c.id: category_insured_entities(c, aliases) for c in cats2}
    out2 = match_one(
        _emp("All Employees", entity="City Serviced Offices Pte Ltd"),
        cats2,
        _build_exact_lookup(cats2),
        {c.id: tokenize(canonicalize_category_name(c.display_name)) for c in cats2},
        idx2,
        entity_aliases=aliases,
    )
    assert out2.category_id == "c2"

    # Resolution expands to a SET; a name with no alias is a one-element set,
    # a blank name the empty set.
    assert resolve_entities("City Serviced Offices Pte Ltd") == frozenset(
        {"city serviced offices pte ltd"}
    )
    assert resolve_entities(None) == frozenset()

    # Single-hop: A->B, B->C must not chain A->C.
    chain = {"a": frozenset({"b"}), "b": frozenset({"c"})}
    assert resolve_entities("A", chain) == frozenset({"b"})


def test_multi_entity_alias_matches_every_subsidiary() -> None:
    """One roster spelling can stand for several registered subsidiaries, each
    a separate insured block — the employee must match EVERY one."""
    aliases = {
        "stmicroelectronics pte ltd": frozenset(
            {"stmicroelectronics pte ltd amk", "stmicroelectronics pte ltd tpy"}
        )
    }
    cats = [
        _cat("amk", "All Employees", priority=0, insured=["STMicroelectronics Pte Ltd AMK"]),
        _cat("tpy", "All Employees", priority=1, insured=["STMicroelectronics Pte Ltd TPY"]),
    ]
    idx = {c.id: category_insured_entities(c, aliases) for c in cats}
    tokens = {c.id: tokenize(canonicalize_category_name(c.display_name)) for c in cats}

    emp = _emp("All Employees", entity="STMICROELECTRONICS PTE LTD")
    # Employee lands on the first-priority category, but the gate lets EITHER
    # through — verify neither is excluded by resolving the gate directly.
    from app.services.matching_engine import _entity_allows, employee_entity

    emp_entities = employee_entity(emp.attribute_values, aliases)
    assert _entity_allows(idx["amk"], emp_entities)
    assert _entity_allows(idx["tpy"], emp_entities)

    out = match_one(emp, cats, _build_exact_lookup(cats), tokens, idx, entity_aliases=aliases)
    assert out.category_id == "amk"  # priority tie-break, but both were allowed

    # An unaliased roster entity is excluded from the sibling it doesn't name.
    other = _emp("All Employees", entity="Some Other Co Pte Ltd")
    other_entities = employee_entity(other.attribute_values, aliases)
    assert not _entity_allows(idx["amk"], other_entities)
    assert not _entity_allows(idx["tpy"], other_entities)


def test_entity_vocab_reconciliation_and_suggestions() -> None:
    """Config entities that match no roster value are surfaced with the roster
    spelling they most likely mean — acronyms included, which token overlap
    alone cannot find."""
    from app.services.entity_vocab import _acronym, _closest, _index_candidates

    roster = _index_candidates({
        "city serviced offices pte ltd": {"value": "City Serviced Offices Pte Ltd"},
        "le grove management pte ltd": {"value": "Le Grove Management Pte Ltd"},
    })
    # Acronym — shares no words with its expansion.
    assert _closest("cso", roster) == "City Serviced Offices Pte Ltd"
    # Partial name — found by token overlap.
    assert _closest("le grove", roster) == "Le Grove Management Pte Ltd"
    # Unrelated — no suggestion rather than a nonsense one.
    assert _closest("totally unrelated zzz", roster) is None
    # A shared corporate suffix is NOT similarity: almost every SG entity ends
    # in "Pte Ltd", so counting it would pair unrelated companies.
    assert _closest("typo holdings pte ltd", roster) is None
    # Corporate suffixes carry no identity and are dropped from initials.
    assert _acronym("city serviced offices pte ltd") == "cso"


def test_product_entities_take_precedence_over_category_insured() -> None:
    """The product's Entities field (set once on the setup header) gates every
    category. Only when it is EMPTY does each category's own slip `insured`
    still gate — that fallback is what keeps multi-entity slips and every
    pre-existing configuration matching unchanged."""
    from app.models.product import Product
    from app.services.matching_engine import product_entities

    cat = _cat("c1", "All Employees", insured=["Le Grove Management Pte Ltd"])

    prod = Product(id="p1", code="GTL", display_name="GTL", product_metadata=None)
    assert product_entities(prod) == frozenset()
    # Empty product field → category's own insured is the gate.
    gate = product_entities(prod) or category_insured_entities(cat)
    assert gate == frozenset({"le grove management pte ltd"})

    prod.product_metadata = {"entities": ["City Developments Limited"]}
    gate = product_entities(prod) or category_insured_entities(cat)
    assert gate == frozenset({"city developments ltd"})

    # Absent product (unlinked categories) must not raise.
    assert product_entities(None) == frozenset()
