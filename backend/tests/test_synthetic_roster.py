"""The synthetic roster generator must produce files the parsers can read."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.roster_parser import (
    EMPLOYEE_COLUMN_MAP,
    _build_column_map,
    parse_dependant_workbook,
    parse_employee_workbook,
)
from tests.fixtures.generate_synthetic_roster import generate


def test_column_map_binds_each_attribute_once() -> None:
    # A secondary "Job Grade ..." header must not steal the job_grade binding
    # from the exact "Job Grade" column (else it overwrites the real code).
    header = ["Staff ID", "Category", "Job Grade", "Job Grade Band"]
    out = _build_column_map(header, EMPLOYEE_COLUMN_MAP)
    assert out[2] == "job_grade"  # exact match wins
    assert 3 not in out  # secondary column left unmapped, not a duplicate binding
    assert out[0] == "staff_id"
    assert out[1] == "category"


def test_column_map_still_allows_loose_prefix() -> None:
    # Loose prefix match still works when there's no exact header.
    out = _build_column_map(["Monthly Salary (SGD)"], EMPLOYEE_COLUMN_MAP)
    assert out[0] == "salary"


def test_column_map_binds_entity_column() -> None:
    # The CDL/STM "Entity" column carries the employing legal entity — it
    # drives the insured-entity gate in matching for multi-subsidiary schemes.
    out = _build_column_map(["Entity", "Staff ID", "Category"], EMPLOYEE_COLUMN_MAP)
    assert out[0] == "entity"
    # A "Company Email" header must NOT be mistaken for the entity (there is
    # deliberately no bare "company" alias).
    out = _build_column_map(["Company Email", "Staff ID"], EMPLOYEE_COLUMN_MAP)
    assert out.get(0) != "entity"


def test_excel_reader_coerces_dates_to_iso() -> None:
    # Excel date/datetime cells must store as "YYYY-MM-DD" (no "00:00:00" tail
    # that later blanks the fact-find age tables); non-dates pass through.
    from datetime import date, datetime

    from app.services.excel_reader import _coerce

    assert _coerce(datetime(1958, 2, 19, 0, 0)) == "1958-02-19"
    assert _coerce(date(1990, 5, 1)) == "1990-05-01"
    assert _coerce("Manager") == "Manager"
    assert _coerce(5000) == 5000
    assert _coerce(None) is None


def test_iso_date_normalizes_stored_values() -> None:
    from app.services.roster_attributes import iso_date

    assert iso_date("1962-01-27 00:00:00") == "1962-01-27"  # legacy stored form
    assert iso_date("1990-05-01") == "1990-05-01"
    assert iso_date(None) is None
    assert iso_date("not a date") == "not a date"  # surfaced, not dropped


@pytest.fixture(scope="module")
def synthetic_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    tmp = tmp_path_factory.mktemp("synthetic")
    return generate(rows=25, out_dir=tmp)


def test_generated_employee_workbook_parses(synthetic_pair: tuple[Path, Path]) -> None:
    emp_path, _ = synthetic_pair
    records = parse_employee_workbook(emp_path)
    assert len(records) == 25
    assert all(r.staff_id.startswith("SYN") for r in records)
    assert all(r.employee_name for r in records)
    assert all("category" in r.attributes for r in records)


def test_generated_dependant_workbook_parses(synthetic_pair: tuple[Path, Path]) -> None:
    _, dep_path = synthetic_pair
    records = parse_dependant_workbook(dep_path)
    # Roughly half of 25 employees get 1-2 dependants → expect some records.
    assert len(records) > 0
    assert all(r.employee_staff_id and r.employee_staff_id.startswith("SYN") for r in records)


def test_generator_is_deterministic(tmp_path: Path) -> None:
    a_emp, _ = generate(rows=10, out_dir=tmp_path / "a")
    b_emp, _ = generate(rows=10, out_dir=tmp_path / "b")
    def _key(r):
        return (r.staff_id, r.employee_name, r.attributes.get("category"))
    a = [_key(r) for r in parse_employee_workbook(a_emp)]
    b = [_key(r) for r in parse_employee_workbook(b_emp)]
    assert a == b
