"""Parse STM-shaped employee + dependant upload templates."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.excel_reader import Cell, Sheet, open_workbook

# Column-name → attribute_id mapping for STM employee template.
EMPLOYEE_COLUMN_MAP: dict[str, str] = {
    # Legal entity employing the member (CDL/STM rosters title it "Entity").
    # Drives the insured-entity gate in matching for multi-subsidiary schemes.
    # No bare "company" alias — it would prefix-claim a "Company Email" column.
    "entity": "entity",
    "legal entity": "entity",
    "employer": "entity",
    "subsidiary": "entity",
    "company name": "entity",
    "staff id": "staff_id",
    # The incumbent platform's own extract titles the staff id "User ID", so
    # its built-in listing re-imports without a hand edit. A FALLBACK spelling
    # (`_FALLBACK_HEADERS`) — a file carrying both headers keys off "Staff ID".
    "user id": "staff_id",
    "employee name": "employee_name",
    "identification no. (nric/fin)": "id_no",
    "identification no.": "id_no",
    "date of birth": "date_of_birth",
    "gender": "gender",
    "marital status": "marital_status",
    "employment status": "employment_status",
    "designation": "designation",
    "job title": "designation",
    "position": "designation",
    "country of work": "country_of_work",
    "foreigner employment pass": "pass",
    "nationality": "nationality",
    "monthly salary": "salary",
    "currency": "currency",
    "date of hire": "date_of_hire",
    "confirmation date": "confirmation_date",
    "effective date": "effective_date",
    "last day of service": "last_day_of_service",
    "category": "category",
    "job grade": "job_grade",
    # "Current Job Grade" cannot reach `job_grade` on the loose pass — that one
    # matches by PREFIX and this header starts with the wrong word.
    "current job grade": "job_grade",
    "division": "division",
    "department": "department",
    "cost centre": "cost_centre",
    # Exact aliases for the incumbent's wording. Both would ALSO resolve on the
    # loose prefix pass ("email address" starts with "email"), but that pass
    # only runs on columns nothing claimed exactly — and these two are written
    # by our own template, which must never depend on the fallback.
    "email address": "email",
    "mobile phone": "mobile",
    # Where the member sits, as the incumbent's built-in listing names it:
    # the employing company and the physical site. Descriptive only — neither
    # is the `entity` that drives the insured-entity gate in matching.
    "company description": "company_description",
    "location description": "location_description",
    # "Employee" / "Director" / … — the incumbent's own line-type marker.
    "person class": "person_class",
    "email": "email",
    "mobile": "mobile",
    "bank code": "bank_code",
    "branch code": "branch_code",
    "bank account no.": "bank_account_no",
    "bank account number": "bank_account_no",
    "has insurance cover last year": "prior_year_cover",
    "eligible to sell leave": "leave_sell_eligible",
    "remarks": "remarks",
}

# Attribute keys whose values are identifiers, not numbers — Excel often types
# them numeric ("081" arrives as int 81), so coerce to a clean string rather
# than persist a float. (Lost leading zeros can't be recovered here; the fix is
# a text-formatted column, which the download template provides.)
_CODE_KEYS = frozenset({
    "bank_code", "branch_code", "bank_account_no", "mobile", "id_no",
    "dependant_id_no", "employee_id_no",
})

# Attribute keys holding yes/no flags — normalized to real booleans on ingest.
_FLAG_KEYS = frozenset({"prior_year_cover", "leave_sell_eligible"})

# Insurer-issued member IDs ride dedicated per-insurer columns, e.g.
# "AIA Member ID" / "Zurich Member ID". Matched dynamically (any insurer name)
# and folded into an ``insurer_member_ids`` dict: {"AIA": "2427617201", ...}.
INSURER_MEMBER_ID_KEY = "insurer_member_ids"
_MEMBER_ID_RE = re.compile(r"^(?P<insurer>.+?)\s+member\s+id$", re.IGNORECASE)


DEPENDANT_COLUMN_MAP: dict[str, str] = {
    "entity": "entity",
    "staff id": "staff_id",
    "employee name": "employee_name",
    "employee's identification no. (nric/fin)": "employee_id_no",
    "employee's identification no.": "employee_id_no",
    "dependant name": "dependant_name",
    "dependant's identification no.": "dependant_id_no",
    "relationship": "relationship",
    "date of marriage": "date_of_marriage",
    "gender": "gender",
    "date of birth": "date_of_birth",
    "effective date": "effective_date",
    "termination date": "termination_date",
    # The incumbent's name for the same date, and the one our own template and
    # built-in listing now print. Both spellings feed `termination_date`
    # because ADC reads exactly one key to decide a dependant came off cover;
    # a second date column would be honoured on one upload path and not the
    # other.
    "deletion date": "termination_date",
    # Fallback spelling, same rule as the employee map: a dependant sheet
    # carrying both "Staff ID" and a login "User ID" keys off the former.
    "user id": "staff_id",
    "remarks": "remarks",
}


# Attribute keys that only a DEPENDANT sheet can produce — every one of them is
# absent from EMPLOYEE_COLUMN_MAP, so their presence is what tells a dependant
# listing apart from an employee listing handed to the wrong parser.
# Header spellings that are a LAST RESORT for their attribute. The incumbent
# platform titles the staff id "User ID", but plenty of HR extracts carry a
# "User ID" (an AD/login handle) NEXT TO the real "Staff ID" — and the staff id
# is the roster's primary key, so binding the wrong column proposes the entire
# roster as joiners and the existing rows as missing. Listed here, the spelling
# binds only when no primary spelling claimed the attribute first.
_FALLBACK_HEADERS = frozenset({"user id"})

DEPENDANT_MARKER_KEYS = (
    "dependant_name",
    "dependant_id_no",
    "relationship",
    "date_of_marriage",
)


@dataclass(frozen=True)
class EmployeeRecord:
    staff_id: str
    employee_name: str | None
    attributes: dict[str, Any]
    # 1-based row in the source workbook (header = row 1) — for user-facing
    # messages (e.g. the duplicate manifest), since blank/no-staff rows are
    # dropped and would otherwise desync a positional index from the sheet.
    row: int = 0


@dataclass(frozen=True)
class DependantRecord:
    employee_staff_id: str | None
    employee_name: str | None
    employee_id_no: str | None
    attributes: dict[str, Any]
    row: int = 0


def _normalize_pass(raw: Any) -> str | None:
    if not raw:
        return None
    s = str(raw).upper().replace("-", "").replace(" ", "")
    if s in {"SPASS", "S", "SP"} or "SPASS" in s:
        return "SP"
    if s in {"WP", "WORKPERMIT", "WORKPASS"}:
        return "WP"
    if s in {"EP", "EMPLOYMENTPASS"}:
        return "EP"
    return str(raw)


def _normalize_name(s: Any) -> str | None:
    if not s:
        return None
    return re.sub(r"[,\s]+", " ", str(s)).strip()


# Characters that make Excel treat a string cell as a live formula. The WRITE
# half of this contract is `insurer_reports.safe_cell`, which imports this tuple
# — the escape and the unescape must never drift apart, or exported values stop
# round-tripping through an upload.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r", "\n")


def unescape_formula_guard(value: Any) -> Any:
    """Undo `insurer_reports.safe_cell` on the way back in.

    Our own exports prefix a value starting with ``= + - @`` with an apostrophe
    so Excel can't execute it as a formula. The listing template is now
    RE-UPLOADED (see `services/adc.py`), so that guard round-trips as data: a
    Malaysian mobile ``+60186448967`` came back as ``'+60186448967``, which the
    diff reported as a change on every upload and would have written the stray
    apostrophe into the roster on apply.

    Only strips when the next character is one the guard actually escapes, so a
    value legitimately beginning with an apostrophe is untouched.
    """
    if (
        isinstance(value, str)
        and len(value) > 1
        and value[0] == "'"
        and value[1] in _FORMULA_LEADERS
    ):
        return value[1:]
    return value


def _normalize_code(raw: Any) -> str:
    """Identifier-shaped value → clean string ('081' text stays '081'; a numeric
    cell 81 / 81.0 becomes '81' rather than '81.0')."""
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


_TRUE_WORDS = {"true", "yes", "y", "1", "1.0"}
_FALSE_WORDS = {"false", "no", "n", "0", "0.0"}


def _normalize_flag(raw: Any) -> Any:
    """Yes/no-ish cell → real bool; unrecognized text is kept raw (visible in
    the roster rather than silently coerced)."""
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return raw


def _coerce_attr(attr_id: str, value: Any) -> Any:
    # Undo our own export's formula guard FIRST, so the apostrophe never reaches
    # a normalizer or the stored value. This has to happen on ingest rather than
    # only in the diff: the listing template is round-tripped now, and hiding
    # the artifact from the comparison would still write "'+60186448967" into
    # the roster the first time that row genuinely changes.
    value = unescape_formula_guard(value)
    if attr_id in _CODE_KEYS:
        return _normalize_code(value)
    if attr_id in _FLAG_KEYS:
        return _normalize_flag(value)
    return value


def _member_id_columns(header_row: list[Cell]) -> dict[int, str]:
    """Column index → insurer name for '<Insurer> Member ID' headers, keeping
    the sheet's own casing for the insurer ('AIA Member ID' → 'AIA')."""
    out: dict[int, str] = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        m = _MEMBER_ID_RE.match(re.sub(r"\s+", " ", str(cell)).strip())
        if m:
            out[idx] = m.group("insurer").strip()
    return out


