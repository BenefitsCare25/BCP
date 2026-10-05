"""Product-level roster matching gaps within each product's insured entities.

Uses persisted assignments: a declined election is still a matched category.
Entity gates are the same gates used by matching and setup's member preview.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, PolicyYear, Product
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
        return {
            pid
            for pid, gates in self.gates.items()
            if any(_entity_allows(gate, entities) for gate in gates)
        }

    def missing(self, employee: Employee, codes: set[str] | None = None) -> set[str]:
        expected = self.expected(employee)
        if codes:
            expected = {pid for pid in expected if self.codes[pid].casefold() in codes}
        return {self.codes[pid] for pid in expected - self.matched(employee)}


def build_coverage_gaps(db: Session, py: PolicyYear) -> CoverageGaps:
    categories = [
        category
        for category in db.scalars(select(Category).where(Category.policy_year_id == py.id))
        if (category.plan_assignments or {}).get("member_scope") != "dependant"
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
    return CoverageGaps(
        categories={c.id: c for c in categories},
        codes={pid: p.code for pid, p in products.items()},
        gates=gates,
        aliases=aliases,
    )
