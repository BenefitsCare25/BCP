---
name: Inspro
description: A group-benefits platform whose member portal is an issued record, not a dashboard.
---

# Design System: Inspro

## Overview

**Creative North Star: "Daylight"**

The member portal is a record *issued to* one person — not a dashboard reporting
on them. That thesis has not changed. What changed is the material it is made
of.

The portal used to be printed paper: a hairline frame on a matte ground, flat,
square-cornered, depth drawn rather than lit. It is now **daylight**. The ground
is a light, warm surface with colour moving across it, and every unit of content
is a **pane of glass laid on that ground** — softly rounded, thin enough that the
colour beneath shows through it, lifted by a shadow rather than outlined by an
ink rule. Depth here is *lit*, not printed. That is the single sentence that
reverses the most previous rules, so it is stated first.

The device the world runs on is still the **mount**: one framed unit that carries
its own label and shows its state by how full it is. A mount is a benefit, and
fullness is **utilisation** — never absence of entitlement. The portal shows only
what the member actually holds, so a mount can never advertise cover they cannot
obtain. A mount that reads empty means an unused limit, not a missing benefit.
This remains the most load-bearing rule in the system and the one most likely to
be got wrong.

Colour is inverted from the category habit. The chrome is quiet and the member's
own figures and states carry the saturation. The brand appears exactly ONCE on
any screen — the mark — and it never tints a surface, a control, a status or a
chart; what a member touches is a separate terracotta. This is not restraint
for its own sake: the product requires that a verdict (approved / pending /
flagged) is never confusable with either the brand or an action, and giving
identity, touch and status three different hues is what makes that enforceable.

**Naming note for anyone reading the code.** The CSS class is `.leaf`, the
stylesheet is `frontend/src/styles/leaf.css` and the primitives live in
`components/portal/leaf/`. Those names are a **technical namespace**, retained so
the scoping mechanism and its behavioural twin (`lib/leaf-scope.ts`) keep working;
they are not the name of the world. Do not rename them to chase this document.

Confirmed anti-references: the benefits dashboard (product-card grid, utilisation
doughnut, trustworthy teal, "Welcome back!"); the black-on-white editorial-serif
wellness look; and the cream-ground / high-contrast-serif / terracotta-accent
combination that generative tools ship whenever handed the word "document".

**Key Characteristics:**
- Issued, not reported: the record belongs to the member
- Every frame carries its own printed label — no term appears unglossed
- Fullness means usage, never entitlement
- Quiet chrome, saturated data; the brand is identity, terracotta is touch
- Tabular figures everywhere a number appears
- Phone-shaped by construction, widened for desktop

## Colors

A near-white ground with colour moving through it, translucent glass over it,
two inks, a brand red that is identity only, a terracotta that is everything
touchable, and a functional strike ramp. Defined in
`frontend/src/styles/leaf.css` and applied only under the `.leaf` root, so the
broker app's token set is untouched.

**Every ratio below was computed, not estimated — and computed against two
hardest cases, not one.** A translucent surface has no single colour; measuring
against the nominal one is how a design like this ships an inaccessible tier.
The two cases are the **hovered pane over the hottest ground** (`#FBF0EF`) for
anything sitting on a mount, and the **bare ground** (`#F8E6E5`) for the things
that stand on the page with no pane beneath them.

### Ground and glass
- **Ground** `#FAFAF9`: the page. A near-white with the barest warmth, so the
  colour on the page comes from the blooms rather than from the base.
- **Shade** `#E9E6DF`: the soft neutral fill — the active nav pill, the current
  tab, a selected chip, the benefit-year chip, an icon's hover. **A separate
  token from the ground, and the split is load-bearing.** These borrowed the
  ground's value while the ground was a warm grey; pointing them at `#FAFAF9`
  would leave the active navigation item at 1.02:1 on the white bar and the
  current page would stop being marked. It is also *darker* than the ground's
  old value: a selected fill has to be a step against every surface it can sit
  on, and there are four — the bar (1.25:1), a pane (1.23:1), the ground
  (1.19:1) and a bloomed pane (1.16:1).
- **Ground blooms**: six large radial washes on the ground, five of them in the
  red family — rose at 17% top-left and 11% upper-right, brand red at 5%
  mid-left, deep brand at 5% mid-right, blush at 7% along the bottom, and the
  logo grey at 12% lower-left as the neutral anchor. **These are the mechanism,
  not decoration.** Over a flat ground, translucent white renders as nothing
  more than a paler flat colour; the blooms are what the glass has to frost.
  Remove them, or cap them so low they cannot be seen, and every mount reads as
  an opaque white box — which is exactly what shipped when they were held at
  9–13%.
  - The top two are **viewport-scaled**, the rest **document-scaled**. Coverage
    runs to nine screenfuls: a bloom whose vertical radius is a percentage of
    *that* has no visible falloff and the first screen arrives as one flat
    sheet, while one sized in `vh` alone is spent before the second screenful
    and abandons every mount below it.
  - Bloom 1 is a **rose, not the brand fill**. Red is dark; the brand value at
    an alpha high enough to see spends the whole luminance budget.
