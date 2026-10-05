"""Partial product matching, entity scope and enrolment remediation stay aligned."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import BrokerFirm, Category, Client, Employee, PolicyYear, Product
from app.schemas.member_query import MemberFilters, MemberQuery
from app.services.coverage_gaps import build_coverage_gaps
from app.services.member_query import build_facets, resolve_listing, resolve_selection


def test_partial_gaps_are_visible_in_listing_and_bulk_selection():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        db.add(Client(id="client", name="Client", broker_firm_id="firm"))
        db.flush()
        py = PolicyYear(
            id="year",
            client_id="client",
            year=2032,
            start_date=date(2032, 1, 1),
            end_date=date(2032, 12, 31),
        )
        db.add(py)
        for code in ("MED", "DENTAL", "EMPTY"):
            db.add(
                Product(
                    id=code,
                    client_id="client",
                    code=code,
                    display_name=code,
                    product_metadata={"entities": ["Entity A"]} if code != "MED" else None,
                )
            )
        db.flush()
        for code in ("MED", "DENTAL", "EMPTY"):
            db.add(
                Category(
                    id=f"cat-{code}",
                    policy_year_id=py.id,
                    product_id=code,
                    display_name="All employees",
                    raw_description="All employees",
                    plan_assignments={},
                    status="confirmed",
                    source="system_generated",
                )
            )
        db.flush()
        rows = [
            ("PARTIAL", "Entity A", ["MED"]),
            ("FULL", "Entity A", ["MED", "DENTAL", "EMPTY"]),
            ("OUTSIDE", "Entity B", ["MED"]),
            ("NONE", "Entity A", []),
        ]
        for staff, entity, codes in rows:
            db.add(
                Employee(
                    id=staff,
                    client_id="client",
                    policy_year_id=py.id,
                    staff_id=staff,
                    attribute_values={"entity": entity, "category": "Executives"},
                    derived_attribute_values={},
                    source="csv_import",
                    status="active",
                    matched_category_id="cat-MED" if codes else None,
                    matched_categories=[{"category_id": f"cat-{c}"} for c in codes],
                )
            )
        db.flush()
        gaps = build_coverage_gaps(db, py)
        partial = db.get(Employee, "PARTIAL")
        outside = db.get(Employee, "OUTSIDE")
        assert gaps.missing(partial) == {"DENTAL", "EMPTY"}
        assert gaps.missing(outside) == set()
        # A dangling assignment must not conceal an unassigned product.
        partial.matched_categories = [
            *partial.matched_categories,
            {"category_id": "deleted", "product_code": "DENTAL"},
        ]
        filters = MemberFilters(match_status="unmatched", product_codes=["dental"])
        selected, _ = resolve_listing(db, py, filters)
        assert {e.staff_id for e in selected} == {"PARTIAL", "NONE"}
        selection = resolve_selection(db, py, MemberQuery(**filters.model_dump()))
        assert selection.ids == [e.id for e in selected]
        global_unmatched, _ = resolve_listing(db, py, MemberFilters(match_status="unmatched"))
        assert {e.staff_id for e in global_unmatched} == {"PARTIAL", "NONE"}
        matched, _ = resolve_listing(db, py, MemberFilters(match_status="matched"))
        assert {e.staff_id for e in matched} == {"FULL", "OUTSIDE"}
        combined, _ = resolve_listing(
            db,
            py,
            MemberFilters(
                match_status="unmatched",
                product_codes=["DENTAL"],
                attributes=[{"key": "category", "values": ["executives"]}],
            ),
        )
        assert {e.staff_id for e in combined} == {"PARTIAL", "NONE"}
        assert {p.code for p in build_facets(db, py).products} == {"MED", "DENTAL", "EMPTY"}
    engine.dispose()
