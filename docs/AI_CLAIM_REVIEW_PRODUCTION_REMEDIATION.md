# AI Claim Review Production Status

Status: deployed and production verified

Last updated: 2026-08-18 10:41 SGT (Asia/Singapore)

Deployed baseline:

- Claims AI hardening: `21384f3efc2bff9b348000f3664f894a21b205b8`
- Runtime packaging and deploy gate: `9aa1c638666ba5ef8f76a46f1708b0d439b830e0`
- Successful deployment run: <https://github.com/BenefitsCare25/BCP/actions/runs/32092000563>

This document records the implemented production behavior. The original
remediation plan has been removed because it duplicated the code and described
`BackgroundTasks`, planned phases, and acceptance criteria that are no longer
the live architecture.

## Configuration ownership

The claims page is `/claims/review`. Its visible tab names and responsibilities
are:

| Tab | Responsibility | Does not control |
|---|---|---|
| Queue | Active claim review work, AI findings, evidence, and the broker's final decision | Autofill or review configuration |
| Messages | Member conversations and reply status | AI behavior |
| Review rules | Per-company, per-claim-type field comparisons, required-document additions, AI rules, severity, evidence requirements, and selective vision checks | Employee claim-form autofill |
| Claim settings | Benefit-year submission and leaver deadlines plus the company's claim-document vocabulary, aliases, key fields, and autofill slot mapping | Review verdict thresholds or provider credentials |

The route query value for **Review rules** remains `tab=ai-extraction` so old
deep links continue to work. The old visible label "AI extraction" was
misleading and has been replaced.

### Employee portal autofill

Employee claim-form autofill is driven by:

1. The platform or company Vertex/Gemini provider configuration under
   **Settings > AI Provider**.
2. The claim intake profiles in `backend/app/services/claim_intake.py`.
3. Document types, aliases, key fields, and autofill slot mappings configured
   under **Claim settings**.
4. Deterministic normalization and suggestion logic in
   `backend/app/services/claim_intake_suggest.py`.
5. The versioned intake confidence thresholds in
   `backend/config/claims_ai_thresholds.json`.

Low-confidence or conflicting values are not silently accepted. The form shows
them for member confirmation or leaves them blank, and normal server-side claim
validation still runs when the claim is saved and submitted.

### AI review results

The **Review rules** tab configures the rules that produce AI review findings
and the suggested `ai_verified` or `ai_flagged` outcome. Defaults remain in
`backend/app/services/claims_review/field_maps.py`; a stored company override is
used only for its matching claim-type key. Deleting an override reverts that
claim type to the code default.

Configured required documents add to profile-derived requirements. They do not
replace subtype, hospital-sector, or referral requirements. `verify_with_vision`
controls additional AI spend, while `require_evidence` independently controls
whether an unsupported field can pass review.

The AI result is decision support. A broker remains responsible for the final
claim decision.

### Provider and platform limits

**Settings > AI Provider** controls the shared Vertex service account, model,
Singapore location, capacity mode, token budgets, and live provider-call
concurrency. A company may use a BYOK override. Provider activation succeeds
only after the exact stored credential fingerprint, model, location, capacity
mode, and structured function-call probe match.

The AI gateway's provider-call concurrency limit is separate from the claim
worker's job concurrency. Both limits can apply to the same review.

## Implemented production controls

The five production-readiness workstreams are complete:

1. **Clear configuration ownership and UI states.** Tabs use task-based names,
   configuration is restricted to `broker_admin` and `system_admin`, terminal
   states are visible, and legacy deep links remain compatible.
2. **Accuracy calibration and fail-closed handling.** A versioned evaluation
   corpus and confidence profile cover intake and review flows. Thresholds can
   vary by model and document type. Malformed, contradictory, incomplete, or
   low-confidence output routes to confirmation or manual review.
3. **Conflict-safe configuration.** Review-rule and document-type writes carry
   expected update timestamps. Stale edits return a conflict instead of
   overwriting newer company configuration. Import and reset operations carry
   target versions.
4. **Durable, fair concurrent execution.** Submission atomically creates the
   review and a PostgreSQL control-plane job. A dedicated leased worker uses
   `FOR UPDATE SKIP LOCKED`, heartbeats, stage checkpoints, bounded retries,
   stale-lease recovery, idempotency keys, and per-company scheduling limits.
5. **Production verification and deploy protection.** Backend, PostgreSQL,
   frontend, E2E, Bicep, container, health, and readiness checks are in the
   release path. The image is validated for its confidence profile before push,
   and production must remain healthy through a delayed stability window.

Additional claims-wide controls include row locks on claim mutations, claim
revision checks, append-only audit records, decimal money invariants, upload
magic-byte validation, private Postgres/Redis/storage networking, and a leased
notification outbox.

## Runtime architecture

```text
member or broker submits/reruns
             |
             | one database transaction
             v
claim = ai_review_pending
review = queued
public.claim_review_jobs = queued
             |
             v
dedicated worker leases the job
             |
             +-- deterministic checks
             +-- document extraction
             +-- field comparison and configured rules
             +-- selective vision verification
             +-- verdict
             |
             v
review = complete or error
claim = ai_verified, ai_flagged, or submitted for manual review
```

The web process never executes the review. A deployment, API recycle, or request
timeout therefore cannot remove the only executor after a claim is marked
pending. A stale worker cannot persist stage output or a verdict after losing
its lease.

