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

// `properties` is an open bag: outpatient copay fields (per_visit / co_payment
// / per_policy_year and their site variants), the dental Panel/Non-Panel axis,
// and the slip parser's qualifier keys below.
//
// Only these four are ALSO written into `limits` by the parser
// (placement_slip_sob.py::_SOB_PROPERTY_PATTERNS), so they are the only ones
// that must be suppressed here to avoid rendering twice. Everything else is
// shown.
//
// This is deliberately a deny-list. It used to be an allow-list of copay
// prefixes, which silently dropped every dental Panel/Non-Panel value and would
// drop any future qualifier preset whose key didn't start with a listed prefix.
const MIRRORED_INTO_LIMITS = new Set([
  "maximum_days",
  "qualification_period",
  "co_insurance",
  "surgical_schedule",
]);

function displayProps(
  properties: Record<string, string> | undefined,
): [string, string][] {
  return Object.entries(properties ?? {}).filter(
    ([k, v]) => !MIRRORED_INTO_LIMITS.has(k) && String(v ?? "").trim() !== "",
  );
}

// Read-only Schedule of Benefits renderer. Shared by the broker employee detail
// view and the employee-facing benefits portal.
//
// A member sees the schedule of every product they're matched to, which for a
// fully-covered employee is ~230 rows. So the headline limits render first and
// the long tail (reference lines, condition lists, rows with no value) collapses
// behind a disclosure — nothing is removed, only demoted.

// How many valued rows to show before collapsing the rest.
const SUMMARY_ROWS = 6;

function formatValue(
  value: string | null | undefined,
  kind?: BenefitKind,
): string | null {
  if (value == null || value === "") return null;
  const v = value.trim();
  const numeric = /^\d{1,3}(,\d{3})*$/.test(v) || /^\d+$/.test(v);
  const asCurrency = () =>
    numeric ? `$${Number(v.replace(/,/g, "")).toLocaleString()}` : v;

  // A kind that states the type outright wins over the digits.
  if (kind === "percent") return v.endsWith("%") ? v : `${v}%`;
  if (kind === "days") return /\bdays?\b/i.test(v) ? v : `${v} days`;
  if (kind === "text" || kind === "boolean" || kind === "list" || kind === "scale") {
    return v;
  }
  if (kind === "currency") return asCurrency();

  // `amount` is the DEFAULT kind, not a broker's choice — `sob_columns` stamps
  // it on any row nobody typed a type for, so it carries no more information
  // than no kind at all. Both fall back to the numeric heuristic, which keeps
  // the 400 stored `amount` rows (all of GBT) rendering as currency.
  //
  // This is why a genuine non-money count still needs its kind set explicitly
  // (the row detail panel has a per-item and per-sub-item picker): "Number of
  // Visits: 6" is only distinguishable from "$6" by someone saying so.
  return asCurrency();
}

// `benefit_schedule` is an untyped JSON column server-side (`dict[str, Any]`),
// so `BenefitItem` describes what writers SHOULD produce, not what every stored
// row actually has — seeded and hand-PATCHed schedules routinely omit
// `sub_items` entirely. Read it defensively: a missing key must render an empty
// section, never crash the member's benefits page.
function subItemsOf(item: BenefitItem): BenefitSubItem[] {
  return Array.isArray(item.sub_items) ? item.sub_items : [];
}

/** A row worth rendering: it says something beyond its own name. */
function hasContent(item: BenefitItem): boolean {
  return Boolean(
    (item.value != null && item.value !== "") ||
      item.note ||
      (item.limits && item.limits.length > 0) ||
      subItemsOf(item).length > 0 ||
      displayProps(item.properties).length > 0,
  );
}

/** Enumerations (covered conditions, compensation scales) are reference, not limits. */
function isEnumeration(item: BenefitItem): boolean {
  return (
    (item.kind === "list" || item.kind === "scale") && subItemsOf(item).length > 0
  );
}

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
  coverDescription?: string | null;
  /** Claim usage per lowercased benefit name, when the surface has it. */
  usageByBenefit?: Map<string, UtilizationBucket>;
  /** The product-level roll-up bucket (benefit_key = null). */
  productUsage?: UtilizationBucket | null;
}

export function BenefitScheduleView({
  schedule,
  annualPolicyLimit,
  coverDescription,
  usageByBenefit,
  productUsage,
}: Props) {
  const [showAll, setShowAll] = useState(false);

  // Rows that say nothing beyond their own name were still rendered as bare
  // labels — a dental plan whose values never got filled in showed nine of them.
  // If that empties the schedule outright the plan genuinely has names but no
  // values, so keep the names: they still tell the member what is covered.
  const all = schedule?.items ?? [];
  const withContent = all.filter(hasContent);
  const items = withContent.length > 0 ? withContent : all;
  const valuesMissing = withContent.length === 0 && all.length > 0;

  if (items.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No schedule of benefits recorded for this plan.
      </p>
    );
  }

  // Headline = the non-enumeration rows, but any row the member has CLAIMED
  // against is always promoted into it. Taking a flat first-N by document order
  // could bury the one balance they opened the page to check behind "View full
  // schedule". Document order is preserved among the chosen rows.
  const candidates = items.filter((i) => !isEnumeration(i));
  const used = (i: BenefitItem) => {
    const b = usageByBenefit?.get((i.name ?? "").trim().toLowerCase());
    return Boolean(b && (b.approved > 0 || b.pending > 0));
  };
  const usedRows = candidates.filter(used);
  const filler = candidates
    .filter((i) => !used(i))
    .slice(0, Math.max(0, SUMMARY_ROWS - usedRows.length));
  const picked = new Set<BenefitItem>([...usedRows, ...filler]);
  const headline = candidates.filter((i) => picked.has(i));
  const collapsible = items.length > headline.length;
  const shown = showAll || !collapsible ? items : headline;

  return (
    <div className="flex flex-col gap-1.5">
      {coverDescription && (
        <p className="text-2xs text-muted-foreground">{coverDescription}</p>
      )}
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
          usage={usageByBenefit?.get((item.name ?? "").trim().toLowerCase())}
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
