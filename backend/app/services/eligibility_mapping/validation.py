"""Structural, company, and AI-specific matching-rule validation."""

from __future__ import annotations

import re
from typing import Any

from app.services.eligibility_mapping.base import (
    _ALL_EMPLOYEES_RE,
    _ALL_OTHER_RE,
    _BASED_IN_RE,
    _EXCLUSION_RE,
    _LEAF_OPS,
    _MAX_RULE_DEPTH,
    _MAX_RULE_NODES,
    _MAX_SET_VALUES,
    _NATIONALITY_ATTRS,
    AttributeValueCatalog,
    Rule,
    RuleValidation,
)
from app.services.eligibility_mapping.clauses import (
    _explicit_grade_mapping,
    _oversized_explicit_grade_clauses,
    _rule_for_values,
    _values_from_grade_clause,
)
from app.services.eligibility_mapping.proposal import propose_category_rule
from app.services.eligibility_mapping.vocabulary import (
    _best_value_mapping,
    _exact_location_countries,
    _intent_text,
    _location_cohort_label,
    _location_value_mapping,
)
from app.services.explicit_grade_clauses import (
    explicit_grade_clauses,
    has_explicit_grade_clause,
)
from app.services.flex_membership import nationality_country_exact


def validate_matching_rule(
    rule: Rule | None,
    catalog: AttributeValueCatalog,
    *,
    allowed_values: dict[str, list[Any]] | None = None,
) -> RuleValidation:
    """Validate JSONLogic shape and referenced attributes against the company.

    Empty-AND is the one rule with no referenced attribute. When a roster is
    present, every referenced attribute must contain at least one real value;
    configured enums alone are insufficient evidence that a rule can match.
    """

    errors: list[str] = []
    warnings: list[str] = []
    referenced: set[str] = set()

    def known_value(attribute_id: str, candidate: Any) -> bool:
        allowed = [
            *catalog.values.get(attribute_id, []),
            *catalog.configured_values.get(attribute_id, []),
            *(allowed_values or {}).get(attribute_id, []),
        ]
        needle = str(candidate).strip().casefold()
        return any(str(value).strip().casefold() == needle for value in allowed)

    def validate_literals(attribute_id: str, op: str, args: list[Any]) -> None:
        data_type = catalog.data_types.get(attribute_id, "string").casefold()
        if op in {">=", "<=", ">", "<", "between"}:
            if data_type not in {"integer", "decimal", "float", "number"}:
                errors.append(
                    f"Operator {op} requires a numeric employee attribute; "
                    f"{attribute_id} is {data_type}"
                )
                return
            for value in args[1:]:
                try:
                    if isinstance(value, bool):
                        raise ValueError
                    float(value)
                except (TypeError, ValueError):
                    errors.append(f"Operator {op} requires numeric comparison values")
                    return

        candidates = args[1] if op in {"in", "not_in"} else [args[1]]
        if op not in {"=", "==", "!=", "in", "not_in"}:
            return
        if not isinstance(candidates, list):
            return
        for candidate in candidates:
            if not known_value(attribute_id, candidate):
                errors.append(f"Unknown company value for {attribute_id}: {candidate}")
            elif catalog.roster_present and not any(
                str(value).strip().casefold() == str(candidate).strip().casefold()
                for value in catalog.values.get(attribute_id, [])
            ):
                warnings.append(
                    f"Configured value {candidate} has no active employees in {attribute_id}"
                )

    node_count = 0

    def walk(node: Any, depth: int = 1) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > _MAX_RULE_NODES:
            errors.append(f"Matching rule exceeds {_MAX_RULE_NODES} nodes")
            return
        if depth > _MAX_RULE_DEPTH:
            errors.append(f"Matching rule exceeds {_MAX_RULE_DEPTH} levels")
            return
        if not isinstance(node, dict) or len(node) != 1:
            errors.append("Each matching-rule node must contain exactly one operator")
            return
        op, args = next(iter(node.items()))
        if op in {"and", "or"}:
            if not isinstance(args, list):
                errors.append(f"Operator {op} requires a list of child rules")
                return
            for child in args:
                walk(child, depth + 1)
            return
        if op == "not":
            if not isinstance(args, dict):
                errors.append("Operator not requires one child rule")
                return
            walk(args, depth + 1)
            return
        if op not in _LEAF_OPS:
            errors.append(f"Unsupported matching-rule operator: {op}")
            return
        required_arity = 3 if op == "between" else 2
        if (
            not isinstance(args, list)
            or len(args) != required_arity
            or not isinstance(args[0], str)
            or not args[0].strip()
        ):
            errors.append(f"Operator {op} requires an employee attribute and value")
            return
        if op in {"in", "not_in"} and (not isinstance(args[1], list) or not args[1]):
            errors.append(f"Operator {op} requires a non-empty list of values")
            return
        if op in {"in", "not_in"} and len(args[1]) > _MAX_SET_VALUES:
            errors.append(f"Operator {op} exceeds {_MAX_SET_VALUES} values")
            return
        attribute_id = args[0]
        referenced.add(attribute_id)
        if attribute_id not in catalog.attribute_ids:
            errors.append(f"Unknown employee attribute: {attribute_id}")
            return
        if catalog.roster_present and catalog.populated.get(attribute_id, 0) == 0:
            errors.append(
                f"Employee attribute {attribute_id} has no values in the employee listing"
            )
            return
        validate_literals(attribute_id, op, args)

    if rule is None:
        errors.append("Matching rule is required")
    else:
        walk(rule)

    return RuleValidation(
        valid=not errors,
        errors=list(dict.fromkeys(errors)),
        warnings=warnings,
        referenced_attributes=sorted(referenced),
    )


