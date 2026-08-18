"""Optional OpenTelemetry instruments for the durable review queue."""
from __future__ import annotations

try:
    from opentelemetry import metrics

    _meter = metrics.get_meter("inspro.claim_review")
    _jobs = _meter.create_counter("claim_review.jobs")
    _duration = _meter.create_histogram("claim_review.duration.seconds")
    _stage_duration = _meter.create_histogram("claim_review.stage.duration.seconds")
    _stage_failures = _meter.create_counter("claim_review.stage.failures")
    _provider_calls = _meter.create_counter("claim_review.provider.calls")
    _provider_duration = _meter.create_histogram("claim_review.provider.duration.seconds")
    _cache = _meter.create_counter("claim_review.cache.requests")
    _queue_age = _meter.create_histogram("claim_review.queue_age.seconds")
    _queue_depth = _meter.create_histogram("claim_review.queue.depth")
    _active_jobs = _meter.create_histogram("claim_review.active")
    _invariants = _meter.create_counter("claim_review.invariant_failures")
    _leases = _meter.create_counter("claim_review.lease_expirations")
except Exception:  # pragma: no cover - telemetry is optional in local/test
    _jobs = _duration = _stage_duration = _stage_failures = None
    _provider_calls = _provider_duration = _cache = None
    _queue_age = _queue_depth = _active_jobs = _invariants = _leases = None


def job(state: str, *, error_code: str | None = None) -> None:
    if _jobs is not None:
        _jobs.add(1, {"state": state, "error_code": error_code or ""})


def duration(seconds: float, *, outcome: str) -> None:
    if _duration is not None:
        _duration.record(seconds, {"outcome": outcome})


def stage_duration(stage: str, seconds: float) -> None:
    if _stage_duration is not None:
        _stage_duration.record(seconds, {"stage": stage})


def stage_failure(stage: str, error_code: str) -> None:
    if _stage_failures is not None:
        _stage_failures.add(1, {"stage": stage, "error_code": error_code})


def provider_call(
    *, provider: str, model: str, operation: str, outcome: str, seconds: float
) -> None:
    attrs = {
        "provider": provider,
        "model": model,
        "operation": operation,
        "outcome": outcome,
    }
    if _provider_calls is not None:
        _provider_calls.add(1, attrs)
    if _provider_duration is not None:
        _provider_duration.record(seconds, attrs)


def cache_request(*, operation: str, hit: bool) -> None:
    if _cache is not None:
        _cache.add(1, {"operation": operation, "cache_hit": str(hit).lower()})


def queue_snapshot(depth: int, oldest_age_seconds: float) -> None:
    if _queue_depth is not None:
        _queue_depth.record(max(0, depth))
    if _queue_age is not None:
        _queue_age.record(max(0.0, oldest_age_seconds))


def active_jobs(count: int, capacity: int) -> None:
    if _active_jobs is not None:
        _active_jobs.record(max(0, count), {"capacity": capacity})


def invariant(name: str, count: int) -> None:
    if _invariants is not None and count:
        _invariants.add(count, {"invariant": name})


def lease_expired(count: int) -> None:
    if _leases is not None and count:
        _leases.add(count)
