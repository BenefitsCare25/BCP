"""One policy-number resolver shared by placement and member reports.

Legacy composite source text is deliberately never treated as a member's policy.
No entity association is inferred from the order of numbers or legal names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException

from app.core.auth import FIRM_OWNER_ROLES
from app.schemas.policy_numbers import PolicyNumberAssignment
from app.services.roster_attributes import first_value

_SEPARATORS = re.compile(r"[,;\r\n]+|\s+/\s+")
_PLACEHOLDERS = {"tba", "tbc", "tbd", "pending", "n/a", "na", "none", "not issued", "-"}


class PolicyTerms(Protocol):
    @property
    def policy_number(self) -> str | None: ...

    @property
    def policy_number_mappings(self) -> list[dict[str, Any]] | None: ...


def entity_key(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def source_numbers(value: str | None) -> list[str]:
    return list(dict.fromkeys(
        part.strip() for part in _SEPARATORS.split(str(value or ""))
        if part.strip() and part.strip().casefold() not in _PLACEHOLDERS
    ))


def validate_assignments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("Use a list of at most 100 policy-number assignments.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        item = PolicyNumberAssignment.model_validate(raw)
        key = entity_key(item.entity)
        if item.entity is not None and not key:
            raise ValueError("Choose All covered entities or enter a legal entity name.")
        if key in seen:
            raise ValueError("Each entity can have only one policy number for this product.")
        if len(source_numbers(item.policy_number)) != 1 or _SEPARATORS.search(item.policy_number):
            raise ValueError(
                "Enter one issued policy number per assignment; "
                "placeholders are not policy numbers."
            )
        seen.add(key)
        result.append(item.model_dump())
    return result


def scalar_number(mappings: list[dict[str, Any]]) -> str | None:
    """The compatibility scalar is safe only for an explicit product-wide number."""
    if len(mappings) == 1 and mappings[0]["entity"] is None:
        return str(mappings[0]["policy_number"])
    return None


def require_assignment_removal_permission(old: Any, new: Any, role: str) -> None:
    if (
        role not in FIRM_OWNER_ROLES and isinstance(old, list)
        and old and (not isinstance(new, list) or len(new) < len(old))
    ):
        raise HTTPException(
            403, "Only firm administrators may remove saved policy-number assignments."
        )


def member_entity(attrs: dict[str, Any] | None) -> str | None:
    return first_value(attrs or {}, ("entity", "company", "subsidiary"))


@dataclass(frozen=True)
class PolicyNumberResolution:
    number: str | None
    status: str
    entity: str | None = None


def resolve_policy_number(term: PolicyTerms | None, entity: str | None) -> PolicyNumberResolution:
    if term is None:
        return PolicyNumberResolution(None, "Not assigned")
    if term.policy_number_mappings is not None:
        try:
            mappings = validate_assignments(term.policy_number_mappings)
        except ValueError:
            return PolicyNumberResolution(None, "Needs review")
        if not mappings:
            return PolicyNumberResolution(None, "Not assigned")
        key = entity_key(entity)
        for item in mappings:
            if item["entity"] is not None and key and entity_key(item["entity"]) == key:
                return PolicyNumberResolution(
                    item["policy_number"], "Entity assignment", item["entity"]
                )
        default = next((item for item in mappings if item["entity"] is None), None)
        if default:
            return PolicyNumberResolution(default["policy_number"], "All covered entities")
        return PolicyNumberResolution(None, "Entity not assigned" if key else "Entity missing")
    numbers = source_numbers(term.policy_number)
    if len(numbers) == 1 and len(numbers[0]) <= 64:
        return PolicyNumberResolution(numbers[0], "Legacy product setting")
    return PolicyNumberResolution(None, "Needs review" if numbers else "Not assigned")


def placement_policy_numbers(term: PolicyTerms | None) -> str:
    """A placement covers the product, so print each entity/number association."""
    if term is not None and term.policy_number_mappings is not None:
        try:
            mappings = validate_assignments(term.policy_number_mappings)
        except ValueError:
            return "Needs review: policy-number assignments"
        return "\n".join(
            f"{item['entity'] or 'All covered entities'}: {item['policy_number']}"
            for item in mappings
        ) or "Not assigned"
    resolution = resolve_policy_number(term, None)
    return resolution.number or resolution.status
