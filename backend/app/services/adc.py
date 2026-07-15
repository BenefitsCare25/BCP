"""ADC (Additions / Deletions / Changes) roster movement engine.

Flow (mirrors bulk_plan_update's preview/apply so the dry-run can't diverge):
- ``build_adc_template_workbook`` — the current active roster round-tripped into
  an .xlsx with an ``Action`` column (blank), one sheet each for employees and
  dependants. Full NRIC is written (the broker's own-tenant working file, and
  the download is audit-logged) because Change/Delete rows resolve by NRIC.
- ``evaluate_adc`` — parse + classify + resolve + diff, NO mutation. Powers the
  preview and is re-run by apply.
- ``apply_adc`` — insert Adds (with dup-skip), merge Changes, soft-terminate
  Deletes, then re-match + re-assign flex for the policy year. Per-row audit.

Identity for Change/Delete resolution: normalized NRIC → else staff_id
(employees) / employee link + name + DOB (dependants) — the same keys as upload
dedup (services/roster_dedup).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
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
from app.schemas.adc import AdcApplyResult, AdcFieldDiff, AdcIssue, AdcOp, AdcPreview
from app.services.derivation_engine import derive
from app.services.excel_reader import open_workbook
from app.services.flex_assignment import assign_flex_safe
from app.services.matching_engine import match_policy_year
from app.services.roster_attributes import (
    DOB_KEYS,
    REL_KEYS,
    first_value,
    iso_date,
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
    DEPENDANT_COLUMN_MAP,
    EMPLOYEE_COLUMN_MAP,
    _build_column_map,
    _normalize_name,
    _normalize_pass,
)

EMP_SHEET = "Employees"
DEP_SHEET = "Dependants"
_VALID_ACTIONS = {"add", "change", "delete"}

# Ordered template columns (header text). Data columns mirror the roster upload
# contract so a filled template re-parses identically; "Action" drives ADC.
_EMP_TEMPLATE_COLS: list[tuple[str, str]] = [
    ("Action", "__action__"),
    ("Staff ID", "staff_id"),
    ("Employee Name", "employee_name"),
    ("Identification No. (NRIC/FIN)", "id_no"),
    ("Date of Birth", "date_of_birth"),
    ("Gender", "gender"),
    ("Marital Status", "marital_status"),
    ("Foreigner Employment Pass", "pass"),
    ("Nationality", "nationality"),
    ("Monthly Salary", "salary"),
    ("Category", "category"),
    ("Job Grade", "job_grade"),
    ("Effective Date", "effective_date"),
]
_DEP_TEMPLATE_COLS: list[tuple[str, str]] = [
    ("Action", "__action__"),
    ("Staff ID", "staff_id"),
    ("Employee Name", "employee_name"),
    ("Dependant Name", "dependant_name"),
    ("Dependant's Identification No.", "dependant_id_no"),
    ("Relationship", "relationship"),
    ("Date of Birth", "date_of_birth"),
    ("Gender", "gender"),
    ("Effective Date", "effective_date"),
]


# ── Template ────────────────────────────────────────────────────────────────


def _autosize(ws: Worksheet) -> None:
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 42)


def build_adc_template_workbook(db: Session, policy_year_id: str) -> Workbook:
    """Current active roster prefilled into an ADC template (Action blank)."""
    wb = Workbook()
    emp_ws = wb.active
    emp_ws.title = EMP_SHEET
    emp_ws.append([label for label, _ in _EMP_TEMPLATE_COLS])

    employees = list(
        db.execute(
            select(Employee)
            .where(
                Employee.policy_year_id == policy_year_id,
                Employee.status == EMPLOYEE_STATUS_ACTIVE,
            )
            .order_by(Employee.staff_id)
        )
        .scalars()
        .all()
    )
    emp_by_id = {e.id: e for e in employees}
    for e in employees:
        attrs = e.attribute_values or {}
        row: list[object] = [""]  # Action blank
        for _label, key in _EMP_TEMPLATE_COLS[1:]:
            if key == "staff_id":
                row.append(e.staff_id)
            elif key == "employee_name":
                row.append(e.employee_name or "")
            elif key == "id_no":
                row.append(first_value(attrs, ("id_no", "nric", "fin")) or "")
            elif key == "date_of_birth":
                row.append(iso_date(first_value(attrs, DOB_KEYS)) or "")
            else:
                row.append(first_value(attrs, (key,)) or "")
        emp_ws.append(row)
    _autosize(emp_ws)

    dep_ws = wb.create_sheet(DEP_SHEET)
    dep_ws.append([label for label, _ in _DEP_TEMPLATE_COLS])
    dependants = list(
        db.execute(
            select(Dependant)
            .where(
                Dependant.policy_year_id == policy_year_id,
                Dependant.status == DEPENDANT_STATUS_ACTIVE,
            )
            .order_by(Dependant.employee_id, Dependant.id)
        )
        .scalars()
        .all()
    )
    for d in dependants:
        attrs = d.attribute_values or {}
        emp = emp_by_id.get(d.employee_id) if d.employee_id else None
        dep_ws.append([
            "",  # Action blank
            (emp.staff_id if emp else first_value(attrs, ("employee_staff_id",))) or "",
            (emp.employee_name if emp else first_value(attrs, ("employee_name",))) or "",
            first_value(attrs, ("dependant_name", "name")) or "",
            first_value(attrs, ("dependant_id_no", "id_no")) or "",
            first_value(attrs, REL_KEYS) or "",
            iso_date(first_value(attrs, DOB_KEYS)) or "",
            first_value(attrs, ("gender",)) or "",
            "",
        ])
    _autosize(dep_ws)
    return wb


# ── Parsing ─────────────────────────────────────────────────────────────────


@dataclass
class _ParsedRow:
    row_no: int
    action: str | None  # normalized lowercase, or None (blank → ignore)
    action_raw: str | None
    attrs: dict
    staff_id: str | None
    employee_name: str | None


def _find_action_col(header: list) -> int | None:
    for idx, cell in enumerate(header):
        if cell is not None and str(cell).strip().lower() == "action":
            return idx
    return None


def _parse_sheet(sheet, spec: dict[str, str], is_dependant: bool) -> list[_ParsedRow]:
    if not sheet.rows:
        return []
    header = sheet.rows[0]
    action_idx = _find_action_col(header)
    col_map = _build_column_map(header, spec)

    out: list[_ParsedRow] = []
    for offset, row in enumerate(sheet.rows[1:]):
        row_no = offset + 2  # 1-based, header is row 1
        if not any(c is not None and str(c).strip() for c in row):
            continue
        raw_action = (
            str(row[action_idx]).strip()
            if action_idx is not None and action_idx < len(row) and row[action_idx]
            else None
        )
        action = raw_action.lower() if raw_action else None

        attrs: dict = {}
        for idx, attr_id in col_map.items():
            if idx >= len(row):
                continue
            value = row[idx]
            if value is None:
                continue
            if attr_id == "pass":
                value = _normalize_pass(value)
            if value in (None, ""):
                continue
            attrs[attr_id] = value

        staff_id = attrs.pop("staff_id", None)
        # Pop for BOTH types: for an employee row the name is the person's own
        # name and belongs on the column (not in attribute_values) — leaving it
        # in attrs makes _merge_and_diff double-record it against the explicit
        # name diff and store an un-normalized copy. For a dependant row it's the
        # SPONSOR's name, re-added below for link resolution.
        employee_name = _normalize_name(attrs.pop("employee_name", None))
        if is_dependant:
            employee_id_no = attrs.pop("employee_id_no", None)
            if staff_id:
                attrs["employee_staff_id"] = str(staff_id).strip()
            if employee_name:
                attrs["employee_name"] = employee_name
            if employee_id_no:
                attrs["employee_id_no"] = str(employee_id_no).strip()
        out.append(
            _ParsedRow(
                row_no=row_no,
                action=action,
                action_raw=raw_action,
                attrs=attrs,
                staff_id=str(staff_id).strip() if staff_id else None,
                employee_name=employee_name,
            )
        )
    return out


def _parse_adc_workbook(path: Path | str) -> tuple[list[_ParsedRow], list[_ParsedRow]]:
    with open_workbook(path) as wb:
        names = {n.lower(): n for n in wb.sheet_names}
        emp_rows: list[_ParsedRow] = []
        dep_rows: list[_ParsedRow] = []
        if EMP_SHEET.lower() in names:
            emp_rows = _parse_sheet(
                wb.sheet(names[EMP_SHEET.lower()]), EMPLOYEE_COLUMN_MAP, False
            )
        if DEP_SHEET.lower() in names:
            dep_rows = _parse_sheet(
                wb.sheet(names[DEP_SHEET.lower()]), DEPENDANT_COLUMN_MAP, True
            )
    return emp_rows, dep_rows


# ── Evaluation ──────────────────────────────────────────────────────────────


@dataclass
class _EmpAdd:
    row_no: int
    attrs: dict
    staff_id: str | None
    employee_name: str | None
    nric: str | None


@dataclass
class _EmpChange:
    row_no: int
    target: Employee
    merged: dict
    employee_name: str | None
    nric: str | None
    diffs: list[AdcFieldDiff]


@dataclass
class _EmpDelete:
    row_no: int
    target: Employee
    effective: date


@dataclass
class _DepAdd:
    row_no: int
    attrs: dict
    employee_id: str | None
    link_method: str
    nric: str | None


@dataclass
class _DepChange:
    row_no: int
    target: Dependant
    merged: dict
    employee_id: str | None
    nric: str | None
    diffs: list[AdcFieldDiff]


@dataclass
class _DepDelete:
    row_no: int
    target: Dependant
    effective: date


@dataclass
class _Plan:
    emp_add: list[_EmpAdd] = field(default_factory=list)
    emp_change: list[_EmpChange] = field(default_factory=list)
    emp_delete: list[_EmpDelete] = field(default_factory=list)
    dep_add: list[_DepAdd] = field(default_factory=list)
    dep_change: list[_DepChange] = field(default_factory=list)
    dep_delete: list[_DepDelete] = field(default_factory=list)
    issues: list[AdcIssue] = field(default_factory=list)


def _merge_and_diff(existing: dict, new: dict) -> tuple[dict, list[AdcFieldDiff]]:
    """Overlay non-empty ``new`` values onto ``existing``; return the merged dict
    and the list of fields that actually changed. Empty cells never clear a
    field (a prefilled template blank means 'unchanged', not 'delete')."""
    merged = dict(existing or {})
    diffs: list[AdcFieldDiff] = []
    for key, value in (new or {}).items():
        if value in (None, ""):
            continue
        old = existing.get(key) if existing else None
        if old is None or str(old) != str(value):
            diffs.append(
                AdcFieldDiff(
                    field=key,
                    old=None if old is None else str(old),
                    new=str(value),
                )
            )
            merged[key] = value
    return merged, diffs


def _effective_from(attrs: dict) -> date:
    raw = first_value(attrs, ("termination_date", "effective_date"))
    return parse_dob(raw) or date.today()


def evaluate_adc(
    db: Session, policy_year_id: str, client_id: str, path: Path | str
) -> tuple[_Plan, AdcPreview]:
    emp_rows, dep_rows = _parse_adc_workbook(path)
    plan = _Plan()

    employees = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
            )
        ).scalars().all()
    )
    emp_by_nric: dict[str, Employee] = {}
    emp_by_staff: dict[str, Employee] = {}
    emp_existing_keys: dict[str, str] = {}
    for e in employees:
        if e.national_id_normalized:
            emp_by_nric.setdefault(e.national_id_normalized, e)
            emp_existing_keys.setdefault(f"nric:{e.national_id_normalized}", e.id)
        if e.staff_id:
            emp_by_staff.setdefault(e.staff_id.strip().lower(), e)
            emp_existing_keys.setdefault(f"staff:{e.staff_id.strip().lower()}", e.id)

    seen_emp: set[str] = set()
    # NRICs assigned by Changes within THIS run → target id. Guards the case
    # (no persisted owner yet) where two Change rows set the same NRIC on two
    # different employees, which the DB has no unique constraint to reject.
    emp_run_nric: dict[str, str] = {}
    for r in emp_rows:
        if not r.action:
            continue
        if r.action not in _VALID_ACTIONS:
            plan.issues.append(
                AdcIssue(row=r.row_no, record_type="employee",
                         message=f"Unknown action '{r.action_raw}'")
            )
            continue
        nric = employee_nric(r.attrs)
        if r.action == "add":
            keys = employee_candidate_keys(r.attrs, r.staff_id)
            if not keys:
                plan.issues.append(
                    AdcIssue(row=r.row_no, record_type="employee",
                             message="Addition has no Staff ID or NRIC — skipped")
                )
                continue
            hit = next((emp_existing_keys[k] for k in keys if k in emp_existing_keys), None)
            in_file = any(k in seen_emp for k in keys)
            if hit or in_file:
                plan.issues.append(
                    AdcIssue(row=r.row_no, record_type="employee",
                             message="Addition already exists — skipped (use Change to edit)")
                )
                continue
            seen_emp.update(keys)
            plan.emp_add.append(
                _EmpAdd(r.row_no, r.attrs, r.staff_id, r.employee_name, nric)
            )
        else:  # change / delete
            # Staff-first for a prefilled row: the staff_id is the stable anchor,
            # so editing the NRIC cell corrects THAT employee (and can be caught
            # as a collision) rather than silently retargeting the NRIC's owner.
            target = (
                (emp_by_staff.get(r.staff_id.strip().lower()) if r.staff_id else None)
                or (emp_by_nric.get(nric) if nric else None)
            )
            if target is None:
                plan.issues.append(
                    AdcIssue(row=r.row_no, record_type="employee",
                             message="No matching employee found for this NRIC/Staff ID")
                )
                continue
            if r.action == "delete":
                plan.emp_delete.append(
                    _EmpDelete(r.row_no, target, _effective_from(r.attrs))
                )
            else:
                # A Change may not reassign an NRIC that already belongs to a
                # different employee (would break per-policy-year NRIC uniqueness).
                nric_owner = emp_by_nric.get(nric) if nric else None
                if nric_owner is not None and nric_owner.id != target.id:
                    plan.issues.append(
                        AdcIssue(
                            row=r.row_no, record_type="employee",
                            message=(
                                "NRIC already belongs to another employee — "
                                "change skipped"
                            ),
                        )
                    )
                    continue
                if nric:
                    prev = emp_run_nric.get(nric)
                    if prev is not None and prev != target.id:
                        plan.issues.append(
                            AdcIssue(
                                row=r.row_no, record_type="employee",
                                message=(
                                    "NRIC assigned to two different employees in "
                                    "this file — change skipped"
                                ),
                            )
                        )
                        continue
                    emp_run_nric[nric] = target.id
                merged, diffs = _merge_and_diff(target.attribute_values or {}, r.attrs)
                name = r.employee_name or target.employee_name
                if name != target.employee_name:
                    diffs.append(
                        AdcFieldDiff(field="employee_name",
                                     old=target.employee_name, new=name)
                    )
                plan.emp_change.append(
                    _EmpChange(r.row_no, target, merged, name, nric, diffs)
                )

    # Dependants — resolve the employee link first for composite keys. Keys are
    # stripped+lowercased consistently; names shared by 2+ employees are dropped
    # from by_name so an ambiguous name can't silently link to the wrong sponsor.
    by_staff = {
        e.staff_id.strip().lower(): e
        for e in employees
        if e.staff_id and e.staff_id.strip()
    }
    _name_groups: dict[str, list[Employee]] = {}
    for e in employees:
        nm = (e.employee_name or "").strip().lower()
        if nm:
            _name_groups.setdefault(nm, []).append(e)
    by_name = {nm: emps[0] for nm, emps in _name_groups.items() if len(emps) == 1}
    existing_deps = list(
        db.execute(
            select(Dependant).where(
                Dependant.client_id == client_id,
                Dependant.policy_year_id == policy_year_id,
            )
        ).scalars().all()
    )
    dep_by_nric: dict[str, Dependant] = {}
    dep_by_comp: dict[str, Dependant] = {}
    dep_existing_keys: dict[str, str] = {}
    for d in existing_deps:
        if d.national_id_normalized:
            dep_by_nric.setdefault(d.national_id_normalized, d)
            dep_existing_keys.setdefault(f"nric:{d.national_id_normalized}", d.id)
        for k in dependant_candidate_keys(
            d.attribute_values, d.employee_id, include_agnostic=d.employee_id is None
        ):
            dep_existing_keys.setdefault(k, d.id)
            if k.startswith(("comp:", "dep:")):
                dep_by_comp.setdefault(k, d)

    seen_dep: set[str] = set()
    dep_run_nric: dict[str, str] = {}
    for r in dep_rows:
        if not r.action:
            continue
        if r.action not in _VALID_ACTIONS:
            plan.issues.append(
                AdcIssue(row=r.row_no, record_type="dependant",
                         message=f"Unknown action '{r.action_raw}'")
            )
            continue
        # Resolve the sponsoring employee.
        emp = None
        method = "unlinked"
        staff_key = (r.staff_id or "").strip().lower()
        name_key = (r.employee_name or "").strip().lower()
        if staff_key and staff_key in by_staff:
            emp, method = by_staff[staff_key], "staff_id"
        elif name_key and name_key in by_name:
            emp, method = by_name[name_key], "name"
        emp_id = emp.id if emp else None
        nric = dependant_nric(r.attrs)

        if r.action == "add":
            keys = dependant_candidate_keys(r.attrs, emp_id)
            if not keys:
                plan.issues.append(
                    AdcIssue(row=r.row_no, record_type="dependant",
                             message="Addition has no NRIC, name, or DOB — skipped")
                )
                continue
            hit = next((dep_existing_keys[k] for k in keys if k in dep_existing_keys), None)
            in_file = any(k in seen_dep for k in keys)
            if hit or in_file:
                plan.issues.append(
                    AdcIssue(row=r.row_no, record_type="dependant",
                             message="Dependant already exists — skipped")
                )
                continue
            seen_dep.update(keys)
            plan.dep_add.append(_DepAdd(r.row_no, r.attrs, emp_id, method, nric))
        else:
            # Only fall back to the employee-agnostic (dep:) key when THIS row is
            # itself unlinked — otherwise a linked mutation could match an
            # unrelated unlinked dependant that merely shares name+DOB.
            target = (
                (dep_by_nric.get(nric) if nric else None)
                or next(
                    (dep_by_comp[k] for k in dependant_candidate_keys(
                        r.attrs, emp_id, include_agnostic=emp_id is None)
                     if k in dep_by_comp),
                    None,
                )
            )
            if target is None:
                plan.issues.append(
                    AdcIssue(row=r.row_no, record_type="dependant",
                             message="No matching dependant found for this NRIC/name")
                )
                continue
            if r.action == "delete":
                plan.dep_delete.append(
                    _DepDelete(r.row_no, target, _effective_from(r.attrs))
                )
            else:
                nric_owner = dep_by_nric.get(nric) if nric else None
                if nric_owner is not None and nric_owner.id != target.id:
                    plan.issues.append(
                        AdcIssue(
                            row=r.row_no, record_type="dependant",
                            message=(
                                "NRIC already belongs to another dependant — "
                                "change skipped"
                            ),
                        )
                    )
                    continue
                if nric:
                    prev = dep_run_nric.get(nric)
                    if prev is not None and prev != target.id:
                        plan.issues.append(
                            AdcIssue(
                                row=r.row_no, record_type="dependant",
                                message=(
                                    "NRIC assigned to two different dependants in "
                                    "this file — change skipped"
                                ),
                            )
                        )
                        continue
                    dep_run_nric[nric] = target.id
                merged, diffs = _merge_and_diff(target.attribute_values or {}, r.attrs)
                plan.dep_change.append(
                    _DepChange(r.row_no, target, merged, emp_id, nric, diffs)
                )

    return plan, _to_preview(plan)


def _to_preview(plan: _Plan) -> AdcPreview:
    additions = [
        AdcOp(row=a.row_no, record_type="employee", name=a.employee_name,
              staff_id=a.staff_id, nric_masked=mask_nric(a.nric) or None)
        for a in plan.emp_add
    ] + [
        AdcOp(row=a.row_no, record_type="dependant",
              name=first_value(a.attrs, ("dependant_name", "name")),
              nric_masked=mask_nric(a.nric) or None)
        for a in plan.dep_add
    ]
    changes = [
        AdcOp(row=c.row_no, record_type="employee", name=c.employee_name,
              staff_id=c.target.staff_id, target_id=c.target.id,
              nric_masked=mask_nric(c.nric) or None, field_diffs=c.diffs)
        for c in plan.emp_change
    ] + [
        AdcOp(row=c.row_no, record_type="dependant",
              name=first_value(c.merged, ("dependant_name", "name")),
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
              name=first_value(d.target.attribute_values, ("dependant_name", "name")),
              target_id=d.target.id, effective=d.effective.isoformat())
        for d in plan.dep_delete
    ]
    return AdcPreview(
        additions=additions,
        changes=changes,
        deletions=deletions,
        issues=plan.issues,
        counts={
            "additions": len(additions),
            "changes": len(changes),
            "deletions": len(deletions),
            "issues": len(plan.issues),
        },
    )


def preview_adc(db: Session, policy_year_id: str, client_id: str, path: Path | str) -> AdcPreview:
    _, preview = evaluate_adc(db, policy_year_id, client_id, path)
    return preview


# ── Apply ───────────────────────────────────────────────────────────────────


def apply_adc(
    db: Session,
    user: CurrentUser,
    policy_year_id: str,
    client_id: str,
    path: Path | str,
) -> AdcApplyResult:
    """Re-evaluate then apply the movement file atomically, then re-match +
    re-assign flex. Per-row audit; a mid-run failure rolls back the whole run."""
    plan, _ = evaluate_adc(db, policy_year_id, client_id, path)

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
            source="adc",
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
        d.target.status = EMPLOYEE_STATUS_TERMINATED
        d.target.terminated_effective = d.effective
        write_audit(
            db, user, action="adc_delete", entity_type="employee",
            entity_id=d.target.id, after={"effective": d.effective.isoformat()},
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
        d.target.status = DEPENDANT_STATUS_TERMINATED
        d.target.terminated_effective = d.effective
        write_audit(
            db, user, action="adc_delete", entity_type="dependant",
            entity_id=d.target.id, after={"effective": d.effective.isoformat()},
        )
        deleted += 1

    write_audit(
        db, user, action="adc_apply", entity_type="policy_year",
        entity_id=policy_year_id,
        after={"added": added, "changed": changed, "deleted": deleted,
               "issues": len(plan.issues)},
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
        skipped=len(plan.issues),
        rematched=rematched,
        issues=plan.issues,
        flex_errors=flex_errors,
    )
