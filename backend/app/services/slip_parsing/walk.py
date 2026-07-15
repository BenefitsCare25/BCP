"""Basis-of-Cover column identification and the category data-row walk."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.services.excel_reader import Cell
from app.services.slip_parsing.models import ExtractedCategory
from app.services.slip_parsing.participation import parse_participation
from app.services.slip_parsing.text import (
    _FOOTNOTE_SPLIT,
    _PLAN_INLINE,
    _PREMIUM_TRAILER,
    _RATE_CODE,
    _SKIP_PHRASES,
    _int_code,
    _non_empty,
    _norm,
    _row_text,
    _safe_float,
)


@dataclass(frozen=True)
class _Columns:
    insured: int
    category: int
    participation: int
    plan: int
    num_employees: int = -1
    basis: int = -1
    sum_insured: int = -1


def _identify_columns(header_row: list[Cell]) -> _Columns:
    """Match column headers by prefix (lowercased).

    Real placement slips use variations like 'Category / Name' or
    'Plan / Region'. The prototype used exact match and silently lost the
    Chubb-GBT sheet on STM; prefix match recovers it without breaking any
    of the standard-header sheets.
    """
    insured = category = participation = plan = -1
    num_employees = basis = sum_insured = -1
    for c, value in enumerate(header_row):
        if value is None:
            continue
        h = str(value).strip().lower()
        if insured < 0 and h.startswith("insured"):
            insured = c
        elif category < 0 and h.startswith("category"):
            category = c
        elif participation < 0 and h.startswith("participation"):
            participation = c
        elif plan < 0 and h.startswith("plan"):
            plan = c
        elif num_employees < 0 and ("no." in h or "number" in h) and "employee" in h:
            num_employees = c
        elif basis < 0 and h.startswith("basis"):
            basis = c
        elif sum_insured < 0 and "sum" in h and ("insured" in h or "assured" in h):
            sum_insured = c
    return _Columns(insured, category, participation, plan, num_employees, basis, sum_insured)


# A category whose text names a dependant population rather than an employee
# group: GPA's "Spouse (Option 1)" / "Child (Option 2)" option rows, and VDL's
# dependants-sheet categories ("Grade 40 & above … eligible dependants").
_DEP_CATEGORY_RE = re.compile(
    r"^(spouse|child(?:ren)?|dependan[td]s?)\b", re.IGNORECASE
)


def _category_member_scope(
    cat: str, product_code: str, participation: str = ""
) -> str | None:
    """"dependant" when this category covers dependants STANDALONE, else None.

    Three signals, in reliability order: the compound product code (a whole
    GHS-DEPENDANTS sheet is dependant-scope), a dependant-population name
    ("Spouse (Option 1)", "Child (Option 2)", "Dependants of …"), or a
    dependant-only participation cell ("Voluntary - Dependents": no employee
    mode at all). A COMPOSITE category covering employees and their dependants
    together ("SM and above … / All Eligible Dependants on Voluntary basis")
    keeps its employee mode and is NOT dependant-scope. Dependant-scope
    categories feed dependant pricing downstream — they are never employee
    election tiers.
    """
    if (product_code or "").strip().upper().endswith("DEPENDANTS"):
        return "dependant"
    if _DEP_CATEGORY_RE.match(cat):
        return "dependant"
    if participation:
        spec = parse_participation(participation)
        if spec.employee is None and spec.dependant is not None:
            return "dependant"
    return None


def _realign_category_column(
    rows: list[list[Cell]], header_idx: int, cols: _Columns
) -> _Columns:
    """Correct a merged-cell column shift between header and data.

    Some slips (VDL WICA) print the "Category" header over what is really the
    *insured* column, with the category text landing 1-3 columns to the right
    under no header at all. Detect it by data shape: when the category column
    holds at most one value across the first data rows but a nearby unclaimed
    column holds several, shift the category column there — and treat the
    original column as Insured when no Insured header was found.
    """
    if cols.category < 0:
        return cols
    claimed = {
        cols.insured, cols.participation, cols.plan,
        cols.num_employees, cols.basis, cols.sum_insured,
    }
    window = rows[header_idx + 1 : header_idx + 9]

    def _count(col: int) -> int:
        return sum(1 for r in window if r and col < len(r) and _non_empty(r[col]))

    if _count(cols.category) > 1:
        return cols
    best, best_n = -1, 1
    for col in range(cols.category + 1, cols.category + 4):
        if col in claimed:
            continue
        n = _count(col)
        if n > best_n:
            best, best_n = col, n
    if best < 0:
        return cols
    insured = cols.insured if cols.insured >= 0 else cols.category
    return replace(cols, insured=insured, category=best)


def _walk_data_rows(
    rows: list[list[Cell]],
    header_idx: int,
    cols: _Columns,
    product_code: str = "",
) -> tuple[ExtractedCategory, ...]:
    cols = _realign_category_column(rows, header_idx, cols)
    out: list[ExtractedCategory] = []
    current_insured = ""
    last_participation = ""
    # Plan code carried onto continuation rows of a merged block. Some layouts
    # (per-member GCGP/GCSP) list several categories under ONE plan, printing the
    # plan number only on the block's first row; the rest are blank-plan
    # continuations that belong to the same plan. Reset at each new insured block.
    last_plan_code = ""
    consec_blank = 0
    for i in range(header_idx + 1, len(rows)):
        row = rows[i] or []
        text = _row_text(row)
        upper = text.upper()
        if "FIGURES ABOVE ARE FOR" in upper or "ACTUAL FIGURES" in upper:
            break
        if re.fullmatch(r"\s*rate\s*:?\s*", text.strip(), re.IGNORECASE):
            break
        if upper.startswith("RATE :"):
            break
        if "SCHEDULE OF BENEFITS" in upper:
            break

        cat_cell = row[cols.category] if cols.category < len(row) else None
        plan_cell = row[cols.plan] if 0 <= cols.plan < len(row) else None

        if not _non_empty(cat_cell) and not _non_empty(plan_cell):
            consec_blank += 1
            if consec_blank >= 3:
                break
            continue
        consec_blank = 0

        cat_str = _norm(cat_cell) if _non_empty(cat_cell) else ""
        plan_raw = _norm(plan_cell) if _non_empty(plan_cell) else ""
        # Strip footnote text merged into the plan cell, e.g.
        # "3 * Bargainable employees is eligible for 4 Bed ..." → "3". Without
        # this the plan code can't be matched to its Rate-section row and the
        # category loses its premium. Compound codes like "1 / International"
        # have no '*' and are preserved.
        if plan_raw:
            plan_raw = _FOOTNOTE_SPLIT.split(plan_raw, maxsplit=1)[0].strip()
        # Normalize numeric plan codes ("1.0" → "1") to match Plan.code
        plan_str = _int_code(plan_raw) if plan_raw else ""

        if cat_str.startswith("*") or "FIGURES" in cat_str.upper():
            break
        if cat_str.lower() in {
            "category", "eo", "es", "ec", "ef", "rate :", "rate",
            # Summary/footer rows: a "Total" / "Sub Total" row often carries a
            # headcount, which would otherwise vouch for it as a genuine row past
            # the noise filters below. Exact-match so real category names that
            # merely contain the word aren't dropped.
            "total", "sub total", "subtotal", "grand total", "sub-total",
        }:
            continue

        if 0 <= cols.insured < len(row) and _non_empty(row[cols.insured]):
            current_insured = _norm(row[cols.insured])
            # A populated insured cell marks a new block — stop carrying the
            # previous block's plan code (the block's own first row sets it).
            last_plan_code = ""
        this_participation = ""
        if 0 <= cols.participation < len(row) and _non_empty(row[cols.participation]):
            # Keep the RAW cell text — the direction ("Downgrade / Upgrade") and
            # the employee/dependant split are lost by normalize_participation and
            # are needed downstream by enrollment. Consumers normalize as needed.
            this_participation = _norm(row[cols.participation])
            last_participation = this_participation

        if not cat_str:
            # A plan-code-only continuation row lists ANOTHER plan available to
            # the block's current category: GHS-style voluntary blocks print one
            # merged category cell with successive plan codes below it
            # ("SM and above … Voluntary - Downgrade → D01 / D02 / D03"). Emit a
            # sibling row for the same population so every listed plan code
            # survives extraction. Adjacency-guarded so a stray plan-column cell
            # far from any category can't clone a stale row.
            if (
                plan_str
                and out
                and plan_str != out[-1].plan_code
                and (i + 1) - out[-1].source_row <= 2
            ):
                prev = out[-1]
                has_basis = 0 <= cols.basis < len(row) and _non_empty(row[cols.basis])
                row_ne = _safe_float(row, cols.num_employees)
                out.append(replace(
                    prev,
                    plan_code=plan_str,
                    participation=last_participation,
                    source_row=i + 1,
                    num_employees=round(row_ne) if row_ne is not None else None,
                    basis=_norm(row[cols.basis]) if has_basis else None,
                    sum_insured=_safe_float(row, cols.sum_insured),
                ))
                last_plan_code = plan_str
            continue

        # Strip footnote text and parenthetical premium trailers
        cat = _FOOTNOTE_SPLIT.split(cat_str, maxsplit=1)[0].strip()
        cat = _PREMIUM_TRAILER.sub("", cat).strip()

        # If no plan column populated, try to extract "Plan X:" from the
        # category text itself (common in older slips).
        if not plan_str:
            m = _PLAN_INLINE.match(cat)
            if m:
                plan_str = m.group(1).strip()
                cat = m.group(2).strip()

        # Carry the block's plan code onto continuation rows: an explicit plan
        # code becomes the block's carry; a blank-plan category inherits it. This
        # maps every category of a merged single-plan block (GCGP/GCSP) to its
        # plan so the Rate section's per-plan rate reaches all of them.
        if plan_str:
            last_plan_code = plan_str
        elif last_plan_code:
            plan_str = last_plan_code

        # Extract financial columns if present in the row (read before the noise
        # filters so a populated headcount can vouch for a genuine row).
        ne_val = _safe_float(row, cols.num_employees)

        member_scope = _category_member_scope(cat, product_code, last_participation)

        # A row pairing a category cell with an explicit EMPLOYEE-scoped
        # Compulsory/Voluntary marker — OR a populated headcount on a continuation
        # row (WICA's "All Others" rows repeat the company's participation only on
        # the first line) — is a genuine basis-of-cover row, even when the label is
        # short ("CEO") or looks like a bare code. Bypass the noise filters (min
        # length + rate-code shape) in that case. A purely dependant-scoped marker
        # ("Voluntary - Dependents" on a GTL/GCI Spouse / Child plan row) does NOT
        # qualify, and those rows carry no headcount — so they stay excluded
        # (capturing one without its siblings would be inconsistent). A category
        # that is itself dependant-scope (a dependants sheet, a "Spouse (Option
        # N)" option row) is genuine as dependant data — it feeds dependant
        # pricing, not the employee tier list.
        is_genuine_row = (
            parse_participation(this_participation).employee is not None
            or (ne_val is not None and ne_val > 0)
            or member_scope == "dependant"
        )
        if len(cat) < 6 and not is_genuine_row:
            continue
        cat_lower = cat.lower()
        if any(p in cat_lower for p in _SKIP_PHRASES):
            continue
        if _RATE_CODE.fullmatch(cat.strip()) and not is_genuine_row:
            continue

        has_basis = 0 <= cols.basis < len(row) and _non_empty(row[cols.basis])
        basis_val = _norm(row[cols.basis]) if has_basis else None
        si_val = _safe_float(row, cols.sum_insured)

        out.append(
            ExtractedCategory(
                insured=current_insured,
                category=cat,
                participation=last_participation,
                plan_code=plan_str,
                source_row=i + 1,
                num_employees=round(ne_val) if ne_val is not None else None,
                basis=basis_val,
                sum_insured=si_val,
                location_scope=parse_participation(last_participation).scope,
                member_scope=member_scope,
            )
        )

    # Some sum-assured layouts (GPA) carry no Plan column and no inline "Plan N:"
    # — each category IS its own sum-insured tier. When the whole sheet yielded no
    # plan codes, number the categories sequentially so they link to plans like the
    # inline-coded GTL/GCI do (1-per-tier); a broker can still merge/rename on the
    # cards. Sheets that produced any code keep theirs untouched.
    if out and not any(c.plan_code for c in out):
        out = [replace(c, plan_code=str(i + 1)) for i, c in enumerate(out)]

    return tuple(out)
