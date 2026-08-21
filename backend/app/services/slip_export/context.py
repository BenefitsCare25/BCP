"""Everything one slip export needs, loaded once and resolved per category.

The renderers are pure: they receive a :class:`SlipContext` and never query. All
of the "where does this number come from" policy lives here, in one place, so
the Basis-of-Cover table, the Rate section and the Overview totals can never
publish three different headcounts for the same category.

The central decision is :func:`figures_for`. A category carries TWO headcounts:

* the figure the insurer's slip stated when it was parsed — frozen at parse
  time, and the only figure that existed before this module;
* the roster's answer today, via ``category_member_counts``.

The roster wins whenever it has anyone, because the document is going back out
to an insurer and must describe the group as it is now. It never overwrites a
stated figure with a **zero**: a category matching nobody is far more often a
gap in matching (or a voluntary tier nobody has elected yet) than a genuinely
empty cohort, and publishing "0 lives" against a cover the slip priced for 56
would be worse than reprinting the slip. Every such fallback is counted and
surfaced on the sheet, so the broker sees which rows are stale rather than
having to trust the document blindly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Category, Plan, PolicyYear, Product, ProductSetup, ProductTerm
from app.services.category_member_counts import build_category_member_counts
from app.services.plan_hydration import basis_amount
from app.services.product_insurer import insurer_from_answers

Mode = Literal["placement", "quotation"]

# Where a printed member count came from.
SOURCE_ROSTER = "roster"
SOURCE_SLIP = "slip"
SOURCE_NONE = "none"


def _natural_code_key(value: str) -> tuple[tuple[int, int | str], ...]:
    """Sort plan codes for people: 1, 2, 10, D01, not 1, 10, 2, D01."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", str(value or ""))
        if part
    )


@dataclass(frozen=True)
class CategoryFigures:
    """The member/amount figures to PRINT for one category."""

    members: int | None = None
    dependants: int | None = None
    tier_counts: dict[str, int] = field(default_factory=dict)
    sum_insured: float | None = None
    source: str = SOURCE_NONE
    # True when the sum insured was rebuilt from the live headcount. False on a
    # roster-sourced row whose basis resolved to no amount (salary-relative with
    # a member missing a salary, or relative to another product): the count is
    # live but the cover beside it is still the slip's, and the two therefore
    # describe different populations. Surfaced in the table footnote.
    cover_from_roster: bool = False

    @property
    def from_roster(self) -> bool:
        return self.source == SOURCE_ROSTER

    @property
    def cover_is_stale(self) -> bool:
        return (
            self.from_roster
            and self.sum_insured is not None
            and not self.cover_from_roster
        )


@dataclass
class SlipContext:
    """One policy year's configuration, resolved for rendering."""

    policy_year: PolicyYear
    mode: Mode
    products: list[Product] = field(default_factory=list)
    cats_by_product: dict[str | None, list[Category]] = field(default_factory=dict)
    plans_by_product: dict[str, list[Plan]] = field(default_factory=dict)
    terms: dict[str, ProductTerm] = field(default_factory=dict)
    # product code (upper) → the guided-setup answers captured for it. The
    # header/eligibility wording the broker entered lives here and nowhere else.
    answers_by_code: dict[str, dict[str, Any]] = field(default_factory=dict)
    figures: dict[str, CategoryFigures] = field(default_factory=dict)

    @property
    def blank_rates(self) -> bool:
        """Quotation mode leaves every rate/premium cell for the quoting insurer."""
        return self.mode == "quotation"

    def answers_for(self, product: Product | None) -> dict[str, Any]:
        if product is None:
            return {}
        return self.answers_by_code.get((product.code or "").upper(), {})

    def insurer_for(self, product: Product | None) -> str:
        """The insurer this benefit year places the product with — resolved by
        the one rule in ``services/product_insurer`` (Header & Policy answer,
        legacy catalog value as fallback), off the answers already loaded."""
        return insurer_from_answers(self.answers_for(product), product)

    def figures_for(self, category: Category) -> CategoryFigures:
        """Resolved figures for a category, computing them if it wasn't loaded.

        The fallback is what makes the renderers safe to call with a category
        the context never saw: it still gets the stored (slip-stated) figures
        rather than silently rendering a row with every amount blank.
        """
        cached = self.figures.get(category.id)
        if cached is not None:
            return cached
        return _resolve_figures(category, {})

    def stale_categories(self, categories: list[Category]) -> int:
        """How many of these rows fell back to the slip's stated headcount."""
        return sum(
            1 for c in categories if self.figures_for(c).source == SOURCE_SLIP
        )


