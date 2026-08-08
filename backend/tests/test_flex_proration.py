"""Pro-rating a flex allowance to the period a member was actually covered.

Two things are under test here, and they are different in kind:

1. The arithmetic (`services/flex_proration.py`) — pure, no database.
2. The two REGRESSIONS this feature exists for: a leaver keeps their allowance
   instead of having it deleted, and their family status survives their
   household being terminated in the same listing sync.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_flex_proration.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Client,
    Dependant,
    Employee,
    FlexScheme,
    PolicyYear,
)
from app.models.flex_scheme import FlexSchemeStatus  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services import flex_proration as fp  # noqa: E402
from app.services.flex_assignment import assign_flex_membership  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-0000000f2000"
PY_ID = "00000000-0000-0000-0000-0000000f2001"
YEAR = (date(2039, 1, 1), date(2039, 12, 31))


# ── The arithmetic ───────────────────────────────────────────────────────────


class _Member:
    """The three things `flex_proration` reads off a member.

    `status` matters: the leaver bound only fires on someone who has actually
    left, because an ACTIVE roster row can carry a stale leaving date.
    """

    def __init__(self, effective=None, last_day=None):
        self.attribute_values = {"effective_date": effective} if effective else {}
        self.terminated_effective = last_day
        self.status = "terminated" if last_day else "active"


def _cfg(basis=fp.BASIS_MONTHS, applies_to=fp.APPLIES_BOTH):
    return fp.ProrationConfig(basis=basis, applies_to=applies_to)


def test_a_part_month_counts_whole():
    # "By months served" already means a part month counts as a month — that is
    # what choosing months over days IS. Joining 20 Oct serves Oct/Nov/Dec = 3.
    r = fp.prorate(1200.0, _Member(effective="2039-10-20"), YEAR, _cfg())
    assert (r.served, r.total) == (3, 12)
    assert r.amount == 300.0


def test_days_basis_counts_inclusive_days():
    r = fp.prorate(3650.0, _Member(last_day=date(2039, 1, 31)), YEAR,
                   _cfg(basis=fp.BASIS_DAYS))
    assert (r.served, r.total) == (31, 365)
    assert r.amount == round(3650.0 * 31 / 365, 2)


def test_a_leap_year_denominator_is_366():
    r = fp.prorate(366.0, _Member(last_day=date(2040, 1, 1)),
                   (date(2040, 1, 1), date(2040, 12, 31)), _cfg(basis=fp.BASIS_DAYS))
    assert r.total == 366


def test_basis_none_does_not_prorate_at_all():
    assert fp.prorate(1200.0, _Member(last_day=date(2039, 6, 30)), YEAR,
                      _cfg(basis=fp.BASIS_NONE)) is None


def test_applies_to_is_checked_per_side():
    # A leavers-only scheme ignores when the member JOINED, and vice versa.
    joiner = _Member(effective="2039-07-01")
    leaver = _Member(last_day=date(2039, 6, 30))
    # The ignored side resolves to the whole period, so nothing is stored.
    assert fp.prorate(1200.0, joiner, YEAR, _cfg(applies_to=fp.APPLIES_LEAVERS)) is None
    assert fp.prorate(1200.0, leaver, YEAR, _cfg(applies_to=fp.APPLIES_LEAVERS)).served == 6
    assert fp.prorate(1200.0, leaver, YEAR, _cfg(applies_to=fp.APPLIES_JOINERS)) is None
    assert fp.prorate(1200.0, joiner, YEAR, _cfg(applies_to=fp.APPLIES_JOINERS)).served == 6


def test_a_member_is_never_reduced_by_a_missing_date():
    # Absence of evidence is not evidence of a short year. Under-allocating a
    # member because their roster row was incomplete is the one error that
    # cannot be walked back with them. They resolve to the full period, so
    # nothing is stored at all (see the full-period test below).
    assert fp.prorate(1200.0, _Member(), YEAR, _cfg()) is None
    assert fp.prorate(1200.0, _Member(effective="not a date"), YEAR, _cfg()) is None


def test_cover_outside_the_entitlement_period_allocates_nothing():
    r = fp.prorate(1200.0, _Member(last_day=date(2038, 12, 1)), YEAR, _cfg())
    assert (r.served, r.factor, r.amount) == (0, 0.0, 0.0)


def test_the_denominator_is_the_entitlement_period_not_a_hardcoded_year():
    # A scheme starting mid-year must resolve a member who served all of it to
    # 1.0 — against a hardcoded 12 they would read as ~46%, and 1.0 stores
    # nothing at all.
    period = (date(2039, 7, 15), date(2039, 12, 31))
    assert fp.prorate(1200.0, _Member(), period, _cfg()) is None
    # And a leaver inside it divides by SIX, not twelve.
    r = fp.prorate(1200.0, _Member(last_day=date(2039, 9, 30)), period, _cfg())
    assert (r.served, r.total) == (3, 6)


def test_the_factor_is_clamped():
    # Cover starting before the period cannot buy more than the whole of it.
    assert fp.prorate(1200.0, _Member(effective="2038-01-01"), YEAR, _cfg()) is None


def test_an_inverted_or_missing_period_disables_proration():
    assert fp.entitlement_period(date(2039, 12, 31), date(2039, 1, 1)) is None
    assert fp.entitlement_period(None, date(2039, 1, 1)) is None
    assert fp.prorate(1200.0, _Member(), None, _cfg()) is None


# ── Config: strict write, tolerant read ──────────────────────────────────────


@pytest.mark.parametrize("scheme", [
    None,
    {},
    {"eligibility": 7},
    {"eligibility": {"proration": "months"}},
    {"eligibility": {"proration": {"basis": "monthly"}}},
    {"eligibility": {"proration": {"basis": None, "applies_to": 7}}},
])
def test_reading_a_malformed_config_never_raises(scheme):
    # A hand-edited or legacy row must degrade to "no pro-ration", never 500 the
    # flex assignment for a whole company.
    cfg = fp.proration_config(scheme)
    assert cfg.basis == fp.BASIS_NONE
    assert not cfg.enabled


def test_the_write_boundary_is_strict_about_what_the_reader_forgives():
    assert fp.proration_errors({"eligibility": {"proration": {"basis": "monthly"}}})
    assert fp.proration_errors({"eligibility": {"entitlement_start": "whenever"}})
    assert fp.proration_errors(
        {"eligibility": {"proration": {"basis": "months_served"}}}
    ) == []


def test_describe_prints_the_fraction_or_nothing():
    assert fp.describe({"basis": "months_served", "served": 6, "total": 12}) == "6/12 months"
    assert fp.describe({"basis": "days_served", "served": 181, "total": 365}) == "181/365 days"
    assert fp.describe({"basis": "none", "served": 12, "total": 12}) == ""
    assert fp.describe(None) == ""


def test_factor_of_defaults_to_one_for_anything_unreadable():
    class E:
        flex_proration = None

    assert fp.factor_of(E()) == 1.0
    E.flex_proration = {"factor": "half"}
    assert fp.factor_of(E()) == 1.0
    E.flex_proration = {"factor": 0.5}
    assert fp.factor_of(E()) == 0.5


# ── The regressions ──────────────────────────────────────────────────────────


SCHEME = {
    "meta": {"currency": "SGD"},
    "tiers": [{
        # A band-less catch-all, so every member lands in it and the test is
        # about the pro-ration rather than tier matching.
        "name": "All employees",
        "employee_type": {"raw": "All employees"},
        "limits": [
            {"family_status": "S", "amount": 1200},
            {"family_status": "M1C", "amount": 2400},
        ],
        "benefit_categories": [{"name": "Medical", "claimable": True}],
    }],
    "eligibility": {"proration": {"basis": "months_served", "applies_to": "both"}},
}

EMP_LEAVER = "00000000-0000-0000-0000-0000000f2101"
EMP_FAMILY = "00000000-0000-0000-0000-0000000f2102"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    from scripts.seed_demo import seed
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Prorata Co", slug="prorata-co",
                     broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(id=PY_ID, client_id=CLIENT_ID, year=2039,
                         start_date=YEAR[0], end_date=YEAR[1],
                         status=PolicyYearStatus.active))
        s.flush()
        s.add(FlexScheme(policy_year_id=PY_ID, scheme=SCHEME,
                         status=FlexSchemeStatus.confirmed))
        s.flush()
        common = dict(
            client_id=CLIENT_ID, policy_year_id=PY_ID, derived_attribute_values={},
            matched_categories=[], source="csv_import",
        )
        s.add_all([
            Employee(id=EMP_LEAVER, staff_id="PR-1", employee_name="Leo Leaver",
                     status="terminated", terminated_effective=date(2039, 6, 30),
                     attribute_values={"category": "Officer",
                                       "marital_status": "Single"}, **common),
            Employee(id=EMP_FAMILY, staff_id="PR-2", employee_name="Fay Family",
                     status="terminated", terminated_effective=date(2039, 6, 30),
                     attribute_values={"category": "Officer"}, **common),
        ])
        s.flush()
        # Terminated on the SAME day as the employee — which is what the listing
        # sync does when a household leaves together.
        s.add_all([
            Dependant(client_id=CLIENT_ID, policy_year_id=PY_ID,
                      employee_id=EMP_FAMILY, status="terminated",
                      terminated_effective=date(2039, 6, 30),
                      attribute_values={"relationship": "Spouse",
                                        "dependant_name": "Sam Family"}),
            Dependant(client_id=CLIENT_ID, policy_year_id=PY_ID,
                      employee_id=EMP_FAMILY, status="terminated",
                      terminated_effective=date(2039, 6, 30),
                      attribute_values={"relationship": "Child",
                                        "dependant_name": "Kim Family"}),
        ])
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


def _user() -> CurrentUser:
    return CurrentUser(user_id="u", broker_firm_id=DEMO_BROKER_FIRM_ID,
                       client_id=CLIENT_ID, role="broker_admin")


def test_a_leaver_keeps_their_allocation_pro_rated():
    """THE regression. Assignment used to end with a bulk
    `UPDATE ... SET flex_* = NULL WHERE status != 'active'`, which every listing
    sync fired in the same request that terminated the member — so the leaver
    sheet, whose whole purpose is to settle up, printed their claims against a
    BLANK allocation."""
    with SessionLocal() as s:
        assign_flex_membership(s, PY_ID, CLIENT_ID)
        s.commit()
        emp = s.get(Employee, EMP_LEAVER)
        assert emp.flex_wallet_amount == 600.0  # 1200 annual, 6/12 months
        assert emp.flex_proration["full_amount"] == 1200.0
        assert (emp.flex_proration["served"], emp.flex_proration["total"]) == (6, 12)


def test_a_leavers_family_status_survives_their_household_terminating():
    """The listing sync terminates a leaver's dependants in the same apply.
    Counting active dependants alone resolves every leaver as Single and
    silently HALVES the settlement figure their sheet is read for."""
    with SessionLocal() as s:
        assign_flex_membership(s, PY_ID, CLIENT_ID)
        s.commit()
        emp = s.get(Employee, EMP_FAMILY)
        assert emp.flex_family_status == "M1C"
        assert emp.flex_proration["full_amount"] == 2400.0
        assert emp.flex_wallet_amount == 1200.0


def test_leavers_do_not_inflate_the_tier_headcounts():
    """Assignments cover everyone; the COUNTS are active-only, or the flex
    overview reports leavers as eligible members."""
    from app.services.flex_membership import compute_flex_membership

    with SessionLocal() as s:
        m = compute_flex_membership(s, PY_ID, CLIENT_ID, include_terminated=True)
        assert len(m.assignments) == 2
        assert sum(t.eligible for t in m.tiers) == 0


def test_a_full_period_member_stores_no_derivation():
    """`prorate` returning a result means "this WAS pro-rated" — every consumer
    reads it that way. A member covered the whole period would otherwise print an
    Annual Allocation identical to the figure beside it, "12/12 months" on every
    report row, and a pro-ration note on the wallet page of someone who was there
    all year."""
    assert fp.prorate(1200.0, _Member(), YEAR, _cfg()) is None
    assert fp.prorate(1200.0, _Member(effective="2039-01-01"), YEAR, _cfg()) is None


def test_an_active_employee_is_never_cut_by_a_stale_leaving_date():
    """`Last Day of Service` is a column of the member-listing template, so it
    round-trips on every sync and an ACTIVE row can carry a stale past date —
    which is exactly why `adc.py` terminates only on a NEWLY stated one. Reading
    the date alone silently cuts a live employee's wallet AND every price tag
    drawn against it."""
    def member(status: str):
        m = _Member(last_day=date(2039, 3, 31))
        m.status = status
        return m

    assert fp.prorate(1200.0, member("active"), YEAR, _cfg()) is None
    assert fp.prorate(1200.0, member("terminated"), YEAR, _cfg()).served == 3


def test_applies_to_defaults_to_leavers_when_a_legacy_extraction_omits_it():
    """`applies_to` did not exist before this feature, so a stored `basis` with
    no `applies_to` is a value AI extracted from a document nobody could review.
    Defaulting it to `both` would invent a decision and cut every joiner."""
    cfg = fp.proration_config({"eligibility": {"proration": {"basis": "months_served"}}})
    assert cfg.applies_to == fp.APPLIES_LEAVERS
    assert cfg.prorates_leavers and not cfg.prorates_joiners
