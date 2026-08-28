/** Multi-invoice upload review.
 *
 * Each distinct invoice remains a separate claim because limits, duplicate
 * checks and AI adjudication operate per visit. This is the batch-level receipt:
 * every invoice, its amount, its place in the run, and currency-safe totals.
 */
import { Check, Files, X } from "lucide-react";
import { Money } from "@/components/portal/leaf/Figure";
import { Strike } from "@/components/portal/leaf/Strike";
import { formatDay } from "@/components/portal/leaf/date";
import type { NewClaimForm } from "./useNewClaimForm";

type BatchRow = {
  key: string;
  invoice: string;
  date: string | null;
  amount: number | null;
  currency: string;
  state: "submitted" | "current" | "pending";
  uploadIndex?: number;
};

function positiveAmount(value: string | number | null | undefined): number | null {
  const amount = Number(value);
  return Number.isFinite(amount) && amount > 0 ? amount : null;
}

function stateLabel(state: BatchRow["state"]): string {
  if (state === "submitted") return "Submitted";
  if (state === "current") return "Reviewing now";
  return "Up next";
}

export function PendingClaimsNotice({ form }: { form: NewClaimForm }) {
  const { pendingClaims, submittedBatchClaims, multiDone, busy } = form;
  if (pendingClaims.length === 0 && multiDone === 0) return null;

  const rows: BatchRow[] = [
    ...submittedBatchClaims.map((claim) => ({
      key: claim.id,
      invoice: claim.invoiceNumber,
      date: claim.incurredDate,
      amount: claim.amount,
      currency: claim.currency.toUpperCase(),
      state: "submitted" as const,
    })),
    {
      key: "current",
      invoice: form.invoiceNumber.trim() || "Current invoice",
      date: form.incurredDate || null,
      amount: positiveAmount(form.amount),
      currency: form.effectiveCurrency.toUpperCase(),
      state: "current",
    },
    ...pendingClaims.map((claim) => ({
      key: `pending-${claim.uploadIndex}`,
      invoice: claim.fields?.invoice_number ?? claim.fileName,
      date: claim.fields?.incurred_date ?? null,
      amount: positiveAmount(claim.fields?.amount),
      currency: (claim.fields?.currency ?? form.effectiveCurrency).toUpperCase(),
      state: "pending" as const,
      uploadIndex: claim.uploadIndex,
    })),
  ];
  const totals = new Map<string, number>();
  for (const row of rows) {
    if (row.amount == null) continue;
    totals.set(row.currency, (totals.get(row.currency) ?? 0) + row.amount);
  }
  const missingAmounts = rows.filter((row) => row.amount == null).length;
  const totalClaims = rows.length;

  return (
    <section
      className="space-y-3 rounded-control border border-hairline bg-bar/70 p-3"
      aria-labelledby="claim-batch-heading"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Files className="size-3.5 shrink-0 text-label" aria-hidden />
          <h2 id="claim-batch-heading" className="text-row font-semibold text-record">
            Invoices in this submission
          </h2>
        </div>
        <Strike tone="pending">
          Claim {multiDone + 1} of {totalClaims}
        </Strike>
      </div>
      <p className="text-row text-label">
        Each invoice is submitted and AI-reviewed as its own claim. Check the
        breakdown and totals as you work through the set.
      </p>

      <ol className="divide-y divide-hairline overflow-hidden rounded-control border border-hairline">
        {rows.map((row) => (
          <li
            key={row.key}
            className="flex min-w-0 items-center gap-3 px-3 py-2 text-row"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium text-record">{row.invoice}</span>
              <span className="block text-label">
                {row.date ? formatDay(row.date) : "Date not read"} · {stateLabel(row.state)}
              </span>
            </span>
            <Money
              value={row.amount}
              currency={row.currency}
              className="shrink-0 text-row font-medium"
            />
            {row.state === "submitted" && (
              <>
                <Check className="size-4 shrink-0 text-strike-approved" aria-hidden />
                <span className="sr-only">Submitted</span>
              </>
            )}
            {row.state === "pending" && row.uploadIndex != null && (
              <button
                type="button"
                disabled={busy}
                title="Don't submit a claim for this invoice"
                onClick={() =>
                  form.setPendingClaims((previous) =>
                    previous.filter((claim) => claim.uploadIndex !== row.uploadIndex),
                  )
                }
                aria-label={`Remove the invoice ${row.invoice}`}
                className="leaf-focus -m-3 inline-flex size-11 shrink-0 items-center justify-center text-label disabled:opacity-50"
              >
                <X className="size-4" aria-hidden />
              </button>
            )}
          </li>
        ))}
      </ol>

      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 border-t border-hairline pt-3">
        <div>
          <p className="text-row font-semibold text-record">
            {missingAmounts > 0 ? "Known subtotal" : "Batch total"}
          </p>
          {missingAmounts > 0 && (
            <p className="text-row text-label">
              {missingAmounts} {missingAmounts === 1 ? "invoice needs" : "invoices need"} an
              amount before the final total is complete.
            </p>
          )}
        </div>
        <dl className="space-y-0.5 text-right">
          {[...totals.entries()].map(([currency, total]) => (
            <div key={currency} className="flex items-baseline justify-end gap-2">
              <dt className="text-2xs font-semibold uppercase tracking-label text-label">
                {currency}
              </dt>
              <dd>
                <Money value={total} currency={currency} emphasis="strong" />
              </dd>
            </div>
          ))}
        </dl>
      </div>
      {pendingClaims.length > 0 && (
        <p className="text-row text-label">
          Remove an invoice if you do not want to submit a claim for it.
        </p>
      )}
    </section>
  );
}
