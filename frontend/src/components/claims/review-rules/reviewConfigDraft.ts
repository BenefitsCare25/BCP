import type { ClaimReviewConfigInput } from "@/api/claims";

export interface EditorTarget {
  key: string;
  configId: string | null;
  expectedUpdatedAt: string | null;
  draft: ClaimReviewConfigInput;
}

export const MAX_FIELD_MAPS = 30;
export const MAX_AI_RULES = 60;
export const MAX_REQUIRED_DOCUMENTS = 15;

export function prepareReviewConfigDraft(
  draft: ClaimReviewConfigInput,
): { ok: true; body: ClaimReviewConfigInput } | { ok: false; error: string } {
  const fieldMaps = draft.field_maps.map((mapping) => ({
    ...mapping,
    portal_field: mapping.portal_field.trim(),
    document_field: mapping.document_field.trim(),
  }));
  if (fieldMaps.some((mapping) => !mapping.portal_field || !mapping.document_field)) {
    return { ok: false, error: "Complete or remove every field mapping." };
  }

  const fieldKeys = fieldMaps.map((mapping) => mapping.portal_field.toLowerCase());
  if (new Set(fieldKeys).size !== fieldKeys.length) {
    return { ok: false, error: "Each claim field can be mapped only once." };
  }
  if (
    fieldMaps.some(
      (mapping) =>
        mapping.mode === "numeric" &&
        (mapping.tolerance == null ||
          !Number.isFinite(mapping.tolerance) ||
          mapping.tolerance < 0),
    )
  ) {
    return { ok: false, error: "Numeric tolerances must be zero or more." };
  }

  const rules = draft.ai_rules.map((rule) => ({
    ...rule,
    rule: rule.rule.trim(),
    category: rule.category.trim() || "general",
  }));
  if (rules.some((rule) => !rule.rule)) {
    return { ok: false, error: "Complete or remove every business rule." };
  }

  const requiredDocuments = Array.from(
    new Map(
      draft.required_documents
        .map((document) => document.trim())
        .filter(Boolean)
        .map((document) => [document.toLowerCase(), document]),
    ).values(),
  );

  return {
    ok: true,
    body: {
      ...draft,
      field_maps: fieldMaps,
      ai_rules: rules,
      required_documents: requiredDocuments,
    },
  };
}
