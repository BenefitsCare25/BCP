"""Alembic environment — reads engine config from app.db.session."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app.db.base import Base
from app.db.session import DATABASE_URL, engine
from app.models import (  # noqa: F401 — register models with metadata
    AISpendLog,
    AuditLog,
    BrokerFirm,
    Category,
    Client,
    Dependant,
    Employee,
    EmployeeAttributeSchema,
    Invitation,
    PlacementSlipRow,
    PlanAttributeSchema,
    PolicyYear,
    Product,
    User,
    UserClientAccess,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
