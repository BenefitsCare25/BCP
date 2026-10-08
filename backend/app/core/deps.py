"""Shared FastAPI dependencies for tenant-scoped resource loading.

Two patterns: `Depends(load_X)` for path-parameter IDs, `assert_*_for_user`
for query/form IDs. Both return 404 on cross-tenant access (not 403) so the
API doesn't leak resource existence. `system_admin` bypasses the filter; the
cross-tenant access is recorded via `AuditLog.cross_tenant_access`. Its reach
is still bounded by firm: the request is routed to the active company's firm
schema, and that company must be one it may access (`app.core.identity`).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.auth import (
    FIRM_OWNER_ROLES,
    READ_ONLY_METHODS,
    ROLE_FIRM_ADMIN,
    ROLE_SYSTEM_ADMIN,
    CurrentUser,
    client_selection_required,
    client_selection_stale,
    get_current_user,
)
from app.core.identity import platform_access_level
from app.db.session import get_db
from app.models import (
    BulkPlanUpdate,
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
_CLAIMS_ROLES = frozenset({"broker_admin", "broker_viewer", ROLE_FIRM_ADMIN, ROLE_SYSTEM_ADMIN})
# Firm administration roles: broker_admin powers, plus firm_admin and the
# platform master admin (through the selected company's firm).
_ADMIN_ROLES = frozenset({"broker_admin", ROLE_FIRM_ADMIN, ROLE_SYSTEM_ADMIN})
_POLICY_YEAR_HEADER = "X-Inspro-Policy-Year-ID"
# Surfaces whose writes do not act on the selected company's firm, so a master
# admin's `read` grant on that firm does not make them read-only
# (`require_write_access`): platform-wide settings and the AI policy library,
# and the consoles that name their target firm and check access to it
# themselves (platform console; admin console users, invitations, companies).
_FIRM_INDEPENDENT_PATHS = (
    "/api/v1/platform/",
    "/api/v1/platform-ai-settings",
    "/api/v1/ai-policies",
    "/api/v1/admin/",
)


def platform_access_read_only() -> HTTPException:
    """403 for a master admin's write in a firm it reaches through a read grant."""
    return HTTPException(
        status.HTTP_403_FORBIDDEN,
        {
            "code": "platform_access_read_only",
            "message": "Your access to this broker is read-only.",
        },
    )


def is_firm_owner(user: CurrentUser) -> bool:
    """Firm admins (own firm) and the platform master admin.

    Gates users and invitations, and every saved-data Remove, Delete, Clear
    all, Unlink or destructive reset (AGENTS.md "Broker portal permissions").
    """
    return user.role in FIRM_OWNER_ROLES


def require_firm_owner(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not is_firm_owner(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requires a firm administrator (firm_admin or system_admin role).",
        )
    return user


def assert_platform_firm_access(
    db: Session, user: CurrentUser, firm_id: str, *, write: bool, not_found: str
) -> None:
    """Confine a master admin's direct reach into a firm to its access model.

    A no-op for firm roles, whose firm boundary is enforced by the caller. A
    system_admin needs standing access (the platform owner's firm) or an active
    grant on ``firm_id``; without one the firm's records are a 404, like any
    other firm's. A `read` grant refuses changes with
    `platform_access_read_only`.
    """
    if user.role != ROLE_SYSTEM_ADMIN:
        return
    level = platform_access_level(db, user.user_id, firm_id)
    if level is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, not_found)
    if write and level == "read":
        raise platform_access_read_only()


def _refuse_read_only_grant(request: Request, user: CurrentUser) -> None:
    if (
        user.platform_access == "read"
        and request.method.upper() not in READ_ONLY_METHODS
        and not request.url.path.startswith(_FIRM_INDEPENDENT_PATHS)
    ):
        raise platform_access_read_only()


