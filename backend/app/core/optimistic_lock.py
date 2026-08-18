"""Shared optimistic-lock checks for timestamped configuration resources."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from fastapi import HTTPException, status


class TimestampedRow(Protocol):
    id: str
    updated_at: datetime


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def assert_not_stale(
    *,
    expected: datetime | None,
    actual: datetime,
    label: str,
    allow_unmaterialized: bool = False,
) -> None:
    if expected is None and allow_unmaterialized:
        return
    if expected is not None and _utc(expected) == _utc(actual):
        return
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "stale_configuration",
            "message": f"{label} changed after you opened it. Reload and review the latest values.",
            "current_updated_at": _utc(actual).isoformat(),
        },
    )


def assert_collection_not_stale(
    *,
    rows: list[TimestampedRow],
    expected_versions: dict[str, datetime],
    label: str,
) -> None:
    actual_ids = {row.id for row in rows}
    if actual_ids != set(expected_versions):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "stale_configuration",
                "message": f"{label} changed after you opened it. Reload before continuing.",
            },
        )
    for row in rows:
        assert_not_stale(
            expected=expected_versions[row.id],
            actual=row.updated_at,
            label=label,
        )


__all__ = ["assert_collection_not_stale", "assert_not_stale"]
