import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AdcApplyResult, AdcPreview } from "@/types";

interface PreviewArgs {
  file: File;
  policyYearId: string;
  employeeColumnMapping?: Record<string, string | null>;
}

interface ApplyArgs extends PreviewArgs {
  /** Terminate people absent from the file. Off unless the broker ticks it. */
  terminateMissing: boolean;
  /** The preview's fingerprint of that set — the server 409s if it moved. */
  missingDigest: string | null;
  mappingDigest: string | null;
}

/** Dry-run: diff an uploaded member listing against the roster, no mutation. */
export function useListingPreview() {
  return useMutation({
    mutationFn: ({ file, policyYearId, employeeColumnMapping }: PreviewArgs) => {
      const fd = new FormData();
      fd.append("file", file);
      if (employeeColumnMapping) {
        fd.append("employee_column_mapping", JSON.stringify(employeeColumnMapping));
      }
      return api.upload<AdcPreview>(
        `/policy-years/${policyYearId}/adc/preview`,
        fd,
      );
    },
  });
}

/** Apply the listing (adds / changes / soft-deletes) + re-match + re-flex. */
export function useListingApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      policyYearId,
      terminateMissing,
      missingDigest,
      mappingDigest,
      employeeColumnMapping,
    }: ApplyArgs) => {
      const fd = new FormData();
      fd.append("file", file);
      // Always sent explicitly — an omitted field would fall to the server
      // default, which is the same `false`, but a silent default is not
      // something a termination should ever depend on.
      fd.append("terminate_missing", terminateMissing ? "true" : "false");
      if (missingDigest) fd.append("missing_digest", missingDigest);
      if (mappingDigest) fd.append("mapping_digest", mappingDigest);
      if (employeeColumnMapping) {
        fd.append("employee_column_mapping", JSON.stringify(employeeColumnMapping));
      }
      return api.upload<AdcApplyResult>(
        `/policy-years/${policyYearId}/adc/apply`,
        fd,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["dependants"] });
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["eligibility-mappings"] });
      qc.invalidateQueries({ queryKey: ["roster-readiness"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      // A roster movement makes the last-saved insurer listings stale.
      qc.invalidateQueries({ queryKey: ["report-version-status"] });
    },
  });
}
