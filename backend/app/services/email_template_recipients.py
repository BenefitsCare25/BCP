"""Read-only recipient resolution. Preview must never provision or issue credentials."""

from collections import Counter, defaultdict
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.core.auth import CurrentUser
from app.core.hr_auth import get_auth_policy
from app.core.tenant_resolution import FirmOriginUnavailable, public_origin
from app.models import (
    AuthCredential,
    Client,
    Employee,
    MemberAccount,
    PolicyYear,
    User,
    UserClientAccess,
)
from app.models.platform import DOMAIN_SURFACE_CLIENT
from app.schemas.email_templates import BrandingContent, TemplateContent
from app.services.email_template_content import valid_email
from app.services.member_invite import login_username, portal_sign_in_url
from app.services.roster_attributes import EMAIL_KEYS, first_value


def roster_email(employee: Employee) -> str:
    return (first_value(employee.attribute_values or {}, EMAIL_KEYS) or "").strip().lower()


def employee_recipients(
    db: Session, client: Client, year_id: str | None, purpose: str
) -> list[dict[str, Any]]:
    year = db.get(PolicyYear, year_id) if year_id else None
    if not year or year.client_id != client.id:
        raise HTTPException(404, "Select a benefit year belonging to this company.")
    roster = list(db.scalars(select(Employee).where(Employee.client_id == client.id)))
    accounts = list(db.scalars(select(MemberAccount).where(MemberAccount.client_id == client.id)))
    by_staff = {account.staff_id: account for account in accounts}
    by_id = {account.id: account for account in accounts}
    owners: dict[str, set[str]] = defaultdict(set)
    for employee in roster:
        if employee.status == "active" and roster_email(employee):
            owners[roster_email(employee)].add(employee.staff_id)
    for owner_account in accounts:
        if owner_account.email:
            owners[owner_account.email.strip().lower()].add(owner_account.staff_id)
    selected = [employee for employee in roster if employee.policy_year_id == year_id]
    counts = Counter(employee.staff_id for employee in selected)
    policy = get_auth_policy(db, client.id)
    result = []
    for employee in selected:
        account = by_id.get(employee.member_account_id or "") or by_staff.get(employee.staff_id)
        email = roster_email(employee)
        state = "not_invited"
        login_id = ""
        reason = ""
        if account:
            state = (
                "disabled"
                if account.status == "disabled"
                else (
                    "activated"
                    if account.last_sign_in_at or account.status == "active"
                    else "invited"
                    if account.invite_sent_at
                    else "not_invited"
                )
            )
            login_id = login_username(account, policy.portal_login_source)
            if purpose != "general":
                email = (account.email or "").strip().lower()
            if account.staff_id != employee.staff_id:
                reason = "Account ownership needs review."
            elif (
                account.email
                and roster_email(employee)
                and account.email.lower() != roster_email(employee)
            ):
                reason = "Roster and account email differ. Review the address first."
        if not reason:
            if employee.status != "active":
                reason = "Employee is inactive."
            elif counts[employee.staff_id] > 1:
                reason = "Duplicate staff ID in this benefit year."
            elif not valid_email(email):
                reason = "Missing or invalid email address."
            elif owners[email] - {employee.staff_id}:
                reason = "Email address is shared with another employee."
            elif purpose != "general" and state == "disabled":
                reason = "Account is disabled. Review it in Portal access."
            elif purpose == "invitation" and state in ("activated", "invited"):
                reason = (
                    "Already activated."
                    if state == "activated"
                    else "Already invited. Use the account resend workflow."
                )
            elif purpose == "password_reset" and not account:
                reason = "No portal account. Use a welcome invitation first."
        result.append(
            {
                "id": employee.id,
                "name": employee.employee_name or employee.staff_id,
                "staff_id": employee.staff_id,
                "email": email,
                "status": state,
                "eligible": not reason,
                "reason": reason,
                "login_identifier": login_id,
                "hr_role_label": "",
                "account_required": account is None and purpose == "invitation",
            }
        )
    return sorted(result, key=lambda row: (str(row["name"]).casefold(), str(row["id"])))


