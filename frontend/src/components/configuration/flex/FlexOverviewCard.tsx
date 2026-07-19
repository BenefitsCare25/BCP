import { useState, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Download,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InfoHint } from "@/components/ui/tooltip";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  downloadFlexCoverage,
  useFlexCoverage,
  useFlexMembership,
} from "@/api/hooks";
import { formatError } from "@/lib/errors";
import {
  FAMILY_STATUS_LABELS,
  type CoverageBucket,
  type CoverageBucketKey,
  type FamilyStatusCode,
} from "@/types";
import { toast } from "sonner";

const ORDER: FamilyStatusCode[] = ["S", "M", "M1C", "M2C", "M3C"];

// What each exception means + how to fix it (shown in the drill-down header).
const BUCKET_HELP: Record<CoverageBucketKey, string> = {
  no_family_status:
    "No spouse/child dependant and no usable marital status on the roster, so the wallet can't be sized by family band. Add their dependants or set marital status.",
  not_in_any_tier:
    "Their grade/designation matches no eligibility tier, so they receive no flex wallet. Add or widen a tier, or fix the roster value.",
  multiple_tiers:
    "They satisfy more than one tier's rules and were assigned to the first. Tighten the overlapping tier match sets.",
  unclassified_relationship:
    "The relationship isn't recognized as spouse or child, so this dependant is excluded from flex sizing. Correct the relationship value.",
  outside_age_window:
    "This spouse/child is past the scheme's dependant age limit, so they draw no coverage or flex and don't count toward the wallet's family band. Adjust the age limit under Scheme details or the dependant's date of birth.",
  orphaned:
    "This dependant isn't linked to any employee, so it attaches to no one's flex. Link it to an employee.",
  inactive_link:
    "This dependant is linked to an employee who isn't on the active roster.",
};

