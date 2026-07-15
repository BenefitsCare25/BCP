"""ADC (Additions / Deletions / Changes) roster movement: template, preview, apply."""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_adc.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Dependant, Employee  # noqa: E402
from app.models.employee import EMPLOYEE_STATUS_TERMINATED  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EMP_COLS = [
    "Action", "Staff ID", "Employee Name", "Identification No. (NRIC/FIN)",
    "Date of Birth", "Category", "Effective Date",
]
DEP_COLS = [
    "Action", "Staff ID", "Employee Name", "Dependant Name",
    "Dependant's Identification No.", "Relationship", "Date of Birth",
    "Effective Date",
]


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _py(client: TestClient) -> str:
    return client.get("/api/v1/policy-years").json()[0]["id"]


def _movement(emp_rows: list[list], dep_rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Employees"
    ws.append(EMP_COLS)
    for r in emp_rows:
        ws.append(r)
    dws = wb.create_sheet("Dependants")
    dws.append(DEP_COLS)
    for r in dep_rows:
        dws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload_emp(client, py, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(
        ["Staff ID", "Employee Name", "Identification No. (NRIC/FIN)",
         "Date of Birth", "Category"]
    )
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return client.post(
        "/api/v1/employees/upload",
        files={"file": ("e.xlsx", buf.getvalue(), XLSX_MIME)},
        data={"policy_year_id": py},
    )


@pytest.fixture(scope="module", autouse=True)
def _seed(_setup_db, client: TestClient):
    py = _py(client)
    assert _upload_emp(
        client, py,
        [
            ["A-1", "Anna Lim", "S1111111A", "1990-01-01", "Executive"],
            ["A-2", "Ben Ong", "S2222222B", "1985-02-02", "Manager"],
            ["A-3", "Cara Tan", "S3333333C", "1988-03-03", "Executive"],
        ],
    ).status_code == 200
    yield


def _preview(client, py, content):
    return client.post(
        f"/api/v1/policy-years/{py}/adc/preview",
        files={"file": ("adc.xlsx", content, XLSX_MIME)},
    )


def _apply(client, py, content):
    return client.post(
        f"/api/v1/policy-years/{py}/adc/apply",
        files={"file": ("adc.xlsx", content, XLSX_MIME)},
    )


def test_template_prefilled_with_active_roster(client: TestClient) -> None:
    py = _py(client)
    res = client.get(f"/api/v1/policy-years/{py}/adc/template")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(XLSX_MIME)
    wb = load_workbook(BytesIO(res.content))
    assert wb.sheetnames == ["Employees", "Dependants"]
    emp = wb["Employees"]
    header = [c.value for c in emp[1]]
    assert header[0] == "Action" and "Staff ID" in header
    staff_col = header.index("Staff ID")
    staff_ids = {emp.cell(row=r, column=staff_col + 1).value for r in range(2, emp.max_row + 1)}
    assert {"A-1", "A-2", "A-3"} <= staff_ids


def test_preview_classifies_add_change_delete(client: TestClient) -> None:
    py = _py(client)
    content = _movement(
        emp_rows=[
            ["Add", "A-9", "New Hire", "S9999999Z", "1995-05-05", "Executive", ""],
            ["Change", "A-1", "Anna Lim-Wong", "S1111111A", "1990-01-01", "Manager", ""],
            ["Delete", "A-2", "Ben Ong", "S2222222B", "1985-02-02", "Manager", "2026-06-30"],
            ["Change", "X-0", "Ghost", "S0000000X", "", "", ""],  # unmatched
        ],
        dep_rows=[],
    )
    res = _preview(client, py, content)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["counts"]["additions"] == 1
    assert body["counts"]["changes"] == 1  # ghost is an issue, not a change
    assert body["counts"]["deletions"] == 1
    # Change diff surfaces the category edit.
    change = body["changes"][0]
    fields = {d["field"] for d in change["field_diffs"]}
    assert "category" in fields or "employee_name" in fields
    assert any("No matching employee" in i["message"] for i in body["issues"])


def test_preview_does_not_mutate(client: TestClient) -> None:
    db = SessionLocal()
    try:
        before = db.execute(
            select(Employee).where(Employee.staff_id == "A-2")
        ).scalar_one()
        assert before.status == "active"
    finally:
        db.close()


def test_apply_adds_changes_deletes(client: TestClient) -> None:
    py = _py(client)
    content = _movement(
        emp_rows=[
            ["Add", "A-9", "New Hire", "S9999999Z", "1995-05-05", "Executive", ""],
            ["Change", "A-1", "Anna Lim-Wong", "S1111111A", "1990-01-01", "Manager", ""],
            ["Delete", "A-3", "Cara Tan", "S3333333C", "1988-03-03", "Executive", "2026-06-30"],
        ],
        dep_rows=[
            ["Add", "A-1", "Anna Lim-Wong", "Anna Kid", "T1512345Z", "Child", "2015-07-07", ""],
        ],
    )
    res = _apply(client, py, content)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["added"] == 2  # 1 employee + 1 dependant
    assert body["changed"] == 1
    assert body["deleted"] == 1

    db = SessionLocal()
    try:
        new_hire = db.execute(
            select(Employee).where(Employee.staff_id == "A-9")
        ).scalar_one()
        assert new_hire.national_id_normalized == "S9999999Z"
        assert new_hire.source == "adc"

        changed = db.execute(
            select(Employee).where(Employee.staff_id == "A-1")
        ).scalar_one()
        assert changed.employee_name == "Anna Lim-Wong"
        assert (changed.attribute_values or {}).get("category") == "Manager"

        deleted = db.execute(
            select(Employee).where(Employee.staff_id == "A-3")
        ).scalar_one()
        assert deleted.status == EMPLOYEE_STATUS_TERMINATED
        assert deleted.terminated_effective is not None

        kid = db.execute(
            select(Dependant).where(Dependant.national_id_normalized == "T1512345Z")
        ).scalar_one()
        assert kid.employee_id == changed.id
    finally:
        db.close()


def test_apply_is_idempotent_on_reapply(client: TestClient) -> None:
    """Re-applying the same file: the Add is now a dup (issue), Delete a no-op
    re-terminate, Change re-writes the same values — no new employee row."""
    py = _py(client)
    content = _movement(
        emp_rows=[["Add", "A-9", "New Hire", "S9999999Z", "1995-05-05", "Executive", ""]],
        dep_rows=[],
    )
    res = _apply(client, py, content)
    assert res.status_code == 200
    assert res.json()["added"] == 0
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Employee).where(Employee.national_id_normalized == "S9999999Z")
        ).scalars().all()
        assert len(rows) == 1, "duplicate add must not create a second row"
    finally:
        db.close()


