import { cloneElement, useId, type ReactElement, type SelectHTMLAttributes } from "react";
import { Button } from "@/components/ui/button";
import { singaporeTodayISO } from "@/lib/business-date";

export function Field({ label, children }: { label: string; children: ReactElement<{ id?: string }> }) {
  const id = useId();
  return <div className="flex min-w-0 flex-col gap-1.5 text-sm font-medium"><label htmlFor={id}>{label}</label>{cloneElement(children, { id })}</div>;
}
export function Choice(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className="h-9 w-full min-w-0 rounded-md border border-input bg-card px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50" />;
}
export function Failure({ error, retry }: { error: unknown; retry?: () => void }) {
  return <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md bg-error-soft p-3 text-sm text-error">{error instanceof Error ? error.message : "Unable to load WICA. Try again."}{retry && <Button variant="outline" size="sm" onClick={retry}>Retry</Button>}</div>;
}
export function Loading() {
  return <div role="status" aria-label="Loading" className="space-y-3 py-4"><div className="h-9 w-1/3 rounded-md bg-muted" /><div className="h-40 rounded-lg bg-muted" /></div>;
}
export const dateLabel = (value: string) => new Intl.DateTimeFormat("en-SG", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value.slice(0, 10) + "T00:00:00"));
export const today = singaporeTodayISO;
