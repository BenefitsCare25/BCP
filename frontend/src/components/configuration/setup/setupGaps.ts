import type {
  BasisModel,
  Category,
  FormProfile,
  PlanAssignment,
  PolicyNumberAssignment,
  ProductTemplate,
  ProductTerm,
  RateModel,
  SetupAnswers,
  VoluntaryRateBand,
} from "@/types";
import { parseAmount } from "@/lib/basis";
import { policySourceNumbers } from "@/lib/policyNumbers";
import { selectedMemberCover } from "./memberEligibility";

/**
 * Missing-data checks for one product setup.
 *
 * One rule set feeds every surface that reports a gap — the checklist above
 * the setup tabs, the per-tab counts, the badges on each plan assignment, the
 * highlighted inputs and the confirm dialog — so they can never disagree.
 * A gap is a value the placement slip normally states (or the broker must
 * supply) that is still blank. Only `blocking` gaps stop a confirm; the rest
 * are reported so a setup is never confirmed with a silently empty rate.
 */

export type GapSection =
  | "header"
  | "eligibility"
  | "basis_of_cover"
  | "schedule_of_benefits";

export const GAP_SECTION_ORDER: GapSection[] = [
  "header",
  "eligibility",
  "basis_of_cover",
  "schedule_of_benefits",
];

export type AssignmentGapField =
  | "plan"
  | "participation"
  | "basis"
  | "rate"
  | "age_bands"
  | "tier_rates"
  | "annual_premium"
  | "earnings"
  | "dependant_rate";

export const ASSIGNMENT_GAP_LABELS: Record<AssignmentGapField, string> = {
  plan: "Plan type",
  participation: "Participation",
  basis: "Basis of cover",
  rate: "Premium rate",
  age_bands: "Age-band rates",
  tier_rates: "Tier rates",
  annual_premium: "Annual premium",
  earnings: "Estimated annual earnings",
  dependant_rate: "Dependant premium rate",
};

export interface AssignmentGap {
  field: AssignmentGapField;
  /** Extra precision, e.g. the tiers that have no rate ("ES, EF"). */
  detail?: string;
}

export interface SetupGap {
  key: string;
  section: GapSection;
  label: string;
  /** The employee category the gap belongs to, when it is assignment-level. */
  where?: string;
  hint?: string;
  categoryId?: string;
  /** Header or eligibility field id, for jumping straight to the input. */
  fieldId?: string;
  /** Confirm is refused while this gap is open. */
  blocking?: boolean;
}

export interface AssignmentGapContext {
  basisModel: BasisModel;
  rateModel: RateModel;
  /** The product's member cover includes a spouse or child. */
  dependantsCovered: boolean;
  /** The product has one plan, which a category with no plan type uses. */
  singlePlan: boolean;
}

const positive = (value: unknown): boolean => {
  const n = parseAmount(value);
  return n !== null && n > 0;
};

const blank = (value: unknown): boolean =>
  (Array.isArray(value) ? value.join("") : String(value ?? "")).trim() === "";

const normalize = (value: string | null | undefined) =>
  String(value ?? "").trim().toLocaleLowerCase().replace(/\s+/g, " ");

type AssignmentWithBands = PlanAssignment & {
  rate_basis?: string | null;
  voluntary_rates?: VoluntaryRateBand[] | null;
};

// A voluntary category prices by age band when it carries a voluntary_rates table
// (or is flagged age_banded). Compulsory / flat-voluntary plans don't.
export function isAgeBanded(c: Category): boolean {
  if (c.participation_model !== "voluntary") return false;
  const pa = (c.plan_assignments ?? {}) as AssignmentWithBands;
  return pa.rate_basis === "age_banded" || !!pa.voluntary_rates;
}

function tierRateGaps(pa: PlanAssignment): AssignmentGap | null {
  const tiers = Object.entries(pa.rate_tiers ?? {});
  if (tiers.length === 0) return { field: "tier_rates" };
  const unpriced = tiers.filter(([, cell]) => !positive(cell?.rate)).map(([code]) => code);
  if (unpriced.length === 0) return null;
  return {
    field: "tier_rates",
    detail: unpriced.length === tiers.length ? undefined : unpriced.join(", "),
  };
}

const hasPricedTier = (pa: PlanAssignment) =>
  Object.values(pa.rate_tiers ?? {}).some((cell) => positive(cell?.rate));

