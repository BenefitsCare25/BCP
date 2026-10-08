"""Email preparation without delivery: no mailer, token minting or account mutations."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import is_firm_owner, platform_access_read_only
from app.core.settings import get_settings
from app.db.session import get_db
from app.db.tenancy import set_search_path
from app.models import Client
from app.models.email_template import (
    EmailBranding,
    EmailPreparation,
    EmailTemplate,
    EmailTemplateVersion,
)
from app.schemas.email_templates import (
    Audience,
    BrandingContent,
    BrandingIn,
    DraftIn,
    PrepareIn,
    PreviewIn,
    Purpose,
    RevisionIn,
    Scope,
    SelectionIn,
    TemplateContent,
)
from app.services import email_template_store as store
from app.services.email_template_content import (
    FIELDS,
    render,
    starters,
    validate_branding,
    validate_content,
    validate_values,
)
from app.services.email_template_recipients import context_values, recipients

router = APIRouter(prefix="/email-templates", tags=["email-templates"])
DELIVERY_REASON = (
    "Email delivery is not configured for this module. You can save templates, "
    "preview and prepare recipients. Nothing will be sent."
)


def actor(response: Response, user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    response.headers["Cache-Control"] = "no-store"
    if user.role not in ("broker_admin", "broker_viewer", "firm_admin", "system_admin"):
        raise HTTPException(403, "Email setup requires a broker role.")
    return user


def writer(user: CurrentUser = Depends(actor)) -> CurrentUser:
    if user.role == "broker_viewer":
        raise HTTPException(403, "The broker_viewer role is read-only.")
    # Registered outside the broker router loop, so `require_write_access`
    # does not refuse a master admin's read-only grant here.
    if user.platform_access == "read":
        raise platform_access_read_only()
    return user


def context(db: Session, user: CurrentUser, scope: Scope) -> tuple[str, Client | None]:
    client = db.get(Client, user.client_id) if user.client_id else None
    if client and user.role != "system_admin" and client.broker_firm_id != user.broker_firm_id:
        raise HTTPException(404, "Company not found.")
    firm_id = client.broker_firm_id if client else user.broker_firm_id
    if not firm_id or (scope == "company" and client is None):
        raise HTTPException(400, "Select a company to configure email.")
    set_search_path(db, firm_id)
    return firm_id, client if scope == "company" else None


def valid_or_raise(content: TemplateContent) -> None:
    errors = validate_content(content)
    if errors:
        raise HTTPException(
            422, {"message": "Correct the template fields before continuing.", "fields": errors}
        )


@router.get("")
def list_templates(
    scope: Scope = "company", user: CurrentUser = Depends(actor), db: Session = Depends(get_db)
) -> dict[str, Any]:
    firm_id, client = context(db, user, scope)
    client_id = client.id if client else None
    keys = set(starters()) | {
        row.template_key for row in store.template_rows(db, firm_id, client_id)
    }
    items = [store.template_out(db, firm_id, client_id, key) for key in sorted(keys)]
    return {
        "items": items,
        "delivery_enabled": False,
        "delivery_reason": DELIVERY_REASON,
        "placeholders": FIELDS,
        "company_name": client.legal_name or client.name if client else None,
    }


@router.get("/branding")
def get_branding(
    scope: Scope = "company", user: CurrentUser = Depends(actor), db: Session = Depends(get_db)
) -> dict[str, Any]:
    firm_id, client = context(db, user, scope)
    return store.branding_out(db, firm_id, client.id if client else None)


@router.put("/branding")
def save_branding(
    body: BrandingIn,
    scope: Scope = "company",
    user: CurrentUser = Depends(writer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    firm_id, client = context(db, user, scope)
    content = store.with_brand_defaults(db, firm_id, client.id if client else None, body.content)
    errors = validate_branding(content)
    if errors:
        raise HTTPException(422, {"message": "Correct the branding fields.", "fields": errors})
    key = client.id if client else "firm"
    row = db.scalar(
        select(EmailBranding).where(
            EmailBranding.broker_firm_id == firm_id, EmailBranding.scope_key == key
        )
    )
    if row:
        changed = db.execute(
            update(EmailBranding)
            .where(
                EmailBranding.id == row.id,
                EmailBranding.revision == body.revision,
            )
            .values(content=content.model_dump(), revision=body.revision + 1)
            .returning(EmailBranding.id)
        )
        if not changed.scalar_one_or_none():
            raise store.stale()
    else:
        if body.revision:
            raise store.stale()
        row = EmailBranding(
            broker_firm_id=firm_id,
            client_id=client.id if client else None,
            scope_key=key,
            content=content.model_dump(),
            revision=1,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise store.stale() from exc
    write_audit(
        db,
        user,
        "email_branding.saved",
        "email_branding",
        row.id,
        after={"scope": scope, "revision": body.revision + 1},
    )
    db.commit()
    return get_branding(scope, user, db)


@router.get("/recipients")
def get_recipients(
    audience: Audience = "employee",
    purpose: Purpose = "general",
    policy_year_id: str | None = None,
    search: str = Query("", max_length=120),
    account_status: str = Query("", max_length=20),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user: CurrentUser = Depends(actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, client = context(db, user, "company")
    assert client
    rows = recipients(
        db, client, user, TemplateContent(audience=audience, purpose=purpose), policy_year_id
    )
    query = search.casefold().strip()
    rows = [
        row
        for row in rows
        if (
            not query or any(query in str(row[k]).casefold() for k in ("name", "staff_id", "email"))
        )
        and (not account_status or row["status"] == account_status)
    ]
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "eligible_ids": [row["id"] for row in rows if row["eligible"]],
    }


@router.delete("/branding", status_code=204)
def reset_branding(
    scope: Scope = "company", user: CurrentUser = Depends(writer), db: Session = Depends(get_db)
) -> Response:
    if not is_firm_owner(user):
        raise HTTPException(403, "Resetting saved branding requires a firm administrator.")
    firm_id, client = context(db, user, scope)
    row = db.scalar(
        select(EmailBranding).where(
            EmailBranding.broker_firm_id == firm_id,
            EmailBranding.scope_key == (client.id if client else "firm"),
        )
    )
    if not row:
        raise HTTPException(404, "Saved branding not found.")
    db.delete(row)
    write_audit(db, user, "email_branding.reset", "email_branding", row.id, after={"scope": scope})
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/preview")
def preview(
    body: PreviewIn,
    scope: Scope = "company",
    user: CurrentUser = Depends(actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    firm_id, client = context(db, user, scope)
    errors = validate_content(body.content)
    recipient = None
    if body.recipient_id:
        if not client:
            raise HTTPException(400, "Choose company scope for a real-recipient preview.")
        recipient = next(
            (
                row
                for row in recipients(db, client, user, body.content, body.policy_year_id)
                if row["id"] == body.recipient_id
            ),
            None,
        )
        if not recipient:
            raise HTTPException(404, "Recipient not found in this company and benefit year.")
    branding = BrandingContent.model_validate(
        store.branding_out(db, firm_id, client.id if client else None)["content"]
    )
    values, warnings = context_values(client, body.content, branding, recipient)
    resolved_errors = validate_values(body.content, values)
    errors = resolved_errors | errors
    return {
        **render(body.content, branding, values),
        "errors": errors,
        "warnings": warnings,
        "values": values,
        "data_source": "real" if recipient else "sample",
        "recipient_email": recipient["email"] if recipient else None,
        "valid": not errors,
        "activation_link_generated": False,
    }


def selection_snapshot(body: SelectionIn, user: CurrentUser, db: Session) -> dict[str, Any]:
    firm_id, client = context(db, user, "company")
    assert client
    effective = store.published(db, firm_id, client.id, body.template_key)
    if not effective:
        raise HTTPException(409, "Publish this template before preparing recipients.")
    content, version, source = effective
    valid_or_raise(content)
    ids = set(body.recipient_ids)
    if len(ids) != len(body.recipient_ids):
        raise HTTPException(422, "Select each recipient only once.")
    rows = [
        row
        for row in recipients(db, client, user, content, body.policy_year_id)
        if row["id"] in ids
    ]
    if len(rows) != len(ids):
        raise HTTPException(404, "A selected recipient is not in this company and benefit year.")
    if content.purpose != "general" and not client.slug:
        raise HTTPException(
            422, "Configure the company portal address before preparing invitations."
        )
    branding = BrandingContent.model_validate(store.branding_out(db, firm_id, client.id)["content"])
    company_values, _ = context_values(client, content, branding, None)
    for row in rows:
        values, _ = context_values(client, content, branding, row)
        resolved_errors = validate_values(content, values)
        if resolved_errors and row["eligible"]:
            row["eligible"], row["reason"] = False, next(iter(resolved_errors.values()))
    return {
        "firm_id": firm_id,
        "client_id": client.id,
        "content": content.model_dump(),
        "branding": branding.model_dump(),
        "version": version,
        "source": source,
        "policy_year_id": body.policy_year_id,
        "company_name": client.legal_name or client.name,
        "company_values": {key: company_values[key] for key in ("company_name", "portal_url")},
        "recipients": rows,
    }


def digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def review_key() -> str:
    return hmac.new(
        get_settings().portal_jwt_secret.encode(), b"email-review-v1", hashlib.sha256
    ).hexdigest()


@router.post("/review")
def review_selection(
    body: SelectionIn, user: CurrentUser = Depends(writer), db: Session = Depends(get_db)
) -> dict[str, Any]:
    snapshot = selection_snapshot(body, user, db)
    rows = snapshot["recipients"]
    token = jwt.encode(
        {
            "typ": "email_review",
            "sub": user.user_id,
            "digest": digest(snapshot),
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        review_key(),
        algorithm="HS256",
    )
    return {
        "recipients": rows,
        "eligible_count": sum(bool(row["eligible"]) for row in rows),
        "excluded_count": sum(not row["eligible"] for row in rows),
        "account_creation_count": sum(row["eligible"] and row["account_required"] for row in rows),
        "template_version": snapshot["version"],
        "review_token": token,
        "delivery_enabled": False,
        "delivery_reason": DELIVERY_REASON,
    }


@router.post("/prepare", status_code=201)
def prepare_selection(
    body: PrepareIn, user: CurrentUser = Depends(writer), db: Session = Depends(get_db)
) -> dict[str, Any]:
    snapshot = selection_snapshot(body, user, db)
    try:
        claims = jwt.decode(
            body.review_token,
            review_key(),
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "typ", "digest"]},
        )
        if (
            claims["typ"] != "email_review"
            or claims["sub"] != user.user_id
            or claims["digest"] != digest(snapshot)
        ):
            raise ValueError("Changed selection")
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise HTTPException(
            409, "The selection or template changed. Review recipients again."
        ) from exc
    selected = [row for row in snapshot["recipients"] if row["eligible"]]
    if not selected:
        raise HTTPException(422, "Select at least one eligible recipient.")
    prior = db.scalar(
        select(EmailPreparation).where(
            EmailPreparation.client_id == snapshot["client_id"],
            EmailPreparation.request_key == str(body.request_key),
        )
    )
    if prior:
        if prior.snapshot.get("review_digest") != digest(snapshot):
            raise HTTPException(409, "This request was already used for a different preparation.")
        return {"id": prior.id, "status": "prepared", "recipient_count": len(prior.recipients)}
    row = EmailPreparation(
        client_id=snapshot["client_id"],
        broker_firm_id=snapshot["firm_id"],
        request_key=str(body.request_key),
        template_key=body.template_key,
        template_title=snapshot["content"]["title"],
        template_version=snapshot["version"],
        snapshot={key: value for key, value in snapshot.items() if key != "recipients"}
        | {"review_digest": digest(snapshot)},
        recipients=[{key: recipient[key] for key in ("id", "email")} for recipient in selected],
        created_by=user.user_id,
        status="prepared",
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "This preparation already exists. Refresh the prepared messages."
        ) from exc
    write_audit(
        db,
        user,
        "email.prepared",
        "email_preparation",
        row.id,
        after={"template_key": body.template_key, "recipient_count": len(selected)},
    )
    db.commit()
    return {"id": row.id, "status": "prepared", "recipient_count": len(selected)}


@router.get("/preparations")
def preparations(
    user: CurrentUser = Depends(actor), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    firm_id, client = context(db, user, "company")
    assert client
    rows = db.scalars(
        select(EmailPreparation)
        .where(EmailPreparation.client_id == client.id, EmailPreparation.broker_firm_id == firm_id)
        .order_by(EmailPreparation.created_at.desc())
        .limit(100)
    )
    return [
        {
            "id": row.id,
            "title": row.template_title,
            "version": row.template_version,
            "recipient_count": len(row.recipients),
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/preparations/{preparation_id}")
def preparation_detail(
    preparation_id: str, user: CurrentUser = Depends(actor), db: Session = Depends(get_db)
) -> dict[str, Any]:
    firm_id, client = context(db, user, "company")
    assert client
    row = db.scalar(
        select(EmailPreparation).where(
            EmailPreparation.id == preparation_id,
            EmailPreparation.client_id == client.id,
            EmailPreparation.broker_firm_id == firm_id,
        )
    )
    if not row:
        raise HTTPException(404, "Preparation not found.")
    content = TemplateContent.model_validate(row.snapshot["content"])
    if content.audience == "hr" and user.role == "broker_viewer":
        raise HTTPException(403, "HR account details require broker administration access.")
    branding = BrandingContent.model_validate(row.snapshot["branding"])
    # Sample only recipient data. Older preparations did not snapshot the portal
    # URL, so use their authorized company's current URL as the fallback.
    values, _ = context_values(client, content, branding, None)
    values["company_name"] = row.snapshot.get("company_name") or values["company_name"]
    saved_company_values = row.snapshot.get("company_values", {})
    for key in ("company_name", "portal_url"):
        if key in saved_company_values:
            values[key] = saved_company_values[key]
    return {
        "id": row.id,
        "title": row.template_title,
        "version": row.template_version,
        "created_at": row.created_at.isoformat(),
        "recipients": row.recipients,
        "content": content.model_dump(),
        "preview": render(content, branding, values),
    }


@router.post("/send")
@router.post("/test")
def delivery_disabled(user: CurrentUser = Depends(writer)) -> None:
    # Explicit product gate, even in local log mode or after SMTP env changes.
    raise HTTPException(503, DELIVERY_REASON)


@router.post("", status_code=201)
def create_template(
    body: DraftIn,
    scope: Scope = "company",
    user: CurrentUser = Depends(writer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    firm_id, client = context(db, user, scope)
    if not body.content.title.strip():
        raise HTTPException(422, "Enter a template title.")
    key = str(uuid4())
    store.save_draft(db, firm_id, client.id if client else None, key, body.content, 0)
    write_audit(db, user, "email_template.created", "email_template", key, after={"scope": scope})
    db.commit()
    return store.template_out(db, firm_id, client.id if client else None, key)


@router.put("/{key}")
def save_template(
    key: str,
    body: DraftIn,
    scope: Scope = "company",
    user: CurrentUser = Depends(writer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    firm_id, client = context(db, user, scope)
    store.template_out(db, firm_id, client.id if client else None, key)
    if not body.content.title.strip():
        raise HTTPException(422, "Enter a template title.")
    store.save_draft(db, firm_id, client.id if client else None, key, body.content, body.revision)
    write_audit(db, user, "email_template.saved", "email_template", key, after={"scope": scope})
    db.commit()
    return store.template_out(db, firm_id, client.id if client else None, key)


@router.post("/{key}/publish")
def publish_template(
    key: str,
    body: RevisionIn,
    scope: Scope = "company",
    user: CurrentUser = Depends(writer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    firm_id, client = context(db, user, scope)
    row = store.exact_row(db, firm_id, client.id if client else "firm", key)
    if not row:
        raise HTTPException(404, "Save a draft before publishing.")
    valid_or_raise(TemplateContent.model_validate(row.draft))
    number = (row.published_version or 0) + 1
    changed = db.execute(
        update(EmailTemplate)
        .where(EmailTemplate.id == row.id, EmailTemplate.revision == body.revision)
        .values(published_version=number, revision=body.revision + 1)
        .returning(EmailTemplate.id)
    )
    if not changed.scalar_one_or_none():
        raise store.stale()
    db.add(
        EmailTemplateVersion(
            template_id=row.id, version=number, content=row.draft, published_by=user.user_id
        )
    )
    write_audit(
        db,
        user,
        "email_template.published",
        "email_template",
        row.id,
        after={"scope": scope, "version": number},
    )
    db.commit()
    return store.template_out(db, firm_id, client.id if client else None, key)


@router.get("/{key}/versions")
def template_versions(
    key: str,
    scope: Scope = "company",
    user: CurrentUser = Depends(actor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    firm_id, client = context(db, user, scope)
    store.template_out(db, firm_id, client.id if client else None, key)
    row = store.exact_row(db, firm_id, client.id if client else "firm", key)
    if not row:
        return []
    return [
        {
            "version": version.version,
            "content": version.content,
            "created_at": version.created_at.isoformat(),
        }
        for version in db.scalars(
            select(EmailTemplateVersion)
            .where(EmailTemplateVersion.template_id == row.id)
            .order_by(EmailTemplateVersion.version.desc())
        )
    ]


@router.delete("/{key}", status_code=204)
def remove_template(
    key: str,
    scope: Scope = "company",
    user: CurrentUser = Depends(writer),
    db: Session = Depends(get_db),
) -> Response:
    if not is_firm_owner(user):
        raise HTTPException(403, "Removing saved templates requires a firm administrator.")
    firm_id, client = context(db, user, scope)
    row = store.exact_row(db, firm_id, client.id if client else "firm", key)
    if not row:
        raise HTTPException(404, "Saved template not found.")
    db.execute(delete(EmailTemplateVersion).where(EmailTemplateVersion.template_id == row.id))
    db.delete(row)
    write_audit(db, user, "email_template.removed", "email_template", key, after={"scope": scope})
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
