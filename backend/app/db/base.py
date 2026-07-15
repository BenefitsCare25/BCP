"""SQLAlchemy declarative base + shared model helpers."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON as _SAJSON
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine


def new_uuid() -> str:
    return str(uuid.uuid4())


def JSON() -> TypeEngine:
    """Cross-dialect JSON: native JSON on SQLite, JSONB on Postgres.

    Use this instead of `sa.JSON()` so JSONB indexing and ops work in prod
    without affecting the SQLite dev path.
    """
    return _SAJSON().with_variant(JSONB(), "postgresql")


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
