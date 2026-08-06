"""Member questions — threads that hang off no claim.

Covers what is particular to a question: the routing topic that must never
become one, the open cap, the closed-thread refusals, the optional claim
REFERENCE, and the fact that a question appears in both conversation lists
without disturbing the claim threads beside it.

Member-level isolation (one member's question is invisible and unreachable to
another) lives here too; cross-TENANT 404s are in test_tenant_isolation.py.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_member_enquiries.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Claim,
    ClaimMessage,
    Employee,
    MemberAccount,
    MemberEnquiry,
    PolicyYear,
)
from app.models.member_account import MEMBER_STATUS_ACTIVE  # noqa: E402
from app.models.member_enquiry import MAX_OPEN_ENQUIRIES  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

PY = "00000000-0000-0000-0000-0000000000q1"
EMP_A = "00000000-0000-0000-0000-0000000000q2"
EMP_B = "00000000-0000-0000-0000-0000000000q3"
ACC_A = "00000000-0000-0000-0000-0000000000q4"
ACC_B = "00000000-0000-0000-0000-0000000000q5"
CLAIM_A = "00000000-0000-0000-0000-0000000000q6"
CLAIM_B = "00000000-0000-0000-0000-0000000000q7"


def _broker_user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000000q9",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(
            PolicyYear(
                id=PY,
                client_id=DEMO_CLIENT_ID,
                year=2029,
                start_date=date(2029, 1, 1),
                end_date=date(2029, 12, 31),
                status=PolicyYearStatus.active,
            )
        )
        s.flush()
        for acc, emp, staff, name in (
            (ACC_A, EMP_A, "Q-1", "Ana"),
            (ACC_B, EMP_B, "Q-2", "Ben"),
        ):
            s.add(
                MemberAccount(
                    id=acc,
                    client_id=DEMO_CLIENT_ID,
                    email=f"{staff.lower()}@q.test",
                    staff_id=staff,
                    status=MEMBER_STATUS_ACTIVE,
                )
            )
            s.flush()
            s.add(
                Employee(
                    id=emp,
                    client_id=DEMO_CLIENT_ID,
                    policy_year_id=PY,
                    staff_id=staff,
                    employee_name=name,
                    member_account_id=acc,
                    attribute_values={},
                    derived_attribute_values={},
                    source="csv_import",
                    status="active",
                )
            )
        s.flush()
        for cid, emp in ((CLAIM_A, EMP_A), (CLAIM_B, EMP_B)):
            s.add(
                Claim(
                    id=cid,
                    client_id=DEMO_CLIENT_ID,
                    policy_year_id=PY,
                    employee_id=emp,
                    claim_kind="insured",
                    product_code="GHS",
                    claim_type="outpatient",
                    incurred_date=date(2029, 3, 1),
                    amount_claimed=42.0,
                    status="submitted",
                )
            )
        s.commit()
    yield
    with SessionLocal() as s:
        s.query(MemberAccount).filter(
            MemberAccount.client_id == DEMO_CLIENT_ID
        ).delete()
        py = s.get(PolicyYear, PY)
        if py is not None:
            s.delete(py)
        s.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _clean_enquiries():
    """Questions do not leak between cases.

    They are capped per member (`MAX_OPEN_ENQUIRIES`), so on a module-scoped
    database an accumulating fixture would silently start 409-ing later tests
    — which is what happened, and which reads as a product bug rather than a
    test one.
    """
    yield
    with SessionLocal() as s:
        s.query(ClaimMessage).filter(ClaimMessage.enquiry_id.is_not(None)).delete()
        s.query(MemberEnquiry).delete()
        s.commit()


@pytest.fixture
def anon() -> TestClient:
    return TestClient(app)


@pytest.fixture
def broker() -> TestClient:
    app.dependency_overrides[get_current_user] = _broker_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _auth(account: str = ACC_A) -> dict[str, str]:
    token, _ = issue_member_token(account, DEMO_CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def _ask(anon: TestClient, account: str = ACC_A, **over) -> dict:
    body = {
        "topic": "coverage",
        "subject": "Am I covered for physiotherapy?",
        "body": "My doctor suggested six sessions.",
    }
    body.update(over)
    res = anon.post("/api/v1/portal/enquiries", json=body, headers=_auth(account))
    assert res.status_code == 201, res.text
    return res.json()


def test_topics_are_served_and_name_the_routing_option(anon: TestClient):
    res = anon.get("/api/v1/portal/enquiry-topics", headers=_auth())
    assert res.status_code == 200
    topics = res.json()
    routing = [t for t in topics if t["routes_to_claim"]]
    assert [t["key"] for t in routing] == ["claim"]
    assert len(topics) > 1


def test_a_claim_question_may_not_become_its_own_thread(anon: TestClient):
    """The routing option is an instruction to the FORM, not a subject.

    Honouring it here would mint the duplicate thread the routing exists to
    prevent: two conversations about one claim, each readable while the other
    still shows unread.
    """
    res = anon.post(
        "/api/v1/portal/enquiries",
        json={"topic": "claim", "subject": "About my claim", "body": "any news?"},
        headers=_auth(),
    )
    assert res.status_code == 422
    assert "belongs on that claim" in res.text


def test_a_question_creates_its_thread_and_first_message(anon: TestClient):
    enquiry = _ask(anon)
    assert enquiry["status"] == "open"
    assert enquiry["about_claim"] is None

    thread = anon.get(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages", headers=_auth()
    ).json()
    assert [m["body"] for m in thread] == ["My doctor suggested six sessions."]
    assert thread[0]["mine"] is True

    # And it is a CONVERSATION beside the member's claim threads.
    rows = anon.get("/api/v1/portal/conversations", headers=_auth()).json()["items"]
    mine = next(c for c in rows if c["subject"]["id"] == enquiry["id"])
    assert mine["subject"]["kind"] == "enquiry"
    assert mine["subject"]["subject"] == "Am I covered for physiotherapy?"
    assert mine["subject"]["topic"] == "coverage"
    assert mine["message_count"] == 1


def test_the_optional_claim_reference_is_context_not_a_second_thread(
    anon: TestClient,
):
    enquiry = _ask(
        anon, subject="Why was June settled at less?", about_claim_id=CLAIM_A
    )
    assert enquiry["about_claim"]["id"] == CLAIM_A
    assert enquiry["about_claim"]["kind"] == "claim"

    # The referenced claim's OWN thread is untouched — no message was added to
    # it, which is the whole point of a reference.
    claim_thread = anon.get(
        f"/api/v1/portal/claims/{CLAIM_A}/messages", headers=_auth()
    ).json()
    assert all(m["body"] != "My doctor suggested six sessions." for m in claim_thread)


def test_a_claim_reference_the_member_does_not_own_404s(anon: TestClient):
    """Validated through `load_member_claim`, so the field cannot be used to
    probe for other members' claims."""
    res = anon.post(
        "/api/v1/portal/enquiries",
        json={
            "topic": "coverage",
            "subject": "About someone else's claim",
            "body": "hello",
            "about_claim_id": CLAIM_B,
        },
        headers=_auth(ACC_A),
    )
    assert res.status_code == 404


