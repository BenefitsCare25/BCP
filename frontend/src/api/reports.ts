import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { triggerDownload } from "@/lib/download";
import { useSession } from "@/stores/session";

export interface ReportReadiness {
  insurers: string[];
  products_without_insurer: string[];
  plans_missing_report_label: { product_code: string; plan_code: string }[];
  employees_missing_nric: number;
  employees_missing_member_id: Record<string, number>;
  employee_count: number;
}

export function useReportReadiness(policyYearId: string | null) {
  // Scope the key by active client so a tenant switch reads a fresh cache
  // (matches every other tenant-scoped hook in the app).
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["report-readiness", policyYearId, cid],
    queryFn: () =>
      api.get<ReportReadiness>(
        `/policy-years/${policyYearId}/reports/readiness`,
      ),
    enabled: Boolean(policyYearId),
  });
}

// ── Composite report workbooks ───────────────────────────────────────────────
//
// One download, several NAMED sheets. These replaced both the zip "report sets"
// and the loose per-file rows: a submission is one artifact, and a workbook is
// the only shape of it that keeps its own table of contents once it is emailed
// on.

export interface ReportSheet {
  title: string;
  description: string;
}

export interface ReportWorkbook {
  key: string;
  label: string;
  description: string;
  requires_insurer: boolean;
  supports_masking: boolean;
  supports_date_range: boolean;
  supports_employee_status: boolean;
  /** Empty unless `requires_insurer` — served so the picker offers exactly the
   *  insurers the download accepts. */
  insurers: string[];
  /** SERVED, never a constant here. The page prints what is inside a workbook
   *  before it is downloaded and a broker files against that; a sheet added on
   *  the server must not need a matching edit here to be described. */
  sheets: ReportSheet[];
  /** The retained series a download of this workbook files a copy into, or null
   *  when it only writes an audit row. Served for the same reason every other
   *  control here is: derived client-side, the page would promise a submission
   *  record the server does not keep. */
  retained_type: string | null;
}

export function useReportWorkbooks(policyYearId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["report-workbooks", policyYearId, cid],
    queryFn: () =>
      api.get<ReportWorkbook[]>(
        `/policy-years/${policyYearId}/reports/workbooks`,
      ),
    enabled: Boolean(policyYearId),
  });
}

// ── Report versioning ────────────────────────────────────────────────────────

export type ReportMode = "versioned" | "latest";

export interface ReportSpec {
  report_type: string;
  label: string;
  mode: ReportMode;
  scope: "insurer" | "window" | null;
  fmt: "xlsx" | "docx";
  has_movement: boolean;
}

export interface ReportVersion {
  id: string;
  report_type: string;
  scope_key: string | null;
  version_no: number;
  mode: ReportMode;
  label: string | null;
  file_name: string;
  size_bytes: number;
  summary: {
    masked?: boolean;
    member_count?: number;
    employee_count?: number;
    dependant_count?: number;
    manifest_hash?: string;
  };
  generated_by_user_id: string | null;
  /** Display name of whoever generated it — resolved server-side. The id has
   *  always been stored and rendered nowhere, so "who sent this" was answerable
   *  only by looking up a UUID by hand. */
  generated_by: string | null;
  /** Human label for `report_type`. The drawer merges a live series with the
   *  superseded ones it replaced, so rows have to name which they came from. */
  report_label: string;
  created_at: string | null;
}

export interface ReportVersionStatus {
  latest: ReportVersion | null;
  is_stale: boolean;
  has_movement: boolean;
}

export interface MovementSummary {
  added: number;
  removed: number;
  changed: number;
}

/** How much the roster moved since a saved version.
 *
 * Its own endpoint rather than a field on the status poll: the counts come
 * from the same full diff the movement workbook runs, and `/status` is polled
 * by EVERY report row whether stale or not. This narrows that to stale rows
 * only — which on a live roster is not rare, so it is a reduction rather than
 * an elimination. Keep it off `/status`. */
export function useMovementSummary(versionId: string | null, enabled: boolean) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["movement-summary", versionId, cid],
    queryFn: () =>
      api.get<MovementSummary>(`/report-versions/${versionId}/movement-summary`),
    enabled: enabled && Boolean(versionId),
    staleTime: 30_000,
    // The badge degrades to its old wording if this fails, so the failure is
    // already handled; without this the global QueryCache.onError would still
    // push it into the notification centre — an alert about nothing.
    meta: { localErrorHandling: true },
  });
}

export interface CreateReportVersionInput {
  report_type: string;
  insurer?: string;
  masked?: boolean;
  window_id?: string;
  label?: string;
}

/** The report-versioning classification (mode/scope/movement per report). */
export function useReportRegistry() {
  return useQuery({
    queryKey: ["report-registry"],
    queryFn: () => api.get<ReportSpec[]>("/report-registry"),
    staleTime: Infinity,
  });
}

export function useReportVersions(
  policyYearId: string | null,
  reportType: string,
  scopeKey: string | null,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["report-versions", policyYearId, reportType, scopeKey, cid],
    queryFn: () => {
      const p = new URLSearchParams({ report_type: reportType });
      if (scopeKey) p.set("scope_key", scopeKey);
      return api.get<ReportVersion[]>(
        `/policy-years/${policyYearId}/report-versions?${p}`,
      );
    },
    enabled: Boolean(policyYearId),
  });
}

export function useReportVersionStatus(
  policyYearId: string | null,
  reportType: string,
  scopeKey: string | null,
  enabled = true,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["report-version-status", policyYearId, reportType, scopeKey, cid],
    queryFn: () => {
      const p = new URLSearchParams({ report_type: reportType });
      if (scopeKey) p.set("scope_key", scopeKey);
      return api.get<ReportVersionStatus>(
        `/policy-years/${policyYearId}/report-versions/status?${p}`,
      );
    },
    enabled: enabled && Boolean(policyYearId),
  });
}

export function useCreateReportVersion(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateReportVersionInput) =>
      api.post<ReportVersion & { unchanged: boolean }>(
        `/policy-years/${policyYearId}/report-versions`,
        input,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["report-versions"] });
      qc.invalidateQueries({ queryKey: ["report-version-status"] });
    },
  });
}

export async function downloadReportVersion(
  versionId: string,
  filename: string,
): Promise<void> {
  const blob = await api.download(`/report-versions/${versionId}/download`);
  triggerDownload(blob, filename);
}

/** Movement (adds/deletions/changes) of a version vs a baseline. `since`:
 *  a prior version id, "live", or undefined (= the previous version). */
export async function downloadMovement(
  versionId: string,
  filename: string,
  since?: string,
): Promise<void> {
  const q = since ? `?since=${encodeURIComponent(since)}` : "";
  const blob = await api.download(`/report-versions/${versionId}/movement${q}`);
  triggerDownload(blob, filename);
}
