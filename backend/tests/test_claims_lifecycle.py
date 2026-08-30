"""Claim lifecycle: member submission validations, member isolation, broker
decisions, and the status machine.

`build_member_statement` is monkeypatched to a canned statement — plan
hydration has its own test coverage; here we exercise the claims rules on a
known coverage shape (GHS with two benefit items + a flex wallet with one
claimable category).
"""
from __future__ import annotations

import itertools
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
    ClaimDocumentSetup,
    Dependant,
    Employee,
    MemberAccount,
    Plan,
    PolicyYear,
    Product,
)
from app.models.claim import MEMBER_EDITABLE_STATUSES  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    FlexBenefitCategoryLine,
    FlexCoverageLine,
    StatementEmployee,
)
from app.services.claims import _EDIT_BLOCKED_DEFAULT  # noqa: E402
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
            # Major Medical + a term-life line: both held by the member but
            # NOT filed through the portal — must be hidden from the claim
            # picker and rejected at submit.
            CoverageLine(
                product_code="GMM",
                product_name="Group Major Medical",
                plan_code="P1",
                covers_dependants=False,
                covered_dependants=[],
            ),
            CoverageLine(
                product_code="GTL",
                product_name="Group Term Life",
                plan_code="P1",
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
        ghs = session.query(Product).filter(Product.code == "GHS").first()
        assert ghs is not None
        session.add(
            Plan(
                product_id=ghs.id,
                policy_year_id=PY,
                code="P1",
                display_name="Plan 1",
                benefit_schedule={
                    "items": [
                        {"number": "1", "name": "Room & Board", "value": "S$650/day"},
                        {"number": "2", "name": "Outpatient GP", "value": "As charged"},
                    ]
                },
                status="confirmed",
            )
        )
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


# A claim is a duplicate when another live claim of the member's already
# carries its INVOICE NUMBER, so every draft needs its own unless the test is
# about duplicates (which passes `invoice_number=` explicitly).
_invoice_seq = itertools.count(1)


def _next_invoice() -> str:
    return f"INV-{next(_invoice_seq):05d}"


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
        "invoice_number": _next_invoice(),
        "diagnosis": "Dengue fever",
        "amount_claimed": 85.0,
        "currency": "SGD",
    }
    body.update(overrides)
    res = anon.post("/api/v1/portal/claims", json=body, headers=_auth(account))
    assert res.status_code == 201, res.text
    return res.json()


def test_client_hr_cannot_access_broker_claims_api(broker: TestClient):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="00000000-0000-0000-0000-00000000hr01",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="client_hr",
    )
    try:
        response = broker.get("/api/v1/claims", params={"policy_year_id": PY})
        assert response.status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="00000000-0000-0000-0000-000000000001",
            broker_firm_id=DEMO_BROKER_FIRM_ID,
            client_id=DEMO_CLIENT_ID,
            role="broker_admin",
        )


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
    # Only member-filed products appear — Major Medical (GMM) and the term-life
    # line (GTL) are held but never offered in the claim picker.
    codes = [p["product_code"] for p in body["insured"]]
    assert codes == ["GHS", "GCGP"]
    assert "GMM" not in codes and "GTL" not in codes
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
    assert [t["scope_code"] for t in ghs["claim_types"]] == [
        "ghs_pre_post",
        "ghs_hospitalisation",
        "ghs_emergency_outpatient",
        "ghs_dialysis_cancer",
    ]
    assert ghs["claim_types"][1]["scope_key"] == (
        "insured:ghs:ghs_hospitalisation"
    )
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
    assert [t["scope_code"] for t in gp["claim_types"]] == [
        "standard", "gp_tcm"
    ]
    assert gp["claim_types"][0]["benefit_key"] is None
    assert gp["claim_types"][1]["benefit_key"] == "TCM & Chiropractor"
    # Document slots: GP is the generic invoice/receipt; the hospitalisation
    # entry defaults to the private set and carries both sector sets for the
    # hospital picker; other inpatient sub-types stay generic.
    assert [s["key"] for s in gp["claim_types"][0]["doc_slots"]] == ["invoice_receipt"]
    hosp = ghs["claim_types"][1]
    assert hosp["sub_type"] == "Hospitalisation/Day Surgery/Other Inpatient Treatment"
    assert hosp["supports_stay_dates"] is True
    assert [s["key"] for s in hosp["doc_slots"]] == [
        "summary_tax_invoice",
        "itemised_tax_invoice",
        "discharge_summary",
    ]
    assert [s["key"] for s in hosp["doc_slots_by_sector"]["govt"]] == [
        "finalised_tax_invoice"
    ]
    emergency = ghs["claim_types"][2]
    assert emergency["supports_stay_dates"] is False
    assert [s["key"] for s in emergency["doc_slots"]] == ["invoice_receipt"]
    assert [s["key"] for s in emergency["doc_slots_by_sector"]["govt"]] == [
        "invoice_receipt"
    ]
    assert [s["key"] for s in emergency["doc_slots_by_sector"]["private"]] == [
        "invoice_receipt"
    ]
    # Hospital registry rides along for the picker.
    sectors = {h["sector"] for h in body["hospitals"]}
    assert sectors == {"govt", "private"}
    assert {"name": "Singapore General Hospital", "sector": "govt"} in body["hospitals"]
    assert {"name": "Raffles Hospital", "sector": "private"} in body["hospitals"]
    assert [s["key"] for s in body["flex"]["doc_slots"]] == ["invoice_receipt"]
    assert "SGD" in body["currencies"]
    assert body["flex"]["categories"] == [
        {
            "name": "Dental",
            "sub_limit": 500.0,
            "note": None,
            "doc_slots": [
                {
                    "key": "invoice_receipt",
                    "label": "Invoice or receipt",
                    "instructions": "Attach the invoice or receipt.",
                }
            ],
        }
    ]  # non-claimable Gym excluded; each category owns its documents
    assert body["dependants"][0]["id"] == DEP_A


