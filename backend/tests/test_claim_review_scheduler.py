"""Concurrency, fairness, isolation and configuration checks for the review worker."""
from __future__ import annotations

import itertools
import logging
import os
import threading
from collections import Counter
from collections.abc import Callable, Collection, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from anthropic import APIConnectionError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import SessionLocal
from app.models import BrokerFirm, ClaimReviewJob
from app.models.claim_review_job import (
    JOB_STATE_FAILED,
    JOB_STATE_QUEUED,
    JOB_STATE_RETRY_WAIT,
    JOB_STATE_RUNNING,
)
from app.workers import claim_review as worker
from app.workers.review_scheduler import ReviewScheduler, WorkerLimits

FIRM_BACKLOG = "firm-backlog"
FIRM_SMALL = "firm-small"
_LIMIT_ENV = (
    "INSPRO_REVIEW_WORKER_CONCURRENCY",
    "INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT",
    "INSPRO_REVIEW_MAX_TOTAL_RUNNING",
    "INSPRO_REVIEW_MAX_PER_FIRM",
)


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
        self.caps: list[tuple[int, int | None]] = []

    def claim(
        self,
        _owner: str,
        excluded_client_ids: Collection[str],
        _max_per_client: int,
        max_total: int,
        max_per_firm: int | None,
    ) -> FakeLease | None:
        self.caps.append((max_total, max_per_firm))
        for index, lease in enumerate(self._items):
            if lease.client_id not in excluded_client_ids:
                return self._items.pop(index)
        return None


def _blocking_processor(release: threading.Event) -> Callable[[FakeLease, str], None]:
    def process(_lease: FakeLease, _owner: str) -> None:
        release.wait(timeout=5)

    return process


def _clear_limit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LIMIT_ENV:
        monkeypatch.delenv(name, raising=False)


def test_worker_limits_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_limit_env(monkeypatch)
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


def test_cluster_caps_default_to_the_single_replica_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset, the cluster-wide cap is one replica's concurrency (what it always
    effectively was) and firms are bounded only by it."""
    _clear_limit_env(monkeypatch)
    monkeypatch.setenv("INSPRO_REVIEW_WORKER_CONCURRENCY", "4")
    limits = WorkerLimits.from_env()
    assert limits.total_running_cap == 4
    assert limits.max_per_firm is None

    monkeypatch.setenv("INSPRO_REVIEW_MAX_TOTAL_RUNNING", "12")
    monkeypatch.setenv("INSPRO_REVIEW_MAX_PER_FIRM", "6")
    assert WorkerLimits.from_env() == WorkerLimits(4, 1, 12, 6)
    assert WorkerLimits.from_env().total_running_cap == 12

    monkeypatch.setenv("INSPRO_REVIEW_MAX_PER_FIRM", "13")
    with pytest.raises(RuntimeError, match="MAX_PER_FIRM cannot exceed"):
        WorkerLimits.from_env()
    monkeypatch.delenv("INSPRO_REVIEW_MAX_TOTAL_RUNNING")
    monkeypatch.setenv("INSPRO_REVIEW_MAX_PER_FIRM", "5")  # above the default total of 4
    with pytest.raises(RuntimeError, match="MAX_PER_FIRM cannot exceed"):
        WorkerLimits.from_env()
    monkeypatch.setenv("INSPRO_REVIEW_MAX_PER_FIRM", "0")
    with pytest.raises(RuntimeError, match="between 1 and"):
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


def test_scheduler_leases_against_the_cluster_caps_not_its_own_concurrency() -> None:
    queue = FakeQueue(["company-a", "company-b"])
    release = threading.Event()
    scheduler = ReviewScheduler(
        owner="worker-1",
        limits=WorkerLimits(2, 1, max_total_running=10, max_per_firm=3),
        claim_next=queue.claim,
        process_lease=_blocking_processor(release),
    )
    try:
        assert scheduler.fill() == 2
        assert set(queue.caps) == {(10, 3)}
    finally:
        release.set()
        scheduler.shutdown()


# ── Leasing against the database (SQLite here; Postgres below) ────────────────


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@pytest.fixture
def add_job() -> Iterator[Callable[..., str]]:
    """Queue claim-review jobs on a clean table; returns each job's id."""
    with SessionLocal() as db:
        db.query(ClaimReviewJob).delete()
        db.commit()
    numbers = itertools.count()

    def add(firm_id: str, client_id: str, *, age_minutes: float = 0) -> str:
        number = next(numbers)
        enqueued = datetime.now(UTC) - timedelta(minutes=age_minutes)
        with SessionLocal() as db:
            job = ClaimReviewJob(
                broker_firm_id=firm_id,
                client_id=client_id,
                claim_id=f"claim-{number}",
                review_id=f"review-{number}",
                claim_revision=1,
                idempotency_key=f"worker-test-{number}",
                state=JOB_STATE_QUEUED,
                available_at=enqueued,
                created_at=enqueued,
            )
            db.add(job)
            db.commit()
            return job.id

    yield add
    with SessionLocal() as db:
        db.query(ClaimReviewJob).delete()
        db.commit()


