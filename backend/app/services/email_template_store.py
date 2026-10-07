"""Scoped draft/publication resolution and optimistic edits."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.email_template import EmailBranding, EmailTemplate, EmailTemplateVersion
from app.schemas.email_templates import BrandingContent, TemplateContent
from app.services.email_template_content import starters


def template_rows(db: Session, firm_id: str, client_id: str | None) -> list[EmailTemplate]:
    return list(
        db.scalars(
            select(EmailTemplate).where(
                EmailTemplate.broker_firm_id == firm_id,
                EmailTemplate.scope_key.in_(["firm", client_id or "firm"]),
            )
        )
    )


def exact_row(db: Session, firm_id: str, scope_key: str, key: str) -> EmailTemplate | None:
    return db.scalar(
        select(EmailTemplate).where(
            EmailTemplate.broker_firm_id == firm_id,
            EmailTemplate.scope_key == scope_key,
            EmailTemplate.template_key == key,
        )
    )


def published(
    db: Session, firm_id: str, client_id: str | None, key: str
) -> tuple[TemplateContent, str, str] | None:
    rows = template_rows(db, firm_id, client_id)
    for scope_key in [client_id, "firm"] if client_id else ["firm"]:
        row = next(
            (item for item in rows if item.template_key == key and item.scope_key == scope_key),
            None,
        )
        if row and row.published_version:
            version = db.scalar(
                select(EmailTemplateVersion).where(
                    EmailTemplateVersion.template_id == row.id,
                    EmailTemplateVersion.version == row.published_version,
                )
            )
            if version:
                return (
                    TemplateContent.model_validate(version.content),
                    f"{row.id}:{version.version}",
                    "company" if row.client_id else "firm",
                )
    builtin = starters().get(key)
    return (builtin, f"builtin:{key}:1", "builtin") if builtin else None


def template_out(db: Session, firm_id: str, client_id: str | None, key: str) -> dict[str, Any]:
    row = exact_row(db, firm_id, client_id or "firm", key)
    effective = published(db, firm_id, client_id, key)
    inherited = exact_row(db, firm_id, "firm", key) if client_id else None
    if not row and not inherited and not effective:
        raise HTTPException(404, "Email template not found.")
    content = (
        row.draft
        if row
        else effective[0].model_dump()
        if effective
        else inherited.draft
        if inherited
        else {}
    )
    return {
        "key": key,
        "content": content,
        "revision": row.revision if row else 0,
        "published_content": effective[0].model_dump() if effective else None,
        "published_version": effective[1] if effective else None,
        "source": effective[2] if effective else "draft",
        "has_local_draft": row is not None,
        "has_changes": effective is None or content != effective[0].model_dump(),
        "updated_at": row.updated_at.isoformat() if row else None,
    }


def stale() -> HTTPException:
    return HTTPException(
        409, "This setting changed. Reload before saving; your draft has been preserved."
    )


def save_draft(
    db: Session,
    firm_id: str,
    client_id: str | None,
    key: str,
    content: TemplateContent,
    revision: int,
) -> EmailTemplate:
    row = exact_row(db, firm_id, client_id or "firm", key)
    if row:
        changed = db.execute(
            update(EmailTemplate)
            .where(
                EmailTemplate.id == row.id,
                EmailTemplate.revision == revision,
            )
            .values(draft=content.model_dump(), revision=revision + 1)
            .returning(EmailTemplate.id)
        )
        if not changed.scalar_one_or_none():
            raise stale()
    else:
        if revision:
            raise stale()
        row = EmailTemplate(
            broker_firm_id=firm_id,
            client_id=client_id,
            scope_key=client_id or "firm",
            template_key=key,
            draft=content.model_dump(),
            revision=1,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise stale() from exc
    db.refresh(row)
    return row


def branding_out(db: Session, firm_id: str, client_id: str | None) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(EmailBranding).where(
                EmailBranding.broker_firm_id == firm_id,
                EmailBranding.scope_key.in_(["firm", client_id or "firm"]),
            )
        )
    )
    exact = next((row for row in rows if row.scope_key == (client_id or "firm")), None)
    row = exact or next((item for item in rows if item.scope_key == "firm"), None)
    return {
        "content": row.content if row else BrandingContent().model_dump(),
        "revision": exact.revision if exact else 0,
        "source": ("company" if row.client_id else "firm") if row else "builtin",
        "has_override": exact is not None,
    }
