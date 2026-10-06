# Enrolment workflow review — 6 October 2026

The reported cancellation was a destructive reset, not a request for correction.
The reset endpoint deleted elections and leave, changed the enrolment to
`not_started`, and wrote a broker audit entry. It neither updated the signed form
nor created an employee notice. The portal bell only displayed browser-local
errors. That explains both the silent cancellation and the contradictory “Sent”
form in the screenshots.

## Workflow contract

| Stage | Expected behaviour | Implementation and boundaries |
| --- | --- | --- |
| Configure and open | Validate setup, identify affected employees, choose dates and defaults, make the period available. | Existing readiness remains advisory, as explicitly requested on 5 October. Opening does not send invitations or change credentials. Date, overlap, tenant and role checks remain enforced. |
| Provision access | Invite employees using individual addresses; handle shared/missing mailboxes separately. | Existing bulk invitation and individual activation flows retained. Opening is independent of mail availability. |
| Save draft | Save plan, dependant and leave choices together without changing live coverage. | Both portal and broker now have an atomic draft endpoint. If leave validation fails, plan changes and any signature invalidation roll back too. |
| Sign and submit | Validate declarations, signature, eligibility, prices and wallet; retain an immutable PDF; place choices in the review queue. | Existing common validation retained. A durable employee receipt is recorded in the transaction. A request identifier makes retries idempotent, and an expected lifecycle event rejects a stale page. |
| Review | Inspect the submitted selection, then confirm or return it. | Broker submitted choices are read-only; direct edit endpoints require an explicit return/reopen first. Acknowledging receipt of a form is distinct from confirming benefits. Invalidated forms cannot be acknowledged. |
| Return for correction | Explain what is wrong, retain choices, ask the employee to correct and sign again. | New `returned` state, required trimmed reason (maximum 2,000 characters), portal notice and email outbox where configured. Member self-service and an editable period are required. Ordinary broker submission cannot bypass the fresh signature. |
| Cancel and clear | Explicitly discard choices, retain evidence and explain the cancellation. | System administrators only, enforced in API and UI. Clears saved choices, marks the old form cancelled, records reason/history/notice, and leaves confirmed coverage untouched. Repeated cancellation cannot duplicate the notice. |
| Confirm | Revalidate and project choices to effective coverage; distinguish internal confirmation from any outstanding underwriting requirements. | Confirmation records a notice, including bulk and period-close paths. Repeated confirmation is idempotent. Underwriting refresh remains in the transaction. |
| Reopen confirmed choices | Preserve current coverage until replacement choices are confirmed. | Required reason and notice. Existing portal signatures become historical; a signed enrolment requires enabled member self-service, employee correction and a new signature. Broker-managed selections without a portal signature retain the existing broker edit/submit path. |
| Deadline and close | Stop edits at the deadline while allowing review; resolve outstanding corrections before applying defaults. | Returned cases appear in the close preview and block closure. Extend the deadline when employees need more time. Submitted choices are revalidated. Default retention/decline outcomes record notices. Saved-draft submission remains an explicit existing broker option. |
| After closure | Retain forms, decisions and notifications. | Portal notices and PDF history remain readable independently of an open period. Confirmed coverage corrections continue through the existing authorized coverage-revert workflow. |

## Notification behaviour

`enrollment_events` is tenant-scoped and committed with each lifecycle change.
Employees see their own notice, reason, period and deadline, with server-persisted
read state. The header bell combines these records with existing local browser
alerts without changing broker/HR alert behaviour. The enrolment page displays
current status and history. A changed lifecycle locks the stale page until the
employee refreshes the saved choices.

Email is separate from the portal record. SMTP readiness and an individual active
account determine whether a generic message is queued. The email contains a
company portal sign-in link, not the correction reason or benefit details. The
existing leased worker retries failures up to five attempts, rechecks recipient
ownership, and records failures without exposing provider response contents.
Log/disabled mail is never labelled sent by this flow. “Sent” in the outbox means
accepted by the mail provider, not proven inbox delivery.

Broker activity exposes queued, sending, accepted, unavailable, failed and
cancelled delivery states. Retry uses the same outbox record and cannot duplicate
queued, sending or already-accepted mail. Shared roster mailboxes, changed
recipients and disabled accounts do not receive individual notices. Users without
email still retain their portal records; offline follow-up remains the benefits
team's responsibility.

The original signed snapshot, content hash and PDF are never rewritten. Only
workflow standing changes. Subsequent signed versions replace earlier versions;
the lifecycle history retains the reasons for return/cancellation. Older forms
whose enrolment is already a draft are displayed as no longer current rather
than “Sent”.

## Migration and local evidence

Migration `c7e9a1b3d5f7`, after `b6d8f0a2c4e6`, creates the event table in public
and existing firm schemas. It recovers only unresolved historical cancellations
proven by matching reset audits, enrolment ownership, current `not_started` state
and form timestamps. Such records explicitly say that a reason was not recorded.
No retrospective email is enqueued. Forms without sufficient evidence are not
guessed or changed. Recovery is tested for repeat execution and preservation of
signed hashes.

