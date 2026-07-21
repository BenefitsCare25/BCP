"""Document-type identification registry (broker-specified, 2026-07-21).

Maps an AI-extracted ``document_type`` + field labels onto the specific
inpatient claim documents the broker recognises, via per-type ALIASES
(alternate titles hospitals print) and KEY FIELDS (a completeness check —
the fields a genuine copy of that document always carries):

- Discharge Summary        — aliases: After Visit Summary / Clinical
  Discharge Summary / Endoscopy Report; key fields: Diagnosis, Surgery.
- Final Tax Invoice        — the PRIVATE-hospital inpatient bill; key
  fields: Case Number, Admission/Discharge Date, Final Bill, HRN.
- Tax Invoice (Finalised)  — the GOVERNMENT (GRH) inpatient bill; key
  fields: Admission/Discharge Date, Schemes, HRN.

Both invoice types share the generic "Invoice"/"Tax Invoice" title, so an
invoice only classifies here when it shows inpatient markers (admission/
discharge/ward/HRN/case/scheme fields) or the claim's hospital sector says
so; govt vs private is decided by the marker fields (Schemes = government
subsidy section, Case Number = private) with the sector as tie-break.

Consumers: intake claim-type inference (`claim_intake_suggest`) and the
broker-side completeness check in the AI review pipeline
(`claims_review.pipeline`). In-code for v1, same convention as
`field_maps.py` / `sg_hospitals.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.claim_intake import DOC_DISCHARGE_SUMMARY, DOC_FINALISED_TAX_INVOICE
from app.services.sg_hospitals import SECTOR_GOVT, SECTOR_PRIVATE

# Private-hospital final bill — detection-only (the private upload slots stay
# Summary + Itemised + Discharge Summary; this key never names a slot).
DOC_FINAL_TAX_INVOICE = "final_tax_invoice"


@dataclass(frozen=True)
class KeyField:
    """One completeness-check field: present when any extracted field's
    label (or value, for section-style fields like Schemes) contains one of
    the match tokens."""

    name: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class DocTypeDefinition:
    key: str
    display: str
    # Lowercased titles (exact after normalization) that identify this type.
    aliases: tuple[str, ...]
    key_fields: tuple[KeyField, ...]
    # govt/private for the invoice pair; None = sector-independent.
    sector: str | None = None
    # The required-document slot this upload fills, when unambiguous.
    slot_key: str | None = None


DISCHARGE_SUMMARY = DocTypeDefinition(
    key=DOC_DISCHARGE_SUMMARY,
    display="Discharge Summary",
    aliases=(
        "discharge summary",
        "after visit summary",
        "clinical discharge summary",
        "endoscopy report",
    ),
    key_fields=(
        KeyField("Diagnosis", ("diagnosis", "condition")),
        KeyField("Surgery", ("surgery", "operation", "procedure")),
    ),
    slot_key=DOC_DISCHARGE_SUMMARY,
)

FINAL_TAX_INVOICE = DocTypeDefinition(
    key=DOC_FINAL_TAX_INVOICE,
    display="Final Tax Invoice",
    aliases=("invoice", "tax invoice", "final tax invoice", "hospital bill"),
    key_fields=(
        KeyField("Case Number", ("case",)),
        KeyField("Admission Date", ("admission",)),
        KeyField("Discharge Date", ("discharge",)),
        KeyField("Final Bill", ("final bill", "final amount", "amount payable")),
        KeyField("HRN", ("hrn", "hospital reference")),
    ),
    sector=SECTOR_PRIVATE,
)

FINALISED_TAX_INVOICE = DocTypeDefinition(
    key=DOC_FINALISED_TAX_INVOICE,
    display="Tax Invoice (Finalised)",
    aliases=(
        "invoice",
        "tax invoice",
        "tax invoice (finalised)",
        "finalised tax invoice",
        "hospital bill",
    ),
    key_fields=(
        KeyField("Admission Date", ("admission",)),
        KeyField("Discharge Date", ("discharge",)),
        KeyField(
            "Schemes", ("scheme", "medishield", "medisave", "chas", "subsidy")
        ),
        KeyField("HRN", ("hrn", "hospital reference")),
    ),
    sector=SECTOR_GOVT,
    slot_key=DOC_FINALISED_TAX_INVOICE,
)

DOC_TYPE_DEFINITIONS: tuple[DocTypeDefinition, ...] = (
    DISCHARGE_SUMMARY,
    FINAL_TAX_INVOICE,
    FINALISED_TAX_INVOICE,
)

_INVOICE_TYPES = (FINAL_TAX_INVOICE, FINALISED_TAX_INVOICE)

# Field-label tokens that mark an invoice as an INPATIENT bill (vs a plain
# clinic receipt, which shares the "tax invoice" title).
_INPATIENT_MARKERS = (
    "admission", "discharge", "ward", "hrn", "case", "scheme",
    "medishield", "medisave", "inpatient",
)

# Markers that decide govt vs private when both alias-match.
_GOVT_MARKERS = ("scheme", "medishield", "medisave", "chas", "subsidy")
_PRIVATE_MARKERS = ("case",)


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def _haystacks(fields: list[dict[str, Any]]) -> list[str]:
    return [
        _norm(f"{f.get('label', '')} {f.get('value', '')}")
        for f in fields
        if isinstance(f, dict)
    ]


def _label_text(fields: list[dict[str, Any]]) -> str:
    return " ".join(
        _norm(str(f.get("label", ""))) for f in fields if isinstance(f, dict)
    )


def classify_document(
    document_type: str | None,
    fields: list[dict[str, Any]],
    *,
    sector_hint: str | None = None,
) -> DocTypeDefinition | None:
    """Which broker-recognised document this extraction is, or None.

    ``sector_hint`` is the claim's hospital sector (from `hospital_sector` on
    the provider) — the tie-break when an invoice shows inpatient markers but
    neither the govt (Schemes) nor private (Case Number) marker fields.
    """
    dt = _norm(document_type or "")
    if not dt:
        return None
    if dt in DISCHARGE_SUMMARY.aliases:
        return DISCHARGE_SUMMARY

    if not any(dt in d.aliases for d in _INVOICE_TYPES):
        return None
    labels = _label_text(fields)
    if not any(m in labels for m in _INPATIENT_MARKERS) and sector_hint is None:
        return None  # a plain outpatient receipt also says "tax invoice"
    if any(m in labels for m in _GOVT_MARKERS):
        return FINALISED_TAX_INVOICE
    if any(m in labels for m in _PRIVATE_MARKERS):
        return FINAL_TAX_INVOICE
    if sector_hint == SECTOR_GOVT:
        return FINALISED_TAX_INVOICE
    if sector_hint == SECTOR_PRIVATE:
        return FINAL_TAX_INVOICE
    return None


def missing_key_fields(
    definition: DocTypeDefinition, fields: list[dict[str, Any]]
) -> list[str]:
    """Key fields a genuine copy of this document carries but the extraction
    doesn't show — completeness signal for the broker, never a member block."""
    hays = _haystacks(fields)
    return [
        kf.name
        for kf in definition.key_fields
        if not any(t in h for t in kf.tokens for h in hays)
    ]