/** What one plan assignment (an employee category row) is still missing. */
export function assignmentGaps(
  category: Category,
  ctx: AssignmentGapContext,
): AssignmentGap[] {
  const pa = (category.plan_assignments ?? {}) as AssignmentWithBands;
  const gaps: AssignmentGap[] = [];
  // With one plan a blank plan type resolves to it (plan_hydration's sole
  // plan); with several, the category has no schedule until one is chosen.
  if (blank(pa.plan_code) && !ctx.singlePlan) gaps.push({ field: "plan" });
  if (blank(category.participation_model)) gaps.push({ field: "participation" });

  if (ctx.basisModel === "sum_assured") {
    if (blank(pa.basis)) gaps.push({ field: "basis" });
    if (isAgeBanded(category)) {
      if (!(pa.voluntary_rates ?? []).some((band) => positive(band.rate))) {
        gaps.push({ field: "age_bands" });
      }
    } else if (!positive(pa.premium_rate)) {
      gaps.push({ field: "rate" });
    }
    return gaps;
  }

  if (ctx.rateModel === "tiered") {
    const tierGap = tierRateGaps(pa);
    if (tierGap) gaps.push(tierGap);
  } else if (ctx.rateModel === "flat") {
    if (!positive(pa.annual_premium)) gaps.push({ field: "annual_premium" });
  } else if (ctx.rateModel === "earnings_based") {
    if (!positive(pa.estimated_annual_earnings)) gaps.push({ field: "earnings" });
    if (!positive(pa.premium_rate)) gaps.push({ field: "rate" });
  } else if (!positive(pa.premium_rate) && !hasPricedTier(pa)) {
    gaps.push({ field: "rate" });
  }

  // Only a per-member product prices dependants by their own rate; tiered,
  // annual-flat and earnings-based products have no such figure.
  const dependantMode = category.participation_detail?.dependant;
  if (
    ctx.dependantsCovered &&
    ctx.rateModel === "per_member" &&
    (dependantMode === "compulsory" || dependantMode === "voluntary") &&
    !hasPricedTier(pa) &&
    !positive(pa.dependant_rate)
  ) {
    gaps.push({ field: "dependant_rate" });
  }
  return gaps;
}

export function assignmentGapText(gap: AssignmentGap): string {
  const label = ASSIGNMENT_GAP_LABELS[gap.field];
  return gap.detail ? `${label} (${gap.detail})` : label;
}

/** Category keys whose rate the slip prints as blank — the gap is the slip's,
 *  not an extraction miss, so the broker knows to ask the insurer. Only the
 *  rate table applied to this benefit year counts, and a tier-priced row has
 *  its rates in `rate_tiers`, not `rate`. */
function slipBlankRateKeys(answers: SetupAnswers): Set<string> {
  const keys = new Set<string>();
  for (const schedule of answers.source_rate_schedules ?? []) {
    if (!schedule.selected) continue;
    for (const row of schedule.rates ?? []) {
      if (row.rate_tiers && Object.keys(row.rate_tiers).length > 0) continue;
      if (!positive(row.rate)) keys.add(normalize(row.key));
    }
  }
  return keys;
}

const NO_ENTRY_AGE_PROFILES: FormProfile[] = ["travel", "statutory"];

export interface SetupGapInput {
  answers: SetupAnswers;
  template: ProductTemplate;
  categories: Category[];
  term: ProductTerm | null;
  policyMappings: PolicyNumberAssignment[];
}

