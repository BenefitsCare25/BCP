import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type {
  BenefitItem,
  BenefitKind,
  BenefitLimit,
  BenefitSchedule,
  BenefitSubItem,
  UtilizationBucket,
} from "@/types";
import { propertyLabel } from "@/lib/sob";
import {
  displayProps,
  formatValue,
  isEnumeration,
  readSchedule,
  subItemsOf,
  usageFor,
} from "@/lib/benefitSchedule";

// Read-only Schedule of Benefits renderer for the BROKER surfaces. The reading
// logic (which rows carry content, which are reference, how values format,
// which rows earn the headline) lives in `lib/benefitSchedule` and is shared
// with the member portal's own renderer, so the two can never disagree about
// what a schedule says — only about how it looks.
//
// A member sees the schedule of every product they're matched to, which for a
// fully-covered employee is ~230 rows. So the headline limits render first and
// the long tail (reference lines, condition lists, rows with no value) collapses
// behind a disclosure — nothing is removed, only demoted.

function money(n: number): string {
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

/**
 * What is left on this benefit. Pending is shown SEPARATELY and never
 * subtracted (mirrors utilization.py) — a member must not read an in-flight
 * claim as already spent.
 */
function UsagePill({ bucket }: { bucket: UtilizationBucket }) {
  if (bucket.approved <= 0 && bucket.pending <= 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-2 text-2xs">
      {bucket.remaining != null ? (
        <span className="font-medium text-foreground/80">
          {money(bucket.remaining)} left
        </span>
      ) : (
        <span className="text-muted-foreground">{money(bucket.approved)} claimed</span>
      )}
      {bucket.remaining != null && bucket.approved > 0 && (
        <span className="text-muted-foreground">
          of {money(bucket.approved + bucket.remaining)}
        </span>
      )}
      {bucket.pending > 0 && (
        <span className="text-warn">{money(bucket.pending)} pending</span>
      )}
    </div>
  );
}

function Limits({ limits }: { limits?: BenefitLimit[] }) {
  if (!limits || limits.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5">
      {limits.map((lim, i) => (
        <span key={i} className="text-2xs text-muted-foreground">
          {lim.label}
          {lim.value ? `: ${lim.value}` : ""}
        </span>
      ))}
    </div>
  );
}

function Row({
  label,
  value,
  kind,
  note,
  limits,
  indent,
}: {
  label: string;
  value: string | null | undefined;
  kind?: BenefitKind;
  note?: string | null;
  limits?: BenefitLimit[];
  indent?: boolean;
}) {
  const formatted = formatValue(value, kind);
  // A value that reads as a sentence (a benefit description rather than an
  // amount/limit) is rendered full-width below the label. Squeezing it into the
  // right-hand value column otherwise forces the label to wrap one word per line
  // and overflows the card, because the amount column is intentionally `shrink-0`.
  const longForm = formatted != null && formatted.length > 45;
  return (
    <div className={indent ? "pl-4" : ""}>
      {longForm ? (
        <div className="flex flex-col gap-0.5 text-xs">
          <span className={indent ? "text-muted-foreground" : "text-foreground"}>
            {label}
          </span>
          <span className={indent ? "text-muted-foreground" : "text-foreground/90"}>
            {formatted}
          </span>
        </div>
      ) : (
        <div className="flex justify-between gap-3 text-xs">
          <span
            className={`min-w-0 break-words ${indent ? "text-muted-foreground" : "text-foreground"}`}
          >
            {label}
          </span>
          {formatted && (
            <span
              className={
                indent
                  ? "shrink-0 text-right text-muted-foreground"
                  : "shrink-0 text-right font-medium text-foreground"
              }
            >
              {formatted}
            </span>
          )}
        </div>
      )}
      {note && <div className="text-2xs italic text-muted-foreground">{note}</div>}
      <Limits limits={limits} />
    </div>
  );
}

function ItemBlock({
  item,
  usage,
}: {
  item: BenefitItem;
  usage?: UtilizationBucket;
}) {
  const [open, setOpen] = useState(false);
  const subs = subItemsOf(item);
  const label = `${item.number ? `${item.number}. ` : ""}${item.name ?? ""}`;

  // A 30-entry covered-condition list is reference material, not a limit —
  // collapse it to one line so it stops burying the rows that are.
  if (isEnumeration(item)) {
    return (
      <div className="flex flex-col gap-0.5">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex items-center gap-1 text-left text-xs text-foreground hover:underline"
        >
          {open ? (
            <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 break-words">{label}</span>
          <span className="shrink-0 text-muted-foreground">
            ({subs.length})
          </span>
        </button>
        {item.note && (
          <div className="pl-4 text-2xs italic text-muted-foreground">
            {item.note}
          </div>
        )}
        {open &&
          subs.map((sub, i) => (
            <Row
              key={i}
              indent
              label={`${sub.key ? `${sub.key} ` : ""}${sub.name}`}
              value={sub.value}
              kind={sub.kind}
              note={sub.note}
              limits={sub.limits}
            />
          ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5">
      <Row
        label={label}
        value={item.value}
        kind={item.kind}
        note={item.note}
        limits={item.limits}
      />
      {usage && <UsagePill bucket={usage} />}
      {displayProps(item.properties).map(([key, value]) => (
        <Row key={key} indent label={propertyLabel(key)} value={value} />
      ))}
      {subs
        .filter(
          (s: BenefitSubItem) =>
            s.value || s.note || (s.limits && s.limits.length > 0),
        )
        .map((sub: BenefitSubItem, i) => (
          <Row
            key={i}
            indent
            label={`${sub.key ? `${sub.key} ` : ""}${sub.name}`}
            value={sub.value}
            kind={sub.kind}
            note={sub.note}
            limits={sub.limits}
          />
        ))}
    </div>
  );
}

interface Props {
  schedule: BenefitSchedule | null | undefined;
  annualPolicyLimit?: string | null;
  /** Claim usage per lowercased benefit name, when the surface has it. */
  usageByBenefit?: Map<string, UtilizationBucket>;
  /** The product-level roll-up bucket (benefit_key = null). */
  productUsage?: UtilizationBucket | null;
}

export function BenefitScheduleView({
  schedule,
  annualPolicyLimit,
  usageByBenefit,
  productUsage,
}: Props) {
  const [showAll, setShowAll] = useState(false);

  const { items, headline, collapsible, valuesMissing } = readSchedule(
    schedule?.items,
    usageByBenefit,
  );

  if (items.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No schedule of benefits recorded for this plan.
      </p>
    );
  }

  const shown = showAll || !collapsible ? items : headline;

  return (
    <div className="flex flex-col gap-1.5">
      {annualPolicyLimit && (
        <div className="text-xs font-medium text-foreground">
          Annual Limit: {formatValue(annualPolicyLimit) ?? annualPolicyLimit}
        </div>
      )}
      {valuesMissing && (
        <p className="text-2xs italic text-muted-foreground">
          Covered benefits are listed below; limits for this plan are not yet
          recorded.
        </p>
      )}
      {productUsage && <UsagePill bucket={productUsage} />}
      {shown.map((item, idx) => (
        <ItemBlock
          key={`${item.number}-${idx}`}
          item={item}
          usage={usageFor(item, usageByBenefit)}
        />
      ))}
      {collapsible && (
        <button
          type="button"
          onClick={() => setShowAll((s) => !s)}
          aria-expanded={showAll}
          className="mt-1 flex items-center gap-1 self-start text-2xs font-medium text-muted-foreground hover:text-foreground hover:underline"
        >
          {showAll ? (
            <>
              <ChevronDown className="size-3" /> Show headline benefits only
            </>
          ) : (
            <>
              <ChevronRight className="size-3" /> View full schedule of benefits (
              {items.length - shown.length} more)
            </>
          )}
        </button>
      )}
    </div>
  );
}
