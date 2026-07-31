import { Wallet, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { InfoHint } from "@/components/ui/tooltip";
import { FlexPriceTagSummary } from "./FlexPriceTagSummary";
import { formatWallet } from "@/lib/flex";
import {
  FAMILY_STATUS_LABELS,
  type FamilyStatusCode,
  type FlexCoverageLine,
} from "@/types";

const SOURCE_LABEL: Record<string, string> = {
  dependants: "Resolved from dependant records",
  roster: "Resolved from the employee roster",
  none: "Family status not resolved",
};

function familyLabel(code: string | null): string | null {
  if (!code) return null;
  return FAMILY_STATUS_LABELS[code as FamilyStatusCode] ?? code;
}

export function FlexCoverageCard({ flex }: { flex: FlexCoverageLine }) {
  const family = familyLabel(flex.family_status);
  const hasCostShare =
    flex.employer_pct != null || flex.employee_pct != null;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Wallet className="size-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">
              Flexible Benefits
            </h3>
            {flex.tier_name && <Badge variant="outline">{flex.tier_name}</Badge>}
          </div>
          {flex.scheme_name && (
            <p className="mt-1 text-xs text-muted-foreground">
              {flex.scheme_name}
            </p>
          )}
        </div>
        <div className="text-right">
          <div className="text-lg font-semibold text-foreground">
            {formatWallet(flex.wallet_amount, flex.currency)}
          </div>
          <div className="flex items-center justify-end gap-1 text-2xs text-muted-foreground">
            Annual wallet
            <InfoHint>
              Your yearly flex dollars to spend across the claimable benefits
              below.
            </InfoHint>
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
        {family && (
          <span className="rounded-md border border-border bg-muted/40 px-2 py-0.5 text-foreground">
            <span className="text-muted-foreground">Family status: </span>
            {family}
          </span>
        )}
        {hasCostShare && (
          <span className="rounded-md border border-border bg-muted/40 px-2 py-0.5 text-foreground">
            <span className="text-muted-foreground">Cost share: </span>
            {flex.employer_pct ?? "—"}% employer / {flex.employee_pct ?? "—"}%
            employee
          </span>
        )}
      </div>
      {flex.source && SOURCE_LABEL[flex.source] && (
        <p className="mt-1.5 text-2xs italic text-muted-foreground">
          {SOURCE_LABEL[flex.source]}
        </p>
      )}

      {flex.assignment_stale && (
        <div className="mt-2 flex items-start gap-1.5 rounded-md border border-border bg-warn-soft/40 p-2 text-2xs text-foreground">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <span>
            The Flex scheme changed after this wallet was assigned, so the
            claimable benefits below may be out of date. Re-assign wallets from
            the Flex tab to refresh.
          </span>
        </div>
      )}

      {flex.price_tags_total != null && (
        <div className="mt-3 border-t border-border pt-3">
          <FlexPriceTagSummary flex={flex} />
        </div>
      )}

      {flex.benefit_categories.length > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <div className="mb-1.5 text-2xs uppercase tracking-wider text-muted-foreground">
            Claimable benefits
          </div>
          <div className="space-y-1">
            {flex.benefit_categories.map((cat, i) => (
              <div
                key={i}
                className="flex items-start justify-between gap-3 text-sm"
              >
                <div className="flex items-start gap-1.5">
                  {cat.claimable ? (
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-good" />
                  ) : (
                    <XCircle className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <div>
                    <span className="text-foreground">{cat.name}</span>
                    {cat.note && (
                      <span className="ml-1.5 text-xs text-muted-foreground">
                        {cat.note}
                      </span>
                    )}
                  </div>
                </div>
                {cat.sub_limit != null && (
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatWallet(cat.sub_limit, flex.currency)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