def test_questions_are_member_scoped(anon: TestClient):
    """404, not 403 — the same not-leaking convention as the rest of the
    portal."""
    enquiry = _ask(anon, ACC_A, subject="Ana's own question")
    for method, path in (
        ("get", f"/api/v1/portal/enquiries/{enquiry['id']}"),
        ("get", f"/api/v1/portal/enquiries/{enquiry['id']}/messages"),
    ):
        assert getattr(anon, method)(path, headers=_auth(ACC_B)).status_code == 404
    assert anon.post(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages",
        json={"body": "peeking"},
        headers=_auth(ACC_B),
    ).status_code == 404

    # And it is absent from Ben's conversation list entirely.
    rows = anon.get(
        "/api/v1/portal/conversations", headers=_auth(ACC_B)
    ).json()["items"]
    assert all(c["subject"]["id"] != enquiry["id"] for c in rows)


def test_the_open_cap_refuses_the_sixth(anon: TestClient):
    for i in range(MAX_OPEN_ENQUIRIES):
        _ask(anon, ACC_B, subject=f"Question {i}")
    res = anon.post(
        "/api/v1/portal/enquiries",
        json={"topic": "other", "subject": "One more", "body": "please"},
        headers=_auth(ACC_B),
    )
    assert res.status_code == 409
    assert str(MAX_OPEN_ENQUIRIES) in res.text


def test_broker_answers_and_the_thread_becomes_answered(
    anon: TestClient, broker: TestClient
):
    enquiry = _ask(anon, subject="Which clinics take my card?")
    res = broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/messages",
        json={"body": "Any clinic on the panel list."},
    )
    assert res.status_code == 201, res.text
    assert broker.get(f"/api/v1/enquiries/{enquiry['id']}").json()["status"] == "answered"

    # The member reads a TEAM, never the individual who replied.
    member_thread = anon.get(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages", headers=_auth()
    ).json()
    assert member_thread[-1]["author_name"] == "Claims team"
    # The broker sees the real author on their own surface.
    broker_thread = broker.get(f"/api/v1/enquiries/{enquiry['id']}/messages").json()
    assert broker_thread[-1]["author_name"] != "Claims team"