def test_claim_type_document_setup_is_enforced_and_duplicates_independently(
    anon: TestClient, broker: TestClient
):
    response = broker.get("/api/v1/claim-document-setups")
    assert response.status_code == 200, response.text
    setups = response.json()
    matching_sources = [
        row
        for row in setups
        if row["claim_key"].upper() == "GHS"
        and row["scope_code"] == "ghs_emergency_outpatient_private"
    ]
    assert matching_sources, [
        (row["claim_key"], row["scope_code"], row["display_label"])
        for row in setups
    ]
    source = matching_sources[0]
    target = next(row for row in setups if row["scope_key"] != source["scope_key"])

    document = {
        "id": "medical-evidence-source",
        "key": "medical_evidence",
        "display": "Medical evidence",
        "instructions": "Attach the clinic's final medical evidence.",
        "aliases": ["clinic evidence", "medical proof"],
        "key_fields": [
            {
                "name": "Patient name",
                "keywords": ["patient", "name"],
                "optional": False,
            }
        ],
    }

    def payload(row: dict, documents: list[dict], expected=None) -> dict:
        return {
            "claim_kind": row["claim_kind"],
            "claim_key": row["claim_key"],
            "scope_code": row["scope_code"],
            "display_label": row["display_label"],
            "documents": documents,
            "expected_updated_at": expected,
        }

    created_ids: list[str] = []
    try:
        unavailable = broker.put(
            "/api/v1/claim-document-setups",
            json={
                "claim_kind": "insured",
                "claim_key": "NOT-A-COMPANY-PRODUCT",
                "scope_code": "standard",
                "display_label": "Unavailable type",
                "documents": [document],
            },
        )
        assert unavailable.status_code == 422, unavailable.text
        assert unavailable.json()["detail"]["code"] == "claim_type_not_available"
        with SessionLocal() as session:
            assert not session.query(ClaimDocumentSetup).filter(
                ClaimDocumentSetup.claim_key == "NOT-A-COMPANY-PRODUCT"
            ).count()

        saved_response = broker.put(
            "/api/v1/claim-document-setups", json=payload(source, [document])
        )
        assert saved_response.status_code == 200, saved_response.text
        saved = saved_response.json()
        created_ids.append(saved["id"])
        assert saved["is_default"] is False

        collision = broker.put(
            "/api/v1/claim-document-setups",
            json=payload(
                saved,
                [
                    document,
                    {
                        **document,
                        "id": "ambiguous-second-document",
                        "key": "other_evidence",
                        "display": "Other evidence",
                        "aliases": ["medical proof"],
                    },
                ],
                saved["updated_at"],
            ),
        )
        assert collision.status_code == 409
        assert collision.json()["detail"]["code"] == "ambiguous_claim_document_alias"

        coverage = anon.get(
            "/api/v1/portal/coverage-options", headers=_auth(ACC_A)
        )
        assert coverage.status_code == 200, coverage.text
        emergency = next(
            option
            for product in coverage.json()["insured"]
            if product["product_code"] == "GHS"
            for option in product["claim_types"]
            if "emergency accidental" in option["label"].lower()
        )
        private_documents = emergency["doc_slots_by_sector"]["private"]
        assert [slot["key"] for slot in private_documents] == ["medical_evidence"]
        assert emergency["doc_slots"] == private_documents

        claim = _draft(anon)
        missing = _submit(anon, claim["id"])
        assert missing.status_code == 422
        assert "medical evidence" in missing.text.lower()
        attached = _upload(
            anon,
            claim["id"],
            PDF + b" scoped-medical-evidence",
            doc_type="medical_evidence",
        )
        assert attached.status_code == 200, attached.text
        assert _submit(anon, claim["id"]).status_code == 200

        duplicated_response = broker.post(
            "/api/v1/claim-document-setups/duplicate",
            json={
                "source_scope_key": saved["scope_key"],
                "target": payload(target, []),
            },
        )
        assert duplicated_response.status_code == 200, duplicated_response.text
        duplicated = duplicated_response.json()
        created_ids.append(duplicated["id"])
        assert duplicated["documents"][0]["id"] != saved["documents"][0]["id"]

        target_document = {
            **duplicated["documents"][0],
            "display": "Target-only evidence",
        }
        updated_response = broker.put(
            "/api/v1/claim-document-setups",
            json=payload(
                duplicated, [target_document], duplicated["updated_at"]
            ),
        )
        assert updated_response.status_code == 200, updated_response.text
        refreshed = broker.get("/api/v1/claim-document-setups").json()
        refreshed_source = next(
            row for row in refreshed if row["scope_key"] == saved["scope_key"]
        )
        assert refreshed_source["documents"][0]["display"] == "Medical evidence"
    finally:
        with SessionLocal() as session:
            for setup_id in created_ids:
                row = session.get(ClaimDocumentSetup, setup_id)
                if row is not None:
                    session.delete(row)
            session.commit()


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
    # A product the member doesn't hold — absent from the resolved statement.
    claim = _draft(anon, product_code="GHSX", sub_type=None)
    assert _upload(anon, claim["id"], PDF + b" ghsx-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "no GHSX coverage" in res.text


def test_non_member_claimable_product_422(anon: TestClient):
    # GTL (term life) is held but never filed through the portal — it's hidden
    # from the picker and rejected at submit even if a request bypasses the UI.
    claim = _draft(anon, product_code="GTL", sub_type=None)
    assert _upload(anon, claim["id"], PDF + b" gtl-1").status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 422
    assert "aren't submitted through the portal" in res.text


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
        "invoice_number": _next_invoice(),
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


def test_hospitalisation_stay_dates_are_optional_consistent_and_served(
    anon: TestClient,
):
    sub_type = "Hospitalisation/Day Surgery/Other Inpatient Treatment"

    visit_only = _draft_res(
        anon, sub_type=sub_type, admission_date=None, discharge_date=None
    )
    assert visit_only.status_code == 201
    assert visit_only.json()["incurred_date"] == "2027-06-15"
    assert visit_only.json()["admission_date"] is None
    assert visit_only.json()["discharge_date"] is None

    reversed_range = _draft_res(
        anon,
        sub_type=sub_type,
        admission_date="2027-06-15",
        discharge_date="2027-06-14",
    )
    assert reversed_range.status_code == 422
    assert "cannot be before" in reversed_range.text.lower()

    independent_visit = _draft_res(
        anon,
        sub_type=sub_type,
        incurred_date="2027-06-14",
        admission_date="2027-06-15",
        discharge_date="2027-06-17",
    )
    assert independent_visit.status_code == 201
    assert independent_visit.json()["incurred_date"] == "2027-06-14"
    assert independent_visit.json()["admission_date"] == "2027-06-15"

    # A day-surgery stay can admit and discharge on the same day.
    claim = _draft(
        anon,
        sub_type=sub_type,
        admission_date="2027-06-15",
        discharge_date="2027-06-15",
    )
    assert claim["incurred_date"] == "2027-06-15"
    assert claim["admission_date"] == "2027-06-15"
    assert claim["discharge_date"] == "2027-06-15"
    assert claim["supports_stay_dates"] is True


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


# ── Served editability (docs/CLAIM_AMENDMENT_PLAN.md, phase 3) ───────────────


def test_member_editable_is_served_and_closes_at_the_decision(
    anon: TestClient, broker: TestClient
):
    """The flag the portal renders its edit button off.

    SERVED, so the client never switches on `status` — the mirror it used to
    keep (`draft || needs_info`) is exactly what stopped being true when the
    window widened to "until the broker decides".
    """
    claim = _draft(anon)
    body = anon.get(
        f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A)
    ).json()
    assert body["member_editable"] is True
    assert body["member_edit_block"] is None
    # The concurrency token rides along from the first read.
    assert body["revision"] == 0
    assert body["amended_at"] is None

    assert _upload(anon, claim["id"], PDF + b" editable-1").status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200

    # In review, undecided — still theirs.
    assert anon.get(
        f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A)
    ).json()["member_editable"] is True

    assert broker.post(
        f"/api/v1/claims/{claim['id']}/decision", json={"action": "approve"}
    ).status_code == 200

    body = anon.get(
        f"/api/v1/portal/claims/{claim['id']}", headers=_auth(ACC_A)
    ).json()
    assert body["member_editable"] is False
    assert "has been assessed" in body["member_edit_block"]


