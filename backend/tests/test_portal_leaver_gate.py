"""The leaver gate, through the HTTP surface.

`test_member_access.py` pins the rule; this pins that every portal endpoint is
actually WIRED to it — which is the half that shipped missing (a leaver kept
their panel e-card, their claim form and their enrolment window indefinitely).

One member is moved through `active → run_off → settling → ended` by editing
their roster row, and each state asserts what the surface answers.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.auth import DEMO_CLIENT_ID
from app.core.portal_auth import issue_member_token
from app.db.session import SessionLocal
from app.main import app
from app.models import Claim, Client, Employee, MemberAccount, PolicyYear
from app.models.claim import CLAIM_STATUS_DRAFT, CLAIM_STATUS_NEEDS_INFO
from app.models.employee import (
    EMPLOYEE_STATUS_ACTIVE,
    EMPLOYEE_STATUS_TERMINATED,
)
from app.models.policy_year import PolicyYearStatus
from app.services.member_access import CODE_ACCESS_ENDED, CODE_COVERAGE_ENDED
from scripts.seed_demo import seed

PY = "00000000-0000-0000-0000-0000000lv001"
EMP = "00000000-0000-0000-0000-0000000lv002"
ACC = "00000000-0000-0000-0000-0000000lv003"
# The portal resolves its tenant from the subdomain, stood in for by
# X-Inspro-Tenant-Slug here — the sign-in endpoints go through
# `require_portal_tenant` and need a real slug on the client.
SLUG = "leaver-co"

TODAY = date.today()
# A year that contains today, so "the member is still covered" is the baseline
# and only the roster row decides otherwise.
YEAR_START = TODAY - timedelta(days=120)
YEAR_END = TODAY + timedelta(days=245)


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    seed()
    with SessionLocal() as s:
        s.get(Client, DEMO_CLIENT_ID).slug = SLUG
        s.add(
            PolicyYear(
                id=PY, client_id=DEMO_CLIENT_ID, year=YEAR_START.year,
                start_date=YEAR_START, end_date=YEAR_END,
                status=PolicyYearStatus.active,
            )
        )
        s.flush()
        s.add(
            MemberAccount(
                id=ACC, client_id=DEMO_CLIENT_ID, email="leaver@lv.test",
                staff_id="LV-1", status="active",
            )
        )
        s.flush()
        s.add(
            Employee(
                id=EMP, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="LV-1", employee_name="Lee", member_account_id=ACC,
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status=EMPLOYEE_STATUS_ACTIVE,
            )
        )
        s.commit()
    yield


@pytest.fixture
def client() -> TestClient:
    token, _ = issue_member_token(ACC, DEMO_CLIENT_ID)
    c = TestClient(app)
    c.headers.update(
        {"Authorization": f"Bearer {token}", "X-Inspro-Tenant-Slug": SLUG}
    )
    return c


def _set_state(*, status: str, last_day: date | None, days: int | None = None) -> None:
    with SessionLocal() as s:
        emp = s.get(Employee, EMP)
        emp.status = status
        emp.terminated_effective = last_day
        s.get(PolicyYear, PY).leaver_access_days = days
        s.commit()


def _claim(status: str) -> str:
    with SessionLocal() as s:
        claim = Claim(
            client_id=DEMO_CLIENT_ID, policy_year_id=PY, employee_id=EMP,
            claim_kind="insured", product_code="GHS", claim_type="outpatient",
            incurred_date=YEAR_START + timedelta(days=10),
            amount_claimed=80.0, currency="SGD", status=status,
        )
        s.add(claim)
        s.commit()
        return claim.id


@pytest.fixture(autouse=True)
def _reset():
    yield
    with SessionLocal() as s:
        s.query(Claim).delete()
        s.commit()
    _set_state(status=EMPLOYEE_STATUS_ACTIVE, last_day=None)


def _code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


# The endpoints that carry an entitlement a third party acts on, or that change
# next year's cover. These are the ones a leaver kept.
GATED_ON_COVER = [
    "/api/v1/portal/cards",
    "/api/v1/portal/clinics",
    "/api/v1/portal/enrollment",
]
READABLE_RECORD = [
    "/api/v1/portal/benefit-statement",
    "/api/v1/portal/utilization",
    "/api/v1/portal/claims",
    "/api/v1/portal/dependants",
]


# ── Active ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", GATED_ON_COVER + READABLE_RECORD)
def test_an_active_member_is_refused_nothing(client: TestClient, path: str):
    assert client.get(path).status_code != 403


# ── Run-off ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", GATED_ON_COVER)
def test_run_off_closes_the_card_the_clinic_list_and_enrolment(
    client: TestClient, path: str
):
    """The panel e-card is the one with a counterparty: a clinic accepts it as
    proof of entitlement and bills the employer's panel against it."""
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=1))
    res = client.get(path)
    assert res.status_code == 403
    # Not `access_ended`: they are still signed in and can still use the rest of
    # the portal, so the client must not end the session on this.
    assert _code(res) == CODE_COVERAGE_ENDED