def _collect_member_ids(
    row: list[Cell], member_id_cols: dict[int, str], attrs: dict[str, Any]
) -> None:
    ids = {
        insurer: _normalize_code(row[idx])
        for idx, insurer in member_id_cols.items()
        if idx < len(row) and row[idx] not in (None, "")
    }
    if ids:
        attrs[INSURER_MEMBER_ID_KEY] = ids


def _read_sheet(path: Path | str, preferred: str) -> Sheet | None:
    """The sheet named ``preferred`` when the workbook has one, else the first.

    Both downloadable templates are TWO-sheet workbooks (``Employees`` /
    ``Dependants``) — the member listing (``member_listing_template``) and the
    ADC movement file — and both roster tabs offer them. Reading sheet 0
    unconditionally meant a dependant upload of either file parsed the
    *employee* sheet through ``DEPENDANT_COLUMN_MAP``: Staff ID and Employee
    Name resolve on that sheet, so the "no identifying column" guard passes and
    the roster imports one nameless, DOB-less dependant per employee.

    Single-sheet rosters in the wild (whatever their sheet is called) keep the
    old behaviour, so nothing that parses today stops parsing.
    """
    with open_workbook(path) as wb:
        names = wb.sheet_names
        if not names:
            return None
        by_name = {n.strip().lower(): n for n in names}
        return wb.sheet(by_name.get(preferred.strip().lower(), names[0]))


