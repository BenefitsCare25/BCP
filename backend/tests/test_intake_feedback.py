"""Autofill evidence must retain provenance and enforce baseline ownership."""

from datetime import date
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.models import AuditLog, Claim, Employee, PolicyYear
from app.schemas.claims import ClaimIntakeSuggestionOut, IntakeFields
from app.services.claim_intake_suggest import intake_field_sources
from app.services.intake_feedback import intake_quality, record_intake_feedback, remember_intake


def test_provenance_retains_upload_position_and_does_not_invent_confidence():
    fields = IntakeFields(amount=120, invoice_number="INV-1", currency="SGD")
    docs = [
        {
            "file_name": "same.jpg",
            "upload_index": 2,
            "fields": [
                {
                    "label": "Total Amount",
                    "value": "SGD 120",
                    "field_type": "currency",
                    "confidence": 0.73,
                },
                {"label": "Invoice Number", "value": "INV-1", "field_type": "text"},
            ],
        }
    ]
    year = PolicyYear(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    sources = intake_field_sources(docs, fields, year, None)
    assert sources["amount"][0].confidence == 0.73
    assert sources["amount"][0].upload_index == 2
    assert sources["invoice_number"][0].confidence is None
    assert sources["currency"][0].confidence is None
    docs[0]["fields"][0]["confidence"] = float("nan")
    assert intake_field_sources(docs, fields, year, None)["amount"][0].confidence is None


def baseline():
    return AuditLog(
        id="intake",
        client_id="client",
        employee_id="employee",
        member_account_id="member",
        action="claim.intake_suggested",
        after={
            "policy_year_id": "year",
            "readings": {
                "0": {
                    "fields": {"amount": 120, "provider_name": "Clinic", "currency": "SGD"},
                    "sources": {},
                }
            },
        },
    )


def test_server_compares_claim_values_preserves_metadata_and_counts_only_recorded_samples():
    db = Mock()
    db.get.return_value = baseline()
    claim = Claim(
        client_id="client",
        employee_id="employee",
        policy_year_id="year",
        amount_claimed=120,
        provider_name="Corrected Clinic",
        currency="SGD",
        intake_meta={"existing": "kept"},
    )
    record_intake_feedback(db, claim, "intake", 0, "member")
    assert claim.intake_meta["existing"] == "kept"
    assert claim.intake_meta["autofill"]["suggested_fields"] == 3
    assert claim.intake_meta["autofill"]["corrected_fields"] == ["provider_name"]
    db.scalars.return_value = [None, {}, claim.intake_meta]
    stats = intake_quality(db, "client", "year")
    assert stats["claims"] == 1
    assert stats["correction_rate"] == 1 / 3
    assert stats["corrections_by_field"] == {"provider_name": 1}


@pytest.mark.parametrize(
    "key,value",
    [
        ("client_id", "other"),
        ("employee_id", "other"),
        ("member_account_id", "other"),
        ("action", "claim.drafted"),
    ],
)
def test_foreign_or_wrong_kind_baseline_is_rejected(key, value):
    db = Mock()
    audit = baseline()
    setattr(audit, key, value)
    db.get.return_value = audit
    claim = Claim(client_id="client", employee_id="employee", policy_year_id="year")
    with pytest.raises(HTTPException) as failure:
        record_intake_feedback(db, claim, "intake", 0, "member")
    assert failure.value.status_code == 404


def test_baseline_year_and_invoice_index_are_bound():
    db = Mock()
    db.get.return_value = baseline()
    claim = Claim(client_id="client", employee_id="employee", policy_year_id="other")
    with pytest.raises(HTTPException) as failure:
        record_intake_feedback(db, claim, "intake", 0, "member")
    assert failure.value.status_code == 404
    claim.policy_year_id = "year"
    with pytest.raises(HTTPException) as failure:
        record_intake_feedback(db, claim, "intake", 2, "member")
    assert failure.value.status_code == 422


def test_remembered_baseline_comes_from_server_suggestion():
    db = Mock()
    suggestion = ClaimIntakeSuggestionOut(fields=IntakeFields(amount=80))
    employee = Employee(id="employee", client_id="client", policy_year_id="year")
    remember_intake(db, suggestion, employee, "member")
    saved = db.add.call_args.args[0]
    assert saved.after["readings"]["0"]["fields"]["amount"] == 80
    assert saved.employee_id == employee.id
    assert saved.member_account_id == "member"