def test_run_off_keeps_the_record_and_the_claim_form(client: TestClient):
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=1))
    for path in READABLE_RECORD:
        assert client.get(path).status_code != 403, path
    assert client.get("/api/v1/portal/coverage-options").status_code != 403


def test_card_artwork_is_gated_as_well_as_the_card_list(client: TestClient):
    """A leaver who bookmarked the artwork URL must not keep the image the card
    is made of — gating only the list would leave the picture reachable."""
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=1))
    res = client.get("/api/v1/portal/cards/any-card/artwork/front")
    assert res.status_code == 403 and _code(res) == CODE_COVERAGE_ENDED


def test_a_member_on_notice_keeps_everything(client: TestClient):
    """Terminated with a FUTURE last day is someone working out their notice —
    still covered, still entitled to walk into a panel clinic."""
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY + timedelta(days=10))
    for path in GATED_ON_COVER:
        assert client.get(path).status_code != 403, path


# ── Settling ──────────────────────────────────────────────────────────────────


def test_settling_can_answer_an_open_claim_but_cannot_start_a_new_one(
    client: TestClient,
):
    """The whole reason `settling` exists. A `needs_info` claim is answered by
    replying and resubmitting; if those needed CLAIM, a member past their
    run-off could read the question and never answer it."""
    claim_id = _claim(CLAIM_STATUS_NEEDS_INFO)
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=30), days=10,
    )

    assert client.get("/api/v1/portal/claims").status_code != 403
    reply = client.post(
        f"/api/v1/portal/claims/{claim_id}/messages", json={"body": "Attached."}
    )
    assert reply.status_code != 403

    # Starting something new is refused — the cover it would be claimed against
    # ended a month ago.
    refused = client.get("/api/v1/portal/coverage-options")
    assert refused.status_code == 403 and _code(refused) == CODE_COVERAGE_ENDED


def test_settling_cannot_submit_a_stale_DRAFT(client: TestClient):
    """A submit is two acts down one route. Answering a `needs_info` is
    RESPOND; finishing a draft is starting a new claim, so it needs CLAIM as
    well — which is what stops a leaver filing from a draft they left behind."""
    _claim(CLAIM_STATUS_NEEDS_INFO)  # holds them in `settling`
    draft = _claim(CLAIM_STATUS_DRAFT)
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=30), days=10,
    )
    res = client.post(f"/api/v1/portal/claims/{draft}/submit")
    assert res.status_code == 403 and _code(res) == CODE_COVERAGE_ENDED


