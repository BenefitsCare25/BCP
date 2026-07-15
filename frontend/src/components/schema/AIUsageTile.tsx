import { Link } from "@tanstack/react-router";
import { Activity, AlertTriangle, CheckCircle2, ZapOff } from "lucide-react";
import { useAIStatus, useAISpend } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type BreakerState = "closed" | "half_open" | "open";

const BREAKER_DISPLAY: Record<
  BreakerState,
  { variant: "good" | "warn" | "error"; icon: JSX.Element }
> = {
  closed: { variant: "good", icon: <CheckCircle2 className="size-3.5" /> },
  half_open: { variant: "warn", icon: <Activity className="size-3.5" /> },
  open: { variant: "error", icon: <ZapOff className="size-3.5" /> },
};

function pct(used: number, total: number): number {
  if (!total) return 0;
  return Math.min(100, Math.round((used / total) * 100));
}

export function AIUsageTile() {
  const { data: status } = useAIStatus();
  const { data: spend } = useAISpend();

  if (!status) return null;
  if (!status.configured) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>AI usage</CardTitle>
          <CardDescription>
            AI provider not configured. Either set a tenant key on{" "}
            <Link className="underline" to="/configuration/ai-provider">
              Configuration → AI provider
            </Link>{" "}
            (recommended), or set the platform-wide{" "}
            <code>AZURE_FOUNDRY_*</code> env vars.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const sourceBadge =
    status.source === "byok" ? (
      <Badge variant="good">BYOK</Badge>
    ) : (
      <Badge variant="primary">platform key</Badge>
    );

  const used = spend?.month_to_date_tokens ?? status.month_to_date_tokens ?? 0;
  const budget =
    spend?.monthly_token_budget ?? status.monthly_token_budget ?? 0;
  const percent = pct(used, budget);
  const breaker = BREAKER_DISPLAY[status.breaker_state as BreakerState];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>AI usage this month</CardTitle>
            <CardDescription>
              Tokens spent against budget. Cache hits don't count.
            </CardDescription>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap justify-end">
            {sourceBadge}
            <Badge variant={breaker.variant}>
              <span className="inline-flex items-center gap-1">
                {breaker.icon}
                breaker {status.breaker_state.replace("_", " ")}
              </span>
            </Badge>
            <Badge variant="outline">cache: {status.cache_kind}</Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end justify-between gap-2">
          <div>
            <div className="text-2xl font-semibold text-foreground">
              {used.toLocaleString()}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                / {budget.toLocaleString()} tokens
              </span>
            </div>
            {spend && (
              <div className="text-xs text-muted-foreground mt-1">
                ≈ ${spend.month_to_date_cost_usd.toFixed(4)} USD this month
              </div>
            )}
          </div>
          {percent >= 80 && (
            <div className="flex items-center gap-1.5 text-warn text-sm">
              <AlertTriangle className="size-4" /> {percent}% of budget
            </div>
          )}
        </div>
        <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
          <div
            className={
              percent >= 100
                ? "h-full bg-error"
                : percent >= 80
                  ? "h-full bg-warn"
                  : "h-full bg-good"
            }
            style={{ width: `${percent}%` }}
          />
        </div>
        {spend && spend.by_operation.length > 0 && (
          <div className="grid grid-cols-3 gap-2 pt-2">
            {spend.by_operation.map((op) => (
              <div
                key={op.operation}
                className="rounded-md border border-border p-2 bg-card"
              >
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {op.operation}
                </div>
                <div className="text-sm font-medium">
                  {op.tokens.toLocaleString()} tok
                </div>
                <div className="text-xs text-muted-foreground">
                  {op.calls} call{op.calls === 1 ? "" : "s"}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
