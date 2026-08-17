# AI Claim Review Production Remediation

Status: implementation and local verification complete; deployment and live-browser validation pending
Prepared: 2026-08-17
Scope: claim submission, AI review execution, persistence, recovery, provider validation, broker UI, deployment, and observability

## Implementation result

The application now uses a durable PostgreSQL control-plane queue and a
separately deployed leased worker. Web requests only enqueue work. Stage
checkpoints, retries, lease recovery, provider activation provenance, terminal
UI states, worker health, metrics, and alerts are implemented.

Final local verification completed on 2026-08-17:

- Backend (default SQLite suite): 2,036 passed, 16 skipped.
- Focused claims/AI regression: 198 passed.
- PostgreSQL 16 migration: empty database upgraded to head successfully.
- PostgreSQL tenant isolation/provisioning/lease checks: 7 passed, including
  `FOR UPDATE SKIP LOCKED` across two concurrent sessions and append-only audit
  enforcement for a tenant provisioned after the migration.
- Frontend production build: passed.
- Alembic empty-database upgrade to head, downgrade to `f3a5c7e9b1d4`, and
  re-upgrade to head: passed.
- Bicep compilation and generated ARM template: passed.
- Ruff and `git diff --check`: passed.

The code is not labelled production-verified until deployment succeeds and the
Chrome smoke test completes through the signed-in Inspro employee portal with a
real test PDF. The live test must confirm upload, submission, durable queue
progress, Gemini extraction/comparison, a terminal review state, broker access,
and the expected member-visible result. A submitted smoke-test claim is retained
as an audited record and closed through the normal broker workflow; it must not
be deleted directly from the database.

## Claims-wide production hardening delivered

The implementation pass expanded beyond the worker lifecycle because a durable
AI queue is not sufficient if authorization, money, evidence, or concurrent
commands can corrupt the claim around it.

- **Authorization and audit:** claim routes are restricted to broker roles;
  viewers are read-only; configuration is admin-only; member/broker claim,
  message, review, and document access is audited with request metadata; audit
  rows are append-only in PostgreSQL and redact secret-bearing fields.
- **AI integrity:** prompts treat documents and stored claim text as untrusted;
  structured outputs are schema- and semantics-validated; missing, duplicate,
  malformed, or contradictory outputs fail closed to manual review; cache keys
  include tenant and provider context and expire after 24 hours.
- **Concurrency and replay safety:** claim mutations use row locks, approval
  locks the utilization bucket, and idempotency records prevent duplicate
  decisions, conversions, settlement operations, assessment, and reruns.
- **Money invariants:** monetary columns use `NUMERIC(18,2)`, FX rates use
  `NUMERIC(18,8)`, database checks constrain statuses and positive values, and
  over-claim/over-limit approvals require explicit acknowledgement. Audit JSON
  preserves canonical decimal values.
- **Evidence safety:** uploads enforce PDF/PNG/JPEG magic bytes and extension
  agreement; failed transactions remove newly written blobs; deletion first
  hides metadata in `delete_pending`, then a worker retries physical deletion;
  pending evidence is excluded from review and download paths.
- **Delivery and UX:** member notifications use a transactional leased outbox
  with bounded retry and no medical detail in email; worker readiness covers
  both AI jobs and notifications; claims search is server-side; review lookup is
  batched; UI actions come from server authorization; high-risk decisions use a
  second confirmation; members can securely download their own evidence.
- **Network and cache:** production storage and Azure Managed Redis are private
  endpoint-only. The move from legacy Azure Cache for Redis avoids provisioning
  a service already on Microsoft's retirement path. The legacy cache must only
  be removed after the new cache, secret, application connectivity, and rollback
  window have been verified.

The migration `a4c6e8f0b2d4_claims_production_integrity.py` applies the new
tables, constraints, numeric types, evidence state, and audit protections to the
public template and every tenant schema.

## Outcome

Replace the current FastAPI `BackgroundTasks` execution path with a durable, leased job queue processed by a dedicated worker. A review must either reach a terminal state or become eligible for automatic retry/recovery without depending on the lifetime of a web request, Gunicorn worker, or App Service container.

