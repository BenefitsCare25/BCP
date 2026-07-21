"""Map an AI document extraction onto claim-form field suggestions.

The claim-review extractor (`ai_gateway.extract_claim_document`) returns a flat
list of generically-labelled fields plus a `document_type`; it does NOT tag which
field is "the amount" or "the provider" (the review AI does that fuzzy mapping
itself). At INTAKE we don't want a second AI call, so this module does the
label→form-field mapping deterministically, and infers the claimant + claim type
by intersecting the reading with what the member actually holds.

Everything here is a SUGGESTION — the member confirms/edits on the form and the
usual server-side validation (`assert_intake_valid`) still runs at submit. Pure
functions, no I/O, so the mapping is unit-testable in isolation.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from app.models import Employee, PolicyYear
from app.schemas.claims import (
    ClaimIntakeSuggestionOut,
    CoverageOptionsOut,
    InsuredClaimOption,
    IntakeClaimant,
    IntakeFields,
)
from app.services.claim_doc_types import DocTypeDefinition, classify_document
from app.services.claim_intake import (
    ALLOWED_CURRENCIES,
    GHS_SUB_TYPES,
    SUB_TYPE_HOSPITALISATION,
    SUB_TYPE_PHYSIO,
    SUB_TYPE_TCM,
)
from app.services.matching_engine import jaccard, tokenize
from app.services.roster_attributes import (
    NAME_KEYS,
    first_value,
    normalize_nric,
    nric_from_attrs,
)
from app.services.sg_hospitals import hospital_sector

# Below this the UI flags a field as a guess (matches the extractor's own
# differentiated-confidence convention).
_CONFIDENCE_FLOOR = 0.6
# Token-similarity a name match must clear.
_NAME_THRESHOLD = 0.6

_NRIC_RE = re.compile(r"^[A-Za-z]\d{7}[A-Za-z]$")
_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
# Date substring extractors — pull the date out of a value that may carry a
# trailing time ("27 JUN 2026 03:03 PM") or other noise.
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_TEXT_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})")  # 27 Jun 2026
_MDY_TEXT_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})")  # Jul 1, 2026
_DMY_NUM_RE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")  # 27/06/26, 27.06.2026

# Name fields that are NOT the patient — a hospital bill carries several "…Name"
# fields; only the patient's should drive claimant detection.
_NON_PATIENT_NAME = (
    "hospital", "clinic", "doctor", "physician", "provider", "company",
    "guarantor", "kin", "payer", "referring", "practitioner", "account",
    "contact", "employer",
)


# ── field accessors ───────────────────────────────────────────────────────────


def _fields(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in (document.get("fields") or []) if isinstance(f, dict)]


def _label(f: dict[str, Any]) -> str:
    return str(f.get("label", "")).lower()


def _val(f: dict[str, Any]) -> str:
    return str(f.get("value", "")).strip()


def _ftype(f: dict[str, Any]) -> str:
    return str(f.get("field_type", "")).lower()


def _conf(f: dict[str, Any]) -> float:
    try:
        return float(f.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _num(s: str) -> float | None:
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_date(s: str) -> date | None:
    """Pull a calendar date out of a field value, tolerating a trailing time
    ("27 JUN 2026 03:03 PM") or surrounding noise."""
    s = s.strip()
    if not s:
        return None
    if (m := _ISO_DATE_RE.search(s)):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    if (m := _DMY_TEXT_RE.search(s)) and (mon := _MONTHS.get(m[2][:3].lower())):
        try:
            return date(int(m[3]), mon, int(m[1]))
        except ValueError:
            pass
    if (m := _MDY_TEXT_RE.search(s)) and (mon := _MONTHS.get(m[1][:3].lower())):
        try:
            return date(int(m[3]), mon, int(m[2]))
        except ValueError:
            pass
    if (m := _DMY_NUM_RE.search(s)):
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        if mo > 12 and d <= 12:  # tolerate a mm/dd source
            d, mo = mo, d
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    return None


# ── individual field extraction ───────────────────────────────────────────────


def _amount_tier(label: str) -> int:
    """Rank a currency field by how well its label names the CLAIMABLE amount.

    A hospital bill lists a gross "Total Amount (Before Govt Subsidy)" AND the
    net "Final Amount Payable" — the claim wants the net payable, so payable/due
    beats a plain total, and a "before …" gross is demoted below everything."""
    if "before" in label:  # gross (before subsidy / before GST)
        return 0
    if (
        "payable" in label
        or "amount due" in label
        or "balance due" in label
        or ("final" in label and ("amount" in label or "bill" in label))
    ):
        return 3
    if "after" in label and ("total" in label or "amount" in label):
        return 2
    if any(k in label for k in ("total", "grand", "amount", "paid", "charge")):
        return 1
    return 0


# Labels marking an IDENTIFIER, not a money amount — a "Case Number" or
# "Invoice No" field can carry a number field_type and must never be read as
# the claimed amount.
_ID_LABEL_RE = re.compile(r"\b(?:no|number|num|ref|reference|hrn|case)\b|#|no\.")


def _amount(fields: list[dict[str, Any]]) -> tuple[float | None, float]:
    nums = [
        f for f in fields
        if _ftype(f) in ("currency", "number")
        and not _ID_LABEL_RE.search(_label(f))
    ]
    if not nums:
        return None, 0.0
    for want in (3, 2, 1):
        pool = [
            (f, v) for f in nums
            if _amount_tier(_label(f)) == want and (v := _num(_val(f))) and v > 0
        ]
        if pool:
            # Within a tier prefer a "final" label, then the larger figure.
            best, val = max(
                pool, key=lambda p: ("final" in _label(p[0]), p[1], _conf(p[0]))
            )
            return val, _conf(best)
    return None, 0.0


def _currency(fields: list[dict[str, Any]]) -> str | None:
    for f in fields:
        text = f"{_val(f)} {f.get('raw_text', '')}".upper()
        if _ftype(f) == "currency" or "currency" in _label(f) or _ftype(f) in ("number",):
            for code in ALLOWED_CURRENCIES:
                if re.search(rf"\b{code}\b", text):
                    return code
    return None


def _date_field(
    fields: list[dict[str, Any]], year: PolicyYear
) -> tuple[str | None, float]:
    cands = [f for f in fields if _ftype(f) == "date" or "date" in _label(f)]
    parsed = [(f, d) for f in cands if (d := _parse_date(_val(f)))]
    if not parsed:
        return None, 0.0
    ceiling = min(date.today(), year.end_date)
    in_window = [p for p in parsed if year.start_date <= p[1] <= ceiling]
    pool = in_window or parsed
    # On an inpatient bill the ADMISSION date is the incurred date — it beats
    # the invoice-issue date (printed at/after discharge).
    best = max(
        pool,
        key=lambda p: (
            any(k in _label(p[0]) for k in ("admission", "visit", "treatment")),
            any(k in _label(p[0]) for k in ("invoice", "service")),
            _conf(p[0]),
        ),
    )
    return best[1].isoformat(), _conf(best[0])


def _keyworded(
    fields: list[dict[str, Any]], keywords: tuple[str, ...]
) -> tuple[str | None, float]:
    cands = [f for f in fields if any(k in _label(f) for k in keywords)]
    if not cands:
        return None, 0.0
    best = max(cands, key=_conf)
    return _val(best) or None, _conf(best)


def _invoice_number(fields: list[dict[str, Any]]) -> tuple[str | None, float]:
    cands = [
        f for f in fields
        if any(k in _label(f) for k in ("invoice", "receipt", "bill", "reference"))
        and any(k in _label(f) for k in ("no", "number", "#", "num", "ref"))
    ]
    if not cands:
        # Inpatient bills often carry no "Invoice No" — the HRN (hospital
        # reference number) or Case Number is the bill's identifier.
        cands = [
            f for f in fields
            if "hrn" in _label(f)
            or ("case" in _label(f) and any(k in _label(f) for k in ("no", "number")))
        ]
    if not cands:
        return None, 0.0
    best = max(cands, key=_conf)
    return _val(best) or None, _conf(best)


def _is_patient_name_field(f: dict[str, Any]) -> bool:
    """A field naming the PATIENT — excludes the other "…Name" fields on a bill
    (hospital / doctor / guarantor / next-of-kin …) so they can't be mistaken
    for the claimant."""
    if _ftype(f) not in ("name", "text", "other", ""):
        return False
    lab = _label(f)
    if any(k in lab for k in _NON_PATIENT_NAME):
        return False
    if any(k in lab for k in ("patient", "claimant", "insured")):
        return True
    # A bare name/member field is a fallback only when it's a name type.
    return _ftype(f) == "name" and ("name" in lab or "member" in lab or not lab)


def _patient_name(fields: list[dict[str, Any]]) -> str | None:
    cands = [f for f in fields if _is_patient_name_field(f)]
    if not cands:
        return None
    # A label containing "patient" is the strongest signal.
    best = max(
        cands,
        key=lambda f: ("patient" in _label(f), "name" in _label(f), _conf(f)),
    )
    return _val(best) or None


def _extracted_nric(fields: list[dict[str, Any]]) -> str | None:
    for f in fields:
        if any(k in _label(f) for k in ("nric", "fin", "identification", "id no", "id number")):
            canon = normalize_nric(_val(f))
            if canon:
                return canon
    for f in fields:
        canon = normalize_nric(_val(f))
        if canon and _NRIC_RE.match(canon):
            return canon
    return None


# ── claimant + claim-type inference ───────────────────────────────────────────


def _detect_claimant(
    fields: list[dict[str, Any]],
    employee: Employee,
    dependants: list[dict[str, Any]],
) -> IntakeClaimant | None:
    attrs = employee.attribute_values or {}
    self_name = employee.employee_name or first_value(attrs, NAME_KEYS) or ""

    extracted_nric = _extracted_nric(fields)
    self_nric = nric_from_attrs(attrs)
    if extracted_nric and self_nric and extracted_nric == self_nric:
        return IntakeClaimant(kind="self", name=self_name or None, confidence=1.0)

    patient = _patient_name(fields)
    if not patient:
        return None

    pt = tokenize(patient)
    if not pt:
        return None
    scored = [("self", None, self_name, jaccard(pt, tokenize(self_name)))]
    for d in dependants:
        scored.append(
            ("dependant", d.get("id"), d.get("name") or "",
             jaccard(pt, tokenize(d.get("name") or "")))
        )
    kind, dep_id, name, score = max(scored, key=lambda c: c[3])
    if score < _NAME_THRESHOLD:
        return None
    return IntakeClaimant(
        kind=kind, dependant_id=dep_id, name=name or None, confidence=round(score, 2)
    )


def _entry_setting(opt: InsuredClaimOption, sub_type: str | None, label: str) -> str:
    if opt.requires_referral:
        return "specialist"
    if opt.category == "inpatient":
        return "inpatient_hosp" if sub_type == SUB_TYPE_HOSPITALISATION else "inpatient_other"
    if opt.category == "outpatient":
        if sub_type == SUB_TYPE_TCM:
            return "tcm"
        if sub_type == SUB_TYPE_PHYSIO:
            return "physio"
        if "dental" in label.lower():
            return "dental"
        return "gp"
    return "other"


def _target_settings(
    document: dict[str, Any],
    text: str,
    provider: str | None,
    doc_types: Sequence[DocTypeDefinition] | None,
) -> set[str]:
    dt = (document.get("document_type") or "").lower()
    text = text.lower()
    if "dental" in text or "dentist" in text:
        return {"dental"}
    if "physio" in text:
        return {"physio"}
    if any(k in text for k in ("tcm", "traditional chinese", "chinese physician")):
        return {"tcm"}
    if "referral" in dt:
        return {"specialist"}
    # Broker document-type registry: an alias title ("After Visit Summary",
    # "Endoscopy Report") or an invoice with inpatient markers (admission/
    # discharge/HRN/case/schemes) identifies an inpatient document even when
    # the free-text type carries no "discharge"/"hospital" wording.
    if classify_document(
        dt,
        _fields(document),
        definitions=doc_types,
        sector_hint=hospital_sector(provider),
    ) is not None:
        return {"inpatient_hosp", "inpatient_other"}
    if any(k in dt for k in ("discharge", "hospital bill", "medical report")):
        return {"inpatient_hosp", "inpatient_other"}
    # A provider in the SG hospital registry (incl. day-surgery centres and
    # names without a "hospital" token — NCCS, Thomson Medical) is an
    # inpatient setting; without this, "Novena Surgery Centre" would fall
    # through to the specialist branch on its "surgery" token.
    if hospital_sector(provider) is not None:
        return {"inpatient_hosp", "inpatient_other"}
    if "hospital" in text and "clinic" not in text:
        return {"inpatient_hosp", "inpatient_other"}
    if any(k in text for k in ("specialist", "surgery", "surgical")):
        return {"specialist"}
    if "prescription" in dt:
        return {"gp"}
    # Plain receipt / tax invoice — an outpatient bill with no strong signal.
    return {"gp", "dental"}


def _inpatient_subtype_from_text(text: str) -> str | None:
    """Narrow an inpatient/GHS claim to ONE of the four GHS sub-types from the
    bill wording. Returns a `GHS_SUB_TYPES` label, or None when nothing points
    clearly at one (leave it for the member). Order matters — the specific
    settings (dialysis/cancer, emergency, pre/post) are checked before the
    general hospitalisation catch-all, whose keywords also appear elsewhere."""
    t = text.lower()
    if any(k in t for k in ("dialysis", "cancer", "chemo", "oncolog", "radiotherap")):
        return GHS_SUB_TYPES[3]  # Kidney Dialysis/Cancer Treatment
    if any(k in t for k in ("emergency", "a&e", "a & e", " a and e", "casualty", "accident")):
        return GHS_SUB_TYPES[2]  # Emergency Accidental Outpatient Treatment
    if any(k in t for k in ("pre-hospitalis", "post-hospitalis", "pre and post",
                            "pre/post", "pre-/post", "follow up", "follow-up")):
        return GHS_SUB_TYPES[0]  # Follow up Pre-/Post-Hospitalisation
    if any(k in t for k in ("day surgery", "surgery", "operation", "admission",
                            "admitted", "ward", "inpatient", "warded", "hospitalis")):
        return GHS_SUB_TYPES[1]  # Hospitalisation/Day Surgery
    return None


def _infer_claim_type(
    document: dict[str, Any],
    coverage_opts: CoverageOptionsOut,
    claimant: IntakeClaimant | None,
    provider: str | None,
    doc_types: Sequence[DocTypeDefinition] | None,
) -> tuple[str | None, list[str]]:
    # Insured products offered for the resolved claimant (mirror the form's
    # claimant filter); default to all when the claimant is unknown.
    if claimant and claimant.kind == "dependant":
        insured = [
            o for o in coverage_opts.insured
            if o.covers_dependants and claimant.dependant_id in o.covered_dependant_ids
        ]
    else:
        insured = list(coverage_opts.insured)

    entries: list[tuple[str, str, str | None]] = []  # (value, setting, sub_type)
    for opt in insured:
        for i, ct in enumerate(opt.claim_types):
            entries.append((
                f"insured:{opt.product_code}:{i}",
                _entry_setting(opt, ct.sub_type, ct.label),
                ct.sub_type,
            ))

    labels = " ".join(_label(f) + " " + _val(f) for f in _fields(document))
    text = " ".join(filter(None, [document.get("document_type") or "", provider or "", labels]))
    targets = _target_settings(document, text, provider, doc_types)
    matched = [(v, setting, st) for v, setting, st in entries if setting in targets]

    # Inpatient bills match all four GHS sub-types by setting — narrow to the
    # one the wording points at (A&E → emergency, "day surgery" → hospitalisation…).
    if any(s in ("inpatient_hosp", "inpatient_other") for _, s, _ in matched):
        sub = _inpatient_subtype_from_text(text)
        narrowed = [v for v, _, st in matched if st == sub] if sub else []
        values = narrowed or [v for v, _, _ in matched]
    else:
        values = [v for v, _, _ in matched]

    # Flex only when a category name clearly appears in the reading (never the
    # default route for an insured bill). Containment, NOT jaccard: the document
    # token set is large, so a jaccard ratio could never clear a threshold —
    # every token of the (short) category name must simply be present.
    if coverage_opts.flex is not None:
        text_tokens = tokenize(text)
        for cat in coverage_opts.flex.categories:
            cat_tokens = tokenize(cat.name)
            if cat_tokens and cat_tokens <= text_tokens:
                values.append(f"flex:{cat.name}")

    if len(values) == 1:
        return values[0], []
    return None, values


# ── entry point ───────────────────────────────────────────────────────────────


def suggest_from_extraction(
    document: dict[str, Any],
    coverage_opts: CoverageOptionsOut,
    employee: Employee,
    year: PolicyYear,
    doc_types: Sequence[DocTypeDefinition] | None = None,
) -> ClaimIntakeSuggestionOut:
    """Turn one extracted document into claim-form suggestions. ``doc_types``
    is the client's configured document-type registry (None → defaults)."""
    fields = _fields(document)

    provider, provider_conf = _keyworded(
        fields,
        ("clinic", "hospital", "provider", "practice", "medical",
         "centre", "center", "dental", "surgery"),
    )
    amount, amount_conf = _amount(fields)
    incurred_date, date_conf = _date_field(fields, year)
    invoice_number, invoice_conf = _invoice_number(fields)
    diagnosis, diag_conf = _keyworded(fields, ("diagnosis", "condition"))
    currency = _currency(fields)

    out_fields = IntakeFields(
        provider_name=provider,
        incurred_date=incurred_date,
        invoice_number=invoice_number,
        amount=amount,
        currency=currency,
        diagnosis=diagnosis,
    )

    low: list[str] = []
    for name, value, conf in (
        ("provider_name", provider, provider_conf),
        ("amount", amount, amount_conf),
        ("incurred_date", incurred_date, date_conf),
        ("invoice_number", invoice_number, invoice_conf),
        ("diagnosis", diagnosis, diag_conf),
    ):
        if value is not None and conf < _CONFIDENCE_FLOOR:
            low.append(name)

    claimant = _detect_claimant(fields, employee, coverage_opts.dependants)
    selection, candidates = _infer_claim_type(
        document, coverage_opts, claimant, provider, doc_types
    )

    # Broker document-type registry: which recognised document this is, and —
    # when unambiguous — the required-document slot it fills, so the form can
    # place the upload into the RIGHT slot instead of blindly the first.
    defn = classify_document(
        document.get("document_type"),
        fields,
        definitions=doc_types,
        sector_hint=hospital_sector(provider),
    )

    return ClaimIntakeSuggestionOut(
        available=True,
        document_type=document.get("document_type") or None,
        detected_doc_type=defn.display if defn else None,
        doc_slot=defn.slot_key if defn else None,
        claimant=claimant,
        claim_selection=selection,
        claim_candidates=candidates,
        fields=out_fields,
        low_confidence=low,
    )