function CheckRow({
  bucket,
  onOpen,
}: {
  bucket: CoverageBucket;
  onOpen: (b: CoverageBucket) => void;
}) {
  const ok = bucket.count === 0;
  return (
    <button
      type="button"
      disabled={ok}
      onClick={() => !ok && onOpen(bucket)}
      className={
        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm " +
        (ok ? "cursor-default" : "hover:bg-muted/60 transition-colors")
      }
    >
      {ok ? (
        <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />
      ) : (
        <AlertTriangle className="size-4 shrink-0 text-amber-500" />
      )}
      <span className="text-foreground">{bucket.label}</span>
      <span
        className={
          "ml-auto font-medium tabular-nums " +
          (ok ? "text-muted-foreground" : "text-amber-600 dark:text-amber-400")
        }
      >
        {bucket.count}
      </span>
      {!ok && <ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
    </button>
  );
}

interface Props {
  policyYearId: string;
  // Benefit-year picker, rendered beside the coverage download. It must survive
  // every fallback branch below — you have to be able to switch back to a year
  // whose roster loads even when this one's doesn't.
  yearSelector?: ReactNode;
}

/**
 * Single Flex overview card: the family-status headcount (from the employee +
 * dependant listings) alongside the coverage-validation reconciliation ("is
 * anyone left out?"). Both read from the same roster, so they live in one card —
 * the distribution up top, the pass/fail checks below, each exception opening a
 * slide-over of exactly who and downloadable as an .xlsx.
 */
export function FlexOverviewCard({ policyYearId, yearSelector }: Props) {
  const membership = useFlexMembership(policyYearId);
  const coverage = useFlexCoverage(policyYearId);
  const [active, setActive] = useState<CoverageBucket | null>(null);
  const [query, setQuery] = useState("");
  const [downloading, setDownloading] = useState(false);

  const onDownload = async () => {
    setDownloading(true);
    try {
      await downloadFlexCoverage(policyYearId);
    } catch (err) {
      toast.error(formatError(err));
    } finally {
      setDownloading(false);
    }
  };

  // Right-aligned picker row, used by the branches that render no card header.
  const selectorRow = yearSelector ? (
    <div className="flex justify-end">{yearSelector}</div>
  ) : null;

  if (membership.isError || coverage.isError) {
    return (
      <div className="space-y-3">
        {selectorRow}
        <Card>
          <CardContent className="flex items-center justify-between gap-3 p-4 text-sm text-muted-foreground">
            <span>Couldn't load the Flex overview.</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void membership.refetch();
                void coverage.refetch();
              }}
            >
              <RefreshCw className="size-4" /> Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const m = membership.data;
  if (membership.isLoading || !m) return selectorRow;

  if (m.employees_total === 0) {
    return (
      <div className="space-y-3">
        {selectorRow}
        <Card>
          <CardContent className="p-4 text-sm text-muted-foreground">
            Upload the employee &amp; dependant rosters under{" "}
            <span className="font-medium text-foreground">Operations</span> to
            see the family-status distribution and coverage checks.
          </CardContent>
        </Card>
      </div>
    );
  }

  const counts = m.family_status_counts;
  const c = coverage.data;

  const employeeBuckets = c?.buckets.filter((b) => b.kind === "employee") ?? [];
  const depBuckets = c?.buckets.filter((b) => b.kind === "dependant") ?? [];

  const rows = active
    ? active.rows.filter((r) => {
        if (!query.trim()) return true;
        const q = query.toLowerCase();
        return [r.staff_id, r.name, r.label, r.detail].some(
          (v) => v && v.toLowerCase().includes(q),
        );
      })
    : [];

  return (
    <Card>
      <CardContent className="p-5 space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="grid size-9 place-items-center rounded-lg bg-muted text-muted-foreground">
              <Users className="size-4.5" />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <h3 className="text-sm font-semibold text-foreground">
                  Membership &amp; coverage
                </h3>
                <InfoHint>
                  The family-status distribution and coverage reconciliation both
                  read from the uploaded employee &amp; dependant listings — the
                  same roster that sizes every flex wallet.
                </InfoHint>
              </div>
              <p className="text-xs text-muted-foreground">
                {m.employees_total.toLocaleString()} employee
                {m.employees_total === 1 ? "" : "s"}
                {c && c.dependants_total > 0
                  ? ` · ${c.dependants_total.toLocaleString()} dependants`
                  : ""}{" "}
                from the roster
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {yearSelector}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onDownload()}
              disabled={downloading || !c}
            >
              <Download className="size-4" />
              {downloading ? "Preparing…" : "Download .xlsx"}
            </Button>
          </div>
        </div>

        {/* Family-status distribution */}
        <section className="space-y-2">
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Family-status distribution
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            {ORDER.map((code) => (
              <div
                key={code}
                className="rounded-lg border border-border bg-muted/20 p-3"
              >
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {FAMILY_STATUS_LABELS[code]}
                </div>
                <div className="mt-1 text-2xl font-semibold tabular-nums text-foreground">
                  {(counts[code] ?? 0).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Coverage validation */}
        <section className="space-y-3">
          {!c ? (
            <p className="text-sm text-muted-foreground">Checking coverage…</p>
          ) : (
            <>
              <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
                <div>
                  <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                    <span className="uppercase tracking-wider text-muted-foreground">
                      Employees
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      <span className="font-medium text-foreground">
                        {c.employees_ok.toLocaleString()}/
                        {c.employees_total.toLocaleString()}
                      </span>{" "}
                      {c.has_tiers ? "resolved & assigned" : "resolved"}
                    </span>
                  </div>
                  {employeeBuckets.map((b) => (
                    <CheckRow key={b.key} bucket={b} onOpen={setActive} />
                  ))}
                </div>

                <div>
                  <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                    <span className="uppercase tracking-wider text-muted-foreground">
                      Dependants
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      <span className="font-medium text-foreground">
                        {c.dependants_ok.toLocaleString()}/
                        {c.dependants_total.toLocaleString()}
                      </span>{" "}
                      eligible
                    </span>
                  </div>
                  {c.dependants_total === 0 ? (
                    <p className="px-2 py-1.5 text-sm text-muted-foreground">
                      No dependants uploaded yet.
                    </p>
                  ) : (
                    depBuckets.map((b) => (
                      <CheckRow key={b.key} bucket={b} onOpen={setActive} />
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </section>
      </CardContent>

      <Sheet
        open={active !== null}
        onOpenChange={(o) => {
          if (!o) {
            setActive(null);
            setQuery("");
          }
        }}
      >
        <SheetContent>
          {active && (
            <>
              <SheetHeader>
                <SheetTitle>{active.label}</SheetTitle>
                <SheetDescription>
                  {active.count.toLocaleString()}{" "}
                  {active.kind === "employee" ? "employee" : "dependant"}
                  {active.count === 1 ? "" : "s"} · {BUCKET_HELP[active.key]}
                </SheetDescription>
              </SheetHeader>

              <SheetBody className="space-y-3">
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search name, staff ID, reason…"
                    className="pl-8"
                  />
                </div>

                {active.truncated && (
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    Showing the first {active.rows.length.toLocaleString()} of{" "}
                    {active.count.toLocaleString()} — download the report for the
                    full list.
                  </p>
                )}

                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50 text-left text-xs uppercase tracking-wider text-muted-foreground">
                      {active.kind === "employee" ? (
                        <tr>
                          <th className="px-3 py-2 font-medium">Staff ID</th>
                          <th className="px-3 py-2 font-medium">Name</th>
                          <th className="px-3 py-2 font-medium">Designation</th>
                          <th className="px-3 py-2 font-medium">Reason</th>
                        </tr>
                      ) : (
                        <tr>
                          <th className="px-3 py-2 font-medium">Employee</th>
                          <th className="px-3 py-2 font-medium">Dependant</th>
                          <th className="px-3 py-2 font-medium">Reason</th>
                        </tr>
                      )}
                    </thead>
                    <tbody className="divide-y divide-border">
                      {rows.map((r, i) => (
                        <tr key={i} className="align-top">
                          {active.kind === "employee" ? (
                            <>
                              <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                                {r.staff_id || "—"}
                              </td>
                              <td className="px-3 py-2 text-foreground">
                                {r.name || "—"}
                              </td>
                              <td className="px-3 py-2 text-muted-foreground">
                                {r.label || "—"}
                              </td>
                              <td className="px-3 py-2 text-muted-foreground">
                                {r.detail}
                              </td>
                            </>
                          ) : (
                            <>
                              <td className="px-3 py-2 text-foreground">
                                {r.name || r.staff_id || "—"}
                              </td>
                              <td className="px-3 py-2 text-foreground">
                                {r.label || "—"}
                              </td>
                              <td className="px-3 py-2 text-muted-foreground">
                                {r.detail}
                              </td>
                            </>
                          )}
                        </tr>
                      ))}
                      {rows.length === 0 && (
                        <tr>
                          <td
                            colSpan={active.kind === "employee" ? 4 : 3}
                            className="px-3 py-6 text-center text-sm text-muted-foreground"
                          >
                            No matches.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </SheetBody>

              <SheetFooter>
                <Button
                  variant="outline"
                  onClick={() => void onDownload()}
                  disabled={downloading}
                >
                  <Download className="size-4" />
                  {downloading ? "Preparing…" : "Download full report"}
                </Button>
              </SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>
    </Card>
  );
}
