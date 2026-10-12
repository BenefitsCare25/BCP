import { usePortalTranslation } from "@/i18n/portal";
import type { CohortTier, ProductTierSet } from "@/api/enrollment";
import type { ProductAction, ProductChoiceInput, PublicForm, PublicProduct } from "@/api/publicLinks";
import { Money } from "@/components/portal/leaf/Figure";
import { Mount, MountRow, MountRule } from "@/components/portal/leaf/Mount";
import { PlanPricing } from "@/components/portal/enrollment/form/PlanPricing";
import { choiceControl, choiceRowClass } from "@/components/portal/enrollment/choiceRow";
import {
  canWithdraw,
  choiceFigures,
  cohortOf,
  coverable,
  squash,
  tierOf,
  type ChoiceFigures,
  type Draft,
} from "./formState";

const COMPOSITION: Record<string, string> = {
  EO: "Employee only",
  ES: "Employee and spouse",
  EC: "Employee and child(ren)",
  EF: "Employee and family",
};

function Choice({
  name, checked, onSelect, title, gloss, type = "radio",
}: {
  name: string;
  checked: boolean;
  onSelect: (checked: boolean) => void;
  title: string;
  gloss?: string;
  type?: "radio" | "checkbox";
}) {
  const pt = usePortalTranslation();
  return (
    <label className={choiceRowClass(checked, "mx-0")}>
      <input type={type} name={name} className={`${choiceControl} mt-0.5`} checked={checked}
        onChange={e => onSelect(e.target.checked)} />
      <span className="min-w-0">
        <span className={`block text-row text-record ${checked ? "font-semibold" : ""}`}>{pt(title)}</span>
        {gloss && <span className="block text-row text-label">{pt(gloss)}</span>}
      </span>
    </label>
  );
}

function Group({ legend, hint, children }: { legend: string; hint?: string; children: React.ReactNode }) {
  const pt = usePortalTranslation();
  return (
    <fieldset className="space-y-1.5">
      <legend className="leaf-label">{pt(legend)}</legend>
      {hint && <p className="text-row text-label">{pt(hint)}</p>}
      {children}
    </fieldset>
  );
}

function personTitle(name: string, relationship: "spouse" | "child", pt: (s: string) => string): string {
  return squash(name) || pt(relationship === "spouse" ? "Spouse" : "Child");
}

/** What the employee pays: a figure only when it is actually known. */
function Figures({ figures }: { figures: ChoiceFigures }) {
  const pt = usePortalTranslation();
  return (
    <>
      <MountRule />
      <dl className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <div>
          <dt className="text-row text-label">{pt("Cover chosen")}</dt>
          <dd className="text-row font-semibold text-record">{pt(COMPOSITION[figures.composition])}</dd>
        </div>
        <div className="text-right">
          <dt className="text-row text-label">{pt("Your share (annual)")}</dt>
          <dd className="text-lg font-semibold text-record">
            {figures.share === null
              ? <span className="text-row text-label">{pt("Confirmed after verification")}</span>
              : <Money value={figures.share} emphasis="strong" />}
          </dd>
        </div>
      </dl>
    </>
  );
}

function EnrolSection({
  form, draft, code, ts, plans, tier, figures, write,
}: {
  form: PublicForm;
  draft: Draft;
  code: string;
  ts: ProductTierSet;
  plans: CohortTier[];
  tier: CohortTier | undefined;
  figures: ChoiceFigures | null;
  write: (next: Partial<ProductChoiceInput>) => void;
}) {
  const pt = usePortalTranslation();
  const choice = draft.products[code];
  const ownVoluntary = ts.employee_participation === "voluntary";
  const familyCover = ts.tiers.some(t => t.dependant_participation === "voluntary");
  const toggle = (key: string, on: boolean) =>
    write({ covered: on ? [...choice.covered, key] : choice.covered.filter(k => k !== key) });
  const fact = form.plans.find(p => p.tier_key === choice.tier_key);
  const contribution = form.contributions.find(c => c.tiers.some(t => t.tier_key === choice.tier_key));
  const people = coverable(draft);
  return (
    <>
      {plans.length > 1 ? (
        <Group legend="Plan">
          <div className="grid gap-1.5 md:grid-cols-2">
            {plans.map(t => (
              <Choice key={t.key} name={`plan-${code}`} checked={choice.tier_key === t.key}
                onSelect={() => write({ tier_key: t.key })} title={t.label}
                gloss={t.is_baseline ? "Standard plan for your category" : undefined} />
            ))}
          </div>
        </Group>
      ) : tier && (
        <dl><MountRow term="Your plan"><span className="font-semibold">{pt(tier.label)}</span></MountRow></dl>
      )}
      {familyCover && (
        <Group legend="Who is covered"
          hint={ownVoluntary ? "You, plus anyone you tick below." : "You are covered automatically. Tick the family members to add."}>
          {people.length === 0 ? (
            <p className="rounded-control bg-action-wash px-3 py-2 text-row text-record">
              {pt("Add family members in the Family step to cover them here.")}
            </p>
          ) : (
            <div className="grid gap-1.5 md:grid-cols-2">
              {people.map(d => (
                <Choice key={d.key} type="checkbox" name={`cover-${code}`} checked={choice.covered.includes(d.key)}
                  onSelect={on => toggle(d.key, on)} title={personTitle(d.name, d.relationship, pt)}
                  gloss={d.relationship === "spouse" ? "Spouse" : "Child"} />
              ))}
            </div>
          )}
        </Group>
      )}
      {tier && <PlanPricing fact={fact} contribution={contribution} tierKey={tier.key} compact />}
      {figures && <Figures figures={figures} />}
    </>
  );
}