def _source_allowed_values(
    description: str, catalog: AttributeValueCatalog
) -> dict[str, list[Any]] | None:
    explicit_attr, explicit_values, _ = _explicit_grade_mapping(description, catalog)
    return {explicit_attr: explicit_values} if explicit_attr and explicit_values else None


def _explicit_code_rule_errors(
    rule: Rule | None, attribute_id: str, allowed_values: list[Any]
) -> list[str]:
    """Reject AI branches that widen a source code list or negate its field."""

    allowed = {str(value).strip().casefold() for value in allowed_values}
    errors: list[str] = []

    def walk(node: Any, *, negated: bool = False) -> None:
        if not isinstance(node, dict) or len(node) != 1:
            return
        operator, args = next(iter(node.items()))
        if operator in {"and", "or"} and isinstance(args, list):
            for child in args:
                walk(child, negated=negated)
            return
        if operator == "not":
            walk(args, negated=not negated)
            return
        if not isinstance(args, list) or len(args) < 2 or args[0] != attribute_id:
            return
        if negated or operator not in {"=", "==", "in"}:
            errors.append(f"Explicit {attribute_id} codes require positive membership")
            return
        candidates = args[1] if operator == "in" else [args[1]]
        if not isinstance(candidates, list):
            return
        for candidate in candidates:
            if str(candidate).strip().casefold() not in allowed:
                errors.append(
                    f"Rule includes {attribute_id} value {candidate} "
                    "outside codes stated on the slip"
                )

    walk(rule)
    return list(dict.fromkeys(errors))


