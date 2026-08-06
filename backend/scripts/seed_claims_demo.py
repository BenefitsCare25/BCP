"""Seed a complete portal demo dataset (Phases 1-4) onto the Demo client.

Creates an ACTIVE policy year with resolved GHS coverage + a confirmed Flex
scheme, two portal members, one pending dependant, and claims in every state
the walkthrough in docs/EMPLOYEE_PORTAL.md needs:

- approved claims that consume the annual / benefit-item / flex limits
  (so /portal/utilization and the broker utilization panel show real bars),
- an ai_flagged claim with a canned COMPLETE AI review (the review panel
  renders without any AI key configured),
- a manual-review claim whose review row records a pipeline error,
- a needs_info claim (member resubmit flow),
- two decidable claims sized to trip the 409 `limit_exceeded` approve guard
  (one insured, one flex) → the "Approve anyway" dialog.

Receipts are tiny generated (valid) PDFs stored through the retained-storage
backend, each with unique bytes so the duplicate-SHA-256 rule passes.

Idempotent: re-running deletes the demo policy year (cascades everything
tenant-scoped), its stored receipt files, and the demo member accounts, then
recreates them.

Run:  cd backend && PYTHONPATH=. uv run python scripts/seed_claims_demo.py

Then: broker UI → switch to the Demo client + the "Demo 2026 (claims demo)"
policy year; portal → sign in as demo.member@inspro.test (dev+mock auto-fills
the OTP).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select

from app.core.auth import DEMO_BROKER_FIRM_ID, DEMO_CLIENT_ID
from app.core.storage import document_path, get_storage
from app.db.session import SessionLocal
from app.db.tenancy import set_search_path
from app.models import (
    Category,
    Claim,
    ClaimAIReview,
    Dependant,
    Employee,
    FlexScheme,
    MemberAccount,
    Plan,
    PolicyYear,
    Product,
    StoredDocument,
)
from app.models.policy_year import PolicyYearStatus

# Fixed ids (hex-only) so the script is idempotent and rows are recognizable.
_P = "00000000-0000-0000-0000-00000000d3"
PY_ID = _P + "01"
CAT_ID = _P + "02"
PLAN_ID = _P + "03"
EMP_A = _P + "04"  # Demo Member — carries all the claims
EMP_B = _P + "05"  # Demo Colleague — for member-isolation checks (empty usage)
ACC_A = _P + "06"
ACC_B = _P + "07"
DEP_PENDING = _P + "08"
FLEX_ID = _P + "09"

MEMBER_EMAIL = "demo.member@inspro.test"
COLLEAGUE_EMAIL = "demo.colleague@inspro.test"

NOW = datetime.now(UTC)


# ── Tiny valid PDF receipts ───────────────────────────────────────────────────


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_receipt_pdf(lines: list[str]) -> bytes:
    """Hand-rolled single-page PDF with real text — valid enough for viewers
    AND for the AI vision pipeline, so 'Re-run AI review' works against these
    receipts when a provider key is configured."""
    text_ops = " ".join(f"({_pdf_escape(ln)}) Tj T*" for ln in lines)
    stream = f"BT /F1 12 Tf 50 780 Td 16 TL {text_ops} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


# ── Cleanup (idempotency) ─────────────────────────────────────────────────────


def _cleanup(db) -> None:
    if db.get(PolicyYear, PY_ID) is not None:
        claim_ids = list(
            db.execute(select(Claim.id).where(Claim.policy_year_id == PY_ID)).scalars()
        )
        if claim_ids:
            storage = get_storage()
            docs = db.execute(
                select(StoredDocument).where(StoredDocument.entity_id.in_(claim_ids))
            ).scalars().all()
            for doc in docs:
                try:
                    storage.delete(doc.storage_path)
                except Exception:
                    pass
                db.delete(doc)
            db.flush()
        # Bulk DELETE (not ORM delete): the DB-level ON DELETE CASCADE clears
        # categories, plans, employees, claims, reviews, dependants, and the
        # flex scheme with the year — the ORM would instead try to NULL the
        # children's FKs.
        db.execute(delete(PolicyYear).where(PolicyYear.id == PY_ID))
    db.execute(delete(MemberAccount).where(MemberAccount.id.in_((ACC_A, ACC_B))))
    db.flush()


# ── Claim factory ─────────────────────────────────────────────────────────────


def _add_claim(
    db,
    *,
    suffix: str,
    kind: str = "insured",
    benefit_key: str | None = None,
    flex_category: str | None = None,
    claim_type: str,
    incurred: date,
    amount: float,
    status: str,
    approved: float | None = None,
    provider: str = "Demo Family Clinic",
    decision_notes: str | None = None,
    receipt_lines: list[str] | None = None,
) -> Claim:
    claim = Claim(
        id=_P + suffix,
        client_id=DEMO_CLIENT_ID,
        policy_year_id=PY_ID,
        employee_id=EMP_A,
        claim_kind=kind,
        product_code="GHS" if kind == "insured" else None,
        benefit_key=benefit_key,
        flex_category_name=flex_category,
        claim_type=claim_type,
        incurred_date=incurred,
        provider_name=provider,
        amount_claimed=amount,
        amount_approved=approved,
        currency="SGD",
        status=status,
        submitted_by_member_id=ACC_A,
        submitted_at=NOW - timedelta(days=3),
        decided_at=NOW - timedelta(days=1) if status in ("approved", "rejected") else None,
        decision_notes=decision_notes,
        form_fields={
            "claim_type": claim_type,
            "incurred_date": incurred.isoformat(),
            "provider_name": provider,
            "diagnosis": None,
            "amount_claimed": amount,
            "currency": "SGD",
        },
    )
    db.add(claim)
    db.flush()

    lines = receipt_lines or [
        "DEMO FAMILY CLINIC",
        "12 Demo Street, Singapore 049999",
        "TAX INVOICE / RECEIPT",
        f"Receipt no: DEMO-{suffix}",
        f"Visit date: {incurred.isoformat()}",
        "Patient: Demo Member",
        f"Service: {claim_type}",
        f"Total amount: SGD {amount:.2f}",
        "Payment: PAID - NETS",
    ]
    content = make_receipt_pdf(lines)
    doc_id = _P + "9" + suffix[1:]  # unique per claim
    path = document_path(DEMO_BROKER_FIRM_ID, DEMO_CLIENT_ID, "claim", claim.id, doc_id, ".pdf")
    import io

    blob = get_storage().save(io.BytesIO(content), path)
    db.add(
        StoredDocument(
            id=doc_id,
            client_id=DEMO_CLIENT_ID,
            entity_type="claim",
            entity_id=claim.id,
            file_name=f"receipt-{suffix}.pdf",
            mime_type="application/pdf",
            size_bytes=blob.size_bytes,
            sha256=blob.sha256,
            storage_path=blob.path,
            uploaded_by_member_id=ACC_A,
        )
    )
    return claim


def seed_claims_demo() -> None:
    db = SessionLocal()
    try:
        set_search_path(db, DEMO_BROKER_FIRM_ID)
        _cleanup(db)

        # ── Policy year (ACTIVE; period covers today so new portal claims
        # submit in-period). year=2027 label only — 2026 is reserved for
        # seed_demo's one_or_none lookup, and start_date 2026-01-02 dodges the
        # (client_id, start_date) unique constraint with seed_demo's draft year.
        db.add(
            PolicyYear(
                id=PY_ID,
                client_id=DEMO_CLIENT_ID,
                year=2027,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 12, 31),
                status=PolicyYearStatus.active,
            )
        )
        db.flush()

        # ── Coverage: GHS product (global catalog) → Plan P1 → matched category.
        ghs = db.execute(
            select(Product).where(Product.code == "GHS", Product.client_id.is_(None))
        ).scalars().first()
        if ghs is None:
            ghs = Product(
                client_id=None, code="GHS",
                display_name="Group Hospital & Surgical",
                has_dependants=True, is_outpatient=False,
            )
            db.add(ghs)
            db.flush()

        db.add(
            Plan(
                id=PLAN_ID,
                product_id=ghs.id,
                policy_year_id=PY_ID,
                code="P1",
                display_name="GHS Plan P1 (demo)",
                cover_description="Demo hospital & surgical cover",
                annual_policy_limit="S$2,000",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "Outpatient GP", "value": "S$800 per year"},
                        {"number": "2", "name": "Dental Emergency", "value": "S$500 per year"},
                        {"number": "3", "name": "Specialist Consultation", "value": "As charged"},
                        {"number": "4", "name": "Room & Board", "value": "S$650/day"},
                    ]
                },
                source="manual",
                status="confirmed",
            )
        )
        db.add(
            Category(
                id=CAT_ID,
                policy_year_id=PY_ID,
                product_id=ghs.id,
                display_name="All Employees — GHS Plan P1 (demo)",
                raw_description="All employees (claims demo)",
                matching_rule={"and": []},
                rule_human_readable="All employees",
                plan_assignments={"plan_code": "P1"},
                source="manual",
                status="confirmed",
            )
        )

        # ── Flex scheme (confirmed) + members with assigned wallets.
        db.add(
            FlexScheme(
                id=FLEX_ID,
                policy_year_id=PY_ID,
                status="confirmed",
                origin="manual",
                confirmed_at=NOW,
                scheme={
                    "meta": {"scheme_name": "Demo Flex Benefits", "currency": "SGD"},
                    "tiers": [
                        {
                            "name": "Tier 1",
                            "employee_type": {"raw": "All confirmed employees"},
                            "limits": [
                                {"family_status": "S", "amount": 1000},
                                {"family_status": "M", "amount": 1200},
                                {"family_status": "M1C", "amount": 1400},
                            ],
                            "cost_sharing": {"employer_pct": 80, "employee_pct": 20},
                            "benefit_categories": [
                                {"name": "Dental", "claimable": True, "sub_limit": 500},
                                {"name": "Optical", "claimable": True, "sub_limit": 300},
                                {"name": "Health Screening", "claimable": True},
                                {
                                    "name": "Gym Membership",
                                    "claimable": False,
                                    "note": "Not claimable under the demo scheme",
                                },
                            ],
                        }
                    ],
                },
            )
        )
        db.flush()

        matched = [
            {"category_id": CAT_ID, "product_code": "GHS", "method": "manual", "confidence": 1.0}
        ]
        # flex_assigned_at slightly in the future of the scheme's updated_at so
        # the statement never shows the "assignment stale" hint.
        assigned_at = NOW + timedelta(minutes=5)
        for emp_id, acc_id, staff, name, email in (
            (EMP_A, ACC_A, "DEMO-001", "Demo Member", MEMBER_EMAIL),
            (EMP_B, ACC_B, "DEMO-002", "Demo Colleague", COLLEAGUE_EMAIL),
        ):
            db.add(
                MemberAccount(
                    id=acc_id,
                    client_id=DEMO_CLIENT_ID,
                    email=email,
                    staff_id=staff,
                    display_name=name,
                    status="active",
                )
            )
            db.add(
                Employee(
                    id=emp_id,
                    client_id=DEMO_CLIENT_ID,
                    policy_year_id=PY_ID,
                    staff_id=staff,
                    employee_name=name,
                    member_account_id=acc_id,
                    attribute_values={"name": name, "email": email},
                    derived_attribute_values={},
                    matched_categories=matched,
                    flex_family_status="S",
                    flex_tier_name="Tier 1",
                    flex_wallet_amount=1000.0,
                    flex_currency="SGD",
                    flex_source="roster",
                    flex_assigned_at=assigned_at,
                    source="manual",
                    status="active",
                )
            )
        db.flush()

        # ── Pending dependant (Phase 2 approval-card demo).
        db.add(
            Dependant(
                id=DEP_PENDING,
                client_id=DEMO_CLIENT_ID,
                policy_year_id=PY_ID,
                employee_id=EMP_A,
                attribute_values={
                    "name": "Demo Junior",
                    "relationship": "child",
                    "dob": "2018-04-12",
                    "employee_staff_id": "DEMO-001",
                },
                link_method="member_portal",
                status="pending_approval",
            )
        )

        # ── Claims (all for Demo Member). Utilization targets:
        # GHS annual S$2,000 → approved 350+450=800, remaining 1,200.
        # 'Outpatient GP' item S$800/yr → approved 350, remaining 450.
        # 'Dental Emergency' item S$500/yr → approved 450, remaining 50.
        # Flex balance 1,000 → approved 200 → available 800;
        # Dental sub-limit 500 → approved 200, remaining 300.
        _add_claim(
            db, suffix="10", benefit_key="Outpatient GP", claim_type="Outpatient GP",
            incurred=date(2026, 2, 10), amount=350.0, approved=350.0, status="approved",
        )
        _add_claim(
            db, suffix="11", benefit_key="Dental Emergency", claim_type="Dental Emergency",
            incurred=date(2026, 3, 22), amount=480.0, approved=450.0, status="approved",
            provider="Demo Dental Surgery",
        )
        flagged = _add_claim(
            db, suffix="12", benefit_key="Outpatient GP", claim_type="Outpatient GP",
            incurred=date(2026, 5, 14), amount=120.0, status="ai_flagged",
        )
        manual = _add_claim(
            db, suffix="13", benefit_key="Specialist Consultation",
            claim_type="Specialist Consultation",
            incurred=date(2026, 6, 2), amount=90.0, status="submitted",
            provider="Demo Specialist Centre",
        )
        _add_claim(
            db, suffix="14", benefit_key="Outpatient GP", claim_type="Outpatient GP",
            incurred=date(2026, 6, 18), amount=60.0, status="needs_info",
            decision_notes="Please attach the itemized receipt.",
        )
        # Guard demo (insured): approving the full 500 exceeds the tightest
        # remaining (Outpatient GP item: 800 - 350 = 450) → 409 limit_exceeded.
        _add_claim(
            db, suffix="15", benefit_key="Outpatient GP", claim_type="Outpatient GP",
            incurred=date(2026, 6, 25), amount=500.0, status="submitted",
        )
        _add_claim(
            db, suffix="16", kind="flex", flex_category="Dental", claim_type="Dental",
            incurred=date(2026, 4, 8), amount=200.0, approved=200.0, status="approved",
            provider="Demo Dental Surgery",
        )
        _add_claim(
            db, suffix="17", kind="flex", flex_category="Optical", claim_type="Optical",
            incurred=date(2026, 6, 20), amount=150.0, status="submitted",
            provider="Demo Optics",
        )
        # Guard demo (flex): Dental sub-limit remaining is 500 - 200 = 300 < 400.
        _add_claim(
            db, suffix="18", kind="flex", flex_category="Dental", claim_type="Dental",
            incurred=date(2026, 6, 28), amount=400.0, status="submitted",
            provider="Demo Dental Surgery",
        )

        # ── Canned AI reviews (panel renders with NO provider key configured).
        db.add(
            ClaimAIReview(
                id=_P + "20",
                client_id=DEMO_CLIENT_ID,
                claim_id=flagged.id,
                status="complete",
                verdict="flagged",
                confidence=0.82,
                summary=(
                    "The receipt total (S$95.00) does not match the claimed amount "
                    "(S$120.00), and a vision re-check confirmed the discrepancy."
                    "\n\nFlagged: Field mismatch: amount_claimed"
                ),
                extractions=[
                    {
                        "document_id": _P + "912",
                        "file_name": "receipt-12.pdf",
                        "document_type": "receipt",
                        "fields": [
                            {"id": "field_1", "label": "Clinic Name",
                             "value": "Demo Family Clinic", "field_type": "text",
                             "confidence": 0.98},
                            {"id": "field_2", "label": "Visit Date",
                             "value": "2026-05-14", "field_type": "date",
                             "confidence": 0.97},
                            {"id": "field_3", "label": "Total Amount",
                             "value": "95.00", "field_type": "currency",
                             "confidence": 0.95},
                        ],
                    }
                ],
                field_comparisons=[
                    {"field_name": "amount_claimed", "claim_value": "120.0",
                     "document_value": "95.00", "status": "MISMATCH",
                     "confidence": 0.96,
                     "notes": "Vision re-check confirmed the discrepancy: the "
                              "receipt total reads SGD 95.00."},
                    {"field_name": "incurred_date", "claim_value": "2026-05-14",
                     "document_value": "2026-05-14", "status": "MATCH",
                     "confidence": 0.99},
                    {"field_name": "provider_name", "claim_value": "Demo Family Clinic",
                     "document_value": "Demo Family Clinic", "status": "MATCH",
                     "confidence": 0.99},
                    {"field_name": "currency", "claim_value": "SGD",
                     "document_value": "SGD", "status": "MATCH", "confidence": 0.98},
                ],
                rule_results=[
                    {"rule": "Incurred date falls within the active policy year.",
                     "status": "pass", "source": "deterministic",
                     "evidence": "2026-05-14 is within 2026-01-01 to 2026-12-31."},
                    {"rule": "No receipt is reused from another live claim.",
                     "status": "pass", "source": "deterministic",
                     "evidence": "1 document(s), no hash reuse across live claims."},
                    {"rule": "The submitted documents must be proof of actual "
                             "treatment/payment.",
                     "status": "pass", "source": "ai",
                     "evidence": "The receipt shows a paid NETS transaction."},
                    {"rule": "Required document present: receipt or tax invoice",
                     "status": "pass", "source": "ai", "evidence": "Found."},
                ],
                vision_checks=[
                    {"field_name": "amount_claimed",
                     "question": 'The claim states amount_claimed = "120.0". Does '
                                 "this document show that value, or a semantically "
                                 "equivalent one?",
                     "document_id": _P + "912", "file_name": "receipt-12.pdf",
                     "verdict": "REFUTED",
                     "explanation": "The receipt clearly shows 'Total amount: "
                                    "SGD 95.00'; no line matches 120.00."},
                ],
                model="claude-sonnet-4-6",
                input_tokens=6412,
                output_tokens=982,
                cost_estimate_usd=0.033966,
            )
        )
        db.add(
            ClaimAIReview(
                id=_P + "21",
                client_id=DEMO_CLIENT_ID,
                claim_id=manual.id,
                status="error",
                rule_results=[
                    {"rule": "Incurred date falls within the active policy year.",
                     "status": "pass", "source": "deterministic",
                     "evidence": "2026-06-02 is within 2026-01-01 to 2026-12-31."},
                    {"rule": "No receipt is reused from another live claim.",
                     "status": "pass", "source": "deterministic",
                     "evidence": "1 document(s), no hash reuse across live claims."},
                ],
                error_detail=(
                    "AI provider not configured. Set VERTEX_PROJECT + "
                    "VERTEX_LOCATION/_MODEL with Google ADC, or configure a "
                    "tenant BYOK service-account key on the AI provider "
                    "settings page."
                ),
            )
        )

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_claims_demo()
    print(
        "Claims demo seeded onto the Demo client.\n\n"
        "Broker UI (http://localhost:5173):\n"
        "  - switch client to the Demo client, policy year 2027 "
        "(2026-01-01 to 2026-12-31, ACTIVE)\n"
        "  - /claims/review            -> queue with flagged/manual/needs-info/"
        "submitted claims + AI review panel\n"
        "  - /policy-admin/coverage-members -> pick 'Demo Member' -> statement + "
        "utilization panel\n"
        "  - /policy-admin/member-listing?tab=dependants -> 'Pending dependant "
        "approvals' card (Demo Junior)\n"
        "  - approve claim 'Outpatient GP S$500' or flex 'Dental S$400' in full "
        "-> 409 limit_exceeded -> 'Approve anyway'\n\n"
        "Member portal (http://localhost:5173/portal/sign-in):\n"
        f"  - {MEMBER_EMAIL}  (dev+mock auto-fills the OTP)\n"
        "  - My benefits / My claims / My usage all have data\n"
        f"  - {COLLEAGUE_EMAIL} signs in to an empty view (member isolation)\n"
    )
