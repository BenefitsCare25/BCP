import type { BenefitLimit, BenefitSchedule, BenefitSubItem } from "@/types";
import { copayFieldLabel } from "@/lib/sob";

// Structured outpatient qualifiers (per-visit / co-payment / per-policy-year
// and their site variants) stored in item.properties by the copay editor and
// the slip parser. Other property keys (maximum_days, surgical_schedule) are
// already mirrored into `limits`, so only these render from properties.
const COPAY_PROP_PREFIXES = [
  "per_visit",
  "co_payment",
  "per_policy_year",
  "per_disability",
];

function copayProps(
  properties: Record<string, string> | undefined,
): [string, string][] {
  return Object.entries(properties ?? {}).filter(([k]) =>
    COPAY_PROP_PREFIXES.some((p) => k.startsWith(p)),
  );
}

// Read-only Schedule of Benefits renderer. Shared by the broker employee detail
// view and the employee-facing benefits portal, so an employee sees the exact
// limits stored on their matched plan — value, footnote, and qualifier rows.

function formatValue(value: string | null | undefined): string | null {
  if (value == null || value === "") return null;
  const v = value.trim();
  // Plain integer amounts render as currency; everything else (ward text,
  // "As per disability", percentages, day caps) renders verbatim.
  if (/^\d{1,3}(,\d{3})*$/.test(v) || /^\d+$/.test(v)) {
    return `$${Number(v.replace(/,/g, "")).toLocaleString()}`;
  }
  return v;
}

function Limits({ limits }: { limits?: BenefitLimit[] }) {
  if (!limits || limits.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5">
      {limits.map((lim, i) => (
        <span key={i} className="text-[11px] text-muted-foreground">
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
  note,
  limits,
  indent,
}: {
  label: string;
  value: string | null | undefined;
  note?: string | null;
  limits?: BenefitLimit[];
  indent?: boolean;
}) {
  const formatted = formatValue(value);
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
          <span
            className={indent ? "text-muted-foreground" : "text-foreground/90"}
          >
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
      {note && (
        <div className="text-[11px] italic text-muted-foreground">{note}</div>
      )}
      <Limits limits={limits} />
    </div>
  );
}

interface Props {
  schedule: BenefitSchedule | null | undefined;
  annualPolicyLimit?: string | null;
  coverDescription?: string | null;
}

export function BenefitScheduleView({
  schedule,
  annualPolicyLimit,
  coverDescription,
}: Props) {
  const items = schedule?.items ?? [];
  if (items.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No schedule of benefits recorded for this plan.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      {coverDescription && (
        <p className="text-[11px] text-muted-foreground">{coverDescription}</p>
      )}
      {annualPolicyLimit && (
        <div className="text-xs font-medium text-foreground">
          Annual Limit: {formatValue(annualPolicyLimit) ?? annualPolicyLimit}
        </div>
      )}
      {items.map((item, idx) => (
        <div key={`${item.number}-${idx}`} className="flex flex-col gap-0.5">
          <Row
            label={`${item.number ? `${item.number}. ` : ""}${item.name}`}
            value={item.value}
            note={item.note}
            limits={item.limits}
          />
          {copayProps(item.properties).map(([key, value]) => (
            <Row key={key} indent label={copayFieldLabel(key)} value={value} />
          ))}
          {item.sub_items
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
                note={sub.note}
                limits={sub.limits}
              />
            ))}
        </div>
      ))}
    </div>
  );
}
