"""E-form setup: generated defaults, load/save, and the product list the setup
screen offers.

Nothing here duplicates the policy setup. Products, plans and rates are read
from the slip at render time; the dependant age windows come from
``flex_pricing_resolver.dependant_age_limits`` (the same window coverage and
pricing already apply). What a broker configures is only what the slip cannot
say: the wording, the documents to read, the employee's share of the premium and
cross-product rules.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EnrollmentWindow, Product
from app.models.enrollment_form import EnrollmentFormConfig
from app.schemas.enrollment_forms import (
    FormClause,
    FormConfigOut,
    FormContribution,
    FormProductOut,
    FormRule,
    FormSettings,
)
from app.services.brand import (
    DEFAULT_BRAND,
    DEFAULT_PRODUCT_NAME,
    DEFAULT_SUPPORT_EMAIL,
    Brand,
    resolve_client_brand,
)
from app.services.cohort_tiers import list_product_tiers
from app.services.flex_pricing_resolver import dependant_age_limits, get_pricing

DEFAULT_CLAUSES: tuple[FormClause, ...] = (
    FormClause(
        id="read_guides",
        text=(
            "I have read and understood Your Guide to Health Insurance, the Product "
            "Summary and the Benefit Schedule before applying for voluntary cover."
        ),
    ),
    FormClause(
        id="accurate",
        text=(
            "The information in this form is true and complete. I will tell my HR "
            "team or broker if any of it changes."
        ),
    ),
    FormClause(
        id="premium_share",
        text=(
            "I agree to bear my share of the premium shown in this form, pro-rated "
            "where applicable, by deduction from my salary."
        ),
    ),
    FormClause(
        id="cease_on_leaving",
        text=(
            "I understand that this cover ends when I leave the company, and that "
            "premiums are not refunded if I resign during the policy period."
        ),
    ),
    FormClause(
        id="dependant_consent",
        applies_to="dependants",
        text=(
            "I have the consent of each family member named in this form to share "
            "their personal data with the broker and insurer for this enrolment."
        ),
    ),
    FormClause(
        id="pre_existing",
        applies_to="dependants",
        text=(
            "I understand that pre-existing conditions of newly enrolled family "
            "members, known or unknown, are excluded for the first 12 months."
        ),
    ),
)

DEFAULT_ELIGIBILITY_NOTES: tuple[str, ...] = (
    "Children must be unmarried and not in full-time employment.",
    "Dependants serving National Service or in full-time military service are excluded.",
    "Dependants must be residing with the employee in Singapore.",
)

# The built-in brand's helpline, as printed on every paper form the platform
# owner issued. Editable per period in the form setup.
DEFAULT_HELPLINE = (
    "For any queries on this form, please contact Inspro Insurance Brokers at "
    "6448 7707 (helpdesk@inspro.com.sg)."
)


def default_helpline(brand: Brand) -> str:
    """The generated helpline: the built-in wording until the brand names its
    own product or support contacts."""
    builtin = (DEFAULT_PRODUCT_NAME, DEFAULT_SUPPORT_EMAIL, None)
    if (brand.product_name, brand.support_email, brand.support_phone) == builtin:
        return DEFAULT_HELPLINE
    contact = (
        f"{brand.support_phone} ({brand.support_email})"
        if brand.support_phone
        else brand.support_email
    )
    return f"For any queries on this form, please contact {brand.product_name} at {contact}."

DEFAULT_SUBMISSION_NOTE = (
    "Thank you for your submission. Your broker will acknowledge your enrolment "
    "within 2 weeks."
)

# Supplementary medical cover that is only sold on top of hospital cover. Used
# to SUGGEST a rule in the generated defaults; the broker can remove it.
_RIDES_ON_GHS = ("GMM", "GMM2")


def _product_names(db: Session, codes: list[str], client_id: str) -> dict[str, str]:
    if not codes:
        return {}
    rows = db.execute(
        select(Product.code, Product.display_name, Product.client_id).where(
            Product.code.in_(codes),
            (Product.client_id.is_(None)) | (Product.client_id == client_id),
        )
    ).all()
    names: dict[str, str] = {}
    # A tenant's own product row wins over the platform default of the same code.
    for code, name, _owner in sorted(rows, key=lambda r: r[2] is not None):
        names[code] = name
    return names


def form_products(db: Session, window: EnrollmentWindow) -> list[FormProductOut]:
    tier_sets = list_product_tiers(db, window.policy_year_id)
    scope = set(window.product_scope or [])
    names = _product_names(db, list(tier_sets), window.client_id)
    out: list[FormProductOut] = []
    for code, ts in sorted(tier_sets.items()):
        if scope and code not in scope:
            continue
        participations = {t.participation for t in ts.tiers if t.participation}
        participation = (
            next(iter(participations)) if len(participations) == 1
            else "mixed" if participations else ts.employee_participation
        )
        dep = ({t.dependant_participation for t in ts.tiers} | {ts.dependant_participation}) - {
            None
        }
        out.append(
            FormProductOut(
                product_code=code,
                product_name=names.get(code),
                participation=participation,
                has_dependant_cover=bool(dep),
                dependant_participation=(
                    next(iter(dep)) if len(dep) == 1 else "mixed" if dep else None
                ),
                has_upgrades=len(ts.tiers) > 1,
            )
        )
    return out


def default_contributions(products: list[FormProductOut]) -> dict[str, FormContribution]:
    """Voluntary cover is, by default, paid by the member — the paper forms'
    usual "100% borne by employee". Compulsory cover stays company-paid, but a
    member choosing a HIGHER plan pays the extra (upgrade share 100%). The
    broker adjusts (e.g. GHS dependants 50%) in setup."""
    out: dict[str, FormContribution] = {}
    for p in products:
        employee = 100.0 if p.participation == "voluntary" else None
        dependant = 100.0 if p.dependant_participation == "voluntary" else None
        upgrade = 100.0 if employee is None and p.has_upgrades else None
        if employee is not None or dependant is not None or upgrade is not None:
            out[p.product_code] = FormContribution(
                employee_pct=employee, dependant_pct=dependant, upgrade_pct=upgrade
            )
    return out


def default_settings(
    products: list[FormProductOut], brand: Brand = DEFAULT_BRAND
) -> FormSettings:
    codes = {p.product_code for p in products}
    rules = [
        FormRule(product_code=code, requires_product_code="GHS")
        for code in _RIDES_ON_GHS
        if code in codes and "GHS" in codes
    ]
    return FormSettings(
        clauses=list(DEFAULT_CLAUSES),
        eligibility_notes=list(DEFAULT_ELIGIBILITY_NOTES),
        submission_note=DEFAULT_SUBMISSION_NOTE,
        helpline=default_helpline(brand),
        rules=rules,
        contributions=default_contributions(products),
    )


def find_config(db: Session, window_id: str) -> EnrollmentFormConfig | None:
    return db.execute(
        select(EnrollmentFormConfig).where(EnrollmentFormConfig.window_id == window_id)
    ).scalar_one_or_none()


def resolve_settings(
    db: Session, window: EnrollmentWindow, products: list[FormProductOut] | None = None
) -> tuple[FormSettings, EnrollmentFormConfig | None]:
    """The window's saved settings, else generated defaults. A stored bag that
    no longer validates (schema moved on) falls back to defaults rather than
    breaking the member's form."""
    row = find_config(db, window.id)
    if row is not None:
        try:
            return FormSettings.model_validate(row.settings or {}), row
        except ValidationError:
            pass
    return default_settings(
        products if products is not None else form_products(db, window),
        resolve_client_brand(db, window.client_id),
    ), row


