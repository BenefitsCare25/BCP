from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AuditLog, BrokerFirm, Claim, Client, Employee, PolicyYear, User
from app.services.claim_workload import claim_workload


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BrokerFirm(id="firm", name="Test firm"))
        session.flush()
        session.add_all([Client(id=k, name=k, broker_firm_id="firm") for k in ("one", "two")])
        session.add(
            User(
                id="broker",
                email="test@example.test",
                display_name="Test broker",
                broker_firm_id="firm",
                role="broker_admin",
                status="active",
            )
        )
        session.flush()
        yield session
    engine.dispose()


def event(db, action, claim, **overrides):
    values = dict(
        client_id="one",
        user_id="broker",
        actor_type="user",
        action=action,
        entity_type="claim",
        entity_id=claim,
        created_at=datetime(2026, 9, 18, 16, tzinfo=UTC),
    )
    values.update(overrides)
    db.add(AuditLog(**values))
    db.flush()


def test_actions_distinct_claims_and_actor_separation(db):
    event(db, "claim.needs_info", "claim1")
    event(db, "claim.approve", "claim1")
    event(db, "claim.reject", "claim2")
    event(db, "claim.approve", "claim3", user_id=None, actor_type="system")
    event(db, "claim.amended", "claim4", user_id=None, actor_type="member")
    event(db, "claim.approve", "foreign", client_id="two")
    result = claim_workload(db, "one", date(2026, 9, 19), date(2026, 9, 19))["items"]
    assert len(result) == 2
    human = next(r for r in result if r["actor_type"] == "human")
    assert human["name"] == "Test broker"
    assert human["claims_handled"] == 2
    assert human["actions"] == 3
    assert human["repeat_actions"] == 1
    assert human["by_status"] == {"needs_info": 1, "approved": 1, "rejected": 1}
    assert human["average_decision_hours"] is None


def test_singapore_date_boundaries_and_nonmutation_events(db):
    event(db, "claim.approve", "before", created_at=datetime(2026, 9, 18, 15, 59, tzinfo=UTC))
    event(db, "claim.approve", "start")
    event(db, "claim.approve", "end", created_at=datetime(2026, 9, 19, 15, 59, tzinfo=UTC))
    event(db, "claim.approve", "after", created_at=datetime(2026, 9, 19, 16, tzinfo=UTC))
    event(db, "claim.viewed", "view")
    rows = claim_workload(db, "one", date(2026, 9, 19), date(2026, 9, 19))["items"]
    assert rows[0]["actions"] == 2


def test_decision_duration_uses_recorded_submission_and_excludes_earlier_events(db):
    db.add(
        PolicyYear(
            id="year",
            client_id="one",
            year=2026,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
    )
    db.flush()
    db.add(Employee(id="ee", client_id="one", policy_year_id="year", staff_id="one"))
    db.flush()
    db.add(
        Claim(
            id="claim",
            client_id="one",
            policy_year_id="year",
            employee_id="ee",
            claim_kind="insured",
            claim_type="GP",
            amount_claimed=10,
            incurred_date=date(2026, 9, 1),
            status="approved",
            submitted_at=datetime(2026, 9, 18, 14),
        )
    )
    db.flush()
    event(db, "claim.approve", "claim", created_at=datetime(2026, 9, 18, 16))
    event(db, "claim.reject", "claim", created_at=datetime(2026, 9, 18, 13))
    result = claim_workload(db, "one", date(2026, 9, 18), date(2026, 9, 19))["items"][0]
    assert result["average_decision_hours"] == 2
    assert result["decision_samples"] == 1
