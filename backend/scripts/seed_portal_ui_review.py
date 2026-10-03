"""Add labelled local-only UI fixtures to one CDL employee, without replacing data.

Run from backend: .venv/Scripts/python scripts/seed_portal_ui_review.py --employee-id ID
Use --cleanup with the same employee id to remove only this script's deterministic
records and local test HR account. No service notification or AI pipeline is called.
"""

# Direct execution needs the backend path before application imports.
# ruff: noqa: E402
from __future__ import annotations

import argparse
import io
import json
import secrets
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.core.storage import LocalStorage, document_path
from app.db.session import SessionLocal, engine
from app.models import (
    Claim,
    Client,
    Dependant,
    Employee,
    MemberAccount,
    StoredDocument,
    User,
    UserClientAccess,
)
from app.models.auth import AuthCredential
from app.models.claim_message import ClaimMessage
from app.models.enrollment_form import EnrollmentFormSubmission
from app.models.member_enquiry import MemberEnquiry
from scripts.seed_claims_demo import make_receipt_pdf

MARKER = "UI REVIEW SAMPLE"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--employee-id", required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    expected_db = ROOT / "inspro.db"
    if (
        engine.url.get_backend_name() != "sqlite"
        or Path(engine.url.database or "").resolve() != expected_db.resolve()
    ):
        raise SystemExit("This fixture script only supports backend/inspro.db.")
    namespace = f"inspro-local-ui-review:{args.employee_id}:"

    def uid(key: str) -> str:
        return str(uuid5(NAMESPACE_URL, namespace + key))

    review_dir = ROOT.parent / "tmp" / "portal-ui-review"
    review_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalStorage()
    with SessionLocal() as db:
        employee = db.get(Employee, args.employee_id)
        if employee is None:
            raise SystemExit("Employee not found.")
        client = db.get(Client, employee.client_id)
        if client is None or client.slug != "cdl":
            raise SystemExit("Only the authorised local CDL employee is supported.")
        hr_id = uid("hr-user")
        states = [
            "draft",
            "submitted",
            "needs_info",
            "approved",
            "sent_to_insurer",
            "paid",
            "rejected",
            "ai_flagged",
        ]
        claims = [(f"claim-{state}", state, "portal") for state in states] + [
            ("hr-claim-draft", "draft", "hr"),
            ("hr-claim-needs_info", "needs_info", "hr"),
            ("claim-usage", "approved", "portal"),
            ("hr-claim-paid", "paid", "hr"),
        ]
        if args.cleanup:
            for key, _, _ in claims:
                for row in db.scalars(
                    select(ClaimMessage).where(ClaimMessage.claim_id == uid(key))
                ).all():
                    db.delete(row)
            for key in ["question-open", "question-closed"]:
                for row in db.scalars(
                    select(ClaimMessage).where(ClaimMessage.enquiry_id == uid(key))
                ).all():
                    db.delete(row)
            db.flush()
            for cls, keys in [
                (StoredDocument, ["receipt-" + key for key, _, _ in claims] + ["form-pdf"]),
                (EnrollmentFormSubmission, ["paper-form"]),
                (MemberEnquiry, ["question-open", "question-closed"]),
                (Claim, [key for key, _, _ in claims]),
                (Dependant, ["family-pending", "family-rejected"]),
                (AuthCredential, ["hr-credential"]),
                (UserClientAccess, ["hr-grant"]),
                (User, ["hr-user"]),
            ]:
                for key in keys:
                    row = db.get(cls, uid(key))
                    if row is not None:
                        if isinstance(row, StoredDocument):
                            storage.delete(row.storage_path)
                        db.delete(row)
                db.flush()
            db.commit()
            print("Removed only local UI-review fixtures; original employee records preserved.")
            return
        now = datetime.now(UTC)
        credentials_path = review_dir / "hr-credentials.json"
        if db.get(User, hr_id) is None:
            from app.core.passwords import hash_password

            password = secrets.token_urlsafe(24)
            db.add(
                User(
                    id=hr_id,
                    email="ui-review-hr@cdl.inspro.test",
                    display_name=MARKER + " HR",
                    broker_firm_id=client.broker_firm_id,
                    role="client_admin",
                    status="active",
                )
            )
            db.flush()
            db.add(UserClientAccess(id=uid("hr-grant"), user_id=hr_id, client_id=client.id))
            db.add(
                AuthCredential(
                    id=uid("hr-credential"),
                    user_id=hr_id,
                    broker_firm_id=client.broker_firm_id,
                    hr_login_id="HR-UIREVIEW",
                    password_hash=hash_password(password),
                    password_updated_at=now,
                    failed_attempts=0,
                )
            )
            credentials_path.write_text(
                json.dumps({"identifier": "HR-UIREVIEW", "password": password}), encoding="utf-8"
            )
        account = db.scalar(
            select(MemberAccount).where(
                MemberAccount.client_id == client.id, MemberAccount.staff_id == employee.staff_id
            )
        )
        member_id = account.id if account else None

        def attach(key: str, entity: str, owner: str, lines: list[str]) -> str:
            doc_id = uid(key)
            if db.get(StoredDocument, doc_id) is None:
                path = document_path(
                    client.broker_firm_id, client.id, entity, owner, doc_id, ".pdf"
                )
                blob = storage.save(
                    io.BytesIO(
                        make_receipt_pdf(
                            [MARKER, "Local layout fixture - not a real submission", *lines]
                        )
                    ),
                    path,
                )
                db.add(
                    StoredDocument(
                        id=doc_id,
                        client_id=client.id,
                        entity_type=entity,
                        entity_id=owner,
                        file_name=f"ui-review-{key}.pdf",
                        mime_type="application/pdf",
                        size_bytes=blob.size_bytes,
                        sha256=blob.sha256,
                        storage_path=path,
                        doc_type="itemised_tax_invoice" if entity == "claim" else None,
                    )
                )
            return doc_id

        for index, (key, state, origin) in enumerate(claims):
            claim_id = uid(key)
            if db.get(Claim, claim_id) is not None:
                continue
            incurred = date(2026, 6, 1) + timedelta(days=index * 7)
            amount = 45 + index * 10
            settled = state in {"approved", "sent_to_insurer", "paid"}
            db.add(
                Claim(
                    id=claim_id,
                    client_id=client.id,
                    policy_year_id=employee.policy_year_id,
                    employee_id=employee.id,
                    claim_kind="insured",
                    case_type="claim",
                    origin=origin,
                    created_by_user_id=hr_id if origin == "hr" else None,
                    intake_meta={"submission_channel": origin, "ui_review_fixture": True},
                    product_code="GCGP",
                    claim_type="Group Clinical GP",
                    incurred_date=incurred,
                    provider_name=f"{MARKER} clinic {index + 1}",
                    invoice_number=f"UI-REVIEW-{index + 1}",
                    diagnosis="Sample GP consultation for layout review",
                    remarks=MARKER + " - local test record",
                    amount_claimed=amount,
                    currency="SGD",
                    amount_approved=amount - 5 if settled else None,
                    status=state,
                    submitted_by_member_id=member_id if origin == "portal" else None,
                    submitted_at=now - timedelta(days=index + 2) if state != "draft" else None,
                    decided_at=now - timedelta(days=1) if settled or state == "rejected" else None,
                    decision_notes="Sample only: please add the itemised receipt."
                    if state == "needs_info"
                    else "UI review sample decision"
                    if settled or state == "rejected"
                    else None,
                    reference_no=f"UI-REVIEW-{index + 1:03d}",
                    sent_to_insurer_at=now - timedelta(days=1)
                    if state in {"sent_to_insurer", "paid"}
                    else None,
                    paid_on=date(2026, 9, 28) if state == "paid" else None,
                    payment_amount=amount - 5 if state == "paid" else None,
                    form_fields={
                        "claim_type": "Group Clinical GP",
                        "incurred_date": incurred.isoformat(),
                        "provider_name": f"{MARKER} clinic {index + 1}",
                        "amount_claimed": amount,
                        "currency": "SGD",
                    },
                    created_at=now - timedelta(days=index + 2),
                )
            )
            db.flush()
            if key == "claim-usage":
                usage_claim = db.get(Claim, claim_id)
                usage_claim.product_code = "GHS"
                usage_claim.benefit_key = "Outpatient Kidney Dialysis / Cancer Treatment"
                usage_claim.claim_type = "Group Hospital & Surgical"
            attach(
                "receipt-" + key,
                "claim",
                claim_id,
                [f"Sample reference UI-REVIEW-{index + 1}", f"Consultation amount SGD {amount}"],
            )
            db.add(
                ClaimMessage(
                    id=uid("message-" + key),
                    client_id=client.id,
                    claim_id=claim_id,
                    author_type="broker",
                    author_name="Inspro",
                    subject=MARKER,
                    body=(
                        f"{MARKER}: this {state.replace('_', ' ')} claim "
                        "is sample data for the local portal review."
                    ),
                    created_at=now - timedelta(days=index + 1),
                )
            )
        for key, state in [("family-pending", "pending_approval"), ("family-rejected", "rejected")]:
            if db.get(Dependant, uid(key)) is None:
                db.add(
                    Dependant(
                        id=uid(key),
                        client_id=client.id,
                        policy_year_id=employee.policy_year_id,
                        employee_id=employee.id,
                        status=state,
                        link_method="staff_id",
                        attribute_values={
                            "name": MARKER
                            + (" child" if state == "pending_approval" else " family member"),
                            "relationship": "child",
                            "date_of_birth": "2016-05-18",
                            "staff_id": employee.staff_id,
                        },
                    )
                )
        for index, (key, state) in enumerate(
            [("question-open", "open"), ("question-closed", "closed")]
        ):
            if db.get(MemberEnquiry, uid(key)) is None:
                db.add(
                    MemberEnquiry(
                        id=uid(key),
                        client_id=client.id,
                        policy_year_id=employee.policy_year_id,
                        employee_id=employee.id,
                        topic="coverage" if index == 0 else "clinics",
                        subject=(
                            f"{MARKER}: "
                            + ("benefit question" if index == 0 else "clinic information")
                        ),
                        status=state,
                        closed_at=now if state == "closed" else None,
                    )
                )
                db.flush()
                for number, author in enumerate(["member", "broker"]):
                    db.add(
                        ClaimMessage(
                            id=uid(f"{key}-message-{number}"),
                            client_id=client.id,
                            enquiry_id=uid(key),
                            author_type=author,
                            author_member_id=member_id if author == "member" else None,
                            author_name="Inspro" if author == "broker" else None,
                            subject=MARKER,
                            body=f"{MARKER}: "
                            + (
                                "Can I see the benefit details?"
                                if author == "member"
                                else (
                                    "Use Coverage to review your published plan details. "
                                    "This is a local sample reply."
                                )
                            ),
                            created_at=now - timedelta(hours=4 - index - number),
                        )
                    )
        if db.get(EnrollmentFormSubmission, uid("paper-form")) is None:
            doc_id = attach(
                "form-pdf",
                "enrol_form",
                uid("paper-form"),
                [
                    "SAMPLE PAPER FORM",
                    "No employee signature or election is represented by this fixture.",
                ],
            )
            db.add(
                EnrollmentFormSubmission(
                    id=uid("paper-form"),
                    client_id=client.id,
                    policy_year_id=employee.policy_year_id,
                    employee_id=employee.id,
                    reference_no="UI-REVIEW-FORM-001",
                    version=1,
                    source="paper",
                    status="submitted",
                    snapshot={},
                    document_id=doc_id,
                    submitted_by_user_id=hr_id,
                    broker_note=MARKER + " - not a signed employee form",
                    created_at=now,
                )
            )
        db.commit()
        print(
            json.dumps(
                {
                    "claims": len(claims),
                    "family_records": 2,
                    "questions": 2,
                    "sample_paper_forms": 1,
                    "hr_login_id": "HR-UIREVIEW",
                    "credentials_file": str(credentials_path),
                    "external_notifications": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
