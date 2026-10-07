"""Read a company's own Employee Listing workbook.

``read_employee_listing`` returns the detected layout (employee, dependant and
per-product column blocks), every employee with their dependant rows, and
row-level issues. Pure and DB-free; mapping blocks to the company's products
and persisting members happen in the import service.
"""
from app.services.el_workbook.layout import detect_layout
from app.services.el_workbook.models import (
    ElBlock,
    ElColumn,
    ElCover,
    ElDependant,
    ElEmployee,
    ElIssue,
    ElLayout,
    ElWorkbook,
)
from app.services.el_workbook.rows import ElFormatError, read_employee_listing, read_rows

__all__ = [
    "ElBlock",
    "ElColumn",
    "ElCover",
    "ElDependant",
    "ElEmployee",
    "ElFormatError",
    "ElIssue",
    "ElLayout",
    "ElWorkbook",
    "detect_layout",
    "read_employee_listing",
    "read_rows",
]
