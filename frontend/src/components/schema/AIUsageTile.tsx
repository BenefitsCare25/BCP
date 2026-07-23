import { useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Infinity as InfinityIcon,
  Loader2,
  Pencil,
  ZapOff,
} from "lucide-react";
import { toast } from "sonner";
import { useAIStatus, useAISpend, useSetAIBudget } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatError } from "@/lib/errors";

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
  const setBudget = useSetAIBudget();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

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
            <code>INSPRO_AI_PROVIDER=vertex</code> +{" "}
            <code>VERTEX_PROJECT</code> env vars (Google ADC).
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const sourceBadge =
    status.source === "byok" ? (
      <Badge variant="good">BYOK</Badge>
    ) : (
      <Badge variant="info">platform key</Badge>
    );

  const used = spend?.month_to_date_tokens ?? status.month_to_date_tokens ?? 0;
  const budget =
    spend?.monthly_token_budget ?? status.monthly_token_budget ?? 0;
  const unlimited = budget === 0;
  const percent = pct(used, budget);
  const breaker = BREAKER_DISPLAY[status.breaker_state as BreakerState];

  const openEditor = () => {
    // 0 (unlimited) shows as an empty box so the user just types a number.
    setDraft(budget === 0 ? "" : String(budget));
    setEditing(true);
  };

  const saveBudget = async () => {
    const trimmed = draft.trim();
    const value = trimmed === "" ? 0 : Number(trimmed);
    if (!Number.isInteger(value) || value < 0) {
      toast.error("Enter a whole number of tokens (or leave blank for unlimited).");
      return;
    }
    try {
      await setBudget.mutateAsync(value);
      toast.success(
        value === 0
          ? "Budget set to unlimited (tracking only)"
          : `Monthly limit set to ${value.toLocaleString()} tokens`,
      );
      setEditing(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>AI usage this month</CardTitle>
            <CardDescription>
              {unlimited
                ? "Tokens tracked; no monthly limit set. Cache hits don't count."
                : "Tokens spent against budget. Cache hits don't count."}
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
                {unlimited ? (
                  <span className="inline-flex items-center gap-1">
                    tokens ·{" "}
                    <InfinityIcon className="size-3.5" /> unlimited
                  </span>
                ) : (
                  <>/ {budget.toLocaleString()} tokens</>
                )}
              </span>
            </div>
            {spend && (
              <div className="text-xs text-muted-foreground mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span>
                  ↓ {spend.month_to_date_input_tokens.toLocaleString()} in
                </span>
                <span>
                  ↑ {spend.month_to_date_output_tokens.toLocaleString()} out
                </span>
                <span aria-hidden>·</span>
                <span>≈ ${spend.month_to_date_cost_usd.toFixed(4)} USD this month</span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!unlimited && percent >= 80 && (
              <div className="flex items-center gap-1.5 text-warn text-sm">
                <AlertTriangle className="size-4" /> {percent}% of budget
              </div>
            )}
            {!editing && (
              <Button
                variant="outline"
                size="sm"
                onClick={openEditor}
                className="shrink-0"
              >
                <Pencil className="size-3.5" />
                {unlimited ? "Set limit" : "Edit limit"}
              </Button>
            )}
          </div>
        </div>
        {editing && (
          <div className="rounded-md border border-border bg-muted/40 p-3 space-y-2">
            <div className="text-xs font-medium text-foreground">
              Monthly token limit
            </div>
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={0}
                inputMode="numeric"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Leave blank for unlimited"
                className="max-w-56"
                autoFocus
              />
              <Button
                size="sm"
                onClick={saveBudget}
                disabled={setBudget.isPending}
              >
                {setBudget.isPending && (
                  <Loader2 className="size-3.5 animate-spin" />
                )}
                Save
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing(false)}
                disabled={setBudget.isPending}
              >
                Cancel
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Blank or <code>0</code> = unlimited (tracking only). When set, AI
              calls are blocked once the month's non-cached tokens reach the
              limit.
            </p>
          </div>
        )}
        {!unlimited && (
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
        )}
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
                  ↓ {op.input_tokens.toLocaleString()} · ↑{" "}
                  {op.output_tokens.toLocaleString()}
                </div>
                <div className="text-xs text-muted-foreground">
                  {op.calls} call{op.calls === 1 ? "" : "s"} · $
                  {op.cost_usd.toFixed(4)}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
