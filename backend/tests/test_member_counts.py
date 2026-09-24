from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Category, Client, Employee, PolicyYear, Product
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
    assert result.employees_in_scope == 0


def test_unmatched_grade_count_is_scoped_to_product_entities() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        db.add(Client(id="client", name="Client", broker_firm_id="firm"))
        db.add(Client(id="other-client", name="Other Client", broker_firm_id="firm"))
        db.add(PolicyYear(
            id="year", client_id="client", year=2026,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        ))
        product = Product(
            id="product", client_id="client", code="GTL", display_name="Life",
            product_metadata={"entities": ["Entity A"]},
        )
        category = Category(
            id="category", policy_year_id="year", product_id="product",
            display_name="Senior (Job category: A1)",
            raw_description="Senior (Job category: A1)",
            matching_rule={"=": ["job_grade", "A1"]},
            plan_assignments={"insured": ["Entity A"]},
        )
        db.add_all([product, category])
        db.flush()
        for staff_id, grade, entity in (
            ("covered", "A1", "Entity A"),
            ("gap", "E10", "Entity A"),
            ("other-entity", "E10", "Entity B"),
        ):
            db.add(Employee(
                client_id="client", policy_year_id="year", staff_id=staff_id,
                status="active",
                attribute_values={"job_grade": grade, "entity": entity},
            ))
        db.add(Employee(
            client_id="other-client", policy_year_id="year", staff_id="foreign-client",
            status="active", attribute_values={"job_grade": "E10", "entity": "Entity A"},
        ))
        db.commit()

        result = compute_member_counts(
            db, "year", "client", False,
            [DraftCategory("category", category.raw_description, ["Entity A"])],
            product_id=product.id,
        )

    assert result.employees_total == 3
    assert result.employees_in_scope == 2
    assert result.employees_matched == 1
    assert result.unmatched_grades == {"E10": 1}
    assert [(employee.staff_id, employee.grade) for employee in result.unmatched_employees] == [
        ("gap", "E10")
    ]
