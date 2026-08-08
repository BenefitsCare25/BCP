import { useState } from "react";
import { ChevronRight, Layers } from "lucide-react";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import { Segmented } from "@/components/ui/segmented";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ReportWorkbook } from "@/api/reports";

/**
 * One composite report: a workbook with several named sheets.
 *
 * The unit a broker works in is the SUBMISSION, not the file — an insurer
 * receives an employee listing, a dependant listing and a benefit-selection
 * record, and there is no point at which two of the three is a meaningful thing
 * to have sent. This page used to offer 26 separate downloads for what is
 * really about a dozen artifacts, so assembling one submission meant five
 * downloads and remembering which five.
 *
 * Three rules:
 *
 * - **The sheet list is SERVED, never a constant here** (`workbook.sheets`).
 *   A broker files against what this row says is inside; a frontend copy would
 *   drift from the composer and mislabel a workbook's contents, which is worse
 *   than not describing them at all.
 * - **Every control is declared by the workbook, not by the tab it sits on.**
 *   `supports_masking` / `supports_date_range` / `supports_employee_status`
 *   decide what is offered, so a control never appears over a report it does
 *   nothing to — the exact mislabelled-scope problem that put an insurer picker
 *   above reports no insurer submission touches.
 * - **The insurers are served too.** Deriving them here is how a picker comes
 *   to offer an insurer the download then 404s.
 */
export function ReportWorkbookRow({
  policyYearId,
  workbook,
  masked,
  year,
}: {
  policyYearId: string;
  workbook: ReportWorkbook;
  masked: boolean;
  year: number;
}) {
  const [insurer, setInsurer] = useState("");
  const [scope, setScope] = useState<"all" | "active">("all");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [open, setOpen] = useState(false);

  const blocked = workbook.requires_insurer && !insurer;

  const query = new URLSearchParams();
  if (workbook.supports_masking && !masked) query.set("masked", "false");
  if (workbook.requires_insurer && insurer) query.set("insurer", insurer);
  if (workbook.supports_employee_status && scope !== "all") {
    query.set("employee_status", scope);
  }
  if (workbook.supports_date_range) {
    if (start) query.set("start", start);
    if (end) query.set("end", end);
  }
  const qs = query.toString();

  const stamp = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const today = `${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}`;
  const slug = insurer
    ? `-${insurer.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`
    : "";

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3 px-4 py-3">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <Layers className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <p className="text-sm font-medium text-foreground">
                {workbook.label}
              </p>
              <span className="text-2xs uppercase tracking-wide text-subtle">
                .xlsx ·{" "}
                {workbook.sheets.length === 1
                  ? "1 sheet"
                  : `${workbook.sheets.length} sheets`}
              </span>
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {workbook.description}
            </p>
            {/* The sheet names, on the row rather than behind the disclosure:
                naming the tabs is the entire reason these are workbooks and not
                zips, so a broker must be able to see them without a click. The
                disclosure only adds what each one holds. */}
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="group flex items-start gap-1 text-left text-xs text-muted-foreground hover:text-foreground"
            >
              <ChevronRight
                className={`mt-0.5 size-3 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
              />
              <span>
                Sheets:{" "}
                <span className="font-medium text-foreground">
                  {workbook.sheets.map((s) => s.title).join(" · ")}
                </span>
              </span>
            </button>
          </div>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {workbook.supports_employee_status && (
            <Segmented
              value={scope}
              onChange={setScope}
              options={[
                { value: "all", label: "All" },
                { value: "active", label: "Active only" },
              ]}
            />
          )}
          {workbook.supports_date_range && (
            <div className="flex items-center gap-1.5">
              {/* Blank = the server's default window (the last 30 days). Said
                  in the placeholder rather than pre-filled, so an untouched
                  control doesn't claim a range the broker never chose. */}
              <input
                type="date"
                aria-label="From"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground shadow-sm"
              />
              <span className="text-xs text-muted-foreground">to</span>
              <input
                type="date"
                aria-label="To"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground shadow-sm"
              />
            </div>
          )}
          {workbook.requires_insurer && (
            <Select
              value={insurer}
              onValueChange={setInsurer}
              disabled={!workbook.insurers.length}
            >
              <SelectTrigger className="w-[170px]" aria-label="Insurer">
                <SelectValue
                  placeholder={
                    workbook.insurers.length
                      ? "Select insurer"
                      : "No insurers configured"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {workbook.insurers.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/workbooks/${workbook.key}${qs ? `?${qs}` : ""}`}
            filename={`${workbook.key}${slug}-${year}-${today}.xlsx`}
            label="Download"
            size="sm"
            disabled={blocked}
          />
        </div>
      </div>

      {open && (
        <dl className="space-y-1.5 border-t border-border px-4 py-3 pl-11">
          {workbook.sheets.map((sheet) => (
            <div key={sheet.title} className="flex flex-wrap gap-x-2 text-xs">
              <dt className="font-medium text-foreground">{sheet.title}</dt>
              <dd className="text-muted-foreground">{sheet.description}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
