"""What the blank QR form offers: every employee cohort's plans and premiums.

The member e-form is built for ONE known employee. A blank form cannot know
who is filling it in, so — like the paper forms — it lists each cohort
("Senior Management — Plan 1", "Managers and others — Plan 2") with its plans
and the employee's premium share, and the visitor picks their own. The figures
come from the same functions the member form uses (`cohort_tier_sets`,
`per_member_financials`, GST gross-up, `product_contributions`, `plan_facts`),
so a premium on the QR form equals the one the member portal shows.

Premiums that depend on the person (age-banded or salary-based cover) have no
company-wide figure; ``contribution_tier`` returns nothing for them and the
form says the premium is calculated after verification.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Client, EnrollmentWindow, Plan, PolicyYear, Product
from app.schemas.enrollment import CohortTierOut, ProductTierSetOut
from app.schemas.enrollment_forms import CompulsoryLineOut, FormClause, FormSettings
from app.schemas.qr_enrolment import (
    PublicCohortOut,
    PublicDocumentOut,
    PublicFormOut,
    PublicProductOut,
)
from app.services.cohort_tiers import (
    CohortTier,
    ProductTierSet,
    cohort_key,
    cohort_tier_sets,
    tier_key,
)
from app.services.enrollment_elections import per_member_financials
from app.services.enrollment_forms.config import resolve_settings, scheme_age_limits
from app.services.enrollment_forms.context import age_notes, intro_lines
from app.services.enrollment_forms.pricing import plan_facts, product_contributions
from app.services.flex_pricing.bag import get_pricing, product_premium_multiplier
from app.services.flex_pricing.dependant_profiles import (
    dependant_age_limits,
    effective_dependant_participation,
)
from app.services.plan_hydration import apply_gst_to_financials
from app.services.plan_labels import member_plan_label

MAX_CHILDREN = 6

# Declarations every QR form adds to the period's own: the visitor is not
# signed in, so they confirm who they are and consent to the data use.
QR_CLAUSES: tuple[FormClause, ...] = (
    FormClause(
        id="qr_identity",
        text=(
            "I am the employee named in this form. I understand it is checked against "
            "the company's records before any change to my cover takes effect."
        ),
    ),
    FormClause(
        id="qr_personal_data",
        text=(
            "I consent to the company, its insurance broker and the insurers collecting, "
            "using and disclosing the personal data in this form to administer my group "
            "insurance enrolment."
        ),
    ),
)


@dataclass(frozen=True)
class Catalog:
    """The form's figures, shared by the page (`PublicFormOut`) and the server
    checks that price a submission."""

    settings: FormSettings
    products: list[PublicProductOut]
    automatic: list[CompulsoryLineOut]
    tier_sets: list[ProductTierSetOut]
    names: dict[str, str]

    def cohort(self, product_code: str, category_id: str) -> PublicCohortOut | None:
        product = next((p for p in self.products if p.product_code == product_code), None)
        if product is None:
            return None
        return next((c for c in product.cohorts if c.category_id == category_id), None)

    def clauses(self) -> list[FormClause]:
        return [*self.settings.clauses, *QR_CLAUSES]


def _label_map(
    db: Session, policy_year_id: str, product_ids: set[str]
) -> dict[tuple[str, str], str]:
    if not product_ids:
        return {}
    return {
        (plan.product_id, plan.code): member_plan_label(plan)
        for plan in db.scalars(select(Plan).where(
            Plan.policy_year_id == policy_year_id, Plan.product_id.in_(product_ids),
        ))
    }


def _tier_out(
    t: CohortTier,
    ts: ProductTierSet,
    labels: dict[tuple[str, str], str],
    pricing: dict[str, object] | None,
) -> CohortTierOut:
    key = tier_key(t.tier_category_id, t.plan_code)
    label = labels.get((ts.product_id, t.plan_code or "")) or t.label
    same_plan = sum(other.plan_code == t.plan_code for other in ts.tiers)
    if same_plan > 1 and t.label != label:
        label = f"{label} · {t.label}"
    return CohortTierOut(
        key=key,
        tier_category_id=t.tier_category_id,
        plan_code=t.plan_code,
        label=label,
        participation=t.participation,
        dependant_participation=effective_dependant_participation(
            pricing, ts.product_id, key, t.dependant_participation or ts.dependant_participation,
        ),
        direction=t.direction,
        is_baseline=t.is_baseline,
        financials=(
            apply_gst_to_financials(
                per_member_financials(t.financials),
                product_premium_multiplier(pricing, ts.product_id),
            )
            if t.financials is not None
            else None
        ),
    )


def _set_out(
    ts: ProductTierSet,
    name: str | None,
    window: EnrollmentWindow,
    labels: dict[tuple[str, str], str],
    pricing: dict[str, object] | None,
) -> ProductTierSetOut:
    tiers = [_tier_out(t, ts, labels, pricing) for t in ts.tiers]
    baseline = next((t for t in tiers if t.is_baseline), None)
    return ProductTierSetOut(
        product_id=ts.product_id,
        product_code=ts.product_code,
        product_name=name,
        employee_participation=ts.employee_participation,
        dependant_participation=baseline.dependant_participation if baseline else None,
        baseline_tier_category_id=ts.baseline_tier_category_id,
        baseline_plan_code=ts.baseline_plan_code,
        allow_plan_change=ts.allow_plan_change and window.allow_plan_change,
        can_decline=ts.can_decline,
        tiers=tiers,
    )


def _electable(sets: list[ProductTierSetOut]) -> bool:
    """Something the employee can choose: voluntary own cover, voluntary family
    cover, or a choice of plans."""
    return any(
        ts.employee_participation == "voluntary"
        or ts.allow_plan_change
        or any(t.dependant_participation == "voluntary" for t in ts.tiers)
        for ts in sets
    )


def build_catalog(db: Session, window: EnrollmentWindow) -> Catalog:
    settings, _ = resolve_settings(db, window)
    pricing = get_pricing(db, window.policy_year_id)
    scope = set(window.product_scope or [])
    # Only the year's product ids and codes are needed here; the tier sets are
    # built per product below, never all at once.
    by_code = {
        str(code): str(pid)
        for pid, code in db.execute(
            select(Product.id, Product.code)
            .join(Category, Category.product_id == Product.id)
            .where(Category.policy_year_id == window.policy_year_id)
            .distinct()
        ).all()
    }
    codes = sorted(code for code in by_code if not scope or code in scope)
    product_ids = {by_code[code] for code in codes}
    rows = db.execute(
        select(Product.id, Product.display_name, Product.insurer).where(
            Product.id.in_(product_ids)
        )
    ).all() if product_ids else []
    names = {str(pid): str(name) for pid, name, _ in rows if name}
    insurers = {str(pid): str(ins) for pid, _, ins in rows if ins}
    labels = _label_map(db, window.policy_year_id, product_ids)

    products: list[PublicProductOut] = []
    automatic: list[CompulsoryLineOut] = []
    tier_sets: list[ProductTierSetOut] = []
    for code in codes:
        pid = by_code[code]
        cohorts = cohort_tier_sets(db, window.policy_year_id, pid, code)
        cohort_out = [
            PublicCohortOut(
                category_id=cat.id,
                label=_cohort_label(cat),
                cohort=cohort_key(cat.raw_description),
                tier_set=_set_out(ts, names.get(pid), window, labels, pricing),
            )
            for cat, ts in sorted(cohorts, key=lambda p: (p[0].priority, p[0].display_name))
        ]
        sets = [c.tier_set for c in cohort_out]
        if not sets:
            continue
        tier_sets.extend(sets)
        if not _electable(sets):
            automatic.append(
                CompulsoryLineOut(product_code=code, product_name=names.get(pid), plan_label=None)
            )
            continue
        products.append(
            PublicProductOut(
                product_code=code,
                product_name=names.get(pid),
                insurer=insurers.get(pid),
                cohorts=cohort_out,
                age_limits=dependant_age_limits(pricing, pid),
                gst_included=any(
                    t.financials is not None and t.financials.gst_included
                    for ts in sets for t in ts.tiers
                ),
            )
        )
    names = {p.product_code: p.product_name or p.product_code for p in products}
    names.update({a.product_code: a.product_name or a.product_code for a in automatic})
    return Catalog(
        settings=settings, products=products, automatic=automatic,
        tier_sets=tier_sets, names=names,
    )


def _without_financials(product: PublicProductOut) -> PublicProductOut:
    return product.model_copy(update={"cohorts": [
        c.model_copy(update={"tier_set": c.tier_set.model_copy(update={"tiers": [
            t.model_copy(update={"financials": None}) for t in c.tier_set.tiers
        ]})})
        for c in product.cohorts
    ]})


def _cohort_label(cat: Category) -> str:
    return (cat.display_name or "").strip() or (cat.raw_description or "").strip() or "Category"


def _documents(settings: FormSettings) -> list[PublicDocumentOut]:
    return [
        PublicDocumentOut(id=d.id, label=d.label, url=d.url, has_file=bool(d.document_id))
        for d in settings.documents
    ]


_FORM_TTL_SECONDS = 60.0
_FORM_CACHE_MAX = 256
_form_cache: dict[str, tuple[float, PublicFormOut]] = {}
_form_cache_lock = threading.Lock()


def cached_open_form(db: Session, window: EnrollmentWindow) -> PublicFormOut:
    """The open form, rebuilt at most once a minute per period and process.

    A shared QR is opened by a whole company within minutes; every load would
    otherwise rebuild the catalog. A setup change shows within a minute.
    Submissions always rebuild, so what is filed is checked against current
    figures."""
    now = time.monotonic()
    with _form_cache_lock:
        hit = _form_cache.get(window.id)
        if hit is not None and hit[0] > now:
            return hit[1]
    form = public_form(db, window, "open", build_catalog(db, window))
    with _form_cache_lock:
        if len(_form_cache) >= _FORM_CACHE_MAX:
            _form_cache.clear()
        _form_cache[window.id] = (now + _FORM_TTL_SECONDS, form)
    return form


def public_form(
    db: Session, window: EnrollmentWindow, status: str, catalog: Catalog | None
) -> PublicFormOut:
    client = db.get(Client, window.client_id)
    company = (client.legal_name or client.name) if client is not None else ""
    base = PublicFormOut(
        status=status,  # type: ignore[arg-type]
        company_name=company,
        window_name=window.name,
        closes_at=window.closes_at,
        opens_at=window.opens_at,
    )
    if status != "open" or catalog is None:
        return base
    year = db.get(PolicyYear, window.policy_year_id)
    settings = catalog.settings
    product_codes = {p.product_code for p in catalog.products} | {
        a.product_code for a in catalog.automatic
    }
    return base.model_copy(update={
        "title": settings.title,
        "window_type": window.window_type,
        "policy_start": year.start_date if year else None,
        "policy_end": year.end_date if year else None,
        "intro_lines": intro_lines(settings, catalog.automatic, window.default_behavior),
        "helpline": settings.helpline,
        "submission_note": settings.submission_note,
        "eligibility_notes": age_notes(scheme_age_limits(db, window.policy_year_id))
        + settings.eligibility_notes,
        "clauses": catalog.clauses(),
        "documents": _documents(settings),
        "rules": [
            r for r in settings.rules
            if r.product_code in product_codes and r.requires_product_code in product_codes
        ],
        # The page needs labels, keys and participation, never the raw
        # financials (group figures for some rate bases); the premiums it shows
        # come from `contributions`, computed above from the full sets.
        "products": [_without_financials(p) for p in catalog.products],
        "automatic": [a.product_name or a.product_code for a in catalog.automatic],
        "contributions": product_contributions(catalog.tier_sets, settings),
        "plans": plan_facts(db, catalog.tier_sets, window.policy_year_id),
        "allow_dependant_changes": window.allow_dependant_changes,
        "max_children": MAX_CHILDREN,
    })
