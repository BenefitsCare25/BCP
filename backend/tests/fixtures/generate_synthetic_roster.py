"""Generate a deterministic synthetic employee + dependant roster.

Decision #2 in the build plan: don't commit real STM PII; generate test data
shaped like the STM template instead. Output is two `.xlsx` files matching
`EMPLOYEE_COLUMN_MAP` and `DEPENDANT_COLUMN_MAP` headers exactly so
`parse_employee_workbook` / `parse_dependant_workbook` read them unchanged.

Run: `uv run python tests/fixtures/generate_synthetic_roster.py [rows]`
"""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

OUTPUT_DIR = Path(__file__).parent / "rosters"
DEFAULT_ROWS = 100
SEED = 20260512

_FIRST_NAMES = [
    "Wei Ming", "Mei Ling", "Aiden", "Priya", "Hari", "Siti", "Daniel", "Yi Xuan",
    "Aravind", "Chloe", "Wai Kit", "Aisha", "Jia En", "Marcus", "Hwee Lin", "Faisal",
]
_LAST_NAMES = ["Tan", "Lim", "Lee", "Ng", "Kumar", "Binte Rashid", "Wong", "Chen", "Goh", "Ravi"]
_CATEGORIES = [
    "Grade 18 Married plus 2 child",
    "Grade 15 Married plus 1 child",
    "Grade 12 Single",
    "All Employees",
    "Bargainable Class",
    "Professional Class",
    "Senior Manager",
    "Director",
    "Grade 20 and above",
    "Grade 10 Married",
    "S-Pass holders",
    "Employment Pass holders",
]
_PASSES = ["EP", "SP", "WP", "CITIZEN"]
_RELATIONSHIPS = ["Spouse", "Child", "Child", "Child"]


def _name(rng: random.Random) -> str:
    return f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"


def _nric(rng: random.Random, prefix: str = "S") -> str:
    return f"{prefix}{rng.randint(1000000, 9999999)}{rng.choice('ABCDEFGHJZ')}"


def _date_offset_iso(base: date, rng: random.Random, low: int, high: int) -> str:
    return (base + timedelta(days=rng.randint(low, high))).isoformat()


def generate(rows: int, out_dir: Path = OUTPUT_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    emp_path = out_dir / f"synthetic_employees_{rows}.xlsx"
    dep_path = out_dir / f"synthetic_dependants_{rows}.xlsx"

    # Employees
    wb = Workbook()
    ws = wb.active
    ws.title = "Employees"
    ws.append([
        "Staff ID", "Employee Name", "Identification No. (NRIC/FIN)", "Date of Birth",
        "Gender", "Marital Status", "Foreigner Employment Pass", "Nationality",
        "Monthly Salary", "Date of Hire", "Confirmation Date", "Effective Date",
        "Category", "Division", "Department", "Cost Centre", "Email", "Mobile",
    ])
    employees: list[tuple[str, str]] = []
    today = date(2026, 1, 1)
    for i in range(rows):
        staff_id = f"SYN{i + 1:04d}"
        name = _name(rng)
        category = rng.choice(_CATEGORIES)
        pass_kind = rng.choice(_PASSES)
        employees.append((staff_id, name))
        ws.append([
            staff_id, name, _nric(rng),
            _date_offset_iso(today, rng, -22000, -8000),
            rng.choice(["M", "F"]),
            rng.choice(["Single", "Married"]),
            pass_kind if pass_kind != "CITIZEN" else "",
            rng.choice(["Singapore", "Malaysia", "India", "China", "Philippines"]),
            rng.randint(2500, 18000),
            _date_offset_iso(today, rng, -3500, -200),
            _date_offset_iso(today, rng, -3300, 0),
            _date_offset_iso(today, rng, -3500, 0),
            category,
            rng.choice(["Engineering", "Operations", "Sales", "Finance"]),
            rng.choice(["RnD", "Manufacturing", "Procurement", "Accounts"]),
            f"CC{rng.randint(100, 999)}",
            f"{staff_id.lower()}@example.test",
            f"+65 8{rng.randint(1000000, 9999999)}",
        ])
    wb.save(emp_path)

    # Dependants (~half of employees get 1-2 dependants)
    wb = Workbook()
    ws = wb.active
    ws.title = "Dependants"
    ws.append([
        "Entity", "Staff ID", "Employee Name", "Employee's Identification No. (NRIC/FIN)",
        "Dependant Name", "Dependant's Identification No.", "Relationship",
        "Date of Marriage", "Gender", "Date of Birth", "Effective Date",
        "Termination Date", "Remarks",
    ])
    for staff_id, name in employees:
        if rng.random() > 0.5:
            continue
        for _ in range(rng.randint(1, 2)):
            rel = rng.choice(_RELATIONSHIPS)
            ws.append([
                "DEMO-CLIENT", staff_id, name, _nric(rng),
                _name(rng), _nric(rng), rel,
                _date_offset_iso(today, rng, -7000, -1000) if rel == "Spouse" else "",
                rng.choice(["M", "F"]),
                _date_offset_iso(today, rng, -20000, -50),
                _date_offset_iso(today, rng, -3500, 0),
                "",
                "",
            ])
    wb.save(dep_path)

    return emp_path, dep_path


def main() -> None:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROWS
    emp, dep = generate(rows)
    print(f"Wrote {emp} and {dep}")


if __name__ == "__main__":
    main()