def test_a_rejected_claim_says_closed_not_assessed(
    anon: TestClient, broker: TestClient
):
    """Two different refusals. "Assessed" invites a correction; "closed"
    invites a challenge, and a rejected claimant is asking the second
    question."""
    claim_id = _submitted_claim(anon, b" editable-rej")
    assert broker.post(
        f"/api/v1/claims/{claim_id}/decision", json={"action": "reject"}
    ).status_code == 200
    body = anon.get(
        f"/api/v1/portal/claims/{claim_id}", headers=_auth(ACC_A)
    ).json()
    assert body["member_editable"] is False
    assert "was closed" in body["member_edit_block"]


def test_reclassifying_to_a_LOG_case_takes_editing_away_not_visibility(
    anon: TestClient, broker: TestClient
):
    """The member keeps SEEING their own submission — the portal filters on
    `origin`, so reclassifying can never retract someone's own record — but it
    stops being theirs to edit.

    A LOG case is created outside `submit_claim` and may legitimately carry no
    documents at all, so re-validating one would refuse the member with "attach
    at least one receipt" on a case they never attached to and cannot satisfy.
    """
    claim_id = _submitted_claim(anon, b" editable-log")
    assert broker.patch(
        f"/api/v1/claims/{claim_id}/case-type",
        json={"case_type": "log", "reason": "recorded from the insurer's email"},
    ).status_code == 200

    res = anon.get(f"/api/v1/portal/claims/{claim_id}", headers=_auth(ACC_A))
    assert res.status_code == 200, "still visible — reclassifying is not a retraction"
    assert res.json()["member_editable"] is False
    assert "handling this case directly" in res.json()["member_edit_block"]


def test_member_editability_is_fail_closed():
    """Everything outside `MEMBER_EDITABLE_STATUSES` is refused, and a status
    with no sentence of its own still gets the default one rather than falling
    through to editable. A new status is far likelier to be post-decision than
    pre-, and wrongly denying an edit costs a message to the broker while
    wrongly allowing one rewrites a settled claim."""
    from app.models.claim import CASE_TYPE_CLAIM, CLAIM_STATUSES, ORIGIN_PORTAL
    from app.services.claims import member_editability

    def _at(status_value: str) -> Claim:
        # `origin` and `case_type` are pinned EXPLICITLY. Their column defaults
        # are Python-side and only run at flush, so a transient Claim() carries
        # `origin=None` and every status would fall into the fail-closed branch
        # — the test would pass for the wrong reason on exactly the statuses it
        # exists to check. This varies the status axis and holds the rest at
        # what a persisted member claim actually has.
        return Claim(
            client_id=DEMO_CLIENT_ID, policy_year_id=PY, employee_id=EMP_A,
            claim_kind="insured", product_code="GHS", claim_type="x",
            incurred_date=date(2027, 6, 15), amount_claimed=1.0,
            origin=ORIGIN_PORTAL, case_type=CASE_TYPE_CLAIM,
            status=status_value,
        )

    for status_value in CLAIM_STATUSES:
        editable, block = member_editability(_at(status_value))
        assert editable is (status_value in MEMBER_EDITABLE_STATUSES), status_value
        # Exactly one of the two is set — a refusal always carries its reason.
        assert (block is None) is editable, status_value

    # An unknown status is refused, not admitted.
    assert member_editability(_at("some_future_status")) == (
        False,
        _EDIT_BLOCKED_DEFAULT,
    )


