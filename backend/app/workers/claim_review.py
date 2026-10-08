"""Durable PostgreSQL-backed claim-review worker."""
from __future__ import annotations

import itertools
import logging
import os
import random
import signal
import socket
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, set_search_path
from app.models import BrokerFirm, Claim, ClaimAIReview, ClaimReviewJob
from app.models.claim import CLAIM_STATUS_AI_REVIEW_PENDING, CLAIM_STATUS_SUBMITTED
from app.models.claim_ai_review import (
    REVIEW_STATUS_ERROR,
    REVIEW_STATUS_RETRY_WAIT,
)
from app.models.claim_review_job import (
    ACTIVE_JOB_STATES,
    JOB_STATE_CANCELLED,
    JOB_STATE_FAILED,
    JOB_STATE_QUEUED,
    JOB_STATE_RETRY_WAIT,
    JOB_STATE_RUNNING,
    JOB_STATE_SUCCEEDED,
)
from app.services.ai_breaker import CircuitOpenError
from app.services.ai_extractor import AINotConfiguredError, AIParseError
from app.services.ai_gateway import AICapacityError
from app.services.claim_notifications import process_one_claim_notification
from app.services.claims import retry_pending_document_deletes
from app.services.claims_review import metrics as review_metrics
from app.services.claims_review.pipeline import (
    ReviewDeadlineExceeded,
    ReviewOwnershipLost,
    execute_leased_review,
    review_age_exceeded,
    review_deadline_seconds,
    review_max_age_seconds,
)
from app.services.workflow_delivery import process_one_workflow_notification
from app.workers.review_scheduler import ReviewScheduler, WorkerLimits

logger = logging.getLogger(__name__)

