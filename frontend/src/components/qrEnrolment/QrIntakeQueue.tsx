/** QR submissions for the benefit year: what is waiting to be matched to the
 *  roster, what was matched, and what was rejected. */
import { useState } from "react";
import { ChevronRight, Inbox } from "lucide-react";
import { useQrIntakes, type IntakeStatus } from "@/api/qrEnrolment";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { QrIntakeSheet } from "./QrIntakeSheet";

const TABS: { value: IntakeStatus; label: string }[] = [
  { value: "pending", label: "Waiting to match" },
  { value: "linked", label: "Matched" },
  { value: "rejected", label: "Rejected" },
];

export function QrIntakeQueue({
  policyYearId,
  windowId,
  readOnly,
}: {
  policyYearId: string;
  windowId?: string;
  readOnly: boolean;
}) {
  const [status, setStatus] = useState<IntakeStatus>("pending");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const q = useDebouncedValue(query, 300);
  const list = useQrIntakes(policyYearId, { status, windowId, query: q, offset });
  const counts = list.data?.counts ?? {};
  const total = list.data?.total ?? 0;

  return (
    <section aria-labelledby="qr-queue-title" className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 id="qr-queue-title" className="font-semibold text-foreground">QR submissions</h3>
          <p className="text-sm text-muted-foreground">
            Check each with HR, then link it to the employee. Linked forms join the register above.
          </p>
        </div>
        <Input className="h-11 sm:h-9 sm:max-w-64" value={query} onChange={e => { setQuery(e.target.value); setOffset(0); }}
          placeholder="Search name, staff ID or reference" aria-label="Search QR submissions" />
      </div>
      <div role="tablist" aria-label="Submission status" className="flex flex-wrap gap-1.5">
        {TABS.map(t => (
          <button key={t.value} type="button" role="tab" aria-selected={status === t.value}
            onClick={() => { setStatus(t.value); setOffset(0); }}
            className={cn(
              "inline-flex h-11 items-center gap-2 rounded-md border px-3 text-sm transition-colors sm:h-9",
              status === t.value
                ? "border-transparent bg-sidebar-active font-medium text-sidebar-active-foreground"
                : "border-border bg-card text-muted-foreground hover:bg-muted",
            )}>
            {t.label}
            <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums text-foreground">{counts[t.value] ?? 0}</span>
          </button>
        ))}
      </div>
      {list.isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map(k => <Skeleton key={k} className="h-16 rounded-lg" />)}</div>
      ) : list.error ? (
        <Card className="p-5" role="alert">
          <p className="font-medium text-foreground">QR submissions could not be loaded</p>
          <p className="mt-1 text-sm text-muted-foreground">{formatError(list.error)}</p>
          <Button variant="outline" className="mt-3" onClick={() => void list.refetch()}>Try again</Button>
        </Card>
      ) : !list.data?.items.length ? (
        <Card className="flex items-center gap-3 p-5 text-sm text-muted-foreground">
          <Inbox className="size-5 shrink-0" aria-hidden />
          {status === "pending"
            ? "Nothing waiting. New QR submissions appear here as employees send them."
            : status === "linked" ? "No matched submissions yet." : "No rejected submissions."}
        </Card>
      ) : (
        <Card className="divide-y divide-border overflow-hidden">
          {list.data.items.map(item => (
            <button key={item.id} type="button" onClick={() => setOpen(item.id)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:outline-none sm:px-5">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-foreground">
                  {item.claimed_name ?? "—"} <span className="font-normal text-muted-foreground">· {item.claimed_staff_id ?? "—"}</span>
                </p>
                <p className="truncate text-sm text-muted-foreground">
                  {item.reference_no} · {fmtDateTime(item.submitted_at)}
                  {item.category_label ? ` · ${item.category_label}` : ""}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {item.status === "linked" && item.linked_employee_name
                    ? `Linked to ${item.linked_employee_name}`
                    : item.status === "rejected" ? item.rejected_reason
                    : item.requests.join(" · ") || "No changes requested"}
                </p>
              </div>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            </button>
          ))}
        </Card>
      )}
      {total > 50 && (
        <div className="flex items-center justify-end gap-2 text-sm text-muted-foreground">
          <span>{offset + 1}–{Math.min(offset + 50, total)} of {total}</span>
          <Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>
          <Button variant="outline" disabled={offset + 50 >= total} onClick={() => setOffset(offset + 50)}>Next</Button>
        </div>
      )}
      <QrIntakeSheet intakeId={open} readOnly={readOnly} onClose={() => setOpen(null)} />
    </section>
  );
}