# ── The shared validation chain (docs/CLAIM_AMENDMENT_PLAN.md, phase 2) ──────
#
# `validate_claim_facts` is everything submit checks about the CLAIM ITSELF,
# extracted so member and broker amendments run the identical rules. These pin
# the two things the split has to get right; the amendment ENDPOINTS that call
# it land in phase 4.


def test_editing_a_rider_claim_away_clears_its_benefit_key(anon: TestClient):
    """`_apply_gp_rider_benefit_key` used to only ever SET the key.

    Edit a GP-TCM claim back down to plain GP and `benefit_key="TCM &
    Chiropractor"` stayed behind — which passes `assert_coverage_claimable`
    (it is a real row on the schedule) while utilization keeps drawing the
    claim against the TCM sub-limit instead of the GP one. Silent, and in the
    money. Only an amendment can reach it, so only an amendment passes the flag.
    """
    from app.services.claims import validate_claim_facts

    claim = _draft(
        anon,
        product_code="GCGP",
        claim_type="TCM (Traditional Chinese Medicine)",
        sub_type="TCM (Traditional Chinese Medicine)",
        diagnosis="Other: lower back pain",
    )
    assert _upload(anon, claim["id"], PDF + b" rider-clear").status_code == 200
    assert _submit(anon, claim["id"]).json()["benefit_key"] == "TCM & Chiropractor"

    with SessionLocal() as s:
        row, emp = s.get(Claim, claim["id"]), s.get(Employee, EMP_A)
        row.sub_type = None
        row.claim_type = "Group Clinical GP"

        # Submit's own call never passes the flag: a legacy row carries a
        # benefit_key from before the column stopped being populated, and
        # blanking those on a needs_info resubmission would move their bucket
        # for no reason.
        validate_claim_facts(s, row, emp, enforce_doctor_name=False)
        assert row.benefit_key == "TCM & Chiropractor"

        # The amendment path, which changed the sub-type, does.
        validate_claim_facts(
            s, row, emp, enforce_doctor_name=False, clear_rider_key=True
        )
        assert row.benefit_key is None


def test_the_grace_deadline_is_a_SUBMIT_rule_not_a_claim_rule(anon: TestClient):
    """Grace bounds the ACT of submitting, never the claim.

    A claim already in the system was filed in time, so re-checking grace when
    it is amended would make a `needs_info` the broker sent back on the last
    grace day unanswerable the next morning — the member would be asked for a
    document and refused when they sent it. So `validate_claim_facts` passes a
    claim whose filing window has closed, and `submit_claim` still refuses it.
    """
    from fastapi import HTTPException

    from app.services.claims import submit_claim, validate_claim_facts

    claim = _draft(anon, incurred_date="2027-06-15")
    assert _upload(anon, claim["id"], PDF + b" grace-1").status_code == 200

    with SessionLocal() as s:
        year = s.get(PolicyYear, PY)
        original = (year.start_date, year.end_date, year.claim_grace_period_days)
        # Move the whole year into the past so the deadline can actually bite —
        # `business_today()` sits before the fixture's 2027 year.
        year.start_date, year.end_date = date(2025, 1, 1), date(2025, 12, 31)
        year.claim_grace_period_days = 0
        row, emp = s.get(Claim, claim["id"]), s.get(Employee, EMP_A)
        row.incurred_date = date(2025, 6, 1)  # inside the shifted year
        s.flush()
        try:
            # The claim itself is entirely valid.
            validate_claim_facts(s, row, emp, enforce_doctor_name=False)

            # Filing it is not.
            with pytest.raises(HTTPException) as exc:
                submit_claim(s, row, emp, submitted_by_member_id=None)
            assert exc.value.status_code == 422
            assert "closed on 2025-12-31" in exc.value.detail
        finally:
            s.rollback()
            year = s.get(PolicyYear, PY)
            (
                year.start_date,
                year.end_date,
                year.claim_grace_period_days,
            ) = original
            s.commit()


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


PRE_POST = "Follow up Pre-/Post-Hospitalisation"


def test_pre_post_hospitalisation_requires_the_doctor_422(anon: TestClient):
    """The consult is claimed against the admission it follows, and the doctor
    is what ties the two together — nothing else on the bill identifies the
    episode."""
    res = _draft_res(anon, sub_type=PRE_POST)
    assert res.status_code == 422
    assert "doctor" in res.text.lower()


def test_pre_post_hospitalisation_with_the_doctor_submits(anon: TestClient):
    claim = _draft(anon, sub_type=PRE_POST, doctor_name="Dr Lim Wei Sheng")
    assert claim["doctor_name"] == "Dr Lim Wei Sheng"
    assert _upload(anon, claim["id"]).status_code == 200
    res = _submit(anon, claim["id"])
    assert res.status_code == 200, res.text


def test_a_needs_info_claim_without_a_doctor_can_still_be_resent(anon: TestClient):
    """The doctor can only be entered on the claim FORM — a needs_info
    resubmission's only control is attaching documents. Re-checking the rule
    there would permanently strand any pre-/post- claim recorded before the
    field existed, with no member-side way to satisfy the refusal."""
    claim = _draft(anon, sub_type=PRE_POST, doctor_name="Dr Lim Wei Sheng")
    assert _upload(anon, claim["id"]).status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200
    with SessionLocal() as s:
        row = s.get(Claim, claim["id"])
        row.status = "needs_info"
        row.doctor_name = None  # as a claim filed before the field existed
        s.commit()

    assert _submit(anon, claim["id"]).status_code == 200


def test_other_claim_types_need_no_doctor(anon: TestClient):
    # Emergency A&E — same product, different sub-type.
    claim = _draft(anon)
    assert claim["doctor_name"] is None
    assert _upload(anon, claim["id"]).status_code == 200
    assert _submit(anon, claim["id"]).status_code == 200


