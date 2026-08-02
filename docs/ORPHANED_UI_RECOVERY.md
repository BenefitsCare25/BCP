# Orphaned UI recovery + follow-up plan

**Status:** open. Written 2026-08-02 after a production incident where a whole
capability was found to have shipped unreachable.

Start here if you are picking this up in a new session. Everything below is
verified against the code at commit `c0ce8e8`, not inferred.

---

## 1. What happened, in one paragraph

The nav consolidation (14 broker pages → 9) retired `/operations/employees` and
split it across the roster and coverage pages. Its route was replaced with a
redirect to `/operations/roster?tab=employees`, but **several panels inside it
were never re-mounted on either successor**. The file
`frontend/src/routes/operations/employees.tsx` (~1000 lines) is now imported by
nothing and routed by nothing, so every feature it uniquely owned shipped
invisible — in local dev and production alike. Nothing caught it: not `pnpm
build`, not the 1382 backend tests, not mypy, not code review. It was found only
by opening the page in a browser.

The most consequential casualty — **Portal access** (create a member account /
mint a set-password link / set a password) — has been restored onto
`routes/operations/coverage.tsx` in commit `c0ce8e8`. While it was orphaned, a
broker had **no way to give any employee portal access at all**, even though the
backend endpoints, hooks and tests were all present and working.

**The lesson to encode:** a filename that reads like a route is not a route.
Reachability is decided by `frontend/src/router.tsx`, not by where a file sits.

---

## 2. Still unreachable — verified inventory

Each of these has **no consumer** other than the orphaned file. Verify any claim
with:

```sh
cd frontend
grep -rl "<hookOrComponent>" src/ --include=*.tsx --include=*.ts \
  | grep -v "routes/operations/employees.tsx"
```

| Feature | Hook / component | Consequence |
|---|---|---|
| **Run matching** (manual trigger) | `useRunMatching` | No way to re-run matching by hand |
| **Match results / status** | `useMatchResults` | **Matching staleness is invisible** — see below |
| Per-employee match override | `useSetMatchOverride` | Can't pin an employee to a category |
| Bulk delete employees | `useBulkDeleteEmployees` | No bulk roster cleanup |
| "Invite all to portal" | `useBulkInviteMembers` | No bulk portal onboarding |
| Flex benefits detail | `FlexBenefitsDetail` | Per-employee flex breakdown unavailable |
| Flex price-tag detail | `FlexPriceTagDetail` | Per-employee price tags unavailable |
| ~~Portal access~~ | ~~`MemberAccountActions`~~ | **RESTORED** in `c0ce8e8` |

**Matching is NOT broken** — `match_policy_year` still runs automatically on slip
upload, roster upload, ADC and setup confirm (see CLAUDE.md). What is missing is
the manual re-run and, more importantly, the display of
`MatchResultsOut.pending`, which means *never run OR stale* (any
`Category.updated_at` newer than the last run). CLAUDE.md says to surface it;
nothing currently does. **A broker can therefore edit categories and never learn
that every employee's assignment is now stale.** This is the highest-priority
item in this document.

`CoverageRevertControls` and `CoverageHistory` also live in the orphan but DO
have another consumer, so they are fine.

---

## 3. Recommended work, in priority order

### P1 — Re-mount the matching surface
Put matching status + re-run on **`frontend/src/routes/operations/roster.tsx`**
(it is the roster page; matching is a roster-wide operation). Minimum viable:
- the `pending` / stale indicator from `useMatchResults`,
- a "Run matching" action (`useRunMatching`),
- surface `MatchRunResult.errors` — it counts employees whose matching CRASHED,
  which is not the same as unmatched, and must not be silently dropped.

Per-employee override (`useSetMatchOverride`) belongs on the coverage page's
employee detail, beside the other per-employee controls.

### P2 — Stop this recurring (do this even if you do nothing else)
Add an unreferenced-code check to CI. `knip` or `ts-prune` in
`.github/workflows/deploy.yml` alongside the existing static checks, failing on
unreferenced **route files and components**. This would have flagged
`employees.tsx` the day it was orphaned. Small, one-off, permanent payoff — rank
it above finishing the type debt.

Consider also a single frontend smoke test asserting the Portal access panel
renders on `/operations/coverage` (frontend tests are on the deferred list; this
bug is the argument for starting them).

### P3 — Fix or hide "Invite to portal"
`POST /employees/{id}/member-account` branches on whether the employee has an
email:
- **no email** → creates the account + system login id + a set-password token.
  This path works.
- **has email** → creates the account, then `issue_otp` + `send_otp` emails a
  **sign-in code**. That code is unusable: portal sign-in is password-based and
  the only code field on `routes/portal/sign-in.tsx` is for **TOTP**. The
  emailed-OTP hooks (`useRequestCode` / `useVerifyCode`) have no consumer at all.
  And in production email cannot send anyway (see P4), so `mail_sent` is false.

Either repoint the button at the set-password flow, or relabel it "Create portal
account" and drop the OTP email. As it stands the label promises something that
cannot happen.

### P4 — Production email
`docs/EMAIL_SETUP.md`. `INSPRO_MAIL_MODE=smtp` with an empty `INSPRO_SMTP_HOST`,
so **prod cannot send any email**. Blocked on M365 admin rights. This is now the
gate on a real member rollout: invites, self-service password links and claim
notices all depend on it. Until then, onboarding is manual via **Set password**
or **Set-password link**.

### P5 — Finish the mypy debt
327 errors remain (was 550). The mechanical bulk is done; what is left needs
per-function judgement (83 `no-untyped-def`, 77 `arg-type`, 45 `union-attr`).
Worth doing **only** to flip the `Static checks (non-gating)` job to gating —
until then it shows a red X on every run that means nothing, which trains people
to ignore CI failures.

Triage note from this session: the scary-looking `union-attr` / `attr-defined`
errors are overwhelmingly **variable-reuse artifacts** (a name bound to one type
in a branch that returns early, then rebound later; mypy keeps the first
binding). Fix by **renaming the second variable**, never by casting. Confirmed
false positives: `rule_generator.py:125-127`, `utilization.py:251-262`,
`adc.py:741-742`, and the intentional `not (x in seen or seen.add(x))` dedupe
idiom in `slip_export/header.py:173`.

---

## 4. How to sign in to the portal (the thing that started this)

**Broker side:** Policy Admin → **Coverage & Members** (`/operations/coverage`),
Broker view, employee selected. **Portal access** is the first panel, above the
employee's name.
1. **Invite to portal** — creates the account (the OTP email fails in prod;
   ignore the warning toast, the account is still created).
2. The panel then shows **Username · no password yet** plus **Set-password link**
   and **Set password**. Use either.

**Member side:** `https://<host>/portal/sign-in?company=<slug>` — the
`?company=` is REQUIRED on first visit in header-tenancy mode (prod and local),
otherwise the tenant cannot resolve and it fails with "Those details weren't
recognised", which is indistinguishable from a wrong password. Slug for CDL is
`cdl`.

Sign-in accepts **any** of: email (anything containing `@`), the **system login
id**, or the **staff id**. An email-less account can only use the latter two.

---

## 5. Verification commands

```sh
cd backend && PYTHONPATH=. uv run pytest        # 1382 tests
cd backend && uv run ruff check app tests scripts
cd backend && uv run mypy app                   # 327 errors, non-gating
cd frontend && pnpm build                       # tsc -b + vite build
```

**A green build proves nothing about reachability.** That is the whole point of
this document — open the page.