def test_settling_cannot_add_documents_to_a_stale_DRAFT(client: TestClient):
    """The upload takes its capability from the ROW, exactly as submit does.

    `MEMBER_EDITABLE_STATUSES` includes `draft`, so gating the route on RESPOND
    alone — correct for the `needs_info` case it was written for — let a member
    past their run-off pile documents onto a draft they can neither submit
    (CLAIM) nor delete (CLAIM), against cover that ended months ago.
    """
    _claim(CLAIM_STATUS_NEEDS_INFO)  # holds them in `settling`
    draft = _claim(CLAIM_STATUS_DRAFT)
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=30), days=10,
    )
    res = client.post(
        f"/api/v1/portal/claims/{draft}/documents",
        files={"file": ("invoice.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert res.status_code == 403 and _code(res) == CODE_COVERAGE_ENDED


def test_without_a_live_claim_the_same_member_is_finished(client: TestClient):
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=30), days=10,
    )
    res = client.get("/api/v1/portal/claims")
    assert res.status_code == 403 and _code(res) == CODE_ACCESS_ENDED


# ── Ended ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", GATED_ON_COVER + READABLE_RECORD)
def test_ended_refuses_every_data_endpoint_with_a_terminal_code(
    client: TestClient, path: str
):
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    res = client.get(path)
    assert res.status_code == 403
    # Terminal: the client ends the session on this one, the way it does on 401.
    assert _code(res) == CODE_ACCESS_ENDED


def test_me_answers_for_a_member_whose_access_has_ended(client: TestClient):
    """`/portal/me` is the endpoint that has to TELL them, so it cannot be one
    of the things their access gates. Without this the shell would 403 on the
    only call it makes before rendering, and a finished member would see a
    broken app instead of an explanation."""
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    res = client.get("/api/v1/portal/me")
    assert res.status_code == 200
    assert res.json()["member"]["staff_id"] == "LV-1"


def test_a_refusal_is_403_and_never_404(client: TestClient):
    """404 is the tenant-scoping convention — "this does not exist". A leaver's
    record does exist; saying otherwise would send them to their broker to
    report missing data."""
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    res = client.get("/api/v1/portal/benefit-statement")
    assert res.status_code == 403
    detail = res.json()["detail"]
    assert detail["state"] == "ended"
    assert detail["access_ends_on"] is not None
    assert detail["message"]


# ── The broker preview is NOT gated ───────────────────────────────────────────


@pytest.fixture
def broker() -> TestClient:
    from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )
    try:
        yield TestClient(app, headers={"X-Inspro-Client": DEMO_CLIENT_ID})
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    "path", ["", "/benefit-statement", "/utilization", "/claims"]
)
def test_the_employee_view_preview_still_opens_a_leavers_record(
    broker: TestClient, path: str
):
    """The preview resolves through `load_employee` (broker access), never
    `resolve_member_employee`, so the leaver gate does not apply to it — and it
    must not. A broker settling a leaver's last claim has to be able to read
    what that member's screens said. Parity with the member is the banner the
    preview renders, not a refusal."""
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    res = broker.get(f"/api/v1/employees/{EMP}/portal-preview{path}")
    assert res.status_code != 403, res.text


# ── The claim window closes with the cover ────────────────────────────────────


def _window(kind: str = "insured"):
    from app.services.claims import claim_period_window

    with SessionLocal() as s:
        return claim_period_window(
            s, s.get(PolicyYear, PY), kind, s.get(Employee, EMP)
        )


def test_a_leavers_claim_window_ends_on_their_last_day():
    """Cover ending is a fact about the COVER, not about the portal — which is
    why it lives in `claim_period_window` and not in the access gate. A broker
    recording a LOG case for the same leaver goes through the same function."""
    last = TODAY - timedelta(days=10)
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=last)
    w = _window()
    assert w.start == YEAR_START
    assert w.end == last
    assert w.label == "cover period"
    # The PERIOD's own end is carried separately and is untouched.
    assert w.period_end == YEAR_END and w.period_label == "policy year"


def test_an_active_member_with_a_stale_last_day_is_not_clamped():
    """`Last Day of Service` round-trips on every listing sync, so an active row
    can carry a date nobody cleared. Reading it here would start refusing a live
    employee's claims — hence `cover_end`, not `resolved_last_day`."""
    with SessionLocal() as s:
        emp = s.get(Employee, EMP)
        emp.status = EMPLOYEE_STATUS_ACTIVE
        emp.terminated_effective = None
        emp.attribute_values = {"last_day_of_service": str(TODAY - timedelta(days=5))}
        s.commit()
    w = _window()
    assert w.end == YEAR_END and w.label == "policy year"
    with SessionLocal() as s:
        s.get(Employee, EMP).attribute_values = {}
        s.commit()


def test_a_last_day_after_the_period_does_not_EXTEND_the_window():
    """Someone still on the books when the year closes is bounded by the year,
    like everyone else — the clamp only ever narrows."""
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED, last_day=YEAR_END + timedelta(days=30)
    )
    assert _window().end == YEAR_END


def test_the_incurred_check_refuses_a_date_after_cover_ended():
    from fastapi import HTTPException

    from app.services.claims import assert_incurred_in_period

    last = TODAY - timedelta(days=10)
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=last)
    claim_id = _claim(CLAIM_STATUS_DRAFT)
    with SessionLocal() as s:
        claim = s.get(Claim, claim_id)
        claim.incurred_date = last + timedelta(days=1)
        year, emp = s.get(PolicyYear, PY), s.get(Employee, EMP)
        with pytest.raises(HTTPException) as exc:
            assert_incurred_in_period(s, year, claim, emp)
        assert exc.value.status_code == 422
        assert "cover period" in exc.value.detail
        # The day itself is still covered.
        claim.incurred_date = last
        assert assert_incurred_in_period(s, year, claim, emp).end == last