def test_one_firms_backlog_cannot_starve_another(add_job) -> None:
    for index in range(40):
        add_job(FIRM_BACKLOG, f"backlog-company-{index % 4}", age_minutes=60 - index)
    add_job(FIRM_SMALL, "small-company-1", age_minutes=1)
    add_job(FIRM_SMALL, "small-company-2", age_minutes=0)

    leases = [worker._claim_next(f"worker-{index}", (), 10, 4) for index in range(4)]
    firms = [lease.broker_firm_id for lease in leases if lease]

    # Oldest first while nothing runs; then the firm with the fewest running
    # reviews — so the small firm is served despite 40 older jobs elsewhere.
    assert firms == [FIRM_BACKLOG, FIRM_SMALL, FIRM_BACKLOG, FIRM_SMALL]
    assert worker._claim_next("worker-extra", (), 10, 4) is None  # cluster cap


def test_per_firm_cap_holds_back_a_busy_firm(add_job) -> None:
    for index in range(6):
        add_job(FIRM_BACKLOG, f"backlog-company-{index}", age_minutes=30 - index)
    add_job(FIRM_SMALL, "small-company", age_minutes=0)

    leases = [
        worker._claim_next(f"worker-{index}", (), 5, 10, max_per_firm=2) for index in range(4)
    ]
    leased = Counter(lease.broker_firm_id for lease in leases if lease)
    assert leased == {FIRM_BACKLOG: 2, FIRM_SMALL: 1}
    # The busy firm is at its cap and the other has nothing left, although the
    # cluster cap (10) has room: capacity stays free for a firm that needs it.
    assert leases[-1] is None


def test_company_cap_and_exclusions_still_apply(add_job) -> None:
    add_job(FIRM_BACKLOG, "company-a", age_minutes=10)
    add_job(FIRM_BACKLOG, "company-a", age_minutes=9)
    add_job(FIRM_BACKLOG, "company-b", age_minutes=1)

    excluded = worker._claim_next("worker-1", {"company-a"}, 5, 10)
    assert excluded is not None and excluded.client_id == "company-b"
    first = worker._claim_next("worker-2", (), 1, 10)
    assert first is not None and first.client_id == "company-a"
    assert worker._claim_next("worker-3", (), 1, 10) is None  # company-a at its cap


def test_every_lease_restarts_the_processing_deadline(add_job) -> None:
    """Queue time is not processing time: an hour-old job starts its review
    budget when it is leased, and again on every retry's lease."""
    job_id = add_job(FIRM_BACKLOG, "company-a", age_minutes=90)
    assert worker._claim_next("worker-1", (), 1, 10) is not None
    with SessionLocal() as db:
        job = db.get(ClaimReviewJob, job_id)
        assert job is not None and job.started_at is not None
        assert datetime.now(UTC) - _aware(job.started_at) < timedelta(minutes=1)
        job.state = JOB_STATE_RETRY_WAIT
        job.lease_owner = None
        job.started_at = datetime.now(UTC) - timedelta(hours=2)
        db.commit()

    assert worker._claim_next("worker-2", (), 1, 10) is not None
    with SessionLocal() as db:
        restarted = db.get(ClaimReviewJob, job_id)
        assert restarted is not None and restarted.started_at is not None
        assert datetime.now(UTC) - _aware(restarted.started_at) < timedelta(minutes=1)
        assert restarted.attempt == 2


