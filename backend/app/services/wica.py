"""WICA scope, serialization and concurrency boundary. No portal side effects."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.core.deps import require_client_id
from app.models import StoredDocument, WicaDocument, WicaIncident, WicaPack, WicaSettings


def incident(db: Session, user: CurrentUser, incident_id: str) -> WicaIncident:
    row = db.scalar(
        select(WicaIncident).where(
            WicaIncident.id == incident_id, WicaIncident.client_id == require_client_id(user)
        )
    )
    if row is None:
        raise HTTPException(404, "Incident not found")
    return row


def enabled(db: Session, user: CurrentUser, *, lock: bool = False) -> None:
    query = select(WicaSettings).where(WicaSettings.client_id == require_client_id(user))
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None or not row.enabled:
        raise HTTPException(
            409, "Enable WICA in Company settings before adding incidents or documents."
        )


def advance(db: Session, row: WicaIncident, revision: int) -> None:
    changed = db.scalar(
        update(WicaIncident)
        .where(
            WicaIncident.id == row.id,
            WicaIncident.client_id == row.client_id,
            WicaIncident.revision == revision,
        )
        .values(revision=revision + 1)
        .returning(WicaIncident.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        raise HTTPException(409, "This incident has changed. Refresh and try again.")
    db.refresh(row)


def document(db: Session, row: WicaIncident, document_id: str) -> WicaDocument:
    doc = db.scalar(
        select(WicaDocument).where(
            WicaDocument.id == document_id, WicaDocument.incident_id == row.id
        )
    )
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


def summary(row: WicaIncident) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "period_id",
            "employee_id",
            "employee_name",
            "staff_id",
            "incident_date",
            "report_number",
            "remarks",
            "revision",
            "created_at",
        )
    }


def detail(db: Session, row: WicaIncident) -> dict[str, Any]:
    docs = db.execute(
        select(WicaDocument, StoredDocument)
        .join(StoredDocument, WicaDocument.stored_document_id == StoredDocument.id)
        .where(WicaDocument.incident_id == row.id, StoredDocument.client_id == row.client_id)
        .order_by(WicaDocument.created_at, WicaDocument.id)
    ).all()
    packs = db.scalars(
        select(WicaPack).where(WicaPack.incident_id == row.id).order_by(WicaPack.created_at.desc())
    ).all()
    return {
        **summary(row),
        "documents": [
            {
                **{
                    key: getattr(doc, key)
                    for key in (
                        "id",
                        "doc_type",
                        "document_date",
                        "benefit_type",
                        "claim_id",
                        "status",
                        "related_ids",
                        "provider",
                        "invoice_number",
                        "incurred_amount",
                        "settlement_amount",
                        "settlement_date",
                        "insurer_reference",
                        "remarks",
                    )
                },
                "file_name": stored.file_name,
                "size_bytes": stored.size_bytes,
                "created_at": stored.created_at,
            }
            for doc, stored in docs
        ],
        "packs": [
            {
                "id": p.id,
                "created_at": p.created_at,
                "sent_on": p.sent_on,
                "sent_reference": p.sent_reference,
                "document_count": len(p.manifest),
            }
            for p in packs
        ],
    }