def scheme_age_limits(db: Session, policy_year_id: str) -> dict[str, dict[str, int]]:
    """Product-agnostic dependant age windows (age next birthday)."""
    return dependant_age_limits(get_pricing(db, policy_year_id), "")


def config_out(db: Session, window: EnrollmentWindow) -> FormConfigOut:
    products = form_products(db, window)
    settings, row = resolve_settings(db, window, products)
    return FormConfigOut(
        window_id=window.id,
        is_default=row is None,
        settings=settings,
        products=products,
        age_limits=scheme_age_limits(db, window.policy_year_id),
        updated_at=row.updated_at if row is not None else None,
    )


def save_settings(
    db: Session, window: EnrollmentWindow, settings: FormSettings, user_id: str
) -> EnrollmentFormConfig:
    """Validate references against the year's products, then upsert. Flushes,
    does not commit."""
    known = {p.product_code for p in form_products(db, window)}
    unknown = sorted(
        {code for code in settings.contributions if code not in known}
        | {
            code
            for rule in settings.rules
            for code in (rule.product_code, rule.requires_product_code)
            if code not in known
        }
    )
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Not a product of this benefit period: {', '.join(unknown)}.",
        )
    payload: dict[str, Any] = settings.model_dump(mode="json")
    row = find_config(db, window.id)
    if row is None:
        row = EnrollmentFormConfig(
            client_id=window.client_id,
            policy_year_id=window.policy_year_id,
            window_id=window.id,
        )
        db.add(row)
    row.settings = payload
    row.updated_by = user_id
    db.flush()
    return row
