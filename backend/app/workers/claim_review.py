"""Durable PostgreSQL-backed claim-review worker."""
from __future__ import annotations

import logging
import os
import random
import signal
import socket
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

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
)

logger = logging.getLogger(__name__)

LEASE_SECONDS = int(os.environ.get("INSPRO_REVIEW_LEASE_SECONDS", "90"))
HEARTBEAT_SECONDS = max(5, LEASE_SECONDS // 3)
POLL_SECONDS = float(os.environ.get("INSPRO_REVIEW_POLL_SECONDS", "2"))
REAPER_SECONDS = int(os.environ.get("INSPRO_REVIEW_REAPER_SECONDS", "30"))
DEADLINE_SECONDS = int(os.environ.get("INSPRO_REVIEW_DEADLINE_SECONDS", "1200"))
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
    if isinstance(exc, BadRequestError):
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


def _record_queue_health(db, now: datetime) -> None:
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


def _claim_next(owner: str) -> JobLease | None:
    now = _now()
    with SessionLocal() as db:
        _record_queue_health(db, now)
        stmt = (
            select(ClaimReviewJob)
            .where(
                ClaimReviewJob.state.in_((JOB_STATE_QUEUED, JOB_STATE_RETRY_WAIT)),
                ClaimReviewJob.available_at <= now,
            )
            .order_by(ClaimReviewJob.available_at, ClaimReviewJob.created_at)
            .limit(1)
        )
        if is_postgres(db):
            stmt = stmt.with_for_update(skip_locked=True)
        job = db.execute(stmt).scalar_one_or_none()
        if job is None:
            return None
        job.state = JOB_STATE_RUNNING
        job.attempt += 1
        job.lease_owner = owner
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        job.started_at = job.started_at or now
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
        (AINotConfiguredError, AIParseError, ReviewDeadlineExceeded, ReviewOwnershipLost),
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
        age_seconds = (_aware(now) - _aware(job.created_at)).total_seconds()
        expired_deadline = age_seconds >= DEADLINE_SECONDS
        retry = _retryable(exc) and job.attempt < job.max_attempts and not expired_deadline
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
            if job.attempt < job.max_attempts:
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


def check_invariants() -> dict[str, int]:
    """Continuously expose queue/tenant state mismatches without leaking PHI."""
    counts = {"pending_without_job": 0, "active_missing_record": 0}
    with SessionLocal() as control_db:
        firm_ids = control_db.execute(select(BrokerFirm.id)).scalars().all()
        active = control_db.execute(
            select(ClaimReviewJob).where(ClaimReviewJob.state.in_(ACTIVE_JOB_STATES))
        ).scalars().all()
        active_by_claim = {job.claim_id: job for job in active}
    target_firms = firm_ids if is_postgres(engine) else [None]
    for firm_id in target_firms:
        with SessionLocal() as db:
            set_search_path(db, firm_id)
            pending_ids = db.execute(
                select(Claim.id).where(Claim.status == CLAIM_STATUS_AI_REVIEW_PENDING)
            ).scalars().all()
            counts["pending_without_job"] += sum(
                claim_id not in active_by_claim for claim_id in pending_ids
            )
            for job in active:
                if firm_id is not None and job.broker_firm_id != firm_id:
                    continue
                claim = db.get(Claim, job.claim_id)
                review = db.get(ClaimAIReview, job.review_id)
                if claim is None or review is None or review.superseded:
                    counts["active_missing_record"] += 1
    for name, count in counts.items():
        review_metrics.invariant(name, count)
        if count:
            logger.error(
                "Claim-review invariant violation",
                extra={"error_code": name, "count": count},
            )
    return counts


def purge_pending_document_deletes() -> int:
    """Retry deferred evidence deletion in every tenant schema."""
    with SessionLocal() as control_db:
        firm_ids = control_db.execute(select(BrokerFirm.id)).scalars().all()
    target_firms = firm_ids if is_postgres(engine) else [None]
    deleted = 0
    for firm_id in target_firms:
        with SessionLocal() as db:
            set_search_path(db, firm_id)
            deleted += retry_pending_document_deletes(db)
            db.commit()
    if deleted:
        logger.info("Pending document blobs deleted", extra={"count": deleted})
    return deleted


def _notification_loop(stopping: threading.Event) -> None:
    """Deliver member emails independently of long-running AI provider calls."""
    global _notification_heartbeat
    while not stopping.is_set():
        _notification_heartbeat = time.monotonic()
        delivered = False
        try:
            with SessionLocal() as control_db:
                firm_ids = control_db.execute(select(BrokerFirm.id)).scalars().all()
            target_firms = firm_ids if is_postgres(engine) else [None]
            for firm_id in target_firms:
                delivered = process_one_claim_notification(firm_id) or delivered
        except Exception:
            logger.exception("Claim notification loop failed")
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
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"unavailable")

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _start_health_server() -> ThreadingHTTPServer:
    port = int(os.environ.get("PORT", os.environ.get("INSPRO_WORKER_HEALTH_PORT", "8081")))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health-server").start()
    return server


def process_one_job(owner: str) -> bool:
    """Lease and process one available job; useful for controlled drains/tests."""
    lease = _claim_next(owner)
    if lease is None:
        return False
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
    return True


def main() -> None:
    global _loop_heartbeat, _worker_stopping
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    from app.core.telemetry import configure_telemetry

    configure_telemetry()
    owner = f"{socket.gethostname()}:{os.getpid()}"
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
    logger.info("Claim-review worker started", extra={"lease_owner": owner})
    try:
        while not stopping.is_set():
            _loop_heartbeat = time.monotonic()
            if time.monotonic() - last_reap >= REAPER_SECONDS:
                reap_expired_jobs()
                last_reap = time.monotonic()
            if time.monotonic() - last_invariant_check >= 60:
                check_invariants()
                last_invariant_check = time.monotonic()
            if time.monotonic() - last_document_cleanup >= 60:
                purge_pending_document_deletes()
                last_document_cleanup = time.monotonic()
            if not process_one_job(owner):
                stopping.wait(POLL_SECONDS)
    finally:
        _worker_stopping = True
        stopping.set()
        notification_thread.join(timeout=5)
        health.shutdown()
        logger.info("Claim-review worker stopped", extra={"lease_owner": owner})


if __name__ == "__main__":
    main()