function WithdrawSection({
  draft, code, ownVoluntary, write,
}: {
  draft: Draft;
  code: string;
  ownVoluntary: boolean;
  write: (next: Partial<ProductChoiceInput>) => void;
}) {
  const pt = usePortalTranslation();
  const choice = draft.products[code];
  const toggle = (key: string, on: boolean) =>
    write({ covered: on ? [...choice.covered, key] : choice.covered.filter(k => k !== key) });
  return (
    <Group legend="Who to withdraw">
      <div className="grid gap-1.5 md:grid-cols-2">
        {ownVoluntary && (
          <Choice type="checkbox" name={`withdraw-${code}`} checked={choice.cover_self}
            onSelect={on => write({ cover_self: on })} title="My own cover" />
        )}
        {draft.dependants.map(d => (
          <Choice key={d.key} type="checkbox" name={`withdraw-${code}`} checked={choice.covered.includes(d.key)}
            onSelect={on => toggle(d.key, on)} title={personTitle(d.name, d.relationship, pt)}
            gloss={d.relationship === "spouse" ? "Spouse" : "Child"} />
        ))}
      </div>
      {draft.dependants.length === 0 && !ownVoluntary && (
        <p className="text-row text-label">{pt("Name the family members to withdraw in the Family step.")}</p>
      )}
    </Group>
  );
}

function ProductCard({
  form, draft, product, onChange,
}: {
  form: PublicForm;
  draft: Draft;
  product: PublicProduct;
  onChange: (next: Draft) => void;
}) {
  const code = product.product_code;
  const choice = draft.products[code];
  const cohort = cohortOf(product, choice);
  const ts = cohort?.tier_set;
  const ownVoluntary = ts?.employee_participation === "voluntary";
  const plans = ts ? (ts.allow_plan_change ? ts.tiers : ts.tiers.filter(t => t.is_baseline)) : [];

  const write = (next: Partial<ProductChoiceInput>, preselect?: string) => {
    const products = { ...draft.products, [code]: { ...choice, ...next } };
    if (preselect) {
      // A cohort chosen once is usually the same cohort on every benefit.
      for (const other of form.products) {
        const c = products[other.product_code];
        if (other.product_code === code || c.category_id) continue;
        const match = other.cohorts.find(o => o.cohort === preselect);
        if (match) products[other.product_code] = { ...c, category_id: match.category_id };
      }
    }
    onChange({ ...draft, products });
  };
  const pickCohort = (categoryId: string) =>
    write({ category_id: categoryId, tier_key: "" }, product.cohorts.find(c => c.category_id === categoryId)?.cohort);
  const pickAction = (action: ProductAction) => {
    const baseline = ts?.tiers.find(t => t.is_baseline)?.key ?? "";
    const tierKey = choice.tier_key || (plans.length === 1 ? plans[0].key : baseline);
    write({ action, tier_key: action === "enrol" ? tierKey : "", covered: [], cover_self: false });
  };

  return (
    <Mount as="li" rise={false} label={product.product_name ?? code} gloss={product.insurer ?? undefined}>
      {product.cohorts.length > 1 && (
        <Group legend="Your category" hint="Choose the group you belong to. We confirm it with your HR records.">
          <div className="grid gap-1.5 md:grid-cols-2">
            {product.cohorts.map(c => (
              <Choice key={c.category_id} name={`cohort-${code}`} checked={choice.category_id === c.category_id}
                onSelect={() => pickCohort(c.category_id)} title={c.label} />
            ))}
          </div>
        </Group>
      )}
      {cohort && (
        <Group legend="What would you like to do?">
          <div className="grid gap-1.5 sm:grid-cols-3">
            <Choice name={`action-${code}`} checked={choice.action === "no_change"} onSelect={() => pickAction("no_change")}
              title="No change" gloss="Keep my current cover" />
            <Choice name={`action-${code}`} checked={choice.action === "enrol"} onSelect={() => pickAction("enrol")}
              title={ownVoluntary ? "Enrol or change" : "Enrol family or change plan"} />
            {canWithdraw(cohort) && (
              <Choice name={`action-${code}`} checked={choice.action === "withdraw"} onSelect={() => pickAction("withdraw")}
                title="Withdraw" gloss={ownVoluntary ? "End cover for me or my family" : "End cover for my family"} />
            )}
          </div>
        </Group>
      )}
      {choice.action === "enrol" && ts && (
        <EnrolSection form={form} draft={draft} code={code} ts={ts} plans={plans}
          tier={tierOf(cohort, choice.tier_key)} figures={choiceFigures(form, draft, product)} write={write} />
      )}
      {choice.action === "withdraw" && (
        <WithdrawSection draft={draft} code={code} ownVoluntary={ownVoluntary} write={write} />
      )}
    </Mount>
  );
}

export function BenefitsStep({
  form,
  draft,
  onChange,
}: {
  form: PublicForm;
  draft: Draft;
  onChange: (next: Draft) => void;
}) {
  const pt = usePortalTranslation();
  return (
    <div className="space-y-3">
      {form.automatic.length > 0 && (
        <p className="rounded-control bg-action-wash px-4 py-3 text-row text-record">
          {pt("Covered automatically, nothing to choose: {0}.", [form.automatic.map(a => pt(a)).join(", ")])}
        </p>
      )}
      {form.products.length === 0 ? (
        <Mount label="Nothing to choose" gloss="Every benefit in this period is covered automatically." rise={false} />
      ) : (
        <ul className="space-y-3" aria-label={pt("Benefits")}>
          {form.products.map(product => (
            <ProductCard key={product.product_code} form={form} draft={draft} product={product} onChange={onChange} />
          ))}
        </ul>
      )}
    </div>
  );
}