This is the production target:

```text
member/broker submit
        |
        | one database transaction
        v
claim = ai_review_pending
review = queued
public.claim_review_jobs = queued
        |
        v
dedicated worker claims job with a lease
        |
        +--> deterministic checks
        +--> document extraction
        +--> comparison
        +--> vision verification
        +--> verdict
        |
        v
review = complete | error
claim  = ai_verified | ai_flagged | submitted
job    = succeeded | failed | cancelled
```

The queue is database-backed. PostgreSQL is already authoritative, supports atomic cross-schema transactions, row locking, partial indexes, and `FOR UPDATE SKIP LOCKED`, and avoids making Redis availability part of claims correctness. Redis remains an AI response cache and rate-limit dependency, not the owner of a claim-review job.

## Historical findings resolved by the queue remediation

The following section records the original failure modes that led to this work.
It describes the pre-remediation implementation, not the current code.

### 1. Work is coupled to the web process

Claim submission commits a pending review and then calls `background_tasks.add_task(run_review, ...)` in:

- `backend/app/api/v1/portal_claims.py`
- `backend/app/api/v1/claims.py`

FastAPI background tasks are in-process and non-durable. A deployment, scale event, crash, worker timeout, or forced recycle can remove the only executor after the database already says the review is pending.

### 2. The shutdown window cannot cover the job duration

Gunicorn grants 60 seconds of graceful shutdown. A three-document review can legitimately require several extraction calls, one comparison call, up to four vision calls, capacity waits, and a throttle retry. Its bounded theoretical duration is measured in minutes, not seconds.

Increasing Gunicorn timeouts is not the fix. It only reduces one interruption mode and still leaves crashes, scaling, deployment cancellation, and process loss.

### 3. Recovery changes the outcome instead of completing the job

`claims_review/recovery.py` runs once at application startup and converts an interrupted review to an error/manual-review outcome. That prevents one stuck queue state, but it does not resume the review and cannot recover a hang that occurs without a later restart.

### 4. Long external calls retain application resources

The pipeline owns one ORM session across multiple provider calls. Once the session is dirty, `ai_gateway._slot` cannot release its checked-out database connection. Concurrent reviews can therefore occupy database connections and Starlette worker threads for minutes.

### 5. The state model cannot distinguish progress from failure

`ClaimAIReview.status` exposes only `pending`, `complete`, and `error`. There is no stage, attempt, lease, heartbeat, retry time, start time, completion time, or structured failure code. A healthy extraction and a dead task are indistinguishable.

### 6. Frontend polling uses a stale proxy for job state

`ClaimReviewPanel` polls when the surrounding claim says `ai_review_pending`. The selected claim prefers the claims-list row, and a rerun does not invalidate every detail cache. Polling can stop while the review is pending or continue after it is terminal.

### 7. Provider activation does not prove production compatibility

Saving a Vertex credential does not require a successful connection test. The current default model is `gemini-3.5-flash` in `asia-southeast1`. Google documents Singapore availability for that model under Provisioned Throughput, while Standard PayGo is listed for global, US, and EU endpoints. The platform must not activate a model/location/capacity combination until that exact combination succeeds.

### 8. Rerun is not idempotent

Two concurrent rerun requests can both supersede the prior review, create active review rows, and launch paid work. The database has no invariant enforcing one active review/job per claim.

## Target architecture

## 1. Durable public job table

Add `claim_review_jobs` to the public/control schema. It must not live inside a firm schema because the worker needs one queue to poll before it knows which tenant search path to select.

Recommended columns:

```text
id                    uuid/string primary key
broker_firm_id        required
client_id             required
claim_id              required
review_id             required
claim_revision        required
state                 queued | running | retry_wait | succeeded | failed | cancelled
stage                 queued | deterministic | extraction | comparison | vision | verdict | persist
attempt               integer, default 0
max_attempts          integer, default 3
available_at          required
lease_owner           nullable
lease_expires_at      nullable
heartbeat_at          nullable
started_at            nullable
finished_at           nullable
last_error_code       nullable, bounded string
last_error_detail     nullable, sanitized text
created_at            required
updated_at            required
```

