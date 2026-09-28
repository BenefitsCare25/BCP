/** A period's rules in one scannable block — the answer to "what did we set
 * this period up to do?" without opening the form. */
import type { EnrollmentWindow } from "@/api/enrollment";
import { SectionLabel } from "@/components/ui/section-label";
import { DEFAULT_BEHAVIOR_TEXT, fmtWhen } from "./periodMeta";

export function PeriodSummary({ window: w }: { window: EnrollmentWindow }) {
  const changes = [
    w.allow_plan_change && "plan",
    w.allow_dependant_changes && "dependants",
    w.allow_leave && "leave",
  ].filter(Boolean) as string[];
  const rows: Array<[string, string]> = [
    ["Members can start", fmtWhen(w.opens_at)],
    ["Deadline", fmtWhen(w.closes_at)],
    [
      "Who chooses",
      w.member_self_service ? "Members, in the portal" : "Brokers only (hidden from the portal)",
    ],
    ["Members can change", changes.length ? changes.join(", ") : "nothing"],
    [
      "Products",
      w.product_scope?.length ? w.product_scope.join(", ") : "All products",
    ],
    ["If they do nothing", `They ${DEFAULT_BEHAVIOR_TEXT[w.default_behavior]}`],
    [
      "Flex wallet",
      w.uses_flex
        ? `${w.flex_drawdown_rule === "on_change" ? "Charged only the difference" : "Charged the full plan price"}${w.allow_overdraft ? ", overdraft allowed" : ", no overdraft"}`
        : "Not used",
    ],
  ];
  return (
    <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
      {rows.map(([term, value]) => (
        <div key={term} className="min-w-0">
          <SectionLabel as="dt">{term}</SectionLabel>
          <dd className="mt-0.5 text-sm text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
