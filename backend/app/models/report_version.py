"""Retained report versions (tenant table).

A generated Reports Center document, persisted so brokers keep a record of what
they submitted to an insurer and when. Two retention modes (see
``services/report_registry.py``):

- ``versioned``: a growing immutable series per (policy_year, report_type,
  scope_key) — the insurer employee/dependant listings + benefit selection.
- ``latest``: one retained copy per (policy_year, report_type); regenerating
  supersedes the prior row + blob.

The bytes live in the storage backend (``app/core/storage.py``); this row is
the queryable record (who/when/counts + the membership ``manifest`` that powers
the movement diff for the two insurer listings).
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

MODE_VERSIONED = "versioned"
MODE_LATEST = "latest"


class ReportVersion(Base, TimestampMixin):
    __tablename__ = "report_versions"
    __table_args__ = (
        # Drives both "next version_no" and history-list queries. No UNIQUE —
        # latest-mode supersede replaces a row and must not fight a constraint.
        Index(
            "ix_report_versions_series",
            "client_id",
            "policy_year_id",
            "report_type",
            "scope_key",
            "version_no",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_type: Mapped[str] = mapped_column(String(48), nullable=False)
    # Series discriminator within a year: insurer name (listings), window id
    # (benefit selection), or NULL (latest-mode config reports).
    scope_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Exact request params (insurer/masked/window_id) — reproducibility + audit.
    params: Mapped[dict] = mapped_column(JSON(), nullable=False, default=dict)
    # Counts shown in the history list (members, adds/leavers, manifest_hash).
    summary: Mapped[dict] = mapped_column(JSON(), nullable=False, default=dict)
    # Membership fingerprint for the movement diff (insurer listings only).
    manifest: Mapped[dict | None] = mapped_column(JSON(), nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    generated_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
