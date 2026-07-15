"""Render a :class:`FactFindContext` into the bundled Fact-Find ``.docx``.

The template is filled by *label matching*, not absolute coordinates: we walk the
document body in order, track the current product-section heading and the last
sub-heading paragraph, and dispatch each table to a handler chosen from its own
header text. This survives minor template edits (added rows, reordered cells)
far better than hard-coded indices.

Cells we cannot resolve are left exactly as the template has them — blank, for
the broker to complete by hand.
"""
from __future__ import annotations

import copy
import io
import re
from typing import TYPE_CHECKING

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from sqlalchemy.orm import Session

from app.models import PolicyYear
from app.services.fact_find_form import (
    AGE_BANDS,
    MAX_BASIS_ROWS,
    TEMPLATE_PATH,
    build_context,
    section_for_code,
)

if TYPE_CHECKING:
    from app.services.fact_find_form import FactFindContext, SectionContext

_NUMERAL_RE = re.compile(r"^([ivx]+)\)", re.I)

# Lowercase roman numerals for renumbering expanded basis rows (i) to xx)).
_ROMAN = (
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
)


def _roman(n: int) -> str:
    return _ROMAN[n - 1] if 1 <= n <= len(_ROMAN) else str(n)

# Paragraph-heading text → section code. Matched as a prefix (case-insensitive).
_HEADINGS: tuple[tuple[str, str], ...] = (
    ("GENERAL INFORMATION", "GI"),
    ("GROUP TERM LIFE", "GTL"),
    ("GROUP PERSONAL ACCIDENT", "GPA"),
    ("GROUP HOSPITAL & SURGICAL", "GHS"),
    ("GROUP CATASTROPHIC MEDICAL", "GCM"),
    ("GROUP CLINICAL GENERAL PRACTITIONER", "GCGP_GCSP"),
    ("GROUP BUSINESS TRAVEL", "GBT"),
    ("NEEDS ANALYSIS", "_NEEDS"),
    ("DECLARATION", "_DECL"),
)


def _heading_section(text: str) -> str | None:
    up = text.strip().upper()
    for prefix, code in _HEADINGS:
        if up.startswith(prefix):
            return code
    return None


def _ctext(cell: _Cell) -> str:
    return " ".join(p.text for p in cell.paragraphs).strip()


def _set(cell: _Cell, text: str | int) -> None:
    """Write ``text`` into a (normally empty) cell, keeping its paragraph style."""
    s = str(text)
    para = cell.paragraphs[0]
    if para.runs:
        para.runs[0].text = s
        for r in para.runs[1:]:
            r.text = ""
    else:
        para.add_run(s)


def _mark(cell: _Cell) -> None:
    _set(cell, "X")


def render_docx(ctx: FactFindContext) -> bytes:
    doc = Document(str(TEMPLATE_PATH))

    section = "GI"
    last_para = ""
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            txt = Paragraph(child, doc).text.strip()
            if txt:
                hs = _heading_section(txt)
                if hs is not None:
                    section = hs
                last_para = txt
        elif tag == "tbl":
            table = Table(child, doc)
            _fill_table(ctx, section, last_para, table)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _header_text(table: Table) -> str:
    if not table.rows:
        return ""
    return " | ".join(_ctext(c) for c in table.rows[0].cells)


def _fill_table(ctx: FactFindContext, section: str, last_para: str, table: Table) -> None:
    header = _header_text(table)
    first = _ctext(table.rows[0].cells[0]) if table.rows else ""

    if section == "GI":
        if first.startswith("Name of Company"):
            _fill_company(ctx, table)
        elif first.startswith("Insurance Coverage"):
            _fill_matrix(ctx, table)
        return

    sec = ctx.sections.get(section)
    if sec is None:
        return  # product not configured on this policy year → leave page blank

    if first.startswith("Presently Insured"):
        _fill_presently_insured(sec, table)
    elif first.startswith("Eligibility"):
        _fill_eligibility(sec, table)
    elif "Age Band" in header:
        _fill_age_band(sec, table)
    elif "Singaporeans" in header:
        _fill_family(sec, table, sec.family_local)
    elif "Holders of EP" in header or "EP, SP" in header:
        _fill_family(sec, table, sec.family_foreign)
    elif first.startswith("Category of Employees") and last_para.strip().lower() == (
        "basis of cover"
    ):
        _fill_basis(sec, table)


