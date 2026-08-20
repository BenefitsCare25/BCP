/** What a foreign bill is worth in the policy currency, and the member saying
 * they accept it.
 *
 * A member files in the currency they were billed in but is reimbursed — and
 * has their limit consumed — in the policy currency. That makes the converted
 * figure a TERM of the claim, not a display nicety, so it is put in front of
 * them before they send.
 *
 * **Displayed, not ticked.** There is deliberately no consent checkbox: sending
 * the claim with this figure on screen IS the acceptance, and the form submits
 * `fx_acknowledged` with the amount it actually displayed. The server still
 * records WHICH figure that was (`fx_acknowledged_at`, stamped only when its own
 * conversion agrees), so the audit value survives without the extra click.
 *
 * Three states, and the third is the one that matters most:
 *   loading      — a quiet placeholder, never a blocked form
 *   converted    — the figure, its rate, and the tick
 *   unavailable  — no rate could be fetched. The member is told plainly that
 *                  their claim still goes through and a person will convert it.
 *                  A currency API being down must never read to a claimant as
 *                  "you cannot claim".
 */
import { AlertTriangle, ArrowRight, Loader2 } from "lucide-react";
import type { FxQuote } from "@/api/portal";

interface Props {
  quote: FxQuote | null;
  loading: boolean;
  /** The quote REQUEST failed (not "there is no rate" — that is `available:
   *  false` on a quote that did arrive). Renders a retry, because with no quote
   *  there is also no checkbox, and submit is blocked until one or the other
   *  resolves. */
  failed?: boolean;
  onRetry?: () => void;
  currency: string;
  policyCurrency: string;
  error?: string;
}

function money(code: string, value: number): string {
  return `${code} ${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function ConversionNotice({
  quote,
  loading,
  currency,
  policyCurrency,
  error,
  failed = false,
  onRetry,
}: Props) {
  if (currency === policyCurrency) return null;

  if (failed && !loading) {
    return (
      <div className="space-y-2 rounded-control bg-bar/70 px-3 py-2.5">
        <p className="flex items-start gap-2 text-row text-strike-pending">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          We couldn&apos;t show the {policyCurrency} amount just now. You can still
          submit the claim; if a rate is available when it is saved, we&apos;ll ask
          you to confirm the converted amount.
        </p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="leaf-focus text-row font-medium text-action-ink underline underline-offset-2"
          >
            Try again
          </button>
        )}
        {error && <p className="text-2xs text-strike-pending">{error}</p>}
      </div>
    );
  }

  if (loading && !quote) {
    return (
      <p className="flex items-center gap-2 rounded-control bg-bar/70 px-3 py-2.5 text-row text-label">
        <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
        Working out what this is in {policyCurrency}…
      </p>
    );
  }

  if (!quote) return null;

  if (!quote.available) {
    return (
      <p className="flex items-start gap-2 rounded-control bg-bar/70 px-3 py-2.5 text-row text-strike-pending">
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        {quote.note ??
          `We could not get an exchange rate for ${currency}. Your claim can ` +
            "still be sent — it will be converted by hand when it is reviewed."}
      </p>
    );
  }

  const converted = quote.converted ?? 0;
  return (
    <div className="space-y-2.5 rounded-control bg-bar/70 px-3 py-3">
      <p className="flex flex-wrap items-center gap-2 text-row">
        <span className="tabular-nums">{money(currency, quote.amount)}</span>
        <ArrowRight className="size-3.5 shrink-0 text-label" aria-hidden />
        <span className="font-medium tabular-nums text-record">
          {money(policyCurrency, converted)}
        </span>
      </p>
      {/* The rate and the day it is from. The day can be earlier than the
          receipt — no rate is published for a weekend or a holiday — and the
          server words that, so the member is not left to infer that an
          unfamiliar date is a mistake. */}
      {quote.note && <p className="text-2xs text-label">{quote.note}</p>}
      {error && <p className="text-2xs text-strike-pending">{error}</p>}
    </div>
  );
}
