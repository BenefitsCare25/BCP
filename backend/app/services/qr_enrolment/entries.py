"""The shared employee QR of one enrolment period: create, pause, rotate, reveal.

The token is stored twice: its SHA-256 (how a visitor's link is found) and an
encrypted copy, so an authorised broker can download the same QR again and
copies HR has already forwarded keep working. Rotating replaces both, which
retires every copy of the old QR.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models import EnrollmentWindow, PublicFormEntry, PublicIntake
from app.schemas.qr_enrolment import QrEntryOut
from app.services.qr_enrolment.access import ENTRY_PATH, hash_token, link_url, new_token


def find_entry(db: Session, window_id: str) -> PublicFormEntry | None:
    return db.execute(
        select(PublicFormEntry).where(PublicFormEntry.window_id == window_id)
    ).scalar_one_or_none()


def _counts(db: Session, window_id: str) -> dict[str, int]:
    return {
        str(state): int(n)
        for state, n in db.execute(
            select(PublicIntake.status, func.count(PublicIntake.id))
            .where(PublicIntake.window_id == window_id)
            .group_by(PublicIntake.status)
        ).all()
    }


def entry_out(db: Session, window: EnrollmentWindow, entry: PublicFormEntry | None) -> QrEntryOut:
    counts = _counts(db, window.id)
    return QrEntryOut(
        exists=entry is not None,
        enabled=bool(entry and entry.enabled),
        window_id=window.id,
        window_name=window.name,
        window_status=window.status,
        closes_at=window.closes_at,
        created_at=entry.created_at if entry else None,
        rotated_at=entry.rotated_at if entry else None,
        pending=counts.get("pending", 0),
        linked=counts.get("linked", 0),
        rejected=counts.get("rejected", 0),
    )


def _set_token(entry: PublicFormEntry) -> None:
    raw = new_token()
    entry.token_hash = hash_token(raw)
    entry.token_enc = encrypt_secret(raw).decode("ascii")


def create_entry(db: Session, window: EnrollmentWindow, user_id: str) -> PublicFormEntry:
    entry = find_entry(db, window.id)
    if entry is not None:
        entry.enabled = True
        return entry
    entry = PublicFormEntry(
        client_id=window.client_id,
        policy_year_id=window.policy_year_id,
        window_id=window.id,
        enabled=True,
        created_by=user_id,
    )
    _set_token(entry)
    db.add(entry)
    db.flush()
    return entry


def rotate_entry(entry: PublicFormEntry) -> None:
    _set_token(entry)
    entry.rotated_at = datetime.now(UTC)


def entry_url(db: Session, entry: PublicFormEntry, firm_id: str) -> str:
    return link_url(db, firm_id, ENTRY_PATH, decrypt_secret(entry.token_enc.encode("ascii")))
