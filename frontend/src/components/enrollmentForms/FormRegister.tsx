/** The enrolment-form register, shared by the broker's Enrolment page and the
 * HR portal: filters, exports, and one row per signed (or scanned) form.
 *
 * The two surfaces differ only in what they may DO — HR downloads, the broker
 * also acknowledges — and in which roles may pull the bulk PDF ZIP or (broker
 * only) full ID numbers in the Excel summary, so those are what is passed in. */
import { CheckCircle2, Download, FileArchive, FileSpreadsheet, Loader2, Search } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type {
  FormRegister as FormRegisterData,
  FormRegisterItem,
  FormStatus,
  RegisterFilters,
} from "@/api/enrollmentForms";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";

export const REGISTER_PAGE_SIZE = 25;

export function FormStatusBadge({ item }: { item: Pick<FormRegisterItem, "status" | "enrollment_status" | "source"> }) {
  if (item.status === "superseded") return <Badge variant="outline">Replaced</Badge>;
  if (item.status === "cancelled") return <Badge variant="outline">Cancelled</Badge>;
  if (item.status === "returned") return <Badge variant="outline">Needs correction</Badge>;
  if (item.source === "portal" && ["not_started", "in_progress", "returned"].includes(item.enrollment_status ?? "")) return <Badge variant="outline">No longer current</Badge>;
  if (item.enrollment_status === "confirmed" || item.enrollment_status === "deemed") {
    return <Badge variant="good">Confirmed</Badge>;
  }
  if (item.status === "acknowledged") return <Badge variant="info">Acknowledged</Badge>;
  return <Badge variant="warn">Awaiting review</Badge>;
}

function useBusy() {
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setBusy(null);
    }
  };
  return { busy, run };
}