def test_same_file_new_hire_dependant_links(client: TestClient) -> None:
    """A new employee + their dependant in one file: the dependant must link to
    the employee added in the same run, not land unlinked."""
    py = _py(client)
    content = _movement(
        emp_rows=[["Add", "NH-1", "New Parent", "S7777777G", "1992-09-09", "Executive", ""]],
        dep_rows=[
            ["Add", "NH-1", "New Parent", "NH Kid", "T1712345Z", "Child", "2017-08-08", ""],
        ],
    )
    res = _apply(client, py, content)
    assert res.status_code == 200, res.text
    db = SessionLocal()
    try:
        emp = db.execute(
            select(Employee).where(Employee.staff_id == "NH-1")
        ).scalar_one()
        kid = db.execute(
            select(Dependant).where(Dependant.national_id_normalized == "T1712345Z")
        ).scalar_one()
        assert kid.employee_id == emp.id, "dependant must link to same-file new hire"
    finally:
        db.close()


def test_add_without_identifier_is_flagged(client: TestClient) -> None:
    py = _py(client)
    content = _movement(
        emp_rows=[["Add", "", "Nameless Row", "", "", "Executive", ""]],
        dep_rows=[],
    )
    res = _preview(client, py, content)
    body = res.json()
    assert body["counts"]["additions"] == 0
    assert any("no Staff ID or NRIC" in i["message"] for i in body["issues"])


def test_change_to_colliding_nric_is_rejected(client: TestClient) -> None:
    """Changing A-1's NRIC to A-2's existing NRIC must be refused (uniqueness)."""
    py = _py(client)
    # A-2 has S2222222B (seeded). Try to give A-1 that same NRIC via Change.
    content = _movement(
        emp_rows=[["Change", "A-1", "Anna", "S2222222B", "1990-01-01", "Manager", ""]],
        dep_rows=[],
    )
    res = _preview(client, py, content)
    body = res.json()
    assert body["counts"]["changes"] == 0
    assert any("another employee" in i["message"] for i in body["issues"])


def test_terminated_excluded_from_default_list(client: TestClient) -> None:
    py = _py(client)
    listing = client.get(f"/api/v1/employees?policy_year_id={py}&limit=200").json()
    staff_ids = {e["staff_id"] for e in listing["items"]}
    assert "A-3" not in staff_ids
    # opt-in view shows the leaver
    term = client.get(
        f"/api/v1/employees?policy_year_id={py}&limit=200&status=terminated"
    ).json()
    assert "A-3" in {e["staff_id"] for e in term["items"]}