def _deny_cross_tenant(user: CurrentUser, resource: str, resource_id: str) -> HTTPException:
    """Log a blocked cross-tenant access attempt and return the 404 to raise.

    Blocked attempts are rejected before any audit row is written, so this
    security log is the only record that someone probed another tenant's IDs.
    """
    logger.warning(
        "cross-tenant access denied: user=%s client=%s firm=%s resource=%s id=%s",
        user.user_id,
        user.client_id,
        user.broker_firm_id,
        resource,
        resource_id,
    )
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{resource} not found")


def require_client_id(user: CurrentUser) -> str:
    if not user.client_id:
        if user.role == ROLE_SYSTEM_ADMIN:
            # A platform admin is never defaulted into a company for a write
            # (see `_build_current_user`); it must choose one explicitly.
            raise client_selection_required()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "User has no active client",
        )
    return user.client_id


def policy_year_company(py: PolicyYear, user: CurrentUser) -> str:
    """The company a policy-year-scoped write belongs to: the year's own.

    The active company comes from the `X-Inspro-Client` header while the year
    comes from the path or body, and `system_admin` passes `user_owns` for
    every company, so a stale tab could stamp company A onto company B's year
    (members, catalog rows, AI spend). Refuse the mismatch with the
    stale-selection 409 rather than guess which of the two was meant.
    """
    if py.client_id != require_client_id(user):
        raise client_selection_stale()
    return py.client_id


def _assert_selected_policy_year(
    request: Request,
    actual_policy_year_id: str,
    resource: str,
) -> None:
    """Reject a stale detail request after the company year was switched.

    List endpoints already receive an explicit ``policy_year_id``. Detail URLs
    only carry an opaque record id, so the UI also sends its selected year as a
    request header. An absent header preserves compatibility for integrations;
    a present, mismatched header is a stale browser action and must not mutate
    or display a different year's record.
    """
    selected = request.headers.get(_POLICY_YEAR_HEADER)
    if selected and selected != actual_policy_year_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{resource} belongs to a different benefit year. Refresh and try again.",
        )


def require_write_access(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Keep viewers read-only and reserve record deletion for firm owners.

    A master admin reaching the selected company's firm through a `read` grant
    is read-only too, except on surfaces that do not act on that firm.
    """
    if request.method.upper() == "DELETE" and not is_firm_owner(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Removing or clearing saved data requires a firm administrator.",
        )
    if user.role == "broker_viewer" and request.method.upper() not in READ_ONLY_METHODS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "The broker_viewer role is read-only.",
        )
    _refuse_read_only_grant(request, user)
    return user


def require_claim_access(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Restrict medical/financial claim records to broker claims staff.

    Client HR identities have a separate HR surface. Merely possessing access
    to a client must not grant access to diagnoses, receipts, adjudication, or
    settlement data. Broker viewers remain read-only.
    """
    if user.role not in _CLAIMS_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Claims access requires a broker claims role.",
        )
    if user.role == "broker_viewer" and request.method.upper() not in READ_ONLY_METHODS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "The broker_viewer role is read-only.",
        )
    _refuse_read_only_grant(request, user)
    return user


