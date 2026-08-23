"""The policy-header + eligibility block at the top of every product sheet.

Its SHAPE is the document's, not a product's: every reference slip opens with
the same Group / Policyholder / Insured / … / Type of Administration ladder, so
that ladder is fixed here. Its VALUES are entirely the product's own — pulled
from the guided-setup answers the broker captured for that product, whose field
set is declared by the product's template, not by this module.

That split is what keeps the block dynamic. A product whose template declares a
field the ladder doesn't name still exports it: every captured answer left over
after the ladder is appended below, labelled with the template's own label. Add
a field to a template and it appears on the slip with no change here.
"""
from __future__ import annotations

from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from app.models import PolicyYear, Product, ProductTerm
from app.services.product_insurer import insurer_from_answers
from app.services.product_templates import (
    ProductTemplate,
    ensure_standard_header_fields,
    get_template,
    standard_eligibility_fields,
)
from app.services.slip_export.styles import label_value_rows

# The ladder, as the reference slips print it: (slip label, answers field id).
# A None field id means the value is computed rather than captured; a None entry
# is a spacer row.
_LADDER: tuple[tuple[str, str | None] | None, ...] = (
    ("Group :", "group"),
    ("Policyholder :", "policyholder"),
    ("Insured :", "insured"),
    ("Address :", "address"),
    ("Business :", "business"),
    ("Period of Insurance :", "period_of_insurance"),
    ("Insurer :", "insurer"),
    ("Pool :", "pool"),
    ("Policy No. :", "policy_no"),
    None,
    ("Eligibility :", "eligibility"),
    ("Eligibility Date :", "eligibility_date"),
    ("Last entry age :", "last_entry_age"),
    None,
    ("Type of Administration :", "admin_basis"),
)

# Field ids the ladder prints, so the dynamic tail below can skip them.
_LADDER_IDS = frozenset(f for entry in _LADDER if entry for f in (entry[1],) if f)

# Answers that are structural rather than descriptive — they drive matching and
# pricing and are already rendered as their own tables, so echoing them as
# header text would just duplicate (and could contradict) those sections.
_NOT_HEADER_TEXT = frozenset({"entities"})


def captured_answers(answers: dict[str, Any]) -> dict[str, str]:
    """Flatten a setup's header + eligibility blocks into ``{field id: text}``.

    Both blocks share one id namespace (the template validator guarantees it),
    so one lookup serves the whole ladder. Multi-select answers arrive as lists;
    they render as a comma list.
    """
    out: dict[str, str] = {}
    for block in ("header", "eligibility"):
        values = answers.get(block)
        if not isinstance(values, dict):
            continue
        for key, raw in values.items():
            if key in _NOT_HEADER_TEXT:
                continue
            if isinstance(raw, (list, tuple)):
                text = ", ".join(str(v).strip() for v in raw if str(v).strip())
            else:
                text = str(raw or "").strip()
            if text:
                out[key] = text
    return out


def _template_labels(product: Product | None) -> dict[str, str]:
    """``{field id: label}`` for whatever fields this product's form declares.

    A curated template supplies its own; anything else falls back to the
    canonical field set every template is guaranteed to carry (the
    ``ProductTemplate`` validator merges it in), so a slip-synthesized product
    still gets real labels rather than raw ids.
    """
    tpl: ProductTemplate | None = get_template(product.code) if product else None
    if tpl is not None:
        fields = list(tpl.header_fields) + list(tpl.eligibility_fields)
    else:
        has_dependants = bool(product.has_dependants) if product else False
        fields = ensure_standard_header_fields([]) + standard_eligibility_fields(
            has_dependants
        )
    return {f.id: f.label for f in fields}


def _humanize(field_id: str) -> str:
    return field_id.replace("_", " ").strip().title()


def fmt_window(start: Any, end: Any) -> str:
    if not start or not end:
        return ""
    return f"{start:%d %b %Y} to {end:%d %b %Y}"


def coverage_window(py: PolicyYear, term: ProductTerm | None) -> str:
    start = term.coverage_start if term and term.coverage_start else py.start_date
    end = term.coverage_end if term and term.coverage_end else py.end_date
    return fmt_window(start, end)


def write_header_block(
    ws: Worksheet,
    py: PolicyYear,
    product: Product | None,
    term: ProductTerm | None,
    answers: dict[str, Any],
    insured_line: str,
    quotation: bool,
) -> None:
    """Render the ladder, then every remaining captured field beneath it."""
    captured = captured_answers(answers)
    labels = _template_labels(product)

    # A quotation goes out to prospective insurers, so the incumbent's identity
    # and issued policy number stay blank alongside the rates.
    computed: dict[str, str] = {
        # The entity list resolved from configuration is authoritative; the
        # captured free text is the slip's own wording and stands in when the
        # product was built without entities.
        "insured": insured_line or captured.get("insured", ""),
        "period_of_insurance": coverage_window(py, term)
        or captured.get("period_of_insurance", ""),
        # The insurer this benefit year places the product with — the broker's
        # Header & Policy answer, never a catalog tag (see product_insurer).
        "insurer": "" if quotation else insurer_from_answers(answers, product),
        "policy_no": "" if quotation else (term.policy_number if term else "") or "",
        # The legal policyholder as captured on the slip; the client record's
        # name is an internal short name ("CDL") and must not go to an insurer.
        "policyholder": captured.get("policyholder")
        or (py.client.name if py.client else ""),
    }

    rows: list[tuple[str, str] | None] = []
    for entry in _LADDER:
        if entry is None:
            rows.append(None)
            continue
        label, field_id = entry
        if field_id is None:
            rows.append((label, ""))
            continue
        value = computed[field_id] if field_id in computed else captured.get(field_id, "")
        rows.append((label, value))

    # Dynamic tail: anything this product's form captured that the ladder does
    # not print. Template order, template labels.
    extras: list[tuple[str, str]] = [
        (f"{labels.get(fid) or _humanize(fid)} :", str(captured[fid]))
        for fid in list(labels) + [k for k in captured if k not in labels]
        if fid in captured and fid not in _LADDER_IDS
    ]
    seen: set[str] = set()
    unique_extras: list[tuple[str, str]] = []
    for row in extras:
        if row[0] in seen:
            continue
        seen.add(row[0])
        unique_extras.append(row)
    if unique_extras:
        rows.append(None)
        rows.extend(unique_extras)

    label_value_rows(ws, rows)
