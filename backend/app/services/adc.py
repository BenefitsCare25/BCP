"""Roster movement engine — Additions / Changes / Deletions, DERIVED.

There is no hand-marked ``Action`` column and no separate movement template.
The broker uploads the **member listing** (the same file
``member_listing_template.py`` hands them, pre-filled with everyone on file) and
this module diffs it against the roster:

- a row whose identity matches nothing on file      → **addition**
- a row that matches, with fields that differ       → **change**
- a row carrying a past leaving date                → **deletion** (explicit)
- someone on file whose identity appears nowhere in the sheet → **missing**

The `Action` column was manual work restating what the diff can compute, and
its absence is what makes the listing template honest: that file's own docstring
has always said it "doubles as an update template", but the upload path resolved
each identity and then `continue`d, so every edit to an existing person was
reported as a skipped duplicate and thrown away.

**`missing` is NOT a deletion, and that distinction is the safety property of
this module.** In the old model a termination was an explicit mark; here the
only signal is absence — and a partial file (new joiners only, one entity, a
filtered HR export) is indistinguishable from a full census that legitimately
dropped people. So missing rows are detected, listed and counted, and are
terminated only when the caller passes ``terminate_missing`` (the preview's
opt-in tick). `roster_total` rides the preview so the UI can shout when the
proportion says "partial export" rather than "leavers".

A kind is in scope for missing-detection only when its sheet actually PARSED
rows. An employees-only workbook must never conclude that every dependant left.

Flow (mirrors bulk_plan_update's preview/apply so the dry-run can't diverge):
- ``evaluate_listing`` — parse + resolve + diff, NO mutation. Powers the preview
  and is re-run by apply.
- ``apply_listing`` — insert Adds, merge Changes, soft-terminate Deletes (and
  Missing when opted in), then re-match + re-assign flex. Per-row audit.

Identity for resolution: normalized NRIC → else staff_id (employees) / employee
link + name + DOB (dependants) — the same keys as upload dedup
(services/roster_dedup), so the three paths can never disagree about who a row
is.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.models import Dependant, Employee, EmployeeAttributeSchema
from app.models.dependant import (
    DEPENDANT_STATUS_ACTIVE,
    DEPENDANT_STATUS_TERMINATED,
)
from app.models.employee import (
    EMPLOYEE_STATUS_ACTIVE,
    EMPLOYEE_STATUS_TERMINATED,
)
from app.schemas.adc import (
    AdcApplyResult,
    AdcFieldDiff,
    AdcIssue,
    AdcOp,
    AdcPreview,
    AdcWarning,
)
from app.services.derivation_engine import derive
from app.services.flex_assignment import assign_flex_safe
from app.services.matching_engine import match_policy_year
from app.services.roster_attributes import (
    first_value,
    is_valid_sg_nric,
    looks_like_sg_nric,
    mask_nric,
    parse_dob,
)
from app.services.roster_dedup import (
    dependant_candidate_keys,
    dependant_nric,
    employee_candidate_keys,
    employee_nric,
)
from app.services.roster_parser import (
    DependantRecord,
    EmployeeRecord,
    _read_sheet,
    has_sheet,
    parse_dependant_workbook,
    parse_employee_workbook,
)

# Columns that state a leaving date in the listing itself. A date at or before
# today terminates the row explicitly — which is strictly better than inferring
# it from absence, because it carries the broker's own effective date (the one
# thing the retired Action column contributed that a diff cannot).
_EMP_LEAVING_KEYS = ("last_day_of_service", "termination_date")
_DEP_LEAVING_KEYS = ("termination_date", "last_day_of_service")


# ── Plan ────────────────────────────────────────────────────────────────────


@dataclass
class _EmpAdd:
    row_no: int
    attrs: dict[str, Any]
    staff_id: str | None
    employee_name: str | None
    nric: str | None


@dataclass
class _EmpChange:
    row_no: int
    target: Employee
    merged: dict[str, Any]
    employee_name: str | None
    nric: str | None
    diffs: list[AdcFieldDiff]


@dataclass
class _EmpDelete:
    row_no: int
    target: Employee
    effective: date
    merged: dict[str, Any] | None = None


@dataclass
class _DepAdd:
    row_no: int
    attrs: dict[str, Any]
    employee_id: str | None
    link_method: str
    nric: str | None


@dataclass
class _DepChange:
    row_no: int
    target: Dependant
    merged: dict[str, Any]
    employee_id: str | None
    nric: str | None
    diffs: list[AdcFieldDiff]


@dataclass
class _DepDelete:
    row_no: int
    target: Dependant
    effective: date
    merged: dict[str, Any] | None = None


@dataclass
class _Plan:
    emp_add: list[_EmpAdd] = field(default_factory=list)
    emp_change: list[_EmpChange] = field(default_factory=list)
    emp_delete: list[_EmpDelete] = field(default_factory=list)
    dep_add: list[_DepAdd] = field(default_factory=list)
    dep_change: list[_DepChange] = field(default_factory=list)
    dep_delete: list[_DepDelete] = field(default_factory=list)
    # Absent from the file — terminated ONLY on the caller's explicit opt-in.
    emp_missing: list[Employee] = field(default_factory=list)
    dep_missing: list[Dependant] = field(default_factory=list)
    issues: list[AdcIssue] = field(default_factory=list)
    # Rows that ARE applied but look wrong (see AdcWarning).
    warnings: list[AdcWarning] = field(default_factory=list)
    unchanged: int = 0
    # Matched rows already terminated. An upload never resurrects anyone (and
    # never duplicates them either), but the count is surfaced so the no-op is
    # visible rather than silent.
    already_terminated: int = 0
    roster_total: int = 0
    dropped_rows: int = 0


_FLOAT_ARTIFACT = re.compile(r"^(-?\d+)\.0$")


def _canon(value: Any) -> str:
    """A stored value and its round-trip, compared on equal terms.

    Exporting the roster and reading it straight back must propose NOTHING, and
    two normalizations stand in the way — both of them applied by the parser on
    the way IN but not to the value already stored:

    - `_normalize_name` collapses commas, so a stored "Lee Puay Tin, Ammie"
      re-reads as "Lee Puay Tin Ammie";
    - `_normalize_code` strips Excel's float tail, so a stored mobile of
      91234567.0 re-reads as "91234567".

    Neither is a change the broker made. Left uncanonicalised they put 17 name
    edits and a column of phone numbers in front of a broker who changed one
    salary — and the real edit is what gets lost in that list. Stored values are
    NOT rewritten by this; the comparison simply stops reporting the artifact.
    """
    text = str(value).strip()
    text = _FLOAT_ARTIFACT.sub(r"\1", text)
    return re.sub(r"[,\s]+", " ", text)


def _merge_and_diff(existing: dict[str, Any], new: dict) -> tuple[dict, list[AdcFieldDiff]]:
    """Overlay non-empty ``new`` values onto ``existing``; return the merged dict
    and the list of fields that actually changed. Empty cells never clear a
    field — a pre-filled template blank means 'unchanged', not 'delete'."""
    merged = dict(existing or {})
    diffs: list[AdcFieldDiff] = []
    for key, value in (new or {}).items():
        if value in (None, ""):
            continue
        old = existing.get(key) if existing else None
        # `insurer_member_ids` is a DICT and must merge key-by-key. The template
        # only writes a column per insurer configured on the current year, so
        # overwriting it wholesale permanently drops an id held under an insurer
        # that has since been reconfigured — or under a differently-spelled name
        # the original roster's "<Insurer> Member ID" header carried.
        if isinstance(value, dict) and isinstance(old, dict):
            combined = {**old, **{k: v for k, v in value.items() if v not in (None, "")}}
            if combined != old:
                diffs.append(
                    AdcFieldDiff(field=key, old=str(old), new=str(combined))
                )
                merged[key] = combined
            continue
        if old is None or _canon(old) != _canon(value):
            diffs.append(
                AdcFieldDiff(
                    field=key,
                    old=None if old is None else str(old),
                    new=str(value),
                )
            )
            merged[key] = value
    return merged, diffs


def _leaving_date(
    attrs: dict[str, Any],
    keys: tuple[str, ...],
    stored: dict[str, Any] | None = None,
) -> date | None:
    """A NEWLY stated leaving date that has already passed.

    Two rules, both of which stop a round-trip becoming a termination:

    - A FUTURE date is someone on notice — still covered, still on the insurer
      listing — so it is an ordinary field change and terminates on a later
      upload, never early.
    - A date the roster ALREADY holds is not a statement, it is the export
      coming back. `Last Day of Service` is a column of the listing template,
      so an active employee carrying a stale past date would be proposed for
      termination every time anyone downloaded the file and changed one salary
      — exactly the invariant `_canon` exists to protect. Only a date that is
      new or different terminates.
    """
    parsed = parse_dob(first_value(attrs, keys))
    if parsed is None or parsed > date.today():
        return None
    if stored is not None and parse_dob(first_value(stored, keys)) == parsed:
        return None
    return parsed


# ── Evaluation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Match:
    target: Any | None = None
    conflict: bool = False
    exhausted: bool = False
    #: Every roster record this row could have meant, whatever the outcome.
    #: A row that is SKIPPED still names these people, so they are not
    #: "missing from the file" — see `_plan_employees`.
    candidates: frozenset[str] = frozenset()


def _resolve(
    by_key: dict[str, list[Any]],
    keys: list[str],
    consumed: set[str],
) -> _Match:
    """Match one uploaded row to a roster record it hasn't already claimed.

    Rosters really do carry two records with an IDENTICAL identity — CDL has two
    dependants sharing employee, name, DOB and relationship, and no NRIC to tell
    them apart. Indexing one record per key made the second unmatchable: both
    file rows resolved to the first, and the second was reported as **missing
    from a file it was actually in** — one tick away from terminating a live
    dependant. So each key holds every record that carries it, and a row takes
    the first one no earlier row has consumed.

    ``exhausted`` = the key exists but every record under it is already claimed,
    i.e. the file repeats a person more times than the roster holds them. That
    is a repeated row, NOT a new person: adding it would duplicate someone who
    is already on file.
    """
    matched = [(k, by_key[k]) for k in keys if k in by_key]
    if not matched:
        return _Match()
    every = frozenset(r.id for _, recs in matched for r in recs)
    # A conflict is two DIFFERENT keys pointing at different records — a staff
    # id and an NRIC that belong to two people, which is what a mistyped NRIC
    # looks like. Several records under ONE key are duplicates, not a conflict:
    # they are indistinguishable by construction, so the row simply takes one.
    first_ids = {r.id for r in matched[0][1]}
    if any(first_ids.isdisjoint({r.id for r in recs}) for _, recs in matched[1:]):
        return _Match(conflict=True, candidates=every)

    seen: set[str] = set()
    candidates = [
        r
        for _, recs in matched
        for r in recs
        if not (r.id in seen or seen.add(r.id))
    ]
    free = [r for r in candidates if r.id not in consumed]
    if not free:
        return _Match(exhausted=True, candidates=every)
    return _Match(target=free[0], candidates=every)


def _plan_employees(
    plan: _Plan,
    records: list[EmployeeRecord],
    employees: list[Employee],
) -> None:
    # Index off the STORED identity columns, not a re-derivation from
    # attribute_values — `national_id_normalized` is the normalized NRIC the
    # rest of the system resolves on, and an employee whose roster row never
    # carried an id_no column would otherwise index on staff id alone.
    by_key: dict[str, list[Employee]] = {}
    for e in employees:
        if e.national_id_normalized:
            by_key.setdefault(f"nric:{e.national_id_normalized}", []).append(e)
        if e.staff_id and e.staff_id.strip():
            by_key.setdefault(f"staff:{e.staff_id.strip().lower()}", []).append(e)

    seen_targets: set[str] = set()
    # Everyone any row NAMED, including rows that were skipped as issues. Only
    # people in neither set are genuinely absent from the file.
    touched: set[str] = set()
    seen_file_keys: set[str] = set()
    # NRICs this run would newly assign → the row that claimed each. The DB has
    # no unique constraint to catch two rows writing one fresh NRIC onto two
    # different people, so the guard has to live here.
    run_nric: dict[str, str] = {}
    for rec in records:
        # An employee's own name belongs to the COLUMN, not attribute_values.
        # `parse_employee_workbook` reads it with `.get`, so it stays in attrs
        # too — and then it is diffed twice: once here against a stale attrs
        # copy that no write path updates, once against the real column. The
        # retired ADC parser popped it for exactly this reason.
        attrs = {k: v for k, v in rec.attributes.items() if k != "employee_name"}
        keys = employee_candidate_keys(attrs, rec.staff_id)
        if not keys:
            plan.issues.append(
                AdcIssue(row=rec.row, record_type="employee",
                         message="No Staff ID or NRIC on this row — skipped")
            )
            continue
        # A row identifies by NRIC *and* staff id. When those resolve to two
        # DIFFERENT people the row is a conflict, not a movement — merging it
        # into whichever key happened to be checked first would rewrite the
        # wrong person, and this is exactly what a mistyped NRIC looks like.
        match = _resolve(by_key, keys, seen_targets)
        # A skipped row still NAMES these people, so they are not absent from
        # the file. Without this, one mistyped NRIC put BOTH the intended
        # employee and the NRIC's real owner into "Not in this file" — which
        # says they are "not named anywhere in this upload" — and a single tick
        # would have soft-terminated two active employees.
        touched |= match.candidates
        target = match.target
        if match.conflict:
            plan.issues.append(
                AdcIssue(row=rec.row, record_type="employee",
                         message=("Staff ID and NRIC on this row belong to two "
                                  "different employees — skipped"))
            )
            continue
        if match.exhausted:
            plan.issues.append(
                AdcIssue(row=rec.row, record_type="employee",
                         message="Repeated in this file — skipped")
            )
            continue
        # The repeat check comes FIRST for a row that matches nobody: two copies
        # of one new hire are a repeated row, not two people fighting over an
        # NRIC, and reporting the second message would send the broker looking
        # for a collision that doesn't exist.
        if target is None and any(k in seen_file_keys for k in keys):
            plan.issues.append(
                AdcIssue(row=rec.row, record_type="employee",
                         message="Repeated in this file — skipped")
            )
            continue
        nric = employee_nric(attrs)
        if nric:
            # Keyed on IDENTITY, never the row number: an addition and a change
            # both claiming one fresh NRIC must collide, while the same person
            # appearing once must not collide with itself.
            row_key = target.id if target is not None else keys[0]
            claimed_by = run_nric.get(nric)
            if claimed_by is not None and claimed_by != row_key:
                plan.issues.append(
                    AdcIssue(row=rec.row, record_type="employee",
                             message=("NRIC assigned to two different employees "
                                      "in this file — skipped"))
                )
                continue
            run_nric[nric] = row_key
            # Advisory, never a skip: a checksum failure is evidence of a
            # transcription typo, not proof, and the roster is the customer's
            # record. The masked form is enough to find the row.
            if looks_like_sg_nric(nric) and not is_valid_sg_nric(nric):
                plan.warnings.append(
                    AdcWarning(
                        row=rec.row, record_type="employee",
                        message=(f"Identification number {mask_nric(nric)} fails its "
                                 "NRIC/FIN checksum — check for a typo"),
                    )
                )
        if target is None:
            seen_file_keys.update(keys)
            plan.emp_add.append(
                _EmpAdd(rec.row, attrs, rec.staff_id, rec.employee_name, nric)
            )
            continue

        seen_targets.add(target.id)
        if target.status == EMPLOYEE_STATUS_TERMINATED:
            plan.already_terminated += 1
            continue

        merged, diffs = _merge_and_diff(target.attribute_values or {}, attrs)
        # Canonicalised on both sides, and the STORED value wins when they
        # differ only by an artifact — rewriting it would make every round-trip
        # a write.
        name = target.employee_name
        if rec.employee_name and _canon(rec.employee_name) != _canon(
            target.employee_name or ""
        ):
            name = rec.employee_name
            diffs.append(
                AdcFieldDiff(field="employee_name", old=target.employee_name, new=name)
            )
        leaving = _leaving_date(attrs, _EMP_LEAVING_KEYS,
                                target.attribute_values or {})
        if leaving is not None:
            plan.emp_delete.append(_EmpDelete(rec.row, target, leaving, merged))
        elif diffs:
            plan.emp_change.append(
                _EmpChange(rec.row, target, merged, name, nric, diffs)
            )
        else:
            plan.unchanged += 1

    # Missing = on file, active, and named nowhere in a sheet that DID parse.
    if records:
        plan.emp_missing = [
            e
            for e in employees
            if e.status == EMPLOYEE_STATUS_ACTIVE
            and e.id not in seen_targets
            and e.id not in touched
        ]


def _plan_dependants(
    plan: _Plan,
    records: list[DependantRecord],
    dependants: list[Dependant],
    employees: list[Employee],
) -> None:
    by_staff = {
        e.staff_id.strip().lower(): e
        for e in employees
        if e.staff_id and e.staff_id.strip()
    }
    # Names shared by 2+ employees are dropped so an ambiguous name can't
    # silently link a dependant to the wrong sponsor.
    name_groups: dict[str, list[Employee]] = {}
    for e in employees:
        nm = (e.employee_name or "").strip().lower()
        if nm:
            name_groups.setdefault(nm, []).append(e)
    by_name = {nm: emps[0] for nm, emps in name_groups.items() if len(emps) == 1}

    # Every record under its key, not just the first — see `_resolve`. This is
    # the side that actually bit: two identical NRIC-less dependants of one
    # employee are indistinguishable, and keeping one made the other
    # permanently "missing".
    by_key: dict[str, list[Dependant]] = {}
    for d in dependants:
        if d.national_id_normalized:
            by_key.setdefault(f"nric:{d.national_id_normalized}", []).append(d)
        # The employee-agnostic (dep:) key is emitted only for rows that are
        # themselves unlinked, so a linked dependant can't false-match another
        # family's NRIC-less dependant sharing a name + DOB.
        for key in dependant_candidate_keys(
            d.attribute_values, d.employee_id, include_agnostic=d.employee_id is None
        ):
            by_key.setdefault(key, []).append(d)

    seen_targets: set[str] = set()
    touched: set[str] = set()
    seen_file_keys: set[str] = set()
    # Same per-run NRIC guard the employee side carries: two rows resolving to
    # two different dependants while both claiming one fresh NRIC would silently
    # break the app-enforced uniqueness (`models/dependant.py`), and there is no
    # DB constraint behind it.
    run_nric: dict[str, str] = {}
    for rec in records:
        staff_key = (rec.employee_staff_id or "").strip().lower()
        name_key = (rec.employee_name or "").strip().lower()
        emp, method = None, "unlinked"
        if staff_key and staff_key in by_staff:
            emp, method = by_staff[staff_key], "staff_id"
        elif name_key and name_key in by_name:
            emp, method = by_name[name_key], "name"
        emp_id = emp.id if emp else None

        keys = dependant_candidate_keys(rec.attributes, emp_id)
        if not keys:
            plan.issues.append(
                AdcIssue(row=rec.row, record_type="dependant",
                         message="No NRIC, name or date of birth on this row — skipped")
            )
            continue
        match = _resolve(by_key, keys, seen_targets)
        # A skipped row still names these dependants — see `_plan_employees`.
        touched |= match.candidates
        target = match.target
        if match.conflict or match.exhausted:
            plan.issues.append(
                AdcIssue(
                    row=rec.row, record_type="dependant",
                    message=(
                        "Matches two different dependants — skipped"
                        if match.conflict
                        else "Repeated in this file — skipped"
                    ),
                )
            )
            continue
        nric = dependant_nric(rec.attributes)
        if nric:
            row_key = target.id if target is not None else keys[0]
            claimed_by = run_nric.get(nric)
            if claimed_by is not None and claimed_by != row_key:
                plan.issues.append(
                    AdcIssue(row=rec.row, record_type="dependant",
                             message=("NRIC assigned to two different dependants "
                                      "in this file — skipped"))
                )
                continue
            run_nric[nric] = row_key
            # Advisory, never a skip: a checksum failure is evidence of a
            # transcription typo, not proof, and the roster is the customer's
            # record. The masked form is enough to find the row.
            if looks_like_sg_nric(nric) and not is_valid_sg_nric(nric):
                plan.warnings.append(
                    AdcWarning(
                        row=rec.row, record_type="dependant",
                        message=(f"Identification number {mask_nric(nric)} fails its "
                                 "NRIC/FIN checksum — check for a typo"),
                    )
                )
        if target is None:
            if any(k in seen_file_keys for k in keys):
                plan.issues.append(
                    AdcIssue(row=rec.row, record_type="dependant",
                             message="Repeated in this file — skipped")
                )
                continue
            seen_file_keys.update(keys)
            plan.dep_add.append(
                _DepAdd(rec.row, rec.attributes, emp_id, method, nric)
            )
            continue

        seen_targets.add(target.id)
        if target.status == DEPENDANT_STATUS_TERMINATED:
            plan.already_terminated += 1
            continue

        merged, diffs = _merge_and_diff(target.attribute_values or {}, rec.attributes)
        leaving = _leaving_date(rec.attributes, _DEP_LEAVING_KEYS,
                                target.attribute_values or {})
        if leaving is not None:
            plan.dep_delete.append(_DepDelete(rec.row, target, leaving, merged))
        elif diffs:
            plan.dep_change.append(
                _DepChange(rec.row, target, merged, emp_id,
                           dependant_nric(rec.attributes), diffs)
            )
        else:
            plan.unchanged += 1

    if records:
        plan.dep_missing = [
            d
            for d in dependants
            if d.status == DEPENDANT_STATUS_ACTIVE
            and d.id not in seen_targets
            and d.id not in touched
        ]


def _data_rows(path: Path | str, preferred: str) -> int:
    """Non-blank data rows on the sheet a parser would read."""
    sheet = _read_sheet(path, preferred)
    if sheet is None or not sheet.rows:
        return 0
    return sum(
        1
        for row in sheet.rows[1:]
        if any(c is not None and str(c).strip() for c in row)
    )


def evaluate_listing(
    db: Session, policy_year_id: str, client_id: str, path: Path | str
) -> tuple[_Plan, AdcPreview]:
    """Diff an uploaded member listing against the roster. No mutation."""
    emp_records = parse_employee_workbook(path)
    dep_records = parse_dependant_workbook(path)

    # Rows the parsers DROPPED — an employee row with no Staff ID, a dependant
    # row naming only its sponsor. They are legitimately unusable, but a broker
    # whose file loses rows has to be told: silently importing 8 of 10 rows and
    # reporting success is how a roster goes quietly wrong.
    #
    # Gated on the sheet EXISTING, not on it having parsed rows — otherwise the
    # worst case reports zero: a Dependants sheet whose header is unrecognised
    # (or whose every row fails the dependant-column guard) yields no records,
    # and the whole sheet would vanish under "matches the roster exactly".
    dropped = max(0, _data_rows(path, "Employees") - len(emp_records))
    if has_sheet(path, "Dependants"):
        dropped += max(0, _data_rows(path, "Dependants") - len(dep_records))

    employees = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
            )
        ).scalars().all()
    )
    dependants = list(
        db.execute(
            select(Dependant).where(
                Dependant.client_id == client_id,
                Dependant.policy_year_id == policy_year_id,
            )
        ).scalars().all()
    )

    plan = _Plan()
    plan.dropped_rows = dropped
    _plan_employees(plan, emp_records, employees)
    _plan_dependants(plan, dep_records, dependants, employees)
    # The denominator for "is this a partial export?" — only the kinds this file
    # actually covers, so an employees-only upload isn't measured against a
    # roster that includes every dependant.
    plan.roster_total = (
        sum(1 for e in employees if e.status == EMPLOYEE_STATUS_ACTIVE)
        if emp_records
        else 0
    ) + (
        sum(1 for d in dependants if d.status == DEPENDANT_STATUS_ACTIVE)
        if dep_records
        else 0
    )
    return plan, _to_preview(plan)


def _dep_name(attrs: dict[str, Any] | None) -> str | None:
    return first_value(attrs or {}, ("dependant_name", "name"))


def _to_preview(plan: _Plan) -> AdcPreview:
    additions = [
        AdcOp(row=a.row_no, record_type="employee", name=a.employee_name,
              staff_id=a.staff_id, nric_masked=mask_nric(a.nric) or None)
        for a in plan.emp_add
    ] + [
        AdcOp(row=a.row_no, record_type="dependant", name=_dep_name(a.attrs),
              nric_masked=mask_nric(a.nric) or None)
        for a in plan.dep_add
    ]
    changes = [
        AdcOp(row=c.row_no, record_type="employee", name=c.employee_name,
              staff_id=c.target.staff_id, target_id=c.target.id,
              nric_masked=mask_nric(c.nric) or None, field_diffs=c.diffs)
        for c in plan.emp_change
    ] + [
        AdcOp(row=c.row_no, record_type="dependant", name=_dep_name(c.merged),
              target_id=c.target.id, nric_masked=mask_nric(c.nric) or None,
              field_diffs=c.diffs)
        for c in plan.dep_change
    ]
    deletions = [
        AdcOp(row=d.row_no, record_type="employee", name=d.target.employee_name,
              staff_id=d.target.staff_id, target_id=d.target.id,
              effective=d.effective.isoformat())
        for d in plan.emp_delete
    ] + [
        AdcOp(row=d.row_no, record_type="dependant",
              name=_dep_name(d.target.attribute_values),
              target_id=d.target.id, effective=d.effective.isoformat())
        for d in plan.dep_delete
    ]
    missing = [
        AdcOp(row=0, record_type="employee", name=e.employee_name,
              staff_id=e.staff_id, target_id=e.id)
        for e in plan.emp_missing
    ] + [
        AdcOp(row=0, record_type="dependant",
              name=_dep_name(d.attribute_values), target_id=d.id)
        for d in plan.dep_missing
    ]
    return AdcPreview(
        additions=additions,
        changes=changes,
        deletions=deletions,
        missing=missing,
        issues=plan.issues,
        warnings=plan.warnings,
        counts={
            "additions": len(additions),
            "changes": len(changes),
            "deletions": len(deletions),
            "missing": len(missing),
            "unchanged": plan.unchanged,
            "already_terminated": plan.already_terminated,
            "issues": len(plan.issues),
            "dropped_rows": plan.dropped_rows,
            "roster_total": plan.roster_total,
        },
    )


def missing_digest(plan: _Plan) -> str:
    """Stable fingerprint of exactly WHO the preview offered to terminate.

    `terminate_missing` is confirmed against a list the broker read, but apply
    re-runs the diff — so a dependant approved through the portal, or another
    broker's upload, between the tick and the confirm would silently change
    (and can only enlarge) the set that actually gets terminated. Apply compares
    this and refuses on drift, the same shape as `bulk_plan_update`'s
    `selection_digest`.
    """
    ids = sorted([e.id for e in plan.emp_missing] + [d.id for d in plan.dep_missing])
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:32]


def preview_listing(
    db: Session, policy_year_id: str, client_id: str, path: Path | str
) -> AdcPreview:
    plan, preview = evaluate_listing(db, policy_year_id, client_id, path)
    preview.missing_digest = missing_digest(plan)
    return preview


# ── Apply ───────────────────────────────────────────────────────────────────


class StaleListingPreview(Exception):
    """The set of people absent from the file moved between preview and apply."""


def apply_listing(
    db: Session,
    user: CurrentUser,
    policy_year_id: str,
    client_id: str,
    path: Path | str,
    *,
    terminate_missing: bool = False,
    expected_missing_digest: str | None = None,
) -> AdcApplyResult:
    """Re-evaluate then apply the listing atomically, then re-match + re-assign
    flex. Per-row audit; a mid-run failure rolls back the whole run.

    ``terminate_missing`` is the preview's opt-in: without it, people absent
    from the file are reported and left alone.
    """
    plan, _ = evaluate_listing(db, policy_year_id, client_id, path)

    # Only the terminations are gated: re-deriving adds and changes against the
    # newest roster is correct and wanted, but ending someone's cover must
    # happen to the exact people the broker read and ticked.
    if terminate_missing and expected_missing_digest is not None:
        current = missing_digest(plan)
        if current != expected_missing_digest:
            raise StaleListingPreview(
                "The roster changed since this file was previewed — re-upload it "
                "and review the terminations again."
            )

    schemas = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                (EmployeeAttributeSchema.client_id == client_id)
                | (EmployeeAttributeSchema.client_id.is_(None))
            )
        ).scalars()
    )

    added = changed = deleted = 0

    # Track newly-added employees so same-file dependant adds can link to them
    # (their sponsoring employee didn't exist when the plan was resolved).
    new_by_staff: dict[str, str] = {}
    new_by_name: dict[str, str] = {}
    for a in plan.emp_add:
        emp = Employee(
            client_id=client_id,
            policy_year_id=policy_year_id,
            staff_id=a.staff_id or "",
            employee_name=a.employee_name,
            attribute_values=a.attrs,
            derived_attribute_values=derive(a.attrs, schemas),
            national_id_normalized=a.nric,
            source="listing_sync",
        )
        db.add(emp)
        db.flush()  # assign the id so a same-file dependant can reference it
        if a.staff_id:
            new_by_staff.setdefault(a.staff_id.strip().lower(), emp.id)
        if a.employee_name:
            new_by_name.setdefault(a.employee_name.lower().strip(), emp.id)
        added += 1
    for c in plan.emp_change:
        c.target.attribute_values = c.merged
        c.target.employee_name = c.employee_name
        c.target.national_id_normalized = c.nric or c.target.national_id_normalized
        c.target.derived_attribute_values = derive(c.merged, schemas)
        write_audit(
            db, user, action="adc_change", entity_type="employee",
            entity_id=c.target.id,
            after={"diffs": [d.model_dump() for d in c.diffs]},
        )
        changed += 1
    for d in plan.emp_delete:
        if d.merged is not None:
            d.target.attribute_values = d.merged
            d.target.derived_attribute_values = derive(d.merged, schemas)
        d.target.status = EMPLOYEE_STATUS_TERMINATED
        d.target.terminated_effective = d.effective
        write_audit(
            db, user, action="adc_delete", entity_type="employee",
            entity_id=d.target.id,
            after={"effective": d.effective.isoformat(), "source": "leaving_date"},
        )
        deleted += 1

    for a in plan.dep_add:
        emp_id = a.employee_id
        link_method = a.link_method
        # Link to an employee added earlier in this same file, if not already linked.
        if emp_id is None:
            staff = str(a.attrs.get("employee_staff_id") or "").strip().lower()
            name = str(a.attrs.get("employee_name") or "").strip().lower()
            if staff and staff in new_by_staff:
                emp_id, link_method = new_by_staff[staff], "staff_id"
            elif name and name in new_by_name:
                emp_id, link_method = new_by_name[name], "name"
        db.add(
            Dependant(
                client_id=client_id,
                policy_year_id=policy_year_id,
                employee_id=emp_id,
                attribute_values=a.attrs,
                link_method=link_method,
                national_id_normalized=a.nric,
            )
        )
        added += 1
    for c in plan.dep_change:
        c.target.attribute_values = c.merged
        c.target.national_id_normalized = c.nric or c.target.national_id_normalized
        write_audit(
            db, user, action="adc_change", entity_type="dependant",
            entity_id=c.target.id,
            after={"diffs": [d.model_dump() for d in c.diffs]},
        )
        changed += 1
    for d in plan.dep_delete:
        if d.merged is not None:
            d.target.attribute_values = d.merged
        d.target.status = DEPENDANT_STATUS_TERMINATED
        d.target.terminated_effective = d.effective
        write_audit(
            db, user, action="adc_delete", entity_type="dependant",
            entity_id=d.target.id,
            after={"effective": d.effective.isoformat(), "source": "leaving_date"},
        )
        deleted += 1

    # Absence-driven terminations, only on the explicit opt-in. Audited with
    # their own source so the record says WHY someone was terminated — a stated
    # leaving date and "wasn't in the file" are very different evidence.
    missing_terminated = 0
    if terminate_missing:
        today = date.today()
        for emp in plan.emp_missing:
            emp.status = EMPLOYEE_STATUS_TERMINATED
            emp.terminated_effective = today
            write_audit(
                db, user, action="adc_delete", entity_type="employee",
                entity_id=emp.id,
                after={"effective": today.isoformat(), "source": "absent_from_listing"},
            )
            missing_terminated += 1
        for dep in plan.dep_missing:
            dep.status = DEPENDANT_STATUS_TERMINATED
            dep.terminated_effective = today
            write_audit(
                db, user, action="adc_delete", entity_type="dependant",
                entity_id=dep.id,
                after={"effective": today.isoformat(), "source": "absent_from_listing"},
            )
            missing_terminated += 1

    write_audit(
        db, user, action="adc_apply", entity_type="policy_year",
        entity_id=policy_year_id,
        after={"added": added, "changed": changed, "deleted": deleted,
               "missing_terminated": missing_terminated,
               "unchanged": plan.unchanged, "issues": len(plan.issues)},
    )
    db.commit()

    # Re-match + re-size flex for the (now changed) active roster. Best-effort,
    # like the upload path — a failure here must not undo the applied movement.
    rematched = 0
    flex_errors: list[str] = []
    try:
        summary = match_policy_year(db, policy_year_id, user)
        rematched = summary.employees_matched
        db.commit()
    except Exception:
        db.rollback()
        flex_errors.append("Re-matching failed; click 'Re-run matching' to retry.")
    assign_flex_safe(
        db, user, policy_year_id, client_id, trigger="auto_on_adc", errors=flex_errors
    )

    return AdcApplyResult(
        added=added,
        changed=changed,
        deleted=deleted,
        missing_terminated=missing_terminated,
        unchanged=plan.unchanged,
        rematched=rematched,
        issues=plan.issues,
        flex_errors=flex_errors,
    )