def test_the_submission_grace_period_still_anchors_on_the_YEAR():
    """Two different bounds. How long a claim may be SENT IN for belongs to the
    year; how long a member was COVERED is their own, with its own control
    (`leaver_access_days`). Anchoring grace on the member would apply the leaver
    bound twice and could close their filing window before the run-off expires."""
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=10)
    )
    w = _window()
    assert w.end < w.period_end
    assert w.period_end == YEAR_END


def test_coverage_options_serves_the_window_the_form_must_obey(client: TestClient):
    """SERVED, so the form states the bound up front instead of surfacing a 422
    after the member has filled the whole thing in."""
    last = TODAY - timedelta(days=3)
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=last)
    res = client.get("/api/v1/portal/coverage-options")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["claimable_from"] == YEAR_START.isoformat()
    assert body["claimable_to"] == last.isoformat()
    # The policy year is still reported unchanged beside it — they answer
    # different questions.
    assert body["policy_year_end"] == YEAR_END.isoformat()
    assert body["claim_block"] is None


def test_a_window_that_refuses_every_date_is_served_as_NO_window(
    client: TestClient,
):
    """Cover that ended BEFORE the period began, while the member still holds
    CLAIM — reachable, and not rare: a leaver inside a generous run-off, or a
    flex scheme that starts mid-year.

    The bounds cross over, and served verbatim they reach the date input as
    `min > max`: the form then refuses every date the member tries with "pick a
    date between 15 Jul and 1 Jun", and the clean 422 the backend would have
    raised is unreachable behind its own client-side validation. So the window
    is served as absent, its options withheld, and `claim_block` says why.
    """
    before_the_year = YEAR_START - timedelta(days=5)
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED, last_day=before_the_year, days=400
    )
    res = client.get("/api/v1/portal/coverage-options")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["claimable_from"] is None and body["claimable_to"] is None
    assert body["insured"] == []
    assert body["flex"] is None
    assert before_the_year.isoformat() in body["claim_block"]
    assert "nothing to claim against" in body["claim_block"]


# ── What the client is told ───────────────────────────────────────────────────


def test_me_serves_the_capability_LIST_not_just_a_state(client: TestClient):
    """The whole contract. A client that switched on `state` would be holding a
    copy of `member_access`'s table, and would go on showing the panel-card tab
    the day that table changes — the same drift class as mirroring the
    pending-claim status set into TypeScript."""
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=1))
    access = client.get("/api/v1/portal/me").json()["access"]
    assert access["state"] == "run_off"
    assert set(access["capabilities"]) == {"record", "respond", "claim"}
    assert access["last_day"] == (TODAY - timedelta(days=1)).isoformat()
    assert access["access_ends_on"] is not None


def test_an_active_member_is_told_nothing_to_print(client: TestClient):
    access = client.get("/api/v1/portal/me").json()["access"]
    assert access["state"] == "active"
    assert len(access["capabilities"]) == 5
    # No dates: a member who has not left has no last day and no bound, and a
    # date here would be printed at them.
    assert access["last_day"] is None and access["access_ends_on"] is None


def test_the_broker_preview_serves_the_SAME_access_block(broker: TestClient):
    """"The preview shows exactly what the member sees" has to include the
    reason half their screens are gone."""
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=1))
    token, _ = issue_member_token(ACC, DEMO_CLIENT_ID)
    member = TestClient(app, headers={
        "Authorization": f"Bearer {token}", "X-Inspro-Tenant-Slug": SLUG,
    })
    assert (
        broker.get(f"/api/v1/employees/{EMP}/portal-preview").json()["access"]
        == member.get("/api/v1/portal/me").json()["access"]
    )


# ── Sign-in ───────────────────────────────────────────────────────────────────


def _password_account(password: str = "Correct-Horse-9!") -> None:
    from app.core import passwords as PW

    with SessionLocal() as s:
        acc = s.get(MemberAccount, ACC)
        acc.password_hash = PW.hash_password(password)
        acc.must_rotate_after = None
        acc.invite_expires_at = None
        acc.failed_attempts = 0
        s.commit()


def test_a_finished_member_cannot_sign_in_at_all():
    """Checked at `_issue_member_login`, the ONE choke point every session on
    this surface passes through — password login, OTP verify, MFA and
    set-password all end there, so the refusal is written once instead of at
    four call sites that could drift."""
    _password_account()
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    res = TestClient(app).post(
        "/api/v1/portal/auth/login",
        json={"identifier": "leaver@lv.test", "password": "Correct-Horse-9!"},
        headers={"X-Inspro-Tenant-Slug": SLUG},
    )
    assert res.status_code == 403
    detail = res.json()["detail"]
    assert detail["code"] == CODE_ACCESS_ENDED
    assert "ended" in detail["message"]


