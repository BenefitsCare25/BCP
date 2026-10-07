"""Token normalization and company-vocabulary value/location matching."""

from __future__ import annotations

from typing import Any

from app.services.eligibility_mapping.base import (
    _ATTR_PRIORITY,
    _BASED_IN_RE,
    _DEPENDANT_TAIL_RE,
    _EXCLUSION_RE,
    _LOCATION_ALIAS_SEPARATOR_RE,
    _NATIONALITY_ATTRS,
    _OPTION_TAIL_RE,
    _PLAN_PREFIX_RE,
    _TOKEN_ALIASES,
    _TOKEN_RE,
    _WORK_LOCATION_ATTRS,
    AttributeValueCatalog,
)
from app.services.flex_membership import nationality_country_exact


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


def _matched_values(
    text: str, values: list[Any], *, allow_single_character: bool = True
) -> list[Any]:
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
        if not allow_single_character and len(needle) == 1 and len(needle[0]) == 1:
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
        matched = _matched_values(text, values, allow_single_character=False)
        if matched:
            candidates.append((-len(matched), _attribute_rank(attribute_id), attribute_id, matched))
    if not candidates:
        return None, []
    _, _, attribute_id, matched = min(candidates)
    return attribute_id, matched


def _exact_value_mapping(text: str, catalog: AttributeValueCatalog) -> tuple[str | None, list[Any]]:
    """Return a company value only when the normalized phrases are identical."""

    needle = _tokens(text)
    if not needle:
        return None, []
    candidates: list[tuple[tuple[int, str], str, Any]] = []
    for attribute_id, values in catalog.values.items():
        if catalog.data_types.get(attribute_id) not in {None, "string", "enum"}:
            continue
        for value in values:
            if _tokens(value) == needle:
                candidates.append((_attribute_rank(attribute_id), attribute_id, value))
    if not candidates:
        return None, []
    _, attribute_id, value = min(candidates)
    return attribute_id, [value]


def _multi_named_cohort_mapping(
    text: str, catalog: AttributeValueCatalog
) -> tuple[str | None, list[Any]]:
    """Resolve a description that explicitly joins multiple company cohorts."""

    candidates: list[tuple[int, str, list[Any]]] = []
    for order, attribute_id in enumerate(
        (
            "employee_category",
            "category",
            "employment_type",
            "employee_type",
            "person_class",
        )
    ):
        values = catalog.values.get(attribute_id, [])
        matched = _matched_values(text, values, allow_single_character=False)
        if len(matched) >= 2:
            candidates.append((order, attribute_id, matched))
    if not candidates:
        return None, []
    _, attribute_id, values = min(candidates)
    return attribute_id, values


def _catalog_attribute(catalog: AttributeValueCatalog, candidates: tuple[str, ...]) -> str | None:
    """Resolve a slip field label to a populated company attribute."""

    return next(
        (
            attribute_id
            for attribute_id in candidates
            if attribute_id in catalog.attribute_ids and catalog.values.get(attribute_id)
        ),
        None,
    )


def _location_aliases(country_text: str) -> list[str]:
    """Return the complete set of explicitly named location clauses."""

    return [
        piece.strip(" .")
        for piece in _LOCATION_ALIAS_SEPARATOR_RE.split(country_text.strip())
        if piece.strip(" .")
    ]


def _complete_location_value_mapping(
    country_text: str,
    catalog: AttributeValueCatalog,
    *,
    allowed: tuple[str, ...],
) -> tuple[str | None, list[Any]]:
    """Map a location only when the entire phrase is represented.

    Generic phrase matching is intentionally unsafe here: it can turn
    ``Thailand and Myanmar`` into a Thailand-only rule. A location attribute
    may either contain the complete phrase as one roster value or contain an
    exact value for every explicitly separated location.
    """

    whole_tokens = _tokens(country_text)
    aliases = _location_aliases(country_text)
    if not whole_tokens or not aliases:
        return None, []

    candidates: list[tuple[int, str, list[Any]]] = []
    for order, attribute_id in enumerate(allowed):
        if catalog.data_types.get(attribute_id) not in {None, "string", "enum"}:
            continue
        catalog_values = catalog.values.get(attribute_id, [])
        whole_matches = [value for value in catalog_values if _tokens(value) == whole_tokens]
        if whole_matches:
            candidates.append((order, attribute_id, [whole_matches[0]]))
            continue

        matched_values: list[Any] = []
        complete = True
        for alias in aliases:
            alias_tokens = _tokens(alias)
            match = next(
                (value for value in catalog_values if _tokens(value) == alias_tokens),
                None,
            )
            if match is None:
                complete = False
                break
            if match not in matched_values:
                matched_values.append(match)
        if complete and matched_values:
            candidates.append((order, attribute_id, matched_values))

    if not candidates:
        return None, []
    _, attribute_id, values = min(candidates)
    return attribute_id, values


def _exact_location_countries(country_text: str) -> list[str] | None:
    """Normalize every named location, rejecting partial/ambiguous text."""

    aliases = _location_aliases(country_text)
    countries = [nationality_country_exact(alias) for alias in aliases]
    if not aliases or not all(countries):
        return None
    return list(dict.fromkeys(country for country in countries if country))


def _location_value_mapping(
    country_text: str, catalog: AttributeValueCatalog
) -> tuple[str | None, list[Any], bool]:
    """Resolve work location, falling back to a review-only nationality proxy."""

    attribute_id, values = _complete_location_value_mapping(
        country_text, catalog, allowed=_WORK_LOCATION_ATTRS
    )
    if attribute_id and values:
        return attribute_id, values, False
    attribute_id, values = _complete_location_value_mapping(
        country_text, catalog, allowed=_NATIONALITY_ATTRS
    )
    if attribute_id and values:
        return attribute_id, values, True

    # A slip names a country ("Thailand") while employee listings commonly use
    # the nationality adjective ("Thai").  Match on the normalized country but
    # keep the exact roster literal in the generated rule so rule evaluation is
    # deterministic.  The alias solves only the literal mismatch; nationality
    # remains a review-only proxy for work location.
    countries = _exact_location_countries(country_text)
    if countries:
        wanted = set(countries)
        for nationality_attr in _NATIONALITY_ATTRS:
            nationality_values = catalog.values.get(nationality_attr, [])
            semantic_matches = [
                value
                for value in nationality_values
                if nationality_country_exact(value) in wanted
            ]
            matched_countries = {
                nationality_country_exact(value) for value in semantic_matches
            }
            if semantic_matches and matched_countries == wanted:
                return nationality_attr, semantic_matches, True
    return None, [], False


def _location_cohort_label(
    text: str, catalog: AttributeValueCatalog
) -> tuple[str | None, Any | None]:
    """Use one unambiguous roster label with the same location and exception."""
    location = _BASED_IN_RE.search(text)
    if location is None:
        return None, None
    words = _tokens(text)
    excluded = _EXCLUSION_RE.search(text)
    excluded_words = _tokens(excluded.group(1)) if excluded else []
    candidates: list[tuple[str, Any]] = []
    for attribute_id in ("category", "employee_category"):
        for value in catalog.values.get(attribute_id, []):
            label = str(value)
            label_location = _BASED_IN_RE.search(label)
            label_excluded = _EXCLUSION_RE.search(label)
            if (
                label_location
                and _tokens(label_location.group(1)) == _tokens(location.group(1))
                and (_tokens(label_excluded.group(1)) if label_excluded else []) == excluded_words
                and _sequence_spans(words, _tokens(label))
            ):
                candidates.append((attribute_id, value))
    if len(candidates) == 1:
        return candidates[0]
    return None, None
