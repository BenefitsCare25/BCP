"""Duplicate detection on roster + dependant upload (NRIC-primary identity).

Covers: NRIC normalization, staff-id fallback, in-file vs existing manifests,
the dependant double-append regression, and national_id_normalized stamping.
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_roster_dedup.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Dependant, Employee  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def _policy_year_id(client: TestClient) -> str:
    return client.get("/api/v1/policy-years").json()[0]["id"]


def _xlsx(headers: list[str], rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


EMP_HEADERS = [
    "Staff ID",
    "Employee Name",
    "Identification No. (NRIC/FIN)",
    "Date of Birth",
    "Category",
]
DEP_HEADERS = [
    "Staff ID",
    "Employee Name",
    "Dependant Name",
    "Dependant's Identification No.",
    "Relationship",
    "Date of Birth",
]


def _upload_employees(client: TestClient, py_id: str, rows: list[list[object]]) -> dict:
    content = _xlsx(EMP_HEADERS, rows)
    res = client.post(
        "/api/v1/employees/upload",
        files={"file": ("roster.xlsx", content, XLSX_MIME)},
        data={"policy_year_id": py_id},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _upload_dependants(client: TestClient, py_id: str, rows: list[list[object]]) -> dict:
    content = _xlsx(DEP_HEADERS, rows)
    res = client.post(
        "/api/v1/dependants/upload",
        files={"file": ("deps.xlsx", content, XLSX_MIME)},
        data={"policy_year_id": py_id},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_employee_upload_stamps_normalized_nric(client: TestClient) -> None:
    py = _policy_year_id(client)
    out = _upload_employees(
        client,
        py,
        [["D-001", "Ada Ng", "s1234567d", "1990-05-01", "Executive"]],
    )
    assert out["inserted"] == 1
    assert out["skipped"] == 0
    assert out["duplicates"] == []
    db = SessionLocal()
    try:
        emp = db.execute(
            select(Employee).where(Employee.staff_id == "D-001")
        ).scalar_one()
        # Normalized: uppercased, punctuation stripped.
        assert emp.national_id_normalized == "S1234567D"
    finally:
        db.close()


def test_employee_reupload_same_nric_is_existing_duplicate(client: TestClient) -> None:
    py = _policy_year_id(client)
    # Same person, different staff id + messy NRIC punctuation → still a dup on NRIC.
    out = _upload_employees(
        client,
        py,
        [["D-999", "Ada Ng (rehired)", "S-1234567-D", "1990-05-01", "Manager"]],
    )
    assert out["inserted"] == 0
    assert out["skipped"] == 1
    dup = out["duplicates"][0]
    assert dup["reason"] == "existing"
    assert dup["nric_masked"] == "S*****67D"
    assert dup["existing_id"]


def test_employee_in_file_duplicate_detected(client: TestClient) -> None:
    py = _policy_year_id(client)
    out = _upload_employees(
        client,
        py,
        [
            ["E-100", "Bo Tan", "S2345678J", "1988-01-01", "Exec"],
            ["E-101", "Bo Tan Clone", "s2345678j", "1988-01-01", "Exec"],
        ],
    )
    assert out["inserted"] == 1
    assert out["skipped"] == 1
    dup = out["duplicates"][0]
    assert dup["reason"] == "in_file"
    # True workbook row: header=1, first data row=2, the duplicate is row 3.
    assert dup["row"] == 3


def test_employee_staff_id_fallback_when_no_nric(client: TestClient) -> None:
    py = _policy_year_id(client)
    first = _upload_employees(client, py, [["NN-1", "No Nric", "", "1991-02-02", "Exec"]])
    assert first["inserted"] == 1
    # Re-upload same staff id, still no NRIC → dedup on staff_id fallback.
    again = _upload_employees(client, py, [["NN-1", "No Nric", "", "1991-02-02", "Exec"]])
    assert again["inserted"] == 0
    assert again["duplicates"][0]["reason"] == "existing"


def test_dependant_reupload_does_not_double(client: TestClient) -> None:
    """Regression: dependant re-upload previously appended blindly (doubled rows)."""
    py = _policy_year_id(client)
    _upload_employees(client, py, [["DEP-OWNER", "Owner One", "S3456789A", "1980-01-01", "Exec"]])
    rows = [["DEP-OWNER", "Owner One", "Kid One", "T0512345Z", "Child", "2012-03-03"]]
    first = _upload_dependants(client, py, rows)
    assert first["inserted"] == 1
    second = _upload_dependants(client, py, rows)
    assert second["inserted"] == 0
    assert second["skipped"] == 1
    assert second["duplicates"][0]["reason"] == "existing"

    db = SessionLocal()
    try:
        count = len(
            db.execute(
                select(Dependant).where(Dependant.national_id_normalized == "T0512345Z")
            ).scalars().all()
        )
        assert count == 1, "dependant must not be duplicated on re-upload"
    finally:
        db.close()


def test_dependant_composite_key_when_no_nric(client: TestClient) -> None:
    py = _policy_year_id(client)
    _upload_employees(client, py, [["DEP-OWNER2", "Owner Two", "S4567890B", "1981-01-01", "Exec"]])
    rows = [["DEP-OWNER2", "Owner Two", "Kid Two", "", "Child", "2013-04-04"]]
    first = _upload_dependants(client, py, rows)
    assert first["inserted"] == 1
    second = _upload_dependants(client, py, rows)
    assert second["inserted"] == 0
    assert second["duplicates"][0]["reason"] == "existing"


def test_dependant_dedup_without_nric_or_name(client: TestClient) -> None:
    """Regression: a dependant with only relationship + DOB (no NRIC, no name)
    must still dedup on re-upload instead of doubling."""
    py = _policy_year_id(client)
    _upload_employees(
        client, py, [["DEP-OWNER3", "Owner Three", "S5678901C", "1982-01-01", "Exec"]]
    )
    rows = [["DEP-OWNER3", "Owner Three", "", "", "Child", "2014-05-05"]]
    first = _upload_dependants(client, py, rows)
    assert first["inserted"] == 1
    second = _upload_dependants(client, py, rows)
    assert second["inserted"] == 0, "rel+DOB-only dependant must not double"


def test_dependant_unlinked_then_linked_dedups(client: TestClient) -> None:
    """Regression: a dependant first stored unlinked (employee absent) must dedup
    when re-uploaded after its employee exists (employee-agnostic bridge key)."""
    py = _policy_year_id(client)
    rows = [["DEP-OWNER4", "Owner Four", "Kid Four", "", "Child", "2016-06-06"]]
    # Employee DEP-OWNER4 does not exist yet → dependant lands unlinked.
    first = _upload_dependants(client, py, rows)
    assert first["inserted"] == 1
    # Now create the employee, then re-upload the same dependant (now linkable).
    _upload_employees(
        client, py, [["DEP-OWNER4", "Owner Four", "S6789012D", "1983-01-01", "Exec"]]
    )
    second = _upload_dependants(client, py, rows)
    assert second["inserted"] == 0, "unlinked→linked dependant must not double"
