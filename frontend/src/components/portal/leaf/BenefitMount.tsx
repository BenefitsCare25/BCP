/** One insured product, as a mount on the member's leaf.
 *
 * Everything here is something the member HOLDS. Matching internals
 * (method / confidence / rule text) and per-member premium are already nulled
 * server-side by `member_statement.py`, and the slip's own category wording
 * ("Managers (Option 1)") is deliberately not rendered either — it is the
 * broker's filing vocabulary, not an answer to "am I covered for this?".
 *
 * The mount never appears for a product the member does not hold: the leaf
 * shows only what was issued.
 *
 * **It carries no claim figures.** What has been claimed against this product,
 * what is still under review and what is left are the "What's left" tab's
 * question; here the answer is what the policy entitles the member to. The two
 * were interleaved — a product's balance printed between its description and
 * its schedule — which made an entitlement page read as a statement of account.
 * Flexible benefits is the one exception, and it is not one in spirit: a flex
 * wallet's allowance and what remains of it ARE its entitlement, so `FlexMount`
 * keeps its ledger. */
import { useId } from "react";
import type { CoverageLine } from "@/types";
import { Mount, MountRule } from "./Mount";
import { Money } from "./Figure";
import { ScheduleLeaf } from "./ScheduleLeaf";
import { glossBeside } from "./glossary";

function dependantLabel(d: {
  name: string | null;
  relationship: string | null;
}): string {
  if (d.name && d.relationship) return `${d.name} (${d.relationship})`;
  return d.name ?? d.relationship ?? "Dependant";
}

/** The slip writes its description as a labelled field — "Cover: Reimbursement
 * of eligible inpatient expenses…". Under the product's own title the label is
 * furniture: the member is not choosing between fields, they are reading what
 * this product does. */
function describedCover(text: string | null | undefined): string | null {
  const trimmed = text?.trim();
  if (!trimmed) return null;
  return trimmed.replace(/^cover\s*[:\-–—]\s*/i, "").trim() || null;
}

/** A value long enough to read as a sentence goes full width beneath its label;
 * squeezed into the right-hand column it forces the label to wrap one word per
 * line. Same threshold `ScheduleRow` uses, so a mount's own rows and its
 * schedule's rows break at the same point. */
const LONG_VALUE = 40;

export function BenefitMount({
  line,
  rise = true,
}: {
  line: CoverageLine;
  /** Off inside a coverage-deck slide, whose own transition owns the arrival. */
  rise?: boolean;
}) {
  const titleId = useId();

  // **One description, in one place.** The mount used to print two: our
  // plain-language gloss under the title, and the slip's own cover description
  // further down inside the schedule. On a product whose NAME already says it
  // ("Group Hospital & Surgical" / "hospital stays and surgery" / "Cover:
  // Reimbursement of eligible inpatient expenses…") the member read the same
  // fact three times before reaching a single figure.
  //
  // The slip's description wins when there is one: it comes from the policy, it
  // is specific to this plan, and the gloss is a generic line written per
  // product CODE. The gloss stays as the fallback, which is what it was for.
  const described = describedCover(line.cover_description);
  const gloss =
    described ??
    glossBeside(
      line.product_name ?? line.product_code,
      line.product_code,
      line.product_name,
    );
  const covered = line.covers_dependants ? line.covered_dependants : [];
  const coveredText = covered.map(dependantLabel).join(", ");

  return (
    <Mount
      as="article"
      rise={rise}
      labelId={titleId}
      label={
        <>
          {line.product_name ?? line.product_code}
          {line.plan_code && (
            <>
              {" "}
              <span className="ml-1 whitespace-nowrap font-normal text-label">
                Plan {line.plan_code}
              </span>
            </>
          )}
        </>
      }
      gloss={gloss}
    >
      {/* **No margins on any of these blocks.** `Mount` is a flex column with
          `gap-3`; the `mb-3` these each carried added to that gap rather than
          replacing it, so every row in the mount's head sat 24px from its
          neighbour while the schedule's rows below sat at 14px. Spacing here is
          the layout's job, in one place. */}

      {/* Every row in this head is the SAME shape — printed term on the left at
          the mount's own margin, value on the right — so they read as one
          column of terms and one of values. "Also covers" used to lead with an
          icon, which indented its label past every other label in the mount for
          no information: the term already says what it is. */}
      {(line.financials?.sum_insured != null || covered.length > 0) && (
        <dl className="flex flex-col gap-2">
          {/* Amount covered is present only where the surface is allowed it —
              `financials` is nulled for members by design. Rendered when it
              survives so the broker preview and the member stay identical. */}
          {line.financials?.sum_insured != null && (
            <div className="flex items-baseline justify-between gap-4">
              <dt className="text-row text-label">Amount you're covered for</dt>
              <dd className="m-0 shrink-0 text-right">
                <Money value={line.financials.sum_insured} emphasis="strong" />
              </dd>
            </div>
          )}
          {covered.length > 0 &&
            (coveredText.length > LONG_VALUE ? (
              <div className="flex flex-col gap-0.5">
                <dt className="text-row text-label">Also covers</dt>
                <dd className="m-0 text-row text-record">{coveredText}</dd>
              </div>
            ) : (
              <div className="flex items-baseline justify-between gap-4">
                <dt className="text-row text-label">Also covers</dt>
                <dd className="m-0 shrink-0 text-right text-row text-record">
                  {coveredText}
                </dd>
              </div>
            ))}
        </dl>
      )}

      <MountRule />

      <ScheduleLeaf
        schedule={line.benefit_schedule}
        annualPolicyLimit={line.annual_policy_limit}
        titleId={titleId}
      />
    </Mount>
  );
}
