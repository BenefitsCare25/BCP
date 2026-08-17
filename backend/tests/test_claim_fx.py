"""Foreign-currency claims: the rate lookup, the conversion, and the two gates.

The suite runs with `INSPRO_FX_ENABLED=0` (see conftest) so no test anywhere
reaches the real Frankfurter. This module turns it back on against a stubbed
`httpx.get`, which is also how it exercises the failure modes — an outage, a
malformed body, a currency the upstream does not carry — that a live API cannot
be asked to reproduce on demand.
"""
from __future__ import annotations

import itertools
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_claim_fx.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

import httpx  # noqa: E402
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
from app.models import Claim, Employee, FxRate, MemberAccount, PolicyYear  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.schemas.api import (  # noqa: E402
    BenefitStatementOut,
    CoverageLine,
    FlexBenefitCategoryLine,
    FlexCoverageLine,
    StatementEmployee,
)
from app.services import fx  # noqa: E402
from app.services.claim_fx import (  # noqa: E402
    FX_SOURCE_BROKER,
    FX_STATE_CONVERTED,
    FX_STATE_NOT_REQUIRED,
    FX_STATE_UNAVAILABLE,
    apply_conversion,
    build_quote,
    fx_state,
    policy_amount,
)
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-0000000000f1"
EMP = "00000000-0000-0000-0000-0000000000f2"
ACC = "00000000-0000-0000-0000-0000000000f3"

PDF = b"%PDF-1.4 fx receipt"
INCURRED = date(2027, 6, 15)
# 1 USD = 1.35 SGD throughout, so every expected figure in here is the claimed
# amount times 1.35 and can be checked by eye.
RATE = 1.35


def _statement_for(employee: Employee) -> BenefitStatementOut:
    return BenefitStatementOut(
        employee=StatementEmployee(
            id=employee.id,
            staff_id=employee.staff_id,
            employee_name=employee.employee_name,
        ),
        policy_year_id=employee.policy_year_id,
        is_matched=True,
        coverage=[
            CoverageLine(
                product_code="GHS",
                product_name="Group Hospital & Surgical",
                plan_code="P1",
                # The number at the heart of the bug this module exists for: a
                # USD 500 bill is SGD 675, which does NOT fit under 600.
                annual_policy_limit="S$600",
                benefit_schedule={"items": []},
                covers_dependants=False,
                covered_dependants=[],
            )
        ],
        dependants=[],
        flex=FlexCoverageLine(
            tier_name="Tier 1",
            wallet_amount=1000.0,
            currency="SGD",
            benefit_categories=[
                FlexBenefitCategoryLine(name="Dental", claimable=True, sub_limit=500.0)
            ],
        ),
    )


# ── Stub upstream ────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> object:
        return self._payload


def _ok(served: date = INCURRED, rate: float = RATE) -> _Resp:
    return _Resp({"amount": 1.0, "base": "USD", "date": served.isoformat(),
                  "rates": {"SGD": rate}})


@pytest.fixture
def upstream(monkeypatch):
    """Replaces the network call and records every request made.

    Returns a small controller: `.queue` is the list of responses to serve (the
    last one repeats), `.calls` counts attempts. Counting is the point — the
    retry budget and the cache are both claims about how many times we call
    out, and neither is observable any other way.
    """

    class Controller:
        def __init__(self) -> None:
            self.queue: list[object] = [_ok()]
            self.calls = 0

        def _get(self, url, params=None, timeout=None, follow_redirects=False):
            self.calls += 1
            item = self.queue[min(self.calls - 1, len(self.queue) - 1)]
            if isinstance(item, Exception):
                raise item
            return item

    ctl = Controller()
    monkeypatch.setattr(httpx, "get", ctl._get)
    # The module's policy year is 2027, so every claim date in here is in the
    # future by the real clock — and `quote` deliberately refuses to price the
    # future, clamping the request to today. Pin the business day inside the
    # year so the dates under test are the dates actually asked for.
    monkeypatch.setattr(fx, "business_today", lambda: date(2027, 6, 20))
    monkeypatch.setenv("INSPRO_FX_ENABLED", "1")
    clear_settings_cache()
    fx.reset_breaker()
    # Nothing sleeps in a test: the retry backoff is real seconds and three
    # attempts would add ~0.7s to every failure case.
    monkeypatch.setattr(fx.time, "sleep", lambda _s: None)
    with SessionLocal() as s:
        s.query(FxRate).delete()
        s.commit()
    yield ctl
    monkeypatch.delenv("INSPRO_FX_ENABLED", raising=False)
    clear_settings_cache()
    fx.reset_breaker()