- **Glass** `rgba(255,255,255,.62)` over a 26px backdrop blur **and a 150%
  backdrop saturate**. The saturate is not a flourish: blurring a coloured field
  averages it toward its surroundings and drains its chroma, so without it the
  bloom arrives under the pane as pale neutral and the pane reads white again.
  Composited to `#FDFDFC` over plain ground and `#FCF5F5` at its hottest.
- **Glass edge** `rgba(255,255,255,.92)` with an inset top highlight, plus a
  **specular** — a white→transparent gradient over the pane's top 38%, the soft
  fall of light below the rim. It is a *background layer*, never an overlay: an
  absolutely-positioned pseudo-element wins the paint order against in-flow
  text and would wash out the heading it sits over.
- **The ground carries the contrast, not the glass.** The blooms are sized so
  the bare ground's own worst point clears both 4.5:1 for Label ink and 3:1 for
  control edges. Compositing white over it can only raise luminance, so no
  glass opacity — resting, hover, or any future state — can drop a pane below
  the envelope. That is what frees hover to thin the glass as far as it likes.
- **Bar white** `#FFFFFF`: the top bar and any floating chrome. Solid, so the
  mark has an uncomplicated field.
- **Hairline** `#DFDCD3`: bar borders and in-mount rules. Decorative, exempt from
  1.4.11, never used for text.
- **Track** `#DEDAD0`: the unfilled part of a fill rule.

### Inks
- **Record Ink** `#17160F` — 16.21:1 on the hardest glass, 15.01:1 on the
  hardest ground. Body copy and every figure.
- **Label Ink** `#5C584D` — 6.35:1 on the hardest glass, 5.87:1 on the hardest
  ground. Printed labels and glosses. **This is the lightest text tier in the
  portal. There is nothing below it, and an opacity modifier on it is a contrast
  bug, not a shade.**
- **Control Edge** `#7D776B` — 3.97:1 on the hardest glass, **3.68:1 on the
  hardest ground**, 4.45:1 on the white bar. Form-control and selector borders,
  which *are* covered by WCAG 1.4.11. The ground figure is the one that binds:
  the benefit-year control sits in the heading row with no pane under it, so it
  is what the blooms are sized against. The broker's `--color-input` measures
  under 3:1 here; the portal needs its own, which is the same trap the broker
  token pair already documents, reappearing because the ground changed.

### Brand
- **Brand Fill** `#D21B21` — sampled directly from the logo mark. White on it
  measures **5.36:1**. The brand is now the **identity only**: the mark, and the
  red running through the ground blooms. It paints no control.
- **Brand Ink** `#B8161D` — 5.30:1 on the hardest glass, for the rare moment the
  brand must be *text*. **Two tokens, split by role. Never use the fill value as
  text.**

### Action
A terracotta, and **deliberately not the brand**. Red is what the portal *is*;
terracotta is what a member *touches*. Separating them buys back what the Twice
Rule was rationing — the brand is free to appear on the mark and in the ground
with no control competing with it — and it puts a whole hue between an action
and a rejected-claim verdict, which is the failure a red action language always
risked.

- **Action Fill** `#B45636` — filled pills, and the hover state of an outline
  pill. White on it measures **4.86:1**.
- **Action Ink** `#92482D` — labels and borders. **4.84:1** on its own wash over
  the hottest surface a member can see.
- **Action Wash** the fill at **10%**, left translucent so it composites over
  glass, ground or bar alike rather than assuming one page colour.

**The source values do not ship as-is.** Sampled from the reference design, the
pill was a `#C15F3C` fill with `#C15F3C` text on a 10% wash: white on that fill
is 4.23:1 and the label on that wash is 3.60:1 — both under AA at these sizes.
The fill is darkened until white clears and the ink darkened further until it
clears on the wash. They read as the same terracotta.

**Everything interactive takes it** — buttons, the focus ring, the "See all
limits →" tier, and `--color-primary`/`--color-ring` for any shared primitive
rendering inside `.leaf`. A surface with terracotta buttons and a red focus ring
reads as a bug, so there is no partial adoption.

### Strike ramp
Full-strength text inks, hue- and value-distinct from the brand, measured on the
hardest glass: **approved** `#1C6B3F` (5.82:1), **pending** `#8A5A06` (5.29:1),
**under review** `#46433D` (8.83:1), **rejected** `#7E1F1A` (8.97:1).

Review is graphite on purpose — it is the most common state and deserves the
quietest ink. Rejected is oxblood, deliberately far darker and less saturated
than the brand fill, because brand-red and rejected-red now share a hue family.