Do not create cross-schema foreign keys from the public job table to tenant claim/review tables. Store identifiers and validate them after selecting the firm search path. The job is operational routing data; the tenant review row remains the business record.

Required indexes and constraints:

- Polling index on `(state, available_at)`.
- Lease-recovery index on `(state, lease_expires_at)`.
- Unique `review_id`.
- A PostgreSQL partial unique index that permits only one active job for a claim while `state` is `queued`, `running`, or `retry_wait`.
- A tenant-side partial unique index permitting only one non-superseded `ClaimAIReview` per claim.

SQLite tests need an equivalent application-level guard because SQLite partial-index behaviour and concurrency do not model PostgreSQL worker leasing fully.

## 2. Atomic enqueue

Submission and rerun must perform the following in one transaction:

1. Lock the claim row.
2. Validate the transition and expected claim revision.
3. Supersede the previous active review where applicable.
4. Create the new `ClaimAIReview` with status `queued`.
5. Set the claim to `ai_review_pending`.
6. Insert the public `claim_review_jobs` row.
7. Write the audit record.
8. Commit once.

There must be no state in which the claim is committed as pending without a committed durable job.

Use an idempotency key for rerun. A suitable server-generated key is:

```text
claim-review:{claim_id}:{claim_revision}:{review_id}
```

Repeated delivery of the same command returns the existing job/review instead of creating another provider run.

## 3. Dedicated worker

Run the worker outside the Gunicorn request-worker pool. Use the same application image with a separate command, for example:

```text
python -m app.workers.claim_review
```

Production deployment requirements:

- A separately deployable worker process/container.
- Minimum one running replica.
- Access to the existing VNet/private PostgreSQL endpoint, Azure Blob storage, Key Vault references, Redis cache, and Application Insights.
- Independent CPU/memory sizing and restart policy.
- A health/readiness surface that reports database reachability and worker-loop liveness.
- Graceful shutdown that stops leasing new work, continues heartbeating the current lease, and releases or expires the lease if the job cannot finish.

The web application may enqueue work but must never execute the full review pipeline as a fallback. Two executors for one queue create duplicate-spend and ownership ambiguity.

## 4. Lease protocol

Claim work atomically with PostgreSQL:

