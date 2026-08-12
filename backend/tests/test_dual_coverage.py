"""Dual coverage — lives insured twice under one company.

Fixtures are built explicitly: the synthetic roster generator gives every
dependant a random name AND a random NRIC, so it can never produce a
dual-coverage family.

The rules under test are the ones whose failure is SILENT — a name read that
matches on the PARENT, an NRIC leg blind to portal self-adds, a symmetric couple
emitting two of everything, a decision orphaned by the very fix it prompted.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_dual_coverage.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Dependant,
    Employee,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-0000000dc000"
PY_ID = "00000000-0000-0000-0000-0000000dc001"
PROD_ID = "00000000-0000-0000-0000-0000000dc002"
CAT_ID = "00000000-0000-0000-0000-0000000dc003"

# The couple: FATHER and MOTHER both work here.
E_FATHER = "00000000-0000-0000-0000-0000000dc010"
E_MOTHER = "00000000-0000-0000-0000-0000000dc011"
E_OTHER = "00000000-0000-0000-0000-0000000dc012"
E_LEAVER = "00000000-0000-0000-0000-0000000dc013"

FATHER_NRIC = "S1111111A"
MOTHER_NRIC = "S2222222B"
KID_NRIC = "T3333333C"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000dc0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


def _dep(s, did, emp_id, attrs, *, status="active", nric=None, link="staff_id"):
    s.add(
        Dependant(
            id=did, client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=emp_id,
            attribute_values=attrs, link_method=link, status=status,
            national_id_normalized=nric,
        )
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Dual Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2030,
            start_date=date(2030, 1, 1), end_date=date(2030, 12, 31),
            status=PolicyYearStatus.active,
        ))
        s.add(Product(id=PROD_ID, client_id=CLIENT_ID, code="GHS",
                      display_name="Hospital", insurer="ACME", has_dependants=True))
        s.flush()
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="All staff and dependants",
            raw_description="All staff and dependants",
            # A tier menu with a family tier — so the cohort heuristic says this
            # category extends to dependants.
            plan_assignments={"plan_code": "PLAN 1", "rate_tiers": {"EF": {"premium": 1.0}}},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        for eid, staff, name, nric, status in (
            (E_FATHER, "D-100", "Tan Kok Leong", FATHER_NRIC, "active"),
            (E_MOTHER, "D-200", "Lim Siew Hong", MOTHER_NRIC, "active"),
            (E_OTHER, "D-300", "Unrelated Person", "S9999999Z", "active"),
            (E_LEAVER, "D-400", "Gone Already", "S8888888Y", "terminated"),
        ):
            s.add(Employee(
                id=eid, client_id=CLIENT_ID, policy_year_id=PY_ID,
                staff_id=staff, employee_name=name,
                attribute_values={"id_no": nric, "date_of_birth": "1980-01-01"},
                national_id_normalized=nric, derived_attribute_values={},
                matched_categories=[{"category_id": CAT_ID, "product_code": "GHS",
                                     "method": "rule", "confidence": 1.0}],
                matched_category_id=CAT_ID, source="csv_import", status=status,
            ))
        s.flush()

        # (a) the SAME child under both parents, matched by NRIC.
        _dep(s, "dc-kid-f", E_FATHER, {
            "dependant_name": "Tan Wei Ming", "relationship": "Son",
            "date_of_birth": "2014-05-02", "dependant_id_no": KID_NRIC,
        }, nric=KID_NRIC)
        _dep(s, "dc-kid-m", E_MOTHER, {
            "dependant_name": "Tan Wei Ming", "relationship": "Child",
            "date_of_birth": "2014-05-02", "dependant_id_no": KID_NRIC,
        }, nric=KID_NRIC)

        # (b) a SECOND child, listed under the mother only, no NRIC — matched by
        # name+DOB nowhere, so this is an OPPORTUNITY once the couple is known.
        _dep(s, "dc-kid2-m", E_MOTHER, {
            "dependant_name": "Tan Wei Jie", "relationship": "Daughter",
            "date_of_birth": "2018-09-09",
        })

        # (c) the couple links: each lists the other as spouse. Symmetric on
        # purpose — one couple must not become two.
        _dep(s, "dc-sp-f", E_FATHER, {
            "dependant_name": "Lim Siew Hong", "relationship": "Spouse",
            "date_of_birth": "1980-01-01", "dependant_id_no": MOTHER_NRIC,
        }, nric=MOTHER_NRIC)
        _dep(s, "dc-sp-m", E_MOTHER, {
            "dependant_name": "Tan Kok Leong", "relationship": "Spouse",
            "date_of_birth": "1980-01-01", "dependant_id_no": FATHER_NRIC,
        }, nric=FATHER_NRIC)

        # Noise that must NOT be reported.
        _dep(s, "dc-solo", E_OTHER, {
            "dependant_name": "Only Child", "relationship": "Son",
            "date_of_birth": "2016-01-01",
        })
        # A row carrying ONLY the parent's name — the DEP_NAME_KEYS trap. It must
        # not match the father by name.
        _dep(s, "dc-nameless", E_OTHER, {
            "employee_name": "Tan Kok Leong", "relationship": "Father",
            "date_of_birth": "1980-01-01",
        })
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _get(client: TestClient) -> dict:
    res = client.get(f"/api/v1/policy-years/{PY_ID}/dual-coverage")
    assert res.status_code == 200, res.text
    return res.json()


def _case_named(body: dict, name: str) -> dict | None:
    return next((c for c in body["cases"] if c["name"] == name), None)


# ── Detection ───────────────────────────────────────────────────────────────


def test_child_listed_under_both_parents_is_a_case(client: TestClient) -> None:
    case = _case_named(_get(client), "Tan Wei Ming")
    assert case is not None
    assert "listed_twice" in case["flags"]
    assert case["match_tier"] == "nric"
    staff = sorted(p["staff_id"] for p in case["parties"])
    assert staff == ["D-100", "D-200"]


def test_an_employee_carried_as_a_spouse_is_a_case(client: TestClient) -> None:
    """They hold their own employee cover AND spouse cover at once."""
    body = _get(client)
    spouse_cases = [c for c in body["cases"] if "employee_as_spouse" in c["flags"]]
    # Both halves of the couple qualify — but each is ONE case, not two.
    assert len(spouse_cases) == 2
    for case in spouse_cases:
        # The life's own employee row is a party with no dependant row.
        assert any(p["dependant_id"] is None for p in case["parties"])


def test_a_row_with_only_the_parents_name_matches_nobody(client: TestClient) -> None:
    """`NAME_KEYS` includes `employee_name` and dependant rows carry it, so
    identifying a dependant through it reports the PARENT's name — which would
    make every nameless row collide with its own parent."""
    from app.services.dual_coverage import dependant_identity

    with SessionLocal() as s:
        dep = s.get(Dependant, "dc-nameless")
        # The row carries the father's name in `employee_name` and nothing else.
        assert dependant_identity(dep).name == ""
        assert dependant_identity(dep).keys == []
    # So it is a party to nothing — in particular it does NOT get pulled into
    # the father's own case, which exists for a legitimate reason (he is carried
    # as the mother's spouse) and would quietly gain a third party.
    body = _get(client)
    attached = {
        p["dependant_id"] for c in body["cases"] for p in c["parties"]
    }
    assert "dc-nameless" not in attached


def test_unrelated_singleton_dependants_are_not_reported(client: TestClient) -> None:
    assert _case_named(_get(client), "Only Child") is None


def test_severity_keys_on_product_overlap(client: TestClient) -> None:
    """Both parents' cohort covers dependants under the same product, so the
    child really is exposed twice."""
    case = _case_named(_get(client), "Tan Wei Ming")
    assert case["overlapping_products"] == ["GHS"]
    assert case["severity"] == "warn"


# ── Opportunities (kept OUT of the alert count) ─────────────────────────────


def test_a_child_listed_once_is_an_opportunity_not_a_case(client: TestClient) -> None:
    body = _get(client)
    assert _case_named(body, "Tan Wei Jie") is None
    names = [o["child_name"] for o in body["opportunities"]]
    assert "Tan Wei Jie" in names


def test_a_symmetric_couple_yields_one_link_per_child(client: TestClient) -> None:
    """A and B list EACH OTHER, so the couple is discovered twice. Keying on the
    sorted pair is what stops every child appearing twice."""
    body = _get(client)
    rows = [o for o in body["opportunities"] if o["child_name"] == "Tan Wei Jie"]
    assert len(rows) == 1


def test_opportunities_are_never_counted_into_the_alert(client: TestClient) -> None:
    """The whole reason they are a separate list: on a real roster they
    outnumber the duplicates and would bury them."""
    body = _get(client)
    assert body["total_opportunities"] >= 1
    assert body["unresolved_cases"] == body["total_cases"]


# ── Decisions ───────────────────────────────────────────────────────────────


def _decide(client: TestClient, key: str, **kw) -> dict:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions",
        json={"subject_key": key, **kw},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_recording_a_decision_clears_the_case_from_the_count(
    client: TestClient,
) -> None:
    before = _get(client)
    case = _case_named(before, "Tan Wei Ming")
    _decide(
        client,
        case["subject_key"],
        decision="carried_by",
        carried_by_employee_id=E_MOTHER,
    )
    after = _get(client)
    assert after["unresolved_cases"] == before["unresolved_cases"] - 1
    decided = _case_named(after, "Tan Wei Ming")
    assert decided["decision"]["decision"] == "carried_by"
    assert decided["decision"]["carried_by_staff_id"] == "D-200"
    assert decided["decision"]["stale"] is False
    # Reopening puts it back.
    res = client.delete(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions/{case['subject_key']}"
    )
    assert res.status_code == 204
    assert _get(client)["unresolved_cases"] == before["unresolved_cases"]


def test_carried_by_must_name_a_party_to_the_case(client: TestClient) -> None:
    """Otherwise a decision could name an unrelated employee entirely."""
    case = _case_named(_get(client), "Tan Wei Ming")
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions",
        json={
            "subject_key": case["subject_key"],
            "decision": "carried_by",
            "carried_by_employee_id": E_OTHER,
        },
    )
    assert res.status_code == 422


def test_carried_by_without_an_employee_is_rejected(client: TestClient) -> None:
    """"Carried by nobody" would clear the flag while recording nothing."""
    case = _case_named(_get(client), "Tan Wei Ming")
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions",
        json={"subject_key": case["subject_key"], "decision": "carried_by"},
    )
    assert res.status_code == 422


def test_a_changed_family_marks_the_decision_stale(client: TestClient) -> None:
    """The failure mode has to be "ask again", never "silently resolved"."""
    case = _case_named(_get(client), "Tan Wei Ming")
    _decide(client, case["subject_key"], decision="intentional_both")
    assert _case_named(_get(client), "Tan Wei Ming")["decision"]["stale"] is False

    # A third sibling row appears under a different employee: the family the
    # broker decided about is no longer the family on file.
    with SessionLocal() as s:
        _dep(s, "dc-kid-x", E_OTHER, {
            "dependant_name": "Tan Wei Ming", "relationship": "Son",
            "date_of_birth": "2014-05-02", "dependant_id_no": KID_NRIC,
        }, nric=KID_NRIC)
        s.commit()
    try:
        after = _case_named(_get(client), "Tan Wei Ming")
        assert after["decision"]["stale"] is True
        assert after["subject_key"] in [c["subject_key"] for c in _get(client)["cases"]]
    finally:
        with SessionLocal() as s:
            s.delete(s.get(Dependant, "dc-kid-x"))
            s.commit()
        client.delete(
            f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions/{case['subject_key']}"
        )


def test_not_a_match_dismisses_a_false_positive(client: TestClient) -> None:
    """Name+DOB matching will occasionally be wrong, so the broker must be able
    to say "two different people" and have it stay said."""
    case = _case_named(_get(client), "Tan Wei Ming")
    _decide(client, case["subject_key"], decision="not_a_match")
    assert _case_named(_get(client), "Tan Wei Ming")["decision"]["decision"] == "not_a_match"
    client.delete(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions/{case['subject_key']}"
    )


# ── Identity rules ──────────────────────────────────────────────────────────


def test_nric_falls_back_to_the_attribute_blob(client: TestClient) -> None:
    """The portal self-add path never writes `national_id_normalized` and
    approval does not backfill it, so without the fallback the highest-risk
    population is invisible to NRIC matching."""
    from app.services.dual_coverage import dependant_identity

    dep = Dependant(
        id="x", client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=E_FATHER,
        attribute_values={"dependant_name": "A", "dependant_id_no": "s12-34567a"},
        national_id_normalized=None, status="active",
    )
    assert dependant_identity(dep).nric == "S1234567A"


def test_the_subject_key_is_opaque(client: TestClient) -> None:
    """It rides in a URL path, so the readable form would put a full name and
    date of birth into access logs and browser history."""
    case = _case_named(_get(client), "Tan Wei Ming")
    key = case["subject_key"]
    assert len(key) == 32 and key.isalnum()
    assert "tan" not in key.lower() and "2014" not in key


def test_name_normalization_folds_punctuation(client: TestClient) -> None:
    """Three normalizations already exist and disagree; this is the one that
    feeds the stored key, so it must be stable and inclusive."""
    from app.services.dual_coverage import normalize_name

    assert normalize_name("Tan, Ah Kow") == normalize_name("Tan Ah Kow")
    assert normalize_name("  O'Brien-Smith ") == normalize_name("O Brien Smith")


def test_an_unparseable_dob_never_matches(client: TestClient) -> None:
    """`parse_dob` and `iso_date` disagree on "15.03.1990"; matching uses the
    strict one so such a row matches nothing rather than only itself."""
    from app.services.dual_coverage import dependant_identity

    dep = Dependant(
        id="y", client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=E_FATHER,
        attribute_values={"dependant_name": "B", "date_of_birth": "15.03.1990"},
        national_id_normalized=None, status="active",
    )
    assert dependant_identity(dep).keys == []


# ── Write-time signals ──────────────────────────────────────────────────────


def test_duplicated_dependant_ids_is_the_cheap_half_of_detect(
    client: TestClient,
) -> None:
    """The bulk tool needs "is this life doubled", not who pays for it. It must
    agree with the cases the review sheet shows, so both share `_group_lives`."""
    from app.models import PolicyYear
    from app.services.dual_coverage import detect, duplicated_dependant_ids

    with SessionLocal() as s:
        py = s.get(PolicyYear, PY_ID)
        cheap = duplicated_dependant_ids(s, py)
        full = {
            p.dependant_id
            for c in detect(s, py).cases
            if "listed_twice" in c.flags
            for p in c.parties
            if p.dependant_id
        }
    assert cheap == full
    assert {"dc-kid-f", "dc-kid-m"} <= cheap
    # The spouse rows are one life each under ONE employee — not duplicated.
    assert "dc-sp-f" not in cheap


def test_the_bulk_warning_fires_only_when_the_life_is_elected(
    client: TestClient,
) -> None:
    """A warning constant nobody emits is worse than no warning at all — so this
    drives `row_codes` rather than just asserting the spec exists.

    Merely HAVING a dual-covered dependant is the roster's problem to fix on the
    Member Listing; only a change that actually elects one raises the flag."""
    from app.services.bulk_warnings import (
        DUAL_COVERAGE,
        WARNING_SPECS,
        WarningContext,
        row_codes,
    )

    spec = WARNING_SPECS[DUAL_COVERAGE]
    assert spec.severity == "warn" and spec.requires_ack is True

    ctx = WarningContext(
        year_start=date(2030, 1, 1), dual_covered_dependant_ids={"dc-kid-f"}
    )
    with SessionLocal() as s:
        emp = s.get(Employee, E_FATHER)
        common = dict(
            product_id=PROD_ID, employee=emp, baseline_category_id=CAT_ID,
            ov_source=None, action="set_plan", target_plan_code="PLAN 1",
            sum_insured_after=None, price_tag_after=None, declined_after=False,
            flex_configured=False, ineligible_dependants=0,
        )
        elected = row_codes(ctx, covered_dependant_ids=["dc-kid-f"], **common)
        untouched = row_codes(ctx, covered_dependant_ids=["dc-solo"], **common)
        none_at_all = row_codes(ctx, covered_dependant_ids=None, **common)

    assert DUAL_COVERAGE in elected
    assert DUAL_COVERAGE not in untouched
    assert DUAL_COVERAGE not in none_at_all


def test_a_decision_survives_the_key_change_it_prompted(client: TestClient) -> None:
    """The workflow's own success used to break it.

    A name+DOB case is decided; the broker then does what the case asked and
    fills in the NRIC, which changes the subject key. The decision must still
    attach (it does, via the candidate-key overlap) AND still be reopenable —
    reopen used to delete by exact key only, so the sheet showed "decided" with
    a Reopen button that 404'd, leaving the case permanently settled.
    """
    # A fresh pair with no NRIC on either row: matched on name + DOB.
    with SessionLocal() as s:
        for did, emp in (("dc-kx-f", E_FATHER), ("dc-kx-m", E_MOTHER)):
            _dep(s, did, emp, {
                "dependant_name": "Key Change Kid", "relationship": "Son",
                "date_of_birth": "2011-11-11",
            })
        s.commit()
    try:
        case = _case_named(_get(client), "Key Change Kid")
        assert case["match_tier"] == "name_dob"
        first_key = case["subject_key"]
        _decide(client, first_key, decision="intentional_both")

        # The broker adds the NRIC the case asked for — the key moves.
        with SessionLocal() as s:
            for did in ("dc-kx-f", "dc-kx-m"):
                d = s.get(Dependant, did)
                d.national_id_normalized = "T1122334E"
                d.attribute_values = {**d.attribute_values, "dependant_id_no": "T1122334E"}
            s.commit()

        moved = _case_named(_get(client), "Key Change Kid")
        assert moved["subject_key"] != first_key
        assert moved["match_tier"] == "nric"
        # Still attached, and still counted as resolved.
        assert moved["decision"]["decision"] == "intentional_both"
        # And reopenable under the NEW key.
        res = client.delete(
            f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions/{moved['subject_key']}"
        )
        assert res.status_code == 204
        assert _case_named(_get(client), "Key Change Kid")["decision"] is None
    finally:
        with SessionLocal() as s:
            for did in ("dc-kx-f", "dc-kx-m"):
                row = s.get(Dependant, did)
                if row:
                    s.delete(row)
            s.commit()


def test_re_deciding_updates_one_row_and_stamps_the_time(client: TestClient) -> None:
    """An exact-key upsert after a key change would write a SECOND row for one
    life, and `decided_at` never moved off the original insert."""
    from app.models.dual_coverage_decision import DualCoverageDecision

    case = _case_named(_get(client), "Tan Wei Ming")
    _decide(client, case["subject_key"], decision="intentional_both")
    first = _case_named(_get(client), "Tan Wei Ming")["decision"]["decided_at"]
    _decide(
        client, case["subject_key"], decision="carried_by",
        carried_by_employee_id=E_MOTHER,
    )
    after = _case_named(_get(client), "Tan Wei Ming")["decision"]
    with SessionLocal() as s:
        rows = s.query(DualCoverageDecision).filter(
            DualCoverageDecision.policy_year_id == PY_ID
        ).all()
        assert len(rows) == 1
    assert after["decision"] == "carried_by"
    assert after["decided_at"] >= first
    client.delete(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions/{case['subject_key']}"
    )


# ── The per-row marker the dependant table reads ────────────────────────────


def test_every_doubled_dependant_row_is_named_in_lives(client: TestClient) -> None:
    """`lives` keys the same detection by DEPENDANT ROW, which is what a
    paginated table has in hand.

    Both of a doubled child's rows appear — the marker has to render on
    whichever of them the broker is looking at — and each carries EVERY party,
    so the cell can name both employees rather than saying "also somewhere
    else" and leaving them to go hunting.
    """
    body = _get(client)
    case = _case_named(body, "Tan Wei Ming")
    assert case is not None

    mine = [life for life in body["lives"] if life["subject_key"] == case["subject_key"]]
    dep_ids = {p["dependant_id"] for p in case["parties"] if p["dependant_id"]}
    assert {life["dependant_id"] for life in mine} == dep_ids
    for life in mine:
        assert sorted(p["staff_id"] for p in life["parties"]) == ["D-100", "D-200"]
        assert life["severity"] == case["severity"]
        assert life["resolved"] is False


def test_lives_is_not_capped_like_the_case_list(client: TestClient) -> None:
    """The cases list is a preview; `lives` drives a per-row marker on a
    paginated table, so a cap would silently leave later pages unmarked."""
    body = _get(client)
    # Every case's dependant-backed party is represented, not just the first
    # `preview_cap` of them.
    expected = sum(
        1 for c in body["cases"] for p in c["parties"] if p["dependant_id"]
    )
    keys = {c["subject_key"] for c in body["cases"]}
    assert sum(1 for life in body["lives"] if life["subject_key"] in keys) == expected


def test_a_decided_life_is_marked_resolved_for_the_table(client: TestClient) -> None:
    body = _get(client)
    case = _case_named(body, "Tan Wei Ming")
    assert case is not None
    keeper = next(p for p in case["parties"] if p["employee_id"])
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions",
        json={
            "subject_key": case["subject_key"],
            "decision": "carried_by",
            "carried_by_employee_id": keeper["employee_id"],
        },
    )
    assert res.status_code == 200, res.text
    try:
        after = _get(client)
        marked = [
            life for life in after["lives"]
            if life["subject_key"] == case["subject_key"]
        ]
        assert marked and all(life["resolved"] for life in marked)
    finally:
        client.delete(
            f"/api/v1/policy-years/{PY_ID}/dual-coverage/decisions/{case['subject_key']}"
        )


# ── Dropping and restoring one side's cover ─────────────────────────────────


def _cover(client: TestClient, dependant_id: str, covered: bool):
    return client.put(
        f"/api/v1/policy-years/{PY_ID}/dual-coverage/dependants/{dependant_id}/cover",
        json={"covered": covered},
    )


def test_dropping_one_side_leaves_the_row_on_file(client: TestClient) -> None:
    """The ask was flexibility, not deletion: both parents keep the child on
    their roster, and only who PAYS moves. A dropped side must still appear as a
    party to the case — otherwise the case dissolves and the broker loses the
    control that put it there."""
    case = _case_named(_get(client), "Tan Wei Ming")
    assert case is not None
    side = next(p for p in case["parties"] if p["dependant_id"] and p["covered"])

    res = _cover(client, side["dependant_id"], False)
    assert res.status_code == 200, res.text
    try:
        after = _case_named(_get(client), "Tan Wei Ming")
        assert after is not None
        dropped = next(
            p for p in after["parties"] if p["dependant_id"] == side["dependant_id"]
        )
        assert dropped["covered"] is False
        assert dropped["covered_products"] == []
        # Still on file, still a side of the case.
        assert len(after["parties"]) == len(case["parties"])
        # And no longer double-paid.
        assert after["overlapping_products"] == []
        assert after["severity"] == "info"
    finally:
        _cover(client, side["dependant_id"], True)


def test_restoring_a_side_puts_the_cover_back(client: TestClient) -> None:
    case = _case_named(_get(client), "Tan Wei Ming")
    assert case is not None
    side = next(p for p in case["parties"] if p["dependant_id"] and p["covered"])
    before = sorted(side["covered_products"])

    assert _cover(client, side["dependant_id"], False).status_code == 200
    assert _cover(client, side["dependant_id"], True).status_code == 200

    after = _case_named(_get(client), "Tan Wei Ming")
    assert after is not None
    restored = next(
        p for p in after["parties"] if p["dependant_id"] == side["dependant_id"]
    )
    assert sorted(restored["covered_products"]) == before


def test_setting_the_cover_it_already_has_changes_nothing(client: TestClient) -> None:
    """The same click arriving twice must not cost a premium."""
    case = _case_named(_get(client), "Tan Wei Ming")
    assert case is not None
    side = next(p for p in case["parties"] if p["dependant_id"] and p["covered"])
    res = _cover(client, side["dependant_id"], True)
    assert res.status_code == 200, res.text
    assert res.json()["products_changed"] == []


def test_cover_cannot_be_set_on_another_tenants_dependant(client: TestClient) -> None:
    res = _cover(client, "00000000-0000-0000-0000-00000000dead", False)
    assert res.status_code == 404