The canonical local database is upgraded and the approved launcher serves
`http://localhost:5173` with API port 8000. Before/after counts are unchanged:
9,151 employees, 470 accounts, 509 enrolments, 26 elections, four plan overrides,
one form and 186 retained document records. Integrity is OK, foreign-key errors
are zero, and the aggregate signed-evidence hash is unchanged. No local records
matched the historical cancellation recovery conditions; no email was queued.
The screenshots' specific cancellation therefore has not been repaired in a
production database during this task.

No database copy, seed replacement, real employee selection, credential change,
live email, commit, push or production deployment was performed. Existing
unrelated packaging/deployment changes in the working tree were preserved.

## Verification and release requirements

- Final full backend suite: **2,714 passed, 21 skipped**, no failures (324.51s).
  Existing dependency deprecation warnings remain.
- 112 focused enrolment tests passed, including cancellation, return, resubmission,
  retries, invalid drafts, PDF failure rollback, historical recovery and deadlines.
- 253 combined portal, tenant-isolation and shared-delivery tests passed.
- Final affected-module rerun: 56 tests passed, including signed reopening with
  self-service disabled. The initial full run exposed two outdated tests: direct
  leave editing after confirmation and replacing saved product mappings without
  administrator permission. Tests now exercise explicit reopening and the
  existing administrator restriction; permissions were not relaxed.
- 42 desktop/mobile browser checks passed across lifecycle, enrolment remediation
  and portal/HR entry flows. New workflow checks cover reason validation,
  non-admin cancellation visibility, failed-request draft retention, notification
  read persistence, request failures, cancelled form labels, horizontal overflow
  and scoped accessibility scans. Synthetic desktop/mobile screenshots were
  visually reviewed. The scoped design detector returned no findings.
- Backend lint and strict MyPy passed for 378 source files. Frontend TypeScript
  and production build passed. Final whitespace checks passed, and the local
  readiness endpoint reports healthy database connectivity.

Release the migration, API, worker and frontend together. Reload old browser
bundles: reset/reopen now require a reason. Verify the migration and concurrent
review/sign/close requests on PostgreSQL before production rollout; Docker's
Linux engine is not running locally, so that acceptance was not executed here.
Use a designated test recipient to verify actual SMTP delivery and failure
monitoring after configuration. No live mailbox delivery is claimed by the
fake-mailer tests. Physical Safari/device acceptance is also untested.

Scheduled opening announcements, deadline reminders and delivery-bounce webhooks
are separate enhancements, not implemented or implied by this correction.
Current notices cover submission, revision, return, cancellation, reopening,
confirmation and default finalisation. A production release is not yet performed
or certified by the local checks.

## Review follow-up: passive sessions, paper receipts and partial drafts

All three review findings were verified and corrected locally:

- Notice and live-period checks are passive. Access validation still enforces
  idle/absolute expiry, but does not advance activity for these reads. Passive
  refreshes carry the previous activity timestamp into the rotated session;
  deliberate authenticated actions still advance it. Tests cover both polling
  without a header and refresh with the passive marker, then actual idle expiry.
- Current paper forms can be acknowledged while elections are not started or
  in progress. Invalidated forms still fail acknowledgement. Broker and employee
  badges apply the legacy draft-state invalidation inference only to portal forms.
- Mail-disabled events never resolve recipients. SMTP ownership checks load only
  staff IDs and mailbox fields once per company transaction, including account
  addresses. Bulk operations reuse that map; each new transaction rebuilds it.
  Shared addresses remain excluded and delivery workers revalidate ownership.

Partial forms are **not autosaved or automatically signed**. Save choices stores
benefit and leave choices only. Other form entries stay in browser memory until
Sign and send, including contact corrections, family requests, declarations and
signature. The page explains this on every step and warns before navigation or
reload would discard unsent work. Invalid leave choices cannot produce a success
message while being silently omitted from a save.

Passing the deadline locks editing/saving/signing; it does not run period closure.
The benefits team closes the period explicitly. Saved-but-unsent elections follow
the configured keep/decline default unless a broker explicitly selects the
existing submit-saved option. That option does not create an employee signature.
Returned submissions continue to block closure until resolved.

The page checks the deadline every second and polls scoped period state every
30 seconds without extending the session. It keeps unsent entries visible when
the period closes early, self-service is disabled, or status checking fails.
Late rejected saves/signatures trigger an immediate status recheck. The backend
remains authoritative between checks. Closure notices report the applied outcome.
Deadline dates and times are shown in Singapore time. Unsaved personal details
are not persisted to browser storage; an expired session still requires sign-in.

Follow-up verification: 196 backend regression tests passed across enrolment,
authentication and delivery; strict MyPy passed for 378 files and lint passed.
The browser runner reports 48 desktop/mobile checks passed, including six new
partial-form/closure checks, passive token refresh, unsaved navigation, failure
recovery and a rejected save racing closure. Desktop/mobile screenshots were
visually reviewed; scoped accessibility and overflow checks passed. Production
frontend build passed. PowerShell's redirected stderr reported a nonzero shell
status for warning output despite the successful runner summary; the build was
also verified directly without redirection. No deployment or live mail was sent.
