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

from app.core.clock import today as business_today
from app.models import Employee, PolicyYear
from app.schemas.claims import (
    ClaimIntakeSuggestionOut,
    CoverageOptionsOut,
    InsuredClaimOption,
    IntakeClaimant,
    IntakeFields,
)
from app.services.claim_doc_types import (
    DocTypeDefinition,
    classify_document,
    definition_targets_scope,
)
from app.services.claim_intake import (
    ALLOWED_CURRENCIES,
    GHS_GENERIC_SCOPE_CODES,
    GHS_SUB_TYPES,
    SUB_TYPE_HOSPITALISATION,
    SUB_TYPE_PHYSIO,
    SUB_TYPE_TCM,
    claim_profile_for,
    claim_scope_key,
    normalize_invoice_number,
    scope_code_for_sub_type,
    sector_scope_code,
)
from app.services.claims_ai_confidence import confidence_threshold
from app.services.matching_engine import jaccard, tokenize
from app.services.roster_attributes import (
    NAME_KEYS,
    first_value,
    normalize_nric,
    nric_from_attrs,
)
from app.services.sg_diagnoses import search_diagnoses
from app.services.sg_hospitals import SECTOR_GOVT, SECTOR_PRIVATE, hospital_sector

# The claim form stores an unlisted diagnosis behind this prefix (mirrors the
# frontend DiagnosisPicker + claim_intake.effective_diagnosis).
_OTHER_PREFIX = "Other: "

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

# Field-label keywords naming the treatment provider.
_PROVIDER_KEYWORDS = (
    "clinic", "hospital", "provider", "practice", "medical",
    "centre", "center", "dental", "surgery",
)

# Name fields that are NOT the patient — a hospital bill carries several "…Name"
# fields; only the patient's should drive claimant detection.
_NON_PATIENT_NAME = (
    "hospital", "clinic", "doctor", "physician", "provider", "company",
    "guarantor", "kin", "payer", "referring", "practitioner", "account",
    "contact", "employer",
)

# Field labels naming the treating doctor (the mirror image of the list above).
_DOCTOR_KEYWORDS = (
    "doctor", "physician", "surgeon", "consultant", "practitioner",
    "attending", "specialist name",
)
# …but not the doctor's credentials, their signature line, or free prose ABOUT
# the doctor: those carry a registration number, nothing at all, or a
# paragraph. The member is REQUIRED to submit this field on a pre/post claim,
# so a wrong prefill is one they have to notice and undo.
_NON_DOCTOR_LABEL = (
    "signature", "mcr", "licence", "license", "registration", "stamp",
    "note", "remark", "comment", "advice", "instruction", "fee", "charge",
)
# A name has letters in it. Registration numbers, amounts and codes don't.
_HAS_LETTERS_RE = re.compile(r"[A-Za-z]{2,}")

# Words that mean an OPERATION took place. Split by how a PRACTICE NAME can
# carry them: in Singapore (as in British usage) a "surgery" is what a doctor's
# practice is called — "Ang Mo Kio Family Surgery" is a GP clinic, not an
# operating theatre — so those two tokens prove nothing inside the provider's
# name and are scanned only OUTSIDE the fields that carry it. No practice is
# ever called an "operation" or a "post-op", so the strong set is scanned
# everywhere.
_WEAK_SURGICAL_MARKERS = ("surgery", "surgical")
_STRONG_SURGICAL_MARKERS = (
    "operation", "operative",
    "post-op", "post op", "postop", "pre-op", "pre op", "preop",
    # The claim type named outright on the bill.
    "pre-hospitalis", "post-hospitalis", "pre and post", "pre/post", "pre-/post",
    "pre-admission", "pre admission", "post-discharge", "post discharge",
)

