"""Concurrency, fairness, and configuration checks for the review worker."""
from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass

import pytest

from app.workers.review_scheduler import ReviewScheduler, WorkerLimits


@dataclass(frozen=True)
class FakeLease:
    job_id: str
    client_id: str


class FakeQueue:
    def __init__(self, client_ids: list[str]) -> None:
        self._items = [
            FakeLease(str(index), client_id)
            for index, client_id in enumerate(client_ids)
        ]

    def claim(
        self,
        _owner: str,
        excluded_client_ids: Collection[str],
        _max_per_client: int,
        _max_total: int,
    ) -> FakeLease | None:
        for index, lease in enumerate(self._items):
            if lease.client_id not in excluded_client_ids:
                return self._items.pop(index)
        return None


def _blocking_processor(release: threading.Event) -> Callable[[FakeLease, str], None]:
    def process(_lease: FakeLease, _owner: str) -> None:
        release.wait(timeout=5)

    return process


def test_worker_limits_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSPRO_REVIEW_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT", raising=False)
    assert WorkerLimits.from_env() == WorkerLimits(1, 1)

    monkeypatch.setenv("INSPRO_REVIEW_WORKER_CONCURRENCY", "4")
    monkeypatch.setenv("INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT", "2")
    assert WorkerLimits.from_env() == WorkerLimits(4, 2)

    monkeypatch.setenv("INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT", "5")
    with pytest.raises(RuntimeError, match="cannot exceed"):
        WorkerLimits.from_env()

    monkeypatch.setenv("INSPRO_REVIEW_WORKER_CONCURRENCY", "invalid")
    with pytest.raises(RuntimeError, match="must be an integer"):
        WorkerLimits.from_env()


def test_scheduler_prefers_distinct_companies_then_uses_spare_capacity() -> None:
    queue = FakeQueue(["company-a", "company-a", "company-a", "company-b", "company-c"])
    release = threading.Event()
    scheduler = ReviewScheduler(
        owner="worker-1",
        limits=WorkerLimits(4, 2),
        claim_next=queue.claim,
        process_lease=_blocking_processor(release),
    )
    try:
        assert scheduler.fill() == 4
        assert scheduler.active_client_counts == {
            "company-a": 2,
            "company-b": 1,
            "company-c": 1,
        }
    finally:
        release.set()
        scheduler.shutdown()


def test_scheduler_enforces_per_company_cap() -> None:
    queue = FakeQueue(["company-a", "company-a", "company-a"])
    release = threading.Event()
    scheduler = ReviewScheduler(
        owner="worker-1",
        limits=WorkerLimits(4, 2),
        claim_next=queue.claim,
        process_lease=_blocking_processor(release),
    )
    try:
        assert scheduler.fill() == 2
        assert scheduler.active_count == 2
        assert scheduler.active_client_counts == {"company-a": 2}
    finally:
        release.set()
        scheduler.shutdown()
