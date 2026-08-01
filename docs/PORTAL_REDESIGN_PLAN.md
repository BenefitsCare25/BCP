# Member portal redesign — build plan

**Read `DESIGN.md` first.** It owns the visual world ("Daylight") and the measured
tokens. This file owns the *build*: what is done, what is next, and the traps.

Reference the user pinned: `dock.cool` — warm light ground, large-radius
translucent panels, floating pill chrome. Approved previews:

- Home mosaic — https://claude.ai/code/artifact/36bdda33-b64d-48d4-b32d-8cbbae976a65
- Header options — https://claude.ai/code/artifact/f2101326-433b-4979-80f8-2562eba72605

---

## Progress

| # | Phase | Status |
|---|-------|--------|
| 0 | `DESIGN.md` rewritten for the new world | done |
| 0 | This plan | done |
| 1 | Token layer — `styles/leaf.css` | **done** |
| 2 | Primitives — `components/portal/leaf/*` | **done** |
| 3 | `PortalShell` + `PortalFrame` mirror | **done** |
| 4 | `/portal` home mosaic | **done**, verified in browser |
| 5 | `/portal/coverage` (3 tabs) | **done**, verified in browser |
| 6 | `/portal/claims` (list, detail, new) | **done**, verified in browser |
| 7 | `/portal/card`, `/clinics`, `/security`, `/enrollment` | **done**, verified in browser |
| 8 | Broker preview: a Home tab + retire the migration aliases | **done** |
| 9 | Verification — detector, full browser pass | **done** |

The **migration aliases** at the foot of `leaf.css`'s `@theme` block are GONE
(phase 8) — nothing reads `--color-mount`, `--color-issue`, `--radius-leaf` or
their siblings any more. If one reappears in a diff, it is a component that was
written against the old world.

### Decisions added while building

- **No "Submit a claim" action on the home** (user, during review). The home is a
  set of answers; submitting belongs to the Claims destination. It also leaves the
  home with no brand fill at all, so the member's own figures and verdicts are the
  only saturated things on it.
- **The benefit-year selector sits on the page-heading row, not in the bar.** In
  the bar it pushed the account icons past the right edge at laptop width.
- **No "OPEN" chip on the Enrolment nav item.** Time-sensitive signals live in the
  page — the home's enrolment tile and the phone dock's dot carry it.
- **The name is `text-2xl`**, not `text-3xl`.
- **Motion is on every route, not just the home** (user). `Mount` applies
  `leaf-rise` itself and the stagger is CSS `:nth-of-type`, so no component
  threads an index. Every mount gets a resting hover (glass brightens, shadow
  deepens); only mounts that navigate additionally lift. DESIGN.md's Motion
  section was rewritten to match — the doc must describe the built world.
- **The coverage tab strip is a glass pill, and it is ONE component**
  (`leaf/TabStrip.tsx`, used by the page and by `PortalFrame`). Active is
  `bg-ground` + ink, exactly as the desktop nav and the dock mark their current
  destination — a red rule under the active tab would be the brand's third
  appearance on a screen that already spends both. Full width on a phone, sized
  to its labels on desktop: stretched across 1180px, three pills stop reading as
  one control.
- **"What's left" partitions its products by whether they have an answer.** A
  product with no cap, nothing claimed and no sub-limits can only print "Nothing
  claimed yet" under its own name — Kamsinah's account rendered that sentence
  seven times, burying the one product with something pending. Those collapse
  into a single mount listing their caps; the rest keep their own. The old
  all-or-nothing `nothingClaimed` gate is now the case where nothing is active.
  Same rule inside the flex mount: a lone category with no sub-limit and no
  activity is the wallet restated, so it is dropped.
- **A schedule's "Show all N benefits" is ink, not brand.** A fully covered
  member holds eleven mounts, so a brand-coloured disclosure put the brand on
  the screen eleven times.