@pytest.fixture(scope="module", autouse=True)
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    os.environ["INSPRO_STORAGE_DIR"] = str(tmp_path_factory.mktemp("fx_storage"))
    clear_settings_cache()

    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY, client_id=DEMO_CLIENT_ID, year=2027,
                start_date=date(2027, 4, 1), end_date=date(2028, 3, 31),
                status=PolicyYearStatus.active,
            )
        )
        session.flush()
        session.add(
            MemberAccount(
                id=ACC, client_id=DEMO_CLIENT_ID, email="fx@cl.test",
                staff_id="FX-1", status="active",
            )
        )
        session.add(
            Employee(
                id=EMP, client_id=DEMO_CLIENT_ID, policy_year_id=PY,
                staff_id="FX-1", employee_name="Farah", member_account_id=ACC,
                attribute_values={}, derived_attribute_values={},
                source="csv_import", status="active",
            )
        )
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(Claim).delete()
        session.query(FxRate).delete()
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
    from app.api.v1 import portal_claims
    from app.services import claims as claims_service
    from app.services import utilization as utilization_service

    for module in (claims_service, portal_claims, utilization_service):
        monkeypatch.setattr(
            module, "build_member_statement", lambda db, emp: _statement_for(emp)
        )


@pytest.fixture(autouse=True)
def _one_member_one_claim():
    """A clean claim ledger per test.

    The database is module-scoped, and both utilization and the approve guard
    read EVERY live claim the member holds — so without this, the SGD 600 limit
    these tests turn on is silently eaten by the claims earlier tests filed, and
    the failures land in whichever test happens to run last.
    """
    yield
    with SessionLocal() as s:
        s.query(Claim).delete()
        s.commit()


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


