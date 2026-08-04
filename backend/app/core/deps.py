"""Shared FastAPI dependencies for tenant-scoped resource loading.

Two patterns: `Depends(load_X)` for path-parameter IDs, `assert_*_for_user`
for query/form IDs. Both return 404 on cross-tenant access (not 403) so the
API doesn't leak resource existence. `system_admin` bypasses the filter; the
cross-tenant access is recorded via `AuditLog.cross_tenant_access`.
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser, get_current_user
from app.db.session import get_db
from app.models import (
    Category,
    Claim,
    Dependant,
    Employee,
    Enrollment,
    EnrollmentWindow,
    PanelCard,
    PanelListing,
    PlacementSlipRow,
    Plan,
    PolicyYear,
    ReportVersion,
)

logger = logging.getLogger(__name__)
_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _deny_cross_tenant(user: CurrentUser, resource: str, resource_id: str) -> HTTPException:
    """Log a blocked cross-tenant access attempt and return the 404 to raise.

    Blocked attempts are rejected before any audit row is written, so this
    security log is the only record that someone probed another tenant's IDs.
    """
    logger.warning(
        "cross-tenant access denied: user=%s client=%s firm=%s resource=%s id=%s",
        user.user_id, user.client_id, user.broker_firm_id, resource, resource_id,
    )
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{resource} not found")


def require_client_id(user: CurrentUser) -> str:
    if not user.client_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "User has no active client",
        )
    return user.client_id


def require_write_access(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Keep broker viewers read-only across the entire API surface."""
    if user.role == "broker_viewer" and request.method.upper() not in _READ_ONLY_METHODS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "The broker_viewer role is read-only.",
        )
    return user


def require_broker_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Gate for tenant-level admin surfaces (BYOK config, billing, etc).

    `system_admin` is intentionally excluded — those endpoints would have to
    accept an explicit `client_id` query param to disambiguate, which is a
    footgun we'd rather not ship until needed.
    """
    if user.role != "broker_admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requires broker_admin role.",
        )
    return user


def require_system_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Gate for platform-level admin surfaces (creating broker firms)."""
    if user.role != ROLE_SYSTEM_ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requires system_admin role.",
        )
    return user


