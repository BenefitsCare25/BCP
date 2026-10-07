"""Deterministic category-rule proposal from the company's own vocabulary."""

from __future__ import annotations

import re
from typing import Any

from app.services.eligibility_mapping.base import (
    _ALL_EMPLOYEES_RE,
    _ALL_OTHER_RE,
    _BASED_IN_RE,
    _COST_CENTRE_ATTRS,
    _COST_CENTRE_RE,
    _EXCLUSION_RE,
    _NATIONALITY_ATTRS,
    _NATIONALITY_RE,
    _PASS_ATTRS,
    AttributeValueCatalog,
    Rule,
    RuleProposal,
)
from app.services.eligibility_mapping.clauses import (
    _actual_pass_values,
    _bargainable_mapping,
    _explicit_grade_mapping,
    _oversized_explicit_grade_clauses,
    _pass_codes,
    _rule_for_values,
    _unresolved_list_clauses,
)
from app.services.eligibility_mapping.vocabulary import (
    _best_value_mapping,
    _exact_value_mapping,
    _intent_text,
    _location_cohort_label,
    _location_value_mapping,
    _multi_named_cohort_mapping,
    _tokens,
)
from app.services.eligibility_scope import prefix_cohort_value
from app.services.explicit_grade_clauses import (
    explicit_grade_clauses,
    has_explicit_grade_clause,
)


def _location_proposal(
    text: str,
    without_exclusion: str,
    exclusion_text: str,
    catalog: AttributeValueCatalog,
) -> RuleProposal | None:
    location_match = _BASED_IN_RE.search(text)
    if not location_match:
        return None
    location_attr, location_values, nationality_proxy = _location_value_mapping(
        location_match.group(1), catalog
    )
    base_text = _BASED_IN_RE.sub("", without_exclusion).strip(" ()-")
    base_attr, base_values = _best_value_mapping(base_text, catalog)
    rules: list[Rule] = []
    readings: list[str] = []
    referenced: list[str] = []
    unresolved: list[str] = []

    for attribute_id, values in (
        (base_attr, base_values),
        (location_attr, location_values),
    ):
        if attribute_id and values:
            rules.append(_rule_for_values(attribute_id, values))
            readings.append(f"{attribute_id} is one of {', '.join(map(str, values))}")
            referenced.append(attribute_id)
    if not location_attr or not location_values:
        unresolved.append(location_match.group(0).strip())
    elif nationality_proxy:
        unresolved.append(
            f"{location_match.group(0).strip()} mapped through nationality; "
            "confirm nationality represents work base"
        )

    if exclusion_text:
        ex_attr, excluded = _best_value_mapping(exclusion_text, catalog)
        if ex_attr and excluded:
            rules.append(_rule_for_values(ex_attr, excluded, negate=True))
            readings.append(f"except {ex_attr} {', '.join(map(str, excluded))}")
            referenced.append(ex_attr)
        else:
            unresolved.append(exclusion_text)

    rule = rules[0] if len(rules) == 1 else {"and": rules} if rules else None
    return RuleProposal(
        rule=rule,
        human_readable=" and ".join(readings) if readings else "Work location needs mapping",
        confidence=0.9 if rule and not unresolved else 0.5,
        source="roster_values" if rule else "unmapped",
        validation_state="proposed" if rule and not unresolved else "needs_review",
        unresolved_clauses=list(dict.fromkeys(unresolved)),
        referenced_attributes=list(dict.fromkeys(referenced)),
    )


def _semantic_field_mapping(
    text: str,
    catalog: AttributeValueCatalog,
) -> tuple[str | None, str | None, list[Any]]:
    for label, pattern, attributes in (
        ("nationality", _NATIONALITY_RE, _NATIONALITY_ATTRS),
        ("cost centre", _COST_CENTRE_RE, _COST_CENTRE_ATTRS),
    ):
        if pattern.search(text):
            attribute_id, values = _best_value_mapping(
                text,
                catalog,
                allowed=attributes,
            )
            return label, attribute_id, values
    return None, None, []


