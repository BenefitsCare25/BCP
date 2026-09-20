"""Company-scoped servicer activity from immutable audit events, not case ownership."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Claim, User

EVENTS = {
    "claim.approve": "approved",
    "claim.reject": "rejected",
    "claim.needs_info": "needs_info",
    "claim.paid": "paid",
    "claim.sent_to_insurer": "sent_to_insurer",
    "claim.amended": "amended",
}


def claim_workload(db: Session, client_id: str, start: date, end: date) -> dict[str, Any]:
    zone = ZoneInfo("Asia/Singapore")
    first = datetime.combine(start, time.min, zone).astimezone(UTC)
    last = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(UTC)
    events = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.client_id == client_id,
            AuditLog.entity_type == "claim",
            AuditLog.action.in_(EVENTS),
            AuditLog.created_at >= first,
            AuditLog.created_at < last,
        )
        .order_by(AuditLog.created_at)
    ).all()
    claims = {
        c.id: c
        for c in db.scalars(
            select(Claim).where(
                Claim.client_id == client_id,
                Claim.id.in_({e.entity_id for e in events}),
            )
        )
    }
    users = {
        u.id: u.display_name or u.email
        for u in db.scalars(
            select(User).where(
                User.id.in_({e.user_id for e in events if e.user_id}),
            )
        )
    }
    groups: dict[tuple[str, str], list[AuditLog]] = defaultdict(list)
    for event in events:
        if event.actor_type == "member":
            continue
        human = bool(event.user_id) and event.actor_type in (None, "user")
        groups[("human" if human else "system", event.user_id or "system")].append(event)
    rows = []
    for (kind, actor), actions in groups.items():
        counts: dict[str, int] = defaultdict(int)
        hours = []
        for action in actions:
            counts[EVENTS[action.action]] += 1
            claim = claims.get(action.entity_id or "")
            if action.action in {"claim.approve", "claim.reject"} and claim and claim.submitted_at:
                submitted = (
                    claim.submitted_at.replace(tzinfo=UTC)
                    if claim.submitted_at.tzinfo is None
                    else claim.submitted_at
                )
                occurred = (
                    action.created_at.replace(tzinfo=UTC)
                    if action.created_at.tzinfo is None
                    else action.created_at
                )
                elapsed = (occurred - submitted).total_seconds() / 3600
                # A later resubmission must not turn an earlier decision into a negative duration.
                if elapsed >= 0:
                    hours.append(elapsed)
        distinct = len({a.entity_id for a in actions if a.entity_id})
        rows.append(
            {
                "actor_id": actor,
                "actor_type": kind,
                "name": users.get(actor, "Former or unknown servicer")
                if kind == "human"
                else "System / automation",
                "claims_handled": distinct,
                "actions": len(actions),
                "by_status": dict(counts),
                "repeat_actions": len(actions) - distinct,
                "average_decision_hours": round(sum(hours) / len(hours), 1) if hours else None,
                "decision_samples": len(hours),
            }
        )
    return {"from_date": start, "to_date": end, "items": sorted(rows, key=lambda r: r["name"])}
