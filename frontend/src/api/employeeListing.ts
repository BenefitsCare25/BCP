import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AdcPreview } from "@/types";

export interface ListingColumn {
  letter: string;
  header: string;
  role: string;
}

export interface ListingBlock {
  index: number;
  kind: "employee" | "dependant" | "product";
  banner: string;
  code_hint: string | null;
  columns: ListingColumn[];
}

export interface ListingCategoryOption {
  product_code: string;
  category_id: string;
  category_label: string;
  plan_code: string | null;
}

export interface ListingLabel {
  block: number;
  key: string;
  label: string;
  employees: number;
  dependants: number;
  plans: string[];
  choice: ListingCategoryOption | null;
  not_covered: boolean;
  confidence: number;
  source: "saved" | "suggested" | "reviewed" | "none";
}

export interface ListingCheck {
  code: string;
  message: string;
  count: number;
  rows: number[];
}

export interface JoinerRule {
  category_id: string;
  category_label: string;
  rule: string;
  listed: number;
  exceptions: number;
}

export interface JoinerProductRules {
  product_code: string;
  attributes: string[];
  rules: JoinerRule[];
  exceptions: number;
}

export interface ListingPreview {
  layout: {
    sheet: string;
    header_row: number;
    reference_date: string | null;
    blocks: ListingBlock[];
    trailing: ListingColumn[];
  };
  products: { code: string; display_name: string }[];
  block_products: Record<string, string[]>;
  labels: ListingLabel[];
  options: Record<string, ListingCategoryOption[]>;
  members: AdcPreview;
  issues: { row: number | null; field: string | null; code: string; message: string }[];
  checks: ListingCheck[];
  reused_profile: boolean;
  joiner_rules: JoinerProductRules[];
}

export interface LabelChoice {
  category_id?: string | null;
  not_covered?: boolean;
}

export interface ListingMapping {
  block_products: Record<string, string[]>;
  labels: Record<string, Record<string, LabelChoice>>;
}

export interface ListingApplyResult {
  added: number;
  changed: number;
  deleted: number;
  missing_terminated: number;
  unchanged: number;
  rematched: number;
  assignments: number;
  dependant_assignments: number;
  profile_saved: boolean;
  flex_errors: string[];
  joiner_rules_written: number;
  underwriting_updated: number;
}

/** The decisions a preview currently shows, in the shape apply expects. */
export function mappingFromPreview(preview: ListingPreview): ListingMapping {
  const labels: ListingMapping["labels"] = {};
  for (const label of preview.labels) {
    const block = (labels[String(label.block)] ??= {});
    if (label.not_covered) block[label.key] = { not_covered: true };
    else if (label.choice) block[label.key] = { category_id: label.choice.category_id };
  }
  return { block_products: { ...preview.block_products }, labels };
}

interface PreviewArgs {
  file: File;
  policyYearId: string;
  mapping?: ListingMapping;
}

/** Read a company Employee Listing and suggest its mapping. No mutation. */
export function useEmployeeListingPreview() {
  return useMutation({
    // The caller owns every outcome: "not_employee_listing" is the routine
    // hand-off to the template sync, not a failure to report.
    meta: { localErrorHandling: true },
    mutationFn: ({ file, policyYearId, mapping }: PreviewArgs) => {
      const fd = new FormData();
      fd.append("file", file);
      if (mapping) fd.append("mapping", JSON.stringify(mapping));
      return api.upload<ListingPreview>(
        `/policy-years/${policyYearId}/employee-listing/preview`,
        fd,
      );
    },
  });
}

interface ApplyArgs {
  file: File;
  policyYearId: string;
  mapping: ListingMapping;
  terminateMissing: boolean;
  missingDigest: string | null;
}

/** Write members and each person's listed cover, then re-match. */
export function useEmployeeListingApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, policyYearId, mapping, terminateMissing, missingDigest }: ApplyArgs) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("mapping", JSON.stringify(mapping));
      fd.append("terminate_missing", terminateMissing ? "true" : "false");
      if (missingDigest) fd.append("missing_digest", missingDigest);
      return api.upload<ListingApplyResult>(
        `/policy-years/${policyYearId}/employee-listing/apply`,
        fd,
      );
    },
    onSuccess: () => {
      for (const key of [
        "employees", "dependants", "entity-vocab", "match-results",
        "eligibility-mappings", "roster-readiness", "categories",
        "flex-membership", "flex-coverage", "benefit-statement", "report-version-status",
      ]) {
        qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}