- **Dependants uses `Action`, not the shared `Button`** — that is what phase 2
  built it for. One brand fill on the page ("Send for approval"); adding a
  family member and attaching a certificate are quiet. A dependant's date of
  birth now prints through `formatDay` (it was raw ISO).
- **The hero figure handles uncapped benefits.** `heroFigure()` — a member whose
  benefits are all "as charged" has no remaining figure, and the first build
  rendered an em-dash where the display figure goes. It now reads approved, else
  pending (labelled as pending, never as settled).

---

## Decisions taken — do not reopen without the user

These were each settled by the user across four preview revisions.

1. **Light only. No dark tiles.** Considered and rejected. Hierarchy is carried by
   tile span and figure scale instead.
2. **The accent is the brand red, sampled from the logo mark** (`#D21B21`), not
   the old violet `#4c3a8f` and not black. The member portal and the broker app
   now share one identity.
3. **A tile home at `/portal`, first destination.** Coverage's three tabs are
   reached from the tiles that summarise them.
4. **One-row desktop header**, with **no primary action in it**. Mark, pill nav,
   year selector, account icons. The action lives in the page.
5. **No logo on mobile.** The phone bar is name + year selector + account icons.
6. **The logo is used whole and uncropped.** `frontend/public/inspro-logo-mark.png`
   is the transparent one (RGBA, alpha extrema 0–255). At 52px its three-line
   wordmark stays legible; below ~44px it does not, which is why the header is one
   tall row rather than two short ones.
7. **The bank-card-looking tile is gone.** It was a mis-drawing of the panel
   e-card. `/portal/card` remains a destination; there is no home tile for it.
8. **The active nav item is marked in ink, not red** — see The Twice Rule.
9. **Motion is wanted**, on the four things DESIGN.md lists. This reverses the
   previous "motion in exactly two places" rule.

## Carried over unchanged from the previous build

Semantics, not styling — all still load-bearing:

- Fullness means **utilisation**, never entitlement. Never render a mount for a
  benefit the member does not hold.
- Pending is shown beside approved and **never subtracted** from what's left.
  Approved is a solid fill, pending a hatch — separable by texture, not hue alone.
- Money is unabbreviated (`S$2,700`, never `S$2.7K`); tabular lining figures set on
  the `.leaf` root.
- Every code carries its gloss in the same frame.
- `.leaf` re-points the shared `ui/*` tokens for the subtree; `lib/leaf-scope.ts`
  is its behavioural twin and flips `InfoHint` from hover-tooltip to tap-open
  panel. **Set the class and the provider together, always.**
- Member-safe vocabulary only. AI pipeline states all collapse to "Under review".

---

## Phase 1 — Token layer (`frontend/src/styles/leaf.css`)

This carries ~80% of the visual change on its own, because every leaf component
already reads `var(--radius-leaf)` / `bg-mount` / `border-mount-rule`.

Define in `@theme` so Tailwind v4 generates utilities:

| Token | Value | Notes |
|---|---|---|
| `--color-ground` | `#F4F3F0` | page |
| `--color-glass` | `rgb(255 255 255 / .60)` | tile fill |
| `--color-glass-hover` | `rgb(255 255 255 / .80)` | |
| `--color-glass-edge` | `rgb(255 255 255 / .92)` | tile border |
| `--color-bar` | `#FFFFFF` | top bar, dock base |
| `--color-hairline` | `#DFDCD3` | borders, rules |
| `--color-track` | `#DEDAD0` | fill-rule track |
| `--color-record` | `#17160F` | body + figures |
| `--color-label` | `#5C584D` | lightest text tier |
| `--color-leaf-input` | `#7D776B` | control edges (1.4.11) |
| `--color-brand` | `#D21B21` | **fills only** |
| `--color-brand-ink` | `#B8161D` | **text only** |
| `--color-strike-*` | approved `#1C6B3F` · pending `#8A5A06` · review `#46433D` · rejected `#7E1F1A` | |
| `--radius-tile` / `--radius-control` / `--radius-pill` | 22px / 12px / 999px | |
| `--shadow-mount` / `--shadow-mount-hover` / `--shadow-float` / `--shadow-cta` | see file | each includes its inset top highlight |
| `--ease-leaf` | `cubic-bezier(.16,1,.3,1)` | |