def test_the_refusal_runs_AFTER_the_password_is_proved():
    """It must not become an oracle. A wrong password on a finished account has
    to look exactly like a wrong password on any other — 401, not 403."""
    _password_account()
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    res = TestClient(app).post(
        "/api/v1/portal/auth/login",
        json={"identifier": "leaver@lv.test", "password": "wrong-password"},
        headers={"X-Inspro-Tenant-Slug": SLUG},
    )
    assert res.status_code == 401


def test_a_member_in_run_off_still_signs_in():
    """Run-off is the state that exists so they CAN — to send in the claims they
    incurred while covered."""
    _password_account()
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=1))
    res = TestClient(app).post(
        "/api/v1/portal/auth/login",
        json={"identifier": "leaver@lv.test", "password": "Correct-Horse-9!"},
        headers={"X-Inspro-Tenant-Slug": SLUG},
    )
    assert res.status_code == 200 and res.json()["token"]


# ── What the BROKER is told ───────────────────────────────────────────────────


def test_the_account_list_reports_the_derived_access_state(broker: TestClient):
    """`status` is the broker's manual switch; `access_state` is derived from
    the roster row and moves on its own. Both are needed — this account is
    `active` and lets nobody in, which is exactly the pair that printed
    "Active" beside a member who was locked out."""
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    rows = broker.get("/api/v1/member-accounts").json()["items"]
    row = next(r for r in rows if r["id"] == ACC)
    assert row["status"] == "active"
    assert row["access_state"] == "ended"
    assert row["access_ends_on"] == (
        TODAY - timedelta(days=390)
    ).isoformat()


def test_a_member_in_run_off_reads_as_winding_down_not_as_finished(
    broker: TestClient,
):
    _set_state(status=EMPLOYEE_STATUS_TERMINATED, last_day=TODAY - timedelta(days=1))
    rows = broker.get("/api/v1/member-accounts").json()["items"]
    assert next(r for r in rows if r["id"] == ACC)["access_state"] == "run_off"


# ── Fixes from the review ─────────────────────────────────────────────────────


def test_a_refused_sign_in_leaves_nothing_behind():
    """The 403 must not commit the caller's pending work.

    `member_set_password` reaches the gate having already written a new
    `password_hash`, `password_updated_at`, `must_rotate_after` and a cleared
    invite deadline. Committing on the way out silently rotated the credential
    of a member being told they cannot come in — and cleared the deadline that
    bounds an unused invite."""
    from app.core import passwords as PW
    from app.core.portal_auth import issue_member_set_password_token

    _password_account("Old-Password-1!")
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    with SessionLocal() as s:
        acc = s.get(MemberAccount, ACC)
        before = acc.password_hash
        acc.must_rotate_after = None
        s.commit()
        token = issue_member_set_password_token(ACC, 0)

    res = TestClient(app).post(
        "/api/v1/portal/auth/set-password",
        json={"token": token, "password": "Brand-New-Password-9!"},
        headers={"X-Inspro-Tenant-Slug": SLUG},
    )
    assert res.status_code == 403
    with SessionLocal() as s:
        acc = s.get(MemberAccount, ACC)
        assert acc.password_hash == before, "the refused password was written"
        assert PW.verify_password(acc.password_hash, "Old-Password-1!")


def test_a_cover_period_that_ended_before_the_window_says_so():
    """A leaver whose last day precedes the period start has NO claimable
    window. The range refuses every date on its own, but a caller PRINTING it
    would ask for "a date between 15 July and 1 June"."""
    from fastapi import HTTPException

    from app.services.claims import assert_incurred_in_period, claim_period_window

    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=YEAR_START - timedelta(days=5),
    )
    claim_id = _claim(CLAIM_STATUS_DRAFT)
    with SessionLocal() as s:
        year, emp = s.get(PolicyYear, PY), s.get(Employee, EMP)
        window = claim_period_window(s, year, "insured", emp)
        assert window.is_empty
        with pytest.raises(HTTPException) as exc:
            assert_incurred_in_period(s, year, s.get(Claim, claim_id), emp)
        assert exc.value.status_code == 422
        assert "before this policy year began" in exc.value.detail


