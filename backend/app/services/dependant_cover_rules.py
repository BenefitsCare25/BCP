"""Which of a member's dependants a product's cover includes.

Every place that works out covered dependants (per-plan headcounts, the Full
EL, benefit statements, insurer listings, limit alerts) first decides how the
plan takes dependants: the member's election, the uploaded Employee Listing,
then the plan's dependant mode. Two rules then apply to all of them:

* The product's Eligibility ticks. A spouse or child is covered only when that
  relationship is ticked, because the slip is what the insurer agreed to
  cover. A setup saved before the ticks existed has no restriction, and a
  dependant whose relationship the roster doesn't state stays covered (and is
  flagged) rather than being dropped silently.
* The broker's decision on one dependant (`DependantCoverDecision`): an
  ``exclude`` takes them off that product. A ``keep`` only records why an
  over-age dependant stays covered.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DependantCoverDecision, ListingAssignment, ProductSetup
from app.models.dependant_cover_decision import DECISION_EXCLUDE
from app.models.employee_listing import FAMILY_TIER_EXCLUDED_KEY
from app.services.roster_attributes import relationship_role

# Eligibility tokens that name dependants without saying which kind.
_ANY_DEPENDANT = frozenset({"dependant", "dependants", "dependent", "dependents", "family"})


class DependantLike(Protocol):
    id: str
    attribute_values: dict[str, Any] | None


def cover_roles(answers: Any) -> frozenset[str] | None:
    """The dependant relationships a setup's Eligibility ticks ("spouse",
    "child"); None when the setup never answered or left it blank, so nothing
    is restricted. A blank answer is a gap the setup checklist reports, not a
    decision that only employees are covered."""
    eligibility = answers.get("eligibility") if isinstance(answers, dict) else None
    if not isinstance(eligibility, dict):
        return None
    raw = eligibility.get("member_cover_eligibility")
    parts = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
    ticks = {str(part).strip().lower() for part in parts if str(part).strip()}
    if not ticks:
        return None
    roles: set[str] = set()
    if ticks & _ANY_DEPENDANT or "spouse" in ticks:
        roles.add("spouse")
    if ticks & _ANY_DEPENDANT or ticks & {"child", "children"}:
        roles.add("child")
    return frozenset(roles)


@dataclass(frozen=True)
class CoverRules:
    """One policy year's Eligibility ticks and broker decisions."""

    roles: dict[str, frozenset[str] | None] = field(default_factory=dict)
    decisions: dict[tuple[str, str], DependantCoverDecision] = field(default_factory=dict)

    def relationship_allowed(self, product_code: str | None, values: dict[str, Any] | None) -> bool:
        roles = self.roles.get(str(product_code or "").strip().upper())
        if roles is None:
            return True
        role = relationship_role(values)
        return role is None or role in roles

    def restricts(self, product_code: str | None) -> bool:
        """The product's ticks leave out a spouse or a child."""
        roles = self.roles.get(str(product_code or "").strip().upper())
        return roles is not None and roles != {"spouse", "child"}

    def decision(self, dependant_id: str, product_id: str | None) -> DependantCoverDecision | None:
        return self.decisions.get((dependant_id, product_id or ""))

    def excluded(self, dependant_id: str, product_id: str | None) -> bool:
        found = self.decision(dependant_id, product_id)
        return found is not None and found.decision == DECISION_EXCLUDE

    def allows(self, dep: DependantLike, product_id: str | None, product_code: str | None) -> bool:
        return self.relationship_allowed(product_code, dep.attribute_values) and not self.excluded(
            dep.id, product_id
        )

    def filter[T: DependantLike](
        self, deps: Iterable[T], product_id: str | None, product_code: str | None
    ) -> list[T]:
        return [dep for dep in deps if self.allows(dep, product_id, product_code)]


NO_RULES = CoverRules()


def load_cover_rules(db: Session, policy_year_id: str) -> CoverRules:
    roles = {
        str(code or "").strip().upper(): cover_roles(answers)
        for code, answers in db.execute(
            select(ProductSetup.product_code, ProductSetup.answers).where(
                ProductSetup.policy_year_id == policy_year_id
            )
        ).all()
    }
    decisions = {
        (row.dependant_id, row.product_id): row
        for row in db.scalars(
            select(DependantCoverDecision).where(
                DependantCoverDecision.policy_year_id == policy_year_id
            )
        )
    }
    return CoverRules(roles=roles, decisions=decisions)


@dataclass(frozen=True)
class ListedCover:
    """What the uploaded Employee Listing says about dependants' cover."""

    # (dependant id, product id) -> covered under that product.
    covered: dict[tuple[str, str], bool] = field(default_factory=dict)
    # Every dependant the listing names under any product.
    named: frozenset[str] = frozenset()


def load_listed_cover(db: Session, policy_year_id: str) -> ListedCover:
    covered: dict[tuple[str, str], bool] = {}
    for dep_id, product_id, recorded in db.execute(
        select(
            ListingAssignment.dependant_id,
            ListingAssignment.product_id,
            ListingAssignment.recorded,
        ).where(
            ListingAssignment.policy_year_id == policy_year_id,
            ListingAssignment.dependant_id.is_not(None),
        )
    ):
        covered[(dep_id, product_id)] = not (recorded or {}).get(FAMILY_TIER_EXCLUDED_KEY)
    return ListedCover(covered=covered, named=frozenset(dep_id for dep_id, _ in covered))


def plan_covered_dependants[T: DependantLike](
    household: Iterable[T],
    mode: str | None,
    elected: list[str] | None,
    product_id: str | None,
    listed: ListedCover,
) -> list[T]:
    """How the plan takes the household's dependants, before the rules above:
    the member's election, then the uploaded listing (covered unless the family
    tier excludes them; a dependant listed only under other products is not),
    then the plan's dependant mode. Mirrors the Full EL's
    `full_el.listing_sheet._dependant_covered`."""
    if elected is not None:
        chosen = set(elected)
        return [dep for dep in household if dep.id in chosen]
    out: list[T] = []
    for dep in household:
        included = listed.covered.get((dep.id, product_id or ""))
        if included is None:
            included = dep.id not in listed.named and mode == "compulsory"
        if included:
            out.append(dep)
    return out