LEASE_SECONDS = int(os.environ.get("INSPRO_REVIEW_LEASE_SECONDS", "90"))
HEARTBEAT_SECONDS = max(5, LEASE_SECONDS // 3)
POLL_SECONDS = float(os.environ.get("INSPRO_REVIEW_POLL_SECONDS", "2"))
REAPER_SECONDS = int(os.environ.get("INSPRO_REVIEW_REAPER_SECONDS", "30"))
LOOP_STALE_SECONDS = max(
    30.0,
    POLL_SECONDS * 5,
    REAPER_SECONDS * 2,
    HEARTBEAT_SECONDS * 2.5,
)
QUEUE_ALERT_SECONDS = int(os.environ.get("INSPRO_REVIEW_QUEUE_ALERT_SECONDS", "120"))
_loop_heartbeat = time.monotonic()
_notification_heartbeat = time.monotonic()
_worker_stopping = False
_last_queue_warning = 0.0
_CLAIM_CAPACITY_LOCK_ID = 724862559301834887
# Per-firm loops start one firm further along each cycle (per task), so a firm
# that keeps failing or runs slowly never always goes first.
_firm_rotation: defaultdict[str, itertools.count[int]] = defaultdict(itertools.count)
# A failing task/firm is logged at most once per interval; the metric counts all.
_TASK_FAILURE_LOG_SECONDS = 60.0
_last_task_failure_log: dict[tuple[str, str], float] = {}
_task_failure_lock = threading.Lock()


@dataclass(frozen=True)
class JobLease:
    job_id: str
    review_id: str
    claim_id: str
    broker_firm_id: str
    client_id: str
    attempt: int
    stage: str

    def log_context(self, owner: str) -> dict[str, str | int]:
        return {
            "job_id": self.job_id,
            "review_id": self.review_id,
            "claim_id": self.claim_id,
            "broker_firm_id": self.broker_firm_id,
            "client_id": self.client_id,
            "attempt": self.attempt,
            "stage": self.stage,
            "lease_owner": owner,
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return "AI provider credentials were rejected."
    if isinstance(exc, (BadRequestError, NotFoundError)):
        return "AI provider configuration or request was rejected."
    if isinstance(exc, RateLimitError):
        return "AI provider rate limit reached; the review will retry."
    if isinstance(exc, (APIConnectionError, APITimeoutError, ConnectionError, TimeoutError)):
        return "AI provider is temporarily unavailable; the review will retry."
    if isinstance(exc, AINotConfiguredError):
        return "AI review is not configured."
    if isinstance(exc, AIParseError):
        return "AI provider returned an invalid structured response."
    if isinstance(exc, AICapacityError):
        return "AI review capacity is temporarily unavailable; the review will retry."
    if isinstance(exc, CircuitOpenError):
        return "AI provider circuit is open; the review will retry."
    if isinstance(exc, ReviewDeadlineExceeded):
        return "Claim review exceeded its processing deadline."
    if isinstance(exc, ReviewOwnershipLost):
        return "Claim review no longer owns the current claim revision."
    if isinstance(exc, OperationalError):
        return "Database connectivity interrupted the review; it will retry."
    return "Claim review failed. Route the claim to manual review."


def _record_queue_health(db: Session, now: datetime) -> None:
    global _last_queue_warning
    available = (
        ClaimReviewJob.state.in_((JOB_STATE_QUEUED, JOB_STATE_RETRY_WAIT)),
        ClaimReviewJob.available_at <= now,
    )
    depth = db.scalar(select(func.count(ClaimReviewJob.id)).where(*available)) or 0
    oldest = db.scalar(select(func.min(ClaimReviewJob.created_at)).where(*available))
    age = (_aware(now) - _aware(oldest)).total_seconds() if oldest else 0.0
    review_metrics.queue_snapshot(int(depth), age)
    if age >= QUEUE_ALERT_SECONDS and time.monotonic() - _last_queue_warning >= 60:
        logger.error(
            "Claim-review queue age exceeded threshold",
            extra={"error_code": "queue_age_exceeded", "queue_depth": depth, "age_seconds": age},
        )
        _last_queue_warning = time.monotonic()


def _running_counts(db: Session) -> tuple[Counter[str], Counter[str]]:
    """Running reviews across the whole cluster, per firm and per company."""
    by_firm: Counter[str] = Counter()
    by_client: Counter[str] = Counter()
    rows = db.execute(
        select(
            ClaimReviewJob.broker_firm_id,
            ClaimReviewJob.client_id,
            func.count(ClaimReviewJob.id),
        )
        .where(ClaimReviewJob.state == JOB_STATE_RUNNING)
        .group_by(ClaimReviewJob.broker_firm_id, ClaimReviewJob.client_id)
    ).all()
    for firm_id, client_id, count in rows:
        by_firm[firm_id] += count
        by_client[client_id] += count
    return by_firm, by_client


def _claim_next(
    owner: str,
    excluded_client_ids: Collection[str] = (),
    max_per_client: int = 1,
    max_total: int = 1,
    max_per_firm: int | None = None,
) -> JobLease | None:
    """Lease the next available job, fairly across firms and then companies.

    ``max_total`` and ``max_per_firm`` are CLUSTER-wide (every replica's running
    jobs count), ``max_per_client`` caps one company, and the scheduler's
    ``excluded_client_ids`` spread one replica's slots. Among eligible jobs the
    firm with the fewest running reviews goes first, so one firm's backlog can
    never starve another; the oldest available job breaks ties. Every leaser
    reads the counts under the same advisory lock, so they cannot rise before
    this commit; SKIP LOCKED steps over rows another transaction holds.
    """
    now = _now()
    with SessionLocal() as db:
        _record_queue_health(db, now)
        postgres = is_postgres(db)
        if postgres:
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _CLAIM_CAPACITY_LOCK_ID},
            )
        running_by_firm, running_by_client = _running_counts(db)
        if sum(running_by_firm.values()) >= max_total:
            return None
        blocked_clients = set(excluded_client_ids) | {
            client_id
            for client_id, count in running_by_client.items()
            if count >= max_per_client
        }
        blocked_firms = {
            firm_id
            for firm_id, count in running_by_firm.items()
            if max_per_firm is not None and count >= max_per_firm
        }
        stmt = select(ClaimReviewJob).where(
            ClaimReviewJob.state.in_((JOB_STATE_QUEUED, JOB_STATE_RETRY_WAIT)),
            ClaimReviewJob.available_at <= now,
        )
        if blocked_clients:
            stmt = stmt.where(ClaimReviewJob.client_id.notin_(blocked_clients))
        if blocked_firms:
            stmt = stmt.where(ClaimReviewJob.broker_firm_id.notin_(blocked_firms))
        if running_by_firm:
            # A firm with nothing running sorts as 0, ahead of every busy firm.
            firm_load = case(dict(running_by_firm), value=ClaimReviewJob.broker_firm_id, else_=0)
            stmt = stmt.order_by(firm_load)
        stmt = stmt.order_by(ClaimReviewJob.available_at, ClaimReviewJob.created_at).limit(1)
        if postgres:
            stmt = stmt.with_for_update(skip_locked=True)
        job = db.execute(stmt).scalar_one_or_none()
        if job is None:
            return None
        job.state = JOB_STATE_RUNNING
        job.attempt += 1
        job.lease_owner = owner
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        # Every lease starts a fresh processing deadline: time spent queued or
        # in retry backoff is not processing time (pipeline.review_deadline_reason).
        job.started_at = now
        db.commit()
        review_metrics.job(JOB_STATE_RUNNING)
        return JobLease(
            job_id=job.id,
            review_id=job.review_id,
            claim_id=job.claim_id,
            broker_firm_id=job.broker_firm_id,
            client_id=job.client_id,
            attempt=job.attempt,
            stage=job.stage,
        )