def require_firm_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Gate for firm-level admin surfaces (managing clients, users, invites).

    `broker_admin` manages their own firm; `system_admin` may manage any firm
    but must name the target firm explicitly in the request.
    """
    if user.role not in ("broker_admin", ROLE_SYSTEM_ADMIN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requires broker_admin or system_admin role.",
        )
    return user


def user_owns(user: CurrentUser, client_id: str | None) -> bool:
    if user.role == ROLE_SYSTEM_ADMIN:
        return True
    return client_id is not None and client_id == user.client_id


def can_write_global(user: CurrentUser) -> bool:
    """True when the user may create/edit firm-library (global) catalog rows.

    Firm-library rows (client_id NULL) apply to every company, so writing them
    is a firm-admin act — mirrors the edit gate in `load_editable_global`.
    """
    return user.role in (ROLE_SYSTEM_ADMIN, "broker_admin")


def tenant_or_global(column, client_id: str | None):
    """SQLAlchemy predicate for "global (NULL client_id) OR this tenant".

    Use for tables like EmployeeAttributeSchema and Product that mix
    Singapore-default rows with per-client overrides.
    """
    return or_(column.is_(None), column == client_id)


def load_editable_global(
    model, row_id: str, user: CurrentUser, db: Session, label: str
):
    """Load a row from a tenant-or-global catalog table for WRITING.

    Encodes the policy shared by every `tenant_or_global` table (Product,
    EmployeeAttributeSchema, Insurer): a global row (client_id NULL) is a
    platform default only admins may edit, and another tenant's row is a 404
    rather than a 403 so its existence isn't leaked.

    `label` is the human name used in the error ("Product", "Insurer").
    """
    row = db.get(model, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found")
    if row.client_id is None:
        if not can_write_global(user):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Only admins can edit global defaults"
            )
    elif not user_owns(user, row.client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found")
    return row


def assert_policy_year_for_user(
    policy_year_id: str, user: CurrentUser, db: Session
) -> PolicyYear:
    """Load the PolicyYear or raise 404 — used when `policy_year_id` comes
    from a query/form parameter rather than the path."""
    py = db.get(PolicyYear, policy_year_id)
    if py is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy year not found")
    if not user_owns(user, py.client_id):
        raise _deny_cross_tenant(user, "Policy year", policy_year_id)
    return py


def assert_policy_year_editable(py: PolicyYear) -> PolicyYear:
    """No-op guard — configuration is editable on every policy year.

    Historically a policy year locked its configuration once activated (a
    frozen snapshot with no rollback path). That lock was removed in favour of
    a lightweight "current year" flag (the ``active`` status is what the member
    portal reads); every year stays editable regardless of status. The guard is
    kept as a seam so config endpoints keep a single, documented place to
    reintroduce a lock if one is ever needed again.
    """
    return py


def load_policy_year(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PolicyYear:
    return assert_policy_year_for_user(policy_year_id, user, db)


def load_category(
    category_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Category:
    # Single JOIN query — fetches the Category and proves tenant ownership
    # via the parent PolicyYear in one round trip.
    is_admin = user.role == ROLE_SYSTEM_ADMIN
    stmt = select(Category).join(PolicyYear, Category.policy_year_id == PolicyYear.id).where(
        Category.id == category_id
    )
    if not is_admin:
        stmt = stmt.where(PolicyYear.client_id == user.client_id)
    c = db.execute(stmt).scalar_one_or_none()
    if c is None:
        if db.get(Category, category_id) is not None:
            raise _deny_cross_tenant(user, "Category", category_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return c


def load_employee(
    employee_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Employee:
    e = db.get(Employee, employee_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    if not user_owns(user, e.client_id):
        raise _deny_cross_tenant(user, "Employee", employee_id)
    return e


def load_dependant(
    dependant_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dependant:
    d = db.get(Dependant, dependant_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dependant not found")
    if not user_owns(user, d.client_id):
        raise _deny_cross_tenant(user, "Dependant", dependant_id)
    return d


def load_placement_slip(
    slip_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PlacementSlipRow:
    is_admin = user.role == ROLE_SYSTEM_ADMIN
    stmt = (
        select(PlacementSlipRow)
        .join(PolicyYear, PlacementSlipRow.policy_year_id == PolicyYear.id)
        .where(PlacementSlipRow.id == slip_id)
    )
    if not is_admin:
        stmt = stmt.where(PolicyYear.client_id == user.client_id)
    slip = db.execute(stmt).scalar_one_or_none()
    if slip is None:
        if db.get(PlacementSlipRow, slip_id) is not None:
            raise _deny_cross_tenant(user, "Placement slip", slip_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Placement slip not found")
    return slip


def load_enrollment_window(
    window_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentWindow:
    """Load an EnrollmentWindow, proving tenant ownership via its client_id."""
    w = db.get(EnrollmentWindow, window_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment period not found")
    if not user_owns(user, w.client_id):
        raise _deny_cross_tenant(user, "Enrollment window", window_id)
    return w


def load_enrollment(
    enrollment_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Enrollment:
    """Load an Enrollment, proving tenant ownership via its client_id."""
    e = db.get(Enrollment, enrollment_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrollment not found")
    if not user_owns(user, e.client_id):
        raise _deny_cross_tenant(user, "Enrollment", enrollment_id)
    return e


def load_claim(
    claim_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Claim:
    """Load a Claim, proving tenant ownership via its client_id."""
    c = db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    if not user_owns(user, c.client_id):
        raise _deny_cross_tenant(user, "Claim", claim_id)
    return c


def load_report_version(
    version_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportVersion:
    """Load a ReportVersion, proving tenant ownership via its client_id."""
    rv = db.get(ReportVersion, version_id)
    if rv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report version not found")
    if not user_owns(user, rv.client_id):
        raise _deny_cross_tenant(user, "Report version", version_id)
    return rv


def load_panel_listing(
    listing_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelListing:
    """Load a PanelListing. NULL client_id = shared library entry, accessible
    to every broker user (schema-per-firm bounds it to the firm on Postgres);
    a client-pinned row keeps strict tenant ownership."""
    listing = db.get(PanelListing, listing_id)
    if listing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel listing not found")
    if listing.client_id is not None and not user_owns(user, listing.client_id):
        raise _deny_cross_tenant(user, "Panel listing", listing_id)
    return listing


def load_panel_card(
    card_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PanelCard:
    """Load a PanelCard. NULL client_id = shared library entry (same posture
    as `load_panel_listing`); a client-pinned row keeps strict ownership."""
    card = db.get(PanelCard, card_id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Panel card not found")
    if card.client_id is not None and not user_owns(user, card.client_id):
        raise _deny_cross_tenant(user, "Panel card", card_id)
    return card


def load_plan(
    plan_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Plan:
    """Load a Plan, proving tenant ownership via its parent PolicyYear."""
    is_admin = user.role == ROLE_SYSTEM_ADMIN
    stmt = (
        select(Plan)
        .join(PolicyYear, Plan.policy_year_id == PolicyYear.id)
        .where(Plan.id == plan_id)
    )
    if not is_admin:
        stmt = stmt.where(PolicyYear.client_id == user.client_id)
    plan = db.execute(stmt).scalar_one_or_none()
    if plan is None:
        if db.get(Plan, plan_id) is not None:
            raise _deny_cross_tenant(user, "Plan", plan_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return plan