def _auth() -> dict[str, str]:
    token, _ = issue_member_token(ACC, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


_invoice_seq = itertools.count(1)


def _draft(anon: TestClient, **overrides) -> dict:
    body = {
        "claim_kind": "insured",
        "product_code": "GHS",
        "claim_type": "Group Hospital & Surgical",
        "sub_type": "Emergency Accidental Outpatient Treatment",
        "incurred_date": INCURRED.isoformat(),
        "provider_name": "Bangkok Hospital",
        "invoice_number": f"FX-{next(_invoice_seq):05d}",
        "diagnosis": "Dengue fever",
        "amount_claimed": 500.0,
        "currency": "USD",
    }
    body.update(overrides)
    res = anon.post("/api/v1/portal/claims", json=body, headers=_auth())
    assert res.status_code == 201, res.text
    return res.json()


def _ready(anon: TestClient, **overrides) -> dict:
    """A draft with its receipt attached — one step short of submit."""
    claim = _draft(anon, **overrides)
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/documents",
        files={"file": ("receipt.pdf", PDF, "application/pdf")},
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    return claim


def _submit(anon: TestClient, claim_id: str):
    return anon.post(f"/api/v1/portal/claims/{claim_id}/submit", headers=_auth())


# ── The rate lookup ──────────────────────────────────────────────────────────


def test_a_rate_is_fetched_once_and_then_cached(upstream):
    with SessionLocal() as s:
        first = fx.quote(s, "USD", INCURRED)
        s.commit()
        second = fx.quote(s, "USD", INCURRED)
    assert first is not None and first.rate == RATE
    assert first.convert(500.0) == 675.0
    assert second is not None and second.rate == RATE
    # The second read came off `fx_rates`. A published rate is immutable, so
    # re-asking would be spending someone else's rate limit on a known answer.
    assert upstream.calls == 1


def test_a_weekend_receipt_takes_the_last_published_rate_and_says_so(upstream):
    saturday = date(2027, 6, 12)
    upstream.queue = [_ok(served=date(2027, 6, 11))]
    with SessionLocal() as s:
        quote = fx.quote(s, "USD", saturday)
        s.commit()
    assert quote is not None
    assert quote.rate_date == date(2027, 6, 11)
    assert quote.as_of_date == saturday
    # `stale` is the honest word for it, not an error: no rate is ever
    # published for a Saturday, so this is the only answer that exists.
    assert quote.stale is True


def test_a_weekend_rate_is_still_cached_rather_than_refetched_forever(upstream):
    """`rate_date != as_of_date` must not mean "provisional" indefinitely.

    A Saturday can never be served its own date, so a freshness rule keyed only
    on that equality would re-fetch every weekend claim on every read, forever.
    """
    saturday = date(2027, 6, 12)
    upstream.queue = [_ok(served=date(2027, 6, 11))]
    with SessionLocal() as s:
        fx.quote(s, "USD", saturday)
        s.commit()
        fx.quote(s, "USD", saturday)
    assert upstream.calls == 1


def test_the_policy_currency_is_never_quoted(upstream):
    with SessionLocal() as s:
        assert fx.quote(s, "SGD", INCURRED) is None
    assert upstream.calls == 0


def test_the_lookup_follows_a_redirect(monkeypatch):
    """The vendor MOVES ITS HOST — `api.frankfurter.app` 301s to
    `api.frankfurter.dev/v1` — and httpx does not follow redirects by default.

    Without `follow_redirects`, every real call returns a 301,
    `raise_for_status` raises, and the whole feature degrades to "no rate,
    ever" — silently, because that is a legitimate outcome here. Asserting the
    flag is the only way a stubbed suite can hold the line on it.
    """
    seen: dict[str, object] = {}

    def _get(url, params=None, timeout=None, follow_redirects=False):
        seen["follow_redirects"] = follow_redirects
        return _ok()

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setenv("INSPRO_FX_ENABLED", "1")
    clear_settings_cache()
    fx.reset_breaker()
    try:
        with SessionLocal() as s:
            s.query(FxRate).delete()
            s.commit()
            assert fx.quote(s, "USD", date(2027, 6, 15)) is not None
    finally:
        monkeypatch.delenv("INSPRO_FX_ENABLED", raising=False)
        clear_settings_cache()
    assert seen["follow_redirects"] is True


def test_a_failed_lookup_retries_twice_and_then_gives_up(upstream):
    upstream.queue = [httpx.ConnectError("down")]
    with SessionLocal() as s:
        assert fx.quote(s, "USD", INCURRED) is None
    # One attempt plus two retries — the budget `fx_max_retries` names.
    assert upstream.calls == 3


def test_a_lookup_that_succeeds_on_the_last_retry_still_converts(upstream):
    upstream.queue = [httpx.ConnectError("down"), httpx.ConnectError("down"), _ok()]
    with SessionLocal() as s:
        quote = fx.quote(s, "USD", INCURRED)
    assert quote is not None and quote.rate == RATE
    assert upstream.calls == 3


def test_an_unreadable_response_is_not_retried(upstream):
    """A 200 we cannot parse is a bad ANSWER, not a bad connection.

    Retrying returns the same bytes, so the budget would be spent for nothing —
    on the request path of a member's submit.
    """
    upstream.queue = [_Resp({"rates": {}, "date": "2027-06-15"})]
    with SessionLocal() as s:
        assert fx.quote(s, "USD", INCURRED) is None
    assert upstream.calls == 1


def test_a_nonsense_rate_is_refused_rather_than_used(upstream):
    upstream.queue = [_ok(rate=0.0)]
    with SessionLocal() as s:
        assert fx.quote(s, "USD", INCURRED) is None


def test_repeated_failures_stop_the_calls_entirely(upstream):
    """The breaker. Without it an outage costs every concurrent submit the full
    retry budget in a threadpool worker, and the slow path becomes the only path."""
    upstream.queue = [httpx.ConnectError("down")]
    with SessionLocal() as s:
        for day in (15, 16, 17):
            fx.quote(s, "USD", date(2027, 6, day))
        calls_before = upstream.calls
        fx.quote(s, "USD", date(2027, 6, 18))
    assert calls_before == 9  # three lookups x three attempts
    assert upstream.calls == calls_before  # the fourth never left the process


def test_a_broker_retry_clears_the_breaker(upstream):
    upstream.queue = [httpx.ConnectError("down")]
    with SessionLocal() as s:
        for day in (15, 16, 17):
            fx.quote(s, "USD", date(2027, 6, day))
        upstream.queue = [_ok()]
        fx.reset_breaker()
        assert fx.quote(s, "USD", date(2027, 6, 18)) is not None


def test_a_future_receipt_date_is_priced_at_today(upstream):
    """There is no published rate for tomorrow, and asking for one gets today's
    silently labelled with tomorrow's date."""
    with SessionLocal() as s:
        quote = fx.quote(s, "USD", date(2099, 1, 1))
    assert quote is not None
    assert quote.as_of_date == date(2027, 6, 20)


# ── Applying it to a claim ───────────────────────────────────────────────────


def test_policy_amount_refuses_to_guess_at_an_unconverted_claim():
    claim = Claim(currency="USD", amount_claimed=500.0, amount_converted=None)
    # NOT 500.0 — that is the whole bug. A USD figure read as SGD understates
    # the claim by the exchange rate everywhere it is summed or compared.
    assert policy_amount(claim) is None
    assert fx_state(claim) == FX_STATE_UNAVAILABLE

    claim.amount_converted = 675.0
    assert policy_amount(claim) == 675.0
    assert fx_state(claim) == FX_STATE_CONVERTED


def test_a_domestic_claim_needs_no_conversion():
    claim = Claim(currency="SGD", amount_claimed=85.0)
    assert fx_state(claim) == FX_STATE_NOT_REQUIRED
    assert policy_amount(claim) == 85.0


def test_reconverting_an_unchanged_claim_keeps_the_members_acknowledgement(upstream):
    stamped = datetime.now(UTC)
    claim = Claim(
        currency="USD", amount_claimed=500.0, incurred_date=INCURRED,
        amount_converted=675.0, fx_acknowledged_at=stamped,
    )
    with SessionLocal() as s:
        apply_conversion(s, claim)
    # The figure did not move, so their consent still describes what they saw —
    # forcing a member to re-confirm the same number is noise, not diligence.
    assert claim.amount_converted == 675.0
    assert claim.fx_acknowledged_at == stamped


def test_a_changed_amount_drops_the_acknowledgement(upstream):
    claim = Claim(
        currency="USD", amount_claimed=800.0, incurred_date=INCURRED,
        amount_converted=675.0, fx_acknowledged_at=datetime.now(UTC),
    )
    with SessionLocal() as s:
        apply_conversion(s, claim)
    assert claim.amount_converted == 1080.0
    # Consent to 675 is not consent to 1080.
    assert claim.fx_acknowledged_at is None


def test_switching_a_claim_to_the_policy_currency_clears_the_whole_trail(upstream):
    claim = Claim(
        currency="SGD", amount_claimed=500.0, incurred_date=INCURRED,
        amount_converted=675.0, fx_rate=1.35, fx_rate_date=INCURRED,
        fx_source="frankfurter", fx_acknowledged_at=datetime.now(UTC),
    )
    with SessionLocal() as s:
        apply_conversion(s, claim)
    assert claim.amount_converted is None
    assert claim.fx_rate is None and claim.fx_source is None
    assert claim.fx_acknowledged_at is None


# ── The member's flow ────────────────────────────────────────────────────────


def test_the_quote_endpoint_states_the_figure_and_the_rate(anon, upstream):
    res = anon.get(
        "/api/v1/portal/fx-quote",
        params={"currency": "USD", "amount": 500, "on": INCURRED.isoformat()},
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["available"] is True
    assert body["converted"] == 675.0
    assert body["rate"] == RATE
    assert body["policy_currency"] == "SGD"
    assert "SGD 675.00" in body["note"]


def test_the_quote_endpoint_says_so_plainly_when_no_rate_can_be_had(anon, upstream):
    upstream.queue = [httpx.ConnectError("down")]
    res = anon.get(
        "/api/v1/portal/fx-quote",
        params={"currency": "USD", "amount": 500, "on": INCURRED.isoformat()},
        headers=_auth(),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is False and body["converted"] is None
    # It must tell the member they can still file. Anything else reads as "your
    # claim is blocked", which is precisely what we refuse to do to them.
    assert "still be sent" in body["note"]


def test_a_draft_carries_its_converted_figure(anon, upstream):
    claim = _draft(anon)
    assert claim["fx_state"] == "converted"
    assert claim["amount_converted"] == 675.0
    assert claim["fx_rate"] == RATE
    assert claim["policy_currency"] == "SGD"


def test_submitting_an_unconfirmed_foreign_claim_asks_for_confirmation(anon, upstream):
    claim = _ready(anon)
    res = _submit(anon, claim["id"])
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "fx_confirmation_required"
    # The figure rides along, so the form can show it and ask — rather than
    # bouncing the member back to a screen with no explanation.
    assert detail["amount_converted"] == 675.0


def test_confirming_the_conversion_lets_the_claim_through(anon, upstream):
    claim = _ready(anon)
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/confirm-conversion",
        json={"converted_amount": 675.0},
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    assert res.json()["fx_acknowledged_at"] is not None
    assert _submit(anon, claim["id"]).status_code == 200


def test_confirming_does_not_count_as_amending_the_claim(anon, upstream):
    """It changes no claim fact. Routed through the amendment it would bump the
    revision, supersede the AI review and tell the member they edited something."""
    claim = _ready(anon)
    before = claim["revision"]
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/confirm-conversion",
        json={"converted_amount": 675.0}, headers=_auth(),
    )
    assert res.json()["revision"] == before


def test_confirming_a_figure_that_has_since_moved_does_not_stamp_consent(
    anon, upstream
):
    claim = _ready(anon)
    res = anon.post(
        f"/api/v1/portal/claims/{claim['id']}/confirm-conversion",
        # What the member's screen showed before the rate moved under them.
        json={"converted_amount": 600.0},
        headers=_auth(),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["fx_acknowledged_at"] is None
    assert body["amount_converted"] == 675.0  # re-asked with the current figure


def test_acknowledging_at_draft_time_carries_through_to_submit(anon, upstream):
    """The ordinary path: the form quotes, the member ticks, the claim is sent
    in one go without a round trip through the 409."""
    claim = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)
    assert claim["fx_acknowledged_at"] is not None
    assert _submit(anon, claim["id"]).status_code == 200


def test_a_member_is_never_blocked_by_a_currency_outage(anon, upstream):
    upstream.queue = [httpx.ConnectError("down")]
    claim = _ready(anon)
    assert claim["fx_state"] == "unavailable"
    assert claim["amount_converted"] is None
    # Nothing to confirm, so nothing to withhold. The claim goes through and
    # reaches the broker flagged.
    res = _submit(anon, claim["id"])
    assert res.status_code == 200, res.text


def test_a_domestic_claim_is_never_asked_to_confirm_anything(anon, upstream):
    claim = _ready(anon, currency="SGD", amount_claimed=85.0)
    assert claim["fx_state"] == "not_required"
    assert _submit(anon, claim["id"]).status_code == 200


def test_correcting_the_amount_reprices_and_re_asks(anon, upstream):
    claim = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)
    res = anon.patch(
        f"/api/v1/portal/claims/{claim['id']}",
        json={"amount_claimed": 800.0, "expected_revision": claim["revision"]},
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["amount_converted"] == 1080.0
    assert body["fx_acknowledged_at"] is None
    assert _submit(anon, claim["id"]).status_code == 409


def test_correcting_and_re_confirming_in_one_request(anon, upstream):
    claim = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)
    res = anon.patch(
        f"/api/v1/portal/claims/{claim['id']}",
        json={
            "amount_claimed": 800.0,
            "expected_revision": claim["revision"],
            "fx_acknowledged": True,
            "fx_quoted_amount": 1080.0,
        },
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    assert res.json()["fx_acknowledged_at"] is not None
    assert _submit(anon, claim["id"]).status_code == 200


# ── The approve guard — the bug this was all built for ───────────────────────


def _submitted(anon: TestClient, upstream, **overrides) -> str:
    claim = _ready(anon, fx_acknowledged=True, **overrides)
    if claim["amount_converted"] is not None:
        assert _submit(anon, claim["id"]).status_code == 200
    else:
        assert _submit(anon, claim["id"]).status_code == 200
    return claim["id"]


def test_a_foreign_claim_is_measured_against_the_limit_in_sgd(
    anon, broker, upstream
):
    """USD 500 is SGD 675 and the member has SGD 600 of cover.

    Before the conversion existed, `approving` was the raw 500 and 500 < 600
    passed the guard silently — the overrun only surfaced when the insurer
    invoice did.
    """
    claim_id = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)["id"]
    assert _submit(anon, claim_id).status_code == 200
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision", json={"action": "approve"}
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "limit_exceeded"
    assert detail["approving"] == 675.0
    assert detail["remaining"] == 600.0
    assert detail["policy_currency"] == "SGD"


def test_approving_an_unconverted_claim_demands_the_sgd_figure(
    anon, broker, upstream
):
    upstream.queue = [httpx.ConnectError("down")]
    claim_id = _ready(anon)["id"]
    assert _submit(anon, claim_id).status_code == 200

    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "acknowledge": True},
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "fx_amount_required"


