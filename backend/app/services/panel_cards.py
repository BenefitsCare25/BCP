"""Panel e-card resolution — turns a card assignment into a renderable card.

The broker configures WHERE each field sits on the artwork (`PanelCard.
placements`) and WHICH identifier is printed (`PolicyYearCard.*_member_id_
source`). This module resolves the member-specific VALUES for those fields, so
the renderer (`components/portal/MemberCard.tsx`) is a pure join of placement
geometry against a `values` dict and never reaches for member data itself.

Cards follow coverage: a card is emitted only for a product the member is
actually covered under (a line in their benefit statement), and a dependant
card only for a dependant in that line's `covered_dependants` — so an
enrollment-elected subset binds here exactly as it does everywhere else.
"""
from __future__ import annotations

import base64
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Client,
    Dependant,
    Employee,
    PanelCard,
    PolicyYear,
    PolicyYearCard,
    Product,
    ProductTerm,
)
from app.models.panel_card import CARD_SERVICE_LABELS, CARD_SERVICES
from app.schemas.api import BenefitStatementOut, CoverageLine, DependantSummary
from app.schemas.panel_card import (
    CardPlacements,
    CardServiceOut,
    MemberCardOut,
)
from app.services.product_insurer import insurer_map
from app.services.roster_attributes import EMAIL_KEYS, first_value
from app.services.roster_parser import INSURER_MEMBER_ID_KEY

# Prefix for ids this platform issues when the panel has no insurer number.
PLATFORM_ID_PREFIX = "INS"
_PLATFORM_ID_CHARS = 8
_DEPENDANT_TOKEN_CHARS = 4


def platform_member_id(client_id: str, staff_id: str) -> str:
    """A stable card number derived from the member's identity, not their row.

    Deliberately keyed on (client, staff id) rather than the Employee row id:
    every renewal creates a NEW Employee row, and a card number that changed
    each policy year would be worthless at a clinic counter. Deterministic, so
    it needs no column and can never drift out of sync.
    """
    digest = hashlib.sha256(f"{client_id}:{staff_id}".encode()).digest()
    token = base64.b32encode(digest).decode("ascii")[:_PLATFORM_ID_CHARS]
    return f"{PLATFORM_ID_PREFIX}-{token}"


def dependant_key(national_id: str | None, name: str | None, relationship: str | None) -> str:
    """Stable identity for a dependant ACROSS renewals.

    Dependant rows are recreated every policy year, so the key must come from
    content (NRIC first, else name + relationship), never the row id.
    """
    raw = (national_id or "").strip() or f"{name or ''}|{relationship or ''}"
    return raw.strip().lower()


def platform_dependant_id(client_id: str, staff_id: str, dep_key: str) -> str:
    """A dependant's platform card number: the employee's number plus a token
    derived from the DEPENDANT's own identity.

    Deliberately NOT a positional index. `covered_dependants` changes whenever
    a dependant is added, dropped or re-elected, so a positional suffix would
    silently renumber every other dependant's card between renewals — the exact
    failure `platform_member_id` avoids for the employee.
    """
    base = platform_member_id(client_id, staff_id)
    digest = hashlib.sha256(f"{client_id}:{staff_id}:{dep_key}".encode()).digest()
    token = base64.b32encode(digest).decode("ascii")[:_DEPENDANT_TOKEN_CHARS]
    return f"{base}-{token}"


def mask_nric(value: str | None) -> str:
    """Show only the last 4 characters — a card is shown in public."""
    raw = (value or "").strip()
    if not raw:
        return ""
    return f"{'*' * max(len(raw) - 4, 0)}{raw[-4:]}"