def _provider_outage() -> APIConnectionError:
    return APIConnectionError(message="down", request=httpx.Request("POST", "http://t/"))


def test_transient_failure_retries_until_the_age_ceiling(add_job) -> None:
    # Waited 45 minutes in the queue — past the old 20-minute end-to-end
    # deadline, which used to refuse the retry outright.
    waited = add_job(FIRM_BACKLOG, "company-a", age_minutes=45)
    worker._claim_next("worker-1", (), 5, 10)
    worker._handle_failure(waited, "worker-1", _provider_outage())

    zombie = add_job(FIRM_BACKLOG, "company-b", age_minutes=25 * 60)
    worker._claim_next("worker-2", {"company-a"}, 5, 10)
    worker._handle_failure(zombie, "worker-2", _provider_outage())

    with SessionLocal() as db:
        assert db.get(ClaimReviewJob, waited).state == JOB_STATE_RETRY_WAIT
        assert db.get(ClaimReviewJob, zombie).state == JOB_STATE_FAILED


def test_reaper_retires_expired_leases_past_the_age_ceiling(add_job) -> None:
    recent = add_job(FIRM_BACKLOG, "company-a", age_minutes=30)
    zombie = add_job(FIRM_BACKLOG, "company-b", age_minutes=25 * 60)
    with SessionLocal() as db:
        for job in db.query(ClaimReviewJob).all():
            job.state = JOB_STATE_RUNNING
            job.attempt = 1
            job.lease_owner = "gone-worker"
            job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    assert worker.reap_expired_jobs() == 2
    with SessionLocal() as db:
        assert db.get(ClaimReviewJob, recent).state == JOB_STATE_RETRY_WAIT
        assert db.get(ClaimReviewJob, zombie).state == JOB_STATE_FAILED


# ── Per-firm fault isolation in the worker loops ──────────────────────────────