export function FormRegisterView({
  data,
  isLoading,
  error,
  onRetry,
  filters,
  onFiltersChange,
  windows,
  showSource = true,
  onDownload,
  onExport,
  canExportPdfs,
  idNumbers,
  onAcknowledge,
  emptyHint,
}: {
  data: FormRegisterData | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  filters: RegisterFilters;
  onFiltersChange: (next: RegisterFilters) => void;
  windows: { id: string; name: string }[];
  showSource?: boolean;
  onDownload: (item: FormRegisterItem) => Promise<unknown>;
  onExport: (kind: "zip" | "xlsx") => Promise<unknown>;
  /** The bulk ZIP holds every signed form unredacted, so each surface offers
   *  it to its write/administrator roles only; the server refuses the rest. */
  canExportPdfs: boolean;
  /** Whether the Excel summary carries full NRIC/FIN numbers. Passed only for
   *  roles the server lets unmask; without it the summary is always masked. */
  idNumbers?: { full: boolean; onChange: (full: boolean) => void };
  onAcknowledge?: (item: FormRegisterItem) => Promise<unknown>;
  emptyHint: string;
}) {
  const { busy, run } = useBusy();
  const page = Math.floor((filters.offset ?? 0) / REGISTER_PAGE_SIZE);
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / REGISTER_PAGE_SIZE));
  const set = (patch: RegisterFilters) => onFiltersChange({ ...filters, ...patch, offset: 0 });
  const counts = data?.counts ?? {};

  return (
    <section className="space-y-3" aria-label="Enrolment forms">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="warn">{counts.submitted ?? 0} awaiting review</Badge>
        <Badge variant="info">{counts.acknowledged ?? 0} acknowledged</Badge>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {/* Beside the one download it qualifies, as on the Reports Center's
              workbook rows. Masked unless deliberately switched. */}
          {idNumbers && (
            <div role="group" aria-label="NRIC/FIN numbers in the Excel summary">
              <Segmented
                value={idNumbers.full ? "full" : "masked"}
                onChange={(v) => idNumbers.onChange(v === "full")}
                disabled={busy === "xlsx"}
                options={[
                  { value: "masked", label: "Masked" },
                  { value: "full", label: "Unmasked" },
                ]}
              />
            </div>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-11 sm:h-9"
            disabled={!data?.total || busy === "xlsx"}
            onClick={() => void run("xlsx", () => onExport("xlsx"))}
          >
            {busy === "xlsx" ? <Loader2 className="size-4 animate-spin" /> : <FileSpreadsheet className="size-4" />}
            Excel summary
          </Button>
          {canExportPdfs && (
            <Button
              variant="outline"
              size="sm"
              className="h-11 sm:h-9"
              disabled={!data?.total || busy === "zip"}
              onClick={() => void run("zip", () => onExport("zip"))}
            >
              {busy === "zip" ? <Loader2 className="size-4 animate-spin" /> : <FileArchive className="size-4" />}
              All PDFs (.zip)
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <label className="relative block w-full sm:max-w-xs">
          <span className="sr-only">Search forms</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            value={filters.query ?? ""}
            onChange={(e) => set({ query: e.target.value })}
            placeholder="Name, staff ID or reference"
            className="h-11 pl-9 sm:h-9"
          />
        </label>
        <NativeSelect
          aria-label="Enrolment period"
          className="h-11 sm:h-9"
          value={filters.windowId ?? ""}
          onChange={(e) => set({ windowId: e.target.value || undefined })}
        >
          <option value="">All periods</option>
          {windows.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Status"
          className="h-11 sm:h-9"
          value={filters.status ?? ""}
          onChange={(e) => set({ status: e.target.value as FormStatus | "" })}
        >
          <option value="">Latest versions</option>
          <option value="submitted">Awaiting review</option>
          <option value="acknowledged">Acknowledged</option>
          <option value="returned">Needs correction</option>
          <option value="cancelled">Cancelled</option>
        </NativeSelect>
        {showSource && (
          <NativeSelect
            aria-label="Source"
            className="h-11 sm:h-9"
            value={filters.source ?? ""}
            onChange={(e) => set({ source: e.target.value as "" | "portal" | "paper" })}
          >
            <option value="">Online and paper</option>
            <option value="portal">Online only</option>
            <option value="paper">Paper only</option>
          </NativeSelect>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((k) => (
            <Skeleton key={k} className="h-16 rounded-lg" />
          ))}
        </div>
      ) : error ? (
        <Card className="p-5" role="alert">
          <p className="font-medium text-foreground">Forms could not be loaded</p>
          <p className="mt-1 text-sm text-muted-foreground">{formatError(error)}</p>
          <Button variant="outline" className="mt-4 h-11 sm:h-9" onClick={onRetry}>
            Try again
          </Button>
        </Card>
      ) : !data?.items.length ? (
        <Card className="p-6 text-sm text-muted-foreground">{emptyHint}</Card>
      ) : (
        <Card className="divide-y divide-border overflow-hidden">
          {data.items.map((item) => (
            <div key={item.id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:px-5">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-medium text-foreground">
                    {item.employee_name ?? "Employee"}
                  </span>
                  <FormStatusBadge item={item} />
                  {item.source === "paper" && <Badge variant="outline">Paper</Badge>}
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {item.reference_no}
                  {item.version > 1 ? ` · v${item.version}` : ""} · {item.staff_id ?? "—"} ·{" "}
                  {item.id_masked || "—"}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {item.source === "paper" ? "Filed" : "Signed"} {fmtDateTime(item.submitted_at)}
                  {item.window_name ? ` · ${item.window_name}` : ""}
                  {item.source === "portal"
                    ? ` · ${item.changes ? `${item.changes} change${item.changes === 1 ? "" : "s"}` : "no changes"}`
                    : ""}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                {onAcknowledge && item.status === "submitted" && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-11 sm:h-9"
                    disabled={busy === `ack-${item.id}`}
                    onClick={() => void run(`ack-${item.id}`, () => onAcknowledge(item))}
                  >
                    {busy === `ack-${item.id}` ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}
                    Acknowledge
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  className="h-11 sm:h-9"
                  disabled={!item.has_pdf || busy === `pdf-${item.id}`}
                  onClick={() => void run(`pdf-${item.id}`, () => onDownload(item))}
                  aria-label={`Download ${item.reference_no}`}
                >
                  {busy === `pdf-${item.id}` ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
                  PDF
                </Button>
              </div>
            </div>
          ))}
        </Card>
      )}
      {pages > 1 && (
        <PaginationControls
          page={page}
          pages={pages}
          onPageChange={(p) => onFiltersChange({ ...filters, offset: p * REGISTER_PAGE_SIZE })}
        />
      )}
    </section>
  );
}
