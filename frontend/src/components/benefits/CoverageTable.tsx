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
 *
 * Every value here is DERIVED from a product's setup, so each one carries a
 * pencil to where it is set (`SourceLink`) — a wrong figure is fixed at its
 * source, never on this page.
 */
import { Fragment, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/cn";
import type { CoverageLine, Utilization } from "@/types";
import { CoverageDetail } from "./CoverageDetail";
import { SourceFlag, SourceIcon } from "./SourceLink";
import { ClaimPosition, indexUsage } from "./usage";

/**
 * How a match is flagged in the LIST.
 *
 * Only two methods earn a mark. `fuzzy_name` is a name-similarity guess and is
 * the thing a broker auditing coverage is looking for; `manual_override` means
 * a person decided, which is worth knowing before you "correct" it. Exact and
 * rule matches are clean. A name match the row's own rule confirms is stored
 * as a rule match by the engine, so this mark now means "only the name agrees".
 */
function matchNote(
  method: string | null,
  confidence: number | null,
): { text: string; warn: boolean } | null {
  if (method === "manual_override") {
    return { text: "Manual override", warn: false };
  }
  if (method !== "fuzzy_name") return null;
  // A name match that scored 1.0 matched exactly once normalized — nothing
  // uncertain about it, and marking it amber teaches a broker to ignore the colour.
  if (confidence != null && confidence >= 1) return null;
  const pct = confidence != null ? ` ${Math.round(confidence * 100)}%` : "";
  return { text: `Name match${pct}`, warn: true };
}

/** A row's identity. `product_code` is NOT unique across a statement —
 * `hydrate_plans` emits one line per matched CATEGORY, and a firm-library
 * product may share a code with a company one — so the position disambiguates.
 * The same collision made two lines share a React key on the member's deck. */
const rowKey = (line: CoverageLine, i: number) => `${line.product_code}~${i}`;

/** Setup not confirmed: the member portal shows this product without its
 * schedule or limits, while this broker table shows everything — so say so. */
const unpublished = (line: CoverageLine) =>
  line.plan_status != null && line.plan_status !== "confirmed";

/** A figure cell: the value, then the pencil to where it is set. */
function Figure({
  value,
  muted,
  title,
  sub,
  productCode,
}: {
  value: string | null;
  muted?: boolean;
  title?: string;
  sub?: string;
  productCode: string;
}) {
  return (
    <div className="flex items-center justify-end gap-1">
      <SourceIcon productCode={productCode} section="basis_of_cover" />
      <span className={cn(muted && "text-muted-foreground")} title={title}>
        {value ?? <span className="text-subtle">—</span>}
        {sub && (
          <span className="block text-2xs font-normal text-muted-foreground">{sub}</span>
        )}
      </span>
    </div>
  );
}

export function CoverageTable({
  lines,
  utilization,
  employeeId,
  canEdit = false,
}: {
  lines: CoverageLine[];
  utilization?: Utilization | null;
  /** With `canEdit`, voluntary rows offer "Mark enrolled" / "Enrol". */
  employeeId?: string;
  canEdit?: boolean;
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
       * columns are sized to hold their own HEADING on one line plus the
       * pencil beside the figure. */}
      <Table className="min-w-[42rem] table-fixed">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Benefit</TableHead>
            <TableHead className="w-20">Plan</TableHead>
            <TableHead className="w-32 text-right">Covered</TableHead>
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
            const eligible = line.enrolment === "eligible";
            const premiumSub =
              fin?.annual_premium == null
                ? undefined
                : eligible
                  ? "if enrolled"
                  : fin.gst_included
                    ? "incl. GST"
                    : undefined;
            return (
              <Fragment key={key}>
                <TableRow
                  onClick={() => toggle(key)}
                  aria-expanded={expanded}
                  aria-controls={`coverage-detail-${key}`}
                  className={cn("group/row", expanded && "border-b-0 bg-muted/25")}
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
                          <span
                            className={cn(
                              "truncate font-medium",
                              eligible ? "text-muted-foreground" : "text-foreground",
                            )}
                          >
                            {line.product_name ?? line.product_code}
                          </span>
                          {eligible && (
                            <Badge
                              variant="outline"
                              title="Voluntary cover not taken up. Not claimable until enrolled."
                            >
                              Eligible · not enrolled
                            </Badge>
                          )}
                          {unpublished(line) && (
                            <SourceFlag
                              productCode={line.product_code}
                              section="schedule_of_benefits"
                              tone="muted"
                              title="Setup not confirmed: the employee portal shows this product without its schedule. Open the setup to confirm it."
                            >
                              <Badge variant="warn">Not on portal</Badge>
                            </SourceFlag>
                          )}
                        </div>
                        {(line.category_display || note) && (
                          // The category truncates; the match note must not.
                          <div
                            className={cn(
                              "flex items-center gap-1.5 text-xs text-muted-foreground",
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
                            {note &&
                              (note.warn ? (
                                <SourceFlag
                                  productCode={line.product_code}
                                  categoryId={line.category_id}
                                  tone="warn"
                                  title="Matched on name similarity only. Open the matching rule to confirm or fix it."
                                >
                                  {note.text}
                                </SourceFlag>
                              ) : (
                                <span className="shrink-0">{note.text}</span>
                              ))}
                            {line.category_id && (
                              <SourceIcon
                                productCode={line.product_code}
                                categoryId={line.category_id}
                              />
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="align-middle">
                    <div className="flex items-center gap-1">
                      {line.plan_code ? (
                        <Badge variant="outline">{line.plan_code}</Badge>
                      ) : (
                        <span className="text-subtle">—</span>
                      )}
                      <SourceIcon productCode={line.product_code} section="basis_of_cover" />
                    </div>
                  </TableCell>
                  <TableCell className="text-right align-middle tabular-nums">
                    <Figure
                      productCode={line.product_code}
                      value={fin?.sum_insured != null ? fmtMoney(fin.sum_insured) : null}
                      muted={eligible}
                    />
                  </TableCell>
                  <TableCell className="text-right align-middle tabular-nums">
                    <Figure
                      productCode={line.product_code}
                      value={fin?.annual_premium != null ? fmtMoney(fin.annual_premium) : null}
                      muted={eligible}
                      title={line.premium_note ?? undefined}
                      sub={premiumSub}
                    />
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
                      <CoverageDetail
                        line={line}
                        usage={usage}
                        employeeId={employeeId}
                        canEdit={canEdit}
                      />
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