def test_the_form_is_told_which_claim_types_need_a_doctor(anon: TestClient):
    """SERVED, never mirrored: the frontend must not have to match on the
    sub-type label to know whether to render the field."""
    res = anon.get("/api/v1/portal/coverage-options", headers=_auth(ACC_A))
    assert res.status_code == 200
    ghs = next(o for o in res.json()["insured"] if o["product_code"] == "GHS")
    by_sub = {t["sub_type"]: t["requires_doctor_name"] for t in ghs["claim_types"]}
    assert by_sub[PRE_POST] is True
    assert all(v is False for k, v in by_sub.items() if k != PRE_POST)


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


def test_duplicate_invoice_number_409(anon: TestClient):
    first = _draft(anon, invoice_number="INV-DUPE-1")
    assert _upload(anon, first["id"], PDF + b" first").status_code == 200
    assert _submit(anon, first["id"]).status_code == 200

    # Different receipt file, different amount — same invoice number.
    second = _draft(anon, invoice_number="INV-DUPE-1", amount_claimed=120.0)
    assert _upload(anon, second["id"], PDF + b" second").status_code == 200
    res = _submit(anon, second["id"])
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "duplicate_invoice_number"
    assert detail["conflicting_claim_ids"] == [first["id"]]


def test_duplicate_invoice_number_ignores_punctuation_and_case(anon: TestClient):
    first = _draft(anon, invoice_number="INV-2027/44")
    assert _upload(anon, first["id"]).status_code == 200
    assert _submit(anon, first["id"]).status_code == 200

    second = _draft(anon, invoice_number="inv 2027 44")
    assert _upload(anon, second["id"]).status_code == 200
    assert _submit(anon, second["id"]).status_code == 409


def test_same_receipt_on_two_invoices_is_not_a_duplicate(anon: TestClient):
    """The multi-invoice split attaches ONE discharge summary (or itemised
    bill) to every claim of an episode — the exact set the intake flow tells
    the member to upload together. Keying duplicates on the document hash
    blocked the second and third submissions of that set."""
    shared = PDF + b" shared discharge summary"
    first = _draft(anon, invoice_number="EPISODE-INV-1")
    assert _upload(anon, first["id"], shared).status_code == 200
    assert _submit(anon, first["id"]).status_code == 200

    second = _draft(anon, invoice_number="EPISODE-INV-2")
    assert _upload(anon, second["id"], shared).status_code == 200
    assert _submit(anon, second["id"]).status_code == 200


def test_duplicate_invoice_number_is_scoped_to_the_member(anon: TestClient):
    """A stranger's identically-numbered receipt must never make a member's own
    claim unfileable — short receipt numbers collide across providers, and the
    member has no way to override the block. Cross-member reuse is surfaced to
    the broker by the review's deterministic rule instead."""
    mine = _draft(anon, account=ACC_A, invoice_number="0001")
    assert _upload(anon, mine["id"], account=ACC_A).status_code == 200
    assert _submit(anon, mine["id"], account=ACC_A).status_code == 200

    theirs = _draft(anon, account=ACC_C, invoice_number="0001")
    assert _upload(anon, theirs["id"], account=ACC_C).status_code == 200
    assert _submit(anon, theirs["id"], account=ACC_C).status_code == 200


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


def test_upload_rejects_extension_content_mismatch(anon: TestClient):
    claim = _draft(anon)
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/documents",
        files={"file": ("forged.pdf", b"not actually a pdf", "application/pdf")},
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


def test_member_can_download_own_claim_document(anon: TestClient):
    claim = _draft(anon)
    uploaded = _upload(anon, claim["id"], PDF + b" member-download")
    assert uploaded.status_code == 200
    doc_id = uploaded.json()["id"]
    res = anon.get(
        f"/api/v1/portal/claims/{claim['id']}/documents/{doc_id}/download",
        headers=_auth(ACC_A),
    )
    assert res.status_code == 200
    assert res.content == PDF + b" member-download"


def test_decision_idempotency_key_replays_success(
    anon: TestClient, broker: TestClient
):
    claim_id = _submitted_claim(anon, b" idempotent-decision")
    headers = {"Idempotency-Key": "decision-test-0001"}
    first = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve"},
        headers=headers,
    )
    second = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve"},
        headers=headers,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "approved"

    from app.models import ClaimNotification
    from app.services.claim_notifications import process_one_claim_notification

    with SessionLocal() as db:
        queued = db.query(ClaimNotification).filter_by(claim_id=claim_id).all()
        assert len(queued) == 1
        notification_id = queued[0].id
        pending_count = db.query(ClaimNotification).filter_by(status="queued").count()
    for _ in range(pending_count):
        assert process_one_claim_notification(None) is True
        with SessionLocal() as db:
            if db.get(ClaimNotification, notification_id).status == "sent":
                break
    with SessionLocal() as db:
        delivered = db.get(ClaimNotification, notification_id)
        assert delivered.status == "sent"
        assert delivered.recipient_email == ""


def test_upload_is_open_until_the_broker_decides(
    anon: TestClient, broker: TestClient
):
    """`MEMBER_EDITABLE_STATUSES` is `DECIDABLE_STATUSES | {draft}`.

    This used to assert a 409 the moment the claim was submitted. It was
    widened with member claim editing (`docs/CLAIM_AMENDMENT_PLAN.md`): a claim
    whose amount a member may correct but whose receipt they may not replace is
    incoherent, so the field-edit window and the evidence window are ONE
    constant. Both ends are pinned here — open while the broker has still to
    decide, shut the moment they have.
    """
    claim_id = _submitted_claim(anon, b" locked-1")

    # In review, no decision yet — the member may still add evidence.
    assert _upload(anon, claim_id, PDF + b" locked-1b").status_code == 200

    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision", json={"action": "approve"}
    )
    assert res.status_code == 200, res.text

    # Decided. Shut — a 403 carrying the SERVED refusal sentence, because this
    # route now asks `member_editability` like the amend and delete routes do
    # rather than testing the status set itself. It used to be a bare 409 with
    # its own wording, which is how the three came to disagree: a claim
    # reclassified as a LOG case is refused by the other two and was still
    # accepting documents here.
    res = _upload(anon, claim_id, PDF + b" locked-1c")
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "claim_not_editable"


