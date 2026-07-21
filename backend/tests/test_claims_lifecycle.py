"""Claim lifecycle: member submission validations, member isolation, broker
decisions, and the status machine.

`build_member_statement` is monkeypatched to a canned statement — plan
hydration has its own test coverage; here we exercise the claims rules on a
known coverage shape (GHS with two benefit items + a flex wallet with one
claimable category).
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_claims_lifecycle.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.core.settings import clear_settings_cache  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Claim,
    Dependant,
    Employee,
    MemberAccount,
    PolicyYear,
)
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    FlexBenefitCategoryLine,
    FlexCoverageLine,
    StatementEmployee,
)
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-00000000cl01"
EMP_A = "00000000-0000-0000-0000-00000000cl02"
EMP_C = "00000000-0000-0000-0000-00000000cl03"
DEP_A = "00000000-0000-0000-0000-00000000cl04"
ACC_A = "00000000-0000-0000-0000-00000000cl05"
ACC_C = "00000000-0000-0000-0000-00000000cl06"
# Active, but NOT in the GHS covered subset (elected spouse-only style).
DEP_B = "00000000-0000-0000-0000-00000000cl07"
# Portal self-added, pending broker approval.
DEP_P = "00000000-0000-0000-0000-00000000cl08"

PDF = b"%PDF-1.4 test receipt bytes"


def _statement_for(employee: Employee) -> BenefitStatementOut:
    dep = DependantSummary(id=DEP_A, name="Alice Jr", relationship="child")
    dep_b = DependantSummary(id=DEP_B, name="Ben", relationship="child")
    return BenefitStatementOut(
        employee=StatementEmployee(
            id=employee.id, staff_id=employee.staff_id, employee_name=employee.employee_name
        ),
        policy_year_id=employee.policy_year_id,
        is_matched=True,
        coverage=[
            CoverageLine(
                product_code="GHS",
                product_name="Group Hospital & Surgical",
                plan_code="P1",
                annual_policy_limit="S$1,000,000",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "Room & Board", "value": "S$650/day"},
                        {"number": "2", "name": "Outpatient GP", "value": "As charged"},
                    ]
                },
                covers_dependants=employee.id == EMP_A,
                # Elected subset: DEP_A only — DEP_B is active on the record
                # but NOT covered under this plan.
                covered_dependants=[dep] if employee.id == EMP_A else [],
            ),
            # GP coverage whose schedule carries a TCM row but no physio row —
            # exercises the plan-aware GP riders (TCM offered/claimable,
            # Physiotherapy rejected at submit).
            CoverageLine(
                product_code="GCGP",
                product_name="Group Clinical GP",
                plan_code="P1",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "GP Consultation", "value": "As charged"},
                        {
                            "number": "2",
                            "name": "TCM & Chiropractor",
                            "value": "S$300 per policy year",
                        },
                    ]
                },
                covers_dependants=False,
                covered_dependants=[],
            ),
        ],
        dependants=[dep, dep_b] if employee.id == EMP_A else [],
        flex=FlexCoverageLine(
            tier_name="Tier 1",
            wallet_amount=1000.0,
            currency="SGD",
            benefit_categories=[
                FlexBenefitCategoryLine(name="Dental", claimable=True, sub_limit=500.0),
                FlexBenefitCategoryLine(name="Gym", claimable=False),
            ],
        ),
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    # Route retained storage at a temp dir for the module.
    storage_dir = tmp_path_factory.mktemp("claims_storage")
    os.environ["INSPRO_STORAGE_DIR"] = str(storage_dir)
    clear_settings_cache()

    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY,
                client_id=DEMO_CLIENT_ID,
                year=2027,  # NOT 2026 — seed() one_or_none's the demo 2026 year
                start_date=date(2027, 4, 1),
                end_date=date(2028, 3, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        for emp_id, staff, name, acc in (
            (EMP_A, "CL-1", "Alice", ACC_A),
            (EMP_C, "CL-2", "Carol", ACC_C),
        ):
            session.add(
                MemberAccount(
                    id=acc, client_id=DEMO_CLIENT_ID, email=f"{name.lower()}@cl.test",
                    staff_id=staff, status="active",
                )
            )
            session.add(
                Employee(
                    id=emp_id, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                    staff_id=staff, employee_name=name, member_account_id=acc,
                    attribute_values={}, derived_attribute_values={},
                    source="csv_import", status="active",
                )
            )
        session.flush()
        for dep_id, dep_name, dep_status in (
            (DEP_A, "Alice Jr", "active"),
            (DEP_B, "Ben", "active"),
            (DEP_P, "Pending Kid", "pending_approval"),
        ):
            session.add(
                Dependant(
                    id=dep_id, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                    employee_id=EMP_A,
                    attribute_values={"name": dep_name, "relationship": "child"},
                    link_method="staff_id", status=dep_status,
                )
            )
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(Claim).delete()
        session.query(MemberAccount).filter(
            MemberAccount.client_id == DEMO_CLIENT_ID
        ).delete()
        py = session.get(PolicyYear, PY)
        if py is not None:
            session.delete(py)
        session.commit()
    os.environ.pop("INSPRO_STORAGE_DIR", None)
    clear_settings_cache()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _canned_statement(monkeypatch):
    from app.services import claims as claims_service

    monkeypatch.setattr(
        claims_service, "build_member_statement", lambda db, emp: _statement_for(emp)
    )
    # coverage-options imports it separately.
    from app.api.v1 import portal_claims

    monkeypatch.setattr(
        portal_claims, "build_member_statement", lambda db, emp: _statement_for(emp)
    )


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
    """This module tests the claim state machine, not the AI pipeline
    (test_claims_review_pipeline.py covers that) — submit/rerun leave the
    claim parked at ai_review_pending."""
    from app.api.v1 import claims as broker_claims
    from app.api.v1 import portal_claims

    monkeypatch.setattr(portal_claims, "run_review", lambda *a, **k: None)
    monkeypatch.setattr(broker_claims, "run_review", lambda *a, **k: None)


@pytest.fixture
def anon() -> TestClient:
    return TestClient(app)


@pytest.fixture
def broker() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _auth(account_id: str) -> dict[str, str]:
    token, _ = issue_member_token(account_id, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def _draft(
    anon: TestClient,
    account: str = ACC_A,
    **overrides,
) -> dict:
    body = {
        "claim_kind": "insured",
        "product_code": "GHS",
        "claim_type": "Group Hospital & Surgical",
        # Emergency = the generic invoice/receipt document slot; the
        # hospitalisation sub-type (sector-specific slots) has its own tests.
        "sub_type": "Emergency Accidental Outpatient Treatment",
        "incurred_date": "2027-06-15",
        "provider_name": "Raffles Medical",
        "invoice_number": "INV-00123",
        "diagnosis": "Dengue fever",
        "amount_claimed": 85.0,
        "currency": "SGD",
    }
    body.update(overrides)
    res = anon.post("/api/v1/portal/claims", json=body, headers=_auth(account))
    assert res.status_code == 201, res.text
    return res.json()


def _upload(
    anon: TestClient,
    claim_id: str,
    content: bytes = PDF,
    account: str = ACC_A,
    doc_type: str | None = None,
):
    return anon.post(
        f"/api/v1/portal/claims/{claim_id}/documents",
        files={"file": ("receipt.pdf", content, "application/pdf")},
        data={"doc_type": doc_type} if doc_type else {},
        headers=_auth(account),
    )


def _submit(anon: TestClient, claim_id: str, account: str = ACC_A):
    return anon.post(
        f"/api/v1/portal/claims/{claim_id}/submit", headers=_auth(account)
    )


# ── Happy path ───────────────────────────────────────────────────────────────


def test_draft_upload_submit_flow(anon: TestClient):
    claim = _draft(anon)
    assert claim["status"] == "draft"

    res = _upload(anon, claim["id"], PDF + b" flow-1")
    assert res.status_code == 200, res.text
    assert res.json()["file_name"] == "receipt.pdf"

    res = _submit(anon, claim["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ai_review_pending"
    assert body["submitted_at"] is not None
    assert len(body["documents"]) == 1

    listing = anon.get("/api/v1/portal/claims", headers=_auth(ACC_A))
    assert listing.status_code == 200
    assert claim["id"] in {c["id"] for c in listing.json()["items"]}


def test_coverage_options(anon: TestClient):
    res = anon.get("/api/v1/portal/coverage-options", headers=_auth(ACC_A))
    assert res.status_code == 200, res.text
    body = res.json()
    ghs = body["insured"][0]
    assert ghs["product_code"] == "GHS"
    # Claim-intake profile drives the conditional form fields.
    assert ghs["sub_types"] == [
        "Follow up Pre-/Post-Hospitalisation",
        "Hospitalisation/Day Surgery/Other Inpatient Treatment",
        "Emergency Accidental Outpatient Treatment",
        "Kidney Dialysis/Cancer Treatment",
    ]
    assert ghs["diagnosis_required"] is True
    assert ghs["requires_referral"] is False
    # Inpatient products expand into their sub-claim types.
    assert ghs["category"] == "inpatient"
    assert [t["sub_type"] for t in ghs["claim_types"]] == ghs["sub_types"]
    # GP-family is outpatient: plain GP always, TCM only because the plan's
    # schedule has a TCM row, no Physiotherapy (no matching row).
    gp = body["insured"][1]
    assert gp["product_code"] == "GCGP"
    assert gp["category"] == "outpatient"
    assert [t["label"] for t in gp["claim_types"]] == [
        "GP (General Practitioner)",
        "TCM (Traditional Chinese Medicine)",
    ]
    assert gp["claim_types"][0]["sub_type"] is None
    # Document slots: GP is the generic invoice/receipt; the hospitalisation
    # entry defaults to the private set and carries both sector sets for the
    # hospital picker; other inpatient sub-types stay generic.
    assert [s["key"] for s in gp["claim_types"][0]["doc_slots"]] == ["invoice_receipt"]
    hosp = ghs["claim_types"][1]
    assert hosp["sub_type"] == "Hospitalisation/Day Surgery/Other Inpatient Treatment"
    assert [s["key"] for s in hosp["doc_slots"]] == [
        "summary_tax_invoice",
        "itemised_tax_invoice",
        "discharge_summary",
    ]
    assert [s["key"] for s in hosp["doc_slots_by_sector"]["govt"]] == [
        "finalised_tax_invoice"
    ]
    emergency = ghs["claim_types"][2]
    assert [s["key"] for s in emergency["doc_slots"]] == ["invoice_receipt"]
    assert emergency["doc_slots_by_sector"] is None
    # Hospital registry rides along for the picker.
    sectors = {h["sector"] for h in body["hospitals"]}
    assert sectors == {"govt", "private"}
    assert {"name": "Singapore General Hospital", "sector": "govt"} in body["hospitals"]
    assert {"name": "Raffles Hospital", "sector": "private"} in body["hospitals"]
    assert [s["key"] for s in body["flex"]["doc_slots"]] == ["invoice_receipt"]
    assert "SGD" in body["currencies"]
    assert body["flex"]["categories"] == [
        {"name": "Dental", "sub_limit": 500.0, "note": None}
    ]  # non-claimable Gym excluded
    assert body["dependants"][0]["id"] == DEP_A


def test_flex_claim_flow(anon: TestClient):
    claim = _draft(
        anon,
        claim_kind="flex",
        product_code=None,
        sub_type=None,
        flex_category_name="Dental",
        claim_type="dental",
        amount_claimed=120.0,
    )
    assert _upload(anon, claim["id"], PDF + b" flex-1").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200


# ── Submission validations ───────────────────────────────────────────────────


def test_submit_without_receipt_422(anon: TestClient):
    claim = _draft(anon)
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "receipt" in res.text.lower()


def test_incurred_date_outside_policy_year_422(anon: TestClient):
    claim = _draft(anon, incurred_date="2026-01-15")  # before 2027-04-01
    assert _upload(anon, claim["id"], PDF + b" oob-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "policy year" in res.text.lower()


def test_unknown_product_422(anon: TestClient):
    claim = _draft(anon, product_code="GTL", sub_type=None)
    assert _upload(anon, claim["id"], PDF + b" gtl-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "no GTL coverage" in res.text


def test_non_claimable_flex_category_422(anon: TestClient):
    claim = _draft(
        anon,
        claim_kind="flex",
        product_code=None,
        sub_type=None,
        flex_category_name="Gym",
        claim_type="wellness",
    )
    assert _upload(anon, claim["id"], PDF + b" gym-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422


# ── Smart intake validations (claim_intake.py) ───────────────────────────────


def _draft_res(anon: TestClient, account: str = ACC_A, **overrides):
    body = {
        "claim_kind": "insured",
        "product_code": "GHS",
        "claim_type": "Group Hospital & Surgical",
        "sub_type": "Emergency Accidental Outpatient Treatment",
        "incurred_date": "2027-06-15",
        "provider_name": "Raffles Medical",
        "invoice_number": "INV-00123",
        "diagnosis": "Dengue fever",
        "amount_claimed": 85.0,
        "currency": "SGD",
    }
    body.update(overrides)
    return anon.post("/api/v1/portal/claims", json=body, headers=_auth(account))


def test_ghs_missing_sub_type_422(anon: TestClient):
    res = _draft_res(anon, sub_type=None)
    assert res.status_code == 422
    assert "sub-type" in res.text.lower()


def test_ghs_invalid_sub_type_422(anon: TestClient):
    res = _draft_res(anon, sub_type="Cosmetic Surgery")
    assert res.status_code == 422


def test_sub_type_on_non_ghs_product_422(anon: TestClient):
    res = _draft_res(
        anon,
        product_code="GTL",
        sub_type="Hospitalisation/Day Surgery/Other Inpatient Treatment",
    )
    assert res.status_code == 422


def test_missing_diagnosis_422(anon: TestClient):
    res = _draft_res(anon, diagnosis=None)
    assert res.status_code == 422
    assert "diagnosis" in res.text.lower()


def test_bare_other_diagnosis_sentinel_422(anon: TestClient):
    # The "Other:" sentinel with no condition after it isn't a diagnosis — the
    # backend must reject it even though the frontend also blocks it.
    for bare in ("Other:", "Other: ", "other:", "Other"):
        res = _draft_res(anon, diagnosis=bare)
        assert res.status_code == 422, f"{bare!r} should be rejected"


def test_other_diagnosis_with_text_ok(anon: TestClient):
    res = _draft_res(anon, diagnosis="Other: rare tropical fever")
    assert res.status_code == 201, res.text


def test_unsupported_currency_422(anon: TestClient):
    res = _draft_res(anon, currency="BTC")
    assert res.status_code == 422
    assert "currency" in res.text.lower()


def test_legacy_sub_type_normalized(anon: TestClient):
    # Drafts created before the 2026-07 relabel (or by stale clients) fold
    # onto the current wording.
    claim = _draft(anon, sub_type="Hospitalisation or Day Surgery")
    assert claim["sub_type"] == "Hospitalisation/Day Surgery/Other Inpatient Treatment"


def test_remarks_over_500_chars_422(anon: TestClient):
    res = _draft_res(anon, remarks="x" * 501)
    assert res.status_code == 422
    assert _draft_res(anon, remarks="x" * 500).status_code == 201


def test_tcm_claim_binds_benefit_row(anon: TestClient):
    # TCM rides on GP coverage — submit stamps the plan's TCM row as the
    # claim's benefit_key so utilization tracks that row's limit.
    claim = _draft(
        anon,
        product_code="GCGP",
        claim_type="TCM (Traditional Chinese Medicine)",
        sub_type="TCM (Traditional Chinese Medicine)",
        diagnosis="Other: lower back pain",
    )
    assert _upload(anon, claim["id"], PDF + b" tcm-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 200, res.text
    assert res.json()["benefit_key"] == "TCM & Chiropractor"


def test_physio_without_plan_row_422(anon: TestClient):
    # The canned GP schedule has no physiotherapy row — the claim type isn't
    # available to this plan, so submit refuses it.
    claim = _draft(
        anon,
        product_code="GCGP",
        claim_type="Physiotherapy",
        sub_type="Physiotherapy",
        diagnosis="Other: knee strain",
    )
    assert _upload(anon, claim["id"], PDF + b" phy-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "does not include" in res.text


HOSP_SUB = "Hospitalisation/Day Surgery/Other Inpatient Treatment"


def test_govt_hospitalisation_needs_finalised_tax_invoice(anon: TestClient):
    claim = _draft(
        anon, sub_type=HOSP_SUB, provider_name="Singapore General Hospital"
    )
    # An untagged receipt doesn't satisfy the finalised-tax-invoice slot …
    assert _upload(anon, claim["id"], PDF + b" grh-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "Finalised tax invoice" in res.text
    # … a tagged one does.
    assert (
        _upload(
            anon, claim["id"], PDF + b" grh-2", doc_type="finalised_tax_invoice"
        ).status_code
        == 200
    )
    assert _submit(anon, claim["id"]).status_code == 200


def test_private_hospitalisation_needs_full_document_set(anon: TestClient):
    claim = _draft(anon, sub_type=HOSP_SUB, provider_name="Gleneagles Hospital")
    assert (
        _upload(
            anon, claim["id"], PDF + b" pvt-1", doc_type="summary_tax_invoice"
        ).status_code
        == 200
    )
    assert (
        _upload(
            anon, claim["id"], PDF + b" pvt-2", doc_type="itemised_tax_invoice"
        ).status_code
        == 200
    )
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "Discharge summary" in res.text
    assert (
        _upload(
            anon, claim["id"], PDF + b" pvt-3", doc_type="discharge_summary"
        ).status_code
        == 200
    )
    assert _submit(anon, claim["id"]).status_code == 200


def test_unlisted_hospital_gets_private_document_set(anon: TestClient):
    # Unlisted/overseas hospitals default to the stricter private set.
    claim = _draft(anon, sub_type=HOSP_SUB, provider_name="Bangkok Hospital")
    assert _upload(anon, claim["id"], PDF + b" ovs-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "Summary tax invoice" in res.text


def test_unknown_doc_type_tag_422(anon: TestClient):
    claim = _draft(anon)
    res = _upload(anon, claim["id"], PDF + b" tag-1", doc_type="mystery_doc")
    assert res.status_code == 422


def test_plain_gp_claim_needs_no_sub_type(anon: TestClient):
    claim = _draft(
        anon,
        product_code="GCGP",
        claim_type="GP (General Practitioner)",
        sub_type=None,
        diagnosis="Acute upper respiratory infection",
    )
    assert claim["sub_type"] is None
    assert _upload(anon, claim["id"], PDF + b" gp-1").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200


def test_specialist_requires_visit_type_422(anon: TestClient):
    res = _draft_res(
        anon, product_code="GCSP", claim_type="Group Clinical Specialist",
        sub_type=None,
    )
    assert res.status_code == 422
    assert "first visit" in res.text.lower()


def test_specialist_first_visit_requires_referral_422(anon: TestClient):
    # "Not applicable" was removed for SP (2026-07-21) — a first visit must
    # name a referral letter even when the member declares N/A.
    res = _draft_res(
        anon, product_code="GCSP", claim_type="Group Clinical Specialist",
        sub_type=None, visit_type="first", referral_not_applicable=True,
    )
    assert res.status_code == 422
    assert "referral" in res.text.lower()


def test_specialist_follow_up_auto_links_latest_letter(anon: TestClient):
    older = _upload_referral(anon, b" ref-old")
    newer = _upload_referral(anon, b" ref-new")
    res = _draft_res(
        anon, product_code="GCSP", claim_type="Group Clinical Specialist",
        sub_type=None, visit_type="follow_up",
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["visit_type"] == "follow_up"
    # A letter on file is linked automatically without the member naming one.
    # (Both letters land in the same second here, so created_at can tie —
    # asserting the exact winner would test SQLite timestamp resolution.)
    assert body["referral_document"]["id"] in {older["id"], newer["id"]}


def test_specialist_follow_up_without_letter_on_file_422(anon: TestClient):
    # Carol has never uploaded a referral letter — the system can't track one,
    # so the follow-up claim prompts for it.
    res = _draft_res(
        anon, account=ACC_C, product_code="GCSP",
        claim_type="Group Clinical Specialist",
        sub_type=None, visit_type="follow_up",
    )
    assert res.status_code == 422
    assert "referral letter on file" in res.text.lower()


def test_visit_type_on_non_specialist_product_422(anon: TestClient):
    res = _draft_res(anon, visit_type="first")
    assert res.status_code == 422
    assert "visit type" in res.text.lower()


def test_referral_on_non_specialist_product_422(anon: TestClient):
    letter = _upload_referral(anon, b" ref-gp")
    res = _draft_res(anon, referral_document_id=letter["id"])
    assert res.status_code == 422


def _upload_referral(anon: TestClient, marker: bytes, account: str = ACC_A) -> dict:
    res = anon.post(
        "/api/v1/portal/referral-letters",
        files={"file": ("referral.pdf", PDF + marker, "application/pdf")},
        headers=_auth(account),
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_referral_letter_upload_and_reuse(anon: TestClient):
    letter = _upload_referral(anon, b" ref-1")

    listing = anon.get("/api/v1/portal/referral-letters", headers=_auth(ACC_A))
    assert letter["id"] in {d["id"] for d in listing.json()}

    res = _draft_res(
        anon, product_code="GCSP", claim_type="Group Clinical Specialist",
        sub_type=None, visit_type="first", referral_document_id=letter["id"],
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["referral_document"]["id"] == letter["id"]

    # The same letter is reusable on a second specialist claim (referral
    # letters are member-level and never trip the duplicate-receipt check).
    res2 = _draft_res(
        anon, product_code="GCSP", claim_type="Group Clinical Specialist",
        sub_type=None, visit_type="first", referral_document_id=letter["id"],
        invoice_number="INV-2",
    )
    assert res2.status_code == 201, res2.text


def test_referral_letter_delete_unused(anon: TestClient):
    letter = _upload_referral(anon, b" ref-del")
    res = anon.delete(
        f"/api/v1/portal/referral-letters/{letter['id']}", headers=_auth(ACC_A)
    )
    assert res.status_code == 204, res.text
    listing = anon.get("/api/v1/portal/referral-letters", headers=_auth(ACC_A))
    assert letter["id"] not in {d["id"] for d in listing.json()}


def test_referral_letter_delete_in_use_409(anon: TestClient):
    letter = _upload_referral(anon, b" ref-inuse")
    claim = _draft_res(
        anon, product_code="GCSP", claim_type="Group Clinical Specialist",
        sub_type=None, visit_type="first", referral_document_id=letter["id"],
    )
    assert claim.status_code == 201, claim.text
    res = anon.delete(
        f"/api/v1/portal/referral-letters/{letter['id']}", headers=_auth(ACC_A)
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "referral_in_use"


def test_referral_letter_delete_other_member_404(anon: TestClient):
    letter = _upload_referral(anon, b" ref-del-iso", account=ACC_A)
    res = anon.delete(
        f"/api/v1/portal/referral-letters/{letter['id']}", headers=_auth(ACC_C)
    )
    assert res.status_code == 404


def test_referral_letter_member_isolation(anon: TestClient):
    letter = _upload_referral(anon, b" ref-iso", account=ACC_A)

    # Carol can't see Alice's letters …
    listing = anon.get("/api/v1/portal/referral-letters", headers=_auth(ACC_C))
    assert letter["id"] not in {d["id"] for d in listing.json()}

    # … and can't ride a claim on one (404, not 403 — existence not leaked).
    res = _draft_res(
        anon, account=ACC_C, product_code="GCSP",
        claim_type="Group Clinical Specialist", sub_type=None,
        visit_type="first", referral_document_id=letter["id"],
    )
    assert res.status_code == 404


# ── Dependant eligibility ────────────────────────────────────────────────────


def test_pending_dependant_not_claimable_422(anon: TestClient):
    res = _draft_res(anon, dependant_id=DEP_P)
    assert res.status_code == 422
    assert "approval" in res.text.lower()


def test_dependant_outside_covered_subset_422(anon: TestClient):
    # DEP_B is active but the GHS covered list (elected subset) is DEP_A only.
    claim = _draft(anon, dependant_id=DEP_B)
    assert _upload(anon, claim["id"], PDF + b" subset-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "not covered under your GHS plan" in res.text


def test_covered_dependant_claim_ok(anon: TestClient):
    claim = _draft(anon, dependant_id=DEP_A)
    assert claim["dependant_name"] == "Alice Jr"
    assert _upload(anon, claim["id"], PDF + b" cov-dep-1").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200


def test_flex_claim_for_dependant_flow(anon: TestClient):
    # Any ACTIVE dependant may draw down the member's flex wallet — DEP_B is
    # outside the GHS insured subset but still claimable under flex.
    claim = _draft(
        anon,
        claim_kind="flex",
        product_code=None,
        sub_type=None,
        flex_category_name="Dental",
        claim_type="dental",
        dependant_id=DEP_B,
        amount_claimed=80.0,
    )
    assert claim["dependant_name"] == "Ben"
    assert _upload(anon, claim["id"], PDF + b" flex-dep-1").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200


def test_flex_claim_for_pending_dependant_422(anon: TestClient):
    res = _draft_res(
        anon,
        claim_kind="flex",
        product_code=None,
        sub_type=None,
        flex_category_name="Dental",
        claim_type="dental",
        dependant_id=DEP_P,
    )
    assert res.status_code == 422


def test_broker_sees_referral_and_claimant(anon: TestClient, broker: TestClient):
    letter = _upload_referral(anon, b" ref-broker")
    res = _draft_res(
        anon, product_code="GCSP", claim_type="Group Clinical Specialist",
        sub_type=None, visit_type="first", referral_document_id=letter["id"],
    )
    assert res.status_code == 201, res.text
    claim_id = res.json()["id"]

    detail = broker.get(f"/api/v1/claims/{claim_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["referral_document"]["id"] == letter["id"]

    # The referral letter itself is downloadable through the broker route.
    dl = broker.get(
        f"/api/v1/claims/{claim_id}/documents/{letter['id']}/download"
    )
    assert dl.status_code == 200
    assert dl.content == PDF + b" ref-broker"


def test_claim_diagnoses_search(anon: TestClient):
    res = anon.get(
        "/api/v1/portal/claim-diagnoses?product_code=GCGP&q=chickenpox",
        headers=_auth(ACC_A),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["group"] == "gp"
    assert any("Chickenpox" in d["label"] for d in body["items"])

    # Group scoping: dental catalog doesn't surface GP conditions.
    res = anon.get(
        "/api/v1/portal/claim-diagnoses?product_code=GD&q=chickenpox",
        headers=_auth(ACC_A),
    )
    assert res.json()["items"] == []


def test_duplicate_receipt_409(anon: TestClient):
    receipt = PDF + b" duplicate-me"
    first = _draft(anon)
    assert _upload(anon, first["id"], receipt).status_code == 200
    assert _submit(anon, first["id"]).status_code == 200

    second = _draft(anon)
    assert _upload(anon, second["id"], receipt).status_code == 200
    res = _submit(anon, second["id"])
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "duplicate_receipt"


def test_insured_claim_requires_product_422(anon: TestClient):
    res = anon.post(
        "/api/v1/portal/claims",
        json={
            "claim_kind": "insured",
            "claim_type": "outpatient",
            "incurred_date": "2027-06-15",
            "amount_claimed": 50.0,
        },
        headers=_auth(ACC_A),
    )
    assert res.status_code == 422


def test_claim_for_someone_elses_dependant_404(anon: TestClient):
    res = anon.post(
        "/api/v1/portal/claims",
        json={
            "claim_kind": "insured",
            "product_code": "GHS",
            "claim_type": "Group Hospital & Surgical",
            "sub_type": "Emergency Accidental Outpatient Treatment",
            "incurred_date": "2027-06-15",
            "provider_name": "Raffles Medical",
            "invoice_number": "INV-00123",
            "diagnosis": "Dengue fever",
            "amount_claimed": 50.0,
            "dependant_id": DEP_A,  # Alice's dependant, Carol claiming
        },
        headers=_auth(ACC_C),
    )
    assert res.status_code == 404


def test_upload_wrong_file_type_415(anon: TestClient):
    claim = _draft(anon)
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/documents",
        files={"file": ("macro.xlsm", b"bytes", "application/vnd.ms-excel")},
        headers=_auth(ACC_A),
    )
    assert res.status_code == 415


# ── Member isolation ─────────────────────────────────────────────────────────


def test_member_cannot_see_other_members_claim(anon: TestClient):
    claim = _draft(anon, account=ACC_A)
    res = anon.get(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_C))
    assert res.status_code == 404
    res = anon.get("/api/v1/portal/claims", headers=_auth(ACC_C))
    assert claim["id"] not in {c["id"] for c in res.json()["items"]}


def test_member_cannot_submit_other_members_claim(anon: TestClient):
    claim = _draft(anon, account=ACC_A)
    assert _submit(anon, claim["id"], account=ACC_C).status_code == 404


# ── Draft management ─────────────────────────────────────────────────────────


def test_delete_draft(anon: TestClient):
    claim = _draft(anon)
    assert _upload(anon, claim["id"], PDF + b" del-1").status_code == 200
    res = anon.delete(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A))
    assert res.status_code == 204
    assert (
        anon.get(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A)).status_code
        == 404
    )


def test_delete_submitted_claim_409(anon: TestClient):
    claim = _draft(anon)
    assert _upload(anon, claim["id"], PDF + b" del-2").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200
    res = anon.delete(f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A))
    assert res.status_code == 409


# ── Broker decisions ─────────────────────────────────────────────────────────


def _submitted_claim(anon: TestClient, marker: bytes) -> str:
    claim = _draft(anon)
    assert _upload(anon, claim["id"], PDF + marker).status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200
    return claim["id"]


def test_broker_list_and_approve(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" approve-1")

    listing = broker.get(f"/api/v1/claims?policy_year_id={PY}&status=ai_review_pending")
    assert listing.status_code == 200, listing.text
    row = next(c for c in listing.json()["items"] if c["id"] == claim_id)
    assert row["staff_id"] == "CL-1"

    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "note": "OK"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["amount_approved"] == 85.0  # defaults to amount_claimed

    # Terminal: cannot decide again.
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision", json={"action": "reject"}
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "invalid_transition"


def test_broker_approve_with_explicit_amount(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" approve-2")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 60.0},
    )
    assert res.json()["amount_approved"] == 60.0


def test_broker_reject(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" reject-1")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "reject", "note": "Not covered"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    assert res.json()["amount_approved"] is None


def test_needs_info_roundtrip(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" info-1")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "needs_info", "note": "Send the itemized bill"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "needs_info"

    # The member sees the note, adds a doc, and resubmits.
    detail = anon.get(f"/api/v1/portal/claims/{claim_id}", headers=_auth(ACC_A))
    assert detail.json()["decision_notes"] == "Send the itemized bill"
    assert _upload(anon, claim_id, PDF + b" info-1b").status_code == 200
    res = _submit(anon, claim_id)
    assert res.status_code == 200
    assert res.json()["status"] == "ai_review_pending"


def test_claim_out_exposes_required_doc_slots(anon: TestClient):
    # The claim payload carries the slots it must fill, so the draft/needs_info
    # detail page can render tagged uploads that match what submit enforces.
    claim = _draft(anon, sub_type=HOSP_SUB, provider_name="Gleneagles Hospital")
    detail = anon.get(
        f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A)
    )
    keys = [s["key"] for s in detail.json()["required_doc_slots"]]
    assert keys == ["summary_tax_invoice", "itemised_tax_invoice", "discharge_summary"]


def test_needs_info_resubmit_with_slot_documents(
    anon: TestClient, broker: TestClient
):
    # A private-hospitalisation claim goes to needs_info; the member must be
    # able to attach the tagged slot documents and resubmit (the untagged
    # add-receipt path alone can't satisfy the specific slots).
    claim = _draft(anon, sub_type=HOSP_SUB, provider_name="Gleneagles Hospital")
    for key, marker in (
        ("summary_tax_invoice", b" sum"),
        ("itemised_tax_invoice", b" item"),
        ("discharge_summary", b" disch"),
    ):
        assert _upload(anon, claim["id"], PDF + marker, doc_type=key).status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200

    res = broker.post(
        f"/api/v1/claims/{claim['id']}/decision",
        json={"action": "needs_info", "note": "Re-send a clearer discharge summary"},
    )
    assert res.status_code == 200

    # Adding an UNTAGGED replacement doesn't newly satisfy any specific slot,
    # but the originally tagged slot docs persist, so resubmit still succeeds.
    assert _upload(anon, claim["id"], PDF + b" extra").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200

    # And a tagged replacement fills the slot for a claim that was missing it.
    fresh = _draft(anon, sub_type=HOSP_SUB, provider_name="Singapore General Hospital")
    assert _upload(anon, fresh["id"], PDF + b" grh").status_code == 200  # untagged
    assert _submit(anon, fresh["id"]).status_code == 422  # needs finalised tax invoice
    assert (
        _upload(
            anon, fresh["id"], PDF + b" grh2", doc_type="finalised_tax_invoice"
        ).status_code
        == 200
    )
    assert _submit(anon, fresh["id"]).status_code == 200


def test_broker_document_download(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" download-1")
    detail = broker.get(f"/api/v1/claims/{claim_id}")
    doc_id = detail.json()["documents"][0]["id"]
    res = broker.get(f"/api/v1/claims/{claim_id}/documents/{doc_id}/download")
    assert res.status_code == 200
    assert res.content == PDF + b" download-1"
    assert "receipt.pdf" in res.headers["content-disposition"]


def test_upload_after_submit_409(anon: TestClient):
    claim_id = _submitted_claim(anon, b" locked-1")
    assert _upload(anon, claim_id, PDF + b" locked-1b").status_code == 409