def has_sheet(path: Path | str, name: str) -> bool:
    """Whether the workbook really carries a sheet of this name.

    Distinct from `_read_sheet`, which FALLS BACK to the first sheet — callers
    that need to know "does this file cover dependants at all" must not read a
    fallback as a yes.
    """
    with open_workbook(path) as wb:
        return name.strip().lower() in {n.strip().lower() for n in wb.sheet_names}


# Columns only a DEPENDANT sheet has. Unlike `DEPENDANT_MARKER_KEYS` this is the
# narrow, unambiguous pair: a "Relationship" or "Date of Marriage" column can
# plausibly appear on an employee roster, but a Dependant Name / Dependant's
# Identification No. cannot.
_DEPENDANT_ONLY_COLUMNS = ("dependant_name", "dependant_id_no")


def parse_employee_workbook(
    path: Path | str,
    column_mapping: dict[int, str | None] | None = None,
) -> list[EmployeeRecord]:
    sheet = _read_sheet(path, "Employees")
    if sheet is None or not sheet.rows:
        return []
    header_row = sheet.rows[0]
    # Mirror of the dependant guard: a single-sheet DEPENDANT roster dropped on
    # the Employees upload falls back to sheet 0 here, and Staff ID + Employee
    # Name resolve on it — so each row would import as an employee carrying the
    # DEPENDANT's date of birth and gender, and (through the listing sync) merge
    # that onto the sponsoring employee as a change.
    dependant_cols = set(_build_column_map(header_row, DEPENDANT_COLUMN_MAP).values())
    if any(c in dependant_cols for c in _DEPENDANT_ONLY_COLUMNS):
        return []
    col_map = (
        {index: attribute_id for index, attribute_id in column_mapping.items() if attribute_id}
        if column_mapping is not None
        else _build_column_map(header_row, EMPLOYEE_COLUMN_MAP)
    )
    member_id_cols = _member_id_columns(header_row)

    records: list[EmployeeRecord] = []
    for offset, row in enumerate(sheet.rows[1:]):
        if not any(c is not None and str(c).strip() for c in row):
            continue
        attrs: dict[str, Any] = {}
        for idx, attr_id in col_map.items():
            if idx < len(row):
                value = row[idx]
                if value is None:
                    continue
                if attr_id == "pass":
                    value = _normalize_pass(value)
                if value in (None, ""):
                    continue
                attrs[attr_id] = _coerce_attr(attr_id, value)
        _collect_member_ids(row, member_id_cols, attrs)
        staff_id = attrs.pop("staff_id", None)
        if not staff_id:
            continue
        records.append(
            EmployeeRecord(
                staff_id=str(staff_id).strip(),
                employee_name=_normalize_name(attrs.get("employee_name")),
                attributes=attrs,
                row=offset + 2,  # header is row 1
            )
        )
    return records


