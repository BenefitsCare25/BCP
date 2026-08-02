/** The strike — the leaf's memorable moment, and the answer to the question
 * members actually come back for.
 *
 * A state is STRUCK onto its mount: real weight, its own full-strength ink, and
 * a rule that draws itself beneath the word. It is deliberately not a chip. The
 * incumbent soft-badge pattern (coloured text on a coloured wash) is banned
 * system-wide — all four of its variants measured between 2.86:1 and 4.24:1 at
 * 12px and every one failed WCAG 1.4.3. Every ink below is a full-strength text
 * colour clearing 5:1 on both the leaf and the mount, so this construction
 * retires that whole class of defect rather than re-tuning it.
 *
 * **Why this is the one thing that animates.** Motion is spent here and almost
 * nowhere else. A verdict is rare, consequential and arrives asynchronously —
 * the three conditions under which motion carries information rather than
 * decorating. By contrast the mounts themselves do NOT animate in: a member
 * opens this page constantly, and an entrance on every card is the uniform
 * page-load fade that delays content and tells them nothing. Restraint
 * elsewhere is what leaves this legible.
 *
 * The rule draws left-to-right because that is the direction a stamp is pulled;
 * the word does not move, so nothing reflows and no text is animated while
 * being read.
 *
 * Member-safe vocabulary only: the AI pipeline's internal states all collapse
 * to "Under review", and fraud signals never reach a member. This is now the
 * ONLY place that vocabulary lives — the old ClaimStatusBadge was deleted when
 * its last consumer moved here. */
import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/cn";

type StrikeTone = "approved" | "pending" | "review" | "rejected";

const TONE_CLASS: Record<StrikeTone, string> = {
  approved: "text-strike-approved",
  pending: "text-strike-pending",
  review: "text-strike-review",
  rejected: "text-strike-rejected",
};

/** Which group of the member's ledger a claim files under. */
export type ClaimBucket = "attention" | "review" | "approved" | "closed";

const CLAIM_STATE: Record<
  string,
  { label: string; tone: StrikeTone; bucket: ClaimBucket }
> = {
  draft: { label: "Not sent", tone: "review", bucket: "attention" },
  submitted: { label: "Under review", tone: "review", bucket: "review" },
  ai_review_pending: { label: "Under review", tone: "review", bucket: "review" },
  ai_verified: { label: "Under review", tone: "review", bucket: "review" },
  ai_flagged: { label: "Under review", tone: "review", bucket: "review" },
  needs_info: { label: "More info needed", tone: "pending", bucket: "attention" },
  approved: { label: "Approved", tone: "approved", bucket: "approved" },
  rejected: { label: "Rejected", tone: "rejected", bucket: "closed" },
};

// A settled state this map hasn't been taught yet. The claim state machine can
// grow one (`models/claim.py` has no cancel-like status today), and the failure
// mode is asymmetric: filing a withdrawn claim under "In Review" tells a member
// we are still working on something that is finished. Matched by PREFIX rather
// than by a list of exact spellings — a guessed literal set ("cancelled",
// "canceled", "withdrawn") is precisely what a real `withdrawn_by_member` or
// `cancelled_by_broker` would slip past.
const SETTLED_PREFIX = /^(cancel|withdraw|void|clos)/;

/** The ledger group for a claim status.
 *
 * Lives HERE because this module already owns the member-facing claim
 * vocabulary (see the header) — the ledger kept a second, hand-synced status
 * map, so a status added to one was silently absent from the other. */
export function claimBucket(status: string): ClaimBucket {
  const known = CLAIM_STATE[status];
  if (known) return known.bucket;
  return SETTLED_PREFIX.test(status) ? "closed" : "review";
}

export function Strike({
  children,
  tone = "review",
  className,
  animate = false,
}: {
  children: React.ReactNode;
  tone?: StrikeTone;
  className?: string;
  /**
   * Draw the rule rather than rendering it complete. Opt-IN, and deliberately
   * off by default: a claims list mounts twenty of these at once, and twenty
   * rules drawing simultaneously is the uniform page-load flourish this world
   * refuses everywhere else. Turn it on where a single verdict is the point of
   * the screen — the claim detail page — not where states are being scanned.
   */
  animate?: boolean;
}) {
  const reduced = useReducedMotion();

  return (
    <span
      className={cn(
        "relative inline-flex items-center gap-1.5 whitespace-nowrap pb-[3px]",
        "text-2xs font-bold uppercase leading-4 tracking-[0.085em]",
        TONE_CLASS[tone],
        className,
      )}
    >
      {children}
      {/* The rule is its own element rather than a border so it can be drawn.
          `aria-hidden` because the state is already carried by the text — this
          is emphasis, not information, and must never be announced twice. */}
      <motion.span
        aria-hidden
        className="absolute inset-x-0 bottom-0 h-[2px] origin-left bg-current"
        initial={animate && !reduced ? { scaleX: 0 } : false}
        animate={{ scaleX: 1 }}
        transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1] }}
      />
    </span>
  );
}

/** A claim's state, struck. Unknown states fall through to the raw value in the
 * quietest ink rather than being dropped — a member seeing an unfamiliar word
 * is recoverable; a member seeing no state at all is not. */
export function ClaimStrike({ status }: { status: string }) {
  const cfg = CLAIM_STATE[status] ?? { label: status, tone: "review" as const };
  return <Strike tone={cfg.tone}>{cfg.label}</Strike>;
}
