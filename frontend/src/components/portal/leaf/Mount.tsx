/** The mount — the portal's one structural device.
 *
 * A mount is a pane of glass laid on the ground: softly rounded, nearly solid,
 * warm-rimmed, lifted by a tinted glow. Depth here is LIT, not printed — a
 * mount that needs to feel above another gets elevation, not an ink frame.
 *
 * `shadow-mount` and `border-glass-edge` are STRUCTURAL, not decorative, and
 * that survived the move to a solid fill for a different reason than before.
 * They used to be the only separation available, because pane and ground were
 * 1.06:1 apart. Now the fill genuinely differs — but only where the ground is
 * bloomed, and the middle of a long page is flat near-white. On those screens
 * the rim and the glow are again the whole boundary, so dropping either makes
 * the mount disappear on exactly the pages nobody checks.
 *
 * Every value here is a token (`bg-glass`, `rounded-tile`, `shadow-mount`,
 * `text-row`…). No raw hex, no `rounded-[Npx]`, no `text-[Npx]` — see
 * DESIGN.md's "Do reach for a token". */
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** The glass surface itself, without the mount's header furniture. Exported so
 * one-off surfaces (a notice strip, a sheet) are made of the same material
 * instead of re-deriving it and drifting.
 *
 * Four parts, and each is load-bearing:
 *   `bg-glass`            the fill — 0.68, so the ground reads THROUGH a pane
 *                         as well as around it (see `--color-glass`)
 *   `leaf-specular`       the lit top face and the body gradient (leaf.css;
 *                         background layers, not an overlay — see the note)
 *   `backdrop-blur/-saturate`
 *                         the frosting, at the reference design's 32px/180%
 *   `border-glass-edge` + `shadow-mount`
 *                         the warm rim, and an elevation that is now entirely
 *                         INSET — a mount casts no drop shadow
 *
 * **THE BACKDROP FILTER WAS REMOVED ONCE AND IS BACK; the thing that changed is
 * the fill, not the filter.** At 0.95 the blur had nothing to frost — 5% of a
 * backdrop — and its only visible contribution was an artifact: Chrome renders
 * a large-radius backdrop blur in downscaled, low-precision passes, and over
 * this nearly flat ground that quantises into grey MOTTLING, which read as a
 * dirty card and was reported as one. At 0.68 the backdrop is a third of what a
 * member sees and the blur is doing real work.
 *
 * **THE MOTTLING NEEDED TWO INGREDIENTS, AND ONLY ONE OF THEM WAS THE BLUR.**
 * The isolation that convicted the blur — clean with the filter off, clean with
 * saturate alone, mottled with blur alone — was run while the ground still
 * carried its sixth wash, a LOGO GREY at 0.12 (the strongest and the only
 * neutral in the stack; see the bloom notes in leaf.css, where it was removed
 * for reading as a shadow). A downscaled blur quantising a grey wash over a
 * warm field is what produced grey patches. With that bloom gone the blur runs
 * over five warm washes and bands invisibly — verified at 0.68 resting and 0.45
 * hovered, on the home tiles and on the full-width coverage slide.
 *
 * So the rule is not "no blur here". It is: **a backdrop blur is only as clean
 * as the field behind it.** Re-introduce a neutral or a high-alpha wash to the
 * ground and this will mottle again. If it does, cut the radius (8–14px bands
 * far less) before cutting the effect. */
export const glassSurface =
  "bg-glass leaf-specular backdrop-blur-glass backdrop-saturate-180 " +
  "border border-glass-edge shadow-mount";

/** THE ONE HOVER RESPONSE IN THE PORTAL: **the pane rises 3px and thins.**
 *
 * Applied to every card and card-like control, so nothing in the portal points
 * at you differently depending on which page you are on.
 *
 * **The LIFT is what a member actually sees, and on this ground it is the only
 * part that can be.** The thinning is real — measured live at 0.68 → 0.45 — but
 * white over `#FAFAF9` barely moves: composited over the ground beneath a home
 * tile the two states are (254,252,251) and (253,249,249), a difference of 1
 * and 3 and 3. That is roughly a 1% shift and it is invisible, which is exactly
 * how it was reported. The reference design's identical gesture reads because
 * its ground is a saturated peach and thinning swings through real colour; ours
 * is near-white, so no opacity value can carry a hover here. **If the ground is
 * ever warmed, the thinning comes alive on its own** — which is why it is kept
 * rather than deleted, and why it clears unevenly (a fixed body gradient in
 * `.leaf-specular` grades the change across the pane's height; the arithmetic
 * is there, and `background-image` cannot tween, so it has to work this way).
 *
 * TWO THINGS THE LIFT DEPENDS ON, both easy to get wrong:
 *
 *   1. **It must use `translate`, NEVER `transform`.** Every mount carries
 *      `leaf-rise`, an animation with `animation-fill-mode: both` whose `to`
 *      state is `transform: none`. Animation styles beat declarative ones, so a
 *      `transform`-based lift on a mount is simply ignored — it would appear to
 *      work on the few surfaces without an entrance and nowhere else. Tailwind
 *      v4's `-translate-y-*` compiles to the separate `translate` property,
 *      which composes with the animation instead of losing to it.
 *   2. **`translate` must be in the transition list.** `transition-transform`
 *      in v4 covers `transform, translate, scale, rotate`, but a hand-written
 *      `transition-[transform,…]` does NOT cover `translate` — which is what
 *      the previous version of this had, so its lift SNAPPED rather than
 *      animating. `scale` is listed too, for the pill controls that press.
 *
 * The shadow deliberately does NOT move. A growing shadow pools ~33px below a
 * full-width deck slide and reads as a dark mass blooming out of nowhere; the
 * lift alone carries the elevation.
 *
 * **This exists because four different responses had accumulated on one
 * material** — nothing on every reading card, fill+shadow+a 3px lift on five
 * home tiles, fill+shadow on the pagers and the neutral pill, and a hand-rolled
 * fill-only on the MFA notice. Read as a whole portal that is one card style
 * behaving four ways depending on which page you were on, which is exactly how
 * it was reported. Two rules keep it collapsed:
 *
 *   1. **`glassSurface` is STATIC, and a mount casts NO DROP SHADOW at all.**
 *      `--shadow-mount` is two inset layers now; there was a `-hover` twin that
 *      deepened them, and it is deleted. Both were reported: a shadow growing
 *      under a full-width deck slide THAT DOES NOT MOVE reads as a dark mass
 *      blooming out of nowhere, and the resting pair read as a smudge hugging
 *      every corner. This ground has no headroom for ink painted OUTSIDE a
 *      pane — see the note on `--shadow-mount` in leaf.css.
 *   2. **Nothing translates.** A lift is a promise that clicking does
 *      something, and it was on exactly five of the portal's several dozen
 *      panes. Removing it is what makes every card behave identically whether
 *      or not it happens to be a link — the pointer cursor from the stretched
 *      anchor is what says "this is a target", not the geometry.
 *
 * WHERE IT GOES: every card — `Mount`, the home `Tile`, the two notice strips —
 * and the card-like controls, the deck's step buttons and the neutral pill.
 *
 * WHERE IT DOES NOT: a pane whose CHILDREN respond — the deck rail, the tab and
 * filter strips, the claim and clinic lists, the dock. Their chips and rows have
 * their own hover, and thinning the container underneath them would fire two
 * responses for one pointer. */