# ── Claim messages (the member <-> broker thread) ────────────────────────────


def _thread(anon: TestClient, claim_id: str, account: str = ACC_A) -> list[dict]:
    res = anon.get(
        f"/api/v1/portal/claims/{claim_id}/messages", headers=_auth(account)
    )
    assert res.status_code == 200, res.text
    return res.json()


def _conversations(anon: TestClient, account: str = ACC_A) -> dict:
    res = anon.get("/api/v1/portal/conversations", headers=_auth(account))
    assert res.status_code == 200, res.text
    return res.json()


def test_submit_posts_the_acknowledgement(anon: TestClient):
    claim_id = _submitted_claim(anon, b" msg-ack")

    thread = _thread(anon, claim_id)
    assert [m["event"] for m in thread] == ["submitted"]
    assert thread[0]["subject"] == "We have your claim"
    assert thread[0]["author_type"] == "system"
    assert thread[0]["unread"] is True
    assert thread[0]["mine"] is False

    # The conversation carries the SUBJECT the thread doesn't need — which is
    # what lets the member tell one of their claims from another, and what
    # sends the home tile to that claim rather than to a longer list.
    inbox = _conversations(anon)
    assert inbox["unread_total"] >= 1
    mine = next(c for c in inbox["items"] if c["subject"]["id"] == claim_id)
    assert mine["subject"]["kind"] == "claim"
    assert mine["subject"]["claim_type"] == "Group Hospital & Surgical"
    # Date and amount are what separate two claims of the SAME type.
    assert mine["subject"]["incurred_date"] and mine["subject"]["amount_claimed"]
    assert mine["last_message"]["id"] == thread[-1]["id"]
    assert mine["message_count"] == len(thread)
    assert mine["unread"] == sum(1 for m in thread if m["unread"])


def test_decision_posts_a_notice_carrying_the_note(
    anon: TestClient, broker: TestClient
):
    claim_id = _submitted_claim(anon, b" msg-decision")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 60.0, "note": "Paid in full."},
    )
    assert res.status_code == 200, res.text

    thread = _thread(anon, claim_id)
    assert [m["event"] for m in thread] == ["submitted", "approved"]
    notice = thread[-1]
    assert notice["subject"] == "Your claim is approved"
    # Written from the claim as decided — the approved figure, not the claimed
    # one — in the member's own currency convention (S$, never "SGD"), and
    # carrying the broker's note verbatim.
    assert "S$60" in notice["body"]
    assert "Paid in full." in notice["body"]


def test_member_reply_reaches_the_broker_queue(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" msg-reply")

    res = anon.post(
        f"/api/v1/portal/claims/{claim_id}/messages",
        json={"body": "  I've attached the itemised bill.  "},
        headers=_auth(ACC_A),
    )
    assert res.status_code == 201, res.text
    posted = res.json()
    assert posted["mine"] is True
    assert posted["body"] == "I've attached the itemised bill."  # trimmed on write

    # The queue surfaces it without opening the sheet.
    listing = broker.get(f"/api/v1/claims?policy_year_id={PY}").json()
    row = next(c for c in listing["items"] if c["id"] == claim_id)
    assert row["unread_member_messages"] == 1

    broker_thread = broker.get(f"/api/v1/claims/{claim_id}/messages").json()
    reply = broker_thread[-1]
    assert reply["author_type"] == "member"
    assert reply["unread"] is True and reply["mine"] is False

    assert broker.post(f"/api/v1/claims/{claim_id}/messages/read").json()["marked"] == 1
    listing = broker.get(f"/api/v1/claims?policy_year_id={PY}").json()
    row = next(c for c in listing["items"] if c["id"] == claim_id)
    assert row["unread_member_messages"] == 0


def test_member_never_sees_which_broker_wrote(anon: TestClient, broker: TestClient):
    """The trail keeps the real author; the member reads a team. A router that
    serialized the model directly would leak the staff name here."""
    claim_id = _submitted_claim(anon, b" msg-anon")
    res = broker.post(
        f"/api/v1/claims/{claim_id}/messages",
        json={"body": "We're waiting on the insurer."},
    )
    assert res.status_code == 201, res.text
    assert res.json()["author_name"] == "Demo Broker Admin"
    assert res.json()["subject"] == "A message about your claim"

    member_view = _thread(anon, claim_id)[-1]
    assert member_view["author_name"] == "Claims team"
    assert "Demo Broker Admin" not in member_view["body"]


def test_member_read_receipt_clears_their_own_count(anon: TestClient):
    claim_id = _submitted_claim(anon, b" msg-read")
    before = _conversations(anon)["unread_total"]
    assert before >= 1

    res = anon.post(
        f"/api/v1/portal/claims/{claim_id}/messages/read", headers=_auth(ACC_A)
    )
    assert res.status_code == 200 and res.json()["marked"] >= 1
    assert _conversations(anon)["unread_total"] == before - res.json()["marked"]
    assert all(not m["unread"] for m in _thread(anon, claim_id))


