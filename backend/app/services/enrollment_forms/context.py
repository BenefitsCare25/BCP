"""The member's e-form context — everything the portal form shows beside the
election deck, built from the policy setup at request time.

* particulars — prefilled from the roster (contact fields editable on the form);
* compulsory  — products the member holds without enrolling;
* contributions — per plan, the full premium per family composition and the
  member's SHARE of it, only for products the broker set a share for (see
  ``pricing.py``);
* plans       — every plan's key benefit and sum insured;
* dependants  — every family member on record with an eligibility verdict from
  the same per-product age windows coverage and pricing apply.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Client,
    Dependant,
    Employee,
    Enrollment,
    EnrollmentWindow,
    PolicyYear,
    StoredDocument,
)
from app.models.dependant import DEPENDANT_STATUS_PENDING
from app.models.enrollment_form import EnrollmentFormSubmission
from app.models.stored_document import DOC_ENTITY_FORM_RESOURCE
from app.schemas.enrollment import EnrollmentOptionsOut, ProductTierSetOut
from app.schemas.enrollment_forms import (
    CompulsoryLineOut,
    FormDependantOut,
    FormDocumentOut,
    FormRuleOut,
    FormSettings,
    FormSubmissionSummary,
    MemberFormContextOut,
    ParticularsOut,
)
from app.services.enrollment_elections import build_enrollment_options, enrollment_detail
from app.services.enrollment_forms.config import resolve_settings, scheme_age_limits
from app.services.enrollment_forms.pricing import plan_facts, product_contributions
from app.services.flex_membership import classify_relationship
from app.services.flex_pricing_resolver import (
    dependant_age_limits,
    get_pricing,
    reference_date,
)
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    DOB_KEYS,
    EMAIL_KEYS,
    EMPLOYEE_ID_KEYS,
    GENDER_KEYS,
    NAME_KEYS,
    REL_KEYS,
    age_next_birthday_as_of,
    first_value,
    iso_date,
    mask_nric,
    parse_dob,
)

GRADE_KEYS = (
    "job_grade", "grade", "job_category", "hr_category", "employee_category", "designation",
)
HIRE_KEYS = (
    "date_of_hire", "date_joined", "join_date", "date_of_joining", "employment_date",
    "hire_date", "doj",
)
PHONE_KEYS = (
    "contact_no", "contact_number", "mobile", "mobile_no", "mobile_number", "phone",
    "handphone",
)
OCCUPATION_KEYS = ("occupation", "dependant_occupation")

_ROLE_LABEL = {"spouse": "spouse", "child": "children"}


def _attrs(employee: Employee) -> dict[str, Any]:
    return {**(employee.derived_attribute_values or {}), **(employee.attribute_values or {})}


def employee_particulars(employee: Employee) -> ParticularsOut:
    av = _attrs(employee)
    return ParticularsOut(
        name=employee.employee_name or first_value(av, NAME_KEYS),
        staff_id=employee.staff_id,
        id_masked=mask_nric(first_value(av, EMPLOYEE_ID_KEYS)),
        gender=first_value(av, GENDER_KEYS),
        dob=iso_date(first_value(av, DOB_KEYS)),
        job_grade=first_value(av, GRADE_KEYS),
        date_of_hire=iso_date(first_value(av, HIRE_KEYS)),
        email=first_value(av, EMAIL_KEYS),
        contact_no=first_value(av, PHONE_KEYS),
    )


def employee_full_id(employee: Employee) -> str | None:
    """Unmasked NRIC/FIN — printed on the PDF only (as the paper form did)."""
    return first_value(_attrs(employee), EMPLOYEE_ID_KEYS)


def _eligibility(
    role: str | None, anb: int | None, limits: dict[str, dict[str, int]]
) -> tuple[bool, str | None]:
    if role is None:
        return True, "Relationship not recognised — your broker will check eligibility."
    if anb is None:
        return True, "Date of birth missing — your broker will check eligibility."
    window = limits.get(role) or {}
    lo, hi = window.get("min"), window.get("max")
    if hi is not None and anb > hi:
        return False, f"Over the age limit for {_ROLE_LABEL[role]} ({hi} next birthday)."
    if lo is not None and anb < lo:
        return False, f"Under the minimum age for {_ROLE_LABEL[role]} ({lo} next birthday)."
    return True, None


def form_dependants(
    db: Session,
    employee: Employee,
    limits: dict[str, dict[str, int]],
    ref: date,
    product_limits: dict[str, dict[str, dict[str, int]]] | None = None,
) -> list[FormDependantOut]:
    """Family members with a scheme-wide verdict (``eligible`` -- what the family
    step states) and the products whose own window excludes them
    (``ineligible_products`` -- what signing enforces, per product)."""
    rows = db.execute(
        select(Dependant).where(
            Dependant.employee_id == employee.id,
            Dependant.status.in_(("active", DEPENDANT_STATUS_PENDING)),
        )
    ).scalars().all()
    out: list[FormDependantOut] = []
    for dep in rows:
        av = dep.attribute_values or {}
        relationship = first_value(av, REL_KEYS)
        role = classify_relationship(relationship)
        dob = parse_dob(first_value(av, DOB_KEYS))
        anb = age_next_birthday_as_of(dob, ref) if dob else None
        eligible, note = _eligibility(role, anb, limits)
        excluded = sorted(
            code
            for code, window in (product_limits or {}).items()
            if not _eligibility(role, anb, window)[0]
        )
        if eligible and excluded:
            note = f"Not eligible for {', '.join(excluded)} (outside that plan's age limit)."
        out.append(
            FormDependantOut(
                id=dep.id,
                name=first_value(av, NAME_KEYS),
                relationship=relationship,
                role=role,
                gender=first_value(av, GENDER_KEYS),
                id_masked=mask_nric(first_value(av, DEPENDANT_ID_KEYS)),
                dob=dob.isoformat() if dob else None,
                age_next_birthday=anb,
                occupation=first_value(av, OCCUPATION_KEYS),
                status="pending" if dep.status == DEPENDANT_STATUS_PENDING else "active",
                eligible=eligible,
                eligibility_note=note,
                ineligible_products=excluded,
            )
        )
    out.sort(key=lambda d: (d.role != "spouse", d.dob or "", d.name or ""))
    return out


def scoped_products(
    options: EnrollmentOptionsOut, window: EnrollmentWindow
) -> list[ProductTierSetOut]:
    scope = set(window.product_scope or [])
    return [p for p in options.products if not scope or p.product_code in scope]


def current_tier_label(ts: ProductTierSetOut) -> str | None:
    tier = next((t for t in ts.tiers if t.is_current), None) or next(
        (t for t in ts.tiers if t.is_baseline), None
    )
    return tier.label if tier else None


_UNANSWERED = {
    "deemed_keep_current": (
        "If you do not submit this form by the closing date, your current cover — "
        "including family members already enrolled — continues, and premiums for "
        "them continue to be deducted."
    ),
    "deemed_decline": (
        "If you do not submit this form by the closing date, you are treated as "
        "declining the voluntary cover offered here."
    ),
}


def intro_lines(
    settings: FormSettings,
    compulsory: list[CompulsoryLineOut],
    default_behavior: str | None = None,
) -> list[str]:
    unanswered = _UNANSWERED.get(default_behavior or "")
    if settings.intro:
        custom = [p.strip() for p in settings.intro.split("\n") if p.strip()]
        return [*custom, unanswered] if unanswered else custom
    lines: list[str] = []
    if compulsory:
        names = ", ".join(c.product_name or c.product_code for c in compulsory)
        lines.append(
            f"You are automatically covered for {names}. No enrolment is needed for these."
        )
    lines.append(
        "Enrolment for voluntary cover and for your family is on a voluntary basis. "
        "Changes are made once a year during the enrolment period, except for new "
        "joiners, marriage or the birth of a child — submit within 30 days of the event."
    )
    if unanswered:
        lines.append(unanswered)
    return lines


def submission_summary(
    sub: EnrollmentFormSubmission, enrollment_status: str | None = None
) -> FormSubmissionSummary:
    return FormSubmissionSummary(
        id=sub.id,
        reference_no=sub.reference_no,
        version=sub.version,
        source=sub.source,  # type: ignore[arg-type]
        status=sub.status,  # type: ignore[arg-type]
        submitted_at=sub.signed_at or sub.created_at,
        signature_name=sub.signature_name,
        acknowledged_at=sub.acknowledged_at,
        has_pdf=sub.document_id is not None,
        enrollment_status=enrollment_status,
    )


def latest_submission(
    db: Session, employee_id: str, window_id: str
) -> EnrollmentFormSubmission | None:
    return db.execute(
        select(EnrollmentFormSubmission)
        .where(
            EnrollmentFormSubmission.employee_id == employee_id,
            EnrollmentFormSubmission.window_id == window_id,
        )
        .order_by(EnrollmentFormSubmission.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def product_age_limits(
    db: Session, products: list[ProductTierSetOut], policy_year_id: str
) -> dict[str, dict[str, dict[str, int]]]:
    """``{product_code: age window}`` for products that carry family cover --
    the same ``dependant_age_limits`` coverage and pricing apply per product."""
    pricing = get_pricing(db, policy_year_id)
    return {
        ts.product_code: dependant_age_limits(pricing, ts.product_id)
        for ts in products
        if ts.dependant_participation is not None
        or any(t.dependant_participation is not None for t in ts.tiers)
    }


def _documents(db: Session, settings: FormSettings) -> list[FormDocumentOut]:
    ids = [d.document_id for d in settings.documents if d.document_id]
    names: dict[str, str] = {
        str(doc_id): str(name)
        for doc_id, name in db.execute(
            select(StoredDocument.id, StoredDocument.file_name).where(
                StoredDocument.id.in_(ids),
                StoredDocument.entity_type == DOC_ENTITY_FORM_RESOURCE,
            )
        ).all()
    } if ids else {}
    return [
        FormDocumentOut(
            id=d.id, label=d.label, url=d.url, document_id=d.document_id,
            file_name=names.get(d.document_id) if d.document_id else None,
        )
        for d in settings.documents
    ]


def build_form_parts(
    db: Session, employee: Employee, window: EnrollmentWindow, enrollment: Enrollment
) -> tuple[MemberFormContextOut, list[ProductTierSetOut]]:
    """The member's form context plus the scoped (unscrubbed) tier sets it was
    built from -- returned together so signing never rebuilds the options."""
    settings, _row = resolve_settings(db, window)
    year = db.get(PolicyYear, window.policy_year_id)
    client = db.get(Client, window.client_id)
    assert year is not None and client is not None
    options = build_enrollment_options(
        db, employee, window, window.policy_year_id, enrollment_id=enrollment.id
    )
    products = scoped_products(options, window)
    compulsory_codes = set(enrollment_detail(db, enrollment).compulsory_product_codes)
    compulsory = [
        CompulsoryLineOut(
            product_code=ts.product_code,
            product_name=ts.product_name,
            plan_label=current_tier_label(ts),
        )
        for ts in products
        if ts.product_code in compulsory_codes
    ]
    names = {ts.product_code: ts.product_name for ts in products}
    limits = scheme_age_limits(db, window.policy_year_id)
    ref = reference_date(db, window.policy_year_id)
    latest = latest_submission(db, employee.id, window.id)
    ctx = MemberFormContextOut(
        company_name=client.legal_name or client.name,
        title=settings.title,
        policy_start=year.start_date,
        policy_end=year.end_date,
        closes_at=window.closes_at,
        window_type=window.window_type,
        intro_lines=intro_lines(settings, compulsory, window.default_behavior),
        submission_note=settings.submission_note,
        helpline=settings.helpline,
        eligibility_notes=age_notes(limits) + settings.eligibility_notes,
        clauses=settings.clauses,
        documents=_documents(db, settings),
        rules=[
            FormRuleOut(
                product_code=r.product_code,
                product_name=names.get(r.product_code),
                requires_product_code=r.requires_product_code,
                requires_product_name=names.get(r.requires_product_code),
            )
            for r in settings.rules
            if r.product_code in names and r.requires_product_code in names
        ],
        particulars=employee_particulars(employee),
        compulsory=compulsory,
        contributions=product_contributions(products, settings),
        plans=plan_facts(db, products, window.policy_year_id),
        flex_wallet=options.flex_wallet,
        flex_currency=options.flex_currency,
        flex_proration_note=options.flex_proration.note if options.flex_proration else None,
        dependants=form_dependants(
            db, employee, limits, ref, product_age_limits(db, products, window.policy_year_id)
        ),
        latest=submission_summary(latest, enrollment.status) if latest else None,
    )
    return ctx, products


def build_form_context(
    db: Session, employee: Employee, window: EnrollmentWindow, enrollment: Enrollment
) -> MemberFormContextOut:
    return build_form_parts(db, employee, window, enrollment)[0]


def age_notes(limits: dict[str, dict[str, int]]) -> list[str]:
    notes: list[str] = []
    spouse = limits.get("spouse") or {}
    child = limits.get("child") or {}
    if spouse.get("max") is not None:
        notes.append(
            f"Spouse: your legal spouse, up to age {spouse['max']} next birthday, "
            "not divorced or legally separated from you."
        )
    if child.get("max") is not None:
        notes.append(
            f"Child: your child from 15 days old (not in hospital confinement) up to "
            f"age {child['max']} next birthday."
        )
    return notes
