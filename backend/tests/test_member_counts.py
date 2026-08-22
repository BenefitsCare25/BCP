from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Client, Employee, PolicyYear
from app.services.member_counts import DraftCategory, _collapse_drafts, compute_member_counts


def test_repeated_plan_rows_share_one_employee_category() -> None:
    drafts = [
        DraftCategory("plan-1", "Managers", ["Example Pte Ltd"]),
        DraftCategory("plan-2", " managers ", ["example pte ltd"]),
    ]

    representatives, representative_by_key = _collapse_drafts(drafts)

    assert [draft.key for draft in representatives] == ["plan-1"]
    assert representative_by_key == {"plan-1": "plan-1", "plan-2": "plan-1"}


def test_same_wording_with_different_entities_stays_separate() -> None:
    drafts = [
        DraftCategory("entity-a", "All employees", ["Entity A"]),
        DraftCategory("entity-b", "All employees", ["Entity B"]),
    ]

    representatives, representative_by_key = _collapse_drafts(drafts)

    assert [draft.key for draft in representatives] == ["entity-a", "entity-b"]
    assert representative_by_key == {"entity-a": "entity-a", "entity-b": "entity-b"}


def test_employee_total_excludes_inactive_employees() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        firm = BrokerFirm(id="firm", name="Firm")
        client = Client(id="client", name="Client", broker_firm_id=firm.id)
        year = PolicyYear(
            id="year",
            client_id=client.id,
            year=2026,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        db.add(firm)
        db.flush()
        db.add(client)
        db.flush()
        db.add(year)
        db.flush()
        db.add_all(
            [
                Employee(
                    client_id=client.id,
                    policy_year_id=year.id,
                    staff_id="active",
                    status="active",
                ),
                Employee(
                    client_id=client.id,
                    policy_year_id=year.id,
                    staff_id="terminated",
                    status="terminated",
                ),
            ]
        )
        db.commit()

        result = compute_member_counts(db, year.id, client.id, False, [])

    assert result.employees_total == 1
