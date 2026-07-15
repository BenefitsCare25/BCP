import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { triggerDownload } from "@/lib/download";
import type { AdcApplyResult, AdcPreview } from "@/types";

/** Download the current active roster as a prefilled ADC movement template. */
export async function downloadAdcTemplate(policyYearId: string): Promise<void> {
  const blob = await api.download(`/policy-years/${policyYearId}/adc/template`);
  triggerDownload(blob, "adc-template.xlsx");
}

interface AdcArgs {
  file: File;
  policyYearId: string;
}

function formData(file: File): FormData {
  const fd = new FormData();
  fd.append("file", file);
  return fd;
}

/** Dry-run: classify + diff a movement file without mutating. */
export function useAdcPreview() {
  return useMutation({
    mutationFn: ({ file, policyYearId }: AdcArgs) =>
      api.upload<AdcPreview>(
        `/policy-years/${policyYearId}/adc/preview`,
        formData(file),
      ),
  });
}

/** Apply a movement file (adds/changes/soft-deletes) + re-match + re-flex. */
export function useAdcApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, policyYearId }: AdcArgs) =>
      api.upload<AdcApplyResult>(
        `/policy-years/${policyYearId}/adc/apply`,
        formData(file),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["dependants"] });
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
    },
  });
}
