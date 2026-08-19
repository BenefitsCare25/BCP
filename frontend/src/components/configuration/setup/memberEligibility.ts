import type { Category, SetupAnswers, TemplateField } from "@/types";

export const MEMBER_COVER_OPTIONS = ["Employee", "Spouse", "Child"] as const;

const SPOUSE_TIER_CODES = new Set(["ES", "EF", "SO", "SC", "FO"]);
const CHILD_TIER_CODES = new Set(["EC", "EF", "CO", "SC", "FO"]);
const SPOUSE_RE = /\bspou(?:se|ses)\b|\bwives\b|\bhusband\b/i;
const CHILD_RE = /\bchild(?:ren)?\b|\bson\b|\bdaughter\b/i;
const DEPENDANT_RE = /\bdependan[td]s?\b/i;

function splitValue(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  return String(value ?? "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function ordered(values: Iterable<string>): string {
  const selected = new Set(values);
  return MEMBER_COVER_OPTIONS.filter((option) => selected.has(option)).join(",");
}

function markText(selected: Set<string>, text: string) {
  const hasSpouse = SPOUSE_RE.test(text);
  const hasChild = CHILD_RE.test(text);
  const hasDependant = DEPENDANT_RE.test(text);
  if (hasSpouse) selected.add("Spouse");
  if (hasChild) selected.add("Child");
  if (hasDependant && !hasSpouse && !hasChild) {
    selected.add("Spouse");
    selected.add("Child");
  }
}

function markTierCodes(selected: Set<string>, source: unknown) {
  if (!source || typeof source !== "object") return;
  for (const raw of Object.keys(source as Record<string, unknown>)) {
    const code = raw.trim().toUpperCase();
    if (SPOUSE_TIER_CODES.has(code)) selected.add("Spouse");
    if (CHILD_TIER_CODES.has(code)) selected.add("Child");
  }
}

function markCategory(selected: Set<string>, category: Category) {
  const detail = category.participation_detail;
  const assignments = category.plan_assignments ?? {};
  markText(
    selected,
    [
      category.display_name,
      category.raw_description,
      detail?.raw,
      String((assignments as Record<string, unknown>).member_scope ?? ""),
    ].join(" "),
  );
  if (detail?.dependant) markText(selected, "dependants");
  if ((assignments as Record<string, unknown>).dependant_rate != null) {
    markText(selected, "dependants");
  }
  markTierCodes(selected, (assignments as Record<string, unknown>).rate_tiers);
  markTierCodes(selected, (assignments as Record<string, unknown>).tier_counts);
  markTierCodes(selected, (assignments as Record<string, unknown>).tier_labels);
}

export function normalizeMemberCover(value: unknown, inferred: string[] = []) {
  const raw = splitValue(value);
  const selected = new Set<string>(raw.length ? raw : ["Employee", ...inferred]);
  selected.add("Employee");
  return ordered(selected);
}

export function selectedMemberCover(value: unknown): Set<string> {
  return new Set(splitValue(normalizeMemberCover(value)));
}

export function hasSelectedDependants(value: unknown): boolean {
  const selected = selectedMemberCover(value);
  return selected.has("Spouse") || selected.has("Child");
}

export function inferMemberCoverFromAnswers(answers: SetupAnswers): string[] {
  const selected = new Set<string>();
  for (const row of answers.categories ?? []) {
    markText(selected, `${row.category} ${row.participation} ${row.plan_code}`);
    markTierCodes(selected, row.tiers);
  }
  for (const byTier of Object.values(answers.rate_table ?? {})) {
    markTierCodes(selected, byTier);
  }
  return MEMBER_COVER_OPTIONS.filter((option) => selected.has(option));
}

export function inferMemberCoverFromCategories(categories: Category[] = []): string[] {
  const selected = new Set<string>();
  for (const category of categories) markCategory(selected, category);
  return MEMBER_COVER_OPTIONS.filter((option) => selected.has(option));
}

export function prepareEligibilityField(field: TemplateField): TemplateField {
  if (field.id !== "member_cover_eligibility") return field;
  return { ...field, options: [...MEMBER_COVER_OPTIONS] };
}
