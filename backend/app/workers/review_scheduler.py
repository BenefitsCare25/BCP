"""Bounded, company-fair scheduling for durable claim-review jobs."""
from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Callable, Collection
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class Lease(Protocol):
    @property
    def client_id(self) -> str: ...


def _positive_env(name: str, default: int, maximum: int = 16) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class WorkerLimits:
    concurrency: int
    max_concurrent_per_client: int

    @classmethod
    def from_env(cls) -> WorkerLimits:
        concurrency = _positive_env("INSPRO_REVIEW_WORKER_CONCURRENCY", 1)
        per_client = _positive_env("INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT", 1)
        if per_client > concurrency:
            raise RuntimeError(
                "INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT cannot exceed "
                "INSPRO_REVIEW_WORKER_CONCURRENCY"
            )
        return cls(concurrency, per_client)


class ReviewScheduler[LeaseT: Lease]:
    """Fill worker slots while preventing one company from consuming the pool."""

    def __init__(
        self,
        *,
        owner: str,
        limits: WorkerLimits,
        claim_next: Callable[[str, Collection[str], int, int], LeaseT | None],
        process_lease: Callable[[LeaseT, str], None],
    ) -> None:
        self._owner = owner
        self._limits = limits
        self._claim_next = claim_next
        self._process_lease = process_lease
        self._executor = ThreadPoolExecutor(
            max_workers=limits.concurrency,
            thread_name_prefix="claim-review",
        )
        self._active: dict[Future[None], LeaseT] = {}

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def capacity(self) -> int:
        return self._limits.concurrency

    @property
    def active_client_counts(self) -> Counter[str]:
        return Counter(lease.client_id for lease in self._active.values())

    def reap_completed(self) -> int:
        completed = [future for future in self._active if future.done()]
        for future in completed:
            lease = self._active.pop(future)
            try:
                future.result()
            except Exception:
                logger.exception(
                    "Claim-review executor failed outside the job handler",
                    extra={"client_id": lease.client_id},
                )
        return len(completed)

    def fill(self) -> int:
        """Lease jobs into free slots, preferring a different company first."""
        self.reap_completed()
        started = 0
        while self.active_count < self.capacity:
            counts = self.active_client_counts
            lease = self._claim_next(
                self._owner,
                frozenset(counts),
                self._limits.max_concurrent_per_client,
                self._limits.concurrency,
            )
            if lease is None:
                saturated = frozenset(
                    client_id
                    for client_id, count in counts.items()
                    if count >= self._limits.max_concurrent_per_client
                )
                lease = self._claim_next(
                    self._owner,
                    saturated,
                    self._limits.max_concurrent_per_client,
                    self._limits.concurrency,
                )
            if lease is None:
                break
            future = self._executor.submit(self._process_lease, lease, self._owner)
            self._active[future] = lease
            started += 1
        return started

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
        self.reap_completed()
