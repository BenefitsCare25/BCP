/** What actually changes if a member picks this plan.
 *
 * This is the half of the decision the page used to omit. "Less cover — adds
 * back S$82.84" says a switch is cheaper without saying what is given up, and
 * the member had no way to find out: the coverage tab renders only the plan
 * they hold today, never the one they are considering.
 *
 * It is a DIFF, not a schedule. Rendering each option's full schedule would
 * reproduce the coverage tab three times inside a form, and the set that
 * matters is small — across CDL's book it is 1 row for GCGP, 2 for GMM, 4 for
 * GCSP, 9 for GD and zero for every life product, whose plans share one
 * schedule and differ only in a sum insured the row above already states.
 *
 * ## The layout, and the two things it fixes
 *
 * **1. Every item is separated by a rule.** The first version set the name,
 * the qualifier and the values in one uniform grey, spaced by 8px — the same
 * gap a wrapped line already leaves. With insurer names running to four wrapped
 * lines there was no way to tell where one changed benefit ended and the next
 * began; it read as one paragraph. Separation here is structural, not
 * decorative, so it gets a real rule.
 *
 * **2. Each item is three ranked parts, not one string.** The parent benefit
 * is set quietly, the specific benefit in full ink, and the insurer's bracketed
 * wording drops to a gloss beneath. The server splits them (`group` /
 * `benefit` / `qualifier`) rather than joining them into
 * "Specialist Care — Panel Specialists (on cashless basis) (including…)",
 * which is what made a scannable list impossible.
 *
 * **No row numbers**, deliberately, and this is the one that looks like an
 * omission: an index would help scanning. But `leaf/ScheduleLeaf` drops the
 * slip's numbering on the member surface on purpose — it is a position in the
 * insurer's document, not a fact about the benefit — and inventing our OWN
 * 1..n index would collide with the values, which cross-reference the slip's
 * numbering verbatim ("Refer to 1a"). A member matching our "3." against the
 * schedule's "1a" would be chasing two different indexes. The rule and the ink
 * carry the structure instead.
 *
 * **Inline, not behind a disclosure.** A disclosure is the right device for the
 * 69-row schedule on the coverage tab, where the member is browsing. Here they
 * are deciding, the list is a handful of rows, and putting the single most
 * decision-relevant fact one tap away on the page whose entire purpose is that
 * decision would be hiding the answer. */
import type { BenefitDifference } from "@/api/enrollment";
import { formatValue } from "@/lib/benefitSchedule";
import { BENEFIT_KINDS, type BenefitKind } from "@/types";
import { cn } from "@/lib/cn";

/** The label/value pair beneath each changed benefit.
 *
 * **The VALUE flexes and the LABEL is rigid**, which is the opposite of every
 * other figure row in the portal and is deliberate. Elsewhere the value is
 * money — short and fixed — so it may sit `shrink-0` against the right margin
 * while the term wraps. A schedule cell is not money: it can be a whole
 * sentence ("Up to 150% of Schedule of Benefits for Benefit 1 to 5"), and as
 * `shrink-0` one of those blew a 390px viewport out to 498px. The plan label
 * is the predictable side, so it is the one that holds its width.
 *
 * `flex-wrap` is the last resort: if the two genuinely cannot share a line,
 * the value drops to its own rather than overflowing. */
const pairRow =
  "flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-0.5";
const pairLabel = "shrink-0 text-row text-label";
const pairValue = "min-w-0 flex-1 text-right text-row";

// Derived from the union's own runtime list, never hand-listed here: a copy
// would compile even after a new kind was added and would silently stop
// formatting it.
const KINDS = new Set<string>(BENEFIT_KINDS);

/** The server sends `kind` as a plain string (it is untyped JSON on the way
 * in); only pass through the ones the formatter knows, so an unrecognised kind
 * formats as a bare value rather than being trusted. */
function asKind(kind: string | null): BenefitKind | undefined {
  return kind && KINDS.has(kind) ? (kind as BenefitKind) : undefined;
}

