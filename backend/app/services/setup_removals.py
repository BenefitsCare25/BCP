"""Saved-entry removals in a product-setup write are firm-owner actions.

The setup form wraps every Remove of a saved benefit row, sub-benefit, limit
(shared or plan-specific), schedule column, endorsement, claim-limit setting or
co-payment field in a firm-owner guard. Save and confirm carry whole answer sections, so the API
enforces the same rule by diffing the incoming sections against the stored
answers (AGENTS.md broker portal permissions). The category cards' rate tiers
travel through PATCH /categories as a whole ``plan_assignments`` dict and are
diffed the same way.

The form also rewrites some of these entries on its own, so the diff ignores
exactly those changes rather than treating them as removals:

* ``reconcileColumns`` drops overall plan limits keyed by a plan code the
  answers no longer carry;
* ``setItemKind`` moves co-payment fields between a row's shared
  ``properties`` and its per-column ``column_properties`` (the keys survive);
* a channel (co-payment) row's claim-limit setting is rebuilt from its yearly
  cap and claim types, and is dropped once the column has no cap left;
* removing a column, or a whole row, strips that column's or row's own entries
  (those removals are checked as the column or row itself).
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.core.auth import FIRM_OWNER_ROLES
from app.services.member_schedule import is_absent_value

# Channel (outpatient co-payment) rows carry their yearly cap per column here.
_YEARLY_CAP_KEY = "per_policy_year"
_LIST_ENTRY_LABELS = {"insured": "an insured entity"}


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _entries(section: Any, key: str, ident: str) -> dict[str, dict[str, Any]]:
    rows = section.get(key) if isinstance(section, dict) else None
    return {
        str(row[ident]): row
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and row.get(ident)
    }


def _limit_total(item: dict[str, Any]) -> int:
    subs = item.get("sub_items")
    return _count(item.get("limits")) + sum(
        _count(sub.get("limits"))
        for sub in (subs if isinstance(subs, list) else [])
        if isinstance(sub, dict)
    )


def _column_limit_removed(old: dict[str, Any], new: dict[str, Any], columns: set[str]) -> bool:
    """A column's own (plan-specific) limit list lost rows, or was dropped so the
    column falls back to the shared list ("Use Plan 1's limits for every plan").
    Overwriting a list's values without shortening it is an edit."""
    kept = _mapping(new.get("column_limits"))
    return any(
        column_id in columns and _count(kept.get(column_id)) < _count(limits)
        for column_id, limits in _mapping(old.get("column_limits")).items()
    )


def _field_keys(item: dict[str, Any], columns: set[str]) -> set[str]:
    """Co-payment / qualifier field keys a row holds: its shared ``properties``
    plus those of each column that exists on both sides of the write."""
    keys = set(_mapping(item.get("properties")))
    per_column = _mapping(item.get("column_properties"))
    for column_id in columns:
        keys |= set(_mapping(per_column.get(column_id)))
    return keys


def _yearly_cap_absent(item: dict[str, Any], column_id: str) -> bool:
    """No yearly cap left in this column (mirror of the form's ``copayValue``)."""
    value = _mapping(_mapping(item.get("column_properties")).get(column_id)).get(_YEARLY_CAP_KEY)
    if value is None:
        value = _mapping(item.get("properties")).get(_YEARLY_CAP_KEY)
    return is_absent_value(value)


def _claim_limit_removed(old: dict[str, Any], new: dict[str, Any], columns: set[str]) -> bool:
    kept = _mapping(new.get("claim_limits"))
    channel = new.get("kind") == "copay"
    return any(
        column_id in columns
        and isinstance(setting, dict)
        and not isinstance(kept.get(column_id), dict)
        and not (channel and _yearly_cap_absent(new, column_id))
        for column_id, setting in _mapping(old.get("claim_limits")).items()
    )


def _plan_codes(plans: Any) -> set[str]:
    return {
        str(plan["code"])
        for plan in (plans if isinstance(plans, list) else [])
        if isinstance(plan, dict) and plan.get("code") is not None
    }


def _plan_limit_removed(old_sob: Any, new_sob: Any, plan_codes: set[str]) -> bool:
    kept = _mapping(_mapping(new_sob).get("plan_claim_limits"))
    return any(
        code in plan_codes and isinstance(setting, dict) and not isinstance(kept.get(code), dict)
        for code, setting in _mapping(_mapping(old_sob).get("plan_claim_limits")).items()
    )


def _sob_removals(old_sob: Any, new_sob: Any, plan_codes: set[str]) -> list[str]:
    removed: list[str] = []
    new_items = _entries(new_sob, "items", "uid")
    old_columns = set(_entries(old_sob, "columns", "id"))
    new_columns = set(_entries(new_sob, "columns", "id"))
    shared_columns = old_columns & new_columns
    for uid, item in _entries(old_sob, "items", "uid").items():
        name = str(item.get("name") or "").strip() or "unnamed row"
        kept = new_items.get(uid)
        if kept is None:
            removed.append(f"benefit {name!r}")
            continue
        if _count(kept.get("sub_items")) < _count(item.get("sub_items")):
            removed.append(f"a sub-benefit of {name!r}")
        elif _limit_total(kept) < _limit_total(item):
            removed.append(f"a limit of {name!r}")
        if _column_limit_removed(item, kept, shared_columns):
            removed.append(f"a plan-specific limit of {name!r}")
        if _field_keys(item, shared_columns) - _field_keys(kept, shared_columns):
            removed.append(f"a co-payment field of {name!r}")
        if _claim_limit_removed(item, kept, shared_columns):
            removed.append(f"a claim limit of {name!r}")
    if old_columns - new_columns:
        removed.append("a schedule column")
    if _plan_limit_removed(old_sob, new_sob, plan_codes):
        removed.append("an overall plan limit")
    return removed


def removed_setup_entries(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Saved entries the incoming answers drop. Sections the body omits are kept
    by the shallow merge, so only sections it carries are compared."""
    plan_codes = _plan_codes(new["plans"] if "plans" in new else old.get("plans"))
    removed = (
        _sob_removals(old.get("sob"), new["sob"], plan_codes) if "sob" in new else []
    )
    if "endorsements" in new and (
        _count(new["endorsements"]) < _count(old.get("endorsements"))
    ):
        removed.append("an endorsement")
    return removed


def removed_assignment_entries(old: Any, new: Any) -> list[str]:
    """Saved entries a category's incoming ``plan_assignments`` drops: rate
    tiers by code, and entries of its list fields (insured entities)."""
    before, after = _mapping(old), _mapping(new)
    kept_tiers = _mapping(after.get("rate_tiers"))
    removed = [
        f"rate tier {code!r}"
        for code in _mapping(before.get("rate_tiers"))
        if code not in kept_tiers
    ]
    removed.extend(
        _LIST_ENTRY_LABELS.get(key, f"an entry of {key!r}")
        for key, value in before.items()
        if isinstance(value, list) and _count(after.get(key)) < len(value)
    )
    return removed


def refuse_removals(removed: list[str], subject: str) -> None:
    """403 naming the first few saved ``subject`` entries a non-owner dropped."""
    if removed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Only firm administrators may remove saved {subject}: "
            + ", ".join(removed[:3])
            + (" and more." if len(removed) > 3 else "."),
        )


def require_setup_removal_permission(
    old: dict[str, Any] | None, new: dict[str, Any], role: str
) -> None:
    if role in FIRM_OWNER_ROLES or not isinstance(old, dict):
        return
    refuse_removals(removed_setup_entries(old, new), "setup entries")
