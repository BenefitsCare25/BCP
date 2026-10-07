"""The Full Employee Listing's column layout for one company.

A company that uploaded its own Employee Listing gets that listing's layout
back (same blocks, headings and column positions). Others get a layout built
from their products in the same template shape: an EMPLOYEE block, a
DEPENDANTS block, then one block per product type whose columns depend on how
that product is rated.
"""
from __future__ import annotations

from typing import Any

from openpyxl.utils import get_column_letter

from app.models import Product
from app.services import product_registry
from app.services.el_workbook import ElBlock, ElColumn, ElLayout

_EMPLOYEE = (
    ("movement", "A - Addition\nD - Deletion\nC - Changes"),
    ("staff_id", "Employee ID No."),
    ("name", "Name\n(Surname, First Name)"),
    ("gender", "Sex (F/M)"),
    ("national_id", "NRIC No. or FIN No."),
    ("department", "Dept"),
    ("designation", "Designation"),
    ("grade", "Grade"),
    ("citizenship", "Citizenship Type"),
    ("date_of_birth", "Date of Birth\n(DD/MMM/YY)"),
    ("age", "Age"),
    ("location", "Location"),
    ("salary", "Salary"),
    ("date_of_hire", "Date of Hire\n(DD/MMM/YY)"),
    ("email", "Email Address"),
    ("mobile", "Mobile No."),
)
_DEPENDANT = (
    ("name", "Name\n(Surname, First Name)"),
    ("relationship", "Relationship\nSpouse - S Child - C"),
    ("gender", "Sex\n(F/M)"),
    ("national_id", "NRIC No. or\nFIN No."),
    ("nationality", "Nationality\n(If not S'porean or PR)"),
    ("date_of_birth", "Date of Birth\n(DD/MMM/YY)"),
    ("age", "Age"),
)
_TRAILING = (
    ("last_day_of_service", "Last day of service (DD/MMM/YY)"),
    ("remarks", "REMARKS"),
    ("internal_note", "Internal use - Updated date of changes"),
)
_LIFE = ("admin_type", "age", "category", "present_si", "eligible_si", "pending_si",
         "mu_status", "mu_decision", "mu_letter_member", "mu_letter_insurer",
         "last_accepted_si", "loading_rate", "acceptance_date", "premium")
_ACCIDENT = ("admin_type", "age", "category", "eligible_si", "premium", "premium_gst")
_TIERED = ("admin_type", "age", "category", "plan_type", "family_group", "premium",
           "premium_gst")
_FLAT = ("admin_type", "age", "category", "plan_type", "premium", "premium_gst")
_PRODUCT_HEADERS = {
    "admin_type": "Type of Administration\nHeadcount / Named Basis",
    "age": "Age",
    "category": "{code} Category",
    "present_si": "{code}\n(Present Sum Insured)",
    "eligible_si": "{code}\nEligible Sum Insured",
    "pending_si": "{code}\n(Pending Sum Insured)",
    "mu_status": "{code}\n(MU Status)",
    "mu_decision": "{code}\n(MU Decision)",
    "mu_letter_member": "{code} New/Renewal MU Letter Sent Date (DD/MMM/YY)",
    "mu_letter_insurer": "{code} New/Renewal MU Letter Sent Date to Insurer (DD/MMM/YY)",
    "last_accepted_si": "{code}\n(Last Accepted Sum Insured at Std Rate)",
    "loading_rate": "{code}\n(H &/or R Premium Rate)",
    "acceptance_date": "{code} Acceptance Date\n(DD/MMM/YY)",
    "plan_type": "{code}\n(Plan Type)",
    "family_group": "{code}\n(Family Group) EO / ES / EC / EF",
    "premium": "{code} Premium",
    "premium_gst": "{code} Premium with GST",
}


def layout_from_json(data: dict[str, Any]) -> ElLayout:
    def cols(items: list[dict[str, Any]]) -> tuple[ElColumn, ...]:
        return tuple(ElColumn(int(c["index"]), c["letter"], c["header"], c["role"])
                     for c in items)

    return ElLayout(
        sheet=data.get("sheet") or "Employee Listing",
        banner_row=int(data.get("banner_row", 1)),
        header_row=int(data.get("header_row", 2)),
        blocks=tuple(
            ElBlock(b["kind"], b["banner"], cols(b["columns"]), b.get("code_hint"))
            for b in data.get("blocks") or []
        ),
        trailing=cols(data.get("trailing") or []),
    )


def _roles_for(products: list[Product], rate_bases: dict[str, set[str]]) -> tuple[str, ...]:
    entry = product_registry.get_entry(product_registry.base_code(products[0].code))
    bases = set().union(*(rate_bases.get(p.code, set()) for p in products))
    if entry is not None and entry.line == "life":
        return _LIFE
    if "per_1000_si" in bases:
        return _ACCIDENT
    if "tiered" in bases:
        return _TIERED
    return _FLAT


def default_layout(
    products: list[Product], rate_bases: dict[str, set[str]]
) -> tuple[ElLayout, dict[int, list[str]]]:
    """Template-shaped layout for a company without its own listing layout."""
    groups: dict[str, list[Product]] = {}
    for product in sorted(products, key=lambda p: p.code):
        bases = rate_bases.get(product.code, set())
        if bases and bases <= {"earnings_based", "annual_flat"}:
            continue  # rated on payroll or per policy, not per person
        groups.setdefault(product_registry.base_code(product.code), []).append(product)

    position = 0
    blocks: list[ElBlock] = []
    block_products: dict[int, list[str]] = {}

    def columns(roles: Any, code: str = "") -> tuple[ElColumn, ...]:
        nonlocal position
        out = []
        for role, header in roles:
            out.append(ElColumn(position, get_column_letter(position + 1),
                                header.format(code=code), role))
            position += 1
        return tuple(out)

    blocks.append(ElBlock("employee", "EMPLOYEE", columns(_EMPLOYEE)))
    blocks.append(ElBlock("dependant", "DEPENDANTS", columns(_DEPENDANT)))
    order = {e.code: i for i, e in enumerate(product_registry.entries())}
    for base in sorted(groups, key=lambda b: (order.get(b, 999), b)):
        members = groups[base]
        name = members[0].display_name.split(" (")[0].upper()
        roles = _roles_for(members, rate_bases)
        block_products[len(blocks)] = [p.code for p in members]
        blocks.append(ElBlock(
            "product", f"{name} ({base})", columns(((r, _PRODUCT_HEADERS[r]) for r in roles), base),
            base,
        ))
    trailing = columns(_TRAILING)
    return ElLayout("Employee Listing", 1, 2, tuple(blocks), trailing), block_products