def insurer_member_id(attributes: dict | None, insurer: str | None) -> str:
    """The insurer's own member number off the roster, tolerating casing drift
    between the roster column header and the configured insurer name."""
    ids = (attributes or {}).get(INSURER_MEMBER_ID_KEY) or {}
    if not isinstance(ids, dict) or not ids:
        return ""
    name = (insurer or "").strip()
    if name and name in ids:
        return str(ids[name])
    if name:
        for key, value in ids.items():
            if str(key).strip().lower() == name.lower():
                return str(value)
    # Single-insurer rosters routinely label the column differently from the
    # product's insurer name; with only one id there is nothing to confuse.
    if len(ids) == 1:
        return str(next(iter(ids.values())))
    return ""


def resolve_member_id(
    source: str,
    *,
    insurer: str | None,
    attributes: dict | None,
    staff_id: str | None,
    email: str | None,
    national_id: str | None,
    client_id: str,
) -> str:
    """Resolve the configured identifier. Returns "" when unavailable — the
    card still renders (blank field) rather than 500ing on a roster gap."""
    if source == "insurer_member_id":
        return insurer_member_id(attributes, insurer)
    if source == "staff_id":
        return (staff_id or "").strip()
    if source == "email":
        return (email or "").strip()
    if source == "national_id_masked":
        return mask_nric(national_id)
    if source == "platform_id":
        return platform_member_id(client_id, staff_id or "")
    return ""


def _service_list(services: dict | None) -> list[CardServiceOut]:
    enabled = services or {}
    return [
        CardServiceOut(key=key, label=CARD_SERVICE_LABELS[key])
        for key in CARD_SERVICES
        if enabled.get(key)
    ]


def _placements(card: PanelCard) -> CardPlacements:
    raw = card.placements if isinstance(card.placements, dict) else {}
    return CardPlacements.model_validate(raw)


def _employee_email(employee: Employee, fallback: str | None) -> str:
    roster = first_value(employee.attribute_values or {}, EMAIL_KEYS)
    return roster or (fallback or "")


def load_year_cards(db: Session, policy_year_id: str) -> list[PolicyYearCard]:
    return list(
        db.scalars(
            select(PolicyYearCard)
            .where(PolicyYearCard.policy_year_id == policy_year_id)
            .order_by(PolicyYearCard.created_at)
        )
    )


def _shared_values(
    assignment: PolicyYearCard,
    card: PanelCard,
    product: Product,
    coverage: CoverageLine,
    term: ProductTerm | None,
    year: PolicyYear | None,
    client: Client | None,
    remarks: dict[str, str],
) -> dict[str, str]:
    """Values identical on every card for this assignment (employee + all
    dependants) — the policy, product and card-level text."""
    # A product's own coverage period wins; otherwise the benefit year's span.
    start = (term.coverage_start if term is not None else None) or (
        year.start_date if year is not None else None
    )
    end = (term.coverage_end if term is not None else None) or (
        year.end_date if year is not None else None
    )
    return {
        "company_name": client.name if client is not None else "",
        "policy_number": (term.policy_number if term is not None else "") or "",
        "product_name": coverage.product_name or product.display_name,
        "plan_name": coverage.plan_code or coverage.cover_description or "",
        "effective_date": start.isoformat() if start else "",
        "expiry_date": end.isoformat() if end else "",
        "insurer": card.insurer,
        "panel_provider": card.panel_provider,
        "card_name": card.name,
        "special_conditions": assignment.special_conditions or "",
        **{f"remark_{key}": value for key, value in remarks.items()},
    }


