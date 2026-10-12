/** The blank QR form's draft, checks and figures.
 *
 * The checks mirror the server's so an employee hears about a problem on the
 * step it belongs to, before signing; the server repeats every one of them,
 * and its answer is the one that counts. Figures use the same rules as the
 * member e-form: the employee's share is their own-cover share (when they pay
 * one) plus the family portion for the composition they tick. */
import type { CohortTier } from "@/api/enrollment";
import type { ContributionTier, FormClause } from "@/api/enrollmentForms";
import type {
  DependantInput,
  ParticularsInput,
  ProductChoiceInput,
  PublicCohort,
  PublicForm,
  PublicProduct,
} from "@/api/publicLinks";

export type StepKey = "details" | "family" | "benefits" | "declarations" | "review";

export interface LifeEventInput {
  kind: "" | "new_hire" | "marriage" | "birth" | "other";
  date: string;
}

export interface Draft {
  requestId: string;
  particulars: ParticularsInput;
  dependants: DependantInput[];
  products: Record<string, ProductChoiceInput>;
  lifeEvent: LifeEventInput;
  accepted: string[];
  signature: string;
  website: string;
}

export interface Problem {
  step: StepKey;
  text: string;
  values?: (string | number)[];
}

export function emptyDraft(form: PublicForm): Draft {
  const products: Record<string, ProductChoiceInput> = {};
  for (const p of form.products) {
    products[p.product_code] = {
      product_code: p.product_code,
      category_id: p.cohorts.length === 1 ? p.cohorts[0].category_id : "",
      action: "",
      tier_key: "",
      cover_self: false,
      covered: [],
    };
  }
  return {
    requestId: crypto.randomUUID(),
    particulars: {
      name: "", id_no: "", staff_id: "", gender: "", dob: "", contact_no: "", email: "",
      job_grade: "", date_of_hire: "", marital_status: "",
    },
    dependants: [],
    products,
    lifeEvent: { kind: "", date: "" },
    accepted: [],
    signature: "",
    website: "",
  };
}

export function newDependant(relationship: "spouse" | "child"): DependantInput {
  return {
    key: `d${Math.random().toString(36).slice(2, 8)}`,
    action: "add",
    relationship,
    name: "", gender: "", id_no: "", dob: "", occupation: "", nationality: "", residence: "",
    child_confirmed: false,
  };
}

const NRIC_SHAPE = /^[STFGM]\d{7}[A-Z]$/;
const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_SHAPE = /^[0-9+()\- ]{6,20}$/;

