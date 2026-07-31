import { AlertTriangle } from "lucide-react";
import { InfoHint } from "@/components/ui/tooltip";
import { formatWallet } from "@/lib/flex";
import type { FlexCoverageLine } from "@/types";

/**
 * Flex price-tag summary: wallet -> price tags used -> net balance, plus the
 * per-product breakdown. Shared by the benefit-statement flex card and the
 * employee detail sheet so the figures (and labels/colors) stay identical.
 * Renders nothing when no price-tag matrix applies.
 */
export function FlexPriceTagSummary({ flex }: { flex: FlexCoverageLine }) {
  if (flex.price_tags_total == null) return null;
  const shortfall = flex.flex_balance != null && flex.flex_balance < 0;
  const lines = flex.price_tag_lines.filter((l) => l.price_tag != null);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1 text-2xs uppercase tracking-wider text-muted-foreground">
        Flex spend (price tags)
        <InfoHint>
          A price tag is the flex-dollar cost of a coverage choice. Wallet minus
          the tags used gives the balance you have left to spend.
        </InfoHint>
      </div>
      <div className="grid grid-cols-3 gap-2 text-sm">
        <Stat label="Wallet" value={formatWallet(flex.wallet_amount, flex.currency)} />
        <Stat
          label="Price tags used"
          value={formatWallet(flex.price_tags_total, flex.currency)}
        />
        <Stat
          label={shortfall ? "Shortfall" : "Balance"}
          value={formatWallet(
            flex.flex_balance != null ? Math.abs(flex.flex_balance) : null,
            flex.currency,
          )}
          tone={shortfall ? "bad" : "good"}
        />
      </div>
      {(lines.length > 0 || flex.leave_flex_amount != null) && (
        <div className="space-y-0.5 border-t border-border pt-1.5">
          {lines.map((l, i) => (
            <div
              key={i}
              className="flex items-center justify-between text-xs text-muted-foreground"
            >
              <span>
                {l.product_code}
                {l.plan_code ? ` · ${l.plan_code}` : ""}
              </span>
              <span className="text-foreground">
                {formatWallet(l.price_tag, flex.currency)}
              </span>
            </div>
          ))}
          {flex.leave_flex_amount != null && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                Leave {flex.leave_action === "buy" ? "bought" : "sold"}
                {flex.leave_days != null
                  ? ` (${flex.leave_days} ${flex.leave_days === 1 ? "day" : "days"})`
                  : ""}
              </span>
              <span
                className={
                  flex.leave_flex_amount < 0 ? "text-error" : "text-good"
                }
              >
                {flex.leave_flex_amount < 0 ? "-" : "+"}
                {formatWallet(Math.abs(flex.leave_flex_amount), flex.currency)}
              </span>
            </div>
          )}
        </div>
      )}
      {!flex.price_age_known && (
        <div className="flex items-start gap-1.5 rounded-md border border-warn/40 bg-warn-soft/30 p-2 text-2xs text-foreground">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <span>
            This member's age couldn't be determined (missing date of birth), so
            price tags weren't applied — the balance may overstate what's available.
          </span>
        </div>
      )}
      {flex.wallet_amount == null && (
        <p className="text-xs text-muted-foreground">
          No flex wallet assigned yet — confirm &amp; assign the Flex scheme to see
          the balance.
        </p>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  return (
    <div>
      <div className="text-2xs uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={
          tone === "bad"
            ? "font-medium text-error"
            : tone === "good"
              ? "font-medium text-good"
              : "font-medium text-foreground"
        }
      >
        {value}
      </div>
    </div>
  );
}
