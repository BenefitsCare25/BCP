/** Choose which forms go to HR and pack them. Every selected PDF is read
 *  before anything is published; a missing file stops the batch. */
import { useMemo, useState } from "react";
import { PackageCheck } from "lucide-react";
import { toast } from "sonner";
import type { EnrollmentWindow } from "@/api/enrollment";
import { useBatchPreview, usePrepareBatch, type Batch, type BatchPreviewItem } from "@/api/qrEnrolment";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";

type Status = "submitted" | "acknowledged";

function Filters({
  windows, windowId, statuses, pendingQr, onWindow, onStatus, onPendingQr,
}: {
  windows: EnrollmentWindow[];
  windowId: string;
  statuses: Status[];
  pendingQr: boolean;
  onWindow: (id: string) => void;
  onStatus: (s: Status, on: boolean) => void;
  onPendingQr: (on: boolean) => void;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-1.5">
        <Label htmlFor="batch-window">Enrolment period</Label>
        <NativeSelect id="batch-window" value={windowId} onChange={e => onWindow(e.target.value)}>
          <option value="">All periods this year</option>
          {windows.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
        </NativeSelect>
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-foreground">Include</legend>
        {(["submitted", "acknowledged"] as const).map(s => (
          <label key={s} className="flex items-center gap-2 text-sm text-foreground">
            <Checkbox checked={statuses.includes(s)} onCheckedChange={v => onStatus(s, v === true)} />
            {s === "submitted" ? "Submitted forms" : "Acknowledged forms"}
          </label>
        ))}
        <label className="flex items-center gap-2 text-sm text-foreground">
          <Checkbox checked={pendingQr} onCheckedChange={v => onPendingQr(v === true)} />
          QR submissions not yet matched
        </label>
      </fieldset>
    </div>
  );
}

function PreviewList({
  items, excluded, chosen, total, limit, over, onToggle,
}: {
  items: BatchPreviewItem[];
  excluded: Set<string>;
  chosen: number;
  total: number;
  limit: number;
  over: boolean;
  onToggle: (id: string, on: boolean) => void;
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-foreground">
        <span className="font-semibold tabular-nums">{chosen}</span> of {total} forms selected
      </p>
      {over && (
        <p className="text-sm text-error" role="alert">
          More than {limit} forms. Choose one period or untick some to prepare them in parts.
        </p>
      )}
      <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-lg border border-border">
        {items.map(i => (
          <li key={i.id}>
            <label className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-muted">
              <Checkbox checked={!excluded.has(i.id)} onCheckedChange={v => onToggle(i.id, v === true)}
                aria-label={`Include ${i.reference_no}`} />
              <span className="min-w-0 flex-1 truncate text-foreground">
                {i.employee_name ?? "—"} <span className="text-muted-foreground">· {i.staff_id ?? "—"} · {i.reference_no}</span>
              </span>
              {i.kind === "intake" ? <Badge variant="warn">Unmatched QR</Badge> : <Badge variant="outline">{i.status}</Badge>}
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PrepareBatchSheet({
  open,
  policyYearId,
  windows,
  defaultWindowId,
  onClose,
  onPrepared,
}: {
  open: boolean;
  policyYearId: string;
  windows: EnrollmentWindow[];
  defaultWindowId?: string;
  onClose: () => void;
  onPrepared: (batch: Batch) => void;
}) {
  const [windowId, setWindowId] = useState<string>(defaultWindowId ?? "");
  const [statuses, setStatuses] = useState<Status[]>(["submitted", "acknowledged"]);
  const [pendingQr, setPendingQr] = useState(false);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const filter = useMemo(
    () => ({ window_id: windowId || null, statuses, include_pending_qr: pendingQr }),
    [windowId, statuses, pendingQr],
  );
  const preview = useBatchPreview(policyYearId, filter, open);
  const prepare = usePrepareBatch(policyYearId);
  const items = preview.data?.items ?? [];
  const chosen = items.filter(i => !excluded.has(i.id)).length;
  const over = (preview.data?.total ?? 0) > (preview.data?.limit ?? 1000);

  const toggleStatus = (s: Status, on: boolean) =>
    setStatuses(prev => (on ? [...prev, s] : prev.filter(x => x !== s)));
  const toggle = (id: string, on: boolean) =>
    setExcluded(prev => { const next = new Set(prev); if (on) next.delete(id); else next.add(id); return next; });
  const submit = async () => {
    try {
      const batch = await prepare.mutateAsync({ ...filter, exclude_ids: [...excluded] });
      toast.success(`${batch.file_count} forms packed`);
      setExcluded(new Set());
      onPrepared(batch);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <Sheet open={open} onOpenChange={o => { if (!o) onClose(); }}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Prepare forms for HR</SheetTitle>
          <SheetDescription>
            Pack the selected form PDFs into one ZIP with a list of what is inside. Later submissions do not
            change a prepared batch. The ZIP is kept for 7 days.
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-5">
          <Filters windows={windows} windowId={windowId} statuses={statuses} pendingQr={pendingQr}
            onWindow={id => { setWindowId(id); setExcluded(new Set()); }} onStatus={toggleStatus} onPendingQr={setPendingQr} />
          {pendingQr && (
            <p className="rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
              Unmatched QR forms are labelled PENDING VERIFICATION in the ZIP: their identity is not yet confirmed.
            </p>
          )}
          {preview.isLoading ? (
            <Skeleton className="h-40 rounded-lg" />
          ) : preview.error ? (
            <p className="text-sm text-error" role="alert">{formatError(preview.error)}</p>
          ) : items.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
              No forms match. Change the period or what to include.
            </p>
          ) : (
            <PreviewList items={items} excluded={excluded} chosen={chosen} total={preview.data?.total ?? 0}
              limit={preview.data?.limit ?? 1000} over={over} onToggle={toggle} />
          )}
        </SheetBody>
        <SheetFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={prepare.isPending} disabled={chosen === 0 || over || statuses.length === 0}>
            <PackageCheck className="size-4" /> Prepare {chosen} forms
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