def test_an_assessor_can_price_and_approve_in_one_request(anon, broker, upstream):
    upstream.queue = [httpx.ConnectError("down")]
    claim_id = _ready(anon)["id"]
    assert _submit(anon, claim_id).status_code == 200

    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "converted_amount": 690.0, "acknowledge": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["amount_converted"] == 690.0
    assert body["fx_source"] == FX_SOURCE_BROKER
    # No explicit `approved_amount`, so the claim settles at its full converted
    # value — in SGD, not the USD figure sitting beside it.
    assert body["amount_approved"] == 690.0


def test_an_explicit_approved_amount_is_read_as_sgd(anon, broker, upstream):
    claim_id = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)["id"]
    assert _submit(anon, claim_id).status_code == 200
    res = broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 400.0},
    )
    assert res.status_code == 200, res.text
    assert res.json()["amount_approved"] == 400.0


def test_the_standalone_conversion_endpoint_prices_a_stranded_claim(
    anon, broker, upstream
):
    upstream.queue = [httpx.ConnectError("down")]
    claim_id = _ready(anon)["id"]
    assert _submit(anon, claim_id).status_code == 200

    res = broker.post(
        f"/api/v1/claims/{claim_id}/conversion",
        json={"converted_amount": 690.0, "note": "Bank statement rate"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["amount_converted"] == 690.0
    # Refused a second time: re-pricing a claim without changing the claim is
    # how a settled figure moves with nothing recording why.
    again = broker.post(
        f"/api/v1/claims/{claim_id}/conversion", json={"converted_amount": 700.0}
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "fx_not_needed"


def test_a_broker_can_retry_the_rate_once_the_upstream_recovers(
    anon, broker, upstream
):
    upstream.queue = [httpx.ConnectError("down")]
    claim_id = _ready(anon)["id"]
    assert _submit(anon, claim_id).status_code == 200

    upstream.queue = [_ok()]
    res = broker.post(f"/api/v1/claims/{claim_id}/fx-refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["fx_state"] == "converted"
    assert body["amount_converted"] == 675.0


# ── Regressions found in review ──────────────────────────────────────────────


def test_a_resubmission_does_not_destroy_the_assessors_figure(anon, broker, upstream):
    """A broker prices a stranded claim, asks for a document, the member resends.

    `submit_claim` re-runs the conversion on that path. Unguarded it wiped the
    assessor's hand-keyed figure — back to `unavailable` with the rate still
    down, and with nothing in the trail to say it had ever been set. Both broker
    endpoints 409 to prevent exactly this; reaching it through submit would be
    the same bug by a longer route.
    """
    upstream.queue = [httpx.ConnectError("down")]
    claim_id = _ready(anon)["id"]
    assert _submit(anon, claim_id).status_code == 200

    assert broker.post(
        f"/api/v1/claims/{claim_id}/conversion", json={"converted_amount": 690.0}
    ).status_code == 200
    assert broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "needs_info", "note": "Send the itemised bill."},
    ).status_code == 200

    # The member is still asked to accept the figure — MORE important when a
    # person chose it, not less: they are reimbursed against it either way, and
    # the claim detail page renders the control for exactly this path.
    assert _submit(anon, claim_id).status_code == 409
    assert anon.post(
        f"/api/v1/portal/claims/{claim_id}/confirm-conversion",
        json={"converted_amount": 690.0},
        headers=_auth(),
    ).status_code == 200

    res = _submit(anon, claim_id)
    assert res.status_code == 200, res.text
    body = res.json()
    # The point of the test: neither the resubmission NOR the member's
    # confirmation re-priced the claim out from under the assessor.
    assert body["amount_converted"] == 690.0
    assert body["fx_source"] == FX_SOURCE_BROKER


def test_a_recovered_rate_does_not_silently_overwrite_the_assessor(
    anon, broker, upstream
):
    """Same path, but the upstream comes back before the member resends.

    The market rate must NOT quietly replace a figure a person chose — that is
    a claim being re-priced with nothing recording it.
    """
    upstream.queue = [httpx.ConnectError("down")]
    claim_id = _ready(anon)["id"]
    assert _submit(anon, claim_id).status_code == 200
    broker.post(
        f"/api/v1/claims/{claim_id}/conversion", json={"converted_amount": 690.0}
    )
    broker.post(f"/api/v1/claims/{claim_id}/decision", json={"action": "needs_info"})

    upstream.queue = [_ok()]  # the rate service recovers
    anon.post(
        f"/api/v1/portal/claims/{claim_id}/confirm-conversion",
        json={"converted_amount": 690.0},
        headers=_auth(),
    )
    assert _submit(anon, claim_id).status_code == 200
    with SessionLocal() as s:
        claim = s.get(Claim, claim_id)
        assert claim.amount_converted == 690.0
        assert claim.fx_source == FX_SOURCE_BROKER


def test_correcting_the_amount_DOES_discard_the_assessors_figure(
    anon, broker, upstream
):
    """The one path where it must go: the figure priced a claim that has since
    changed, so it describes something that no longer exists."""
    upstream.queue = [httpx.ConnectError("down")]
    claim = _ready(anon)
    assert _submit(anon, claim["id"]).status_code == 200
    broker.post(
        f"/api/v1/claims/{claim['id']}/conversion", json={"converted_amount": 690.0}
    )
    broker.post(f"/api/v1/claims/{claim['id']}/decision", json={"action": "needs_info"})

    upstream.queue = [_ok()]
    res = anon.patch(
        f"/api/v1/portal/claims/{claim['id']}",
        json={"amount_claimed": 800.0},
        headers=_auth(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["amount_converted"] == 1080.0  # 800 x 1.35, re-priced
    assert body["fx_source"] == "frankfurter"


def test_the_quote_endpoint_actually_warms_the_cache(anon, upstream):
    """Its docstring promises submit will be a cache hit.

    `get_db` never commits, so without an explicit one the fetched row is
    discarded on the way out — the promise was false and the member paid the
    full upstream retry budget again on create AND on submit, inside requests
    they are waiting on.
    """
    res = anon.get(
        "/api/v1/portal/fx-quote",
        params={"currency": "USD", "amount": 500, "on": INCURRED.isoformat()},
        headers=_auth(),
    )
    assert res.status_code == 200
    assert upstream.calls == 1
    with SessionLocal() as s:
        assert s.query(FxRate).count() == 1

    # The draft and the submit that follow must both read it back, not re-ask.
    claim = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)
    assert _submit(anon, claim["id"]).status_code == 200
    assert upstream.calls == 1


def test_a_broker_amendment_cannot_stamp_the_members_consent(anon, broker, upstream):
    """`fx_acknowledged_at` records that the CLAIMANT accepted the figure, and it
    is what submit gates on. A broker body carrying the flag would write a false
    record of consent and open the gate on the member's behalf."""
    claim = _ready(anon)
    res = broker.patch(
        f"/api/v1/claims/{claim['id']}",
        json={
            "amount_claimed": 500.0,
            "fx_acknowledged": True,
            "fx_quoted_amount": 675.0,
            "reason": "Corrected from the invoice.",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["fx_acknowledged_at"] is None
    # And the member is still asked.
    assert _submit(anon, claim["id"]).status_code == 409


def test_a_second_writer_for_the_same_day_does_not_break_the_first(upstream):
    """The duplicate-insert race, which is why the write is ON CONFLICT DO
    NOTHING rather than a try/except: on Postgres a unique violation aborts the
    caller's WHOLE transaction, and the caller is a member's claim submission."""
    # Exercised through the write itself rather than two live sessions: two
    # concurrent SQLite writers deadlock on the dev dialect regardless of what
    # the statement says, so a "real" race here would only ever test SQLite's
    # locking. The conflict CLAUSE is the thing under test, and a second insert
    # of the same key is exactly what a loser of the race issues.
    row = {
        "base_currency": "USD",
        "quote_currency": "SGD",
        "as_of_date": INCURRED,
        "rate_date": INCURRED,
        "rate": RATE,
        "source": "frankfurter",
        "fetched_at": datetime.now(UTC),
    }
    with SessionLocal() as s:
        fx._insert_ignoring_conflicts(s, {"id": "fx-race-a", **row})
        # Raises nothing — which is the requirement. A bare INSERT would throw,
        # and on Postgres that aborts the caller's WHOLE transaction: the loser
        # of a race over a cache row would lose a member's claim submission.
        fx._insert_ignoring_conflicts(s, {"id": "fx-race-b", **row})
        s.commit()
        assert s.query(FxRate).count() == 1


def test_the_quoted_figure_and_the_stored_one_agree_to_the_cent(upstream):
    """They are compared against a half-cent tolerance when the member's
    acknowledgement is stamped, so the two paths must convert the SAME number.
    `build_quote` used to round its input first while `apply_conversion` passed
    the raw amount — enough to refuse a valid acknowledgement."""
    odd = 500.005
    with SessionLocal() as s:
        quoted = build_quote(s, currency="USD", amount=odd, on=INCURRED).converted
        s.commit()
        claim = Claim(currency="USD", amount_claimed=odd, incurred_date=INCURRED)
        apply_conversion(s, claim)
    assert quoted == claim.amount_converted


# ── Everything downstream of the figure ──────────────────────────────────────


def test_an_unconverted_claim_is_counted_but_never_summed(anon, broker, upstream):
    upstream.queue = [httpx.ConnectError("down")]
    claim_id = _ready(anon)["id"]
    assert _submit(anon, claim_id).status_code == 200

    res = broker.get(f"/api/v1/employees/{EMP}/utilization")
    assert res.status_code == 200, res.text
    ghs = next(b for b in res.json()["insured"] if b["benefit_key"] is None)
    # 500 is a USD figure. Adding it to an SGD bucket is arithmetic across two
    # currencies, so it is reported as a count instead.
    assert ghs["pending"] == 0.0
    assert ghs["pending_unconverted"] == 1
    assert claim_id in ghs["pending_claim_ids"]


def test_a_converted_claim_pends_at_its_sgd_value(anon, broker, upstream):
    claim_id = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)["id"]
    assert _submit(anon, claim_id).status_code == 200

    res = broker.get(f"/api/v1/employees/{EMP}/utilization")
    ghs = next(b for b in res.json()["insured"] if b["benefit_key"] is None)
    assert ghs["pending"] == 675.0
    assert ghs["pending_unconverted"] == 0


def test_the_review_flags_an_unpriceable_claim_without_burning_the_ai_pass(
    upstream,
):
    """`warning` + `flag`, not `fail`.

    A `fail` short-circuits the whole pipeline, so a currency API being briefly
    unreachable would cost the assessor every document check on a claim whose
    paperwork is almost certainly fine. The two questions are unrelated.
    """
    from app.services.claims_review import rules
    from app.services.claims_review.verdict import compute_verdict

    claim = Claim(currency="USD", amount_claimed=500.0, incurred_date=INCURRED)
    result = rules._check_currency(claim)
    assert result["status"] == "warning"
    assert result["flag"] is True
    assert not rules.has_failures([result])  # the pipeline runs on

    verdict, reasons = compute_verdict([result], [], [], 1.0)
    assert verdict == "flagged"
    assert reasons


def test_a_priced_claim_passes_the_review_and_shows_its_working(upstream):
    from app.services.claims_review import rules

    claim = Claim(
        currency="USD", amount_claimed=500.0, incurred_date=INCURRED,
        amount_converted=675.0, fx_rate=RATE, fx_rate_date=date(2027, 6, 12),
        fx_source="frankfurter",
    )
    result = rules._check_currency(claim)
    assert result["status"] == "pass"
    # An assessor comparing the two dates must not read an ordinary weekend
    # receipt as a discrepancy, so the reason is spelled out.
    assert "none is published for 2027-06-15" in result["evidence"]


def test_the_approval_notice_quotes_sgd_not_the_claims_own_currency(
    anon, broker, upstream
):
    claim_id = _ready(anon, fx_acknowledged=True, fx_quoted_amount=675.0)["id"]
    assert _submit(anon, claim_id).status_code == 200
    assert broker.post(
        f"/api/v1/claims/{claim_id}/decision",
        json={"action": "approve", "approved_amount": 675.0, "acknowledge": True},
    ).status_code == 200

    res = anon.get(f"/api/v1/portal/claims/{claim_id}/messages", headers=_auth())
    assert res.status_code == 200, res.text
    approved = [m for m in res.json() if "approved" in (m["subject"] or "").lower()]
    assert approved, res.text
    # "US$675" would overstate the reimbursement by the exchange rate, in a
    # message written by the system about to pay it.
    assert "S$675" in approved[0]["body"]
    assert "US$675" not in approved[0]["body"]