export function setupGaps({
  answers,
  template,
  categories,
  term,
  policyMappings,
}: SetupGapInput): SetupGap[] {
  const gaps: SetupGap[] = [];
  const fieldLabel = (id: string, fallback: string) =>
    [...template.header_fields, ...template.eligibility_fields].find((f) => f.id === id)
      ?.label ?? fallback;
  const add = (gap: Omit<SetupGap, "key"> & { key?: string }) =>
    gaps.push({ ...gap, key: gap.key ?? `${gap.section}:${gap.label}` });

  // Header & Policy
  const header = answers.header ?? {};
  for (const [id, fallback] of [
    ["policyholder", "Policyholder"],
    ["insurer", "Insurer"],
    ["admin_basis", "Type of Administration"],
  ] as const) {
    if (template.header_fields.some((f) => f.id === id) && blank(header[id])) {
      add({ section: "header", label: fieldLabel(id, fallback), fieldId: id });
    }
  }
  const sourceNumbers = policySourceNumbers(String(header.policy_no ?? ""));
  if (sourceNumbers.length === 0 && !policyMappings.some((m) => !blank(m.policy_number))) {
    add({
      section: "header",
      label: "Policy number",
      fieldId: "policy_no",
      hint: "The slip doesn't state one. Add it once the insurer issues the policy.",
    });
  }
  if (term) {
    const terms = answers.policy_terms ?? {};
    const start = terms.coverage_start ?? term.coverage_start;
    const end = terms.coverage_end ?? term.coverage_end;
    if (!start || !end) {
      add({ section: "header", label: "Coverage period", blocking: true });
    }
    if (term.line === "life" && template.form_profile === "sum_assured") {
      if (!positive(terms.free_cover_limit ?? term.free_cover_limit)) {
        add({
          section: "header",
          label: "Free cover limit (non-evidence limit)",
          hint: "Sum insured above it needs underwriting.",
        });
      }
      if (!positive(terms.nel_age_limit ?? term.nel_age_limit)) {
        add({ section: "header", label: "NEL age" });
      }
    }
  }

  // Eligibility
  const eligibility = answers.eligibility ?? {};
  const covered = selectedMemberCover(eligibility.member_cover_eligibility);
  const dependantsCovered = covered.has("Spouse") || covered.has("Child");
  const eligibilityChecks: [string, string, boolean, string?][] = [
    ["eligibility", "Eligibility", true],
    ["member_cover_eligibility", "Member cover eligibility", true],
    [
      "last_entry_age",
      "Last entry age",
      !NO_ENTRY_AGE_PROFILES.includes(template.form_profile),
    ],
    [
      "spouse_age_limit",
      "Spouse age limit",
      covered.has("Spouse"),
      "Not stated on the slip — enter the insurer's limit, or untick Spouse.",
    ],
    [
      "child_age_limit",
      "Child age limit",
      covered.has("Child"),
      "Not stated on the slip — enter the insurer's limit, or untick Child.",
    ],
  ];
  for (const [id, fallback, applies, hint] of eligibilityChecks) {
    if (!applies || !template.eligibility_fields.some((f) => f.id === id)) continue;
    if (blank(eligibility[id])) {
      add({ section: "eligibility", label: fieldLabel(id, fallback), hint, fieldId: id });
    }
  }

  // Employee Category & Plan Type
  const ctx: AssignmentGapContext = {
    basisModel: template.basis_model,
    rateModel: template.rate_model,
    dependantsCovered,
    singlePlan: answers.plans.filter((plan) => plan.selected).length <= 1,
  };
  const slipBlank = slipBlankRateKeys(answers);
  for (const category of categories) {
    const where = category.display_name;
    for (const gap of assignmentGaps(category, ctx)) {
      const fromSlip =
        gap.field === "rate" &&
        (slipBlank.has(normalize(category.display_name)) ||
          slipBlank.has(normalize(category.raw_description)));
      add({
        key: `basis_of_cover:${category.id}:${gap.field}`,
        section: "basis_of_cover",
        label: assignmentGapText(gap),
        where,
        categoryId: category.id,
        hint: fromSlip ? "Blank on the slip — get it from the insurer." : undefined,
      });
    }
  }
  if (
    dependantsCovered &&
    categories.length > 0 &&
    template.basis_model !== "sum_assured" &&
    categories.every((c) => !c.participation_detail?.dependant)
  ) {
    add({
      section: "basis_of_cover",
      label: "Dependant participation",
      hint: "Member cover includes dependants, but no employee category covers them. Set it on each plan assignment, or untick Spouse and Child in Eligibility.",
    });
  }

  // SOB
  if ((answers.sob?.items.length ?? 0) === 0) {
    add({ section: "schedule_of_benefits", label: "Schedule of benefits" });
  }
  if (blank(answers.cover_description)) {
    add({ section: "schedule_of_benefits", label: "Cover description" });
  }

  return gaps.sort(
    (a, b) =>
      Number(Boolean(b.blocking)) - Number(Boolean(a.blocking)) ||
      GAP_SECTION_ORDER.indexOf(a.section) - GAP_SECTION_ORDER.indexOf(b.section),
  );
}

export function gapCountsBySection(gaps: SetupGap[]): Partial<Record<GapSection, number>> {
  const counts: Partial<Record<GapSection, number>> = {};
  for (const gap of gaps) counts[gap.section] = (counts[gap.section] ?? 0) + 1;
  return counts;
}
