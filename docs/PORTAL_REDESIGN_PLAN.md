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
| 10 | Coverage deck — "What's covered" becomes an index + a stage | **done**, verified in browser |

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

## Phase 10 — the coverage deck (2026-08-01)

"What's covered" rendered one `BenefitMount` per product down one column. A fully
covered CDL member holds nine, each with its own schedule, and the page ran to
nine screenfuls — which serves neither thing a member opens it to do (look one
benefit up; see what they hold). It is now `leaf/Deck.tsx`: a sticky rail naming
everything, and one product on stage. `CoverageLeaf` owns what belongs to
coverage — which slides exist, their rail labels, which one it opens on, how the
selection is carried — and `Deck` owns the mechanism.

Settled while building, and the reasons are the load-bearing part:

- **The rail is the point, not the animation.** A deck without a visible index is
  a carousel, and a carousel is strictly worse than a scroll for a lookup. Sticky,
  so it is still reachable three screenfuls down.
- **It opens on what is MOVING** — the product with a pending claim, else one with
  an approved claim, else the first. Opening on whichever product sorts first is
  an arbitrary choice dressed as a default.
- **ONE tablist element, relaid out.** The obvious build renders a horizontal rail
  and a vertical one and hides one with `hidden`; that is fine for a heading
  (`PortalShell` does it with its two `h1`s) and wrong for a tablist, which owns
  roving focus and `aria-controls` that a second live copy duplicates. Same note
  as `HeadRail`, same reason.
- **The layout switches on the DECK'S width, via ResizeObserver — not the
  viewport's.** `PortalFrame` renders this in a column much narrower than its
  window, and a media query would hand that column a two-column desktop layout it
  cannot fit. Verified: the preview column measures 836px and gets the vertical
  rail; a 390px iframe of the same code gets the horizontal one.
- **The pill is measured and transformed, NOT a `layoutId`.** motion.dev's layout
  animation is the idiomatic answer and the wrong one here: the rail is a
  horizontally scrolling container that the deck also scrolls programmatically to
  reveal the active chip, and a layout projection measured across a concurrent
  scroll lands the pill somewhere it does not belong.
- **Only a change of SELECTION animates the pill** (`data-still`). This was a real
  defect caught in the browser, not a hypothetical: `wide` starts false, so the
  pill is measured against the horizontal rail one frame before the
  ResizeObserver reports the vertical one, and it flew diagonally across the page
  on every load. Suppressed for two frames — one to paint the move, one to restore
  the transition, because restoring it in the same commit as the move is the case
  browsers disagree about. The `ResizeObserver` guard compares against the last
  size for a related reason: a fresh observer always fires once on `observe`, and
  the effect re-runs on every selection, so an unguarded callback cancelled the
  very travel it existed to preserve.
- **`Mount` gained `rise={false}`.** Two entrances on one element do not compose —
  the deck's directional transition ran with `leaf-rise` fading and lifting the
  same node underneath it. Verified both ways: deck slides carry no `.leaf-rise`,
  and "What's left" still staggers at 0 / 0.05 / 0.1s.
- **`?p=` uses `replace: true`.** Stepping through nine products is reading, not
  navigating; without it Back walked back through every product visited instead of
  leaving the page. Verified: `history.length` unchanged across selections. The
  tab switcher deliberately does NOT carry `p` across — it names a slide only
  "What's covered" has.
- **An unknown `?p=` resolves to the default**, not to an empty stage — a
  bookmarked product dropped at renewal must still open the page. Verified with
  `?p=WICA` on a member who has no WICA.