# ── General Information ──────────────────────────────────────────────────────
def _fill_company(ctx: FactFindContext, table: Table) -> None:
    for row in table.rows:
        label = _ctext(row.cells[0])
        if label.startswith("Name of Company") and len(row.cells) > 1:
            _set(row.cells[1], ctx.company_name)
        elif label.startswith("Country of Origin"):
            # row: [Country][value][Total Number of Employees:][value]
            for i, c in enumerate(row.cells):
                if _ctext(c).startswith("Total Number of Employees") and i + 1 < len(row.cells):
                    _set(row.cells[i + 1], ctx.total_employees)


def _fill_matrix(ctx: FactFindContext, table: Table) -> None:
    """Mark Compulsory/Voluntary for each configured product line.

    Each product occupies two physical rows: the first (carrying the 'Group …'
    label) is the employees row, the row immediately after is dependants.
    """
    rows = table.rows
    i = 0
    while i < len(rows):
        label = _ctext(rows[i].cells[0])
        m = re.search(r"\(([A-Z][A-Z &]*)\)", label)
        code = section_for_code(_canon_matrix_code(m.group(1))) if m else None
        if code and code in ctx.sections:
            sec = ctx.sections[code]
            col = 2 if (sec.participation or "").startswith("vol") else 1
            if len(rows[i].cells) > col:
                _mark(rows[i].cells[col])
            if sec.has_dependants and i + 1 < len(rows) and len(rows[i + 1].cells) > col:
                _mark(rows[i + 1].cells[col])
            i += 2
        else:
            i += 1


def _canon_matrix_code(raw: str) -> str:
    """Normalise a matrix label code like 'GCGP & GCSP' to a single code."""
    raw = raw.strip().upper()
    return raw.split("&")[0].strip()


# ── Per-product pages ────────────────────────────────────────────────────────
def _fill_presently_insured(sec: SectionContext, table: Table) -> None:
    for row in table.rows:
        label = _ctext(row.cells[0])
        if label.startswith("If Yes") and "insurer" in label and len(row.cells) > 1:
            _set(row.cells[1], sec.insurer)
        elif label.startswith("Period of Insurance"):
            # [Period of Insurance:][from][value][to][value]
            cells = row.cells
            for j, c in enumerate(cells):
                t = _ctext(c)
                if t == "from" and j + 1 < len(cells) and sec.period_from:
                    _set(cells[j + 1], sec.period_from)
                elif t == "to" and j + 1 < len(cells) and sec.period_to:
                    _set(cells[j + 1], sec.period_to)


def _fill_eligibility(sec: SectionContext, table: Table) -> None:
    for row in table.rows:
        cells = row.cells
        joined = " ".join(_ctext(c) for c in cells)
        is_emp_only = "Employees only" in joined and "Dependants" not in joined
        is_emp_dep = "Employees and Dependants" in joined
        if not (is_emp_only or is_emp_dep):
            continue
        want = is_emp_dep if sec.has_dependants else is_emp_only
        # Mark the cross cell (the cell immediately before the label cell).
        for j, c in enumerate(cells):
            t = _ctext(c)
            if t in ("Employees only", "Employees and Dependants") and j > 0 and want:
                _mark(cells[j - 1])
            if t.startswith("No. of employees") and j + 1 < len(cells) and want:
                _set(cells[j + 1], sec.employees_count)
            if t.startswith("No. of dependants") and j + 1 < len(cells) and want:
                _set(cells[j + 1], sec.dependants_count)


