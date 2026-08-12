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
    row can't be distinguished. Used as the composite fallback when there's no
    NRIC. Requires at least a name OR a DOB — relationship alone is too coarse
    (two 'Child' rows with no name/DOB would collapse into one, silently dropping
    a real dependant), so such rows are treated as unidentifiable and never
    deduped."""
    name = (first_value(attrs or {}, DEP_NAME_KEYS) or "").strip().lower()
    dob = iso_date(first_value(attrs or {}, ("date_of_birth", "dob"))) or ""
    rel = (first_value(attrs or {}, REL_KEYS) or "").strip().lower()
    if not (name or dob):
        return None
    return f"{name}|{dob}|{rel}"


def employee_nric(attrs: dict[str, Any] | None) -> str | None:
    """Normalized NRIC/FIN for an employee row, or None when absent."""
    return normalize_nric(first_value(attrs or {}, EMPLOYEE_ID_KEYS))


def dependant_nric(attrs: dict[str, Any] | None) -> str | None:
    """Normalized NRIC/FIN for a dependant row, or None when absent."""
    return normalize_nric(first_value(attrs or {}, DEPENDANT_ID_KEYS))


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
    nric: str | None = None,
) -> list[str]:
    """Every identity key a dependant row could collide on.

    **A dependant's identity is scoped to the employee who sponsors them, and
    that is the whole rule.** The same child genuinely appears twice when both
    parents work here — two coverage lines for one human, which is a fact about
    the payroll, not a duplicate row. A globally-unique NRIC key made the second
    parent's row unimportable: 74 of STM's 4,867 dependants were dropped as
    "Repeated in this file", and the child silently ended up under whichever
    parent the file happened to list first. Dual coverage is DETECTED and
    reviewed (``services/dual_coverage.py``), never prevented at the door.

    - ``nric:<emp>|<n>`` — the durable key, scoped to the sponsor.
    - ``comp:<emp>|<sig>`` — the employee-scoped composite (name+DOB+relationship),
      for rows that withhold an NRIC.
    - ``nric:-|<n>`` / ``dep:<sig>`` — the employee-AGNOSTIC pair, emitted for
      incoming rows and for EXISTING dependants that are currently UNLINKED.
      They bridge one case only: a dependant first stored unlinked, re-uploaded
      once its employee exists (the scoped keys would differ and duplicate it).
      Linked existing rows omit them, so two families' dependants never
      false-match. **Callers deduping WITHIN one file must pass
      ``include_agnostic=False``** — the bridge keys are identical for both
      parents, which is the collision this scoping exists to remove.

    ``nric`` overrides the value read from ``attrs`` — existing rows carry the
    canonical form in ``national_id_normalized``, which a row stamped by the
    portal has when its attributes do not.
    """
    keys: list[str] = []
    found = nric or dependant_nric(attrs)
    scope = employee_id or "-"
    if found:
        keys.append(f"nric:{scope}|{found}")
        if include_agnostic and employee_id:
            keys.append(f"nric:-|{found}")
    sig = _dep_signature(attrs)
    if sig:
        keys.append(f"comp:{scope}|{sig}")
        if include_agnostic:
            keys.append(f"dep:{sig}")
    return keys
