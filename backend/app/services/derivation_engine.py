"""Schema-driven attribute derivation.

Reads `employee_attribute_schemas.derivation_rule` JSON specs and applies them
to raw `employee.attribute_values`, producing the structured
`derived_attribute_values` that the matching engine evaluates JSONLogic rules
against (see brief §8.4).

The supported operations are:

- `regex_extract`: extract a capture group from a source field, optionally cast.
- `regex_case`: first-match-wins lookup, each case maps a pattern to a literal value.
- `passthrough`: copy a raw attribute through (used to surface enum fields like
  `pass` into the derived view without re-keying).
- `value_map`: translate exact, case-insensitive source values into company
  eligibility labels; unlisted values can be omitted or preserved.

A bad regex or missing source produces `None` for that attribute — never raises.
The rest of the schemas continue to derive.

Patterns run once per employee against roster text, so a pattern that can
backtrack exponentially would stall a whole roster run. `rule_pattern_error`
refuses such patterns when a rule is saved, the engine refuses to run one that
was stored before that check existed, and every match reads at most
`MAX_MATCH_INPUT` characters of the source value.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.models.schema_def import EmployeeAttributeSchema

logger = logging.getLogger(__name__)

MAX_PATTERN_LENGTH = 200
# Roster cells are short; the cap bounds what a polynomial pattern (".*.*x")
# can cost on one oversized value.
MAX_MATCH_INPUT = 1000

_REGEX_OPS = ("regex_extract", "regex_case")
_SIMPLE_QUANTIFIERS = {"*": (0.0, math.inf), "+": (1.0, math.inf), "?": (0.0, 1.0)}
_BRACE_QUANTIFIER = re.compile(r"\{(\d*)(,?)(\d*)\}")
_INLINE_FLAGS = re.compile(r"\(\?([aiLmsux]*)(?:-[imsx]+)?[:)]")
_NESTED_REPETITION = (
    "repeats a group that itself matches a varying amount of text (for example "
    "(a+)+ or (a|aa)+), which can take exponential time on some roster values. "
    "Use a single quantifier or a character class instead"
)
_VERBOSE_MODE = "turns on verbose mode (?x), which derivation patterns do not support"


@dataclass
class _Group:
    varies: bool = False  # holds a quantifier whose match length can vary
    branches: bool = False  # holds an alternation


def _quantifier_at(pattern: str, i: int) -> tuple[float, float, int] | None:
    """``(min, max, end)`` of the quantifier starting at ``pattern[i]``, if any."""
    ch = pattern[i]
    if ch in _SIMPLE_QUANTIFIERS:
        low, high = _SIMPLE_QUANTIFIERS[ch]
        end = i + 1
    else:
        brace = _BRACE_QUANTIFIER.match(pattern, i) if ch == "{" else None
        # "{}" and "{x}" are literals; "{,}" is an unbounded repeat.
        if brace is None or not (brace.group(1) or brace.group(2)):
            return None
        low = float(brace.group(1) or 0)
        if not brace.group(2):
            high = low
        else:
            high = float(brace.group(3)) if brace.group(3) else math.inf
        end = brace.end()
    if pattern[end:end + 1] in ("?", "+"):  # lazy or possessive form
        end += 1
    return low, high, end


def _class_end(pattern: str, i: int) -> int:
    """Index just past the character class that opens at ``pattern[i]``."""
    j = i + 1
    if pattern[j:j + 1] == "^":
        j += 1
    if pattern[j:j + 1] == "]":  # a leading "]" is a literal
        j += 1
    while j < len(pattern) and pattern[j] != "]":
        j += 2 if pattern[j] == "\\" else 1
    return j + 1


def _backtracking_risk(pattern: str) -> str | None:
    """Why a COMPILABLE pattern could backtrack exponentially, else None.

    Deliberately conservative: any group repeated more than once whose body
    varies in length or alternates is refused, although some such groups
    ((a|b)+) are harmless. Verbose mode is refused outright because its
    whitespace and comments would hide a quantifier from this scan.
    """
    stack = [_Group()]
    repeated: _Group | None = None  # the group a quantifier here would repeat
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        quantifier = _quantifier_at(pattern, i)
        if quantifier is not None:
            low, high, i = quantifier
            if repeated is not None and high > 1 and (repeated.varies or repeated.branches):
                return _NESTED_REPETITION
            stack[-1].varies |= low != high
            repeated = None
        elif pattern.startswith("(?#", i):
            # A comment is not an atom: a quantifier after it repeats what preceded it.
            i = pattern.index(")", i) + 1
        elif ch == "(":
            flags = _INLINE_FLAGS.match(pattern, i)
            if flags is not None and "x" in flags.group(1):
                return _VERBOSE_MODE
            stack.append(_Group())
            repeated = None
            i += 2 if pattern.startswith("(?", i) else 1
        elif ch == ")":
            repeated = stack.pop()
            stack[-1].varies |= repeated.varies
            stack[-1].branches |= repeated.branches
            i += 1
        else:
            stack[-1].branches |= ch == "|"
            repeated = None
            i = _class_end(pattern, i) if ch == "[" else i + (2 if ch == "\\" else 1)
    return None


def pattern_error(pattern: object) -> str | None:
    """Why ``pattern`` must not be stored as a derivation pattern, else None."""
    if not isinstance(pattern, str):
        return "Derivation patterns must be text."
    if len(pattern) > MAX_PATTERN_LENGTH:
        return f"Derivation patterns must be at most {MAX_PATTERN_LENGTH} characters."
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"The derivation pattern {pattern!r} is not a valid regular expression: {exc}."
    risk = _backtracking_risk(pattern)
    return f"The derivation pattern {pattern!r} {risk}." if risk is not None else None


def rule_pattern_error(rule: object) -> str | None:
    """The first reason a derivation rule's regex patterns must not be stored,
    or None when the rule has none or they are all safe. Blank patterns are left
    to the engine, which skips them."""
    if not isinstance(rule, dict) or rule.get("op") not in _REGEX_OPS:
        return None
    if rule["op"] == "regex_extract":
        patterns = [rule.get("pattern")]
    else:
        cases = rule.get("cases")
        patterns = [
            case.get("pattern") for case in cases if isinstance(case, dict)
        ] if isinstance(cases, list) else []
    for pattern in patterns:
        if pattern is None or pattern == "":
            continue
        problem = pattern_error(pattern)
        if problem is not None:
            return problem
    return None


@lru_cache(maxsize=512)
def _compile(pattern: str, ignore_case: bool) -> re.Pattern[str]:
    """Compile regex once and reuse — derivation runs the same patterns per
    employee, so caching avoids reparsing 4607x per attribute on full-roster
    runs.

    A pattern stored before `rule_pattern_error` existed is still refused here
    (raised as ``re.error``, like any other unusable pattern).
    """
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise re.error(f"derivation pattern is longer than {MAX_PATTERN_LENGTH} characters")
    compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    risk = _backtracking_risk(pattern)
    if risk is not None:
        raise re.error(f"unsafe derivation pattern: it {risk}")
    return compiled


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
    if op == "value_map":
        source = _read_source(rule, raw)
        if source is None:
            return None
        key = source.strip().casefold()
        for entry in rule.get("mappings", []):
            if str(entry["from"]).strip().casefold() == key:
                return entry["to"]
        return source if rule.get("unmapped") == "keep" else None
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


def _regex_input(rule: dict[str, Any], raw: dict[str, Any]) -> str | None:
    """The source value a pattern searches, capped at `MAX_MATCH_INPUT`."""
    text = _read_source(rule, raw)
    return text[:MAX_MATCH_INPUT] if text is not None else None


def _regex_extract(rule: dict[str, Any], raw: dict[str, Any]) -> Any:
    text = _regex_input(rule, raw)
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
    text = _regex_input(rule, raw)
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
