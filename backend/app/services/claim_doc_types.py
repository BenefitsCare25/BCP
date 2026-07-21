"""Document-type identification — broker-configurable registry.

Identifies the specific claim documents the broker recognises, via per-type
ALIASES (alternate titles hospitals print) and KEY FIELDS (a completeness
check — the fields a genuine copy of that document always carries). Seeded
defaults (broker-specified, 2026-07-21):

- Discharge Summary        — aliases: After Visit Summary / Clinical
  Discharge Summary / Endoscopy Report; key fields: Diagnosis, Surgery.
- Final Tax Invoice        — the PRIVATE-hospital inpatient bill; key
  fields: Case Number, Admission/Discharge Date, Final Bill, HRN.
- Tax Invoice (Finalised)  — the GOVERNMENT (GRH) inpatient bill; key
  fields: Admission/Discharge Date, Schemes, HRN.

The VOCABULARY lives in the ``claim_doc_types`` table (per client, edited on
the broker claims page, lazily seeded from ``DEFAULT_DOC_TYPES``); a client
with no rows falls back to the defaults, so classification never depends on
config existing. The classification LOGIC stays in code: sector-neutral
types match by alias alone; sector-bearing invoice types share the generic
"Invoice"/"Tax Invoice" title, so they additionally need inpatient markers
(admission/discharge/ward/HRN/case/scheme fields) or the claim's hospital
sector, with govt vs private decided by marker fields (Schemes = government
subsidy section, Case Number = private) and the sector as tie-break.

Consumers: intake claim-type inference (`claim_intake_suggest`) and the
broker-side completeness check in the AI review pipeline
(`claims_review.doc_completeness`).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClaimDocType
from app.services.claim_intake import DOC_DISCHARGE_SUMMARY, DOC_FINALISED_TAX_INVOICE
from app.services.sg_hospitals import SECTOR_GOVT, SECTOR_PRIVATE

# Private-hospital final bill — detection-only (the private upload slots stay
# Summary + Itemised + Discharge Summary; this key never names a slot).
DOC_FINAL_TAX_INVOICE = "final_tax_invoice"


@dataclass(frozen=True)
class KeyField:
    """One completeness-check field: present when any extracted field's
    label (or value, for section-style fields like Schemes) contains one of
    the match tokens.

    ``optional`` fields are shown to the broker and checked, but their absence
    is NOT a completeness warning — a genuine copy of the document does not
    ALWAYS carry them (e.g. a discharge summary for a non-surgical admission
    has no surgery/procedure). Only a missing REQUIRED field is flagged."""

    name: str
    tokens: tuple[str, ...]
    optional: bool = False


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
        # A non-surgical admission (medical observation, IV antibiotics) has no
        # surgery — expected, not a completeness gap. Optional so it never
        # false-warns.
        KeyField("Surgery", ("surgery", "operation", "procedure"), optional=True),
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

DEFAULT_DOC_TYPES: tuple[DocTypeDefinition, ...] = (
    DISCHARGE_SUMMARY,
    FINAL_TAX_INVOICE,
    FINALISED_TAX_INVOICE,
)

DEFAULT_KEYS: frozenset[str] = frozenset(d.key for d in DEFAULT_DOC_TYPES)

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


# ── DB-backed resolution ──────────────────────────────────────────────────────


def definition_from_row(row: ClaimDocType) -> DocTypeDefinition:
    """Build a definition from a stored row, defensively — JSON columns can
    carry legacy shapes; a key field with no keywords matches on its name."""
    aliases = tuple(
        _norm(a) for a in (row.aliases or []) if isinstance(a, str) and a.strip()
    )
    key_fields: list[KeyField] = []
    for kf in row.key_fields or []:
        if not isinstance(kf, dict):
            continue
        name = str(kf.get("name", "")).strip()
        if not name:
            continue
        tokens = tuple(
            _norm(t)
            for t in (kf.get("keywords") or [])
            if isinstance(t, str) and t.strip()
        ) or (_norm(name),)
        key_fields.append(KeyField(name, tokens, optional=bool(kf.get("optional"))))
    sector = row.sector if row.sector in (SECTOR_GOVT, SECTOR_PRIVATE) else None
    return DocTypeDefinition(
        key=row.key,
        display=row.display,
        aliases=aliases,
        key_fields=tuple(key_fields),
        sector=sector,
        slot_key=row.slot_key,
    )


def client_doc_type_rows(db: Session, client_id: str) -> list[ClaimDocType]:
    return list(
        db.execute(
            select(ClaimDocType)
            .where(ClaimDocType.client_id == client_id)
            .order_by(ClaimDocType.created_at, ClaimDocType.key)
        ).scalars()
    )


def resolve_doc_types(
    db: Session, client_id: str | None
) -> tuple[DocTypeDefinition, ...]:
    """The client's configured document types, or the in-code defaults when
    none are stored — classification must keep working with zero config."""
    if not client_id:
        return DEFAULT_DOC_TYPES
    rows = client_doc_type_rows(db, client_id)
    if not rows:
        return DEFAULT_DOC_TYPES
    return tuple(definition_from_row(r) for r in rows)


def seed_default_doc_types(db: Session, client_id: str) -> list[ClaimDocType]:
    """Materialize the in-code defaults as this client's rows (first read of
    the config surface / explicit reset). Caller commits."""
    rows = [
        ClaimDocType(
            client_id=client_id,
            key=d.key,
            display=d.display,
            aliases=list(d.aliases),
            key_fields=[
                {"name": kf.name, "keywords": list(kf.tokens), "optional": kf.optional}
                for kf in d.key_fields
            ],
            sector=d.sector,
            slot_key=d.slot_key,
        )
        for d in DEFAULT_DOC_TYPES
    ]
    db.add_all(rows)
    db.flush()
    return rows


# ── classification + completeness ─────────────────────────────────────────────


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
    definitions: Sequence[DocTypeDefinition] | None = None,
    sector_hint: str | None = None,
) -> DocTypeDefinition | None:
    """Which broker-recognised document this extraction is, or None.

    ``sector_hint`` is the claim's hospital sector (from `hospital_sector` on
    the provider) — the tie-break when an invoice shows inpatient markers but
    neither the govt (Schemes) nor private (Case Number) marker fields.
    """
    defs = tuple(definitions) if definitions is not None else DEFAULT_DOC_TYPES
    dt = _norm(document_type or "")
    if not dt:
        return None
    labels = _label_text(fields)

    # Sectored invoice types are MORE SPECIFIC than a generic sector-neutral
    # type that merely shares the "invoice"/"tax invoice" title — so when the
    # document actually looks like an inpatient bill (marker fields present, or
    # the claim's hospital sector is known), resolve those FIRST. This stops a
    # broker's custom sector-neutral type from shadowing the govt/private
    # classification just by carrying the same alias.
    sectored = [d for d in defs if d.sector is not None and dt in d.aliases]
    if sectored and (any(m in labels for m in _INPATIENT_MARKERS) or sector_hint is not None):
        def _by_sector(sector: str) -> DocTypeDefinition | None:
            return next((d for d in sectored if d.sector == sector), None)

        if any(m in labels for m in _GOVT_MARKERS):
            return _by_sector(SECTOR_GOVT)
        if any(m in labels for m in _PRIVATE_MARKERS):
            return _by_sector(SECTOR_PRIVATE)
        if sector_hint in (SECTOR_GOVT, SECTOR_PRIVATE):
            return _by_sector(sector_hint)
        # Markers present but sector genuinely ambiguous — fall through to a
        # sector-neutral match rather than guessing govt vs private.

    # Sector-neutral types (discharge summary, custom types) match on title.
    for d in defs:
        if d.sector is None and dt in d.aliases:
            return d
    return None


def missing_key_fields(
    definition: DocTypeDefinition, fields: list[dict[str, Any]]
) -> list[str]:
    """REQUIRED key fields a genuine copy of this document always carries but
    the extraction doesn't show — completeness signal for the broker, never a
    member block. Optional key fields (e.g. Surgery on a medical admission) are
    excluded so a legitimately-complete document is not flagged."""
    hays = _haystacks(fields)
    return [
        kf.name
        for kf in definition.key_fields
        if not kf.optional and not any(t in h for t in kf.tokens for h in hays)
    ]
