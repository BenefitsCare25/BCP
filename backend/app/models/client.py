"""Broker firm + Client (tenant) models."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid

DEFAULT_AI_MONTHLY_TOKEN_BUDGET = 100_000

if TYPE_CHECKING:
    from app.models.client_ai_config import ClientAIConfig
    from app.models.policy_year import PolicyYear


class BrokerFirm(Base, TimestampMixin):
    __tablename__ = "broker_firms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    clients: Mapped[list[Client]] = relationship(back_populates="broker_firm")


class Client(Base, TimestampMixin):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    broker_firm_id: Mapped[str] = mapped_column(
        ForeignKey("broker_firms.id", ondelete="RESTRICT"), nullable=False
    )
    ai_monthly_token_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_AI_MONTHLY_TOKEN_BUDGET
    )

    broker_firm: Mapped[BrokerFirm] = relationship(back_populates="clients")
    policy_years: Mapped[list[PolicyYear]] = relationship(back_populates="client")
    ai_config: Mapped[ClientAIConfig | None] = relationship(
        back_populates="client",
        uselist=False,
        cascade="all, delete-orphan",
    )
