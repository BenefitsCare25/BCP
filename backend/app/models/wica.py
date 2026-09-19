"""Broker-only WICA records, deliberately separate from portal claims."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class WicaSettings(Base, TimestampMixin):
    __tablename__ = "wica_settings"
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class WicaPeriod(Base, TimestampMixin):
    __tablename__ = "wica_periods"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="dates_valid"),
        CheckConstraint("grace_days IS NULL OR grace_days >= 0", name="grace_valid"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    grace_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WicaIncident(Base, TimestampMixin):
    __tablename__ = "wica_incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    period_id: Mapped[str] = mapped_column(ForeignKey("wica_periods.id"), index=True)
    employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    employee_name: Mapped[str] = mapped_column(String(255))
    staff_id: Mapped[str] = mapped_column(String(128))
    incident_date: Mapped[date] = mapped_column(Date, index=True)
    report_number: Mapped[str] = mapped_column(String(128), default="")
    remarks: Mapped[str] = mapped_column(String(4000), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class WicaDocument(Base, TimestampMixin):
    __tablename__ = "wica_documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('untagged','supporting','submitted',"
            "'pending_insurer','settled','rejected')",
            name="status_valid",
        ),
        CheckConstraint("settlement_amount IS NULL OR settlement_amount >= 0", name="amount_valid"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("wica_incidents.id"), index=True)
    stored_document_id: Mapped[str] = mapped_column(ForeignKey("stored_documents.id"), unique=True)
    doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    benefit_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_id: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="untagged")
    related_ids: Mapped[list[str]] = mapped_column(JSON(), default=list)
    provider: Mapped[str] = mapped_column(String(255), default="")
    invoice_number: Mapped[str] = mapped_column(String(128), default="")
    incurred_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    settlement_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    insurer_reference: Mapped[str] = mapped_column(String(128), default="")
    remarks: Mapped[str] = mapped_column(String(2000), default="")


class WicaPack(Base, TimestampMixin):
    __tablename__ = "wica_packs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("wica_incidents.id"), index=True)
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON())
    created_by: Mapped[str] = mapped_column(String(36))
    sent_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sent_reference: Mapped[str] = mapped_column(String(255), default="")
