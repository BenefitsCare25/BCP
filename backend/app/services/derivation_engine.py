"""Schema-driven attribute derivation.

Reads `employee_attribute_schemas.derivation_rule` JSON specs and applies them
to raw `employee.attribute_values`, producing the structured
`derived_attribute_values` that the matching engine evaluates JSONLogic rules
against (see brief §8.4).

Three ops are supported:

- `regex_extract`: extract a capture group from a source field, optionally cast.
- `regex_case`: first-match-wins lookup, each case maps a pattern to a literal value.
- `passthrough`: copy a raw attribute through (used to surface enum fields like
  `pass` into the derived view without re-keying).

A bad regex or missing source produces `None` for that attribute — never raises.
The rest of the schemas continue to derive.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from app.models.schema_def import EmployeeAttributeSchema

logger = logging.getLogger(__name__)


@lru_cache(maxsize=512)
def _compile(pattern: str, ignore_case: bool) -> re.Pattern[str]:
    """Compile regex once and reuse — derivation runs the same patterns per
    employee, so caching avoids reparsing 4607x per attribute on full-roster
    runs.
    """
    return re.compile(pattern, re.IGNORECASE if ignore_case else 0)


def resolve_attribute_schemas(
    schemas: Iterable[EmployeeAttributeSchema],
) -> list[EmployeeAttributeSchema]:
    """Collapse global + client-specific schemas to one row per attribute_id,
    preferring the client-specific override.

    A client can override a global default (e.g. a per-client `grade`
    derivation rule). Both rows come back from a `tenant_or_global` query, so
    without this an attribute would be derived twice with ambiguous ordering.
    Client-specific (`client_id` set) always wins over global (`client_id` None).
    """
    resolved: dict[str, EmployeeAttributeSchema] = {}
    for schema in schemas:
        existing = resolved.get(schema.attribute_id)
        if existing is None or (existing.client_id is None and schema.client_id is not None):
            resolved[schema.attribute_id] = schema
    return list(resolved.values())


def derive(
    raw_attributes: dict[str, Any],
    schemas: Iterable[EmployeeAttributeSchema],
) -> dict[str, Any]:
    """Apply each schema's `derivation_rule` and return a dict of derived values.

    Schemas without a `derivation_rule` are skipped. Values that derive to `None`
    are omitted from the result (callers should treat missing keys as "no
    derivation produced a value", not "the value is null"). Global and
    client-specific schemas are first collapsed so a client override wins.
    """
    out: dict[str, Any] = {}
    for schema in resolve_attribute_schemas(schemas):
        rule = schema.derivation_rule
        if not rule or not isinstance(rule, dict):
            continue
        try:
            value = _apply(rule, raw_attributes)
        except Exception:
            logger.exception(
                "derivation failed for attribute %s", schema.attribute_id
            )
            value = None
        if value is not None:
            out[schema.attribute_id] = value
    return out


def apply_rule(rule: dict[str, Any], raw: dict[str, Any]) -> Any:
    """Apply a single derivation rule to a raw attribute mapping.

    Unlike `derive`, this does NOT swallow exceptions — a malformed pattern
    raises `re.error`. Used by the roster-profiling validator so a bad
    AI-proposed rule is surfaced to the reviewer rather than silently yielding
    None for every row.
    """
    return _apply(rule, raw)


def _apply(rule: dict[str, Any], raw: dict[str, Any]) -> Any:
    op = rule.get("op")
    if op == "regex_extract":
        return _regex_extract(rule, raw)
    if op == "regex_case":
        return _regex_case(rule, raw)
    if op == "passthrough":
        return _passthrough(rule, raw)
    logger.warning("unknown derivation op: %s", op)
    return None


def _read_source(rule: dict[str, Any], raw: dict[str, Any]) -> str | None:
    source = rule.get("source")
    if not source:
        return None
    val = raw.get(source)
    if val is None or val == "":
        return None
    return str(val)


def _regex_extract(rule: dict[str, Any], raw: dict[str, Any]) -> Any:
    text = _read_source(rule, raw)
    if text is None:
        return None
    pattern = rule.get("pattern")
    if not pattern:
        return None
    m = _compile(pattern, rule.get("ignore_case", True)).search(text)
    if not m:
        return None
    group = rule.get("group", 1)
    value: Any = m.group(group)
    cast = rule.get("cast")
    if cast == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if cast == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return value


def _regex_case(rule: dict[str, Any], raw: dict[str, Any]) -> Any:
    text = _read_source(rule, raw)
    if text is None:
        return None
    cases = rule.get("cases") or []
    ignore_case = rule.get("ignore_case", True)
    for case in cases:
        pattern = case.get("pattern")
        if not pattern:
            continue
        if _compile(pattern, ignore_case).search(text):
            return case.get("value")
    return rule.get("default")


def _passthrough(rule: dict[str, Any], raw: dict[str, Any]) -> Any:
    source = rule.get("source")
    if not source:
        return None
    val = raw.get(source)
    if val == "":
        return None
    return val
