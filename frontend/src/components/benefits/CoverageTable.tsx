/** The broker's coverage list: one row per product, expandable to its schedule.
 *
 * Replaces `CoverageCard`. A fully-covered CDL employee holds eight to eleven
 * products, and one card per product — each carrying its own header, badges,
 * match rationale, financial strip and six-row schedule — ran this pane to
 * 5,600px. The information a broker scans for (which plan, how much cover, what
 * premium, what's been claimed) was never in the same place twice, so nothing
 * could be compared across products without scrolling between them.
 *
 * A table puts those four answers in columns and everything else behind a row
 * expander. It also absorbs the retired "Claims utilization" section: the
 * per-product buckets that section listed ARE these rows (see `usage.tsx`).
 */
import { Fragment, useMemo, useState } from "react";
import { ChevronRight, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { SectionLabel } from "@/components/ui/section-label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { BenefitScheduleView } from "@/components/configuration/BenefitScheduleView";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/cn";
import type { CoverageLine, UtilizationBucket, Utilization } from "@/types";
import { ClaimPosition, indexUsage, type ProductUsage } from "./usage";

const METHOD_LABEL: Record<string, string> = {
  exact_name: "Exact match",
  fuzzy_name: "Fuzzy match",
  rule: "Rule match",
  manual_override: "Manual override",
};

/**
 * How a match is flagged in the LIST.
 *
 * Only two methods earn a mark. `fuzzy_name` is a name-similarity guess and is
 * the thing a broker auditing coverage is looking for; `manual_override` means
 * a person decided, which is worth knowing before you "correct" it. Exact and
 * rule matches are clean, and marking them put a lozenge on every row of the
 * table — on CDL's roster six of eight rows are fuzzy, so a warn badge for each
 * one stopped being a signal and became the background. Every method, with its
 * confidence, is still stated in the row's own detail panel.
 */
function matchNote(
  method: string | null,
  confidence: number | null,
): { text: string; warn: boolean } | null {
  if (method === "manual_override") {
    return { text: "Manual override", warn: false };
  }
  if (method !== "fuzzy_name") return null;
  // A fuzzy match that scored 1.0 is a name that matched exactly once
  // normalized — there is nothing uncertain about it, and marking it amber
  // beside a genuine 67% teaches a broker to ignore the colour.
  if (confidence != null && confidence >= 1) return null;
  const pct = confidence != null ? ` ${Math.round(confidence * 100)}%` : "";
  return { text: `Fuzzy${pct}`, warn: true };
}

function depLabel(d: { name: string | null; relationship: string | null }): string {
  if (d.name && d.relationship) return `${d.name} (${d.relationship})`;
  return d.name ?? d.relationship ?? "Dependant";
}

/** A row's identity. `product_code` is NOT unique across a statement —
 * `hydrate_plans` emits one line per matched CATEGORY, and a firm-library
 * product may share a code with a company one — so the position disambiguates.
 * The same collision made two lines share a React key on the member's deck. */
const rowKey = (line: CoverageLine, i: number) => `${line.product_code}~${i}`;

/** Benefits this product has claims against that the schedule doesn't name.
 * They were visible in the old utilization list; without this they'd vanish,
 * because the schedule merges usage by benefit NAME and an unmatched key has no
 * row to merge into. */
function unmatchedBuckets(
  line: CoverageLine,
  usage: ProductUsage | undefined,
): UtilizationBucket[] {
  if (!usage || usage.byBenefit.size === 0) return [];
  const named = new Set(
    (line.benefit_schedule?.items ?? []).map((i) =>
      (i.name ?? "").trim().toLowerCase(),
    ),
  );
  return [...usage.byBenefit].filter(([k]) => !named.has(k)).map(([, b]) => b);
}

function MetaLine({ parts }: { parts: (string | null | undefined)[] }) {
  const shown = parts.filter((p): p is string => Boolean(p && p.trim()));
  if (shown.length === 0) return null;
  return (
    <p className="text-xs text-muted-foreground">
      {shown.map((p, i) => (
        <Fragment key={i}>
          {i > 0 && <span aria-hidden className="mx-1.5 text-subtle">·</span>}
          {p}
        </Fragment>
      ))}
    </p>
  );
}

/** Everything that isn't a column: why this cover applies, who else it reaches,
 * the rate detail behind the premium, and the schedule itself. */
function CoverageDetail({
  line,
  usage,
}: {
  line: CoverageLine;
  usage: ProductUsage | undefined;
}) {
  const fin = line.financials;
  const voluntary =
    fin?.rate_basis === "age_banded" || (fin?.voluntary_rates?.length ?? 0) > 0;
  const confidence =
    line.match_confidence != null
      ? `${Math.round(line.match_confidence * 100)}%`
      : null;
  const orphanRows = unmatchedBuckets(line, usage);

  return (
    <div className="flex flex-col gap-3 border-t border-border bg-muted/25 px-3 py-3.5">
      {/* No `category_display` here — the row above states it, and expanding
        * un-truncates it there rather than printing it twice. */}
      <MetaLine
        parts={[
          line.match_method
            ? `${METHOD_LABEL[line.match_method] ?? line.match_method}${
                confidence ? ` · ${confidence}` : ""
              }`
            : null,
          line.rule_human_readable ? `Rule: ${line.rule_human_readable}` : null,
          voluntary && fin?.premium_rate != null
            ? `Rate ${fin.premium_rate} per $1,000 of cover (age band)`
            : null,
        ]}
      />

      {line.covers_dependants && line.covered_dependants.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <Users className="size-3.5 shrink-0" aria-hidden />
          <span>Also covers</span>
          {line.covered_dependants.map((d) => (
            <Badge key={d.id} variant="outline">
              {depLabel(d)}
            </Badge>
          ))}
        </div>
      )}

      {line.benefit_schedule?.items?.length ? (
        <BenefitScheduleView
          schedule={line.benefit_schedule}
          annualPolicyLimit={line.annual_policy_limit}
          usageByBenefit={usage?.byBenefit}
          /* The product roll-up is already the Claims column of the row this
           * panel belongs to — repeating it here is the duplication this
           * redesign removes. */
        />
      ) : (
        <p className="text-xs text-muted-foreground">
          No schedule of benefits recorded for this plan.
        </p>
      )}

      {orphanRows.length > 0 && (
        <div className="flex flex-col gap-1 border-t border-border pt-2.5">
          <SectionLabel as="h4">Claimed against benefits not in this schedule</SectionLabel>
          {orphanRows.map((b) => (
            <div
              key={b.benefit_key}
              className="flex items-baseline justify-between gap-3 text-xs"
            >
              <span className="min-w-0 break-words text-foreground">
                {b.benefit_key}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {fmtMoney(b.approved)} approved
                {b.pending > 0 ? ` · ${fmtMoney(b.pending)} pending` : ""}
                {/* Absent from `pending` by design — see BenefitStatement. */}
                {b.pending_unconverted > 0 ? (
                  <span className="text-warn">
                    {" · "}
                    {b.pending_unconverted} awaiting conversion
                  </span>
                ) : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CoverageTable({
  lines,
  utilization,
}: {
  lines: CoverageLine[];
  utilization?: Utilization | null;
}) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const usageByProduct = useMemo(() => indexUsage(utilization), [utilization]);

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      {/* 42rem is the floor at which every column still reads; below it the
       * wrapper `Table` provides scrolls horizontally rather than the page.
       * A 1280px laptop gives this pane ~709px, so the floor must stay under
       * that or the common case scrolls sideways every time. The figure
       * columns are sized to hold their own HEADING on one line — "Premium /
       * yr" broke after the slash at 6.75rem, which reads as two columns. */}
      <Table className="min-w-[42rem] table-fixed">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Benefit</TableHead>
            <TableHead className="w-16">Plan</TableHead>
            <TableHead className="w-28 text-right">Covered</TableHead>
            <TableHead className="w-[8.5rem] whitespace-nowrap text-right">
              Premium / yr
            </TableHead>
            <TableHead className="w-[9.5rem] text-right">Claims</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lines.map((line, i) => {
            const key = rowKey(line, i);
            const expanded = open.has(key);
            const usage = usageByProduct.get(line.product_code);
            const fin = line.financials;
            const note = matchNote(line.match_method, line.match_confidence);
            return (
              <Fragment key={key}>
                <TableRow
                  onClick={() => toggle(key)}
                  aria-expanded={expanded}
                  aria-controls={`coverage-detail-${key}`}
                  className={cn(expanded && "border-b-0 bg-muted/25")}
                >
                  <TableCell className="align-middle">
                    <div className="flex items-start gap-1.5">
                      <ChevronRight
                        aria-hidden
                        className={cn(
                          "mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
                          expanded && "rotate-90",
                        )}
                      />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-2xs text-muted-foreground">
                            {line.product_code}
                          </span>
                          <span className="truncate font-medium text-foreground">
                            {line.product_name ?? line.product_code}
                          </span>
                        </div>
                        {(line.category_display || note) && (
                          // The category truncates; the match note must not.
                          // Appended to the same text run it was simply cut
                          // off — a category on this roster runs to 80
                          // characters, so the one mark a broker is scanning
                          // for was invisible on every row that had it.
                          <div
                            className={cn(
                              "flex items-baseline gap-1.5 text-xs text-muted-foreground",
                              expanded && "flex-wrap",
                            )}
                          >
                            {/* Wraps in full once the row is open, so the
                              * detail panel below never has to restate the
                              * cohort just to show the end of its name. */}
                            <span
                              className={cn(!expanded && "truncate")}
                              title={line.category_display ?? undefined}
                            >
                              {line.category_display}
                            </span>
                            {note && (
                              <span
                                className={cn(
                                  "shrink-0",
                                  note.warn && "text-warn",
                                )}
                                title={
                                  note.warn
                                    ? "Matched on name similarity — worth checking"
                                    : undefined
                                }
                              >
                                {note.text}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="align-middle">
                    {line.plan_code ? (
                      <Badge variant="outline">{line.plan_code}</Badge>
                    ) : (
                      <span className="text-subtle">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right align-middle tabular-nums">
                    {fin?.sum_insured != null ? (
                      fmtMoney(fin.sum_insured)
                    ) : (
                      <span className="text-subtle">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right align-middle tabular-nums">
                    {fin?.annual_premium != null ? (
                      <>
                        {fmtMoney(fin.annual_premium)}
                        {fin.gst_included && (
                          <div className="text-2xs font-normal text-muted-foreground">
                            incl. GST
                          </div>
                        )}
                      </>
                    ) : (
                      <span className="text-subtle">—</span>
                    )}
                  </TableCell>
                  <TableCell className="align-middle">
                    <ClaimPosition bucket={usage?.product} />
                  </TableCell>
                </TableRow>
                {expanded && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell
                      colSpan={5}
                      id={`coverage-detail-${key}`}
                      className="p-0"
                    >
                      <CoverageDetail line={line} usage={usage} />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