def _heartbeat(job_id: str, owner: str, stop: threading.Event) -> None:
    global _loop_heartbeat
    while not stop.wait(HEARTBEAT_SECONDS):
        # Readiness represents the worker process, not just its queue-polling
        # thread. A healthy long-running AI call must not make /readyz go stale.
        _loop_heartbeat = time.monotonic()
        now = _now()
        try:
            with SessionLocal() as db:
                job = db.get(ClaimReviewJob, job_id)
                if job is None or job.state != JOB_STATE_RUNNING or job.lease_owner != owner:
                    return
                job.heartbeat_at = now
                job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                db.commit()
            with SessionLocal() as tenant_db:
                set_search_path(tenant_db, job.broker_firm_id)
                review = tenant_db.get(ClaimAIReview, job.review_id)
                if review is not None:
                    review.heartbeat_at = now
                    tenant_db.commit()
        except Exception as exc:
            logger.error(
                "Claim-review heartbeat failed",
                extra={"job_id": job_id, "error_code": type(exc).__name__},
            )


def _finish_success(job_id: str, owner: str) -> None:
    with SessionLocal() as db:
        job = db.get(ClaimReviewJob, job_id)
        if job is None or job.state != JOB_STATE_RUNNING or job.lease_owner != owner:
            raise ReviewOwnershipLost("Lease changed before job finalization")
        job.state = JOB_STATE_SUCCEEDED
        job.stage = "persist"
        job.finished_at = _now()
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()
    review_metrics.job(JOB_STATE_SUCCEEDED)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError, BadRequestError)):
        return False
    if isinstance(
        exc,
        (AINotConfiguredError, ReviewDeadlineExceeded, ReviewOwnershipLost),
    ):
        return False
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(
        exc,
        (
            RateLimitError,
            APIConnectionError,
            APITimeoutError,
            AICapacityError,
            AIParseError,
            CircuitOpenError,
            OperationalError,
            TimeoutError,
            ConnectionError,
        ),
    )


def _mark_review_for_retry(job: ClaimReviewJob, code: str, detail: str) -> None:
    with SessionLocal() as db:
        set_search_path(db, job.broker_firm_id)
        review = db.get(ClaimAIReview, job.review_id)
        if review is not None and not review.superseded:
            review.status = REVIEW_STATUS_RETRY_WAIT
            review.error_code = code
            review.error_detail = detail
            db.commit()


def _terminal_failure(job: ClaimReviewJob, code: str, detail: str) -> None:
    now = _now()
    with SessionLocal() as db:
        set_search_path(db, job.broker_firm_id)
        claim = db.get(Claim, job.claim_id, with_for_update=True)
        review = db.get(ClaimAIReview, job.review_id)
        if review is not None and not review.superseded:
            review.status = REVIEW_STATUS_ERROR
            review.error_code = code
            review.error_detail = detail
            review.completed_at = now
        if (
            claim is not None
            and claim.status == CLAIM_STATUS_AI_REVIEW_PENDING
            and claim.revision == job.claim_revision
        ):
            claim.status = CLAIM_STATUS_SUBMITTED
        db.commit()


