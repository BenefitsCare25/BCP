import {
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ReactNode,
} from "react";
import { Link } from "@tanstack/react-router";
import { FlaskConical, RotateCcw, ArrowLeft, ArrowUpRight } from "lucide-react";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AI_REVIEW_ENABLED, useAIReview } from "./review-state";

export const fieldClass =
  "w-full rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/50";
export const panelClass = "rounded-lg border border-border bg-card";
export function ReviewGate({
  children,
  platform = false,
}: {
  children: ReactNode;
  platform?: boolean;
}) {
  const { data: me, isLoading, error, refetch } = useMe();
  if (isLoading)
    return (
      <p role="status" className="p-6 text-sm text-muted-foreground">
        Loading your access…
      </p>
    );
  if (error)
    return (
      <div className="space-y-4 p-6">
        <h1 className="text-xl font-semibold">We couldn’t check your access</h1>
        <p>Check that the local API is running, then try again.</p>
        <Button type="button" onClick={() => void refetch()}>
          Try again
        </Button>
      </div>
    );
  if (
    !me ||
    (platform
      ? me.role !== "system_admin"
      : !["broker_admin", "system_admin"].includes(me.role))
  )
    return (
      <div className="space-y-3 p-6">
        <h1 className="text-xl font-semibold">Access restricted</h1>
        <p className="text-sm text-muted-foreground">
          {platform ? "Platform administrator" : "Broker administrator"} access
          is required for this workspace.
        </p>
      </div>
    );
  if (!AI_REVIEW_ENABLED)
    return (
      <div className="space-y-3 p-6">
        <h1 className="text-xl font-semibold">AI oversight</h1>
        <p className="max-w-xl text-sm text-muted-foreground">
          This workspace is awaiting its governance service connection. No
          records or approval actions are available yet.
        </p>
      </div>
    );
  return <>{children}</>;
}
export function PreviewNotice() {
  const reset = useAIReview((s) => s.reset);
  return (
    <div
      className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-md border border-info/20 bg-info-soft px-4 py-3 text-xs text-info"
      role="note"
    >
      <div className="flex min-w-0 items-start gap-2">
        <FlaskConical className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <p>
          <strong>Local UI review · Sample data.</strong> Changes stay in this
          browser tab until refresh. No claims, approvals or messages are sent.
        </p>
      </div>
      <Button
        type="button"
        variant="ghost"
        className="h-9 text-xs text-info"
        onClick={reset}
      >
        <RotateCcw className="size-3.5" aria-hidden="true" />
        Reset samples
      </Button>
    </div>
  );
}
export function Status({ value }: { value: string }) {
  const variant = [
    "Active",
    "Reviewed",
    "Approved",
    "Complete",
    "Passed",
    "Current",
  ].includes(value)
    ? "good"
    : ["Blocked", "Action required", "Review due", "Open"].includes(value)
      ? "warn"
      : [
            "Awaiting review",
            "Awaiting approval",
            "In progress",
            "Review requested",
          ].includes(value)
        ? "info"
        : "default";
  return <Badge variant={variant}>{value}</Badge>;
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  const id = useId();
  const child = children as ReactElement<{
    id?: string;
    "aria-describedby"?: string;
  }>;
  const describedBy =
    [child.props?.["aria-describedby"], hint ? `${id}-hint` : undefined]
      .filter(Boolean)
      .join(" ") || undefined;
  return (
    <div className="space-y-2 text-sm">
      <label htmlFor={id} className="block font-medium">
        {label}
      </label>
      {isValidElement(children)
        ? cloneElement(child, { id, "aria-describedby": describedBy })
        : children}
      {hint && (
        <p
          id={`${id}-hint`}
          className="text-xs leading-relaxed text-muted-foreground"
        >
          {hint}
        </p>
      )}
    </div>
  );
}
export function BackToOversight() {
  return (
    <Link
      to="/firm/ai-oversight"
      className="mb-4 inline-flex min-h-11 items-center gap-1 text-sm text-muted-foreground hover:text-primary"
    >
      <ArrowLeft className="size-4" aria-hidden="true" />
      AI oversight
    </Link>
  );
}
export function WorkflowLinks() {
  const { data: me } = useMe();
  return (
    <nav
      aria-label="Related AI workflows"
      className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border pt-4 text-sm"
    >
      <span className="font-medium text-muted-foreground">
        Review a workflow
      </span>
      <Link
        to="/claims/review"
        search={{ preview: "ai-governance" }}
        className="inline-flex min-h-11 items-center gap-1 text-primary"
      >
        Claim decision
        <ArrowUpRight className="size-4" aria-hidden="true" />
      </Link>
      <Link
        to="/firm/ai-oversight/member-journey"
        className="inline-flex min-h-11 items-center gap-1 text-primary"
      >
        Member journey
        <ArrowUpRight className="size-4" aria-hidden="true" />
      </Link>
      {me?.role === "system_admin" && (
        <Link
          to="/platform/ai-oversight"
          className="inline-flex min-h-11 items-center gap-1 text-primary"
        >
          Platform releases
          <ArrowUpRight className="size-4" aria-hidden="true" />
        </Link>
      )}
    </nav>
  );
}
