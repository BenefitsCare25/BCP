"""Panel setup history — what clinic networks and e-cards each benefit year had.

Read-only, and deliberately spans BOTH panel surfaces: the library (listings +
card artwork) is year-independent, but the per-year selections are not, so
"what did members actually see in 2025?" can only be answered by joining the
two year-scoped tables. One endpoint rather than an N+1 of the per-year
`GET /policy-years/{id}/panels` + `/cards` calls, so the history tab renders in
a single round trip.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_client_id
from app.db.session import get_db
from app.models import (
    PanelCard,
    PanelClinic,
    PanelListing,
    PolicyYear,
    PolicyYearCard,
    PolicyYearPanel,
    Product,
)
from app.models.panel_card import CARD_SERVICE_LABELS
from app.models.panel_clinic import clinic_type_label
from app.models.policy_year import PolicyYearStatus
from app.schemas.panel import (
    PanelSetupHistoryOut,
    SetupHistoryCard,
    SetupHistoryListing,
    SetupHistoryYear,
)

router = APIRouter(prefix="/panel-setup", tags=["panel-listings"])


@router.get("/history", response_model=PanelSetupHistoryOut)
def panel_setup_history(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelSetupHistoryOut:
    """Every benefit year for the active company, newest first, with the clinic
    networks enabled and the e-cards issued for each."""
    client_id = require_client_id(user)
    years = db.scalars(
        select(PolicyYear)
        .where(PolicyYear.client_id == client_id)
        .order_by(PolicyYear.start_date.desc())
    ).all()
    if not years:
        return PanelSetupHistoryOut()
    year_ids = [y.id for y in years]

    # Listings enabled per year, with their clinic counts.
    listings_by_year: dict[str, list[SetupHistoryListing]] = {}
    clinic_counts: dict[str, int] = {}
    rows = db.execute(
        select(PolicyYearPanel.policy_year_id, PanelListing)
        .join(PanelListing, PolicyYearPanel.panel_listing_id == PanelListing.id)
        .where(PolicyYearPanel.policy_year_id.in_(year_ids))
        .order_by(PanelListing.insurer, PanelListing.panel_provider)
    ).all()
    if rows:
        clinic_counts = {
            listing_id: count
            for listing_id, count in db.execute(
                select(PanelClinic.panel_listing_id, func.count(PanelClinic.id))
                .where(
                    PanelClinic.panel_listing_id.in_({listing.id for _, listing in rows})
                )
                .group_by(PanelClinic.panel_listing_id)
            ).all()
        }
    for year_id, listing in rows:
        listings_by_year.setdefault(year_id, []).append(
            SetupHistoryListing(
                id=listing.id,
                display_label=listing.display_label(),
                type_label=clinic_type_label(listing.country, listing.clinic_type),
                country=listing.country,
                clinic_count=clinic_counts.get(listing.id, 0),
            )
        )

    # Cards issued per year.
    cards_by_year: dict[str, list[SetupHistoryCard]] = {}
    card_rows = db.execute(
        select(PolicyYearCard, PanelCard, Product)
        .join(PanelCard, PolicyYearCard.panel_card_id == PanelCard.id)
        .join(Product, PolicyYearCard.product_id == Product.id)
        .where(PolicyYearCard.policy_year_id.in_(year_ids))
        .order_by(Product.code)
    ).all()
    for assignment, card, product in card_rows:
        services = assignment.services or {}
        cards_by_year.setdefault(assignment.policy_year_id, []).append(
            SetupHistoryCard(
                id=assignment.id,
                card_name=card.name,
                product_code=product.code,
                product_name=product.display_name,
                employee_member_id_source=assignment.employee_member_id_source,
                dependant_member_id_source=assignment.dependant_member_id_source,
                service_labels=[
                    CARD_SERVICE_LABELS[key]
                    for key in CARD_SERVICE_LABELS
                    if services.get(key)
                ],
                remark_keys=[k for k, v in (assignment.remarks or {}).items() if v],
                special_conditions=assignment.special_conditions,
            )
        )

    return PanelSetupHistoryOut(
        years=[
            SetupHistoryYear(
                policy_year_id=year.id,
                year=year.year,
                status=year.status.value,
                start_date=year.start_date.isoformat(),
                end_date=year.end_date.isoformat(),
                is_current=year.status == PolicyYearStatus.active,
                listings=listings_by_year.get(year.id, []),
                cards=cards_by_year.get(year.id, []),
            )
            for year in years
        ]
    )