- **Chips carry the short gloss, never the code.** `productShortLabel` in
  `glossary.ts` is a second tier of the same copy (the sentence gloss's headline),
  falling back to the insurer's product NAME and only then to the code. A rail
  reading `GCGP · GCSP · GHS` is exactly what the Printed-Label Rule forbids.
- **Prev/next are named by destination and are `neutral`**, not terracotta — they
  pick a view (Do-vs-Pick). They go to `opacity-0` at the ends rather than
  greying: a permanently dead control is furniture, and the counter says where you
  are. The counter itself is `aria-hidden`; the chips carry `aria-posinset` /
  `aria-setsize`, so a screen reader would otherwise announce the position twice.
- **The outgoing panel is `inert` + `aria-hidden` while motion keeps it mounted**,
  set through a ref because React 18 drops an unknown boolean `inert` prop
  silently. Verified mid-transition: two panels, the outgoing one absolute, inert
  and hidden.
- **Height never animates** (`mode="popLayout"`), and the reader is scrolled to the
  top of the new schedule when the stage has already left the viewport — never
  landed halfway down a product they have not seen. `scroll-mt-20` keeps the
  sticky rail off the heading it scrolls to.
- **Swipe locks direction**: 40px threshold, 2:1 horizontal over vertical, and a
  24px left-edge guard so it cannot fight the OS back-swipe. Vertical scrolling
  wins every tie — this page is read by scrolling.

Browser pass: nine chips at 390px, all 44px, rail scrolls inside itself with no
document overflow and no sub-44px target; sticky at 8px; arrows / Home / End move
selection and focus with a correct roving tabindex; the outer Coverage strip is
unaffected.

### Fixed on the user's review of phase 10

Four faults, three of them older than the deck — the deck only made them legible by
putting one product on screen at a time.

- **The activity dot is GONE.** It was added in this phase and the first person to
  see it asked what it meant, which is the answer. A mark carrying meaning no frame
  explains is an unglossed term, and a nine-chip rail has nowhere to print the gloss
  it would need. The signal survives where it can be read: the deck still opens on
  the product that is moving, and that product's mount states the claim in words.
- **Two descriptions became one.** `BenefitMount` printed our per-code gloss under
  the title AND the slip's `cover_description` further down inside the schedule. On
  GHS the member read the same fact three times ("Group Hospital & Surgical" /
  "hospital stays and surgery" / "Cover: Reimbursement of eligible inpatient
  expenses…") before reaching a figure. The policy's description now sits under the
  title and the gloss is its fallback; `ScheduleLeaf` no longer takes
  `coverDescription` at all. The leading `Cover:` label is stripped — it is a slip
  field name, and under the title it is furniture.
- **Margins compounded with the mount's gap.** `Mount` is `flex flex-col gap-3` and
  every block in `BenefitMount` also carried `mb-3`, so the head's rows sat 24px
  apart against the schedule's 14px; `FlexMount` had the same problem with `my-1`
  rules and an `mt-3` heading, giving 16px on one side of a rule and 24px on the
  other. All removed — see the Single-Spacing Rule now in DESIGN.md. Verified: 0
  stray margins across all nine products at both widths.
- **"Also covers" was indented past every other label** by a decorative `Users`
  icon, and set as label-then-value inline while every neighbouring row was
  justified. It is a proper `dt`/`dd` row now, on the mount's own left edge, and a
  long dependant list stacks beneath its label at the same 40-character threshold
  `ScheduleRow` already uses.
- **The disclosure stated one quantity twice** — "Show all 10 benefits" on the left,
  "4 more" on the right. The schedule's own size survives (it is what the button
  reveals and what the insurer's document states); the tail count is derivable and
  was never the point. Expanded now reads "Show fewer" rather than "Show headline
  benefits", which was our vocabulary, not the member's.
- Also: a **lone claimable flex category with no cap of its own is dropped**, using
  the rule `UsageLeaf.FlexBlock` already applied to the same list — a mount titled
  "Flexible benefits" was printing "What you can claim for: Flexible Benefits · No
  separate cap". The two tabs must not disagree about whether that row is worth
  printing.

### Fixed from the code review of phase 10

Twelve findings, all real. The two that would have shipped as behaviour bugs:

- **`defaultKey` was frozen at mount.** `useState(defaultKey)` reads its argument
  once, and `defaultKey` is derived from utilisation, which arrives later — the
  broker preview deliberately does not gate rendering on it. So the uncontrolled
  deck kept whatever the default was at mount (the first product) while the member
  page, always controlled and re-deriving every render, moved to the pending one:
  a divergence in the one component whose whole job is not to diverge. It now
  falls through (`internalKey ?? defaultKey`), which makes both paths the same
  expression. Verified: the preview opens Kamsinah on Hospital, as her own page does.
- **`product_code` is not a unique slide key.** `hydrate_plans` emits a line per
  matched CATEGORY and the product index is keyed on id; a firm-library product and
  a company one can share a code (the unique constraint exempts `client_id IS
  NULL`), and an unlinked category falls back to `"?"`. Two such lines collided on
  the React key, on the `deck-tab-`/`deck-panel-` ids wiring the tablist together,
  and on the `findIndex` resolving a selection — the second line was unreachable.
  Keys are now suffixed on collision. The same slip carries `GHS`/`GHS2` and
  `GMM`/`GMM2`, which share a short label, so a repeated LABEL is disambiguated by
  plan code and then by product name.

**The stage no longer uses `AnimatePresence`.** Chasing the review's "two live
tabpanels" finding surfaced a worse one: under rapid switching — tapping through
nine products, which is exactly what a deck invites — exits stopped replacing one
another and stacked, seven full schedules deep, lingering for seconds. The swap is
unchanged; what changed is who owns removal. `exiting` is now one piece of state
cleared on a known deadline and the entrance is a CSS keyframe that runs on
remount, so *at most one outgoing slide, gone in 200ms* is a property of the code
rather than a hope about a scheduler — and it is how every other animation in this
world is already written. Measured: max 2 panels while tapping all nine, settling
to 1. It also deleted the `forwardRef`/`useIsPresent`/`popLayout` machinery, and
with it the ref-forwarding bug I introduced while trying to keep it.

Also fixed: the outgoing panel carries `inert`/`aria-hidden`/`tabIndex={-1}` on the
element holding `role="tabpanel"` (it was on an inner div, leaving the panel itself
live and tabbable); `aria-controls` is set only on the selected tab, since the
others named ids that are not in the document; `onPointerCancel` no longer commits
a swipe the reader never released; `onPointerDown` ignores non-primary pointers and
non-left mouse buttons; `moveStill` cancels *both* of its frames, so two layout
changes in consecutive frames — the documented mount sequence — cannot re-enable
the pill transition early; `useIsWide` measures synchronously so the deck never
paints the phone rail before flipping to the vertical one; the lone-benefit path
renders its mount **with** the entrance, since outside the deck nothing else owns
it; and `FlexMount`'s comment no longer claims a parity with `UsageLeaf.FlexBlock`
that the two data shapes cannot support.

**`PortalFrame`'s wrapper is `overflow-clip`, not `overflow-hidden`.** Hidden makes
that box a scroll container, which becomes the containing block for any `position:
sticky` inside it — and since it never scrolls, the deck's sticky rail silently did
nothing in the preview while working on the member's page. `clip` clips identically,
radius included, without the side effect. Verified: the rail moves 345px against a
500px scroll instead of 500.

### "What's covered" carries no claim figures (user, 2026-08-01)

The tab is ENTITLEMENT. Every utilisation figure came off the insured mounts — the
product-level `FillRule` ("Still under review · S$303.48", "Nothing claimed yet",
the approved/pending bar) and the per-schedule-row fullness inside `ScheduleLeaf`.
Those answer "what's left", which has its own tab, and interleaving them put a
figure that changes weekly beside one that holds for the year. **`FlexMount` keeps
its ledger** — a flex wallet's allowance and what remains of it *are* its
entitlement, not an account of it.

`BenefitMount` and `ScheduleLeaf` no longer take a `Utilization` at all, so
`CoverageLeaf` no longer takes one either and neither `routes/portal/benefits` nor
`PortalFrame.BenefitsTab` fetches it for this tab. `readSchedule`/`usageFor` keep
their optional usage parameters — the broker's `BenefitScheduleView` and
`CoverageCard` still show both — this surface simply passes none.

Two consequences worth knowing:

- **The deck now opens on the FIRST product, not the one with a claim in flight.**
  Opening on the moving product was defensible while the tab showed claim figures;
  with them gone the reason is invisible, and a page that opens halfway down a list
  for an unstated reason is the same failure as the activity dot the rail used to
  carry. `DeckActivity`, `DeckSlide.activity` and `Deck`'s `defaultKey` were
  deleted rather than left dead.
- **Nothing was lost.** Verified: "What's left" still renders "Group Hospital &
  Surgical · Still under review · S$303.48" with its fill bars, and all nine
  products on "What's covered" now match `/still under review|approved and
  paid|nothing claimed|being assessed/` zero times, with zero fill bars, at 390px
  and desktop.

### "What's left" — itemised pending, and two duplications removed (user, 2026-08-01)

- **"Still under review" now itemises the claims behind it.** The figure IS a sum
  of submitted claims — `utilization.py` adds `amount_converted or amount_claimed`
  for every claim whose status is in `PENDING_STATUSES` (everything except draft,
  rejected and approved) — and a bare "S$303.48" answers nothing on its own. The
  member is the only person who can tell us a receipt is missing from that total,
  so `PendingBreakdown` (`UsageLeaf`) lists each contributing claim: provider,
  date, dependant, amount. It **renders only when the rows reconcile with the
  bucket to the cent** — the total comes from the utilisation service and the rows
  from the claims list, two independent queries that can be a moment apart, and a
  breakdown that does not add up reads as a fault in the number rather than in the
  pairing. `IN_FLIGHT` is spelled out rather than derived by subtraction, because
  a set defined as "everything except" silently grows a member the day a status is
  added server-side, and these two lists have to agree for the reconciliation to
  hold. `UsageLeaf` takes `claims` as a PROP (never fetches), so the member page
  and `PortalFrame` pass their own member-gated queries and stay identical.
- **The "Nothing claimed yet" mount is gone.** A product with no cap, nothing
  claimed and no sub-limits has no fullness to draw and nothing to count down; the
  only thing it could state is that it has no yearly cap, which is a fact about
  the policy and belongs to the other tab. Collapsed into one mount it was still
  eight rows of "No yearly cap" — the largest object on a page about what is left,
  carrying nothing that is left. The empty state now gates on `active`, not on
  `products`, or a member with nine uncapped products and no claims gets a blank
  page.
- **`glossBeside` now measures what a gloss ADDS.** An exact-match test was not
  enough: "Group Hospital & Surgical" glossed "hospital stays and surgery" is a
  different string and the same sentence. A gloss contributing fewer than two
  words the title does not already carry is an echo, not a translation, and is
  dropped. Two, not one, because English always supplies a connective the title
  omits. Words match on a four-character stem (`surgery`/`surgical`,
  `accident`/`accidental`), which is the shortest prefix that does not start
  pairing unrelated words. Verified against every entry in the map: only
  `GHS`/`GHS2` suppress; a code-only heading shares no words at all and so always
  keeps its gloss, which is the case the Printed-Label Rule actually cares about.
- **The flex tile stated what is left twice** — "S$2,680 · LEFT TO CLAIM" as the
  mount's aside and "S$2,680 left" in the fill legend. The legend no longer takes
  `remaining`; it keeps the half the aside does not carry, which is how much of
  the allowance has gone.

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
