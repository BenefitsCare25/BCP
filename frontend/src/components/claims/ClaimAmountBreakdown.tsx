import { AlertTriangle, CheckCircle2, ScanLine } from "lucide-react";
import type { ClaimAmountBreakdown as Breakdown } from "@/api/claims";
import { cn } from "@/lib/cn";

const STATUS = {
  match: {
    label: "Document total matches",
    icon: CheckCircle2,
    className: "border-good/30 bg-good-soft text-good",
  },
  mismatch: {
    label: "Amount mismatch",
    icon: AlertTriangle,
    className: "border-warn/40 bg-warn-soft text-warn",
  },
  needs_review: {
    label: "Total needs review",
    icon: AlertTriangle,
    className: "border-warn/40 bg-warn-soft text-warn",
  },
  not_available: {
    label: "No amount read",
    icon: ScanLine,
    className: "border-border bg-muted text-muted-foreground",
  },
} as const;

function money(currency: string | null, amount: number | null): string {
  if (amount == null) return "Not read";
  return `${currency ?? ""} ${amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`.trim();
}

function treatmentLabel(line: Breakdown["lines"][number]): string {
  if (line.included_in_total) return "Included";
  if (line.resolution === "duplicate") return "Not added twice";
  if (line.resolution === "supporting") return "Supporting figure";
  if (line.resolution === "ambiguous") return "Needs review";
  return "No total read";
}

export function ClaimAmountBreakdown({ breakdown }: { breakdown: Breakdown }) {
  const status = STATUS[breakdown.status];
  const StatusIcon = status.icon;

  return (
    <section className="space-y-3" aria-labelledby="amount-reconciliation-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="amount-reconciliation-heading" className="text-sm font-semibold text-foreground">
            Document amount reconciliation
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Amounts are read by AI; grouping and totals are calculated by Inspro.
          </p>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium",
            status.className,
          )}
        >
          <StatusIcon className="size-3.5" aria-hidden />
          {status.label}
        </span>
      </div>

      <div
        className="overflow-x-auto rounded-md border border-border"
        role="region"
        aria-label="Document amount breakdown table"
        tabIndex={0}
      >
        <table className="w-full min-w-[36rem] text-left text-xs">
          <caption className="sr-only">
            AI-extracted invoice and receipt amounts included in the claim total
          </caption>
          <thead className="bg-muted text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Document</th>
              <th scope="col" className="px-3 py-2 font-medium">Invoice</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Amount read</th>
              <th scope="col" className="px-3 py-2 font-medium">Treatment</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {breakdown.lines.map((line) => (
              <tr key={line.document_id} className="align-top">
                <td className="max-w-52 px-3 py-2.5">
                  <span className="block truncate font-medium text-foreground" title={line.file_name}>
                    {line.file_name}
                  </span>
                  <span className="block text-muted-foreground">{line.document_type}</span>
                </td>
                <td className="px-3 py-2.5 text-muted-foreground">
                  {line.invoice_number || "Not read"}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums text-foreground">
                  {money(line.currency, line.amount)}
                </td>
                <td className="max-w-64 px-3 py-2.5">
                  <span className="block font-medium text-foreground">{treatmentLabel(line)}</span>
                  <span className="block text-muted-foreground">{line.note}</span>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-border bg-card">
            <tr>
              <th scope="row" colSpan={2} className="px-3 py-2.5 font-medium text-foreground">
                Document total
              </th>
              <td className="px-3 py-2.5 text-right tabular-nums font-semibold text-foreground">
                {breakdown.totals.length > 0
                  ? breakdown.totals.map((total) => money(total.currency, total.amount)).join(" + ")
                  : "Not available"}
              </td>
              <td className="px-3 py-2.5 text-muted-foreground">
                Claimed {money(breakdown.claimed_currency, breakdown.claimed_amount)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">{breakdown.note}</p>
    </section>
  );
}