def _cards_for_assignment(
    db: Session,
    employee: Employee,
    assignment: PolicyYearCard,
    card: PanelCard,
    product: Product,
    coverage: CoverageLine,
    term: ProductTerm | None,
    year: PolicyYear | None,
    client: Client | None,
    member_email: str | None,
    product_insurer: str,
) -> list[MemberCardOut]:
    """The member's card for one product, plus one per covered dependant."""
    placements = _placements(card)
    services = _service_list(assignment.services)
    remarks = {k: v for k, v in (assignment.remarks or {}).items() if v}
    # The insurer this BENEFIT YEAR places the product with; the card artwork's
    # own insurer stands in when the product has none configured.
    insurer = product_insurer or card.insurer

    shared = _shared_values(
        assignment, card, product, coverage, term, year, client, remarks
    )

    def make_card(
        holder_type: str,
        holder_id: str,
        holder_name: str | None,
        values: dict[str, str],
    ) -> MemberCardOut:
        return MemberCardOut(
            card_id=card.id,
            assignment_id=assignment.id,
            holder_type=holder_type,
            holder_id=holder_id,
            holder_name=holder_name,
            product_code=product.code,
            product_name=coverage.product_name or product.display_name,
            card_name=card.name,
            aspect_ratio=card.aspect_ratio,
            has_front=bool(card.artwork_front_path),
            has_back=bool(card.artwork_back_path),
            placements=placements,
            values=values,
            services=services,
            remarks=remarks,
            special_conditions=assignment.special_conditions,
        )

    out = [
        make_card(
            "employee",
            employee.id,
            employee.employee_name,
            {
                **shared,
                **_employee_values(employee, assignment, insurer, member_email),
            },
        )
    ]
    if not coverage.covered_dependants:
        return out

    dependants = {
        row.id: row
        for row in db.scalars(
            select(Dependant).where(
                Dependant.id.in_({d.id for d in coverage.covered_dependants}),
                Dependant.employee_id == employee.id,
            )
        )
    }
    for summary in coverage.covered_dependants:
        row = dependants.get(summary.id)
        if row is None:
            continue
        out.append(
            make_card(
                "dependant",
                row.id,
                summary.name,
                {
                    **shared,
                    **_dependant_values(
                        employee, row, summary, assignment, insurer
                    ),
                },
            )
        )
    return out


def _employee_values(
    employee: Employee,
    assignment: PolicyYearCard,
    insurer: str | None,
    member_email: str | None,
) -> dict[str, str]:
    """The member-specific values printed on the employee's own card."""
    email = _employee_email(employee, member_email)
    return {
        "member_name": employee.employee_name or employee.staff_id,
        "staff_id": employee.staff_id,
        "email": email,
        "nric_masked": mask_nric(employee.national_id_normalized),
        "dependant_name": "",
        "relationship": "",
        "member_id": resolve_member_id(
            assignment.employee_member_id_source,
            insurer=insurer,
            attributes=employee.attribute_values,
            staff_id=employee.staff_id,
            email=email,
            national_id=employee.national_id_normalized,
            client_id=employee.client_id,
        ),
    }


def _dependant_values(
    employee: Employee,
    row: Dependant,
    summary: DependantSummary,
    assignment: PolicyYearCard,
    insurer: str | None,
) -> dict[str, str]:
    """The member-specific values printed on one dependant's card."""
    attrs = row.attribute_values or {}
    dep_email = first_value(attrs, EMAIL_KEYS) or ""
    source = assignment.dependant_member_id_source
    if source == "platform_id":
        member_id = platform_dependant_id(
            employee.client_id,
            employee.staff_id,
            dependant_key(
                row.national_id_normalized, summary.name, summary.relationship
            ),
        )
    else:
        member_id = resolve_member_id(
            source,
            insurer=insurer,
            attributes=attrs,
            staff_id=employee.staff_id,
            email=dep_email,
            national_id=row.national_id_normalized,
            client_id=employee.client_id,
        )
        if not member_id and source == "insurer_member_id":
            # Rosters routinely carry the insurer's number on the EMPLOYEE row
            # only. Falling back to the policyholder's number beats printing a
            # blank Member ID, which makes the card unusable at a counter.
            member_id = insurer_member_id(employee.attribute_values, insurer)
    return {
        "member_name": summary.name or "",
        "staff_id": employee.staff_id,
        "email": dep_email,
        "nric_masked": mask_nric(row.national_id_normalized),
        "dependant_name": summary.name or "",
        "relationship": summary.relationship or "",
        "member_id": member_id,
    }