### Named Rules

**The Twice Rule, now a Once Rule.** The brand appears at most **once** on a
screen: the mark. It was two — the mark and the primary action — until actions
moved to terracotta, and the rule tightened rather than relaxed because the
budget it was rationing has been spent somewhere better. The active navigation
item is still marked in **ink, not colour**, for the same reason it always was.

**The Do-vs-Pick Rule.** Not every button is a call to action. A control that
**does** something to the member's record — call the clinic, add a dependant,
send the elections — is a terracotta pill. A control that **picks** a view — a
filter chip, a disclosure toggle, a Previous/Next pair — is neutral, and its
selected member is marked the way the nav, dock and tabs mark theirs: a `shade`
pill with ink text. Dressing pickers in terracotta both shouts and inverts the
hierarchy, leaving a row of coloured chips whose *selected* one reads quietest.

**The Status-Is-Not-Brand Rule.** Claim and approval states draw from the
functional ramp. A member must never have to decide whether a coloured thing is
branding or a verdict. Enforced three ways now: the action colour is a different
hue from the brand *and* from `strike-rejected`, and **an action is a pill; a
verdict is struck text on the glass. Never the reverse.**

**The Ink-Over-Tint Rule.** State is a struck mark in its own ink, not tinted
text on a tinted fill. The incumbent soft-badge pattern is banned outright: all
four of its variants measured between 2.86:1 and 4.24:1 and every one failed AA.

**The Composited-Measurement Rule.** Any ink placed on glass is measured against
the composite, at its darkest realistic ground. A value that passes on `#FBFAF9`
and fails on `#EEE9E9` has not been measured.

## Typography

**Display / Body / Label Font:** Archivo Variable, self-hosted via
`@fontsource-variable/archivo` (weight axis only), with a system grotesque
fallback stack.
**Figures:** tabular lining numerals, set on the `.leaf` root via
`font-variant-numeric` rather than per component — a figure that opts out is a
bug, and opting in per site guarantees some site forgets.

**Character:** a workhorse grotesque with an administrative temperament — the
register of something issued to you rather than marketed at you. Deliberately not
Inter as a display face, and none of the Fraunces / Playfair / Space Grotesk /
Plus Jakarta family of generative defaults.

### Hierarchy
- **Display**: the one monumental figure per screen, 46px on a phone and 62px on
  desktop, weight 700, tracking −0.036em. Used **once**. Its currency mark is set
  at 0.44em so it reads as a unit rather than as a second number.
- **Headline**: the page heading — whose record this is.
- **Title**: a mount's name, always paired with its plain-language gloss.
- **Body**: prose and schedule rows. 65–75ch.
- **Label**: one uppercase tier app-wide, 11px / 0.085em. Hierarchy comes from
  rules and spacing, never from a second size step in the same treatment.

### Named Rules

**The Printed-Label Rule.** No product code, benefit key, tier code or family
status appears without its plain-language gloss beside it in the same frame.
`GCGP` is never shown alone; it is shown as `GCGP — everyday clinic visits`. A
term whose gloss will not fit is a term that needs a shorter gloss, not a tooltip.

**The Tabular-Figure Rule.** Every figure that can be compared to another is set
in tabular lining numerals and right-aligned in its column. Money is never
abbreviated on a member surface: `S$2,700`, never `S$2.7K`.

## Layout

**The home is a mosaic.** `/portal` is the first destination and answers the four
questions the member actually arrives with — what's left, what happened to my
claim, what am I covered for, where do I go — as tiles sized to their answers.
Tiles span one or two columns; **hierarchy comes from span and figure scale**,
which is what carries it now that there is no dark tile to carry it.

**One row of chrome.** The desktop top bar is a single row: mark, pill
navigation, benefit-year selector, account icons. No primary action in it — an
action inside a wayfinding row has nothing to belong to. The **primary action
lives in the page**: at the head of the content on desktop, a full-width pill on
a phone, so both viewports place it identically.

**The dock.** Below `sm`, navigation is a floating glass pill fixed to the
bottom, carrying all five destinations at once. Nothing a member may need on a
deadline is ever reachable only by horizontal scroll, and there is no overflow
menu.

**The benefit-year selector is a scope control**, not a caption — it changes what
the whole page shows. It sits with the account controls, never stacked under the
member's name. With one year on file it renders as plain text with no affordance;
with several it opens a menu; with a past year selected it turns pending-ink and
the page carries a notice that claims cannot be submitted against a closed year.

### Named Rules

**The Whole-Frame Rule.** A mount reflows by whole frames, never fractions. There
is no half-width form field on a phone.

**The Reach Rule.** Every interactive element is at minimum 44×44 CSS px on touch
and no smaller than 24×24 anywhere. Form controls render at 16px minimum on touch
viewports, because anything smaller makes iOS Safari zoom the page and break the
column.

