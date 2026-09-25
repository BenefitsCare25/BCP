import { EyeOff } from "lucide-react";
import { cn } from "@/lib/cn";
import type { LimitTone } from "@/lib/claimLimits";

const TONE_CLASS: Record<LimitTone, string> = {
  live: "border-good/40 bg-good/10 text-foreground",
  review: "border-warn/50 bg-warn/10 text-foreground",
  wording: "border-border bg-muted/40 text-muted-foreground",
  none: "border-dashed border-border text-muted-foreground",
};

export const TONE_LABEL: Record<LimitTone, string> = {
  live: "Drawdown",
  review: "Review",
  wording: "No drawdown",
  none: "",
};

/** The row is hidden from employees in the SOB step. */
export function HiddenTag() {
  return (
    <span className="mt-0.5 inline-flex items-center gap-1 text-2xs text-muted-foreground">
      <EyeOff className="size-3" aria-hidden /> Hidden in SOB
    </span>
  );
}

/**
 * One claim-limit cell: its state (Drawdown / Review / Display) and a plain
 * reading of the limit. Shared by the Claim limits editor (interactive) and
 * the setup summary (read-only), so the two can never describe the same
 * setting differently.
 */
export function LimitChip({
  text,
  tone,
  sub,
  unset = false,
  active = false,
  onClick,
}: {
  text: string;
  tone: LimitTone;
  sub?: string;
  /** No setting yet: the slip's wording only. */
  unset?: boolean;
  active?: boolean;
  /** Omitted in read-only views. */
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="flex items-baseline gap-1.5">
        {unset && tone !== "none" ? (
          <span className="shrink-0 text-2xs font-medium uppercase tracking-wider opacity-70">Not set</span>
        ) : TONE_LABEL[tone] && (
          <span className="shrink-0 text-2xs font-medium uppercase tracking-wider opacity-80">
            {TONE_LABEL[tone]}
          </span>
        )}
        <span className="line-clamp-3 min-w-0 break-words" title={text}>
          {text}
        </span>
      </span>
      {sub && (
        <span className="mt-0.5 block truncate text-2xs text-muted-foreground" title={sub}>
          {sub}
        </span>
      )}
    </>
  );
  const className = cn("w-full rounded-md border px-2 py-1 text-left text-xs", TONE_CLASS[tone]);
  if (!onClick) return <div className={className}>{body}</div>;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(className, "transition-colors hover:border-foreground/30", active && "ring-2 ring-ring/40")}
    >
      {body}
    </button>
  );
}
