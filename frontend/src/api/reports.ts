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