def parse_dependant_workbook(path: Path | str) -> list[DependantRecord]:
    sheet = _read_sheet(path, "Dependants")
    if sheet is None or not sheet.rows:
        return []
    header_row = sheet.rows[0]
    col_map = _build_column_map(header_row, DEPENDANT_COLUMN_MAP)
    member_id_cols = _member_id_columns(header_row)

    records: list[DependantRecord] = []
    for offset, row in enumerate(sheet.rows[1:]):
        if not any(c is not None and str(c).strip() for c in row):
            continue
        attrs: dict[str, Any] = {}
        for idx, attr_id in col_map.items():
            if idx < len(row):
                value = row[idx]
                if value in (None, ""):
                    continue
                attrs[attr_id] = _coerce_attr(attr_id, value)
        _collect_member_ids(row, member_id_cols, attrs)
        staff_id = attrs.pop("staff_id", None)
        employee_name = _normalize_name(attrs.pop("employee_name", None))
        employee_id_no = attrs.pop("employee_id_no", None)
        if not (staff_id or employee_name or employee_id_no):
            continue
        # The row must say something about the DEPENDANT, not only about their
        # sponsor. Without this an EMPLOYEE sheet fed to this parser clears the
        # guard above on Staff ID / Employee Name alone and imports one row per
        # employee. `_read_sheet` stops that for the two-sheet templates; this
        # stops it for a single-sheet employee roster uploaded on the Dependants
        # tab, where nothing else stands in the way.
        #
        # The test is a dependant-only COLUMN, not a name: a row carrying just
        # relationship + DOB is a legitimate dependant (regression-tested in
        # test_roster_dedup) and an employee sheet has none of these four.
        if not any(k in attrs for k in DEPENDANT_MARKER_KEYS):
            continue
        # Persist employee-identifying info in attribute_values so the auto-match
        # endpoint can re-run linking without re-uploading the roster.
        if staff_id:
            attrs["employee_staff_id"] = str(staff_id).strip()
        if employee_name:
            attrs["employee_name"] = employee_name
        if employee_id_no:
            attrs["employee_id_no"] = str(employee_id_no).strip()
        records.append(
            DependantRecord(
                employee_staff_id=str(staff_id).strip() if staff_id else None,
                employee_name=employee_name,
                employee_id_no=str(employee_id_no).strip() if employee_id_no else None,
                attributes=attrs,
                row=offset + 2,  # header is row 1
            )
        )
    return records


def _build_column_map(header_row: list[Cell], spec: dict[str, str]) -> dict[int, str]:
    """Map column index → attribute id. Each attribute binds to at most ONE
    column: exact header matches are assigned first (most reliable), then loose
    prefix matches fill remaining columns against still-unclaimed attributes.

    Binding each attribute once prevents a secondary header (e.g. "Job Grade
    Band" alongside "Job Grade") from prefix-matching the same attribute and
    overwriting the real value downstream (last-column-wins in the attrs dict).
    """
    keys: list[tuple[int, str]] = []
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        keys.append((idx, re.sub(r"\s+", " ", str(cell)).strip().lower()))

    out: dict[int, str] = {}
    taken: set[str] = set()
    # Pass 1 — exact matches win, PRIMARY spellings before fallback ones.
    # Order matters within this pass because a column binds by position: a file
    # carrying both "User ID" and "Staff ID" would otherwise key every employee
    # by whichever came first, and the real staff id column would be dropped
    # (pass 2 skips claimed attributes). Splitting the pass makes the fallback
    # spelling mean what it says — used only when nothing better is present.
    for fallback in (False, True):
        for idx, key in keys:
            if idx in out or (key in _FALLBACK_HEADERS) is not fallback:
                continue
            attr_id = spec.get(key)
            if attr_id is not None and attr_id not in taken:
                out[idx] = attr_id
                taken.add(attr_id)
    # Pass 2 — loose prefix match for slight variations ("Monthly Salary (SGD)"),
    # but only onto attributes no exact column already claimed.
    for idx, key in keys:
        if idx in out:
            continue
        for spec_key, attr_id in spec.items():
            if attr_id not in taken and key.startswith(spec_key):
                out[idx] = attr_id
                taken.add(attr_id)
                break
    return out