Plus: the three ground blooms as a `.leaf-ground::before` layer, and the existing
`.leaf` re-point block updated to the new values.

**No component may carry a raw hex, a `rounded-[Npx]`, a `text-[Npx]` or an inline
shadow.** The user asked for this explicitly. Existing components are full of
`text-[0.8125rem]`-style values; convert them as you touch each file.

## Phase 2 — Primitives (`frontend/src/components/portal/leaf/`)

- `Mount.tsx` — glass tile: `bg-glass`, `backdrop-blur`, `border-glass-edge`,
  `rounded-tile`, `shadow-mount`; add an opt-in `tap` variant with the hover lift.
- `FillRule.tsx` — rounded track, `scaleX` fill animation with `transform-origin:left`,
  reduced-motion resolves to `scaleX(1)`. Keep the hatch for pending.
- `Strike.tsx` — rejected moves to oxblood. Everything else unchanged.
- `Figure.tsx` — currency mark at `0.44em`; add the display emphasis step.
- New `Action.tsx` — the filled brand pill and the ghost link, replacing the
  duplicated `leafAction` / `leafPrimaryAction` class pairs in clinics, security
  and enrollment. **This also fixes the last sub-44px control** (`LeaveTradingCard`'s
  36px Save button).
- `Field.tsx`, `LeafSkeleton.tsx` — retune to tokens.

## Phase 3 — Shell (`components/portal/PortalShell.tsx`)

Desktop, one row: mark (52px) │ hairline │ pill nav │ spacer │ year selector │
hairline │ bell, shield, sign-out. Active nav item = filled `bg-ground` pill,
ink text.

Nav labels shorten to fit one row: **Home · Coverage · Claims · Card · Clinics ·
Enrolment**.

Member name and benefit period move **into the page** as the heading.

Mobile: bar of name + year selector + icons (no mark); floating glass dock at the
bottom with the same five destinations, 52px targets.

**`components/operations/PortalFrame.tsx` mirrors this and must change in the same
commit** — the broker's employee-view preview renders the same components inside
`.leaf`.

## Phase 4 — `/portal` home

New route + `components/portal/HomeMosaic.tsx`. Tiles, in order:

1. **Left to claim** (span 2) — the one display figure, its fill rule, then two
   compact limit rows. → `/portal/coverage?tab=usage`
2. **Your claims** (span 1 desktop / 2 phone) — newest verdict struck, then two
   compact rows. → `/portal/claims`
3. **Submit a claim** — the brand pill. Head of content on desktop, full-width on
   phone.
4. **What's covered** → `?tab=benefits` · **My family** → `?tab=dependants`
5. **Nearest panel clinic** → `/portal/clinics`
6. **Enrolment notice** (span all) — only when a window is open. A pending-ink
   strike and a text link, **never a second brand fill**.

No backend work: `usePortalUtilization`, `usePortalClaims`, `usePortalMe` and the
clinic hooks all exist. Empty states matter — see the "quiet state" phone frame in
the preview.

## Phases 6–8 — what they settled

- **The claim form is split** into `components/portal/claims/`: `claimForm.ts`
  (vocabulary + pure helpers incl. `planFromSuggestion`), `claimValidation.ts`
  (the rules, pure), `useNewClaimForm.ts` (the state machine) and five section
  components; the route is 121 lines of composition. Behaviour is unchanged —
  the ordering rules, the rollback, the concurrent `allSettled` uploads and the
  multi-invoice queue were each a bug found once, so they were carried over with
  their comments, and every branch (referral, hospital sector, diagnosis,
  doc slots) was re-exercised in the browser afterwards.
  **The hook is ~690 lines and stays one file on purpose.** Every function in it
  is under 100; the pure parts are already out. Splitting the remaining STATE
  across two hooks would mean threading ~20 setters between them, which trades a
  line count for a coupling you have to hold in your head.