def test_a_conversation_is_a_thread_not_a_stream(
    anon: TestClient, broker: TestClient
):
    """The defect this list replaced, in miniature.

    Two claims of the SAME type differ only by date and amount. In the flat
    inbox they printed the same subject ("We have your claim"), the same grey
    claim-type sub-line, and the only thing telling them apart was a date
    inside a body snippet clamped to one line — a real CDL member holds two
    such pairs. As conversations they are one row each, and each row names its
    own claim.
    """
    first = _draft(anon, incurred_date="2027-06-01", amount_claimed=165.83)
    assert _upload(anon, first["id"], PDF + b" conv-a").status_code == 200
    assert _submit(anon, first["id"]).status_code == 200
    second = _draft(anon, incurred_date="2027-06-20", amount_claimed=49.25)
    assert _upload(anon, second["id"], PDF + b" conv-b").status_code == 200
    assert _submit(anon, second["id"]).status_code == 200

    # A reply on the FIRST, so its thread is both longer and more recent.
    assert broker.post(
        f"/api/v1/claims/{first['id']}/messages", json={"body": "One moment."}
    ).status_code == 201

    rows = _conversations(anon)["items"]
    by_id = {c["subject"]["id"]: c for c in rows}
    a, b = by_id[first["id"]], by_id[second["id"]]

    # Same title; told apart by the subject's own fields, which is the whole
    # reason the subject carries a date and an amount.
    assert a["subject"]["claim_type"] == b["subject"]["claim_type"]
    assert a["subject"]["incurred_date"] != b["subject"]["incurred_date"]
    assert a["subject"]["amount_claimed"] != b["subject"]["amount_claimed"]

    # ONE row per thread, not one per message.
    assert a["message_count"] == 2 and b["message_count"] == 1
    assert a["last_message"]["body"] == "One moment."
    # Whoever wrote last is who the thread is waiting on — derived, not stored.
    assert a["last_message"]["author_type"] == "broker"
    assert a["last_message"]["author_name"] == "Claims team"

    # Most recently active first.
    assert [c["subject"]["id"] for c in rows][:2] == [first["id"], second["id"]]


def test_a_message_must_belong_to_exactly_one_thread() -> None:
    """The owner invariant, enforced in `_post` because it cannot be a DB CHECK.

    `sync_firm_schema` propagates new tables and new columns to firm schemas,
    never constraints — so a CHECK would hold in `public` and nowhere else,
    which reads as enforced and is not. A message with NEITHER owner belongs to
    no thread and is invisible on every surface; one with BOTH would appear in
    two.
    """
    import pytest

    from app.db.session import SessionLocal
    from app.models import ClaimMessage
    from app.services.claim_messages import _post

    with SessionLocal() as db:
        for kwargs in (
            {},  # neither
            {"claim_id": "c", "enquiry_id": "q"},  # both
        ):
            with pytest.raises(ValueError, match="exactly one thread"):
                _post(
                    db,
                    ClaimMessage(
                        client_id="x",
                        author_type="member",
                        subject="s",
                        body="b",
                        **kwargs,
                    ),
                )
        db.rollback()


def test_the_broker_queue_is_who_is_waiting_on_us(
    anon: TestClient, broker: TestClient
):
    """The complaint this tab answers: the claims queue could only be SCROLLED
    for unread badges — `GET /claims` filters on status, employee and case type
    and nothing else, so there was no way to ask who is waiting on a reply."""
    quiet = _submitted_claim(anon, b" queue-quiet")
    waiting = _submitted_claim(anon, b" queue-waiting")
    assert anon.post(
        f"/api/v1/portal/claims/{waiting}/messages",
        json={"body": "Any news on this one?"},
        headers=_auth(ACC_A),
    ).status_code == 201

    q = broker.get(f"/api/v1/conversations?policy_year_id={PY}")
    assert q.status_code == 200, q.text
    ids = [c["subject"]["id"] for c in q.json()["items"]]
    # Only threads whose LAST word is the member's. `quiet` ends on our own
    # submission notice, so it is not work.
    assert waiting in ids and quiet not in ids
    row = next(c for c in q.json()["items"] if c["subject"]["id"] == waiting)
    assert row["last_message"]["body"] == "Any news on this one?"
    assert row["last_message"]["author_type"] == "member"
    # The broker reads it as unread-from-the-member, and `mine` is FALSE here
    # where it is true on the member's own surface — same row, opposite sense.
    assert row["unread"] == 1 and row["last_message"]["mine"] is False
    # WHO is waiting, without opening anything. This is the point of the tab.
    assert row["employee"]["staff_id"] == "CL-1"
    assert row["employee"]["employee_name"]

    # `any` is for lookup and carries the quiet thread too.
    everything = broker.get(
        f"/api/v1/conversations?policy_year_id={PY}&awaiting=any"
    ).json()
    every_id = [c["subject"]["id"] for c in everything["items"]]
    assert waiting in every_id and quiet in every_id

    # The two views sort in opposite directions, each right for its own job.
    def stamps(body):
        return [c["last_message"]["created_at"] for c in body["items"]]

    assert stamps(q.json()) == sorted(stamps(q.json()))
    assert stamps(everything) == sorted(stamps(everything), reverse=True)

    # Lookup is server-side, so it still searches the whole benefit year when
    # the inbox grows past one page. The latest message is searchable because
    # it is often the only phrase an assessor remembers.
    found = broker.get(
        f"/api/v1/conversations?policy_year_id={PY}&awaiting=any&q=Any%20news"
    )
    assert found.status_code == 200, found.text
    assert [c["subject"]["id"] for c in found.json()["items"]] == [waiting]

    missing = broker.get(
        f"/api/v1/conversations?policy_year_id={PY}&awaiting=any&q=no-such-thread"
    ).json()
    assert missing["total"] == 0 and missing["items"] == []


def test_the_broker_queues_unread_total_is_the_whole_view(
    anon: TestClient, broker: TestClient
):
    """`unread_total` means the same thing on every surface: unread messages
    across the whole VIEW, never just the page returned. It was briefly
    page-local here, which put two meanings on one shared schema field — a
    badge wired to it would then undercount silently on page 1 of many."""
    for marker in (b" ut-a", b" ut-b"):
        cid = _submitted_claim(anon, marker)
        anon.post(
            f"/api/v1/portal/claims/{cid}/messages",
            json={"body": "Checking in on this one."},
            headers=_auth(ACC_A),
        )

    whole = broker.get(f"/api/v1/conversations?policy_year_id={PY}").json()
    assert whole["unread_total"] == sum(c["unread"] for c in whole["items"])
    assert whole["unread_total"] >= 2

    one = broker.get(f"/api/v1/conversations?policy_year_id={PY}&limit=1").json()
    assert len(one["items"]) == 1
    assert one["total"] == whole["total"]
    # The page shrank; the figure describing the view did not.
    assert one["unread_total"] == whole["unread_total"]


