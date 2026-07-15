"""JSONLogic predicate evaluator.

Ported from the prototype's `evalCondition`. Lives next to the rule generator
because the round-trip (rule_generator emits → evaluator consumes) is the
load-bearing contract the four form primitives will build against in Phase 3.

String comparisons are case-insensitive (uppercased on both sides) because
that's how the prototype handled enum-like fields (pass codes, class names).
Numeric comparisons coerce via float.

Errors are isolated: malformed values for a comparison return ``False``
(no-match) rather than propagating an exception that would crash the
entire matching run. The caller can detect "I evaluated a rule and it was
broken" via the standard "no-match" outcome; a logging hook lives in the
matching engine.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

Rule = dict[str, Any]
EmployeeView = dict[str, Any]


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate(rule: Rule | None, employee: EmployeeView) -> bool:
    if rule is None:
        return False
    if not isinstance(rule, dict) or not rule:
        return False
    key = next(iter(rule))
    args = rule[key]

    if key == "and":
        if not isinstance(args, list):
            return False
        return all(evaluate(c, employee) for c in args)
    if key == "or":
        if not isinstance(args, list):
            return False
        return any(evaluate(c, employee) for c in args)
    if key == "not":
        return not evaluate(args, employee)

    if not isinstance(args, list) or not args:
        return False

    attr = args[0]
    value = employee.get(attr)
    if value is None:
        return False

    sv = str(value).upper()

    try:
        if key in {"=", "=="}:
            return sv == str(args[1]).upper()
        if key == "!=":
            return sv != str(args[1]).upper()
        if key in {">=", "<=", ">", "<"}:
            lhs = _safe_float(value)
            rhs = _safe_float(args[1])
            if lhs is None or rhs is None:
                return False
            if key == ">=":
                return lhs >= rhs
            if key == "<=":
                return lhs <= rhs
            if key == ">":
                return lhs > rhs
            return lhs < rhs
        if key == "between":
            v = _safe_float(value)
            lo = _safe_float(args[1])
            hi = _safe_float(args[2])
            if v is None or lo is None or hi is None:
                return False
            return lo <= v <= hi
        if key == "in":
            choices = args[1]
            if not isinstance(choices, list):
                return False
            return sv in [str(x).upper() for x in choices]
        if key == "not_in":
            choices = args[1]
            if not isinstance(choices, list):
                return False
            return sv not in [str(x).upper() for x in choices]
    except (IndexError, KeyError) as exc:
        # Malformed rule shape (missing args). Log once per rule key — a
        # bad rule should be visible but should NOT crash the matcher.
        logger.warning("Rule eval error for %r: %s", key, exc)
        return False

    # Unknown operator — log once so admins notice.
    logger.warning("Unknown JSONLogic operator: %r", key)
    return False