def _handle_failure(job_id: str, owner: str, exc: BaseException) -> None:
    now = _now()
    code = "review_ownership_lost" if isinstance(exc, ReviewOwnershipLost) else type(exc).__name__
    detail = _safe_error(exc)
    with SessionLocal() as db:
        job = db.get(ClaimReviewJob, job_id)
        if job is None or job.lease_owner != owner:
            return
        # Each retry gets its own processing deadline; only the age ceiling
        # (and the attempt budget) stops retrying.
        retry = (
            _retryable(exc)
            and job.attempt < job.max_attempts
            and not review_age_exceeded(job, now)
        )
        if isinstance(exc, ReviewOwnershipLost):
            job.state = JOB_STATE_CANCELLED
            job.finished_at = now
        elif retry:
            job.state = JOB_STATE_RETRY_WAIT
            delay = min(120, 30 * (2 ** max(0, job.attempt - 1))) + random.uniform(0, 5)
            job.available_at = now + timedelta(seconds=delay)
        else:
            job.state = JOB_STATE_FAILED
            job.finished_at = now
        job.last_error_code = code[:64]
        job.last_error_detail = detail
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()
        failed_stage = job.stage
        detached = job
    review_metrics.stage_failure(failed_stage, code)
    logger.log(
        logging.WARNING if retry else logging.ERROR,
        "Claim-review job retry scheduled" if retry else "Claim-review job terminated",
        extra={
            "job_id": job.id,
            "review_id": job.review_id,
            "claim_id": job.claim_id,
            "broker_firm_id": job.broker_firm_id,
            "client_id": job.client_id,
            "attempt": job.attempt,
            "stage": failed_stage,
            "error_code": code,
        },
    )
    if retry:
        review_metrics.job(JOB_STATE_RETRY_WAIT, error_code=code)
        _mark_review_for_retry(detached, code, detail)
    elif not isinstance(exc, ReviewOwnershipLost):
        review_metrics.job(JOB_STATE_FAILED, error_code=code)
        _terminal_failure(detached, code, detail)
    else:
        review_metrics.job(JOB_STATE_CANCELLED, error_code=code)


def reap_expired_jobs() -> int:
    now = _now()
    with SessionLocal() as db:
        stmt = select(ClaimReviewJob).where(
                ClaimReviewJob.state == JOB_STATE_RUNNING,
                ClaimReviewJob.lease_expires_at < now,
            )
        if is_postgres(db):
            stmt = stmt.with_for_update(skip_locked=True)
        jobs = db.execute(stmt).scalars().all()
        for job in jobs:
            if job.attempt < job.max_attempts and not review_age_exceeded(job, now):
                job.state = JOB_STATE_RETRY_WAIT
                job.available_at = now
            else:
                job.state = JOB_STATE_FAILED
                job.finished_at = now
            job.last_error_code = "lease_expired"
            job.last_error_detail = "Worker lease expired before the review completed."
            job.lease_owner = None
            job.lease_expires_at = None
        db.commit()
    for job in jobs:
        if job.state == JOB_STATE_RETRY_WAIT:
            _mark_review_for_retry(job, "lease_expired", job.last_error_detail or "")
        else:
            _terminal_failure(job, "lease_expired", job.last_error_detail or "")
    review_metrics.lease_expired(len(jobs))
    if jobs:
        logger.error(
            "Claim-review leases expired",
            extra={"error_code": "lease_expired", "count": len(jobs)},
        )
    return len(jobs)


def _record_task_failure(task: str, firm_id: str | None = None) -> None:
    """Count a failed worker-loop task and log it, at most once per interval
    for each task and firm. Call from inside the ``except`` block."""
    review_metrics.task_failure(task, broker_firm_id=firm_id)
    key = (task, firm_id or "")
    now = time.monotonic()
    with _task_failure_lock:
        last = _last_task_failure_log.get(key)
        if last is not None and now - last < _TASK_FAILURE_LOG_SECONDS:
            return
        _last_task_failure_log[key] = now
    error = sys.exc_info()[0]
    logger.exception(
        "Claim-review worker task failed; continuing",
        extra={
            "task": task,
            "broker_firm_id": firm_id or "",
            "error_code": error.__name__ if error else "unknown",
        },
    )


def _firm_targets(rotation: int) -> list[str | None]:
    """Every firm schema to visit, starting ``rotation`` firms along the list.

    SQLite (dev/test) has the single schema, addressed as ``None``.
    """
    if not is_postgres(engine):
        return [None]
    with SessionLocal() as control_db:
        firm_ids = list(
            control_db.execute(select(BrokerFirm.id).order_by(BrokerFirm.id)).scalars()
        )
    if not firm_ids:
        return []
    start = rotation % len(firm_ids)
    targets: list[str | None] = [*firm_ids[start:], *firm_ids[:start]]
    return targets


