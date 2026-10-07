"""Company Employee Listing import: the terminate-missing opt-in.

The listing reader skips rows it cannot read as a person — a dependant with no
employee row, an ID with no name, an employee with neither Staff ID nor NRIC.
Anyone on such a row looks absent from the file, so the preview reports them
and apply refuses to terminate the absent until the file is fixed.
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_employee_listing_import.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog, Dependant, Employee, EmployeeAttributeSchema  # noqa: E402
from app.models.employee_listing import ElLayoutProfile, ListingAssignment  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

BANNER = ["EMPLOYEE", None, None, None, "DEPENDANTS", None, None, None]
HEADER = [
    "Staff ID", "Name", "NRIC", "Date of Birth",
    "Dependant Name", "Relationship", "NRIC", "Date of Birth",
]
ANNA = ["E-1", "Anna Lim", "S1111111D", "1990-01-01", None, None, None, None]
BEN = ["E-2", "Ben Ong", "S2222222J", "1985-02-02", None, None, None, None]


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


def _workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Listing"
    ws.append(BANNER)
    ws.append(HEADER)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post(client: TestClient, py: str, step: str, content: bytes, data: dict[str, str]):
    return client.post(
        f"/api/v1/policy-years/{py}/employee-listing/{step}",
        files={"file": ("listing.xlsx", content, XLSX_MIME)},
        data=data,
    )


@pytest.fixture(scope="module", autouse=True)
def _seed_roster(_setup_db, client: TestClient):
    res = _post(client, _py(client), "apply", _workbook([ANNA, BEN]), {"mapping": "{}"})
    assert res.status_code == 200, res.text
    assert res.json()["added"] == 2
    yield


def _snapshot() -> dict[str, object]:
    db = SessionLocal()
    try:
        return {
            "employees": sorted(
                (e.id, e.staff_id, e.status) for e in db.execute(select(Employee)).scalars()
            ),
            **{
                model.__name__: db.execute(select(func.count()).select_from(model)).scalar_one()
                for model in (Dependant, AuditLog, EmployeeAttributeSchema,
                              ListingAssignment, ElLayoutProfile)
            },
        }
    finally:
        db.close()


def test_clean_listing_reports_no_dropped_rows(client: TestClient) -> None:
    res = _post(client, _py(client), "preview", _workbook([ANNA]), {})
    assert res.status_code == 200, res.text
    counts = res.json()["members"]["counts"]
    assert counts["dropped_rows"] == 0
    assert counts["missing"] == 1


def test_unreadable_rows_are_counted_in_the_preview(client: TestClient) -> None:
    rows = [
        ANNA,
        # Ben's Staff ID with no name: the reader skips it, so Ben looks absent.
        ["E-2", None, None, "1985-02-02", None, None, None, None],
        # A dependant whose employee row is not in the file.
        ["E-404", None, None, None, "Kid Orphan", "Child", None, "2015-01-01"],
        # Named, but neither a Staff ID nor an NRIC identifies them.
        [None, "Nameless Ident", None, "1991-01-01", None, None, None, None],
    ]
    res = _post(client, _py(client), "preview", _workbook(rows), {})
    assert res.status_code == 200, res.text
    counts = res.json()["members"]["counts"]
    assert counts["dropped_rows"] == 3
    assert "E-2" in {op["staff_id"] for op in res.json()["members"]["missing"]}


def test_apply_refuses_termination_when_rows_were_dropped(client: TestClient) -> None:
    py = _py(client)
    content = _workbook([
        ANNA,
        ["E-2", None, None, "1985-02-02", None, None, None, None],
        ["E-9", "Would Be Added", "S9999999D", "1995-05-05", None, None, None, None],
    ])
    preview = _post(client, py, "preview", content, {}).json()
    assert preview["members"]["counts"]["dropped_rows"] == 1
    before = _snapshot()

    res = _post(client, py, "apply", content, {
        "mapping": "{}",
        "terminate_missing": "true",
        "missing_digest": preview["members"]["missing_digest"],
    })
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "termination_blocked_dropped_rows"
    assert "1 row could not be read" in detail["message"]
    assert _snapshot() == before, "nothing may be written"
