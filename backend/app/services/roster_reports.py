"""Downloadable coverage reports — employee + dependant listings with their
resolved insurance and flex coverage.

Both are listing-level (one row per person, plan codes + wallet summary), not the
full Schedule of Benefits — that detail stays on the per-employee benefit
statement. NRIC/FIN is masked (PII). Terminated leavers are excluded.

Assembly is batched: ``hydrate_plans`` resolves every active employee's
insurance in one pass; ``build_benefit_statement`` is called only for the
(smaller) set of employees who actually have active dependants, to resolve which
products cover each dependant.
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dependant, Employee, PolicyYear
from app.models.dependant import DEPENDANT_STATUS_ACTIVE
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.services.benefit_statement import build_benefit_statement
from app.services.plan_hydration import hydrate_plans
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    DOB_KEYS,
    EMPLOYEE_ID_KEYS,
    REL_KEYS,
    first_value,
    iso_date,
    mask_nric,
)


def _active_employees(db: Session, policy_year_id: str) -> list[Employee]:
    return list(
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


def _flex_summary(emp: Employee) -> tuple[str, str]:
    """(tier label, wallet display) from the persisted flex snapshot."""
    tier = emp.flex_tier_name or ""
    if emp.flex_wallet_amount is None:
        return tier, ""
    currency = emp.flex_currency or ""
    amount = f"{emp.flex_wallet_amount:,.2f}".rstrip("0").rstrip(".")
    return tier, f"{currency} {amount}".strip()


def _autosize(ws: Worksheet) -> None:
    """Widen columns to fit their content (cap so a long note can't balloon)."""
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 48)


def build_employee_report_workbook(db: Session, policy_year_id: str) -> Workbook:
    employees = _active_employees(db, policy_year_id)
    plans_by_emp = hydrate_plans(employees, db, policy_year_id)

    # Product columns = the union of products anyone is covered for, ordered.
    product_codes: list[str] = sorted(
        {p.product_code for plans in plans_by_emp.values() for p in plans}
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Employee coverage"
    header = [
        "Staff ID",
        "Employee Name",
        "NRIC/FIN",
        "Date of Birth",
        "Pass",
        "Family Status",
        "Category",
        *product_codes,
        "Flex Tier",
        "Flex Wallet",
    ]
    ws.append(header)

    for emp in employees:
        attrs = emp.attribute_values or {}
        emp_plans = plans_by_emp.get(emp.id, [])
        # A member can hold several plans under one product (e.g. GPA options),
        # so accumulate every plan code per product rather than overwriting.
        by_code: dict[str, list[str]] = {}
        for p in emp_plans:
            by_code.setdefault(p.product_code, []).append(p.plan_code or "✓")
        category = next(
            (p.category_display for p in emp_plans if p.category_display), None
        ) or first_value(attrs, ("category",))
        flex_tier, flex_wallet = _flex_summary(emp)
        row: list[object] = [
            emp.staff_id,
            emp.employee_name or "",
            mask_nric(first_value(attrs, EMPLOYEE_ID_KEYS)),
            iso_date(first_value(attrs, DOB_KEYS)) or "",
            first_value(attrs, ("pass",)) or "",
            emp.flex_family_status or first_value(attrs, ("marital_status",)) or "",
            category or "",
        ]
        for code in product_codes:
            codes = by_code.get(code)
            row.append(", ".join(dict.fromkeys(codes)) if codes else "")
        row.extend([flex_tier, flex_wallet])
        ws.append(row)

    _autosize(ws)
    return wb


def build_dependant_report_workbook(db: Session, policy_year_id: str) -> Workbook:
    db.get(PolicyYear, policy_year_id)  # touch for search_path / existence parity
    employees = {e.id: e for e in _active_employees(db, policy_year_id)}

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

    # Which products cover each dependant — resolved via the benefit statement,
    # only for employees that actually have dependants (bounded work).
    emp_ids_with_deps = {d.employee_id for d in dependants if d.employee_id}
    covered_by: dict[str, list[str]] = {}
    for emp_id in emp_ids_with_deps:
        emp = employees.get(emp_id)
        if emp is None:
            continue
        statement = build_benefit_statement(db, emp)
        for line in statement.coverage:
            for cd in line.covered_dependants:
                covered_by.setdefault(cd.id, []).append(line.product_code)

    wb = Workbook()
    ws = wb.active
    ws.title = "Dependant coverage"
    ws.append([
        "Employee Staff ID",
        "Employee Name",
        "Dependant Name",
        "Relationship",
        "Date of Birth",
        "NRIC/FIN",
        "Status",
        "Insurance Products",
        "Flex Tier",
        "Flex Wallet",
    ])

    for dep in dependants:
        attrs = dep.attribute_values or {}
        emp = employees.get(dep.employee_id) if dep.employee_id else None
        flex_tier, flex_wallet = _flex_summary(emp) if emp else ("", "")
        products = ", ".join(sorted(set(covered_by.get(dep.id, []))))
        ws.append([
            emp.staff_id if emp else "",
            (emp.employee_name if emp else "") or "",
            first_value(attrs, ("dependant_name", "name", "full_name")) or "",
            first_value(attrs, REL_KEYS) or "",
            iso_date(first_value(attrs, DOB_KEYS)) or "",
            mask_nric(first_value(attrs, DEPENDANT_ID_KEYS)),
            dep.status,
            products,
            flex_tier,
            flex_wallet,
        ])

    _autosize(ws)
    return wb