def build_member_cards(
    db: Session,
    employee: Employee,
    statement: BenefitStatementOut,
    *,
    member_email: str | None = None,
) -> list[MemberCardOut]:
    """Every e-card this member (and their covered dependants) holds.

    `statement` is the MEMBER statement (`build_member_statement`) — taking it
    as an argument keeps this free of the broker/member statement distinction
    and lets the portal + preview reuse a statement they already built.
    """
    assignments = load_year_cards(db, employee.policy_year_id)
    if not assignments:
        return []

    cards_by_id = {
        card.id: card
        for card in db.scalars(
            select(PanelCard).where(
                PanelCard.id.in_({a.panel_card_id for a in assignments})
            )
        )
    }
    products_by_id = {
        product.id: product
        for product in db.scalars(
            select(Product).where(Product.id.in_({a.product_id for a in assignments}))
        )
    }
    terms_by_product = {
        term.product_id: term
        for term in db.scalars(
            select(ProductTerm).where(
                ProductTerm.policy_year_id == employee.policy_year_id,
                ProductTerm.product_id.in_({a.product_id for a in assignments}),
            )
        )
    }
    insurers_by_product = insurer_map(
        db, employee.policy_year_id, products_by_id.values()
    )
    coverage_by_code = {line.product_code: line for line in statement.coverage}
    year = db.get(PolicyYear, employee.policy_year_id)
    client = db.get(Client, employee.client_id)

    out: list[MemberCardOut] = []
    for assignment in assignments:
        card = cards_by_id.get(assignment.panel_card_id)
        product = products_by_id.get(assignment.product_id)
        # A card with no front artwork isn't renderable, and a member holds no
        # card for a product they aren't covered under.
        if card is None or product is None or not card.artwork_front_path:
            continue
        coverage = coverage_by_code.get(product.code)
        if coverage is None:
            continue
        out.extend(
            _cards_for_assignment(
                db,
                employee,
                assignment,
                card,
                product,
                coverage,
                terms_by_product.get(assignment.product_id),
                year,
                client,
                member_email,
                insurers_by_product.get(product.id, ""),
            )
        )
    return out


def carry_over_card_assignments(
    db: Session, new_year, source_policy_year_id: str | None = None
) -> int:
    """Copy card assignments onto a freshly created policy year, so "which
    e-cards does this company issue" survives a renewal.

    `source_policy_year_id` names the year to copy FROM — callers that clone a
    specific year (copy-from-year) must pass it, otherwise the cards would come
    from whichever year happens to be most recent rather than the one whose
    products and plans were just cloned. Omit it for a plain new year, where
    "most recent prior year" is the right default.

    Flush only; the caller owns the commit. Returns the number copied.
    """
    prior_year_id = source_policy_year_id
    if prior_year_id is None:
        prior_year_id = db.execute(
            select(PolicyYear.id)
            .where(
                PolicyYear.client_id == new_year.client_id,
                PolicyYear.id != new_year.id,
                PolicyYear.start_date < new_year.start_date,
            )
            .order_by(PolicyYear.start_date.desc())
            .limit(1)
        ).scalar_one_or_none()
    if prior_year_id is None:
        return 0
    prior = load_year_cards(db, prior_year_id)
    for assignment in prior:
        db.add(
            PolicyYearCard(
                policy_year_id=new_year.id,
                panel_card_id=assignment.panel_card_id,
                product_id=assignment.product_id,
                employee_member_id_source=assignment.employee_member_id_source,
                dependant_member_id_source=assignment.dependant_member_id_source,
                services=assignment.services,
                remarks=assignment.remarks,
                special_conditions=assignment.special_conditions,
                show_future_cards=assignment.show_future_cards,
            )
        )
    if prior:
        db.flush()
    return len(prior)