```sql
SELECT id
FROM public.claim_review_jobs
WHERE state IN ('queued', 'retry_wait')
  AND available_at <= now()
ORDER BY available_at, created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

Inside the same short transaction:

- Change state to `running`.
- Increment `attempt`.
- Set `lease_owner` to a unique worker instance ID.
- Set `lease_expires_at` to a short future time.
- Set `heartbeat_at` and `started_at`.

Heartbeat during external provider calls. Lease renewal must use a separate short database session so it is not blocked behind the pipeline's business transaction.

Before every stage write, confirm:

- The job is still leased by this worker.
- The review has not been superseded.
- The claim revision still matches the job snapshot.
- The claim is still `ai_review_pending`.

If any condition fails, mark the job `cancelled` and do not write a verdict over newer member or broker activity.

## 5. Stage checkpoints and database boundaries

Do not keep one ORM session open across the full review.

For every stage:

1. Open a short tenant-bound session.
2. Load the immutable inputs and the current job/review ownership.
3. Record the stage and commit.
4. Close the session before calling Vertex or reading large documents where possible.
5. Perform the external work.
6. Open a new tenant-bound session.
7. Revalidate the lease, supersession flag, claim revision, and claim status.
8. Persist the stage output and commit.

Persist extraction output per document. A comparison failure must not erase completed extraction evidence or its spend record.

Recommended review fields:

```text
status              queued | running | complete | error | cancelled
stage               same stage vocabulary as the job
progress_current    integer
progress_total      integer
attempt             integer
started_at          nullable
heartbeat_at        nullable
completed_at        nullable
error_code          nullable
error_detail        nullable, sanitized
```

Keep `verdict` restricted to terminal successful reviews.

## 6. Retry and terminal-failure policy

Classify failures instead of sending every exception through one generic fallback.

Retryable:

- Provider 429/throttling.
- Provider 5xx.
- Network timeout/connection reset.
- Temporary capacity-slot exhaustion.
- Worker interruption or expired lease.
- Transient database connectivity error before a durable stage write.

Permanent for the current job:

- Missing or invalid provider configuration.
- Authentication/permission failure.
- Unsupported model/location/capacity combination.
- Invalid document format after deterministic normalization attempts.
- Repeated structured-output parse failure after one bounded retry.
- Claim/review missing or superseded.

Use exponential backoff with jitter. Suggested defaults are three total attempts with delays near 30 seconds and two minutes, capped by an overall review deadline. Do not retry credential errors or deterministic business failures.

When attempts are exhausted:

- Mark the job `failed`.
- Mark the review `error` with a safe operator-facing error code and message.
- Move the claim back to `submitted` only if it is still owned by that job.
- Preserve completed stage outputs and paid token accounting.
- Emit an error metric and structured log.

## 7. Periodic recovery

Replace startup-only recovery with a periodic lease reaper.

The reaper must:

- Find `running` jobs whose `lease_expires_at` is in the past.
- Return retryable jobs to `retry_wait` when attempts remain.
- Fail exhausted jobs using the normal terminal-failure path.
- Reconcile impossible combinations such as a complete review with a pending claim.
- Never touch a job with a valid lease.

Keep startup recovery temporarily during migration, but narrow it to legacy pending reviews without a durable job. Remove it after all legacy rows have been reconciled and the new queue has operated successfully through at least one deployment cycle.

## 8. Provider validation and activation

Credential storage and provider activation must be separate states.

Store validation provenance:

```text
validated_fingerprint
validated_model
validated_location
validated_capacity_mode
validated_at
validation_status
validation_error
```

Rules:

- Saving a new key/model/location invalidates prior validation.
- The exact saved combination must pass a live structured-output probe before it becomes active.
- The probe must exercise function calling/tool output, not only a one-token text response, because the claims pipeline depends on structured function calls.
- Production review enqueue should fail over to an explicit manual-review outcome when no validated active configuration exists; it must not create a job that is guaranteed to fail.
- The UI must explain whether the selected model requires Standard PayGo or Provisioned Throughput in the fixed Singapore region.
- Do not silently substitute the global endpoint because claim documents are required to remain in Singapore.

Immediate production check before implementation: confirm the stored model and whether the associated GCP project has Provisioned Throughput for `gemini-3.5-flash` in `asia-southeast1`. If it uses Standard PayGo, select a Singapore PayGo-compatible model or provision the required throughput, then run the structured-output probe.

## 9. Frontend state ownership

The review endpoint is the source of truth for review polling.

Polling behaviour:

- Poll while `review.status` is `queued` or `running`.
- If the review does not exist yet and the claim is `ai_review_pending`, poll until the enqueue/read race resolves.
- Stop polling on `complete`, `error`, or `cancelled`.
- On a terminal transition, invalidate both `claims` and `claim-detail` queries.
- The rerun mutation must immediately update or invalidate `claim-detail`, `claims`, and `claim-review` using the active client-aware query keys.

UI requirements:

- Show the current stage and document progress.
- Show elapsed time from `started_at`.
- Distinguish queued, running, retrying, interrupted/retrying, failed/manual review, deterministic short-circuit, and fully completed review.
- Do not display a spinner indefinitely. Once the server's overall deadline is exceeded, show a recoverable error state backed by the job record.
- Disable rerun when an active job exists.
- Return the existing active job when rerun is repeated rather than displaying a duplicate success notification.

## 10. Observability

Every log record must include structured identifiers:

```text
job_id
review_id
claim_id
broker_firm_id
client_id
attempt
stage
lease_owner
duration_ms
provider
model
cache_hit
error_code
```

Do not log claim form contents, extracted medical details, document contents, credentials, or raw provider responses.

Required metrics:

- Jobs queued, running, retrying, succeeded, failed, and cancelled.
- Queue age of the oldest available job.
- End-to-end review duration.
- Duration and failure count per stage.
- Lease expirations/recoveries.
- Provider calls, latency, 429s, 5xx, auth failures, and parse failures.
- Cache-hit ratio.
- Active database connections attributable to the worker.
- Claims in `ai_review_pending` without an active job.
- Active jobs whose review or claim is missing/incompatible.

Required alerts:

- Oldest queued job exceeds the normal queue threshold.
- Any lease expires repeatedly.
- Terminal review failure rate exceeds threshold.
- Provider authentication or model-availability failure occurs.
- Pending claim/job invariant count is non-zero.
- Worker readiness is unhealthy or no heartbeat is recorded.

## State invariants

The implementation is not complete until these invariants are enforced or continuously reconciled:

1. A claim in `ai_review_pending` has exactly one non-superseded review and one active job.
2. An active job references a non-superseded review for the same claim and revision.
3. A `complete` review has a verdict and terminal timestamps.
4. An `error` review has an error code and no clean/flagged verdict.
5. A `succeeded` job corresponds to a complete review.
6. A `failed` job corresponds to an error review and a claim no longer owned by the pipeline.
7. A superseded review cannot have an active job.
8. At most one worker owns a valid lease for a job.
9. A worker without the current lease cannot persist stage output or a verdict.
10. Token/spend records survive a later stage failure.

## Implementation sequence

### Phase 0: production configuration containment

- Inspect the active platform and tenant model settings.
- Validate the exact credential/model/location/capacity combination with structured output.
- Resolve the Singapore PayGo versus Provisioned Throughput requirement.
- Add an operator-visible configuration failure message if the current combination cannot run.

Exit criterion: a live production-compatible structured-output probe succeeds, or claims explicitly degrade to manual review without entering a false running state.

### Phase 1: schema and enqueue transaction

- Add the public job model and migration.
- Add review progress/timestamp/error fields.
- Add active-review and active-job uniqueness constraints.
- Implement atomic enqueue for member submission and broker rerun.
- Preserve the old executor behind a disabled-by-default migration flag only if rollback requires it.

Exit criterion: every newly pending claim has one durable queued job before the API returns success.

### Phase 2: worker and leases

- Add the dedicated worker entry point.
- Implement polling, leasing, heartbeat, cancellation, retry, and lease reaping.
- Move stage orchestration out of FastAPI background execution.
- Split database sessions around external calls.
- Persist per-document extraction checkpoints.

Exit criterion: forcibly terminate the worker mid-review; another worker resumes after lease expiry without duplicate finalization or lost spend data.

### Phase 3: frontend state and operator UX

- Serve job/review progress fields.
- Poll from review status.
- Reconcile claim caches on terminal transitions.
- Add stage, retry, elapsed-time, terminal-error, and deterministic-short-circuit states.
- Make rerun idempotent and unavailable during an active job.

Exit criterion: the broker UI reaches the correct terminal state without a page reload for normal completion, retry, permanent failure, and worker interruption.

### Phase 4: deployment and telemetry

- Provision the dedicated worker deployment and networking.
- Add worker health/readiness and alert rules.
- Add dashboards for queue depth, age, duration, retries, provider failures, and invariants.
- Exercise deployment while a review is in progress.

Exit criterion: a production-style rolling deployment does not lose or manually downgrade an otherwise valid review.

### Phase 5: legacy reconciliation and removal

- Reconcile legacy pending/error rows.
- Confirm no pending claim lacks an active job.
- Remove `BackgroundTasks` dispatch and legacy startup recovery.
- Remove migration flags after a stable deployment period.
- Update `CLAIMS.md`, `AI_GATEWAY.md`, `EMPLOYEE_PORTAL.md`, and `DEPLOY_RUNBOOK.md` to describe the final architecture.

Exit criterion: there is one execution path and one recovery model.

## Verification matrix

Extend the existing claim-review pipeline coverage rather than creating an unrelated parallel test suite.

Required scenarios:

- Submission atomically creates claim, review, and job states.
- Transaction rollback leaves none of the three partially committed.
- Two workers cannot lease the same job.
- Lease expiry permits one safe retry.
- A stale worker cannot persist after losing its lease.
- Member amendment cancels an in-flight job and prevents stale verdict writes.
- Broker decision wins the race against finalization.
- Concurrent reruns produce one active review/job and one provider run.
- Provider 429 retries with backoff and preserves earlier extraction results.
- Provider authentication/model errors fail permanently without retry storms.
- Worker termination during extraction/comparison/vision resumes correctly.
- Exhausted retries return only the still-owned claim to manual review.
- Cached document extraction remains idempotent across retry.
- PostgreSQL tenant search paths are restored for every short stage session.
- The worker does not hold a database connection during provider latency.
- Polling stops on terminal review state and refreshes claim detail/list state.
- A deterministic failure is labelled as a short-circuit, not a full document review.
- Deployment/restart with active work causes lease recovery, not job loss.

Production verification must include PostgreSQL. SQLite remains useful for business-stage logic but cannot prove row locking, `SKIP LOCKED`, partial uniqueness under concurrency, search-path behaviour, or lease races.

## Rollout and rollback

Use a staged cutover:

1. Deploy schema additions first; existing code ignores them.
2. Deploy the worker with leasing disabled and verify readiness/database access.
3. Enable durable enqueue while leaving worker consumption disabled; confirm job creation invariants.
4. Enable one worker replica for a controlled company or feature-flag cohort.
5. Disable FastAPI `BackgroundTasks` execution before widening the cohort. Never run both executors for the same jobs.
6. Observe queue age, duration, errors, and invariant metrics through a full business cycle and a deployment.
7. Expand to all companies.
8. Reconcile legacy rows and remove the old path.

Rollback must disable new worker leasing first. Queued jobs remain durable and can wait. Do not roll back by deleting jobs or reverting pending claims. If the old web-task executor must temporarily return, it must consume only explicitly marked legacy work and never jobs owned by the durable queue.

## Definition of production-ready

The remediation is complete only when all of the following are true:

- No claim-review correctness depends on a web process remaining alive.
- Every pending claim has one durable active job.
- A worker crash and a rolling deployment are recoverable without manual intervention.
- External calls do not retain long-lived ORM transactions or exhaust the web database pool.
- One claim cannot incur duplicate concurrent reviews.
- Provider configuration is validated for the exact production model, region, capacity mode, and structured-output capability.
- Review progress and retries are visible without exposing PHI.
- The UI reaches terminal state without a manual refresh.
- Failed reviews return safely to manual processing with a specific operator-facing reason.
- Stage output and token spend already incurred are not lost on later failure.
- Alerts detect queue age, worker loss, provider incompatibility, and state-invariant violations.
- The old `BackgroundTasks` and startup-only recovery path have been removed.

## Primary code surfaces

Backend:

- `backend/app/api/v1/portal_claims.py`
- `backend/app/api/v1/claims.py`
- `backend/app/services/claims_review/pipeline.py`
- `backend/app/services/claims_review/recovery.py`
- `backend/app/services/claims_review/extraction.py`
- `backend/app/services/claims_review/comparison.py`
- `backend/app/services/claims_review/vision_verify.py`
- `backend/app/services/ai_gateway.py`
- `backend/app/core/ai_config.py`
- `backend/app/services/vertex_probe.py`
- `backend/app/models/claim_ai_review.py`
- `backend/app/models/claim.py`
- `backend/app/db/tenancy.py`
- `backend/app/main.py`

Frontend:

- `frontend/src/api/claims.ts`
- `frontend/src/components/claims/ClaimReviewPanel.tsx`
- `frontend/src/routes/operations/claims.tsx`

Deployment and documentation:

- `backend/Dockerfile`
- `infra/bicep/main.bicep`
- `.github/workflows/deploy.yml`
- `docs/CLAIMS.md`
- `docs/AI_GATEWAY.md`
- `docs/EMPLOYEE_PORTAL.md`
- `docs/DEPLOY_RUNBOOK.md`

## New-session starting instruction

Use this document as the implementation contract. Start with Phase 0 and Phase 1. Do not patch the symptom by increasing Gunicorn timeouts or adding more startup recovery. Preserve the existing deterministic checks, comparison logic, vision cap, tenant isolation, amendment/decision race protections, caching, budget enforcement, and manual-review fallback while replacing only the unreliable execution and state-coordination layers.