def _for_each_firm[ResultT](
    task: str, work: Callable[[str | None], ResultT]
) -> list[ResultT]:
    """Run ``work`` once per firm schema with each firm's failure isolated.

    A firm whose work raises is recorded and skipped; the firms after it still
    run, and the starting firm rotates every cycle, so one broken firm (a
    missing schema, a bad row) can neither stop nor permanently delay others.
    """
    results: list[ResultT] = []
    for firm_id in _firm_targets(next(_firm_rotation[task])):
        try:
            results.append(work(firm_id))
        except Exception:
            _record_task_failure(task, firm_id)
    return results


def _firm_invariants(firm_id: str | None, active: list[ClaimReviewJob]) -> Counter[str]:
    active_claim_ids = {job.claim_id for job in active}
    counts: Counter[str] = Counter()
    with SessionLocal() as db:
        set_search_path(db, firm_id)
        pending_ids = db.execute(
            select(Claim.id).where(Claim.status == CLAIM_STATUS_AI_REVIEW_PENDING)
        ).scalars().all()
        counts["pending_without_job"] = sum(
            claim_id not in active_claim_ids for claim_id in pending_ids
        )
        for job in active:
            if firm_id is not None and job.broker_firm_id != firm_id:
                continue
            claim = db.get(Claim, job.claim_id)
            review = db.get(ClaimAIReview, job.review_id)
            if claim is None or review is None or review.superseded:
                counts["active_missing_record"] += 1
    return counts


def check_invariants() -> dict[str, int]:
    """Continuously expose queue/tenant state mismatches without leaking PHI."""
    counts = {"pending_without_job": 0, "active_missing_record": 0}
    with SessionLocal() as control_db:
        active = list(
            control_db.execute(
                select(ClaimReviewJob).where(ClaimReviewJob.state.in_(ACTIVE_JOB_STATES))
            ).scalars()
        )
    for firm_counts in _for_each_firm(
        "invariants", lambda firm_id: _firm_invariants(firm_id, active)
    ):
        for name, count in firm_counts.items():
            counts[name] += count
    for name, count in counts.items():
        review_metrics.invariant(name, count)
        if count:
            logger.error(
                "Claim-review invariant violation",
                extra={"error_code": name, "count": count},
            )
    return counts


def _purge_firm_documents(firm_id: str | None) -> int:
    with SessionLocal() as db:
        set_search_path(db, firm_id)
        deleted = retry_pending_document_deletes(db)
        db.commit()
    return deleted


def purge_pending_document_deletes() -> int:
    """Retry deferred evidence deletion in every tenant schema."""
    deleted = sum(_for_each_firm("document_cleanup", _purge_firm_documents))
    if deleted:
        logger.info("Pending document blobs deleted", extra={"count": deleted})
    return deleted


def _deliver_notifications() -> bool:
    """One delivery pass over every firm; a failing firm is skipped, not fatal."""
    claims = _for_each_firm("claim_notifications", process_one_claim_notification)
    workflow = _for_each_firm("workflow_notifications", process_one_workflow_notification)
    return any(claims) or any(workflow)


def _notification_loop(stopping: threading.Event) -> None:
    """Deliver member emails independently of long-running AI provider calls."""
    global _notification_heartbeat
    while not stopping.is_set():
        _notification_heartbeat = time.monotonic()
        try:
            delivered = _deliver_notifications()
        except Exception:
            # Only listing the firms can land here (control database down).
            _record_task_failure("notifications")
            stopping.wait(5)
            continue
        stopping.wait(0.5 if delivered else 2)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/healthz", "/readyz"}:
            self.send_response(404)
            self.end_headers()
            return
        ok = True
        if self.path == "/readyz":
            now = time.monotonic()
            ok = not _worker_stopping and all(
                now - heartbeat <= LOOP_STALE_SECONDS
                for heartbeat in (_loop_heartbeat, _notification_heartbeat)
            )
            try:
                with SessionLocal() as db:
                    db.execute(text("SELECT 1"))
            except Exception:
                ok = False
        self.send_response(200 if ok else 503)
        self.send_header("X-Inspro-Version", os.environ.get("INSPRO_GIT_SHA", "unknown"))
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"unavailable")

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _start_health_server() -> ThreadingHTTPServer:
    port = int(os.environ.get("PORT", os.environ.get("INSPRO_WORKER_HEALTH_PORT", "8081")))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health-server").start()
    return server