**The Gutter Rule.** Everything in the top bar lands on one gutter. The mark, the
navigation and the account cluster share it, so the bar reads as a system rather
than as objects that happen to be near each other.

## Elevation & Depth

**Lit, not printed.** This reverses the previous system outright. Separation
comes from radius, a soft shadow, and the lit top edge of the glass — not from an
ink frame. Ground and glass sit only 1.06:1 apart in luminance, so the shadow and
the edge highlight are load-bearing, not ornament.

Three elevations, no more: the **ground** (no shadow), the **mount** (resting
shadow), and the **floating chrome** — the top bar and the dock — which carries a
deeper shadow and a full-opacity inner highlight.

## Shapes

Softly rounded. `--radius-tile` 22px, `--radius-control` 12px, and full pills
(999px) for navigation items, the dock, and every button. The previous system's
"rectilinear, no pill shapes, no full-round containers" rule is **retired** — it
described the printed world and is false here.

**The Perimeter Rule survives.** Frames are full perimeters. Directional accent
borders — a coloured `border-left` as a highlight rail — remain banned
system-wide, inherited from the project's standing house rules.

**No nested cards.** A tile may contain rows, rules and figures. It may not
contain another tile.

## Motion

Motion is spent on four things, and each is tied to feedback or to data:

1. **The surface answering the pointer**, in two grades, and the difference
   between them is a promise. *Every* mount **thins its glass** (62% → 40%) and
   deepens its shadow on hover — it says the surface is live. Only a mount that
   actually navigates or expands additionally **lifts 3px** and settles on press:
   the lift is the affordance, so a mount that lifts and then does nothing is a
   promise the surface breaks.

   **The direction is the gesture.** Brightening the pane toward white is a fill
   changing shade — the response a button makes — and it whitens away the very
   ground the pane was frosting, so the surface stops reading as glass at the
   moment it is touched. Clearing it is what a pane of glass does: it becomes
   more glass, and the colour beneath comes up through it.
2. **The fill drawing its value.** A fill rule grows from zero on load, the
   pending hatch following the approved fill rather than racing it. The motion
   *is* the datum — it encodes how much of the limit is used.
3. **The entrance.** Every mount rises 12px once, staggered by sibling position
   (50ms steps, capped at the sixth). It is applied by `Mount` and staggered in
   CSS via `:nth-of-type`, so no component threads an index. The cap matters:
   past the sixth, delay stops being rhythm and starts withholding content from
   someone already scrolling.
4. **The strike.** A verdict's rule draws itself beneath the word — claim detail
   only, `animate` opt-in, so a list of twenty verdicts never fires at once.

No infinite loops. Nothing animates on a keyboard-triggered or high-frequency
action. `prefers-reduced-motion` resolves every one of these to its **end state** —
never a blanket kill, and never an element left mid-transition and invisible.

## Do's and Don'ts

### Do:
- **Do** treat fullness as utilisation. A partly-filled mount means a partly-used
  limit.
- **Do** print the plain-language label beside every code, in the frame.
- **Do** express state as a struck mark with its own ink.
- **Do** set every comparable figure in tabular lining numerals, unabbreviated.
- **Do** design the column at phone width first, then widen it.
- **Do** measure any ink on glass against the composite at its darkest.
- **Do** reach for a token. Radii, shadows, easings, type steps and every colour
  are CSS variables in `leaf.css`; a raw hex, a `rounded-[18px]` or a
  `text-[13px]` in a component is a bug.

### Don't:
- **Don't** render an empty mount for a benefit the member does not have.
- **Don't** let the brand appear more than once on a screen, or paint a control with it.
- **Don't** use a fill value as text — that is what the `-ink` half of each pair is for.
- **Don't** dress a filter, toggle or pager in the action colour; those pick, they don't do.
- **Don't** put a primary action inside the navigation row.
- **Don't** ship coloured text on a coloured wash as a status badge.
- **Don't** abbreviate money on a member surface.
- **Don't** put help behind hover *alone*. Hover is the desktop interaction and
  the hint is tuned for it, but a phone has no hover state, so the same panel
  must also open on tap. Behind hover only, the content is unreachable to the
  portal's primary audience.
- **Don't** let help expand the page. The hint FLOATS over the content; an
  inline panel re-flows the row it opens in, which on the claim form's header
  visibly jumped the controls beside it.
- **Don't** remove the ground blooms, or quieten them until they cannot be seen.
  Without something worth frosting the glass is just paler paint.
- **Don't** make hover brighten a pane. Hover *thins* it — see Motion.
- **Don't** stack the benefit period under the member's name; it is a control.
- **Don't** show broker vocabulary, remediation instructions or pipeline
  diagnostics on a member surface.
- **Don't** add a second uppercase label tier. One tier, app-wide.
