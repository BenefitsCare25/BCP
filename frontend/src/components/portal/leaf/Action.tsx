/** Actions on the member surface.
 *
 * Three reasons this module exists rather than loose class strings:
 *
 * 1. **The shared `Button` tops out at h-9 (36px)** and has no touch scale, so
 *    member surfaces were hand-rolling a `leafAction` / `leafPrimaryAction`
 *    class pair in clinics, security and enrollment — three copies, already
 *    drifting. Everything here clears the 44×44 Reach Rule by construction.
 *
 * 2. **One action language, in terracotta — and a neutral tier outside it.**
 *    `quiet` and `primary` are the SAME PILL at two points on one journey: the
 *    quiet one is an outline over a 10% wash, and its hover is exactly what
 *    primary already looks like at rest. That is the hierarchy — primary is the
 *    action that has already arrived.
 *
 *    `neutral` is the third tone and it exists because **not every button is a
 *    call to action.** A filter chip, a disclosure toggle, a Previous/Next
 *    pair: those choose a view, they do not do a thing to the member's record,
 *    and dressing them in terracotta both shouts and inverts the hierarchy —
 *    a row of coloured chips whose SELECTED member is marked in neutral ink
 *    reads with the active one as the quietest. Selection in this world is
 *    always marked the way the nav, dock and tabs mark it: a `bg-shade` pill
 *    with ink text. Reach for `neutral` whenever the control picks something,
 *    and for `quiet` whenever it does something.
 *
 * 3. **A verdict must never look like a button.** The action colour is
 *    deliberately not the brand, which also puts it a whole hue away from
 *    `strike-rejected`; the older defence — treatment alone, since brand red
 *    and rejected red shared a hue family — is now belt as well as braces. An
 *    action is a PILL, a verdict is STRUCK TEXT on the glass.
 *
 * **Why classes and not `<Link>` wrappers.** Wrapping TanStack's `Link` erases
 * its route generics — `search={{ tab: "usage" }}` stops type-checking against
 * the actual route and silently accepts anything. Styling is single-sourced
 * here; routing stays on the real, fully-typed `Link`. */
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

type Tone = "primary" | "quiet" | "neutral";

const BASE =
  "leaf-focus inline-flex items-center justify-center gap-2 rounded-pill " +
  "font-semibold whitespace-nowrap disabled:pointer-events-none disabled:opacity-60 " +
  "transition-[transform,box-shadow,background-color,filter] duration-200 ease-leaf";

/** 44px is the Reach Rule floor, not a target — the primary action sits above
 * it because it is the thing a member came to do.
 *
 * **The border is on BOTH tones and never changes colour**, which is what makes
 * the hover read as one pill filling rather than as two different components.
 * On `primary` it also survives as a darker rim around the fill, the detail
 * that keeps a solid pill from looking like a flat rectangle of colour.
 *
 * `text-action-ink`, never `text-action`: the fill value measures 3.60:1 as a
 * label on its own wash. That is the same trap the token pair is named for. */
const TONE: Record<Tone, string> = {
  primary:
    "h-12 px-6 text-md text-action-foreground shadow-cta " +
    "bg-action border border-action-ink " +
    "hover:-translate-y-px hover:shadow-cta-hover hover:brightness-95 " +
    "active:translate-y-0 active:brightness-90",
  quiet:
    "h-11 px-5 text-row text-action-ink bg-action-wash border border-action-ink " +
    "hover:bg-action hover:text-action-foreground hover:shadow-cta " +
    "active:scale-[0.99]",
  neutral:
    "h-11 px-5 text-row text-record bg-glass border border-glass-edge shadow-mount " +
    "hover:bg-glass-hover hover:shadow-mount-hover active:scale-[0.99]",
};

/** `block: true` is full width everywhere; `block: "phone"` is full width on a
 * phone and natural width from `sm` up.
 *
 * The distinction is load-bearing rather than cosmetic: `flex` makes the
 * element a BLOCK-level flex container, so `w-auto` still fills its line —
 * returning to the desktop width needs `inline-flex` back, not `w-auto`. A
 * brand pill stretched across a 1180px column reads as a banner, not a button. */
export function actionClass(
  tone: Tone = "quiet",
  opts?: { block?: boolean | "phone"; className?: string },
) {
  return cn(
    BASE,
    TONE[tone],
    opts?.block && "flex w-full",
    opts?.block === "phone" && "sm:inline-flex sm:w-auto",
    opts?.className,
  );
}

export function Action({
  tone = "quiet",
  block,
  className,
  children,
  ...rest
}: {
  tone?: Tone;
  block?: boolean | "phone";
  className?: string;
  children: ReactNode;
} & Omit<ComponentProps<"button">, "className" | "children">) {
  return (
    <button className={actionClass(tone, { block, className })} {...rest}>
      {children}
    </button>
  );
}

/** The "See all limits →" tier. Put this on a real `<Link>` and give it
 * `<GoArrow />` as its last child.
 *
 * `stretch` is the stretched-link pattern: the anchor grows an invisible
 * overlay across its nearest positioned ancestor, so a whole tile is tappable
 * through ONE anchor. The alternative — wrapping the tile in a `<Link>` and
 * leaving this one inside it — nests anchors, which is invalid HTML and hands
 * screen-reader users two targets for one destination. Anything else
 * interactive inside a stretched tile needs `relative z-10` to stay clickable. */
export function goLinkClass(opts?: {
  brand?: boolean;
  stretch?: boolean;
  className?: string;
}) {
  return cn(
    "leaf-focus group inline-flex min-h-11 items-center gap-1.5 text-row font-semibold",
    // `--color-action-ink`, never `--color-action`: the fill measures 4.23:1
    // and misses AA as text. `brand` is kept as the PROP name — it means "give
    // this link the action colour", and renaming it would touch six call sites
    // for no behavioural gain.
    opts?.brand ? "text-action-ink" : "text-record",
    opts?.stretch && "after:absolute after:inset-0 after:content-['']",
    opts?.className,
  );
}

/** `aria-hidden` because it is emphasis, not information — the link text
 * already says where it goes. */
export function GoArrow({ brand = false }: { brand?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "transition-transform duration-200 ease-leaf group-hover:translate-x-1",
        brand ? "text-action-ink" : "text-label",
      )}
    >
      →
    </span>
  );
}
