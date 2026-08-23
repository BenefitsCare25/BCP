import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Layers } from "lucide-react";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import { SubmissionRecord } from "@/components/operations/ReportVersionActions";
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
 * - **The submission record belongs to THIS row, under its own Download.**
 *   Downloading a submission-grade workbook files a retained copy, so what was
 *   last sent is a property of this button and its insurer — not of a separate
 *   section further down the page, which is what the retained listings used to
 *   be. That section duplicated two of this workbook's three sheets and offered
 *   a Save button as its only control, so the archive read as a chore with no
 *   visible payoff and the download hid two clicks inside it.
 */
/** Retired series merged into a live one's history. Mirrors
 *  `report_registry.SUPERSEDED_TYPES` — a short, closed list whose only job is
 *  to keep old submissions reachable, so it is not worth a served field. */
const SUPERSEDED: Record<string, string[]> = {
  insurer_submission: ["employee_listing", "dependant_listing"],
};

export function ReportWorkbookRow({
  policyYearId,
  workbook,
  year,
}: {
  policyYearId: string;
  workbook: ReportWorkbook;
  year: number;
}) {
  const [insurer, setInsurer] = useState("");
  // Masking is the row's OWN control, like every other one here. It arrived as
  // a prop from a section header while `supports_masking` was already served —
  // so the toggle governing the insurer submission sat in the header of the
  // section BELOW it, labelled "Internal registers". That was merely confusing
  // until masking became load-bearing: an unmasked pull is what files a
  // submission, and a broker cannot be asked to discover that from a control in
  // a different section.
  const [masked, setMasked] = useState(true);
  const [scope, setScope] = useState<"all" | "active">("all");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();

  const blocked = workbook.requires_insurer && !insurer;
  // A download FILES a copy server-side, so the record line below is stale the
  // instant the click succeeds. Without this the broker downloads, sees "Not
  // sent yet" still sitting under the button, and downloads again.
  const onDownloaded = workbook.retained_type
    ? () => {
        qc.invalidateQueries({ queryKey: ["report-version-status"] });
        qc.invalidateQueries({ queryKey: ["report-versions"] });
      }
    : undefined;

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

        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0">
          {workbook.supports_masking && (
            <Segmented
              value={masked ? "masked" : "full"}
              onChange={(v) => setMasked(v === "masked")}
              options={[
                { value: "masked", label: "Masked" },
                { value: "full", label: "Unmasked" },
              ]}
            />
          )}
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
            onDownloaded={onDownloaded}
          />
        </div>
      </div>

      {workbook.retained_type && (
        <SubmissionRecord
          policyYearId={policyYearId}
          reportType={workbook.retained_type}
          supersededTypes={SUPERSEDED[workbook.retained_type] ?? []}
          scopeKey={insurer ? insurer.toLowerCase() : null}
          scopeLabel={insurer || undefined}
          hasMovement={workbook.requires_insurer}
          disabled={blocked}
          filesOnDownload={!workbook.supports_masking || !masked}
        />
      )}

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