- **The receipt is the claim's own page**, reached with `?submitted=true`, not a
  screen of its own: it already holds what was sent, the document manifest and
  the status, so a member who reopens the claim a week later reads the same
  record. Resending from `needs_info` lands there too.
  - **No reference number, deliberately** (user, 2026-08-01). The invoice number
    is the CLINIC's, optional, and one invoice can split into several claims —
    and `list_claims` filters by status and employee only, so nothing in the
    broker app can look up an invoice number OR a claim id today. A number the
    member is told to quote that no one can resolve is worse than none. If
    lookup is wanted, add search to the broker queue; no schema change needed.
  - **No turnaround promise** (user: "skip for now"). Honest anyway: the AI
    check is immediate, the decision is a person's, and prod cannot email yet.
- **`Action` replaced the three copies of `leafAction`/`leafPrimaryAction`** in
  clinics, security and enrollment, which had already drifted. `block: "phone"`
  is full width on a phone and natural width from `sm` — `flex` makes an element
  a BLOCK-level flex container, so `w-auto` alone still fills the line and a
  brand pill stretched across 1180px reads as a banner.
- **Radix `Select` portals its listbox to `document.body`, outside `.leaf`** —
  so the member's open dropdown rendered in BROKER tokens. `LeafSelectContent`
  in `electionShared.tsx` re-declares `leaf` on the portalled content. Same
  class of bug as the hover-hint one; `InfoHint` solves it the other way.
- **The Reach Rule holds everywhere now.** The portal's account icons are 44px,
  `NotificationBell` grows to 44px and stops hiding its dismiss control behind
  hover when `useInLeaf()`, and `LeaveTradingCard`'s controls take 44px on the
  member surface only (the broker page stays on its 36px rhythm).
- **The broker preview gained the Home tab** and now opens on it.
  `HomeMosaicView` is the pure view; the member wrapper calls the hooks, the
  preview passes its own query results. Tiles navigate through `onGo`, because a
  real `<Link>` there would walk a broker out of their own application.

Still true, and still worth watching:

- `components/enrollment/electionShared.tsx` is **shared with the broker
  elections page**. Token-scoped changes are safe; a hardcoded radius or colour
  there leaks into the broker app.
- `ClinicLocator.tsx` and `MemberCard.tsx` are used by both the portal and the
  broker preview.

---

## What the code review caught (2026-08-01, after phase 9)

Fixed. Each was a case of the re-authoring dropping something the component it
replaced was doing:

- **The home had no error state**, and gated its skeleton on
  `utilization.isLoading && claims.isLoading`. Portal queries carry
  `localErrorHandling` + `retry:false`, so a failed `/portal/claims` showed the
  member "You haven't made a claim yet" — and the AND gate printed the same
  sentence transiently on every load. Now: OR for loading, `PortalErrorState`
  for any non-404 failure, with one retry that refetches all four.
- **`PortalErrorState` itself was still broker-styled** (a `bg-card` box and a
  32px button) on every member surface. Re-authored as a mount + `Action`.
- **The flex mount stopped short of the ledger.** It gated on
  `price_tags_total !== 0`, so a member with a leave trade and no priced
  upgrades saw an allowance that disagreed with the "What's left" tab; the leave
  row was gone, so allowance − spent ≠ left; a negative balance printed as
  "Left to spend S$-450"; and `price_age_known === false` (no DOB → tags never
  applied) was silent. All four restored, in member wording — and the negative
  reads "Short by" on both tabs.
- **The panel card lost its printed values when the artwork failed.**
  `CardCanvas` only prints placed fields when it has the artwork; the legible
  list beneath reprinted only member id + policy number. It now falls back to
  every value the card would have printed.