def test_closing_needs_an_answer_first_and_then_locks_the_thread(
    anon: TestClient, broker: TestClient
):
    """A thread that ends with nobody having answered reads as being ignored on
    purpose — and from the member's side that is indistinguishable from what it
    would be."""
    enquiry = _ask(anon, subject="Can I add my son?")
    assert broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/status", json={"action": "close"}
    ).status_code == 409

    broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/messages", json={"body": "Yes — here's how."}
    )
    assert broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/status", json={"action": "close"}
    ).status_code == 200

    # Closed: neither side may write into it.
    assert anon.post(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages",
        json={"body": "one more thing"},
        headers=_auth(),
    ).status_code == 409
    assert broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/messages", json={"body": "hello?"}
    ).status_code == 409

    # Reopening restores it.
    assert broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/status", json={"action": "reopen"}
    ).json()["status"] == "answered"
    assert anon.post(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages",
        json={"body": "one more thing"},
        headers=_auth(),
    ).status_code == 201


def test_a_question_reaches_the_broker_queue_like_any_thread(
    anon: TestClient, broker: TestClient
):
    """It is not a separate queue: "a member is waiting on a reply" is one job
    whatever the thread hangs off."""
    enquiry = _ask(anon, subject="Where do I find my card?")
    queue = broker.get(f"/api/v1/conversations?policy_year_id={PY}").json()
    row = next(c for c in queue["items"] if c["subject"]["id"] == enquiry["id"])
    assert row["subject"]["kind"] == "enquiry"
    assert row["employee"]["staff_id"] == "Q-1"
    assert row["last_message"]["author_type"] == "member"
    assert row["unread"] == 1


def test_the_preview_mirrors_a_question_through_the_member_serializer(
    anon: TestClient, broker: TestClient
):
    enquiry = _ask(anon, subject="Preview me")
    broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/messages", json={"body": "answered"}
    )
    preview = broker.get(
        f"/api/v1/employees/{EMP_A}/portal-preview/enquiries/{enquiry['id']}/messages"
    )
    member = anon.get(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages", headers=_auth()
    )
    assert preview.status_code == member.status_code == 200
    assert preview.json() == member.json()
    assert preview.json()[-1]["author_name"] == "Claims team"

    # Another employee's question is not reachable through this preview.
    assert broker.get(
        f"/api/v1/employees/{EMP_B}/portal-preview/enquiries/{enquiry['id']}"
    ).status_code == 404


def test_the_topic_is_LABELLED_on_both_lists_not_left_as_a_key(
    anon: TestClient, broker: TestClient
):
    """The vocabulary has one home on the backend.

    Both surfaces got this wrong from opposite ends: the broker's queue printed
    the raw key (`Question · clinics · answered`) and the member's row dropped
    the topic altogether, so every question they had ever asked read `Question`
    and nothing told two of them apart.
    """
    enquiry = _ask(anon, topic="clinics", subject="Which clinics are on panel?")
    assert enquiry["topic_label"] == "Clinics & cards"

    mine = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    row = next(c for c in mine["items"] if c["subject"]["id"] == enquiry["id"])
    assert row["subject"]["topic_label"] == "Clinics & cards"

    theirs = broker.get(f"/api/v1/conversations?policy_year_id={PY}").json()
    brow = next(c for c in theirs["items"] if c["subject"]["id"] == enquiry["id"])
    assert brow["subject"]["topic_label"] == "Clinics & cards"


def test_a_guarantee_letter_request_outranks_the_wait_in_the_queue(
    anon: TestClient, broker: TestClient
):
    """The queue sorts oldest-first, which is right for everything except the
    one thing that cannot wait.

    A Letter of Guarantee is what a hospital wants before it admits somebody, so
    a member asking for one is usually standing at an admissions counter — and
    such a request is NEWEST by definition, which oldest-first buries at the
    bottom of the list. It is lifted server-side (in the SQL order as well as
    the merge, since each side is cut to `depth` before they meet).
    """
    older = _ask(anon, topic="coverage", subject="Asked first")
    urgent = _ask(anon, topic="log_request", subject="Admitted tomorrow")

    queue = broker.get(
        f"/api/v1/conversations?policy_year_id={PY}&awaiting=us"
    ).json()
    ids = [c["subject"]["id"] for c in queue["items"]]
    assert ids.index(urgent["id"]) < ids.index(older["id"])

    row = queue["items"][ids.index(urgent["id"])]
    assert row["subject"]["topic_urgent"] is True
    assert row["subject"]["topic_label"] == "Letter of Guarantee (LOG)"
    # Every other topic is ordinary — urgency is a property of the topic, and a
    # flag that were true for all of them would rank nothing.
    assert queue["items"][ids.index(older["id"])]["subject"]["topic_urgent"] is False


