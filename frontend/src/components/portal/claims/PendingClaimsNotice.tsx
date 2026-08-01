/** Multi-invoice upload — each distinct invoice is a separate visit and needs
 * its own claim (per-visit limits and duplicate checks adjudicate per invoice).
 * The form walks them one at a time; this states where the member is in that
 * run and lets them drop an invoice they don't want to claim for.
 *
 * A notice, so it carries a strike rather than a fill — the brand on this page
 * belongs to the submit button. */
import { Files, X } from "lucide-react";
import { Strike } from "@/components/portal/leaf/Strike";
import { Money, currencySymbol } from "@/components/portal/leaf/Figure";
import { formatDay } from "@/components/portal/leaf/date";
import type { NewClaimForm } from "./useNewClaimForm";

export function PendingClaimsNotice({ form }: { form: NewClaimForm }) {
  const { pendingClaims, multiDone, busy } = form;
  if (pendingClaims.length === 0 && multiDone === 0) return null;
  const total = multiDone + 1 + pendingClaims.length;

  return (
    <div className="space-y-2 rounded-control border border-hairline bg-bar/70 p-3">
      <div className="flex items-center gap-1.5">
        <Files className="size-3.5 shrink-0 text-label" aria-hidden />
        <Strike tone="pending">
          Claim {multiDone + 1} of {total}
        </Strike>
      </div>
      <p className="text-row text-label">
        {pendingClaims.length > 0
          ? "Your upload contains several different invoices. Each invoice is a separate visit and needs its own claim — submit this one and we'll prefill the next automatically."
          : "This is the last claim from your upload."}
      </p>
      {pendingClaims.length > 0 && (
        <>
          <ul className="space-y-1">
            {pendingClaims.map((p) => (
              <li
                key={p.uploadIndex}
                className="flex items-center justify-between gap-2 rounded-control bg-glass px-3 py-1.5 text-row"
              >
                <span className="min-w-0 truncate text-record">
                  {p.fields?.invoice_number ?? p.fileName}
                  <span className="text-label">
                    {p.fields?.incurred_date
                      ? ` · ${formatDay(p.fields.incurred_date)}`
                      : ""}
                    {p.fields?.amount != null ? " · " : ""}
                  </span>
                  {p.fields?.amount != null && (
                    <Money
                      value={p.fields.amount}
                      currency={currencySymbol(p.fields.currency)}
                      className="text-label"
                    />
                  )}
                  <span className="text-label"> · up next</span>
                </span>
                <button
                  type="button"
                  disabled={busy}
                  title="Don't submit a claim for this invoice"
                  onClick={() =>
                    form.setPendingClaims((prev) =>
                      prev.filter((q) => q.uploadIndex !== p.uploadIndex),
                    )
                  }
                  aria-label={`Remove the invoice ${p.fields?.invoice_number ?? p.fileName}`}
                  className="leaf-focus -m-3 inline-flex size-11 shrink-0 items-center justify-center text-label disabled:opacity-50"
                >
                  <X className="size-4" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
          <p className="text-row text-label">
            Remove an invoice if you don't want to submit a claim for it.
          </p>
        </>
      )}
    </div>
  );
}
