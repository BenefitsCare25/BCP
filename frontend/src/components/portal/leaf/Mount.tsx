/** The mount — the portal's one structural device.
 *
 * A mount is a pane of glass laid on the ground: softly rounded, thin enough
 * that the ground's colour reads through it, lifted by a shadow and separated
 * by the lit highlight along its top edge. Depth here is LIT, not printed —
 * a mount that needs to feel above another gets elevation, not an ink frame.
 *
 * Ground and glass sit only 1.06:1 apart in luminance, so `shadow-mount` and
 * `border-glass-edge` are structural rather than decorative. Dropping either
 * makes the tile disappear into the page.
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
 *   `bg-glass`            the translucent fill — the ground reads through it
 *   `leaf-specular`       the lit top face (leaf.css; a background layer, not
 *                         an overlay — see the note there)
 *   `backdrop-saturate`   the blur alone GREYS what it frosts, because
 *                         averaging a coloured field toward its surroundings
 *                         drains chroma. Without this the blooms arrive under
 *                         the pane as pale neutral and the pane reads white
 *                         again, which defeats the fill above it.
 *   `shadow-mount`        the resting elevation, carrying the 1px inset rim */
export const glassSurface =
  "bg-glass leaf-specular backdrop-blur-glass backdrop-saturate-150 " +
  "border border-glass-edge shadow-mount";

/** Two hover responses, and the difference between them is a promise.
 *
 * `glassResting` — every mount gets this: the pane THINS (62% → 40%, see
 * `--color-glass-hover`) so the ground's colour rises through it, and its
 * shadow deepens. It says the surface is live. It deliberately does NOT move,
 * because the mount may not be a target.
 *
 * The direction is the gesture and it is easy to get backwards. Brightening the
 * pane toward white — which is what this did first — is a fill changing shade,
 * the response a button makes; the pane whitens, loses the ground it was
 * frosting, and stops reading as glass at the exact moment it is being touched.
 * Clearing it is the response a pane of glass makes: it becomes more glass.
 *
 * `glassInteractive` — only for a mount that actually navigates or expands. The
 * lift is the affordance, so a mount that lifts and then does nothing is a
 * promise the surface breaks. */
export const glassResting =
  "transition-[transform,box-shadow,background-color] duration-200 ease-leaf " +
  "hover:bg-glass-hover hover:shadow-mount-hover";

export const glassInteractive =
  glassResting +
  " hover:-translate-y-[3px] active:-translate-y-px active:scale-[0.996]";

export function Mount({
  label,
  gloss,
  aside,
  children,
  className,
  as: Tag = "section",
  labelId,
  interactive = false,
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
  /** Give the mount the hover lift. Set this only when the whole mount is a
   * target. */
  interactive?: boolean;
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
        // `leaf-rise` on every mount, staggered by sibling position in CSS so
        // no component has to thread an index. It runs once, for half a second.
        rise && "leaf-rise",
        "flex flex-col gap-3 rounded-tile p-4 sm:p-5",
        interactive ? glassInteractive : glassResting,
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