@pytest.fixture
def three_firms(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Make the loops visit three firm schemas (SQLite routing is a no-op)."""
    firm_ids = ["firm-iso-a", "firm-iso-b", "firm-iso-c"]
    with SessionLocal() as db:
        for firm_id in firm_ids:
            if db.get(BrokerFirm, firm_id) is None:
                db.add(BrokerFirm(id=firm_id, name=firm_id))
        db.commit()
    monkeypatch.setattr(worker, "is_postgres", lambda _bind: True)
    yield firm_ids
    with SessionLocal() as db:
        db.query(BrokerFirm).filter(BrokerFirm.id.in_(firm_ids)).delete()
        db.commit()


@pytest.fixture
def task_failures(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    recorded: list[tuple[str, str | None]] = []

    def record(task: str, *, broker_firm_id: str | None = None) -> None:
        recorded.append((task, broker_firm_id))

    monkeypatch.setattr(worker.review_metrics, "task_failure", record)
    return recorded


def test_a_failing_firm_neither_stops_nor_always_precedes_the_others(
    three_firms, task_failures, monkeypatch: pytest.MonkeyPatch
) -> None:
    visited: list[str | None] = []

    def deliver(firm_id: str | None) -> bool:
        visited.append(firm_id)
        if firm_id == "firm-iso-a":
            raise RuntimeError("firm schema is missing")
        return firm_id == "firm-iso-c"

    monkeypatch.setattr(worker, "process_one_claim_notification", deliver)
    monkeypatch.setattr(worker, "process_one_workflow_notification", lambda _firm: False)

    assert worker._deliver_notifications() is True
    first_cycle = list(visited)
    assert sorted(first_cycle) == sorted(three_firms)
    assert task_failures == [("claim_notifications", "firm-iso-a")]

    visited.clear()
    worker._deliver_notifications()
    assert sorted(visited) == sorted(three_firms)
    assert visited[0] != first_cycle[0]  # the starting firm rotates every cycle


def test_invariant_checks_and_cleanup_continue_past_a_failing_firm(
    three_firms, task_failures, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed: list[str | None] = []
    real_route = worker.set_search_path

    def route(db, firm_id):
        routed.append(firm_id)
        if firm_id == "firm-iso-b":
            raise RuntimeError("firm schema is missing")
        real_route(db, firm_id)

    monkeypatch.setattr(worker, "set_search_path", route)
    monkeypatch.setattr(worker, "retry_pending_document_deletes", lambda _db: 1)

    assert worker.check_invariants() == {"pending_without_job": 0, "active_missing_record": 0}
    assert worker.purge_pending_document_deletes() == 2  # the two healthy firms
    assert sorted(routed) == sorted(three_firms * 2)
    assert task_failures == [("invariants", "firm-iso-b"), ("document_cleanup", "firm-iso-b")]


def test_maintenance_and_lease_failures_never_stop_the_loop(
    task_failures, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> None:
        raise RuntimeError("database blip")

    class BrokenScheduler:
        def fill(self) -> int:
            raise RuntimeError("database blip")

    caplog.set_level(logging.ERROR, logger=worker.__name__)
    worker._run_task("reaper-test", broken)
    worker._run_task("reaper-test", broken)
    assert worker._fill(BrokenScheduler()) == 0  # type: ignore[arg-type]

    assert task_failures == [("reaper-test", None), ("reaper-test", None), ("lease", None)]
    # A persistent failure is counted every time but logged once per interval.
    logged = [r for r in caplog.records if getattr(r, "task", None) == "reaper-test"]
    assert len(logged) == 1


# ── PostgreSQL: concurrent replicas share one firm-fair capacity ──────────────

PG_URL = os.environ.get("INSPRO_PG_TEST_URL")


@pytest.mark.skipif(not PG_URL, reason="INSPRO_PG_TEST_URL not set — Postgres-only test")
def test_postgres_leasing_is_firm_fair_across_concurrent_replicas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PG_URL is not None
    engine = create_engine(PG_URL)
    ClaimReviewJob.__table__.create(engine, checkfirst=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    now = datetime.now(UTC)
    try:
        with session_factory() as session:
            session.query(ClaimReviewJob).delete()
            plan = [(FIRM_BACKLOG, f"backlog-{i % 5}", 120 - i) for i in range(30)]
            plan += [(FIRM_SMALL, f"small-{i}", 3 - i) for i in range(3)]
            for index, (firm_id, client_id, age) in enumerate(plan):
                enqueued = now - timedelta(minutes=age)
                session.add(
                    ClaimReviewJob(
                        broker_firm_id=firm_id,
                        client_id=client_id,
                        claim_id=f"pg-fair-claim-{index}",
                        review_id=f"pg-fair-review-{index}",
                        claim_revision=1,
                        idempotency_key=f"pg-fair-{index}",
                        state=JOB_STATE_QUEUED,
                        available_at=enqueued,
                        created_at=enqueued,
                    )
                )
            session.commit()

        def lease(index: int):
            return worker._claim_next(f"pg-replica-{index}", (), 10, 6)

        with ThreadPoolExecutor(max_workers=8) as executor:
            leases = [found for found in executor.map(lease, range(8)) if found]
        assert len(leases) == 6
        assert len({found.job_id for found in leases}) == 6
        assert Counter(found.broker_firm_id for found in leases) == {
            FIRM_BACKLOG: 3,
            FIRM_SMALL: 3,
        }
    finally:
        with session_factory() as session:
            session.query(ClaimReviewJob).delete()
            session.commit()
        engine.dispose()