def test_urgency_is_a_broker_fact_and_never_reorders_the_members_own_list(
    anon: TestClient
):
    """The member's inbox is theirs, in the order things happened.

    Hoisting their own guarantee-letter request above the claim they are waiting
    on would be the portal telling them what to worry about — and the flag is
    served on the shared subject only because both surfaces read one serializer.
    """
    _ask(anon, topic="coverage", subject="Asked first")
    urgent = _ask(anon, topic="log_request", subject="Admitted tomorrow")
    mine = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    # Newest-first for the member, so the newest ask leads whatever its topic —
    # the ORDER is unchanged by urgency, which is the point.
    assert mine["items"][0]["subject"]["id"] == urgent["id"]
    assert mine["items"][0]["subject"]["topic_urgent"] is True


def test_the_unread_badge_counts_a_questions_reply(
    anon: TestClient, broker: TestClient
):
    """`unread_total` is what the portal shell's Messages badge reads.

    It was counted with a join through `claims`, which an enquiry message —
    `claim_id IS NULL` by construction — cannot satisfy. So a broker answering a
    question left the conversation row saying `unread: 1` while the badge that
    would have sent the member to look said 0.
    """
    enquiry = _ask(anon, subject="Badge me")
    before = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    assert before["unread_total"] == 0  # our own message is never unread to us

    broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/messages", json={"body": "here you go"}
    )
    after = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    row = next(c for c in after["items"] if c["subject"]["id"] == enquiry["id"])
    assert row["unread"] == 1
    assert after["unread_total"] == 1, "the badge must see what the row sees"

    # Reading it clears both, together.
    anon.post(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages/read", headers=_auth()
    )
    read = anon.get("/api/v1/portal/conversations", headers=_auth()).json()
    assert read["unread_total"] == 0


def test_the_cap_counts_only_questions_waiting_on_us(
    anon: TestClient, broker: TestClient
):
    """An ANSWERED question must not hold a slot.

    Nothing a member can do clears one: the first broker reply marks a thread
    answered and only a broker may close it. Counting those made the cap
    unclearable — five answered questions refused the sixth forever, with a
    message telling the member to do something that could not help.
    """
    for i in range(MAX_OPEN_ENQUIRIES):
        made = _ask(anon, subject=f"Question {i}")
        broker.post(
            f"/api/v1/enquiries/{made['id']}/messages", json={"body": "answered"}
        )

    res = anon.post(
        "/api/v1/portal/enquiries",
        json={"topic": "other", "subject": "One more", "body": "please"},
        headers=_auth(),
    )
    assert res.status_code == 201, res.text

    # Unanswered ones still fill it: that is the sink the cap exists to stop.
    while True:
        res = anon.post(
            "/api/v1/portal/enquiries",
            json={"topic": "other", "subject": "Again", "body": "please"},
            headers=_auth(),
        )
        if res.status_code == 409:
            break
        assert res.status_code == 201, res.text
    assert "waiting on us" in res.json()["detail"]


def test_reopen_refuses_a_question_that_is_not_closed(
    anon: TestClient, broker: TestClient
):
    """Otherwise it stamps an unanswered thread `answered`, which the member's
    strike renders as the green settled state over a thread nobody has written
    in. The UI only offers it on a closed thread; the endpoint is the contract.
    """
    enquiry = _ask(anon, subject="Not closed")
    res = broker.post(
        f"/api/v1/enquiries/{enquiry['id']}/status", json={"action": "reopen"}
    )
    assert res.status_code == 409
    assert (
        broker.get(f"/api/v1/enquiries/{enquiry['id']}").json()["status"] == "open"
    )


def test_a_question_keeps_the_paragraphs_it_was_written_with(anon: TestClient):
    """Bodies render `whitespace-pre-line` on both surfaces.

    The blank-check used to collapse whitespace on anything under 256
    characters, so a short two-paragraph question arrived flattened while a long
    one did not — and the member's own later replies, validated elsewhere, kept
    theirs. One member, one thread, two treatments of the same key.
    """
    body = "First paragraph.\n\nSecond paragraph."
    enquiry = _ask(anon, subject="  Spaced   out  ", body=body)
    msgs = anon.get(
        f"/api/v1/portal/enquiries/{enquiry['id']}/messages", headers=_auth()
    ).json()
    assert msgs[0]["body"] == body
    # A SUBJECT is one line by definition and is still collapsed.
    assert enquiry["subject"] == "Spaced out"
