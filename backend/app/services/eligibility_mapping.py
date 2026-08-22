"""Company-aware eligibility intent extraction and rule validation.

The legacy :mod:`rule_generator` translates one description in isolation using
global regexes.  This module deliberately takes the opposite approach: it maps
the slip's words onto the *current company's* populated employee attributes and
keeps unresolved intent explicit instead of silently dropping clauses.

The database orchestration lives further down in this module; the pure proposal
and validation functions at the top are intentionally easy to exercise as
golden evals.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import (
    Category,
    EligibilityMappingProfile,
    Employee,
    EmployeeAttributeSchema,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.schemas.api import AttributeSchemaOut
from app.services.derivation_engine import derive, resolve_attribute_schemas
from app.services.matching_engine import (
    _entity_allows,
    category_insured_entities,
    employee_entity,
    entity_alias_map,
    product_entities,
    rule_specificity,
)
from app.services.rule_evaluator import evaluate

Rule = dict[str, Any]

_PLAN_PREFIX_RE = re.compile(r"^\s*plan\s+[a-z0-9/]+\s*(?:-|:|\u2013|\u2014)\s*", re.IGNORECASE)
_DEPENDANT_TAIL_RE = re.compile(
    r"\s+(?:and|&)\s+(?:their|the)\s+(?:eligible\s+)?"
    r"dependan[td]s?.*$",
    re.IGNORECASE,
)
_OPTION_TAIL_RE = re.compile(r"\s*\(option\s+\d+\)\s*$", re.IGNORECASE)
_EXCLUSION_RE = re.compile(r"\b(?:excluding|except(?:\s+for)?)\s+([^)]*)(?:\)|$)", re.IGNORECASE)
_ALL_EMPLOYEES_RE = re.compile(r"^all\s+(?:employees?|staff|members?)$", re.IGNORECASE)
_ALL_OTHER_RE = re.compile(r"^all\s+other\s+(?:employees?|staff|members?)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TOKEN_ALIASES = {
    "asst": "assistant",
    "dept": "department",
    "mgr": "manager",
}

_ATTR_PRIORITY = (
    "designation",
    "employee_category",
    "employment_type",
    "employee_type",
    "job_title",
    "category",
    "role",
    "job_grade",
    "job_category",
    "class",
    "occupation",
    "job_function",
)
_PASS_ATTRS = ("pass", "pass_type", "employment_pass", "work_pass")
_LEAF_OPS = frozenset({"=", "==", "!=", ">=", "<=", ">", "<", "between", "in", "not_in"})
_MAX_RULE_DEPTH = 12
_MAX_RULE_NODES = 100
_MAX_SET_VALUES = 200


@dataclass(frozen=True)
class AttributeValueCatalog:
    """Non-PII employee attribute vocabulary available to the compiler.

    ``values`` contains actual distinct roster values when a roster exists, or
    configured enum values when it does not. ``populated`` always represents
    real roster fill counts, never enum cardinality.
    """

    values: dict[str, list[Any]]
    data_types: dict[str, str]
    populated: dict[str, int]
    employee_count: int
    roster_present: bool = True
    # Configured enums remain valid even when the current roster has no member
    # in that band yet (for example, a future M1 hire). Keeping them separate
    # from observed values lets validation distinguish "allowed but unused"
    # from an AI-invented literal.
    configured_values: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def attribute_ids(self) -> set[str]:
        return set(self.data_types) | set(self.values) | set(self.populated)


@dataclass(frozen=True)
class RuleProposal:
    rule: Rule | None
    human_readable: str
    confidence: float
    source: str
    validation_state: str
    unresolved_clauses: list[str] = field(default_factory=list)
    referenced_attributes: list[str] = field(default_factory=list)
    relative_remainder: bool = False


@dataclass(frozen=True)
class RuleValidation:
    valid: bool
    errors: list[str]
    warnings: list[str]
    referenced_attributes: list[str]


@dataclass(frozen=True)
class MappingItem:
    category_id: str
    product_code: str | None
    display_name: str
    plan_code: str | None
    category_status: str
    rule_status: str
    source: str
    matching_rule: Rule | None
    rule_human_readable: str | None
    confidence: float | None
    matched_count: int | None
    expected_count: int | None
    unresolved_clauses: list[str]
    errors: list[str]
    warnings: list[str]
    reused: bool


@dataclass(frozen=True)
class MissingCategoryPlan:
    plan_id: str
    product_id: str
    product_code: str
    product_display_name: str
    plan_code: str
    plan_display_name: str
    source_hint: str | None


@dataclass(frozen=True)
class CategoryRuleAssessment:
    valid: bool
    rule_status: str
    validation: dict[str, Any]


@dataclass(frozen=True)
class MappingSummary:
    policy_year_id: str
    employee_count: int
    total: int
    validated: int
    proposed: int
    needs_review: int
    unmapped: int
    reused: int
    categories: list[MappingItem]
    missing_categories: int
    missing_category_plans: list[MissingCategoryPlan]


def _intent_text(description: str) -> str:
    text = _PLAN_PREFIX_RE.sub("", (description or "").strip())
    text = _OPTION_TAIL_RE.sub("", text).strip()
    return _DEPENDANT_TAIL_RE.sub("", text).strip()


def _singular(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ers") and len(token) > 5:
        return token[:-1]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _tokens(text: Any) -> list[str]:
    return [
        _TOKEN_ALIASES.get(token, token)
        for raw in _TOKEN_RE.findall(str(text or "").lower())
        if (token := _singular(raw))
    ]


def category_signature(description: str) -> str:
    """Stable, company-local identity for reusing a confirmed mapping.

    Plan numbers and dependant boilerplate are assignment details rather than
    employee-cohort meaning, so they are removed before normalization.
    """

    return " ".join(_tokens(_intent_text(description)))


def _sequence_spans(haystack: list[str], needle: list[str]) -> list[tuple[int, int]]:
    if not needle or len(needle) > len(haystack):
        return []
    width = len(needle)
    return [
        (i, i + width)
        for i in range(len(haystack) - width + 1)
        if haystack[i : i + width] == needle
    ]


def _matched_values(text: str, values: list[Any]) -> list[Any]:
    """Return roster values explicitly named in ``text``.

    Longest non-overlapping phrase matching handles both sides of the MCIL
    edge case: ``Senior Vice President / Vice President`` selects both values,
    while ``NON-MANUAL`` does not also select the nested ``MANUAL`` value.
    """

    haystack = _tokens(text)
    candidates: list[tuple[int, int, int, Any]] = []
    for order, raw in enumerate(values):
        needle = _tokens(raw)
        if not needle or needle in (["employee"], ["staff"], ["member"], ["other"]):
            continue
        for lo, hi in _sequence_spans(haystack, needle):
            candidates.append((-(hi - lo), lo, order, raw))

    occupied: set[int] = set()
    selected: dict[int, Any] = {}
    for neg_width, lo, order, raw in sorted(candidates):
        width = -neg_width
        span = set(range(lo, lo + width))
        if span & occupied:
            continue
        occupied |= span
        selected.setdefault(order, raw)
    return [selected[i] for i in sorted(selected)]


def _attribute_rank(attribute_id: str) -> tuple[int, str]:
    try:
        return (_ATTR_PRIORITY.index(attribute_id), attribute_id)
    except ValueError:
        return (len(_ATTR_PRIORITY), attribute_id)


def _best_value_mapping(
    text: str,
    catalog: AttributeValueCatalog,
    *,
    allowed: tuple[str, ...] | None = None,
) -> tuple[str | None, list[Any]]:
    candidates: list[tuple[int, tuple[int, str], str, list[Any]]] = []
    for attribute_id, values in catalog.values.items():
        if allowed is not None and attribute_id not in allowed:
            continue
        if catalog.data_types.get(attribute_id) not in {None, "string", "enum"}:
            continue
        matched = _matched_values(text, values)
        if matched:
            candidates.append((-len(matched), _attribute_rank(attribute_id), attribute_id, matched))
    if not candidates:
        return None, []
    _, _, attribute_id, matched = min(candidates)
    return attribute_id, matched


def _unresolved_list_clauses(text: str, included: list[Any]) -> list[str]:
    """Keep slash/semicolon cohorts that did not map to a company value.

    A partially mapped list is useful, but silently discarding one title would
    make it look complete. Free-form prose is not split here because a value
    may legitimately be embedded in a sentence; this guard targets the clear
    list grammar used by placement slips such as MCIL's plan bands.
    """

    if "/" not in text and ";" not in text:
        return []
    unresolved: list[str] = []
    for raw_clause in re.split(r"\s*[/;]\s*", text):
        clause = raw_clause.strip(" (),")
        clause_tokens = _tokens(clause)
        if not clause_tokens:
            continue
        if any(_sequence_spans(clause_tokens, _tokens(value)) for value in included):
            continue
        unresolved.append(clause)
    return unresolved


def _rule_for_values(attribute_id: str, values: list[Any], *, negate: bool = False) -> Rule:
    if len(values) == 1 and not negate:
        return {"=": [attribute_id, values[0]]}
    return {"not_in" if negate else "in": [attribute_id, values]}


def _pass_codes(text: str) -> list[str]:
    codes: list[str] = []
    normalized = " ".join(_tokens(text))
    if re.search(r"\bs\s*pass\b|\bspass\b|\bsp\b", normalized):
        codes.append("SP")
    if re.search(r"\bwork\s+permit\b|\bwp\b", normalized):
        codes.append("WP")
    if re.search(r"\bemployment\s+pass\b|\bep\b", normalized):
        codes.append("EP")
    return codes


def _actual_pass_values(codes: list[str], values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for code in codes:
        code_tokens = _tokens(code)
        hit = next(
            (
                value
                for value in values
                if _tokens(value) == code_tokens
                or (code == "SP" and _tokens(value) in (["s", "pass"], ["spass"]))
                or (code == "WP" and _tokens(value) == ["work", "permit"])
                or (code == "EP" and _tokens(value) == ["employment", "pass"])
            ),
            None,
        )
        if hit is not None:
            out.append(hit)
    return out


def propose_category_rule(description: str, catalog: AttributeValueCatalog) -> RuleProposal:
    """Propose a rule using the company's own non-PII roster vocabulary.

    This function never calls AI and never marks a rule confirmed. It returns a
    useful partial proposal with explicit ``unresolved_clauses`` when the slip
    expresses an open hierarchy (``and above``) or a clause has no roster-backed
    attribute/value mapping.
    """

    text = _intent_text(description)
    without_exclusion = _EXCLUSION_RE.sub("", text).strip(" ()")
    relative_remainder = bool(_ALL_OTHER_RE.match(without_exclusion))

    if _ALL_EMPLOYEES_RE.match(without_exclusion):
        return RuleProposal(
            rule={"and": []},
            human_readable="All employees",
            confidence=0.95,
            source="deterministic",
            validation_state="proposed",
        )

    exclusion_text = ""
    if exclusion := _EXCLUSION_RE.search(text):
        exclusion_text = exclusion.group(1).strip()

    # Relative cohorts are compiled after their specific siblings. The matching
    # engine's specificity ordering makes an empty-AND the safe remainder; a
    # stated exclusion is retained when the company vocabulary can express it.
    if relative_remainder:
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
            rule={"and": []},
            human_readable="All remaining employees; exclusion needs mapping",
            confidence=0.5,
            source="product_context",
            validation_state="needs_review",
            unresolved_clauses=[exclusion_text],
            relative_remainder=True,
        )

    pass_codes = _pass_codes(without_exclusion)
    if pass_codes:
        pass_attr = next(
            (
                attr
                for attr in _PASS_ATTRS
                if attr in catalog.attribute_ids and catalog.values.get(attr)
            ),
            None,
        )
        if pass_attr:
            pass_values = _actual_pass_values(pass_codes, catalog.values[pass_attr])
            if len(pass_values) == len(pass_codes):
                rule = _rule_for_values(pass_attr, pass_values)
                return RuleProposal(
                    rule=rule,
                    human_readable=(f"{pass_attr} is one of {', '.join(map(str, pass_values))}"),
                    confidence=0.95,
                    source="roster_values",
                    validation_state="proposed",
                    referenced_attributes=[pass_attr],
                )

    attr, included = _best_value_mapping(without_exclusion, catalog)
    if attr and included:
        rule = _rule_for_values(attr, included)
        unresolved = _unresolved_list_clauses(without_exclusion, included)
        if re.search(r"(?:\band\b|&)\s+above\b", without_exclusion, re.IGNORECASE):
            unresolved.append("above")
        if exclusion_text:
            ex_attr, excluded = _best_value_mapping(exclusion_text, catalog)
            if ex_attr == attr and excluded:
                rule = {"and": [rule, _rule_for_values(attr, excluded, negate=True)]}
            else:
                unresolved.append(exclusion_text)
        return RuleProposal(
            rule=rule,
            human_readable=f"{attr} is one of {', '.join(map(str, included))}",
            confidence=0.7 if unresolved else 0.9,
            source="roster_values",
            validation_state="needs_review" if unresolved else "proposed",
            unresolved_clauses=unresolved,
            referenced_attributes=[attr],
        )

    return RuleProposal(
        rule=None,
        human_readable="Unmapped — company roster field/value mapping required",
        confidence=0.2,
        source="unmapped",
        validation_state="unmapped",
        unresolved_clauses=[text] if text else ["empty description"],
    )


def validate_matching_rule(rule: Rule | None, catalog: AttributeValueCatalog) -> RuleValidation:
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
            elif (
                catalog.roster_present
                and not any(
                    str(value).strip().casefold()
                    == str(candidate).strip().casefold()
                    for value in catalog.values.get(attribute_id, [])
                )
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
        if op in {"in", "not_in"} and (
            not isinstance(args[1], list) or not args[1]
        ):
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
            errors.append(f"Employee attribute {attribute_id} has no populated roster values")
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


def validate_ai_matching_rule(
    description: str, rule: Rule | None, catalog: AttributeValueCatalog
) -> RuleValidation:
    """Apply structural/company validation plus AI-specific semantic guards."""

    validation = validate_matching_rule(rule, catalog)
    errors = list(validation.errors)
    text = _intent_text(description)
    if rule == {"and": []} and not (
        _ALL_EMPLOYEES_RE.match(text) or _ALL_OTHER_RE.match(text)
    ):
        errors.append(
            "AI may use an all-employees rule only when the eligibility wording says so"
        )
    return RuleValidation(
        valid=not errors,
        errors=list(dict.fromkeys(errors)),
        warnings=validation.warnings,
        referenced_attributes=validation.referenced_attributes,
    )


def build_attribute_catalog(
    db: Session, policy_year_id: str, client_id: str
) -> tuple[AttributeValueCatalog, list[Employee], list[dict[str, Any]]]:
    """Build a non-PII, tenant-resolved vocabulary and employee eval views."""

    schemas = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    safe_schemas = [schema for schema in schemas if not schema.is_pii]
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
                Employee.status == EMPLOYEE_STATUS_ACTIVE,
            )
        ).scalars()
    )
    views = [
        {
            **(employee.attribute_values or {}),
            **derive(employee.attribute_values or {}, safe_schemas),
        }
        for employee in employees
    ]

    values: dict[str, list[Any]] = {}
    configured_values: dict[str, list[Any]] = {}
    populated: dict[str, int] = {}
    data_types: dict[str, str] = {}
    for schema in safe_schemas:
        attribute_id = schema.attribute_id
        data_types[attribute_id] = schema.data_type
        configured_values[attribute_id] = list(schema.enum_values or [])
        distinct: list[Any] = []
        seen: set[str] = set()
        count = 0
        for view in views:
            value = view.get(attribute_id)
            if value in (None, ""):
                continue
            count += 1
            key = str(value).strip().casefold()
            if key not in seen:
                seen.add(key)
                distinct.append(value)
        populated[attribute_id] = count
        # With no/currently-empty roster field, configured enum values still let
        # us compile a proposal, but ``populated`` remains zero so validation
        # does not confuse catalog vocabulary with current matching evidence.
        if not distinct and schema.enum_values:
            distinct = list(schema.enum_values)
        values[attribute_id] = distinct

    return (
        AttributeValueCatalog(
            values=values,
            data_types=data_types,
            populated=populated,
            employee_count=len(employees),
            roster_present=bool(employees),
            configured_values=configured_values,
        ),
        employees,
        views,
    )


_AI_CONTEXT_MAX_VALUES = 40
_AI_CONTEXT_MAX_DISTINCT = 100
_AI_CONTEXT_MAX_SIBLINGS = 25


def _prompt_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:128]


def build_ai_eligibility_inputs(
    db: Session,
    *,
    category: Category,
    client_id: str,
    plan: Plan | None = None,
) -> tuple[list[AttributeSchemaOut], dict[str, Any], AttributeValueCatalog]:
    """Build bounded, non-PII context for one AI eligibility request.

    Employee rows, names, staff IDs, and high-cardinality/free-text values are
    deliberately never included. The model sees only resolved schemas plus a
    small company vocabulary that the deterministic validator will enforce
    again after generation.
    """

    catalog, _, _ = build_attribute_catalog(db, category.policy_year_id, client_id)
    resolved = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    safe_schemas = sorted(
        (schema for schema in resolved if not schema.is_pii),
        key=lambda value: value.attribute_id,
    )
    usable_schemas = [
        schema
        for schema in safe_schemas
        if catalog.values.get(schema.attribute_id)
        or catalog.configured_values.get(schema.attribute_id)
    ][:64]
    schema_out = [
        AttributeSchemaOut.model_validate(schema).model_copy(
            update={"enum_values": list(schema.enum_values or [])[:100] or None}
        )
        for schema in usable_schemas
    ]

    product = (
        db.execute(
            select(Product).where(
                Product.id == category.product_id,
                tenant_or_global(Product.client_id, client_id),
            )
        ).scalar_one_or_none()
        if category.product_id
        else None
    )
    pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
    plan_code = str(pa.get("plan_code") or "").strip()
    if plan is None and product is not None and plan_code:
        plan = db.execute(
            select(Plan).where(
                Plan.policy_year_id == category.policy_year_id,
                Plan.product_id == product.id,
                Plan.code == plan_code,
            )
        ).scalar_one_or_none()

    sibling_stmt = select(Category).where(
        Category.policy_year_id == category.policy_year_id,
        Category.product_id == category.product_id,
    )
    if category.id:
        sibling_stmt = sibling_stmt.where(Category.id != category.id)
    siblings = list(
        db.execute(sibling_stmt.order_by(Category.priority).limit(_AI_CONTEXT_MAX_SIBLINGS))
        .scalars()
    )

    employee_attributes: list[dict[str, Any]] = []
    for schema in sorted(usable_schemas, key=lambda value: value.attribute_id):
        attribute_id = schema.attribute_id
        populated = catalog.populated.get(attribute_id, 0)
        observed = catalog.values.get(attribute_id, []) if populated else []
        include_values = len(observed) <= _AI_CONTEXT_MAX_DISTINCT
        employee_attributes.append(
            {
                "attribute_id": attribute_id,
                "data_type": schema.data_type,
                "populated_employee_count": populated,
                "observed_distinct_count": len(observed),
                "observed_values": (
                    [_prompt_scalar(value) for value in observed[:_AI_CONTEXT_MAX_VALUES]]
                    if include_values
                    else []
                ),
                "observed_values_withheld": not include_values,
                "configured_values": [
                    _prompt_scalar(value)
                    for value in catalog.configured_values.get(attribute_id, [])[:100]
                ],
            }
        )

    deterministic = propose_category_rule(category.raw_description, catalog)
    context: dict[str, Any] = {
        "product": (
            {"code": product.code, "display_name": product.display_name}
            if product is not None
            else None
        ),
        "plan": (
            {
                "code": plan.code,
                "display_name": plan.display_name,
                "cover_description": plan.cover_description,
            }
            if plan is not None
            else ({"code": plan_code} if plan_code else None)
        ),
        "target": {
                "display_name": category.display_name[:512],
            "plan_code": plan_code or None,
            "expected_count": pa.get("num_employees"),
        },
        "sibling_categories": [
            {
                "display_name": sibling.display_name[:256],
                "raw_description": sibling.raw_description[:512],
                "plan_code": str(
                    (sibling.plan_assignments or {}).get("plan_code") or ""
                )
                or None,
                "current_reading": (
                    sibling.rule_human_readable[:512]
                    if sibling.rule_human_readable
                    else None
                ),
            }
            for sibling in siblings
        ],
        "employee_attributes": employee_attributes,
        "deterministic_candidate": {
            "rule": deterministic.rule,
            "human_readable": deterministic.human_readable,
            "unresolved_clauses": deterministic.unresolved_clauses,
        },
        "roster_employee_count": catalog.employee_count,
    }
    return schema_out, context, catalog


def missing_category_plans(
    db: Session, *, policy_year_id: str
) -> list[MissingCategoryPlan]:
    """Return materialized plans that no employee category assigns."""

    categories = list(
        db.execute(
            select(Category).where(Category.policy_year_id == policy_year_id)
        ).scalars()
    )
    assigned = {
        (
            category.product_id,
            str((category.plan_assignments or {}).get("plan_code") or "")
            .strip()
            .casefold(),
        )
        for category in categories
        if category.product_id and isinstance(category.plan_assignments, dict)
    }
    plans = list(
        db.execute(
            select(Plan)
            .where(Plan.policy_year_id == policy_year_id)
            .order_by(Plan.product_id, Plan.code)
        ).scalars()
    )
    client_id = db.scalar(
        select(PolicyYear.client_id).where(PolicyYear.id == policy_year_id)
    )
    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({plan.product_id for plan in plans}),
                tenant_or_global(Product.client_id, client_id),
            )
        ).scalars()
    }
    missing: list[MissingCategoryPlan] = []
    for plan in plans:
        if (plan.product_id, plan.code.strip().casefold()) in assigned:
            continue
        product = products.get(plan.product_id)
        if product is None:
            continue
        missing.append(
            MissingCategoryPlan(
                plan_id=plan.id,
                product_id=plan.product_id,
                product_code=product.code,
                product_display_name=product.display_name,
                plan_code=plan.code,
                plan_display_name=plan.display_name,
                source_hint=plan.cover_description,
            )
        )
    return missing


def _profile_proposal(profile: EligibilityMappingProfile) -> RuleProposal:
    validation = profile.validation if isinstance(profile.validation, dict) else {}
    unresolved = validation.get("unresolved_clauses")
    return RuleProposal(
        rule=profile.matching_rule,
        human_readable=profile.rule_human_readable or profile.display_name,
        confidence=float(profile.confidence or 0.85),
        source="prior_mapping",
        validation_state="proposed",
        unresolved_clauses=(
            [str(value) for value in unresolved] if isinstance(unresolved, list) else []
        ),
        referenced_attributes=list(profile.required_attributes or []),
        relative_remainder=bool(validation.get("relative_remainder")),
    )


def _previous_confirmed_rules(
    db: Session, policy_year_id: str, client_id: str
) -> dict[str, Category]:
    rows = list(
        db.execute(
            select(Category)
            .join(PolicyYear, PolicyYear.id == Category.policy_year_id)
            .where(
                PolicyYear.client_id == client_id,
                Category.policy_year_id != policy_year_id,
                Category.status == CategoryStatus.confirmed.value,
                Category.matching_rule.is_not(None),
            )
            .order_by(PolicyYear.year.desc(), Category.updated_at.desc())
        ).scalars()
    )
    out: dict[str, Category] = {}
    for category in rows:
        out.setdefault(category_signature(category.raw_description), category)
    return out


def _upsert_profile(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str,
    category: Category,
    proposal: RuleProposal,
    status: str,
    validation: dict[str, Any],
) -> EligibilityMappingProfile:
    signature = category_signature(category.raw_description)
    profile = db.execute(
        select(EligibilityMappingProfile).where(
            EligibilityMappingProfile.client_id == client_id,
            EligibilityMappingProfile.category_signature == signature,
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = EligibilityMappingProfile(
            client_id=client_id,
            category_signature=signature,
            display_name=category.display_name,
        )
        db.add(profile)
        db.flush()
    # A fresh machine proposal cannot overwrite a broker-confirmed reusable
    # profile. Confirm flow passes ``status=confirmed`` and may update it.
    if profile.status != "confirmed" or status == "confirmed":
        profile.display_name = category.display_name
        profile.matching_rule = proposal.rule
        profile.rule_human_readable = proposal.human_readable
        profile.required_attributes = proposal.referenced_attributes
        profile.validation = validation
        profile.source = proposal.source
        profile.confidence = proposal.confidence
        profile.status = status
        profile.last_policy_year_id = policy_year_id
    return profile


def _candidate_for_category(
    category: Category,
    catalog: AttributeValueCatalog,
    profiles: dict[str, EligibilityMappingProfile],
    previous: dict[str, Category],
) -> tuple[RuleProposal, bool]:
    signature = category_signature(category.raw_description)
    if profile := profiles.get(signature):
        if profile.status == "confirmed" and profile.matching_rule:
            return _profile_proposal(profile), True
    if prior := previous.get(signature):
        return (
            RuleProposal(
                rule=prior.matching_rule,
                human_readable=prior.rule_human_readable or prior.display_name,
                confidence=float(prior.confidence or 0.85),
                source="prior_year",
                validation_state="proposed",
                referenced_attributes=validate_matching_rule(
                    prior.matching_rule, catalog
                ).referenced_attributes,
            ),
            True,
        )
    return propose_category_rule(category.raw_description, catalog), False


def _assignment_counts(
    *,
    db: Session,
    client_id: str,
    categories: list[Category],
    employees: list[Employee],
    views: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    """Assign each employee with the live matcher's rule precedence per product.

    This intentionally evaluates rules only. Exact/fuzzy name matching can make
    runtime matching more forgiving, but it must not make a broken rule look
    validated in the configuration workbench.
    """

    by_product: dict[str | None, list[Category]] = defaultdict(list)
    for category in categories:
        by_product[category.product_id].append(category)

    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories if c.product_id})
            )
        ).scalars()
    }
    aliases = entity_alias_map(db, client_id)
    counts: dict[str, int] = defaultdict(int)
    overlaps: dict[str, int] = defaultdict(int)

    def rank(category: Category) -> tuple[int, int]:
        status_rank = (
            0
            if category.status == CategoryStatus.confirmed.value
            else 1
            if category.status == CategoryStatus.needs_review.value
            else 2
        )
        return status_rank, -rule_specificity(category.matching_rule)

    for product_id, product_categories in by_product.items():
        product = products.get(product_id) if product_id else None
        product_gate = product_entities(product, aliases)
        gates = {
            category.id: product_gate or category_insured_entities(category, aliases)
            for category in product_categories
        }
        for employee, view in zip(employees, views, strict=True):
            employee_gate = employee_entity(employee.attribute_values, aliases)
            matches = [
                category
                for category in product_categories
                if category.matching_rule
                and _entity_allows(gates[category.id], employee_gate)
                and evaluate(category.matching_rule, view)
            ]
            if not matches:
                continue
            # Mirror matching_engine.match_one exactly: a broker-confirmed rule
            # wins first, then specificity, then priority. The workbench count
            # must describe the assignment the live matcher will actually save.
            best_rank = min(rank(category) for category in matches)
            best = [category for category in matches if rank(category) == best_rank]
            if len(best) > 1:
                for category in best:
                    overlaps[category.id] += 1
            winner = min(best, key=lambda category: category.priority)
            counts[winner.id] += 1
    return counts, overlaps


def assess_category_rule(
    db: Session,
    *,
    category: Category,
    client_id: str,
    unresolved_clauses: list[str] | None = None,
    source: str,
) -> CategoryRuleAssessment:
    """Validate one proposed rule and measure its real roster outcome.

    This is the save gate for AI output. It uses the same evaluation and
    precedence as the live matcher, so a syntactically plausible rule cannot be
    presented as ready when it references invented values, matches nobody, or
    conflicts with an equally-specific sibling.
    """

    catalog, employees, views = build_attribute_catalog(
        db, category.policy_year_id, client_id
    )
    validation = validate_matching_rule(category.matching_rule, catalog)
    categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == category.policy_year_id)
            .order_by(Category.product_id, Category.priority)
        ).scalars()
    )
    if category not in categories:
        categories.append(category)
    counts, overlaps = _assignment_counts(
        db=db,
        client_id=client_id,
        categories=categories,
        employees=employees,
        views=views,
    )

    pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
    expected_raw = pa.get("num_employees")
    expected = int(expected_raw) if isinstance(expected_raw, (int, float)) else None
    matched = counts.get(category.id, 0) if catalog.roster_present else None
    warnings = list(validation.warnings)
    unresolved = [str(value)[:512] for value in (unresolved_clauses or [])][:20]
    if not catalog.roster_present and category.matching_rule is not None:
        warnings.append("No active roster is available to validate matched employees")
    if overlaps.get(category.id):
        warnings.append(
            f"{overlaps[category.id]} employees also match an equally specific sibling rule"
        )
    if expected is not None and matched is not None and expected != matched:
        warnings.append(f"Matched {matched} employees; placement slip states {expected}")
    if matched == 0 and catalog.roster_present and category.matching_rule is not None:
        warnings.append("Rule matches no active employees")
    warnings.extend(f"Unresolved clause: {clause}" for clause in unresolved)
    warnings = list(dict.fromkeys(warnings))

    if category.matching_rule is None:
        rule_status = "unmapped"
    elif validation.errors or warnings:
        rule_status = "needs_review"
    elif catalog.roster_present:
        rule_status = "validated"
    else:
        rule_status = "proposed"
    payload = {
        "state": rule_status,
        "source": source,
        "errors": validation.errors,
        "warnings": warnings,
        "unresolved_clauses": unresolved,
        "required_attributes": validation.referenced_attributes,
        "matched_count": matched,
        "expected_count": expected,
        "reused": False,
    }
    return CategoryRuleAssessment(
        valid=validation.valid,
        rule_status=rule_status,
        validation=payload,
    )


def auto_map_policy_year(
    db: Session,
    *,
    policy_year_id: str,
    client_id: str,
    persist_profiles: bool = True,
) -> MappingSummary:
    """Generate and persist company-aware proposals for one policy year.

    Existing human-authored non-null rules are validated but never overwritten.
    A confirmed category with ``rule=None`` is always returned to review.
    The caller owns transaction commit and audit logging.
    """

    catalog, employees, views = build_attribute_catalog(db, policy_year_id, client_id)
    categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == policy_year_id)
            .order_by(Category.product_id, Category.priority)
        ).scalars()
    )
    profiles = {
        profile.category_signature: profile
        for profile in db.execute(
            select(EligibilityMappingProfile).where(
                EligibilityMappingProfile.client_id == client_id
            )
        ).scalars()
    }
    previous = _previous_confirmed_rules(db, policy_year_id, client_id)
    proposal_meta: dict[str, tuple[RuleProposal, bool, RuleValidation]] = {}

    for category in categories:
        preserve = bool(category.human_modified and category.matching_rule)
        if preserve:
            validation = validate_matching_rule(category.matching_rule, catalog)
            proposal = RuleProposal(
                rule=category.matching_rule,
                human_readable=category.rule_human_readable or category.display_name,
                confidence=float(category.confidence or 0.85),
                source="manual",
                validation_state="proposed",
                referenced_attributes=validation.referenced_attributes,
            )
            reused = False
        else:
            proposal, reused = _candidate_for_category(category, catalog, profiles, previous)
            validation = validate_matching_rule(proposal.rule, catalog)
            category.matching_rule = proposal.rule
            category.rule_human_readable = proposal.human_readable
            category.confidence = proposal.confidence

        category.rule_status = (
            "unmapped"
            if proposal.rule is None
            else "needs_review"
            if proposal.unresolved_clauses or not validation.valid
            else "proposed"
        )
        if category.matching_rule is None and category.status == CategoryStatus.confirmed.value:
            category.status = CategoryStatus.needs_review.value
        proposal_meta[category.id] = (proposal, reused, validation)

    counts, overlaps = _assignment_counts(
        db=db,
        client_id=client_id,
        categories=categories,
        employees=employees,
        views=views,
    )

    items: list[MappingItem] = []
    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories if c.product_id})
            )
        ).scalars()
    }
    for category in categories:
        proposal, reused, validation = proposal_meta[category.id]
        pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
        expected_raw = pa.get("num_employees")
        expected = int(expected_raw) if isinstance(expected_raw, (int, float)) else None
        matched = counts.get(category.id, 0) if catalog.roster_present else None
        errors = list(validation.errors)
        warnings: list[str] = []
        if not catalog.roster_present and proposal.rule is not None:
            warnings.append("No active roster is available to validate matched employees")
        if overlaps.get(category.id):
            warnings.append(
                f"{overlaps[category.id]} employees also match an equally specific sibling rule"
            )
        if expected is not None and matched is not None and expected != matched:
            warnings.append(f"Matched {matched} employees; placement slip states {expected}")
        warnings.extend(f"Unresolved clause: {clause}" for clause in proposal.unresolved_clauses)

        if proposal.rule is None:
            rule_status = "unmapped"
        elif errors or warnings:
            rule_status = "needs_review"
        elif catalog.roster_present:
            rule_status = "validated"
        else:
            rule_status = "proposed"
        category.rule_status = rule_status
        payload = {
            "state": rule_status,
            "source": proposal.source,
            "errors": errors,
            "warnings": warnings,
            "unresolved_clauses": proposal.unresolved_clauses,
            "required_attributes": validation.referenced_attributes,
            "matched_count": matched,
            "expected_count": expected,
            "relative_remainder": proposal.relative_remainder,
            "reused": reused,
        }
        category.rule_validation = payload
        if persist_profiles and proposal.rule is not None:
            profile = _upsert_profile(
                db,
                client_id=client_id,
                policy_year_id=policy_year_id,
                category=category,
                proposal=proposal,
                status="proposed",
                validation=payload,
            )
            category.mapping_profile_id = profile.id

        product = products.get(category.product_id) if category.product_id else None
        items.append(
            MappingItem(
                category_id=category.id,
                product_code=product.code if product else None,
                display_name=category.display_name,
                plan_code=str(pa.get("plan_code") or "") or None,
                category_status=category.status,
                rule_status=rule_status,
                source=proposal.source,
                matching_rule=category.matching_rule,
                rule_human_readable=category.rule_human_readable,
                confidence=category.confidence,
                matched_count=matched,
                expected_count=expected,
                unresolved_clauses=list(proposal.unresolved_clauses),
                errors=errors,
                warnings=warnings,
                reused=reused,
            )
        )

    missing = missing_category_plans(db, policy_year_id=policy_year_id)
    return MappingSummary(
        policy_year_id=policy_year_id,
        employee_count=catalog.employee_count,
        total=len(items),
        validated=sum(item.rule_status == "validated" for item in items),
        proposed=sum(item.rule_status == "proposed" for item in items),
        needs_review=sum(item.rule_status == "needs_review" for item in items),
        unmapped=sum(item.rule_status == "unmapped" for item in items),
        reused=sum(item.reused for item in items),
        categories=items,
        missing_categories=len(missing),
        missing_category_plans=missing,
    )


def confirm_category_mapping(
    db: Session, *, category: Category, client_id: str
) -> EligibilityMappingProfile:
    """Validate and persist one broker-confirmed reusable mapping profile."""

    catalog, _, _ = build_attribute_catalog(db, category.policy_year_id, client_id)
    validation = validate_matching_rule(category.matching_rule, catalog)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    existing = category.rule_validation if isinstance(category.rule_validation, dict) else {}
    proposal = RuleProposal(
        rule=category.matching_rule,
        human_readable=category.rule_human_readable or category.display_name,
        confidence=float(category.confidence or 0.85),
        source="manual" if category.human_modified else str(existing.get("source") or "confirmed"),
        validation_state="validated",
        referenced_attributes=validation.referenced_attributes,
        relative_remainder=bool(existing.get("relative_remainder")),
    )
    payload = {
        **existing,
        "state": "validated",
        "errors": [],
        "required_attributes": validation.referenced_attributes,
        "confirmed": True,
    }
    profile = _upsert_profile(
        db,
        client_id=client_id,
        policy_year_id=category.policy_year_id,
        category=category,
        proposal=proposal,
        status="confirmed",
        validation=payload,
    )
    category.mapping_profile_id = profile.id
    category.rule_status = "validated"
    category.rule_validation = payload
    category.status = CategoryStatus.confirmed.value
    return profile


def stored_mapping_summary(db: Session, *, policy_year_id: str) -> MappingSummary:
    """Read the last persisted mapping/validation state without side effects."""

    categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == policy_year_id)
            .order_by(Category.product_id, Category.priority)
        ).scalars()
    )
    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories if c.product_id})
            )
        ).scalars()
    }
    items: list[MappingItem] = []
    for category in categories:
        validation = category.rule_validation if isinstance(category.rule_validation, dict) else {}
        pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
        expected_raw = validation.get("expected_count", pa.get("num_employees"))
        matched_raw = validation.get("matched_count")
        expected = int(expected_raw) if isinstance(expected_raw, (int, float)) else None
        matched = int(matched_raw) if isinstance(matched_raw, (int, float)) else None
        rule_status = str(
            category.rule_status or ("unmapped" if category.matching_rule is None else "proposed")
        )
        product = products.get(category.product_id) if category.product_id else None
        items.append(
            MappingItem(
                category_id=category.id,
                product_code=product.code if product else None,
                display_name=category.display_name,
                plan_code=str(pa.get("plan_code") or "") or None,
                category_status=category.status,
                rule_status=rule_status,
                source=str(validation.get("source") or category.source),
                matching_rule=category.matching_rule,
                rule_human_readable=category.rule_human_readable,
                confidence=category.confidence,
                matched_count=matched,
                expected_count=expected,
                unresolved_clauses=[
                    str(value) for value in validation.get("unresolved_clauses", [])
                ],
                errors=[str(value) for value in validation.get("errors", [])],
                warnings=[str(value) for value in validation.get("warnings", [])],
                reused=bool(validation.get("reused")),
            )
        )
    employee_count = db.execute(
        select(Employee.id).where(Employee.policy_year_id == policy_year_id)
    ).all()
    missing = missing_category_plans(db, policy_year_id=policy_year_id)
    return MappingSummary(
        policy_year_id=policy_year_id,
        employee_count=len(employee_count),
        total=len(items),
        validated=sum(item.rule_status == "validated" for item in items),
        proposed=sum(item.rule_status == "proposed" for item in items),
        needs_review=sum(item.rule_status == "needs_review" for item in items),
        unmapped=sum(item.rule_status == "unmapped" for item in items),
        reused=sum(item.reused for item in items),
        categories=items,
        missing_categories=len(missing),
        missing_category_plans=missing,
    )


__all__ = [
    "AttributeValueCatalog",
    "CategoryRuleAssessment",
    "MappingItem",
    "MappingSummary",
    "MissingCategoryPlan",
    "RuleProposal",
    "RuleValidation",
    "assess_category_rule",
    "auto_map_policy_year",
    "build_ai_eligibility_inputs",
    "build_attribute_catalog",
    "category_signature",
    "confirm_category_mapping",
    "missing_category_plans",
    "propose_category_rule",
    "stored_mapping_summary",
    "validate_ai_matching_rule",
    "validate_matching_rule",
]