- **`dependantCost` returned 0 both for free cover and for a price we cannot
  work out**, so the member card asserted "Covering them draws nothing from your
  flex wallet" next to its own "pick a level" warning, then 409'd
  `unpriced_elections` on submit. Split into `dependantPricing` →
  `{ total, unresolved }`.
- **The submitted-enrollment banner was gated on `!readOnly`**, so the broker
  preview couldn't tell a submitted enrollment from an untouched one — the one
  thing the preview exists to show.
- **Cancelling the add-dependant form mid-submit** unmounted the only place its
  error could render (create-succeeded/upload-failed left a pending dependant
  and said nothing). Disabled while busy.
- **`aria-controls` pointed at an element that only existed while open.** The
  tail now stays mounted with `hidden`, so the association resolves in the
  collapsed state, where it is the thing that says there is more to read.
- Smaller: the schedule printed a bare `$` beside `FillRule`'s `S$`
  (`formatValue` takes a symbol now, broker unchanged); `productGloss` falls
  back to the product NAME, which several mounts also use as the heading, so
  they printed it twice (`glossBeside`); `ClinicLocator` had hardcoded DOM ids
  in a component rendered on two surfaces (`useId`); the phone year chip used
  `new Date(iso).getFullYear()`, the UTC trap `leaf/date.ts` exists to avoid.
- Removed: the `[animation-delay:Nms]` classes on the home tiles were dead —
  `.leaf-rise:nth-of-type()` in `leaf.css` outranks them.

**Not changed, deliberately:** with the schedule expanded, the promoted rows
(anything the member has claimed against) still render before the rest rather
than in the insurer's document order. That is the cost of having both the
promotion rule and the aperture; reopening it means giving up one of them.

---

## Open — needs a decision or backend work

1. **Benefit-year switching is not a UI toggle.** The portal resolves the member
   against the **current** year only (`portal_auth.active_policy_year`,
   `resolve_member_employee`). Reading a closed year means threading a year id
   through the portal endpoints, scoping statement/utilisation/claims to it, and
   gating it so a member can only open years in which they actually had an
   Employee row. **Until that lands, render the selector as plain text** (the
   single-year state), which is honest and ships today.
2. **Draft persistence on the claim form.** The receipt is built and the
   reference/turnaround questions are settled (see phases 6–8), but a member who
   loses the tab mid-form still loses the form. The draft claim only exists once
   submit runs.
3. **A panel-card home tile**, if wanted — the feature is real, only the
   bank-card drawing was wrong.
4. **Claim lookup for support.** `list_claims` filters by status and employee
   only. Adding a free-text search over invoice number would make the number the
   member already has quotable — the reason no reference number was minted.

---

## Verification

```sh
cd frontend && pnpm build                 # tsc -b + vite build
node "C:/Users/huien/.agents/skills/impeccable/scripts/detect.mjs" --json <changed targets>
```

Then a **live browser pass** on every route at 390px and desktop width. A green
build never proves a feature is reachable — that is standing feedback from the
user, and this redesign adds a new route and moves navigation, which is exactly
the class of change a type-check cannot catch.

Local portal sign-in has its own trap: it is **password, not OTP**, and needs
`?company=cdl` on the URL. See the auto-memory note `project_portal_local_signin`.

**If the browser window will not resize below its maximized width** (the Chrome
tooling could not change it during phase 5), the phone pass still runs honestly:
inject a `390×844` same-origin `<iframe>` pointed at the route. An iframe gets
its OWN viewport, so `sm:` media queries evaluate against 390px, and the frame's
`contentDocument` can be measured for overflow (`scrollWidth`) and 44px targets.

**Impeccable scripts must be run from `~/.agents/...`, not `~/.claude-work/...`** —
the junction breaks their main-module guard and they exit 0 having done nothing.