_GP_CONSULT_RE = re.compile(
    r"\b(?:gp|general\s+practitioner|family\s+physician|family\s+medicine)\b"
    r"|general\s+(?:practice|medical)\s+consult(?:ation)?"
    r"|(?:gp|doctor)\s+consult(?:ation)?",
    re.IGNORECASE,
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
    # Exclude a field only when it looks like a pure IDENTIFIER — an ID token in
    # the label AND no amount signal. A field that names a real amount (tier > 0)
    # is kept even if the label also contains "case"/"no" etc. ("Total Case
    # Amount", "Amount Due No. 3"), so the guard can't drop a genuine total.
    nums = [
        f for f in fields
        if _ftype(f) in ("currency", "number")
        and (_amount_tier(_label(f)) > 0 or not _ID_LABEL_RE.search(_label(f)))
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
    # Business date, not the UTC one (`core/clock.py`) — the same clock the
    # served claim window and submit use. On UTC this refused to suggest today's
    # date for every claim filed between midnight and 8am Singapore.
    ceiling = min(business_today(), year.end_date)
    in_window = [p for p in parsed if year.start_date <= p[1] <= ceiling]
    pool = in_window or parsed
    # Prefer an explicit visit/treatment date. Admission is the fallback on an
    # inpatient bill that states no visit date; both beat the invoice date.
    def treatment_priority(field: dict[str, Any]) -> int:
        label = _label(field)
        if "visit" in label or "treatment" in label:
            return 3
        if "admission" in label or "admitted" in label:
            return 2
        if "service" in label or "invoice" in label or "bill" in label:
            return 1
        return 0

    best = max(
        pool,
        key=lambda p: (treatment_priority(p[0]), _conf(p[0])),
    )
    return best[1].isoformat(), _conf(best[0])


def _stay_date_field(
    fields: list[dict[str, Any]], keywords: tuple[str, ...]
) -> tuple[str | None, float, bool]:
    """Read one explicitly-labelled bound of an inpatient stay.

    Returns ``(value, confidence, conflict)``. Distinct readable values across
    an invoice/discharge-summary set are never silently collapsed: the form
    leaves that bound blank and asks the member to check it.
    """
    parsed = [
        (field, parsed_date)
        for field in fields
        if any(keyword in _label(field) for keyword in keywords)
        and (parsed_date := _parse_date(_val(field))) is not None
    ]
    if not parsed:
        return None, 0.0, False
    distinct = {parsed_date for _, parsed_date in parsed}
    confidence = max(_conf(field) for field, _ in parsed)
    if len(distinct) > 1:
        return None, confidence, True
    best = max(parsed, key=lambda item: _conf(item[0]))
    return best[1].isoformat(), _conf(best[0]), False


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


def _doctor_name(fields: list[dict[str, Any]]) -> tuple[str | None, float]:
    """The treating doctor named on the document.

    Prefers the attending/treating doctor over a REFERRING one: on a specialist
    invoice both appear, and the claim is about the doctor who provided the
    treatment — the referrer is already evidenced by the referral letter."""
    cands = [
        f for f in fields
        if any(k in _label(f) for k in _DOCTOR_KEYWORDS)
        and not any(k in _label(f) for k in _NON_DOCTOR_LABEL)
        and not _ID_LABEL_RE.search(_label(f))
        and _ftype(f) in ("name", "text", "other", "")
        # A name, not a registration number and not a paragraph about one.
        and _HAS_LETTERS_RE.search(_val(f))
        and len(_val(f)) <= 80
    ]
    if not cands:
        return None, 0.0
    best = max(
        cands,
        key=lambda f: (
            "referring" not in _label(f),
            any(k in _label(f) for k in ("attending", "surgeon", "consultant")),
            _conf(f),
        ),
    )
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


def _surgical_context(document: dict[str, Any], text: str) -> bool:
    """Whether the reading evidences an operation (or names the pre/post claim
    type outright) — see the marker lists above for the weak/strong split.

    The weak tokens are scanned over the fields that do NOT name the provider,
    rebuilt from the document rather than string-replaced out of ``text``.
    Both details matter: a global replace of the provider's name would also
    delete a genuine "Day Surgery Charges" line item from a clinic called
    "… Surgery" (suppressing detection in the other direction), and a reading
    where the provider was never extracted — an unlabelled letterhead — would
    keep the practice name in the scan and route an ordinary consult to a
    hospitalisation claim type.
    """
    if any(k in text for k in _STRONG_SURGICAL_MARKERS):
        return True
    body = " ".join(
        f"{_label(f)} {_val(f)}"
        for f in _fields(document)
        if not any(k in _label(f) for k in _PROVIDER_KEYWORDS)
        # An unlabelled name field is a letterhead until proven otherwise.
        and not (_ftype(f) == "name" and not _label(f))
    ).lower()
    return any(k in body for k in _WEAK_SURGICAL_MARKERS)


def _gp_consult_context(document: dict[str, Any]) -> bool:
    """Whether non-provider document text explicitly names a GP consultation.

    A generic receipt can belong to GP or Dental when both covers exist. The
    line item "General practitioner consultation" is a stronger signal than the
    receipt type itself, but provider names are excluded so a clinic brand does
    not force the route.
    """
    body = " ".join(
        f"{_label(f)} {_val(f)}"
        for f in _fields(document)
        if not any(k in _label(f) for k in _PROVIDER_KEYWORDS)
    )
    return bool(_GP_CONSULT_RE.search(body))


def _target_settings(
    document: dict[str, Any],
    text: str,
    provider: str | None,
    doc_types: Sequence[DocTypeDefinition] | None,
) -> tuple[set[str], bool]:
    """The claim settings this document could belong to, and whether it is an
    OUTPATIENT SPECIALIST bill evidencing surgery (which the caller pins to the
    pre-/post-hospitalisation sub-type)."""
    dt = (document.get("document_type") or "").lower()
    text = text.lower()
    if "dental" in text or "dentist" in text:
        return {"dental"}, False
    if "physio" in text:
        return {"physio"}, False
    if any(k in text for k in ("tcm", "traditional chinese", "chinese physician")):
        return {"tcm"}, False
    if "referral" in dt:
        return {"specialist"}, False
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
        return {"inpatient_hosp", "inpatient_other"}, False
    if any(k in dt for k in ("discharge", "hospital bill", "medical report")):
        return {"inpatient_hosp", "inpatient_other"}, False
    # A provider in the SG hospital registry (incl. day-surgery centres and
    # names without a "hospital" token — NCCS, Thomson Medical) is an
    # inpatient setting; without this, "Novena Surgery Centre" would fall
    # through to the specialist branch on its "surgery" token.
    if hospital_sector(provider) is not None:
        return {"inpatient_hosp", "inpatient_other"}, False
    if "hospital" in text and "clinic" not in text:
        return {"inpatient_hosp", "inpatient_other"}, False
    if any(k in text for k in ("specialist", "surgery", "surgical")):
        # A SPECIALIST invoice that evidences an operation is the consult
        # BEFORE or AFTER an admission, not an outpatient specialist visit: the
        # hospital bills the stay itself, so an operation can never appear on
        # the specialist clinic's own invoice except as the reason for the
        # visit. That is the pre-/post-hospitalisation sub-type of the
        # inpatient product — a different benefit, with a different limit, from
        # the SP cover the bill would otherwise be filed against.
        if _surgical_context(document, text):
            return {"specialist", "inpatient_other"}, True
        return {"specialist"}, False
    if "prescription" in dt:
        return {"gp"}, False
    if _gp_consult_context(document):
        return {"gp"}, False
    # Plain receipt / tax invoice — an outpatient bill with no strong signal.
    return {"gp", "dental"}, False


def _inpatient_subtype_from_text(
    text: str, *, specialist_surgical: bool = False
) -> str | None:
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
    if specialist_surgical:
        # Established by `_target_settings`: an outpatient specialist bill
        # referencing an operation. It must NOT fall into the hospitalisation
        # catch-all below — that sub-type is the admission itself, which this
        # bill by definition is not. Checked before the catch-all because a
        # "post-operative review" carries no keyword the catch-all matches
        # either, and leaving it unresolved would drop the claim back to an
        # ambiguous list.
        return GHS_SUB_TYPES[0]  # Follow up Pre-/Post-Hospitalisation
    if any(k in t for k in ("day surgery", "surgery", "operation", "admission",
                            "admitted", "ward", "inpatient", "warded", "hospitalis")):
        return GHS_SUB_TYPES[1]  # Hospitalisation/Day Surgery
    return None


def _routing_scope_keys(
    scope_key: str, sub_type: str | None, sector: str | None
) -> tuple[str, ...]:
    """Concrete document-routing keys for one member-visible claim choice.

    The member form keeps one generic GHS choice per subclaim. Claim Settings
    splits that choice into Government/Private leaves, so document inference
    tests the known sector leaf (or both leaves when the evidence has no sector)
    and finally the legacy generic key for backward compatibility.
    """
    generic = scope_code_for_sub_type(sub_type)
    if generic not in GHS_GENERIC_SCOPE_CODES:
        return (scope_key,)
    sectors = (sector,) if sector is not None else (SECTOR_GOVT, SECTOR_PRIVATE)
    prefix = scope_key.rsplit(":", 1)[0]
    leaves = tuple(
        f"{prefix}:{sector_scope_code(generic, candidate)}"
        for candidate in sectors
    )
    return (*leaves, scope_key)


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

    entries: list[tuple[str, str, str | None, str]] = []
    # (selection value, inferred setting, sub_type, stable scope key)
    for opt in insured:
        for i, ct in enumerate(opt.claim_types):
            entries.append((
                f"insured:{opt.product_code}:{i}",
                _entry_setting(opt, ct.sub_type, ct.label),
                ct.sub_type,
                ct.scope_key,
            ))
    if coverage_opts.flex is not None:
        entries.extend(
            (
                f"flex:{category.name}",
                "flex",
                None,
                claim_scope_key("flex", category.name),
            )
            for category in coverage_opts.flex.categories
        )

    labels = " ".join(_label(f) + " " + _val(f) for f in _fields(document))
    text = " ".join(filter(None, [document.get("document_type") or "", provider or "", labels]))

    # Broker-configured routing is authoritative when the recognised document
    # type maps to one or more scopes the member actually holds. Multiple
    # matches remain candidates; the form never guesses between two covered
    # products that intentionally share the same document definition.
    definition = classify_document(
        document.get("document_type"),
        _fields(document),
        definitions=doc_types,
        sector_hint=hospital_sector(provider),
    )
    if definition is not None and definition.claim_scope_keys:
        inferred_sector = definition.sector or hospital_sector(provider)
        configured = [
            value
            for value, _, sub_type, scope_key in entries
            if any(
                definition_targets_scope(definition, candidate)
                for candidate in _routing_scope_keys(
                    scope_key, sub_type, inferred_sector
                )
            )
        ]
        configured = list(dict.fromkeys(configured))
        if len(configured) == 1:
            return configured[0], []
        if configured:
            return None, configured

    targets, specialist_surgical = _target_settings(document, text, provider, doc_types)
    matched = [
        (v, setting, st) for v, setting, st, _ in entries if setting in targets
    ]

    # Inpatient bills match all four GHS sub-types by setting — narrow to the
    # one the wording points at (A&E → emergency, "day surgery" → hospitalisation…).
    # A surgical specialist bill narrows to pre-/post-hospitalisation, which
    # DROPS the specialist entries that also matched: that is the point of the
    # detection, and a member whose cover has no inpatient product never gets
    # here (nothing matched an inpatient setting) so they still resolve to SP.
    if any(s in ("inpatient_hosp", "inpatient_other") for _, s, _ in matched):
        sub = _inpatient_subtype_from_text(
            text, specialist_surgical=specialist_surgical
        )
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


def _selection_requires_doctor(
    selection: str | None, coverage_opts: CoverageOptionsOut
) -> bool:
    """Whether the resolved claim type asks for the treating doctor — read off
    the SERVED `ClaimTypeOption.requires_doctor_name`, never re-derived from the
    sub-type label here."""
    if not selection or not selection.startswith("insured:"):
        return False
    parts = selection.split(":")
    if len(parts) < 3:
        return False
    opt = next(
        (o for o in coverage_opts.insured if o.product_code == parts[1]), None
    )
    if opt is None:
        return False
    try:
        return opt.claim_types[int(parts[2])].requires_doctor_name
    except (ValueError, IndexError):
        return False


def _diagnosis_group_for(selection: str | None) -> str | None:
    """The diagnosis catalog group for a resolved insured claim selection
    (`insured:<code>:<idx>`), or None for flex / unresolved."""
    if not selection or not selection.startswith("insured:"):
        return None
    parts = selection.split(":")
    if len(parts) < 2:
        return None
    return claim_profile_for(parts[1]).diagnosis_group


def _resolve_diagnosis(raw: str | None, selection: str | None) -> str | None:
    """Map an extracted free-text diagnosis onto the claim form's diagnosis
    value. When it matches a catalog entry for the claim's group the exact
    catalog label is returned (so the form SELECTS it); otherwise it rides
    behind the ``Other: `` prefix as free text.

    Conservative matching: an exact (case-insensitive) label match wins;
    otherwise a catalog label whose tokens are fully contained in the reading
    ("Acute Appendicitis" → "Appendicitis") matches only when the label
    accounts for at least half of the reading's words — so a generic label
    ("Fever") can't hijack a specific reading."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    raw_tokens = tokenize(text)
    if not raw_tokens:
        return _OTHER_PREFIX + text
    group = _diagnosis_group_for(selection)
    # Whole group (search with no query returns every entry in the group).
    catalog = search_diagnoses(group, "", limit=1000)
    for hit in catalog:
        if hit.label.strip().lower() == text.lower():
            return hit.label
    best: str | None = None
    best_cover = 0
    for hit in catalog:
        lab_tokens = tokenize(hit.label)
        if not lab_tokens or not (lab_tokens <= raw_tokens):
            continue
        # The label must cover ≥ half the reading's words to count as a match.
        if len(lab_tokens) * 2 >= len(raw_tokens) and len(lab_tokens) > best_cover:
            best, best_cover = hit.label, len(lab_tokens)
    if best is not None:
        return best
    return _OTHER_PREFIX + text


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
    confidence_floor = confidence_threshold(
        "intake", document_type=str(document.get("document_type") or "")
    )

    provider, provider_conf = _keyworded(fields, _PROVIDER_KEYWORDS)
    amount, amount_conf = _amount(fields)
    incurred_date, date_conf = _date_field(fields, year)
    admission_date, admission_conf, admission_conflict = _stay_date_field(
        fields, ("admission", "admitted")
    )
    discharge_date, discharge_conf, discharge_conflict = _stay_date_field(
        fields, ("discharge", "discharged")
    )
    if admission_conflict and not any(
        any(keyword in _label(field) for keyword in ("visit", "treatment"))
        for field in fields
    ):
        incurred_date, date_conf = None, 0.0
    invoice_number, invoice_conf = _invoice_number(fields)
    diagnosis, diag_conf = _keyworded(fields, ("diagnosis", "condition"))
    doctor, doctor_conf = _doctor_name(fields)
    currency = _currency(fields)

    out_fields = IntakeFields(
        provider_name=provider,
        incurred_date=incurred_date,
        admission_date=admission_date,
        discharge_date=discharge_date,
        invoice_number=invoice_number,
        amount=amount,
        currency=currency,
        diagnosis=diagnosis,
        doctor_name=doctor,
    )

    low: list[str] = []
    for name, value, conf in (
        ("provider_name", provider, provider_conf),
        ("amount", amount, amount_conf),
        ("incurred_date", incurred_date, date_conf),
        ("admission_date", admission_date, admission_conf),
        ("discharge_date", discharge_date, discharge_conf),
        ("invoice_number", invoice_number, invoice_conf),
        ("diagnosis", diagnosis, diag_conf),
    ):
        if value is not None and conf < confidence_floor:
            low.append(name)
    if admission_conflict and "admission_date" not in low:
        low.append("admission_date")
    if discharge_conflict and "discharge_date" not in low:
        low.append("discharge_date")

    claimant = _detect_claimant(fields, employee, coverage_opts.dependants)
    selection, candidates = _infer_claim_type(
        document, coverage_opts, claimant, provider, doc_types
    )

    # Resolve the diagnosis against the catalog for the (now-known) claim group
    # so the form can SELECT a listed condition instead of always falling to
    # free text — the whole reason a diagnosis reads back empty when only a
    # bill (no diagnosis) was uploaded.
    out_fields.diagnosis = _resolve_diagnosis(diagnosis, selection)

    # The doctor is warned about only on the claim types that ASK for it: the
    # form renders that field for pre-/post-hospitalisation alone, and naming a
    # field in the double-check hint that isn't on screen sends the member
    # looking for a control that doesn't exist.
    if (
        doctor is not None
        and doctor_conf < confidence_floor
        and _selection_requires_doctor(selection, coverage_opts)
    ):
        low.append("doctor_name")

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


# ── multi-document intake ─────────────────────────────────────────────────────


def _billing_identity(
    fields: list[dict[str, Any]],
) -> tuple[str | None, float | None]:
    """A document's billing identity — normalized invoice number + amount — used
    to detect distinct invoices. Cheap (no provider/date/diagnosis reads).

    The number is normalized by `claim_intake.normalize_invoice_number`, the
    same key the duplicate-claim check uses, so a set this splits into two
    claims can never be two claims the submit gate then reads as one bill."""
    number, _ = _invoice_number(fields)
    amount, _ = _amount(fields)
    return (normalize_invoice_number(number) or None), amount


def _doc_reading(
    fields: list[dict[str, Any]],
    year: PolicyYear,
    *,
    document_type: str | None = None,
) -> tuple[IntakeFields, list[str]]:
    """One document's OWN field reading (no cross-document merging) plus the
    field names read below the confidence floor — the basis for prefilling a
    later invoice's claim and warning on the fields the AI was unsure about
    (mirrors ``suggest_from_extraction``'s own low-confidence logic)."""
    provider, provider_conf = _keyworded(fields, _PROVIDER_KEYWORDS)
    amount, amount_conf = _amount(fields)
    incurred_date, date_conf = _date_field(fields, year)
    admission_date, admission_conf, admission_conflict = _stay_date_field(
        fields, ("admission", "admitted")
    )
    discharge_date, discharge_conf, discharge_conflict = _stay_date_field(
        fields, ("discharge", "discharged")
    )
    if admission_conflict and not any(
        any(keyword in _label(field) for keyword in ("visit", "treatment"))
        for field in fields
    ):
        incurred_date, date_conf = None, 0.0
    invoice_number, invoice_conf = _invoice_number(fields)
    diagnosis, diag_conf = _keyworded(fields, ("diagnosis", "condition"))
    out = IntakeFields(
        provider_name=provider,
        incurred_date=incurred_date,
        admission_date=admission_date,
        discharge_date=discharge_date,
        invoice_number=invoice_number,
        amount=amount,
        currency=_currency(fields),
        diagnosis=diagnosis,
        # Prefilled, but deliberately never listed in `low` below: this reading
        # belongs to a queued invoice whose claim type isn't resolved here, so
        # the hint could name a field the form won't render.
        doctor_name=_doctor_name(fields)[0],
    )
    confidence_floor = confidence_threshold("intake", document_type=document_type)
    low = [
        name
        for name, value, conf in (
            ("provider_name", provider, provider_conf),
            ("amount", amount, amount_conf),
            ("incurred_date", incurred_date, date_conf),
            ("admission_date", admission_date, admission_conf),
            ("discharge_date", discharge_date, discharge_conf),
            ("invoice_number", invoice_number, invoice_conf),
            ("diagnosis", diagnosis, diag_conf),
        )
        if value is not None and conf < confidence_floor
    ]
    if admission_conflict:
        low.append("admission_date")
    if discharge_conflict:
        low.append("discharge_date")
    return out, low


def _merge_extractions(
    extractions: list[dict[str, Any]],
    doc_types: Sequence[DocTypeDefinition] | None,
) -> dict[str, Any]:
    """Fold up to three extracted documents into ONE virtual document for field
    mapping: the fields are concatenated (so the amount from the invoice and the
    diagnosis from the discharge summary are both visible), and the
    ``document_type`` is taken from the first document that classifies to a
    recognised inpatient type — so the merged reading routes to the inpatient
    setting even when another page is a plain receipt."""
    all_fields = [
        f
        for e in extractions
        for f in (e.get("fields") or [])
        if isinstance(f, dict)
    ]
    primary_type = ""
    for e in extractions:
        efields = [f for f in (e.get("fields") or []) if isinstance(f, dict)]
        if classify_document(
            e.get("document_type"), efields, definitions=doc_types
        ) is not None:
            primary_type = str(e.get("document_type") or "")
            break
    if not primary_type:
        primary_type = next(
            (str(e.get("document_type")) for e in extractions if e.get("document_type")),
            "",
        )
    return {"document_type": primary_type, "fields": all_fields}


def build_intake_suggestion(
    extractions: list[dict[str, Any]],
    coverage_opts: CoverageOptionsOut,
    employee: Employee,
    year: PolicyYear,
    doc_types: Sequence[DocTypeDefinition] | None = None,
) -> ClaimIntakeSuggestionOut:
    """Turn one-to-three extracted documents into claim-form suggestions, plus a
    per-document classification so the form can drop each upload into the
    required-document slot it fills. Each extraction dict is
    ``{file_name, document_type, fields}``.

    Two upload shapes are distinguished:

    - ONE claim's document set (invoice + itemised bill + discharge summary):
      the readings are merged, so the amount from the invoice and the diagnosis
      from the discharge summary both fill the form.
    - SEVERAL distinct invoices (one visit each): merging would pick one
      arbitrary amount/date, so instead each invoice ANCHORS its own claim —
      ``multi_claim=True``, the top-level fields prefill the first invoice's
      claim, and later anchors carry ``claim_index``/``fields`` for the form's
      one-claim-per-invoice flow.
    """
    from app.schemas.claims import IntakeDocument

    # Per doc: (extraction, classification, its fields, billing key, amount,
    # upload index). Only the CHEAP billing identity is read here; the full
    # per-doc reading is deferred to the later anchors that actually use it.
    # ``upload_index`` is the document's position in the ORIGINAL upload (the
    # endpoint may skip unreadable files, so it can't be re-derived from order)
    # — the form joins files to documents on it, robust to duplicate names.
    per_doc: list[
        tuple[dict[str, Any], DocTypeDefinition | None, list[dict[str, Any]],
              str | None, float | None, int]
    ] = []
    for i, e in enumerate(extractions):
        efields = [f for f in (e.get("fields") or []) if isinstance(f, dict)]
        defn = classify_document(
            e.get("document_type"), efields, definitions=doc_types
        )
        key, amount = _billing_identity(efields)
        per_doc.append((e, defn, efields, key, amount, e.get("upload_index", i)))

    # Claim anchors: the FIRST document to carry a given billing identity (an
    # invoice number AND an amount). Anchors with DIFFERENT invoice numbers are
    # different visits — one claim each — while an itemised bill reprinting the
    # invoice's number stays supporting material for THAT invoice's claim. A
    # document with no readable number never splits (conservative: a hard-to-
    # read receipt must not fragment a genuine single-claim set).
    anchor_idx: list[int] = []
    number_to_claim: dict[str, int] = {}
    for i, (_, _, _, key, amount, _) in enumerate(per_doc):
        if key and amount is not None and key not in number_to_claim:
            number_to_claim[key] = len(anchor_idx)
            anchor_idx.append(i)
    multi = len(anchor_idx) >= 2

    # Attribute every document to a claim by its billing number: a doc whose
    # number matches an anchor supports THAT anchor's claim; a doc with no
    # matching number is general material for the first claim. The top-level
    # suggestion merges only the first claim's documents, so a LATER invoice's
    # supporting docs (e.g. its itemised bill) can't bleed their amount/date
    # into the first claim.
    def _claim_of(key: str | None) -> int:
        return number_to_claim.get(key, 0) if key else 0

    pool = [e for (e, _, _, key, _, _) in per_doc if _claim_of(key) == 0]
    merged = _merge_extractions(pool, doc_types)
    suggestion = suggest_from_extraction(
        merged, coverage_opts, employee, year, doc_types
    )
    suggestion.multi_claim = multi

    # Single-claim mode with documents quoting DIFFERENT amounts: the merge
    # picked one of them — surface it via the "double-check" hint.
    if not multi:
        amounts = {amt for (_, _, _, _, amt, _) in per_doc if amt is not None}
        if len(amounts) > 1 and "amount" not in suggestion.low_confidence:
            suggestion.low_confidence.append("amount")

    claim_order = {doc_i: order for order, doc_i in enumerate(anchor_idx)}
    documents: list[IntakeDocument] = []
    for i, (e, defn, efields, _, _, upload_index) in enumerate(per_doc):
        idx = claim_order.get(i) if multi else None
        # Only a LATER anchor prefills its own claim: ship its own reading (with
        # the diagnosis resolved against the inferred claim group) plus the
        # fields the AI was unsure about, so the form can warn on advance. The
        # first claim always prefills from the top-level merged suggestion, so
        # its (and every supporting doc's) per-doc fields would go unused.
        fields: IntakeFields | None = None
        low: list[str] = []
        if idx is not None and idx > 0:
            reading, low = _doc_reading(
                efields, year, document_type=str(e.get("document_type") or "")
            )
            reading.diagnosis = _resolve_diagnosis(
                reading.diagnosis, suggestion.claim_selection
            )
            fields = reading
        documents.append(
            IntakeDocument(
                file_name=str(e.get("file_name") or "document"),
                detected_doc_type=defn.display if defn else None,
                doc_slot=defn.slot_key if defn else None,
                claim_index=idx,
                upload_index=upload_index,
                fields=fields,
                low_confidence=low,
            )
        )
    suggestion.documents = documents
    # Top-level detected type / slot mirror the primary document (the first
    # that fills a slot) for single-document consumers.
    primary = next((d for d in documents if d.doc_slot), documents[0] if documents else None)
    if primary is not None:
        suggestion.detected_doc_type = primary.detected_doc_type
        suggestion.doc_slot = primary.doc_slot
    return suggestion