def _plan_assignments(cat: Category) -> dict[str, Any]:
    return cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}


def _resolve_figures(
    cat: Category, live: dict[str, Any]
) -> CategoryFigures:
    """Pick the figures to print for one category — see the module docstring."""
    pa = _plan_assignments(cat)
    stored_members = pa.get("num_employees")
    stored_tiers = pa.get("tier_counts")
    stored_si = pa.get("sum_insured")

    members = live.get("employees", 0) if live else 0
    if members:
        # Roster wins, and the group sum insured is rebuilt from it — the stored
        # SI is ``stated headcount x basis``, so keeping it would pair a live
        # headcount with cover computed from a different one (and the
        # per-S$1,000 premium derived from it would be wrong too).
        #
        # A plain per-member basis multiplies out here. A SALARY-RELATIVE basis
        # ("12 times basic monthly salary") has no per-member amount, so the
        # counter summed each member's own resolved figure instead. Only when
        # neither resolves does the stated aggregate stand — it is the sole
        # figure available, and dropping it would leave the insurer with no
        # cover amount at all.
        per_member = basis_amount(pa)
        roster_si = live.get("sum_insured")
        if per_member is not None:
            sum_insured = round(members * per_member, 2)
            rebuilt = True
        elif roster_si is not None:
            sum_insured = roster_si
            rebuilt = True
        else:
            sum_insured = stored_si
            rebuilt = False
        return CategoryFigures(
            members=members,
            dependants=live.get("dependants") or None,
            tier_counts=dict(live.get("tier_counts") or {}),
            sum_insured=sum_insured,
            source=SOURCE_ROSTER,
            cover_from_roster=rebuilt,
        )
    if stored_members is not None:
        return CategoryFigures(
            members=stored_members,
            dependants=None,
            tier_counts=dict(stored_tiers or {}),
            sum_insured=stored_si,
            source=SOURCE_SLIP,
        )
    # Nothing stated and nobody matched — a voluntary option nobody has elected.
    # Its cover amount still prints; the count cell stays blank, as on the slips.
    return CategoryFigures(sum_insured=stored_si, source=SOURCE_NONE)


def load_context(db: Session, py: PolicyYear, mode: Mode) -> SlipContext:
    categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == py.id)
            .order_by(Category.priority)
        ).scalars()
    )
    plans = sorted(
        db.execute(
            select(Plan).where(Plan.policy_year_id == py.id).order_by(Plan.code)
        ).scalars(),
        key=lambda plan: _natural_code_key(plan.code),
    )
    terms = {
        t.product_id: t
        for t in db.execute(
            select(ProductTerm).where(ProductTerm.policy_year_id == py.id)
        ).scalars()
    }
    answers_by_code = {
        (s.product_code or "").upper(): (s.answers or {})
        for s in db.execute(
            select(ProductSetup).where(ProductSetup.policy_year_id == py.id)
        ).scalars()
    }

    product_ids = {c.product_id for c in categories if c.product_id}
    product_ids |= {p.product_id for p in plans}
    products = sorted(
        db.execute(
            select(Product).where(
                Product.id.in_(product_ids),
                # Product mixes global + per-client rows — scope to this tenant
                # (plus global catalog) even though the ids come from this year's
                # already-scoped categories/plans (defense in depth).
                tenant_or_global(Product.client_id, py.client_id),
            )
        ).scalars()
        if product_ids
        else [],
        key=lambda p: p.code,
    )

    cats_by_product: dict[str | None, list[Category]] = {}
    for c in categories:
        cats_by_product.setdefault(c.product_id, []).append(c)
    plans_by_product: dict[str, list[Plan]] = {}
    for p in plans:
        plans_by_product.setdefault(p.product_id, []).append(p)

    live = build_category_member_counts(db, py.id)
    figures = {
        c.id: _resolve_figures(
            c,
            {
                "employees": m.employees,
                "dependants": m.dependants,
                "tier_counts": m.tier_counts,
                "sum_insured": m.sum_insured,
            }
            if (m := live.get(c.id))
            else {},
        )
        for c in categories
    }

    return SlipContext(
        policy_year=py,
        mode=mode,
        products=products,
        cats_by_product=cats_by_product,
        plans_by_product=plans_by_product,
        terms=terms,
        answers_by_code=answers_by_code,
        figures=figures,
    )