def _process_lease(lease: JobLease, owner: str) -> None:
    job_id = lease.job_id
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(job_id, owner, heartbeat_stop),
        daemon=True,
        name=f"heartbeat-{job_id}",
    )
    heartbeat.start()
    started = time.monotonic()
    try:
        logger.info("Claim-review job started", extra=lease.log_context(owner))
        execute_leased_review(job_id, owner)
        _finish_success(job_id, owner)
        logger.info(
            "Claim-review job succeeded",
            extra={
                **lease.log_context(owner),
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        review_metrics.duration(time.monotonic() - started, outcome="succeeded")
    except Exception as exc:
        logger.error(
            "Claim-review job failed",
            extra={**lease.log_context(owner), "error_code": type(exc).__name__},
        )
        _handle_failure(job_id, owner, exc)
        review_metrics.duration(time.monotonic() - started, outcome="failed")
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=2)


def process_one_job(owner: str) -> bool:
    """Lease and process one available job; useful for controlled drains/tests."""
    lease = _claim_next(owner)
    if lease is None:
        return False
    _process_lease(lease, owner)
    return True


def _run_task(task: str, run: Callable[[], object]) -> None:
    """One maintenance pass; a failure is recorded and never stops leasing."""
    try:
        run()
    except Exception:
        _record_task_failure(task)


def _fill(scheduler: ReviewScheduler[JobLease]) -> int:
    """Lease into free slots; a failed lease attempt never stops the loop."""
    try:
        return scheduler.fill()
    except Exception:
        # A transient database fault must not take down in-flight reviews;
        # the next poll tries again (and /readyz reports the database).
        _record_task_failure("lease")
        return 0


def main() -> None:
    global _loop_heartbeat, _worker_stopping
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    from app.core.telemetry import configure_telemetry

    configure_telemetry()
    from app.services.claims_review.recovery import (
        recover_unreviewed_amendments,
        retry_failed_parse_reviews,
    )

    # Read every review setting before doing any work, so a bad value stops the
    # worker at start-up rather than failing each review at its first checkpoint.
    limits = WorkerLimits.from_env()
    deadline_seconds = review_deadline_seconds()
    max_age_seconds = review_max_age_seconds()
    recovered_parse_reviews = retry_failed_parse_reviews()
    recovered_amendments = recover_unreviewed_amendments()
    owner = f"{socket.gethostname()}:{os.getpid()}"
    scheduler = ReviewScheduler(
        owner=owner,
        limits=limits,
        claim_next=_claim_next,
        process_lease=_process_lease,
    )
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: stopping.set())
    health = _start_health_server()
    notification_thread = threading.Thread(
        target=_notification_loop,
        args=(stopping,),
        daemon=True,
        name="claim-notifications",
    )
    notification_thread.start()
    last_reap = 0.0
    last_invariant_check = 0.0
    last_document_cleanup = 0.0
    logger.info(
        "Claim-review worker started",
        extra={
            "lease_owner": owner,
            "concurrency": limits.concurrency,
            "max_concurrent_per_client": limits.max_concurrent_per_client,
            "max_total_running": limits.total_running_cap,
            "max_per_firm": limits.max_per_firm or 0,
            "deadline_seconds": deadline_seconds,
            "max_age_seconds": max_age_seconds,
            "recovered_parse_reviews": recovered_parse_reviews,
            "recovered_amendments": recovered_amendments,
        },
    )
    try:
        while not stopping.is_set():
            _loop_heartbeat = time.monotonic()
            if time.monotonic() - last_reap >= REAPER_SECONDS:
                _run_task("reaper", reap_expired_jobs)
                last_reap = time.monotonic()
            if time.monotonic() - last_invariant_check >= 60:
                _run_task("invariants", check_invariants)
                last_invariant_check = time.monotonic()
            if time.monotonic() - last_document_cleanup >= 60:
                _run_task("document_cleanup", purge_pending_document_deletes)
                last_document_cleanup = time.monotonic()
            started = _fill(scheduler)
            review_metrics.active_jobs(scheduler.active_count, scheduler.capacity)
            if started == 0:
                wait_seconds = min(POLL_SECONDS, 0.5) if scheduler.active_count else POLL_SECONDS
                stopping.wait(wait_seconds)
    finally:
        _worker_stopping = True
        stopping.set()
        scheduler.shutdown()
        review_metrics.active_jobs(0, scheduler.capacity)
        notification_thread.join(timeout=5)
        health.shutdown()
        logger.info("Claim-review worker stopped", extra={"lease_owner": owner})


if __name__ == "__main__":
    main()
