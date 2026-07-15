"""Identity keys + duplicate detection for roster / dependant uploads.

Identity precedence (locked by the roster-setup plan):
- Employee:  normalized NRIC/FIN  → else staff_id.
- Dependant: normalized NRIC/FIN  → else (employee link + name + DOB).

The normalized NRIC is the durable person key across re-uploads and re-hires;
staff_id / the composite are fallbacks for rows that withhold an NRIC. Callers
use these keys to (a) skip-and-report duplicates on plain upload and (b) resolve
an existing record for an ADC Change/Delete.
"""
from __future__ import annotations

from typing import Any

from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    EMPLOYEE_ID_KEYS,
    REL_KEYS,
    first_value,
    iso_date,
    normalize_nric,
)

DEP_NAME_KEYS = ("dependant_name", "name", "full_name")


def _dep_signature(attrs: dict[str, Any] | None) -> str | None:
    """Stable name+DOB+relationship signature for a dependant, or None when the
    row carries none of those (genuinely unidentifiable). Used as the composite
    fallback when there's no NRIC — including relationship/DOB means a row with,
    say, only 'Child / 2015-03-01' still keys instead of silently doubling."""
    name = (first_value(attrs or {}, DEP_NAME_KEYS) or "").strip().lower()
    dob = iso_date(first_value(attrs or {}, ("date_of_birth", "dob"))) or ""
    rel = (first_value(attrs or {}, REL_KEYS) or "").strip().lower()
    if not (name or dob or rel):
        return None
    return f"{name}|{dob}|{rel}"


def employee_nric(attrs: dict[str, Any] | None) -> str | None:
    """Normalized NRIC/FIN for an employee row, or None when absent."""
    return normalize_nric(first_value(attrs or {}, EMPLOYEE_ID_KEYS))


def dependant_nric(attrs: dict[str, Any] | None) -> str | None:
    """Normalized NRIC/FIN for a dependant row, or None when absent."""
    return normalize_nric(first_value(attrs or {}, DEPENDANT_ID_KEYS))


def employee_key(attrs: dict[str, Any] | None, staff_id: str | None) -> str | None:
    """Dedup key for an employee: ``nric:<n>`` else ``staff:<id>`` (lowercased).

    None only when a row has neither an NRIC nor a staff_id (unidentifiable —
    the parser already drops such rows before this point).
    """
    nric = employee_nric(attrs)
    if nric:
        return f"nric:{nric}"
    if staff_id and str(staff_id).strip():
        return f"staff:{str(staff_id).strip().lower()}"
    return None


def dependant_key(
    attrs: dict[str, Any] | None, employee_id: str | None
) -> str | None:
    """Dedup key for a dependant: ``nric:<n>`` else a composite of the linked
    employee + name + DOB + relationship. None when there's nothing to key on."""
    return next(iter(dependant_candidate_keys(attrs, employee_id)), None)


def employee_candidate_keys(
    attrs: dict[str, Any] | None, staff_id: str | None
) -> list[str]:
    """Every identity key an employee row could collide on — NRIC key AND staff
    key when both are present. Matching on the union catches the case where an
    existing row was created staff-only and is re-uploaded with an NRIC (or vice
    versa), which a single primary key would miss and duplicate."""
    keys: list[str] = []
    nric = employee_nric(attrs)
    if nric:
        keys.append(f"nric:{nric}")
    if staff_id and str(staff_id).strip():
        keys.append(f"staff:{str(staff_id).strip().lower()}")
    return keys


def dependant_candidate_keys(
    attrs: dict[str, Any] | None,
    employee_id: str | None,
    *,
    include_agnostic: bool = True,
) -> list[str]:
    """Every identity key a dependant row could collide on.

    - ``nric:<n>`` when an NRIC is present (the durable key).
    - ``comp:<emp>|<sig>`` — the employee-scoped composite (keeps two families'
      NRIC-less dependants that share a name+DOB from colliding).
    - ``dep:<sig>`` — an *employee-agnostic* composite, emitted for incoming
      rows and for EXISTING dependants that are currently UNLINKED. This bridges
      the case where a dependant was first stored unlinked and is re-uploaded
      after its employee exists (the two emp-scoped composites would differ);
      linked existing rows omit it, so linked dependants of different employees
      never false-match on name+DOB alone.
    """
    keys: list[str] = []
    nric = dependant_nric(attrs)
    if nric:
        keys.append(f"nric:{nric}")
    sig = _dep_signature(attrs)
    if sig:
        keys.append(f"comp:{employee_id or '-'}|{sig}")
        if include_agnostic:
            keys.append(f"dep:{sig}")
    return keys
