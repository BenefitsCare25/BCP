"""Eligibility rules for joiners, learned from the company's Employee Listing.

People on the listing keep the category it states. Someone added later is
placed by rules derived from how the listing assigns existing people: for each
product, employees are grouped by up to two roster attributes (grade and work
pass for GAS), and each group's most common category becomes a rule. A value
whose every group agrees collapses to that value alone, so an unseen pass type
of a known grade still matches. Members whose listed category differs from
their group's are exceptions: they keep their listed category, and the count is
reported so the broker can check them.

Pure: no database. ``persist_rules`` writes the rules onto categories that a
broker has not edited, marked with source ``employee_listing`` so the
eligibility auto-mapper keeps them.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.models import Category
from app.services.eligibility_mapping import LISTING_RULE_SOURCE

# Attributes a listing rule may test, in preference order. Only attributes with
# a handful of values make rules a broker can read and check.
_CANDIDATES = ("grade", "pass", "job_grade", "class", "location", "depot", "entity")
_MAX_VALUES = 30
_ATTRIBUTE_LABELS = {"pass": "work pass", "job_grade": "job grade"}


@dataclass
class ProductRules:
    product_code: str
    attributes: tuple[str, ...]
    rules: dict[str, dict[str, Any]]  # category id -> JSONLogic
    readable: dict[str, str]  # category id -> human-readable rule
    members: Counter[str] = field(default_factory=Counter)  # category id -> listed count
    exceptions: Counter[str] = field(default_factory=Counter)  # category id -> count
    exception_staff: list[str] = field(default_factory=list)


def _value(attrs: dict[str, Any], attr: str) -> str:
    return str(attrs.get(attr) or "").strip()


def _purity(rows: list[tuple[dict[str, Any], str | None]], attrs: tuple[str, ...]) -> int:
    groups: dict[tuple[str, ...], Counter[str | None]] = defaultdict(Counter)
    for row_attrs, category in rows:
        groups[tuple(_value(row_attrs, a) for a in attrs)][category] += 1
    return sum(c.most_common(1)[0][1] for c in groups.values())


def _choose_attributes(rows: list[tuple[dict[str, Any], str | None]]) -> tuple[str, ...]:
    usable = [
        a for a in _CANDIDATES
        if 1 < len({_value(r, a) for r, _ in rows} - {""}) <= _MAX_VALUES
        and all(_value(r, a) for r, _ in rows)
    ]
    if not usable:
        return ()
    # Earlier candidates win ties: the preference order is the readable one.
    single = max(usable, key=lambda a: (_purity(rows, (a,)), -usable.index(a)))
    best: tuple[str, ...] = (single,)
    best_score = _purity(rows, best)
    for other in usable:
        if other == single:
            continue
        score = _purity(rows, (single, other))
        if score > best_score:
            best, best_score = (single, other), score
    return best


def _condition(attr: str, values: list[str]) -> dict[str, Any]:
    # The rule dialect of ``rule_evaluator``: [attribute, value(s)].
    if len(values) == 1:
        return {"=": [attr, values[0]]}
    return {"in": [attr, sorted(values)]}


def _text(attr: str, values: list[str]) -> str:
    name = _ATTRIBUTE_LABELS.get(attr, attr.replace("_", " "))
    return f"{name} is {', '.join(sorted(values))}" if len(values) > 1 else f"{name} is {values[0]}"


def derive_product_rules(
    product_code: str, rows: list[tuple[str, dict[str, Any], str | None]]
) -> ProductRules | None:
    """``rows``: (staff ID, roster attributes, listed category id or None for no cover)."""
    pairs = [(attrs, category) for _staff, attrs, category in rows]
    attrs = _choose_attributes(pairs)
    if not attrs:
        return None
    groups: dict[tuple[str, ...], Counter[str | None]] = defaultdict(Counter)
    for row_attrs, category in pairs:
        groups[tuple(_value(row_attrs, a) for a in attrs)][category] += 1
    winner = {key: counts.most_common(1)[0][0] for key, counts in groups.items()}

    result = ProductRules(product_code, attrs, {}, {})
    for staff, row_attrs, category in rows:
        key = tuple(_value(row_attrs, a) for a in attrs)
        if category is not None:
            result.members[category] += 1
        if winner[key] != category:
            result.exceptions[category or "none"] += 1
            result.exception_staff.append(staff)

    first = attrs[0]
    by_first: dict[str, set[str | None]] = defaultdict(set)
    for key, category in winner.items():
        by_first[key[0]].add(category)
    clauses: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    whole_values: dict[str, list[str]] = defaultdict(list)
    for value, categories in sorted(by_first.items()):
        if len(categories) == 1:
            (category,) = categories
            if category is not None:
                whole_values[category].append(value)
            continue
        if len(attrs) < 2:
            continue
        second: dict[str | None, list[str]] = defaultdict(list)
        for key, category in winner.items():
            if key[0] == value:
                second[category].append(key[1])
        for category, values in second.items():
            if category is None:
                continue
            clauses[category].append((
                {"and": [_condition(first, [value]), _condition(attrs[1], values)]},
                f"{_text(first, [value])} and {_text(attrs[1], values)}",
            ))
    for category, values in whole_values.items():
        clauses[category].insert(0, (_condition(first, values), _text(first, values)))
    for category, parts in clauses.items():
        result.rules[category] = parts[0][0] if len(parts) == 1 else {"or": [p for p, _ in parts]}
        result.readable[category] = " or ".join(f"({t})" if len(parts) > 1 else t
                                                for _, t in parts)
    return result


def persist_rules(categories: dict[str, Category], rules: list[ProductRules]) -> int:
    """Write listing rules onto categories a broker hasn't edited or confirmed."""
    written = 0
    for product in rules:
        for category_id, rule in product.rules.items():
            category = categories.get(category_id)
            if category is None or category.human_modified or category.status == "confirmed":
                continue
            category.matching_rule = rule
            category.rule_human_readable = product.readable[category_id]
            category.rule_status = "proposed"
            category.rule_validation = {
                "state": "proposed",
                "source": LISTING_RULE_SOURCE,
                "errors": [],
                "warnings": [],
                "listed_members": product.members.get(category_id, 0),
                "listed_exceptions": product.exceptions.get(category_id, 0),
                "required_attributes": list(product.attributes),
            }
            written += 1
    return written


def rows_from_listing(
    workbook: Any,
    attributes_by_row: dict[int, dict[str, Any]],
    decisions: dict[tuple[int, str], Any],
    block_products: dict[int, list[str]],
) -> dict[str, list[tuple[str, dict[str, Any], str | None]]]:
    """Per product: (staff ID, roster attributes, listed category id or None)
    for every listed employee, from the listing and its reviewed mapping."""
    from app.services.el_import.mapping import normalize_label
    from app.services.el_workbook.values import text

    out: dict[str, list[tuple[str, dict[str, Any], str | None]]] = defaultdict(list)
    for emp in workbook.employees:
        attrs = attributes_by_row.get(emp.row, {})
        staff = text(emp.fields.get("staff_id"))
        chosen: dict[str, str] = {}
        for cover in emp.covers:
            decision = decisions.get(
                (cover.block, normalize_label(text(cover.values.get("category"))))
            )
            if decision is not None and decision.choice is not None:
                chosen[decision.choice.product_code] = decision.choice.category_id
        for codes in block_products.values():
            for code in codes:
                out[code].append((staff, attrs, chosen.get(code)))
    return out
