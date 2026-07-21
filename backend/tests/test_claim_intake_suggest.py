"""Unit tests for the document-driven claim-intake autofill mapper.

Pure mapping — no DB. Builds a fake extraction `document` + a `CoverageOptionsOut`
and asserts the suggested claimant / claim type / fields.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models import Employee, PolicyYear
from app.schemas.claims import (
    ClaimTypeOption,
    CoverageOptionsOut,
    FlexClaimCategoryOption,
    FlexClaimOptions,
    InsuredClaimOption,
)
from app.services.claim_intake import GHS_SUB_TYPES
from app.services.claim_intake_suggest import suggest_from_extraction

YEAR = PolicyYear(
    id="py1", client_id="c1", year=2027,
    start_date=date(2027, 1, 1), end_date=date(2027, 12, 31),
)


def _employee(name: str = "John Tan", nric: str = "S1234567A") -> Employee:
    return Employee(
        employee_name=name,
        attribute_values={"id_no": nric},
        derived_attribute_values={},
    )


def _gp_option() -> InsuredClaimOption:
    return InsuredClaimOption(
        product_code="GP",
        product_name="Group Outpatient GP",
        category="outpatient",
        claim_types=[ClaimTypeOption(label="GP (General Practitioner)")],
    )


def _dental_option() -> InsuredClaimOption:
    return InsuredClaimOption(
        product_code="GD",
        product_name="Group Dental",
        category="outpatient",
        claim_types=[ClaimTypeOption(label="Dental")],
    )


def _ghs_option() -> InsuredClaimOption:
    return InsuredClaimOption(
        product_code="GHS",
        product_name="Group Hospital & Surgical",
        category="inpatient",
        claim_types=[ClaimTypeOption(label=s, sub_type=s) for s in GHS_SUB_TYPES],
    )


def _hospital_bill_document():
    """A Singapore government-hospital A&E bill: gross vs net-payable amounts,
    a bill date AND a visit date (with time), EMERGENCY setting."""
    return {
        "document_type": "hospital bill",
        "fields": [
            {"id": "1", "label": "Bill Date", "value": "01 JUL 2026",
             "field_type": "date", "confidence": 0.9},
            {"id": "2", "label": "Visit Date", "value": "27 JUN 2026 03:03 PM",
             "field_type": "date", "confidence": 0.9},
            {"id": "3", "label": "Location", "value": "EMERGENCY",
             "field_type": "text", "confidence": 0.9},
            {"id": "4", "label": "Hospital Name", "value": "National University Hospital",
             "field_type": "text", "confidence": 0.9},
            {"id": "5", "label": "Total Amount (Before Govt Subsidy)", "value": "441.97",
             "field_type": "currency", "confidence": 0.9},
            {"id": "6", "label": "Total Amount (After Govt Subsidy)", "value": "165.83",
             "field_type": "currency", "confidence": 0.9},
            {"id": "7", "label": "Final Amount Payable", "value": "165.83",
             "field_type": "currency", "confidence": 0.9},
            {"id": "8", "label": "Net Payment made", "value": "0.00",
             "field_type": "currency", "confidence": 0.9},
        ],
    }


def _coverage(insured, dependants=None, flex=None) -> CoverageOptionsOut:
    return CoverageOptionsOut(
        policy_year_start="2027-01-01",
        policy_year_end="2027-12-31",
        insured=insured,
        flex=flex,
        dependants=dependants or [],
    )


def _receipt_document(patient="John Tan", provider="Raffles Medical Clinic"):
    return {
        "document_type": "receipt",
        "fields": [
            {"id": "f1", "label": "Patient Name", "value": patient,
             "field_type": "name", "confidence": 0.95},
            {"id": "f2", "label": "Clinic Name", "value": provider,
             "field_type": "name", "confidence": 0.9},
            {"id": "f3", "label": "Total Amount", "value": "SGD 45.00",
             "field_type": "currency", "confidence": 0.92},
            {"id": "f4", "label": "Invoice No", "value": "INV-123",
             "field_type": "text", "confidence": 0.8},
            {"id": "f5", "label": "Invoice Date", "value": "2027-03-15",
             "field_type": "date", "confidence": 0.88},
            {"id": "f6", "label": "Diagnosis", "value": "Acute URTI",
             "field_type": "text", "confidence": 0.72},
        ],
    }


def test_field_mapping_from_receipt():
    out = suggest_from_extraction(
        _receipt_document(), _coverage([_gp_option()]), _employee(), YEAR
    )
    assert out.available is True
    assert out.fields.amount == 45.0
    assert out.fields.currency == "SGD"
    assert out.fields.incurred_date == "2027-03-15"
    assert out.fields.provider_name == "Raffles Medical Clinic"
    assert out.fields.invoice_number == "INV-123"
    # A free-text reading that doesn't match a catalog entry rides behind the
    # "Other: " prefix (the backend now owns this so the form can select a
    # matched catalog label directly).
    assert out.fields.diagnosis == "Other: Acute URTI"
    assert out.low_confidence == []


def test_low_confidence_flagged():
    doc = _receipt_document()
    doc["fields"][2]["confidence"] = 0.3  # amount below the floor
    out = suggest_from_extraction(doc, _coverage([_gp_option()]), _employee(), YEAR)
    assert "amount" in out.low_confidence


def test_claimant_matches_self_by_name():
    out = suggest_from_extraction(
        _receipt_document(patient="John Tan"),
        _coverage([_gp_option()]),
        _employee("John Tan"),
        YEAR,
    )
    assert out.claimant is not None
    assert out.claimant.kind == "self"


def test_claimant_matches_dependant_by_name():
    out = suggest_from_extraction(
        _receipt_document(patient="Mary Tan"),
        _coverage(
            [_gp_option()],
            dependants=[{"id": "dep1", "name": "Mary Tan", "relationship": "Spouse"}],
        ),
        _employee("John Tan"),
        YEAR,
    )
    assert out.claimant is not None
    assert out.claimant.kind == "dependant"
    assert out.claimant.dependant_id == "dep1"


def test_claimant_matches_self_by_nric_without_name():
    doc = {
        "document_type": "receipt",
        "fields": [
            {"id": "f1", "label": "NRIC", "value": "S1234567A",
             "field_type": "text", "confidence": 0.9},
            {"id": "f2", "label": "Clinic", "value": "Raffles Medical",
             "field_type": "name", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(
        doc, _coverage([_gp_option()]), _employee("Someone Else", "S1234567A"), YEAR
    )
    assert out.claimant is not None
    assert out.claimant.kind == "self"
    assert out.claimant.confidence == 1.0


def test_claim_type_unambiguous_selection():
    out = suggest_from_extraction(
        _receipt_document(), _coverage([_gp_option()]), _employee(), YEAR
    )
    assert out.claim_selection == "insured:GP:0"
    assert out.claim_candidates == []


def test_claim_type_ambiguous_leaves_candidates():
    out = suggest_from_extraction(
        _receipt_document(),
        _coverage([_gp_option(), _dental_option()]),
        _employee(),
        YEAR,
    )
    # A plain receipt with GP + Dental both open — can't decide, so no
    # preselection but both offered.
    assert out.claim_selection is None
    assert set(out.claim_candidates) == {"insured:GP:0", "insured:GD:0"}


def test_dental_keyword_picks_dental():
    out = suggest_from_extraction(
        _receipt_document(provider="Smile Dental Surgery"),
        _coverage([_gp_option(), _dental_option()]),
        _employee(),
        YEAR,
    )
    assert out.claim_selection == "insured:GD:0"


def test_amount_prefers_final_payable_over_gross_total():
    out = suggest_from_extraction(
        _hospital_bill_document(), _coverage([_ghs_option()]), _employee(), YEAR
    )
    assert out.fields.amount == 165.83  # not 441.97 (gross before subsidy)


def test_visit_date_with_time_beats_bill_date():
    out = suggest_from_extraction(
        _hospital_bill_document(), _coverage([_ghs_option()]), _employee(), YEAR
    )
    assert out.fields.incurred_date == "2026-06-27"  # visit, not bill (01 JUL)


def test_inpatient_narrows_to_emergency_subtype():
    out = suggest_from_extraction(
        _hospital_bill_document(), _coverage([_ghs_option()]), _employee(), YEAR
    )
    # GHS_SUB_TYPES[2] = "Emergency Accidental Outpatient Treatment"
    assert out.claim_selection == "insured:GHS:2"
    assert out.claim_candidates == []


def test_inpatient_day_surgery_narrows_to_hospitalisation():
    doc = _hospital_bill_document()
    doc["fields"][2]["value"] = "DAY SURGERY"  # Location
    out = suggest_from_extraction(doc, _coverage([_ghs_option()]), _employee(), YEAR)
    assert out.claim_selection == "insured:GHS:1"  # Hospitalisation/Day Surgery


def test_no_patient_name_leaves_claimant_unset():
    doc = {
        "document_type": "receipt",
        "fields": [
            {"id": "f1", "label": "Clinic", "value": "Raffles Medical",
             "field_type": "name", "confidence": 0.9},
            {"id": "f2", "label": "Total", "value": "20.00",
             "field_type": "currency", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(doc, _coverage([_gp_option()]), _employee(), YEAR)
    assert out.claimant is None


def test_non_patient_name_field_does_not_drive_claimant():
    # "Hospital Name" happens to equal the member's name — it must NOT be
    # treated as the patient.
    doc = {
        "document_type": "hospital bill",
        "fields": [
            {"id": "f1", "label": "Hospital Name", "value": "John Tan",
             "field_type": "name", "confidence": 0.95},
        ],
    }
    out = suggest_from_extraction(
        doc, _coverage([_ghs_option()]), _employee("John Tan"), YEAR
    )
    assert out.claimant is None


def test_flex_category_matched_by_containment():
    doc = {
        "document_type": "tax invoice",
        "fields": [
            {"id": "f1", "label": "Description", "value": "Optical lens and frame",
             "field_type": "text", "confidence": 0.9},
        ],
    }
    flex = FlexClaimOptions(categories=[FlexClaimCategoryOption(name="Optical")])
    out = suggest_from_extraction(doc, _coverage([], flex=flex), _employee(), YEAR)
    assert out.claim_selection == "flex:Optical"


@pytest.mark.parametrize("value,expected", [
    ("Jul 1, 2027", "2027-07-01"),      # month-first
    ("01.07.2027", "2027-07-01"),       # dot-separated
    ("1 Jul 2027", "2027-07-01"),       # day-first text
    ("2027-07-01", "2027-07-01"),       # ISO
])
def test_date_formats_parse(value, expected):
    doc = {
        "document_type": "receipt",
        "fields": [
            {"id": "f1", "label": "Visit Date", "value": value,
             "field_type": "date", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(doc, _coverage([_gp_option()]), _employee(), YEAR)
    assert out.fields.incurred_date == expected


# ── Broker document-type registry (screenshots 2026-07-21) ────────────────────


def _govt_finalised_invoice():
    """SGH-style Tax Invoice (Finalised): HRN, admission/discharge dates,
    government schemes section, Final Bill amount."""
    return {
        "document_type": "tax invoice",
        "fields": [
            {"id": "1", "label": "Patient Name", "value": "John Tan",
             "field_type": "name", "confidence": 0.95},
            {"id": "2", "label": "HRN", "value": "2027123456",
             "field_type": "number", "confidence": 0.9},
            {"id": "3", "label": "Admission Date", "value": "10 MAR 2027",
             "field_type": "date", "confidence": 0.9},
            {"id": "4", "label": "Discharge Date", "value": "14 MAR 2027",
             "field_type": "date", "confidence": 0.9},
            {"id": "5", "label": "Invoice Date", "value": "20 MAR 2027",
             "field_type": "date", "confidence": 0.9},
            {"id": "6", "label": "MediShield Life Scheme", "value": "-1,200.00",
             "field_type": "currency", "confidence": 0.85},
            {"id": "7", "label": "Final Bill", "value": "864.20",
             "field_type": "currency", "confidence": 0.92},
            {"id": "8", "label": "Hospital", "value": "Singapore General Hospital",
             "field_type": "text", "confidence": 0.95},
        ],
    }


def test_govt_finalised_invoice_full_mapping():
    out = suggest_from_extraction(
        _govt_finalised_invoice(), _coverage([_ghs_option()]), _employee(), YEAR
    )
    assert out.detected_doc_type == "Tax Invoice (Finalised)"
    assert out.doc_slot == "finalised_tax_invoice"
    # "Final Bill" is the claimable amount (MediShield line is an ID-free
    # currency field but negative-value; Final Bill outranks it at tier 3).
    assert out.fields.amount == 864.20
    # HRN is the bill's identifier when no Invoice No exists... here Invoice
    # Date exists but no Invoice Number field, so HRN fills in.
    assert out.fields.invoice_number == "2027123456"
    # Admission date beats the invoice-issue date as the incurred date.
    assert out.fields.incurred_date == "2027-03-10"
    # Hospitalisation/Day Surgery sub-type (admission wording).
    assert out.claim_selection == "insured:GHS:1"


def test_private_final_tax_invoice_detected_by_case_number():
    doc = {
        "document_type": "tax invoice",
        "fields": [
            {"id": "1", "label": "Case No", "value": "IP-000888",
             "field_type": "text", "confidence": 0.9},
            {"id": "2", "label": "Admission Date", "value": "2027-04-02",
             "field_type": "date", "confidence": 0.9},
            {"id": "3", "label": "Final Bill Amount", "value": "12,340.00",
             "field_type": "currency", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(doc, _coverage([_ghs_option()]), _employee(), YEAR)
    assert out.detected_doc_type == "Final Tax Invoice"
    # Private final invoice maps to no single slot (summary/itemised pair).
    assert out.doc_slot is None
    assert out.fields.amount == 12340.0
    assert out.fields.invoice_number == "IP-000888"
    assert out.claim_selection == "insured:GHS:1"


def test_discharge_summary_alias_routes_inpatient():
    doc = {
        "document_type": "After Visit Summary",
        "fields": [
            {"id": "1", "label": "Diagnosis", "value": "Appendicitis",
             "field_type": "text", "confidence": 0.9},
            {"id": "2", "label": "Surgery", "value": "Laparoscopic appendectomy",
             "field_type": "text", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(
        doc, _coverage([_gp_option(), _ghs_option()]), _employee(), YEAR
    )
    assert out.detected_doc_type == "Discharge Summary"
    assert out.doc_slot == "discharge_summary"
    # Inpatient, narrowed to Hospitalisation/Day Surgery by the surgery wording
    # — never the GP option a bare free-text type would fall back to.
    assert out.claim_selection == "insured:GHS:1"


def test_day_surgery_centre_routes_inpatient_not_specialist():
    doc = {
        "document_type": "tax invoice",
        "fields": [
            {"id": "1", "label": "Clinic Name", "value": "Novena Surgery Centre",
             "field_type": "text", "confidence": 0.9},
            {"id": "2", "label": "Total Amount Payable", "value": "3,200.00",
             "field_type": "currency", "confidence": 0.9},
        ],
    }
    sp = InsuredClaimOption(
        product_code="SP",
        product_name="Group Specialist",
        category="outpatient",
        requires_referral=True,
        claim_types=[ClaimTypeOption(label="SP (Specialist)")],
    )
    out = suggest_from_extraction(
        doc, _coverage([sp, _ghs_option()]), _employee(), YEAR
    )
    # "Surgery" in the centre's name must not misroute to the SP claim — the
    # hospital registry knows Novena Surgery Centre is a (private) inpatient
    # setting, and the wording narrows to Hospitalisation/Day Surgery.
    assert out.claim_selection == "insured:GHS:1"


def test_identifier_number_fields_never_read_as_amount():
    doc = {
        "document_type": "receipt",
        "fields": [
            {"id": "1", "label": "Invoice Number", "value": "20270456",
             "field_type": "number", "confidence": 0.95},
            {"id": "2", "label": "Total Amount", "value": "58.00",
             "field_type": "currency", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(doc, _coverage([_gp_option()]), _employee(), YEAR)
    assert out.fields.amount == 58.0  # never the 8-digit invoice number


def test_amount_field_with_case_token_still_read():
    # A currency field labeled "Total Case Amount" names a real amount (tier 1)
    # and must NOT be excluded just because the label contains "case".
    doc = {
        "document_type": "tax invoice",
        "fields": [
            {"id": "1", "label": "Case No", "value": "IP-77",
             "field_type": "text", "confidence": 0.9},
            {"id": "2", "label": "Total Case Amount", "value": "4,210.00",
             "field_type": "currency", "confidence": 0.9},
            {"id": "3", "label": "Admission Date", "value": "2027-05-01",
             "field_type": "date", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(doc, _coverage([_ghs_option()]), _employee(), YEAR)
    assert out.fields.amount == 4210.0


def test_diagnosis_matches_catalog_entry_and_selects_it():
    # "Appendicitis" is an exact hospital-catalog label → the form selects it
    # (no "Other:" prefix). Requires the GHS claim type to resolve so the group
    # is "hospital".
    doc = {
        "document_type": "discharge summary",
        "fields": [
            {"id": "1", "label": "Diagnosis", "value": "Acute Appendicitis",
             "field_type": "text", "confidence": 0.9},
            {"id": "2", "label": "Surgery", "value": "Appendectomy",
             "field_type": "text", "confidence": 0.9},
        ],
    }
    out = suggest_from_extraction(doc, _coverage([_ghs_option()]), _employee(), YEAR)
    assert out.claim_selection == "insured:GHS:1"
    assert out.fields.diagnosis == "Appendicitis"  # catalog label, not "Other:"


def test_diagnosis_without_catalog_match_falls_to_other():
    doc = _receipt_document()
    doc["fields"][5]["value"] = "Some unlisted rare condition xyz"
    out = suggest_from_extraction(doc, _coverage([_gp_option()]), _employee(), YEAR)
    assert out.fields.diagnosis == "Other: Some unlisted rare condition xyz"


def test_build_intake_suggestion_merges_invoice_and_discharge_summary():
    from app.services.claim_intake_suggest import build_intake_suggestion

    # A real 2-document set: the invoice carries the amount/date, the discharge
    # summary carries the diagnosis. The merged suggestion has BOTH, and each
    # document is classified to its slot.
    invoice = {
        "file_name": "invoice.pdf",
        "document_type": "tax invoice",
        "fields": [
            {"id": "1", "label": "Admission Date", "value": "2027-05-10",
             "field_type": "date", "confidence": 0.9},
            {"id": "2", "label": "Final Bill", "value": "8,400.00",
             "field_type": "currency", "confidence": 0.9},
            {"id": "3", "label": "MediShield Life Scheme", "value": "-2,000.00",
             "field_type": "currency", "confidence": 0.85},
            {"id": "4", "label": "HRN", "value": "202755", "field_type": "number",
             "confidence": 0.9},
        ],
    }
    discharge = {
        "file_name": "discharge.pdf",
        "document_type": "After Visit Summary",
        "fields": [
            {"id": "1", "label": "Diagnosis", "value": "Appendicitis",
             "field_type": "text", "confidence": 0.9},
            {"id": "2", "label": "Surgery", "value": "Laparoscopic appendectomy",
             "field_type": "text", "confidence": 0.9},
        ],
    }
    out = build_intake_suggestion(
        [invoice, discharge], _coverage([_ghs_option()]), _employee(), YEAR
    )
    assert out.available is True
    assert out.fields.amount == 8400.0            # from the invoice
    assert out.fields.incurred_date == "2027-05-10"
    assert out.fields.diagnosis == "Appendicitis"  # from the discharge summary
    assert out.claim_selection == "insured:GHS:1"
    # Per-document classification for slot placement.
    by_name = {d.file_name: d for d in out.documents}
    assert by_name["invoice.pdf"].detected_doc_type == "Tax Invoice (Finalised)"
    assert by_name["invoice.pdf"].doc_slot == "finalised_tax_invoice"
    assert by_name["discharge.pdf"].detected_doc_type == "Discharge Summary"
    assert by_name["discharge.pdf"].doc_slot == "discharge_summary"


def test_custom_sector_neutral_type_does_not_shadow_invoice():
    from app.services.claim_doc_types import (
        DEFAULT_DOC_TYPES,
        DocTypeDefinition,
        KeyField,
        classify_document,
    )

    # A broker's custom sector-neutral type sharing the "tax invoice" alias must
    # not shadow the govt/private classification when the doc is an inpatient
    # bill (has admission/schemes markers).
    custom = DocTypeDefinition(
        key="custom_note", display="Custom Note",
        aliases=("tax invoice", "note"), key_fields=(KeyField("X", ("x",)),),
        sector=None,
    )
    defs = (*DEFAULT_DOC_TYPES, custom)
    fields = [
        {"label": "Admission Date", "value": "x"},
        {"label": "MediShield Life Scheme", "value": "y"},
    ]
    defn = classify_document("tax invoice", fields, definitions=defs)
    assert defn is not None and defn.key == "finalised_tax_invoice"