def _is_vmerge_continuation_row(row, anchor_col: int) -> bool:
    """True when ``row`` continues a vertical merge in its anchor column.

    Reads the raw ``<w:tc>`` of the physical row (not python-docx's merge-resolved
    cell, which returns the origin and so always looks like a ``restart``). A
    ``<w:vMerge>`` without ``val="restart"`` marks a continuation row.
    """
    tcs = row._tr.findall(qn("w:tc"))
    if anchor_col >= len(tcs):
        return False
    tc_pr = tcs[anchor_col].find(qn("w:tcPr"))
    if tc_pr is None:
        return False
    v_merge = tc_pr.find(qn("w:vMerge"))
    if v_merge is None:
        return False
    return v_merge.get(qn("w:val")) != "restart"


def _basis_units(table: Table, anchor_col: int) -> list[list[int]]:
    """Group physical row indices into category units.

    A unit begins at a numbered origin row and absorbs the vertical-merge
    continuation rows beneath it (the GCGP/GCSP page stacks 3 physical rows per
    category). Header / Total / blank rows are ignored.
    """
    units: list[list[int]] = []
    for i, r in enumerate(table.rows):
        if not _NUMERAL_RE.match(_ctext(r.cells[0])):
            continue
        if _is_vmerge_continuation_row(r, anchor_col):
            if units:
                units[-1].append(i)
        else:
            units.append([i])
    return units


def _clone_last_unit(table: Table, units: list[list[int]]) -> None:
    """Deep-copy the last category unit's ``<w:tr>`` rows and append them after
    it, preserving borders, column widths and the vertical-merge structure."""
    insert_after = table.rows[units[-1][-1]]._tr
    for ridx in units[-1]:
        clone = copy.deepcopy(table.rows[ridx]._tr)
        insert_after.addnext(clone)
        insert_after = clone


def _write_designation(
    cells: list[_Cell], c_num: int | None, c_desig: int | None,
    numeral: str, designation: str,
) -> None:
    """Write the row's numeral and designation, robust to both template layouts.

    When the numbered ("i)") and designation columns are distinct cells, the
    numeral goes in the narrow cell and the text in the wide one. When a single
    cell holds both (un-merged template variant), they are written together.
    The numeral is always the freshly computed one, so cloned rows can't keep a
    stale numeral copied from the template's last slot.
    """
    if c_desig is None or c_desig >= len(cells):
        return
    desig_cell = cells[c_desig]
    num_cell = cells[c_num] if (c_num is not None and c_num < len(cells)) else None
    if num_cell is not None and desig_cell is not num_cell:
        _set(num_cell, numeral)
        _set(desig_cell, designation)
        return
    _set(desig_cell, f"{numeral} {designation}".strip())