def test_the_batched_map_refuses_an_ambiguous_staff_id_like_the_single_path():
    """`_locate` deliberately returns nothing when two unclaimed rows share a
    staff id — ambiguity is `resolve_member_employee`'s 409 to raise. A
    first-one-wins in the batch would report a broker an access state derived
    from a row the member's own gate refuses to pick."""
    from app.services.member_access import access_map

    with SessionLocal() as s:
        for suffix in ("a", "b"):
            s.add(
                Employee(
                    id=f"amb-{suffix}", client_id=DEMO_CLIENT_ID,
                    policy_year_id=PY, staff_id="AMBIG-1",
                    status=EMPLOYEE_STATUS_TERMINATED,
                    terminated_effective=TODAY - timedelta(days=400),
                    attribute_values={},
                )
            )
        s.commit()

    class _Ref:
        id = "acc-ambiguous"
        staff_id = "AMBIG-1"

    with SessionLocal() as s:
        assert access_map(s, DEMO_CLIENT_ID, [_Ref])[_Ref.id].state == "unknown"
        s.query(Employee).filter(Employee.staff_id == "AMBIG-1").delete()
        s.commit()


# ── The emailed-code route is the same door ───────────────────────────────────


def test_every_route_that_mints_a_member_session_resolves_the_tenant():
    """`require_portal_tenant` is not just tenant SELECTION on these routes.

    It calls `set_search_path`, and `_issue_member_login` reads `policy_years`,
    `employees` and `claims` — all tenant tables. Unrouted on Postgres every one
    of them resolves against `public`, which holds no tenant rows: the leaver
    check comes back `unknown` and the route signs in the members the password
    route refuses. `/verify` was missing it because until the leaver gate these
    routes touched only control tables, which live in `public` everywhere, so
    nothing noticed.

    Enumerated rather than listed, so a NEW way to mint a session is caught too.
    The routing itself is unobservable here (SQLite has no schemas) and is
    covered by `tests/test_schema_isolation_pg.py`.
    """
    import inspect

    from app.api.v1 import portal_auth as mod

    minting = [
        fn
        for name, fn in vars(mod).items()
        if inspect.isfunction(fn)
        and inspect.getmodule(fn) is mod
        and name != "_issue_member_login"
        and "_issue_member_login" in inspect.getsource(fn)
    ]
    assert minting, "found no session-minting routes — this scan has rotted"
    missing = [
        fn.__name__
        for fn in minting
        if "require_portal_tenant" not in inspect.getsource(fn)
    ]
    assert not missing, (
        "routes that mint a member session without resolving the portal "
        f"tenant (so the leaver check reads the wrong schema): {missing}"
    )


def test_the_emailed_code_route_refuses_a_request_with_no_tenant():
    """The behavioural half of the rule above."""
    res = TestClient(app).post(
        "/api/v1/portal/auth/verify",
        json={"email": "leaver@lv.test", "code": "123456"},
    )
    assert res.status_code == 400


def test_a_refused_sign_in_still_SPENDS_the_emailed_code():
    """A code is spent when it MATCHES, not when the sign-in succeeds.

    `_issue_member_login` rolls back on a leaver refusal — it has to, because
    `member_set_password` reaches it holding a freshly written credential — and
    that rollback was reverting the consumption too. A correctly-guessed code
    stayed live for the rest of its TTL, so the refusal was replayable and, if
    the member's access state moved inside the window, the already-used code
    would mint a session.
    """
    from app.models import MemberOtpCode

    _password_account()
    _set_state(
        status=EMPLOYEE_STATUS_TERMINATED,
        last_day=TODAY - timedelta(days=400), days=10,
    )
    api = TestClient(app)
    try:
        issued = api.post(
            "/api/v1/portal/auth/request-code", json={"email": "leaver@lv.test"}
        )
        assert issued.status_code == 202, issued.text
        code = issued.json().get("debug_code")
        assert code, "dev+mock surfaces the code so local sign-in works"

        def _verify():
            return api.post(
                "/api/v1/portal/auth/verify",
                json={"email": "leaver@lv.test", "code": code},
                headers={"X-Inspro-Tenant-Slug": SLUG},
            )

        refused = _verify()
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == CODE_ACCESS_ENDED

        # 401 (no live code matches), NOT another 403 — a second 403 would mean
        # the code was still there to be matched.
        assert _verify().status_code == 401
    finally:
        with SessionLocal() as s:
            s.query(MemberOtpCode).delete()
            s.commit()