def require_claim_configuration(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Restrict claim-review and document-registry configuration to admins."""
    if user.role not in _ADMIN_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Claim configuration requires broker_admin, firm_admin or system_admin role.",
        )
    return user


def require_broker_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Gate for tenant-level admin surfaces (BYOK config, billing, etc).

    `firm_admin` holds every `broker_admin` power. A `system_admin` operates
    through the selected active client, so it has the same tenant-admin powers
    plus the platform-only surfaces.
    """
    if user.role not in _ADMIN_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requires broker_admin, firm_admin or system_admin role.",
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
    """Gate for company administration within a broker firm.

    `broker_admin` and `firm_admin` manage their own firm; `system_admin`
    names the target firm in the request and needs access to it
    (`assert_platform_firm_access`). Users and invitations use the stricter
    `require_firm_owner` dependency.
    """
    if user.role not in _ADMIN_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requires broker_admin, firm_admin or system_admin role.",
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
    A system_admin reaches a firm's library only through the selected company's
    firm; with no company selected the write would land in `public`, so it is
    refused with `client_selection_required` instead.
    """
    if user.role == ROLE_SYSTEM_ADMIN and not user.client_id:
        raise client_selection_required()
    return user.role in _ADMIN_ROLES


def tenant_or_global(column: Any, client_id: str | None) -> ColumnElement[bool]:
    """SQLAlchemy predicate for "global (NULL client_id) OR this tenant".

    Use for tables like EmployeeAttributeSchema and Product that mix
    Singapore-default rows with per-client overrides.
    """
    return or_(column.is_(None), column == client_id)


class _TenantOwned(Protocol):
    @property
    def client_id(self) -> str | None: ...


def load_editable_global[RowT: _TenantOwned](
    model: type[RowT],
    row_id: str,
    user: CurrentUser,
    db: Session,
    label: str,
) -> RowT:
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
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can edit global defaults")
    elif not user_owns(user, row.client_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found")
    return row


def assert_policy_year_for_user(policy_year_id: str, user: CurrentUser, db: Session) -> PolicyYear:
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
    stmt = (
        select(Category)
        .join(PolicyYear, Category.policy_year_id == PolicyYear.id)
        .where(Category.id == category_id)
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
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Employee:
    e = db.get(Employee, employee_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    if not user_owns(user, e.client_id):
        raise _deny_cross_tenant(user, "Employee", employee_id)
    _assert_selected_policy_year(request, e.policy_year_id, "Employee")
    return e


def load_dependant(
    dependant_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dependant:
    d = db.get(Dependant, dependant_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dependant not found")
    if not user_owns(user, d.client_id):
        raise _deny_cross_tenant(user, "Dependant", dependant_id)
    _assert_selected_policy_year(request, d.policy_year_id, "Dependant")
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
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentWindow:
    """Load an EnrollmentWindow, proving tenant ownership via its client_id."""
    w = db.get(EnrollmentWindow, window_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment period not found")
    if not user_owns(user, w.client_id):
        raise _deny_cross_tenant(user, "Enrollment window", window_id)
    _assert_selected_policy_year(request, w.policy_year_id, "Enrollment period")
    return w


def load_bulk_plan_update(
    batch_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkPlanUpdate:
    """Load a bulk coverage batch, proving tenant ownership via its client_id."""
    b = db.get(BulkPlanUpdate, batch_id)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coverage change not found")
    if not user_owns(user, b.client_id):
        raise _deny_cross_tenant(user, "Bulk plan update", batch_id)
    _assert_selected_policy_year(request, b.policy_year_id, "Coverage change")
    return b


def load_enrollment(
    enrollment_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Enrollment:
    """Load an Enrollment, proving tenant ownership via its client_id."""
    e = db.get(Enrollment, enrollment_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrollment not found")
    if not user_owns(user, e.client_id):
        raise _deny_cross_tenant(user, "Enrollment", enrollment_id)
    _assert_selected_policy_year(request, e.policy_year_id, "Enrollment")
    return e


def load_claim(
    claim_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Claim:
    """Load a Claim, proving tenant ownership via its client_id."""
    c = db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    if not user_owns(user, c.client_id):
        raise _deny_cross_tenant(user, "Claim", claim_id)
    _assert_selected_policy_year(request, c.policy_year_id, "Claim")
    return c


def load_report_version(
    version_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportVersion:
    """Load a ReportVersion, proving tenant ownership via its client_id."""
    rv = db.get(ReportVersion, version_id)
    if rv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report version not found")
    if not user_owns(user, rv.client_id):
        raise _deny_cross_tenant(user, "Report version", version_id)
    _assert_selected_policy_year(request, rv.policy_year_id, "Report version")
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