def _fill_basis(sec: SectionContext, table: Table) -> None:
    """Fill numbered category rows with designation, count and cover.

    The row layout differs per product, so we resolve target columns from the
    header labels rather than fixed indices.
    """
    headers = [_ctext(c).lower() for c in table.rows[0].cells]

    def col(*needles: str) -> int | None:
        for idx, h in enumerate(headers):
            if any(n in h for n in needles):
                return idx
        return None

    # "Category of Employees / Designation" is a header merged across two grid
    # columns: a narrow numbered column ("i)", "ii)") and the wide designation
    # column. Resolve both endpoints so the designation lands in the wide cell —
    # writing it into the narrow numbered cell wraps it one char per line.
    desig_cols = [
        i for i, h in enumerate(headers)
        if "category of employees" in h or "designation" in h
    ]
    c_num = desig_cols[0] if desig_cols else None
    c_desig = desig_cols[-1] if desig_cols else None
    c_count = col("no. of employees")
    c_plan = col("plan name")
    c_class = col("classification")
    c_rb = col("room & board", "room and board")
    c_basis = col("basis of cover", "sum insured")

    anchor_col = c_num if c_num is not None else 0
    # Group physical rows into category "units" (one numbered row + any
    # vertical-merge continuation rows below it), then clone the last unit until
    # every category has a row. This lets the form grow past its template's
    # i) to iv) slots instead of dropping the smaller cohorts.
    units = _basis_units(table, anchor_col)
    if not units:
        return
    needed = min(len(sec.basis_rows), MAX_BASIS_ROWS)
    while len(units) < needed:
        before = len(units)
        _clone_last_unit(table, units)
        units = _basis_units(table, anchor_col)
        if len(units) <= before:
            break  # safety: a clone that isn't re-detected as a unit would loop forever

    for idx, (unit, br) in enumerate(zip(units, sec.basis_rows, strict=False)):
        cells = table.rows[unit[0]].cells
        _write_designation(cells, c_num, c_desig, f"{_roman(idx + 1)})", br.designation)
        # Always write (even blank) so cloned rows don't inherit stale values.
        if c_count is not None and c_count < len(cells):
            _set(cells[c_count], br.num_employees)
        if c_plan is not None and c_plan < len(cells):
            _set(cells[c_plan], br.plan_name)
        if c_class is not None and c_class < len(cells):
            _set(cells[c_class], br.classification)
        if c_rb is not None and c_rb < len(cells):
            _set(cells[c_rb], br.room_board)
        if c_basis is not None and c_basis < len(cells):
            _set(cells[c_basis], br.sum_insured or br.room_board)


def _fill_age_band(sec: SectionContext, table: Table) -> None:
    if not sec.age_bands:
        return
    # Columns: find the Male / Female header cells (second header row usually).
    male_col = female_col = None
    for hrow in table.rows[:2]:
        for j, c in enumerate(hrow.cells):
            t = _ctext(c).lower()
            if t == "male" and male_col is None:
                male_col = j
            elif t == "female" and female_col is None:
                female_col = j
    if male_col is None or female_col is None:
        return
    band_labels = {lbl for lbl, _, _ in AGE_BANDS}
    for row in table.rows:
        label = _ctext(row.cells[0])
        if label in band_labels and label in sec.age_bands:
            m, f = sec.age_bands[label]
            if male_col < len(row.cells):
                _set(row.cells[male_col], m)
            if female_col < len(row.cells):
                _set(row.cells[female_col], f)


def _fill_family(sec: SectionContext, table: Table, data: dict[str, dict[str, int]]) -> None:
    """Family-composition matrix: Plan rows × (EO / ES / EC / EF)."""
    if not data:
        return
    # Map header columns to family buckets.
    bucket_col: dict[str, int] = {}
    for hrow in table.rows[:2]:
        for j, c in enumerate(hrow.cells):
            t = _ctext(c).lower()
            if "employee only" in t:
                bucket_col["EO"] = j
            elif "spouse" in t:
                bucket_col["ES"] = j
            elif "child" in t:
                bucket_col["EC"] = j
            elif "family" in t:
                bucket_col["EF"] = j
    if not bucket_col:
        return
    plan_rows = [r for r in table.rows if _ctext(r.cells[0]).lower().startswith("plan")]
    # Fill the most-populated plans first so a template with fewer rows than
    # plans keeps the largest cohorts (the rest are flagged in completeness).
    ordered = sorted(data.items(), key=lambda kv: -sum(kv[1].values()))
    for row, (plan_name, counts) in zip(plan_rows, ordered, strict=False):
        _set(row.cells[0], plan_name)
        for bucket, jcol in bucket_col.items():
            if jcol < len(row.cells):
                _set(row.cells[jcol], counts.get(bucket, 0))


def generate(db: Session, policy_year: PolicyYear) -> tuple[bytes, list[str]]:
    """Build the context and render the filled ``.docx``; returns (bytes, notes)."""
    ctx = build_context(db, policy_year)
    return render_docx(ctx), ctx.completeness


__all__ = ["generate", "render_docx"]