export const glassHover =
  "leaf-lift transition-[background-color,translate,scale] duration-200 ease-leaf " +
  "hover:bg-glass-hover hover:-translate-y-[3px]";

export function Mount({
  label,
  gloss,
  aside,
  children,
  className,
  as: Tag = "section",
  labelId,
  rise = true,
}: {
  /** The mount's name, set as a Title. Sentence case, not the uppercase tier:
   * an insurer product name run through `text-transform: uppercase` is 40-odd
   * characters of tracked capitals, which is the least readable thing on the
   * page and would make an eyebrow of every heading. The uppercase tier is for
   * printed furniture labels ("Left to claim"), never for a heading. */
  label?: ReactNode;
  /** The plain-language line that must accompany any code or insurer wording
   * in `label` (The Printed-Label Rule). */
  gloss?: ReactNode;
  /** Right-hand furniture: a strike, a figure, a control. */
  aside?: ReactNode;
  children?: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "li";
  labelId?: string;
  /* There is no `interactive` prop. It used to switch the hover lift on, no
     caller in the portal ever passed it, and the lift is gone — every card
     responds the same way now. See `glassHover`. */
  /** The entrance, on by default.
   *
   * Turn it OFF only where something else already owns this mount's arrival —
   * that is any DECK slide (coverage and enrollment both), whose own
   * directional transition would otherwise run underneath a second 500ms
   * rise-and-fade of the same element. Two entrances on one object do not
   * compose; they read as a stutter.
   *
   * Concretely, when both run: the opacities MULTIPLY (the deck's 80ms delay
   * holds the wrapper at 0 while the mount inside is already fading in), the
   * travel goes diagonal (32px X from the wrapper, 12px Y from here), and the
   * two finish 80ms apart — so the tail of the transition is a lone vertical
   * settle after the horizontal has landed. The enrollment deck shipped this
   * way while the coverage deck did not, which is exactly how it was found. */
  rise?: boolean;
}) {
  return (
    <Tag
      className={cn(
        glassSurface,
        glassHover,
        // `leaf-rise` on every mount, staggered by sibling position in CSS so
        // no component has to thread an index. It runs once, for half a second.
        rise && "leaf-rise",
        "flex flex-col gap-3 rounded-tile p-4 sm:p-5",
        className,
      )}
    >
      {(label || aside) && (
        <div className="flex items-start justify-between gap-3">
          {label && (
            <div className="min-w-0">
              <h2
                id={labelId}
                className="text-md font-semibold leading-5 text-record"
              >
                {label}
              </h2>
              {gloss && (
                <p className="mt-1 text-row text-label">{gloss}</p>
              )}
            </div>
          )}
          {aside && <div className="shrink-0">{aside}</div>}
        </div>
      )}
      {children}
    </Tag>
  );
}

/** A hairline that genuinely separates rows inside a mount rather than
 * decorating them. */
export function MountRule({ className }: { className?: string }) {
  return (
    <hr className={cn("border-0 border-t border-hairline/75", className)} />
  );
}

/** A labelled row inside a mount: printed term on the left, its value set as a
 * figure on the right. `dt`/`dd` so the pairing is in the accessibility tree
 * and not merely visual (WCAG 1.3.1). */
export function MountRow({
  term,
  gloss,
  children,
}: {
  term: ReactNode;
  gloss?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <div className="min-w-0">
        <dt className="text-row text-record">{term}</dt>
        {gloss && <dd className="mt-0.5 text-row text-label">{gloss}</dd>}
      </div>
      <dd className="shrink-0 text-right text-row text-record">{children}</dd>
    </div>
  );
}