def hr_recipients(
    db: Session, client: Client, user: CurrentUser, purpose: str
) -> list[dict[str, Any]]:
    if user.role not in ("broker_admin", "firm_admin", "system_admin"):
        raise HTTPException(403, "HR account details require broker administration access.")
    policy = get_auth_policy(db, client.id)
    rows = db.execute(
        select(User, AuthCredential)
        .join(UserClientAccess, UserClientAccess.user_id == User.id)
        .join(AuthCredential, AuthCredential.user_id == User.id)
        .where(
            UserClientAccess.client_id == client.id,
            User.broker_firm_id == client.broker_firm_id,
            User.role.in_(("client_admin", "client_hr")),
        )
    )
    result = []
    for account, credential in rows:
        state = (
            "disabled"
            if account.status == "disabled"
            else (
                "activated"
                if credential.last_login_at or account.status == "active"
                else "not_invited"
            )
        )
        reason = ""
        if state == "disabled":
            reason = "Account is disabled."
        elif not valid_email(account.email):
            reason = "Invalid email address."
        elif purpose == "invitation" and state == "activated":
            reason = "Already activated."
        result.append(
            {
                "id": account.id,
                "name": account.display_name or account.email,
                "staff_id": "",
                "email": account.email,
                "status": state,
                "eligible": not reason,
                "reason": reason,
                "login_identifier": (
                    credential.hr_login_id
                    if policy.hr_login_source == "system_id"
                    else account.email
                )
                or "",
                "hr_role_label": "HR Administrator"
                if account.role == "client_admin"
                else "HR Officer",
                "account_required": False,
            }
        )
    return sorted(result, key=lambda row: str(row["name"]).casefold())


def recipients(
    db: Session, client: Client, user: CurrentUser, content: TemplateContent, year_id: str | None
) -> list[dict[str, Any]]:
    if content.audience == "hr":
        return hr_recipients(db, client, user, content.purpose)
    return employee_recipients(db, client, year_id, content.purpose)


def company_sign_in_url(client: Client, audience: str) -> str:
    """The company's sign-in link for `audience` (`hr` or `employee`).

    On the company's own broker address (`tenant_resolution.public_origin`):
    `/hr/sign-in?company=<alias>` for HR, `portal_sign_in_url` for employees.
    Raises `FirmOriginUnavailable` when the broker has no address yet, or when
    `client` is not attached to a session the address could be read through.
    """
    db = object_session(client)
    if db is None:
        raise FirmOriginUnavailable(client.broker_firm_id)
    if audience != "hr":
        return portal_sign_in_url(db, client)
    origin = public_origin(db, client.broker_firm_id, DOMAIN_SURFACE_CLIENT)
    return f"{origin}/hr/sign-in?company={quote(client.slug or '')}"


def context_values(
    client: Client | None,
    content: TemplateContent,
    branding: BrandingContent,
    recipient: dict[str, Any] | None,
) -> tuple[dict[str, str], list[str]]:
    warnings = []
    if client:
        company_name = client.legal_name or client.name
        if not client.legal_name:
            warnings.append(
                "Registered company name is missing; the company's display name is shown."
            )
        try:
            portal_url = company_sign_in_url(client, content.audience)
        except FirmOriginUnavailable:
            # Left blank: `validate_values` then refuses every template that
            # uses `{{portal_url}}`, so nothing is prepared with a dead link.
            portal_url = ""
            warnings.append(FirmOriginUnavailable.message)
        if not client.slug:
            warnings.append("Company portal address is not configured.")
    else:
        company_name, portal_url = (
            "Example Company (sample)",
            "https://example.invalid/portal/sign-in",
        )
    if recipient:
        name = str(recipient["name"])
        email, staff, login_id = (
            str(recipient[key]) for key in ("email", "staff_id", "login_identifier")
        )
        if not recipient["eligible"]:
            warnings.append(str(recipient["reason"]))
        if not login_id and (
            content.purpose != "general" or "login_identifier" in content.model_dump_json()
        ):
            warnings.append("Sign-in ID is unavailable; account setup has not been performed.")
    else:
        name, email, staff, login_id = (
            "Alex Tan (sample)",
            "alex@example.invalid",
            "SAMPLE-001",
            "SAMPLE-LOGIN",
        )
    if content.purpose != "general":
        warnings.append("Preview only. No activation link or credential has been generated.")
    return {
        "company_name": company_name,
        "recipient_name": name,
        "recipient_first_name": name.split()[0] if name else "there",
        "recipient_email": email,
        "staff_id": staff,
        "login_identifier": login_id
        or ("[Assigned when invitation is sent]" if content.purpose != "general" else ""),
        "portal_url": portal_url,
        "sender_display_name": branding.sender_display_name,
        "support_email": branding.support_email,
        "hr_role_label": str(recipient["hr_role_label"])
        if recipient
        else "HR Administrator (sample)",
        "activation_expires_at": "Link expires "
        + ("72" if content.audience == "employee" else "48")
        + " hours after sending.",
    }, warnings