def _category_only_for_location_cohort(
    rule: Rule | None,
    description: str,
    catalog: AttributeValueCatalog,
    location_exclusions: dict[str, list[Any]] | None = None,
) -> bool:
    """Allow roster category only for a source location or product exclusion."""
    location_attr, location_value = _location_cohort_label(description, catalog)
    exclusions = location_exclusions or {}

    def walk(node: Any) -> bool:
        if not isinstance(node, dict) or len(node) != 1:
            return False
        op, args = next(iter(node.items()))
        if op in {"and", "or"} and isinstance(args, list):
            return all(walk(child) for child in args)
        if op == "not":
            if isinstance(args, dict) and isinstance(args.get("in"), list):
                child = args["in"]
                if len(child) == 2 and child[0] in {"category", "employee_category"}:
                    values = child[1]
                    return (
                        isinstance(values, list)
                        and bool(values)
                        and set(map(str, values))
                        == set(map(str, exclusions.get(child[0], [])))
                    )
            return not _references_category(args) and walk(args)
        if (
            not isinstance(args, list)
            or len(args) < 2
            or args[0] not in {"category", "employee_category"}
        ):
            return True
        attribute_id = args[0]
        values = args[1] if isinstance(args[1], list) else [args[1]]
        if not values:
            return False
        if op in {"not_in", "!="}:
            return set(map(str, values)) == set(map(str, exclusions.get(attribute_id, [])))
        return (
            op in {"in", "=", "=="}
            and location_attr == attribute_id
            and all(value == location_value for value in values)
        )

    def _references_category(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        return any(
            (isinstance(args, list) and bool(args) and args[0] in {"category", "employee_category"})
            or (
                isinstance(args, list)
                and any(_references_category(child) for child in args)
            )
            or (isinstance(args, dict) and _references_category(args))
            for args in node.values()
        )

    return walk(rule)


def _explicit_rule_stays_within_source(
    rule: Rule | None,
    description: str,
    catalog: AttributeValueCatalog,
    attribute_id: str,
    allowed_values: list[Any],
) -> bool:
    """Every satisfying branch must require a slip code or an explicit union."""
    allowed = {str(value).strip().casefold() for value in allowed_values}
    proposal = propose_category_rule(description, catalog)
    exceptions: list[Rule] = []
    nationality_review_only = all(
        "mapped through nationality; confirm nationality represents work base" in clause
        for clause in proposal.unresolved_clauses
    )
    if nationality_review_only and isinstance(proposal.rule, dict):
        children = proposal.rule.get("or")
        if isinstance(children, list):
            exceptions = [child for child in children if isinstance(child, dict)]

    def requires_code(node: Any) -> bool:
        if not isinstance(node, dict) or len(node) != 1:
            return False
        operator, args = next(iter(node.items()))
        if operator == "and" and isinstance(args, list):
            return any(requires_code(child) for child in args)
        if operator == "or" and isinstance(args, list):
            return bool(args) and all(requires_code(child) for child in args)
        if operator not in {"=", "==", "in"} or not isinstance(args, list) or len(args) != 2:
            return False
        if args[0] != attribute_id:
            return False
        values = args[1] if operator == "in" else [args[1]]
        return bool(values) and all(str(value).strip().casefold() in allowed for value in values)

    def covered(node: Any) -> bool:
        if requires_code(node) or node in exceptions:
            return True
        if not isinstance(node, dict) or len(node) != 1:
            return False
        operator, args = next(iter(node.items()))
        if operator == "and" and isinstance(args, list):
            return any(covered(child) for child in args)
        if operator == "or" and isinstance(args, list):
            return bool(args) and all(covered(child) for child in args)
        return False

    return covered(rule)


def validate_ai_matching_rule(
    description: str,
    rule: Rule | None,
    catalog: AttributeValueCatalog,
    *,
    location_exclusions: dict[str, list[Any]] | None = None,
) -> RuleValidation:
    """Apply structural/company validation plus AI-specific semantic guards."""

    source_values = _source_allowed_values(description, catalog)
    validation = validate_matching_rule(
        rule,
        catalog,
        allowed_values=source_values,
    )
    errors = list(validation.errors)
    text = _intent_text(description)
    for clause in _oversized_explicit_grade_clauses(text):
        errors.append(
            f"Explicit employee range exceeds {_MAX_SET_VALUES} values: {clause}"
        )
    universal = re.sub(r"\s*\(\s*(?:incl\.?|including)\s+[^)]*\)\s*$", "", text, flags=re.I)
    if rule == {"and": []} and not (
        _ALL_EMPLOYEES_RE.fullmatch(universal) or _ALL_OTHER_RE.fullmatch(universal)
    ):
        errors.append("AI may use an all-employees rule only when the eligibility wording says so")
    if has_explicit_grade_clause(text) and not source_values:
        errors.append("Could not map explicit employee codes to a company employee field")
    if source_values:
        source_attribute = next(iter(source_values))
        if source_attribute not in validation.referenced_attributes:
            clauses = explicit_grade_clauses(text)
            for alternate in ("job_category", "job_grade", "grade"):
                if (
                    alternate not in validation.referenced_attributes
                    or alternate == source_attribute
                ):
                    continue
                alternate_values = list(dict.fromkeys(
                    value
                    for clause in clauses
                    for value in _values_from_grade_clause(
                        clause, catalog.values.get(alternate, [])
                    )
                ))
                if {
                    str(value).casefold() for value in alternate_values
                } == {
                    str(value).casefold() for value in source_values[source_attribute]
                }:
                    source_attribute = alternate
                    source_values = {alternate: alternate_values}
                    break
        if source_attribute not in validation.referenced_attributes:
            errors.append(f"Matching rule omitted explicit employee attribute: {source_attribute}")
        errors.extend(
            _explicit_code_rule_errors(rule, source_attribute, source_values[source_attribute])
        )
        if not _explicit_rule_stays_within_source(
            rule, description, catalog, source_attribute, source_values[source_attribute]
        ):
            errors.append("Rule can cover employees outside the slip's explicit codes")
        if (
            source_attribute not in {"category", "employee_category"}
            and {"category", "employee_category"} & set(validation.referenced_attributes)
            and not _category_only_for_location_cohort(
                rule, description, catalog, location_exclusions
            )
        ):
            errors.append(
                "AI rule may not use roster category text when the slip specifies employee codes"
            )
    return RuleValidation(
        valid=not errors,
        errors=list(dict.fromkeys(errors)),
        warnings=validation.warnings,
        referenced_attributes=validation.referenced_attributes,
    )


def normalize_ai_matching_rule(
    description: str, rule: Rule | None, catalog: AttributeValueCatalog
) -> tuple[Rule | None, list[str]]:
    """Apply deterministic source facts that the AI may omit or reorder."""

    attribute_id, source_values, _ = _explicit_grade_mapping(description, catalog)
    if rule is None:
        return rule, []

    def normalize(node: Any) -> Any:
        if not isinstance(node, dict) or len(node) != 1:
            return node
        operator, args = next(iter(node.items()))
        if operator in {"and", "or"} and isinstance(args, list):
            return {operator: [normalize(child) for child in args]}
        if operator == "not" and isinstance(args, dict):
            return {operator: normalize(args)}
        if (
            operator in {"=", "==", "in"}
            and isinstance(args, list)
            and len(args) == 2
            and args[0] == attribute_id
        ):
            if len(source_values) == 1:
                return {"=": [attribute_id, source_values[0]]}
            return {"in": [attribute_id, list(source_values)]}
        return node

    normalized = normalize(rule) if attribute_id and source_values else rule

    text = _intent_text(description)
    location_match = _BASED_IN_RE.search(text)
    review_clauses: list[str] = []
    if location_match:
        location_attr, location_values, nationality_proxy = _location_value_mapping(
            location_match.group(1), catalog
        )
        if location_attr and location_values:
            if location_attr in _NATIONALITY_ATTRS:
                expected_countries = set(
                    _exact_location_countries(location_match.group(1)) or []
                )

                def normalize_nationality_literal(node: Any) -> Any:
                    if not isinstance(node, dict) or len(node) != 1:
                        return node
                    operator, args = next(iter(node.items()))
                    if operator in {"and", "or"} and isinstance(args, list):
                        return {
                            operator: [normalize_nationality_literal(child) for child in args]
                        }
                    if operator == "not" and isinstance(args, dict):
                        return {operator: normalize_nationality_literal(args)}
                    if (
                        operator in {"=", "==", "in"}
                        and isinstance(args, list)
                        and len(args) == 2
                        and args[0] == location_attr
                    ):
                        raw_values = args[1] if isinstance(args[1], list) else [args[1]]
                        countries = {
                            nationality_country_exact(value) for value in raw_values
                        }
                        if expected_countries and countries == expected_countries:
                            return _rule_for_values(location_attr, location_values)
                    return node

                normalized = normalize_nationality_literal(normalized)

            source_allowed = _source_allowed_values(description, catalog)
            current = validate_matching_rule(normalized, catalog, allowed_values=source_allowed)
            if location_attr not in current.referenced_attributes:
                location_rule: Rule | None = _rule_for_values(location_attr, location_values)
                if exclusion := _EXCLUSION_RE.search(text):
                    exclusion_attr, exclusion_values = _best_value_mapping(
                        exclusion.group(1).strip(), catalog
                    )
                    if exclusion_attr and exclusion_values:
                        location_rule = {
                            "and": [
                                location_rule,
                                _rule_for_values(
                                    exclusion_attr,
                                    exclusion_values,
                                    negate=True,
                                ),
                            ]
                        }
                    else:
                        location_rule = None
                if location_rule is not None:
                    operator = (
                        "or"
                        if re.search(r"\band\s+all\s+employees?\s+based\s+in\b", text, re.I)
                        else "and"
                    )
                    existing_children = (
                        list(normalized[operator])
                        if isinstance(normalized, dict)
                        and list(normalized) == [operator]
                        and isinstance(normalized[operator], list)
                        else [normalized]
                    )
                    normalized = {operator: [*existing_children, location_rule]}

            final_validation = validate_matching_rule(
                normalized,
                catalog,
                allowed_values=source_allowed,
            )
            if nationality_proxy and location_attr in final_validation.referenced_attributes:
                review_clauses.append(
                    f"{location_match.group(0).strip()} mapped through nationality; "
                    "confirm nationality represents work base"
                )
    return normalized, review_clauses
