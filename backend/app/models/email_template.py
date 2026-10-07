"""Firm-owned templates, immutable publications and explicitly unsent preparations."""

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class EmailTemplate(Base, TimestampMixin):
    __tablename__ = "email_templates"
    __table_args__ = (UniqueConstraint("broker_firm_id", "scope_key", "template_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    broker_firm_id: Mapped[str] = mapped_column(ForeignKey("broker_firms.id"), index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id"), index=True)
    # Non-null keys make firm-default uniqueness portable to SQLite/Postgres.
    scope_key: Mapped[str] = mapped_column(String(40))
    template_key: Mapped[str] = mapped_column(String(80))
    draft: Mapped[dict[str, Any]] = mapped_column(JSON())
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_version: Mapped[int | None] = mapped_column(Integer)


class EmailTemplateVersion(Base, TimestampMixin):
    __tablename__ = "email_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    template_id: Mapped[str] = mapped_column(ForeignKey("email_templates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSON())
    published_by: Mapped[str] = mapped_column(String(36))


class EmailBranding(Base, TimestampMixin):
    __tablename__ = "email_branding"
    __table_args__ = (UniqueConstraint("broker_firm_id", "scope_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    broker_firm_id: Mapped[str] = mapped_column(ForeignKey("broker_firms.id"), index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id"))
    scope_key: Mapped[str] = mapped_column(String(40))
    content: Mapped[dict[str, Any]] = mapped_column(JSON())
    revision: Mapped[int] = mapped_column(Integer, default=1)


class EmailPreparation(Base, TimestampMixin):
    __tablename__ = "email_preparations"
    __table_args__ = (UniqueConstraint("client_id", "request_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    broker_firm_id: Mapped[str] = mapped_column(ForeignKey("broker_firms.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(36))
    template_key: Mapped[str] = mapped_column(String(80))
    template_title: Mapped[str] = mapped_column(String(120))
    template_version: Mapped[str] = mapped_column(String(100))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON())
    recipients: Mapped[list[dict[str, Any]]] = mapped_column(JSON())
    created_by: Mapped[str] = mapped_column(String(36))
    # No worker consumes this table. SMTP configuration must never auto-send it.
    status: Mapped[str] = mapped_column(String(16), default="prepared")