## Current production capacity

Production currently runs one dedicated review-worker App Service instance:

| Setting | Value |
|---|---:|
| `INSPRO_REVIEW_WORKER_CONCURRENCY` | 4 |
| `INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT` | 2 |
| `INSPRO_DB_POOL_SIZE` | 8 |
| `INSPRO_DB_MAX_OVERFLOW` | 4 |

This means:

- Up to four claim reviews execute concurrently across the platform.
- One company can consume at most two active review slots.
- Multiple companies are preferred when filling available slots.
- Hundreds of submissions can be accepted durably and queued.
- The current system does not execute 300 AI reviews simultaneously.

The scheduler validates both concurrency settings as integers from 1 through
16 and rejects a per-company limit above the total worker limit. The production
values are a comfortable starting point for the current B2 App Service plan,
PostgreSQL B2s database, and shared provider quota; they are not a claim of
capacity beyond the measured environment.

Before increasing concurrency, run a production-like load test and inspect
provider 429s, queue age, review duration, worker memory/CPU, and database pool
wait time. Scale in measured steps. Scaling App Service instances does not by
itself raise active review capacity because the database-backed global worker
limit is enforced across workers.

## Accuracy profile

The deployed confidence profile was generated at `2026-08-18T09:07:12+08:00`
from a hashed gold dataset:

| Flow | Samples | Default threshold | Auto-accept coverage | Observed false-accept rate |
|---|---:|---:|---:|---:|
| Intake | 24 | 0.73 | 54.17% | 0% |
| Review | 16 | 0.70 | 62.5% | 0% |

These are conservative initial calibration results, not a statistical guarantee
of production accuracy. The sample sizes are small. Expand the de-identified,
adjudicated gold set across insurers, scans, languages, document quality, claim
types, and edge cases before loosening thresholds. Recalibration must preserve
the configured maximum 5% false-accept policy and version the resulting profile.

## State invariants

The queue and recovery paths enforce or reconcile these invariants:

1. A claim in `ai_review_pending` has one current review and one active job.
2. An active job references the same claim revision and a non-superseded review.
3. At most one worker owns a valid job lease.
4. A worker without the current lease cannot persist output or a verdict.
5. A complete review has a terminal verdict and timestamps.
6. A failed review returns the claim to a manual-reviewable state.
7. A superseded review has no active job.
8. Provider spend already incurred survives a later stage failure.

## Monitoring and operations

Health gates:

- API liveness: `/health`
- API dependency readiness: `/readiness`
- Worker loop and notification readiness: `/readyz`

Monitor:

- Queue depth and age of the oldest available job.
- Running, retrying, succeeded, failed, and cancelled jobs.
- End-to-end and per-stage duration and failure count.
- Lease expiration and recovery count.
- Provider latency, 429s, 5xx, authentication failures, and parse failures.
- Cache hit ratio and worker database-pool pressure.
- `pending_without_job` and `active_missing_record` invariant failures.
- Worker heartbeat/readiness and terminal review failure rate.

Logs include job, review, claim, firm, client, attempt, stage, lease-owner, model,
cache, duration, and error-code context. Logs must not contain claim form values,
medical details, document contents, credentials, or raw provider responses.

## Deployment record

The first `21384f3` deployment exposed a missing runtime artifact: the API image
did not contain `backend/config/claims_ai_thresholds.json`, so Gunicorn workers
failed during startup. The API was rolled back while the independent review
worker remained healthy.

Commit `9aa1c63` corrected the image and release process:

- `backend/Dockerfile` packages `backend/config` as `/app/config`.
- CI loads and validates the confidence profile inside the built image before
  pushing it.
- Azure deployment uses `--container-image-name`.
- Production smoke testing waits for App Service recycling, checks API liveness,
  API dependency readiness, and worker readiness, then repeats all checks after
  a 30-second stability window.

At 10:41 SGT on 2026-08-18, the API and worker were both on image `9aa1c63`, the
API reported database and Redis ready, and the worker reported ready.

## Verification baseline

Completed before the production deployment:

- Backend default suite: 2,052 passed, 16 skipped.
- PostgreSQL contention and tenant-isolation suite: 8 passed.
- Ruff and Python compilation: passed.
- Frontend typecheck, production build, and claims review E2E: passed.
- Bicep validation: passed.
- Local production-image build, confidence-profile load, and full application
  import: passed.
- Delayed production API and worker smoke checks: passed.

## Remaining operational debt

- Expand the small claims AI gold dataset before treating accuracy percentages
  as stable across the production population.
- Run a sustained multi-company load test before raising worker concurrency or
  promising a specific queue-drain SLA.
- MyPy currently reports annotation debt under a non-blocking CI step.
- GitHub Actions still emits Node runtime deprecation warnings for older action
  versions.
- One worker instance and the current Basic plan do not provide worker-process
  high availability. The durable queue prevents lost work, but recovery waits
  for the service to restart.

The local workspace references `docs/DEPLOY_RUNBOOK.md` for deployment and
rollback, `docs/CLAIMS.md` for claims behavior, and `docs/AI_GATEWAY.md` for
provider controls. Those general Markdown notes are intentionally ignored by
this repository; this production status file is the tracked source of truth for
the claims AI remediation.