def test_the_broker_queue_names_the_real_author(
    anon: TestClient, broker: TestClient
):
    """A broker surface shows who actually replied; only the member's side is
    substituted with the team label. The two serializers are the reason."""
    claim_id = _submitted_claim(anon, b" queue-author")
    broker.post(f"/api/v1/claims/{claim_id}/messages", json={"body": "Looking."})
    anon.post(
        f"/api/v1/portal/claims/{claim_id}/messages",
        json={"body": "Thanks."},
        headers=_auth(ACC_A),
    )
    row = next(
        c
        for c in broker.get(
            f"/api/v1/conversations?policy_year_id={PY}&awaiting=any"
        ).json()["items"]
        if c["subject"]["id"] == claim_id
    )
    assert row["message_count"] == 3  # submitted notice + ours + theirs
    thread = broker.get(f"/api/v1/claims/{claim_id}/messages").json()
    assert any(m["author_name"] == "Demo Broker Admin" for m in thread)


def test_a_members_conversation_never_names_the_employee(anon: TestClient):
    """`employee` is broker-only furniture. The member's own list must not
    carry it — naming them to themselves is noise, and the field existing on
    their payload is how it ends up rendered on the wrong surface."""
    _submitted_claim(anon, b" no-employee")
    for c in _conversations(anon)["items"]:
        assert c["employee"] is None


def test_messages_are_member_scoped(anon: TestClient):
    """Carol may not read or write on Alice's claim — 404, not 403, so the
    portal can't be used to discover whose claims exist."""
    claim_id = _submitted_claim(anon, b" msg-isolation")
    assert (
        anon.get(
            f"/api/v1/portal/claims/{claim_id}/messages", headers=_auth(ACC_C)
        ).status_code
        == 404
    )
    assert (
        anon.post(
            f"/api/v1/portal/claims/{claim_id}/messages",
            json={"body": "hello"},
            headers=_auth(ACC_C),
        ).status_code
        == 404
    )
    assert all(
        c["subject"]["id"] != claim_id for c in _conversations(anon, ACC_C)["items"]
    )


def test_reply_on_a_draft_409_and_blank_body_422(anon: TestClient):
    draft = _draft(anon)
    res = anon.post(
        f"/api/v1/portal/claims/{draft['id']}/messages",
        json={"body": "anyone there?"},
        headers=_auth(ACC_A),
    )
    assert res.status_code == 409

    claim_id = _submitted_claim(anon, b" msg-blank")
    assert (
        anon.post(
            f"/api/v1/portal/claims/{claim_id}/messages",
            json={"body": "   "},
            headers=_auth(ACC_A),
        ).status_code
        == 422
    )


def test_preview_messages_match_the_member(anon: TestClient, broker: TestClient):
    claim_id = _submitted_claim(anon, b" msg-preview")
    broker.post(
        f"/api/v1/claims/{claim_id}/messages", json={"body": "Preview parity."}
    )

    member_thread = _thread(anon, claim_id)
    preview = broker.get(
        f"/api/v1/employees/{EMP_A}/portal-preview/claims/{claim_id}/messages"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json() == member_thread

    inbox = broker.get(f"/api/v1/employees/{EMP_A}/portal-preview/conversations")
    assert inbox.status_code == 200
    mine = next(
        c for c in inbox.json()["items"] if c["subject"]["id"] == claim_id
    )
    # A conversation carries only the LAST word; the whole thread is on the
    # claim, which the preview now drills into.
    assert mine["last_message"]["id"] == member_thread[-1]["id"]
    assert mine["message_count"] == len(member_thread)

    # Another member's claim is not reachable through this employee's preview.
    assert (
        broker.get(
            f"/api/v1/employees/{EMP_C}/portal-preview/claims/{claim_id}/messages"
        ).status_code
        == 404
    )


def test_a_duplicate_invoice_has_no_member_side_override(anon: TestClient):
    """A HARD refusal: the portal will not take a second submission of a number
    it already holds, and there is no acknowledge escape. The cases where two
    legitimate claims share a number — one family receipt covering the member
    AND their child — are recorded broker-side as LOG cases, which never reach
    this path."""
    first = _draft(anon, invoice_number="FAM-1")
    assert _upload(anon, first["id"]).status_code == 200
    assert _submit(anon, first["id"]).status_code == 200

    # Same number, different claimant: still refused.
    second = _draft(anon, invoice_number="FAM-1", dependant_id=DEP_A)
    assert _upload(anon, second["id"]).status_code == 200
    assert _submit(anon, second["id"]).status_code == 409

    # No body, flag or spelling gets past it.
    for body in ({"acknowledge_duplicate": True}, {"acknowledge": True}, {}):
        res = anon.post(
            f"/api/v1/portal/claims/{second['id']}/submit",
            json=body,
            headers=_auth(ACC_A),
        )
        assert res.status_code == 409, f"{body} got past the block"
        assert res.json()["detail"]["code"] == "duplicate_invoice_number"

    # The member must be TOLD, and told which invoice. `detail.message` is what
    # reaches the screen verbatim: the portal fetch wrapper promotes a coded 409
    # to `ConflictDetailError(message)`, `formatError` returns that message, and
    # the claim form renders it in its `FormAlert`. A message that didn't name
    # the number would leave them re-reading a form with nothing marked wrong.
    detail = res.json()["detail"]
    assert "FAM-1" in detail["message"]
    assert "already been claimed" in detail["message"]
    assert detail["conflicting_claim_ids"] == [first["id"]]
