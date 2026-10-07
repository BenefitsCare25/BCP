"""Product-level roster matching gaps within each product's insured entities.

Uses persisted assignments: a declined election is still a matched category.
Entity gates are the same gates used by matching and setup's member preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, PolicyYear, Product
from app.models.employee_listing import ListingAssignment
from app.services.matching_engine import (
    _entity_allows,
    category_entity_gate,
    employee_entity,
    entity_alias_map,
    product_entities,
)


@dataclass
class CoverageGaps:
    categories: dict[str, Category]
    codes: dict[str, str]
    gates: dict[str, list[frozenset[str]]]
    aliases: dict[str, frozenset[str]]
    # The company's Employee Listing: products it reports on, and per listed
    # employee the products it gives them cover under. A listed product
    # without cover for that person is not applicable to them (e.g. a
    # directors' policy for a bus captain), so it is no gap.
    listed_products: frozenset[str] = frozenset()
    listed_cover: dict[str, frozenset[str]] = field(default_factory=dict)

    def matched(self, employee: Employee) -> set[str]:
        return {
            category.product_id
            for match in employee.matched_categories or []
            if isinstance(match, dict)
            and (category := self.categories.get(str(match.get("category_id") or "")))
            and category.product_id is not None
        }

    def expected(self, employee: Employee) -> set[str]:
        entities = employee_entity(employee.attribute_values, self.aliases)
        expected = {
            pid
            for pid, gates in self.gates.items()
            if any(_entity_allows(gate, entities) for gate in gates)
        }
        if employee.id in self.listed_cover:
            expected -= self.listed_products - self.listed_cover[employee.id]
        return expected

    def missing(self, employee: Employee, codes: set[str] | None = None) -> set[str]:
        expected = self.expected(employee)
        if codes:
            expected = {pid for pid in expected if self.codes[pid].casefold() in codes}
        return {self.codes[pid] for pid in expected - self.matched(employee)}


def build_coverage_gaps(db: Session, py: PolicyYear) -> CoverageGaps:
    # Dependant-only rows, and payroll-rated (WICA) rows that insure a
    # workforce's estimated earnings rather than named people, place no
    # employee, so they are no employee's gap.
    categories = [
        category
        for category in db.scalars(select(Category).where(Category.policy_year_id == py.id))
        if (category.plan_assignments or {}).get("member_scope") != "dependant"
        and (category.plan_assignments or {}).get("rate_basis") != "earnings_based"
    ]
    products = {
        p.id: p
        for p in db.scalars(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories if c.product_id})
            )
        )
    }
    aliases = entity_alias_map(db, py.client_id)
    gates: dict[str, list[frozenset[str]]] = {}
    for category in categories:
        if category.product_id not in products:
            continue
        pid = str(category.product_id)
        gates.setdefault(pid, []).append(
            category_entity_gate(category, product_entities(products[pid], aliases), aliases)
        )
    listed_products: set[str] = set()
    listed_cover: dict[str, set[str]] = {}
    for employee_id, product_id, category_id in db.execute(
        select(
            ListingAssignment.employee_id,
            ListingAssignment.product_id,
            ListingAssignment.category_id,
        ).where(
            ListingAssignment.policy_year_id == py.id,
            ListingAssignment.dependant_id.is_(None),
        )
    ):
        listed_products.add(product_id)
        covered = listed_cover.setdefault(employee_id, set())
        if category_id is not None:
            covered.add(product_id)
    return CoverageGaps(
        categories={c.id: c for c in categories},
        codes={pid: p.code for pid, p in products.items()},
        gates=gates,
        aliases=aliases,
        listed_products=frozenset(listed_products),
        listed_cover={k: frozenset(v) for k, v in listed_cover.items()},
    )