/** A schedule cell as the member reads it.
 *
 * A null or blank cell is "Not covered", never an empty space: on a row headed
 * "what changes", a blank is indistinguishable from a value that failed to
 * load, and the difference between "this plan drops the benefit" and "we don't
 * know" is the whole point of the row. `formatValue` handles the rest, so
 * these figures are written exactly as the coverage tab writes them.
 */
function cell(value: string | null, kind: string | null): string {
  return formatValue(value, asKind(kind), "S$") ?? "Not covered";
}

export function TierDifferences({
  differences,
  total,
  currentLabel,
  electedLabel,
  settled = false,
}: {
  differences: BenefitDifference[];
  /** Count before truncation — equal to `differences.length` in every real
   * case, but a silently shortened cover comparison is exactly the kind of
   * omission this component exists to prevent. */
  total: number;
  /** The baseline tier's name — the "before" side. */
  currentLabel?: string | null;
  /** This tier's name — the "after" side. */
  electedLabel?: string | null;
  /** The change has already been made (a confirmed enrollment, or the
   * broker's read-only preview). "If you switch" is a future tense that is
   * simply untrue there — the elected plan IS the member's plan now, and the
   * baseline is what they held before. */
  settled?: boolean;
}) {
  if (!differences.length) return null;
  const hidden = Math.max(0, total - differences.length);

  return (
    <div className="mt-2.5 border-t border-hairline/75 pt-2.5">
      <h4 className="leaf-label">What changes</h4>
      <p className="text-row text-label">
        {currentLabel
          ? `compared with ${currentLabel}, your current plan`
          : "compared with your current plan"}
      </p>

      {/* The rule between items is the thing that makes this a list rather
          than a paragraph — see the note above. */}
      <dl className="mt-2 divide-y divide-hairline/75 border-t border-hairline/75">
        {/* The qualifier is part of the KEY, not just of the display. Row
            identity server-side is the raw benefit name, brackets and all, so
            one payload legitimately carries "Panel Specialists (on cashless
            basis)" and "(on reimbursement basis)" as two entries — and
            `_split_qualifier` moves the brackets into `qualifier`, leaving
            group+benefit identical. Keyed on those two alone React saw
            duplicates and mis-reconciled or dropped one of the pair, which on
            this list means a benefit change silently vanishing. */}
        {differences.map((d) => (
          <div
            key={`${d.group ?? ""}|${d.benefit}|${d.qualifier ?? ""}`}
            className="py-2.5"
          >
            <dt>
              {d.group && (
                <span className="block text-row text-label">{d.group}</span>
              )}
              <span className="block text-row font-medium text-record">
                {d.benefit}
              </span>
              {d.qualifier && (
                <span className="mt-0.5 block text-row text-label">
                  {d.qualifier}
                </span>
              )}
            </dt>
            {/* Both sides NAMED, not just arranged.
                An arrow between two bare values ("As charged → S$3,000") left
                the reader to infer which end was the plan they hold, and the
                weight difference alone did not carry it — this is the one row
                on the page where getting the direction backwards means reading
                a downgrade as an upgrade. Each value now sits against the plan
                it belongs to, in the same term/value shape the rest of the
                portal uses, and the current one is marked "now". */}
            <dd className="mt-1.5">
              <div className={pairRow}>
                <span className={pairLabel}>
                  {currentLabel
                    ? `${currentLabel} — ${settled ? "before" : "now"}`
                    : settled
                      ? "Your plan before"
                      : "Your plan now"}
                </span>
                <span className={cn(pairValue, "text-label")}>
                  {cell(d.current, d.kind)}
                </span>
              </div>
              <div className={pairRow}>
                <span className={pairLabel}>
                  {electedLabel
                    ? `${electedLabel} — ${settled ? "now" : "if you switch"}`
                    : settled
                      ? "Your plan now"
                      : "If you switch"}
                </span>
                {/* Full ink and weight: this is the outcome being offered. */}
                <span className={cn(pairValue, "font-semibold text-record")}>
                  {cell(d.elected, d.kind)}
                </span>
              </div>
            </dd>
          </div>
        ))}
      </dl>

      {hidden > 0 && (
        <p className="mt-2 text-row text-label">
          and {hidden} more {hidden === 1 ? "benefit" : "benefits"} — ask your
          HR team for the full schedule.
        </p>
      )}
    </div>
  );
}