export function squash(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function cohortOf(product: PublicProduct, choice: ProductChoiceInput | undefined): PublicCohort | undefined {
  return product.cohorts.find(c => c.category_id === choice?.category_id);
}

export function tierOf(cohort: PublicCohort | undefined, key: string): CohortTier | undefined {
  return cohort?.tier_set.tiers.find(t => t.key === key);
}

/** Dependants a product's plan can cover: those being added or kept. */
export function coverable(draft: Draft): DependantInput[] {
  return draft.dependants.filter(d => d.action !== "withdraw");
}

export function composition(spouses: number, children: number): "EO" | "ES" | "EC" | "EF" {
  if (spouses && children) return "EF";
  if (spouses) return "ES";
  return children ? "EC" : "EO";
}

/** [full annual premium, employee's share] — null where not shown. */
export function price(
  tier: ContributionTier | undefined,
  spouses: number,
  children: number,
  own: boolean,
): [number | null, number | null] {
  if (!tier) return [null, null];
  const n = spouses + children;
  if (tier.mode === "tiered") {
    const full = tier.premium[composition(spouses, children)] ?? null;
    const role = spouses && children ? "both" : spouses ? "spouse" : children ? "child" : null;
    const family = role ? tier.family[role] : 0;
    // Company-paid own cover with no family on it: nothing for the employee to pay.
    if (!own && n === 0) return [full, 0];
    const ownPart = own ? tier.employee : 0;
    if (ownPart === null || family === undefined) return [full, null];
    return [full, Math.round(((ownPart ?? 0) + (family ?? 0)) * 100) / 100];
  }
  const base = tier.premium.EO;
  const full = base === undefined ? null : Math.round((base + (tier.premium_per_dependant ?? 0) * n) * 100) / 100;
  const ownPart = own ? tier.employee : 0;
  if (ownPart === null) return [full, null];
  return [full, Math.round(((ownPart ?? 0) + (tier.per_dependant ?? 0) * n) * 100) / 100];
}

export function contributionTier(form: PublicForm, tierKey: string): ContributionTier | undefined {
  for (const c of form.contributions) {
    const hit = c.tiers.find(t => t.tier_key === tierKey);
    if (hit) return hit;
  }
  return undefined;
}

export interface ChoiceFigures {
  full: number | null;
  share: number | null;
  composition: "EO" | "ES" | "EC" | "EF";
}

export function choiceFigures(form: PublicForm, draft: Draft, product: PublicProduct): ChoiceFigures | null {
  const choice = draft.products[product.product_code];
  if (!choice || choice.action !== "enrol") return null;
  const cohort = cohortOf(product, choice);
  const tier = tierOf(cohort, choice.tier_key);
  if (!cohort || !tier) return null;
  const covered = draft.dependants.filter(d => choice.covered.includes(d.key));
  const spouses = covered.filter(d => d.relationship === "spouse").length;
  const children = covered.length - spouses;
  const own = cohort.tier_set.employee_participation === "voluntary" || !tier.is_baseline;
  const [full, share] = price(contributionTier(form, tier.key), spouses, children, own);
  return { full, share, composition: composition(spouses, children) };
}

export function canWithdraw(cohort: PublicCohort | undefined): boolean {
  if (!cohort) return true;
  const ts = cohort.tier_set;
  return ts.employee_participation === "voluntary"
    || ts.tiers.some(t => t.dependant_participation === "voluntary");
}

/** Age next birthday on `ref`. */
export function ageNextBirthday(dob: string, ref: string): number | null {
  const d = new Date(`${dob}T00:00:00`);
  const r = new Date(`${ref}T00:00:00`);
  if (Number.isNaN(d.getTime()) || Number.isNaN(r.getTime())) return null;
  let age = r.getFullYear() - d.getFullYear();
  if (r.getMonth() < d.getMonth() || (r.getMonth() === d.getMonth() && r.getDate() < d.getDate())) age -= 1;
  return age + 1;
}

export function applicableClauses(form: PublicForm, draft: Draft): FormClause[] {
  const named = draft.dependants.length > 0;
  return form.clauses.filter(c => c.applies_to === "all" || named);
}

function detailsProblems(form: PublicForm, draft: Draft): Problem[] {
  const p = draft.particulars;
  const out: Problem[] = [];
  const step = "details" as const;
  if (squash(p.name).length < 2) out.push({ step, text: "Enter your full name as in your NRIC or FIN." });
  if (!NRIC_SHAPE.test(p.id_no.trim().toUpperCase())) out.push({ step, text: "Enter a valid NRIC or FIN number." });
  if (!squash(p.staff_id)) out.push({ step, text: "Enter your staff ID." });
  if (!p.gender) out.push({ step, text: "Choose your sex." });
  if (!PHONE_SHAPE.test(p.contact_no.trim())) out.push({ step, text: "Enter a contact number." });
  if (!EMAIL_SHAPE.test(p.email.trim())) out.push({ step, text: "Enter a valid email address." });
  const lifeEventWindow = form.window_type === "new_hire" || form.window_type === "life_event";
  if (lifeEventWindow && (!draft.lifeEvent.kind || !draft.lifeEvent.date)) {
    out.push({ step, text: "Tell us what changed and when." });
  }
  return out;
}

function familyProblems(form: PublicForm, draft: Draft): Problem[] {
  const out: Problem[] = [];
  const step = "family" as const;
  const spouses = draft.dependants.filter(d => d.relationship === "spouse").length;
  const children = draft.dependants.length - spouses;
  if (spouses > 1) out.push({ step, text: "Name only one spouse." });
  if (children > form.max_children) out.push({ step, text: "Name at most {0} children on one form.", values: [form.max_children] });
  for (const d of draft.dependants) {
    const who = squash(d.name) || (d.relationship === "spouse" ? "your spouse" : "your child");
    if (squash(d.name).length < 2 || !d.gender || d.id_no.trim().length < 5 || !d.dob) {
      out.push({ step, text: "Complete the details for {0}.", values: [who] });
    }
    if (d.relationship === "child" && d.action === "add" && !d.child_confirmed) {
      out.push({ step, text: "Confirm that {0} is unmarried, not working full time and not serving National Service.", values: [who] });
    }
  }
  return out;
}

function benefitProblems(form: PublicForm, draft: Draft): Problem[] {
  const out: Problem[] = [];
  const step = "benefits" as const;
  const ref = draft.lifeEvent.date || form.policy_start || "";
  for (const product of form.products) {
    const name = product.product_name ?? product.product_code;
    const choice = draft.products[product.product_code];
    const cohort = cohortOf(product, choice);
    if (!choice?.category_id || !cohort) { out.push({ step, text: "Choose your category for {0}.", values: [name] }); continue; }
    if (!choice.action) { out.push({ step, text: "Choose an option for {0}.", values: [name] }); continue; }
    if (choice.action === "enrol") {
      const tier = tierOf(cohort, choice.tier_key);
      if (!tier) { out.push({ step, text: "Choose a plan for {0}.", values: [name] }); continue; }
      for (const d of draft.dependants.filter(dep => choice.covered.includes(dep.key))) {
        const limits = product.age_limits[d.relationship] ?? {};
        const age = d.dob && ref ? ageNextBirthday(d.dob, ref) : null;
        if (age !== null && ((limits.min !== undefined && age < limits.min) || (limits.max !== undefined && age > limits.max))) {
          out.push({ step, text: "{0} is outside the age limit for {1}.", values: [squash(d.name), name] });
        }
      }
    }
    if (choice.action === "withdraw" && !choice.cover_self && choice.covered.length === 0) {
      out.push({ step, text: "Choose who to withdraw from {0}.", values: [name] });
    }
  }
  for (const rule of form.rules) {
    const mine = draft.products[rule.product_code];
    const needed = draft.products[rule.requires_product_code];
    if (mine?.action !== "enrol" || !needed) continue;
    const a = form.products.find(p => p.product_code === rule.product_code)?.product_name ?? rule.product_code;
    const b = form.products.find(p => p.product_code === rule.requires_product_code)?.product_name ?? rule.requires_product_code;
    if (needed.action === "withdraw" && needed.cover_self) out.push({ step, text: "{0} must be taken together with {1}.", values: [a, b] });
    if (needed.action === "enrol" && mine.covered.some(k => !needed.covered.includes(k))) {
      out.push({ step, text: "Family members covered under {0} must also be covered under {1}.", values: [a, b] });
    }
  }
  return out;
}

export function allProblems(form: PublicForm, draft: Draft): Problem[] {
  const out = [...detailsProblems(form, draft), ...familyProblems(form, draft), ...benefitProblems(form, draft)];
  const required = applicableClauses(form, draft).map(c => c.id);
  if (required.some(id => !draft.accepted.includes(id))) {
    out.push({ step: "declarations", text: "Tick every declaration before signing." });
  }
  return out;
}

export function signatureMatches(draft: Draft): boolean {
  return squash(draft.signature).toLowerCase() === squash(draft.particulars.name).toLowerCase()
    && squash(draft.signature).length >= 2;
}

export function payload(form: PublicForm, draft: Draft): Record<string, unknown> {
  const p = draft.particulars;
  const lifeEventWindow = form.window_type === "new_hire" || form.window_type === "life_event";
  const keep = new Set(draft.dependants.map(d => d.key));
  return {
    request_id: draft.requestId,
    particulars: {
      name: squash(p.name),
      id_no: p.id_no.trim().toUpperCase(),
      staff_id: squash(p.staff_id),
      gender: p.gender,
      dob: p.dob || null,
      contact_no: p.contact_no.trim(),
      email: p.email.trim(),
      job_grade: squash(p.job_grade) || null,
      date_of_hire: p.date_of_hire || null,
      marital_status: p.marital_status || null,
    },
    dependants: draft.dependants.map(d => ({
      key: d.key,
      action: d.action,
      relationship: d.relationship,
      name: squash(d.name),
      gender: d.gender,
      id_no: d.id_no.trim().toUpperCase(),
      dob: d.dob,
      occupation: squash(d.occupation) || null,
      nationality: squash(d.nationality) || null,
      residence: squash(d.residence) || null,
      child_confirmed: d.child_confirmed,
    })),
    products: form.products.map(product => {
      const c = draft.products[product.product_code];
      return {
        product_code: product.product_code,
        category_id: c.category_id,
        action: c.action,
        tier_key: c.action === "enrol" ? c.tier_key : null,
        cover_self: c.action === "withdraw" && c.cover_self,
        covered: c.covered.filter(k => keep.has(k)),
      };
    }),
    life_event: lifeEventWindow && draft.lifeEvent.kind && draft.lifeEvent.date
      ? { kind: draft.lifeEvent.kind, date: draft.lifeEvent.date }
      : null,
    accepted_clause_ids: draft.accepted,
    signature_name: squash(draft.signature),
    website: draft.website,
  };
}
