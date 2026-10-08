"""Roster profiling — sample distinct raw attribute values per column.

Feeds the AI derivation-rule proposer: instead of sending thousands of rows,
we send each column's distinct values (capped) plus frequency, which is enough
for the model to infer an extraction/case rule. Pure sampling, no AI here.

What may leave for the AI provider is decided by `ai_column_payload` alone, on
an ALLOWLIST: sample values go out only for low-cardinality categorical columns
that are not personal data — the department, grade, job level, employment type
and entity vocabularies derivation rules are built from. Every other column
(names, identifiers, contact and bank details, salaries, dates, numbers, free
text, and anything the company flagged as personal or keeps from AI) is
described by name, inferred type and distinct count only.
`ColumnProfile.samples` keeps raw values in-process for validating proposed
rules; it is never the AI payload.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models import EmployeeAttributeSchema
from app.services.derivation_engine import resolve_attribute_schemas
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    DOB_KEYS,
    EMAIL_KEYS,
    EMPLOYEE_ID_KEYS,
    NAME_KEYS,
    looks_like_sg_nric,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Cap distinct values surfaced per column. Enough variety for the model to
# generalise a rule without blowing the prompt (and the AI spend) up.
MAX_DISTINCT_PER_COLUMN = 40
# Columns with more distinct values than this are almost certainly free-text /
# identifiers (names, emails, IDs), not derivable enums — skip them.
HIGH_CARDINALITY_SKIP = 200
# Sample values reach the AI provider only for categorical columns with at most
# this many distinct values: room for any department / grade / level / entity
# vocabulary, too few for a column of individual people's values.
MAX_SHAREABLE_DISTINCT = 30
# A category label is short; anything longer is prose (remarks, addresses).
_MAX_LABEL_CHARS = 48
_MAX_LABEL_WORDS = 6
# Keys that are never derivation sources (identity / contact / raw PII). Covers
# every NRIC/FIN alias so a roster that lands its ID under a non-``id_no`` column
# can't surface raw NRICs into the AI-proposer prompt.
_NON_SOURCE_KEYS: frozenset[str] = frozenset(
    {"staff_id", "employee_name", "mobile"}
    | set(EMPLOYEE_ID_KEYS)
    | set(DEPENDANT_ID_KEYS)
    | set(EMAIL_KEYS)
)
# Column-name words that mark personal data, identifiers, contact or bank
# details, money, dates or free text. A column named with one never shares
# its values, whatever they look like.
_PERSONAL_KEY_WORDS = frozenset(
    {
        "name", "names", "surname", "firstname", "lastname", "fullname", "nickname",
        "id", "ids", "nric", "fin", "uin", "passport", "ic", "no", "num", "number",
        "email", "mail", "phone", "mobile", "tel", "telephone", "contact", "address",
        "postal", "postcode", "zip", "bank", "account", "acct", "iban", "swift",
        "salary", "wage", "wages", "income", "pay", "payroll", "compensation",
        "remuneration", "bonus", "allowance", "amount", "date", "dob", "birth",
        "birthday", "birthdate", "remark", "remarks", "note", "notes", "comment",
        "comments", "memo", "reason", "description", "details", "gender", "sex",
        "marital", "nationality", "citizenship", "race", "religion", "ethnicity",
        "medical", "health", "diagnosis", "disability",
    }
)
_BOOLEAN_WORDS = frozenset({"y", "n", "yes", "no", "true", "false"})
_CURRENCY = re.compile(r"(?i)s\$|us\$|sgd|usd|myr|rm|\$|€|£")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?")
_INTEGER = re.compile(r"[+-]?\d+")
_NUMERIC_DATE = re.compile(
    r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?"
)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_NAMED_DATE = re.compile(
    rf"(?i)\d{{1,2}}[\s-]+{_MONTH}[\s,-]+\d{{2,4}}|{_MONTH}\s+\d{{1,2}},?\s+\d{{2,4}}"
)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_PUNCTUATION = re.compile(r"[\s()+.-]")


@dataclass(frozen=True)
class ColumnProfile:
    key: str
    total: int  # non-empty value count
    distinct_count: int
    samples: tuple[str, ...]  # up to MAX_DISTINCT_PER_COLUMN, most-frequent first
    high_cardinality: bool  # likely free-text / identifier, not an enum source
    inferred_type: str  # boolean / integer / decimal / date / email / text
    shareable: bool  # values may be shown to the AI provider (see module doc)


@dataclass(frozen=True)
class RosterProfile:
    employee_count: int
    columns: tuple[ColumnProfile, ...]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


@lru_cache(maxsize=1)
def _personal_keys() -> frozenset[str]:
    # Lazy: the listing importer is heavy, and only its PII vocabulary is needed.
    from app.services.el_import.records import PII_ATTRIBUTES

    return frozenset(PII_ATTRIBUTES) | _NON_SOURCE_KEYS | set(NAME_KEYS) | set(DOB_KEYS)


def _key_words(key: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
    return set(re.findall(r"[a-z]+", spaced))


def _number_text(value: str) -> str:
    return _CURRENCY.sub("", value).replace(",", "").replace(" ", "")


def _looks_numeric(value: str) -> bool:
    return _NUMBER.fullmatch(_number_text(value)) is not None


def _looks_like_date(value: str) -> bool:
    return bool(_NUMERIC_DATE.fullmatch(value) or _NAMED_DATE.fullmatch(value))


def _looks_like_identifier(value: str) -> bool:
    """NRIC/FIN, phone number, or a single token carrying a long digit run."""
    if looks_like_sg_nric(value):
        return True
    phone = _PHONE_PUNCTUATION.sub("", value)
    if len(phone) >= 7 and phone.isdigit():
        return True
    return " " not in value and sum(char.isdigit() for char in value) >= 5


def _personal_or_free_text(value: str) -> bool:
    return (
        len(value) > _MAX_LABEL_CHARS
        or len(value.split()) > _MAX_LABEL_WORDS
        or _looks_numeric(value)
        or _looks_like_date(value)
        or _EMAIL.search(value) is not None
        or _looks_like_identifier(value)
    )


def infer_value_type(values: Iterable[str]) -> str:
    """Best-effort value type of a column, from its distinct values."""
    present = [value.strip() for value in values if value and value.strip()]
    if not present:
        return "unknown"
    if {value.lower() for value in present} <= _BOOLEAN_WORDS:
        return "boolean"
    if all(_looks_numeric(value) for value in present):
        integers = all(_INTEGER.fullmatch(_number_text(value)) for value in present)
        return "integer" if integers else "decimal"
    if all(_looks_like_date(value) for value in present):
        return "date"
    if all(_EMAIL.fullmatch(value) for value in present):
        return "email"
    return "text"


def column_shareable(
    key: str,
    values: Collection[str],
    *,
    distinct_count: int,
    total: int,
    restricted_keys: Collection[str] = (),
) -> bool:
    """May this column's distinct values be shown to the AI provider?

    Allowlist: only a low-cardinality categorical column whose name marks no
    personal data, that the company has not restricted, where values repeat
    across rows (a column unique per row is an identifier, however it is named)
    and no value looks like a number, date, contact detail, identifier or prose.
    """
    if (
        not values
        or key in restricted_keys
        or key.strip().lower() in _personal_keys()
        or _key_words(key) & _PERSONAL_KEY_WORDS
        or distinct_count > MAX_SHAREABLE_DISTINCT
        or distinct_count >= total
    ):
        return False
    return not any(_personal_or_free_text(value) for value in values)


def ai_column_payload(
    columns: Iterable[Mapping[str, Any]],
    restricted_keys: Collection[str] = (),
) -> list[dict[str, Any]]:
    """The only view of roster columns that may be sent to the AI provider.

    Takes profile dicts (``key``, ``samples``, ``distinct_count``, ``total``,
    optionally ``inferred_type`` / ``shareable`` from `ColumnProfile`) and
    returns them with ``samples`` emptied for every column outside the
    allowlist. Idempotent, so each boundary can re-apply it.
    """
    payload: list[dict[str, Any]] = []
    for column in columns:
        key = str(column.get("key") or "")
        samples = [str(value) for value in column.get("samples") or ()]
        distinct_count = int(column.get("distinct_count") or len(samples))
        total = int(column.get("total") or 0)
        shareable = bool(column.get("shareable", True)) and column_shareable(
            key,
            samples,
            distinct_count=distinct_count,
            total=total,
            restricted_keys=restricted_keys,
        )
        payload.append(
            {
                "key": key,
                "inferred_type": str(column.get("inferred_type") or infer_value_type(samples)),
                "distinct_count": distinct_count,
                "total": total,
                "samples": samples[:MAX_SHAREABLE_DISTINCT] if shareable else [],
            }
        )
    return payload


def ai_restricted_keys(db: Session, client_id: str) -> frozenset[str]:
    """Attribute ids whose values this company keeps from the AI provider:
    anything flagged personal data, or with "Share values with AI" switched off
    (a company definition overrides the firm-library default)."""
    # Lazy: deps pulls in the request auth stack, which this module must not.
    from app.core.deps import tenant_or_global

    schemas = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    return frozenset(
        schema.attribute_id
        for schema in schemas
        if schema.is_pii or not schema.allow_ai_values
    )


def profile_roster(
    rows: Iterable[Mapping[str, Any]],
    *,
    max_distinct: int = MAX_DISTINCT_PER_COLUMN,
) -> RosterProfile:
    """Build a per-column distinct-value profile from raw employee attributes.

    `rows` is each employee's raw ``attribute_values`` mapping. Values are
    stringified and trimmed; empties are ignored. Columns are returned sorted
    by descending fill count so the most-populated (most-derivable) appear
    first.
    """
    counters: dict[str, Counter[str]] = {}
    employee_count = 0
    for row in rows:
        employee_count += 1
        if not row:
            continue
        for key, raw in row.items():
            if key in _NON_SOURCE_KEYS:
                continue
            cleaned = _clean(raw)
            if cleaned is None:
                continue
            counters.setdefault(key, Counter())[cleaned] += 1

    columns: list[ColumnProfile] = []
    for key, counter in counters.items():
        total = sum(counter.values())
        distinct_count = len(counter)
        high_card = distinct_count > HIGH_CARDINALITY_SKIP
        # High-cardinality columns are free-text / identifiers, not enum sources.
        # Don't keep their raw values — the model skips them anyway, and they
        # may hold PII the alias filter above didn't anticipate.
        samples = (
            ()
            if high_card
            else tuple(v for v, _ in counter.most_common(max_distinct))
        )
        columns.append(
            ColumnProfile(
                key=key,
                total=total,
                distinct_count=distinct_count,
                samples=samples,
                high_cardinality=high_card,
                inferred_type=infer_value_type(counter),
                shareable=column_shareable(
                    key, list(counter), distinct_count=distinct_count, total=total
                ),
            )
        )

    columns.sort(key=lambda c: c.total, reverse=True)
    return RosterProfile(employee_count=employee_count, columns=tuple(columns))


__all__ = [
    "MAX_DISTINCT_PER_COLUMN",
    "MAX_SHAREABLE_DISTINCT",
    "ColumnProfile",
    "RosterProfile",
    "ai_column_payload",
    "ai_restricted_keys",
    "column_shareable",
    "infer_value_type",
    "profile_roster",
]