def propose_category_rule(description: str, catalog: AttributeValueCatalog) -> RuleProposal:
    """Propose a rule using the company's own non-PII roster vocabulary.

    This function never calls AI and never marks a rule confirmed. It returns a
    useful partial proposal with explicit ``unresolved_clauses`` when the slip
    expresses an open hierarchy (``and above``) or a clause has no roster-backed
    attribute/value mapping.
    """

    text = _intent_text(description)
    # Inclusion notes widen an already universal cohort; they do not restrict
    # it to the examples in parentheses ("All Employees incl. WP/SP").
    universal = re.sub(r"\s*\(\s*(?:incl\.?|including)\s+[^)]*\)\s*$", "", text, flags=re.I)
    if _ALL_EMPLOYEES_RE.fullmatch(universal):
        return RuleProposal(
            rule={"and": []},
            human_readable="All employees",
            confidence=0.95,
            source="deterministic",
            validation_state="proposed",
        )
    if _ALL_OTHER_RE.match(text):
        text = universal
    # Company roster labels often prefix the actual insurer cohort with local
    # grade codes ("A and B - MSO Grade"). Match the complete labelled suffix;
    # never infer a hierarchy from a substring such as the letters "SP".
    qualifier = _ALL_OTHER_RE.sub("", text).strip(" ().") if _ALL_OTHER_RE.match(text) else text

    def cohort_words(value: str) -> list[str]:
        value = re.sub(r"\bs[ -]?pass\b", "SP", value, flags=re.I)
        value = re.sub(r"\bwork\s+permit\b", "WP", value, flags=re.I)
        return [token for token in _tokens(value) if token not in {"all", "and", "only"}]

    for attribute in ("employee_category", "category"):
        matches = [
            value
            for value in catalog.values.get(attribute, [])
            if len(label_parts := re.split(r"\s+-\s+|\s*:\s*", str(value))) > 1
            and cohort_words(label_parts[-1]) == cohort_words(qualifier)
            and cohort_words(qualifier)
        ]
        if matches:
            return RuleProposal(
                rule=_rule_for_values(attribute, matches),
                human_readable=f"{attribute} is one of {', '.join(map(str, matches))}",
                confidence=0.95,
                source="roster_values",
                validation_state="proposed",
                referenced_attributes=[attribute],
            )
    oversized_clauses = _oversized_explicit_grade_clauses(text)
    if oversized_clauses:
        return RuleProposal(
            rule=None,
            human_readable="Explicit employee range needs review",
            confidence=0.0,
            source="unmapped",
            validation_state="needs_review",
            unresolved_clauses=oversized_clauses,
        )
    without_exclusion = _EXCLUSION_RE.sub("", text).strip(" ()")
    relative_remainder = bool(_ALL_OTHER_RE.match(without_exclusion))
    explicit_codes = has_explicit_grade_clause(without_exclusion)
    explicit_attr, explicit_values, unresolved = _explicit_grade_mapping(
        without_exclusion, catalog
    )
    if explicit_codes and not explicit_attr:
        return RuleProposal(
            rule=None,
            human_readable="Explicit employee codes need an employee field mapping",
            confidence=0.0,
            source="unmapped",
            validation_state="needs_review",
            unresolved_clauses=unresolved or explicit_grade_clauses(without_exclusion),
        )

    if _ALL_EMPLOYEES_RE.match(without_exclusion):
        return RuleProposal(
            rule={"and": []},
            human_readable="All employees",
            confidence=0.95,
            source="deterministic",
            validation_state="proposed",
        )

    # A roster-owned text label is useful only when the slip gives no explicit
    # employee codes. Otherwise its broad wording can mask the code boundary.
    if not explicit_codes:
        exact_attr, exact_values = _exact_value_mapping(text, catalog)
        if exact_attr and exact_values:
            return RuleProposal(
                rule=_rule_for_values(exact_attr, exact_values),
                human_readable=f"{exact_attr} is {exact_values[0]}",
                confidence=0.98,
                source="roster_values",
                validation_state="proposed",
                referenced_attributes=[exact_attr],
            )

        named_attr, named_values = _multi_named_cohort_mapping(text, catalog)
        if named_attr and named_values:
            return RuleProposal(
                rule=_rule_for_values(named_attr, named_values),
                human_readable=(f"{named_attr} is one of {', '.join(map(str, named_values))}"),
                confidence=0.98,
                source="roster_values",
                validation_state="proposed",
                referenced_attributes=[named_attr],
            )

    exclusion_text = ""
    if exclusion := _EXCLUSION_RE.search(text):
        exclusion_text = exclusion.group(1).strip()

    # Relative cohorts are compiled after their specific siblings. The matching
    # engine's specificity ordering makes an empty-AND the safe remainder; a
    # stated exclusion is retained when the company vocabulary can express it.
    remainder_qualifier = _ALL_OTHER_RE.sub("", without_exclusion).strip(" ().")
    if relative_remainder and not explicit_codes and not remainder_qualifier:
        if not exclusion_text:
            return RuleProposal(
                rule={"and": []},
                human_readable="All remaining employees",
                confidence=0.9,
                source="product_context",
                validation_state="proposed",
                relative_remainder=True,
            )
        attr, excluded = _best_value_mapping(exclusion_text, catalog)
        if attr and excluded:
            return RuleProposal(
                rule=_rule_for_values(attr, excluded, negate=True),
                human_readable=f"All remaining employees except {', '.join(map(str, excluded))}",
                confidence=0.9,
                source="roster_values",
                validation_state="proposed",
                referenced_attributes=[attr],
                relative_remainder=True,
            )
        return RuleProposal(
            rule=None,
            human_readable="All remaining employees; exclusion needs mapping",
            confidence=0.5,
            source="product_context",
            validation_state="needs_review",
            unresolved_clauses=[exclusion_text],
            relative_remainder=True,
        )

    # Explicit insurer field clauses are more authoritative than prose titles.
    # CDL writes the exact job-category codes in parentheses; STM uses ordered
    # Hay Job Grade ranges. Compile those first, and combine them with pass-type
    # requirements instead of returning early and silently dropping the grade.
    parts: list[Rule] = []
    readings: list[str] = []
    referenced: list[str] = []
    if explicit_attr and explicit_values:
        parts.append(_rule_for_values(explicit_attr, explicit_values))
        readings.append(f"{explicit_attr} is one of {', '.join(map(str, explicit_values))}")
        referenced.append(explicit_attr)

    semantic_label, semantic_attr, semantic_values = _semantic_field_mapping(
        without_exclusion,
        catalog,
    )
    if semantic_label:
        if semantic_attr and semantic_values:
            parts.append(_rule_for_values(semantic_attr, semantic_values))
            readings.append(f"{semantic_attr} is one of {', '.join(map(str, semantic_values))}")
            referenced.append(semantic_attr)
        else:
            unresolved.append(f"{semantic_label} values")

    pass_codes = _pass_codes(without_exclusion)
    pass_attr = next(
        (
            attr
            for attr in _PASS_ATTRS
            if attr in catalog.attribute_ids and catalog.values.get(attr)
        ),
        None,
    )
    pass_values = (
        _actual_pass_values(pass_codes, catalog.values[pass_attr])
        if pass_codes and pass_attr
        else []
    )
    if pass_codes:
        if pass_attr and len(pass_values) == len(pass_codes):
            parts.append(_rule_for_values(pass_attr, pass_values))
            readings.append(f"{pass_attr} is one of {', '.join(map(str, pass_values))}")
            referenced.append(pass_attr)
        else:
            unresolved.append("work-pass types")

    # One recurring CDL cohort is a genuine OR: Officers by job category, plus
    # every Thailand employee except Directors. Keeping it as an AND would drop
    # both the Singapore officers and the non-officer Thailand employees.
    if parts and re.search(r"\band\s+all\s+employees?\s+based\s+in\b", text, re.I):
        cohort_attr, cohort_value = _location_cohort_label(text, catalog)
        if cohort_attr and cohort_value is not None:
            primary = parts[0] if len(parts) == 1 else {"and": parts}
            return RuleProposal(
                rule={"or": [primary, {"=": [cohort_attr, cohort_value]}]},
                human_readable=f"{' and '.join(readings)}, or {cohort_attr} is {cohort_value}",
                confidence=0.95 if not unresolved else 0.7,
                source="roster_values",
                validation_state="proposed" if not unresolved else "needs_review",
                unresolved_clauses=unresolved,
                referenced_attributes=list(dict.fromkeys([*referenced, cohort_attr])),
            )
        location_match = _BASED_IN_RE.search(text)
        location_attr, location_values, nationality_proxy = (
            _location_value_mapping(location_match.group(1), catalog)
            if location_match
            else (None, [], False)
        )
        exclusion_attr, exclusion_values = (
            _best_value_mapping(exclusion_text, catalog) if exclusion_text else (None, [])
        )
        if location_attr and location_values and exclusion_attr and exclusion_values:
            if nationality_proxy and location_match:
                unresolved.append(
                    f"{location_match.group(0).strip()} mapped through nationality; "
                    "confirm nationality represents work base"
                )
            location_parts = [
                _rule_for_values(location_attr, location_values),
                _rule_for_values(exclusion_attr, exclusion_values, negate=True),
            ]
            primary = parts[0] if len(parts) == 1 else {"and": parts}
            rule = {"or": [primary, {"and": location_parts}]}
            return RuleProposal(
                rule=rule,
                human_readable=(
                    f"{' and '.join(readings)}, or {location_attr} is one of "
                    f"{', '.join(map(str, location_values))} except "
                    f"{', '.join(map(str, exclusion_values))}"
                ),
                confidence=0.9 if not unresolved else 0.7,
                source="roster_values",
                validation_state="proposed" if not unresolved else "needs_review",
                unresolved_clauses=unresolved,
                referenced_attributes=list(
                    dict.fromkeys([*referenced, location_attr, exclusion_attr])
                ),
            )
        unresolved.append(
            location_match.group(0).strip()
            if location_match
            else "employee work-location exception"
        )

    if relative_remainder and remainder_qualifier and not parts:
        # A qualified remainder is not an unrestricted fallback. Resolve its
        # grade against this company's vocabulary or retain it for review.
        qualifier = re.sub(r"\bgrade\b", "", remainder_qualifier, flags=re.I).strip()
        grade_attr, grade_values = _best_value_mapping(
            qualifier,
            catalog,
            allowed=("job_grade", "grade", "job_category", "employee_category", "class"),
        )
        if grade_attr and grade_values:
            parts.append(_rule_for_values(grade_attr, grade_values))
            readings.append(f"{grade_attr} is one of {', '.join(map(str, grade_values))}")
            referenced.append(grade_attr)
        else:
            return RuleProposal(
                rule=None,
                human_readable="Qualified employee cohort needs mapping",
                confidence=0.0,
                source="unmapped",
                validation_state="needs_review",
                unresolved_clauses=[remainder_qualifier],
                relative_remainder=True,
            )

    if parts:
        rule = parts[0] if len(parts) == 1 else {"and": parts}
        bargainable = bool(re.search(r"\band\s+bargainable\s+(?:staff|employees?)\b", text, re.I))
        if bargainable:
            bargainable_attr, bargainable_values = _bargainable_mapping(catalog)
            if bargainable_attr and bargainable_values:
                rule = {
                    "or": [
                        rule,
                        _rule_for_values(bargainable_attr, bargainable_values),
                    ]
                }
                readings.append(
                    f"{bargainable_attr} is one of {', '.join(map(str, bargainable_values))}"
                )
                referenced.append(bargainable_attr)
            else:
                unresolved.append("Bargainable Staff")
        return RuleProposal(
            rule=rule,
            human_readable=(" or " if bargainable else " and ").join(readings),
            confidence=0.95 if not unresolved else 0.7,
            source="roster_values",
            validation_state="proposed" if not unresolved else "needs_review",
            unresolved_clauses=list(dict.fromkeys(unresolved)),
            referenced_attributes=list(dict.fromkeys(referenced)),
        )

    if location_proposal := _location_proposal(text, without_exclusion, exclusion_text, catalog):
        return location_proposal

    if semantic_label:
        return RuleProposal(
            rule=None,
            human_readable=f"{semantic_label.title()} needs mapping",
            confidence=0.2,
            source="unmapped",
            validation_state="needs_review",
            unresolved_clauses=list(dict.fromkeys(unresolved)),
        )

    attr, included = _best_value_mapping(without_exclusion, catalog)
    if attr and included:
        rule = _rule_for_values(attr, included)
        unresolved = _unresolved_list_clauses(without_exclusion, included)
        if re.search(r"(?:\band\b|&)\s+above\b", without_exclusion, re.IGNORECASE):
            unresolved.append("above")
        readable = f"{attr} is one of {', '.join(map(str, included))}"
        referenced = [attr]
        if exclusion_text:
            ex_attr, excluded = _best_value_mapping(exclusion_text, catalog)
            if ex_attr and excluded:
                rule = {"and": [rule, _rule_for_values(ex_attr, excluded, negate=True)]}
                readable += f" except {ex_attr} {', '.join(map(str, excluded))}"
                referenced.append(ex_attr)
            else:
                unresolved.append(exclusion_text)
        return RuleProposal(
            rule=rule,
            human_readable=readable,
            confidence=0.7 if unresolved else 0.9,
            source="roster_values",
            validation_state="needs_review" if unresolved else "proposed",
            unresolved_clauses=unresolved,
            referenced_attributes=list(dict.fromkeys(referenced)),
        )

    prefix_attr, prefix_value = prefix_cohort_value(without_exclusion, catalog.values)
    if prefix_attr and prefix_value is not None and not exclusion_text:
        return RuleProposal(
            rule={"=": [prefix_attr, prefix_value]},
            human_readable=f"{prefix_attr} is {prefix_value}",
            confidence=0.85,
            source="roster_values",
            validation_state="proposed",
            referenced_attributes=[prefix_attr],
        )

    return RuleProposal(
        rule=None,
        human_readable="Unmapped — company employee-listing field/value mapping required",
        confidence=0.2,
        source="unmapped",
        validation_state="unmapped",
        unresolved_clauses=[text] if text else ["empty description"],
    )
