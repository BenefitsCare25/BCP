import { Plus, Trash2 } from "lucide-react";
import { usePortalTranslation } from "@/i18n/portal";
import type { DependantAction, DependantInput, PublicForm } from "@/api/publicLinks";
import { Action } from "@/components/portal/leaf/Action";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { Mount } from "@/components/portal/leaf/Mount";
import { choiceControl, choiceRowClass } from "@/components/portal/enrollment/choiceRow";
import { newDependant, type Draft } from "./formState";

const ACTIONS: { value: DependantAction; label: string; gloss: string }[] = [
  { value: "add", label: "Add to cover", gloss: "Enrol this family member" },
  { value: "withdraw", label: "Withdraw", gloss: "Remove them from cover" },
  { value: "update", label: "Update details", gloss: "Already covered; correct their details" },
];

function DependantCard({
  dep,
  title,
  canChange,
  onChange,
  onRemove,
}: {
  dep: DependantInput;
  title: string;
  canChange: boolean;
  onChange: (next: DependantInput) => void;
  onRemove: () => void;
}) {
  const pt = usePortalTranslation();
  const set = <K extends keyof DependantInput>(key: K, value: DependantInput[K]) => onChange({ ...dep, [key]: value });
  const actions = canChange ? ACTIONS : ACTIONS.filter(a => a.value === "update");
  return (
    <Mount
      as="li"
      rise={false}
      label={title}
      aside={(
        <button type="button" onClick={onRemove}
          className="leaf-focus inline-flex min-h-11 items-center gap-1.5 rounded-control px-2 text-row font-semibold text-action-ink hover:bg-shade/50">
          <Trash2 className="size-4" aria-hidden /> {pt("Remove")}
        </button>
      )}
    >
      <fieldset>
        <legend className="sr-only">{pt("What would you like to do for {0}?", [title])}</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {actions.map(a => (
            <label key={a.value} className={choiceRowClass(dep.action === a.value, "mx-0")}>
              <input type="radio" name={`action-${dep.key}`} className={`${choiceControl} mt-0.5`}
                checked={dep.action === a.value} onChange={() => set("action", a.value)} />
              <span className="min-w-0">
                <span className="block text-row font-semibold text-record">{pt(a.label)}</span>
                <span className="block text-row text-label">{pt(a.gloss)}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name" required hint="As in NRIC, birth certificate or FIN" className="sm:col-span-2">
          {props => <input {...props} className={leafControl} value={dep.name} maxLength={120}
            onChange={e => set("name", e.target.value)} />}
        </Field>
        <Field label="NRIC / birth certificate / FIN" required>
          {props => <input {...props} className={leafControl} value={dep.id_no} maxLength={20} spellCheck={false}
            onChange={e => set("id_no", e.target.value.toUpperCase())} />}
        </Field>
        <Field label="Date of birth" required>
          {props => <input {...props} type="date" className={leafControl} value={dep.dob}
            max={new Date().toISOString().slice(0, 10)} onChange={e => set("dob", e.target.value)} />}
        </Field>
        <Field label="Sex" required>
          {props => (
            <select {...props} className={leafControl} value={dep.gender}
              onChange={e => set("gender", e.target.value as DependantInput["gender"])}>
              <option value="">{pt("Choose")}</option>
              <option value="M">{pt("Male")}</option>
              <option value="F">{pt("Female")}</option>
            </select>
          )}
        </Field>
        <Field label="Occupation">
          {props => <input {...props} className={leafControl} value={dep.occupation} maxLength={80}
            onChange={e => set("occupation", e.target.value)} />}
        </Field>
      </div>
      <details className="group">
        <summary className="leaf-focus cursor-pointer rounded-control text-row font-semibold text-action-ink">
          {pt("Not Singaporean or PR, or living outside Singapore?")}
        </summary>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <Field label="Nationality">
            {props => <input {...props} className={leafControl} value={dep.nationality} maxLength={60}
              onChange={e => set("nationality", e.target.value)} />}
          </Field>
          <Field label="Country of residence">
            {props => <input {...props} className={leafControl} value={dep.residence} maxLength={60}
              onChange={e => set("residence", e.target.value)} />}
          </Field>
        </div>
      </details>
      {dep.relationship === "child" && dep.action === "add" && (
        <label className={choiceRowClass(dep.child_confirmed, "mx-0")}>
          <input type="checkbox" className={`${choiceControl} mt-0.5`} checked={dep.child_confirmed}
            onChange={e => set("child_confirmed", e.target.checked)} />
          <span className="text-row text-record">
            {pt("This child is unmarried, not working full time and not serving National Service.")}
          </span>
        </label>
      )}
    </Mount>
  );
}

export function FamilyStep({
  form,
  draft,
  onChange,
}: {
  form: PublicForm;
  draft: Draft;
  onChange: (next: Draft) => void;
}) {
  const pt = usePortalTranslation();
  const hasSpouse = draft.dependants.some(d => d.relationship === "spouse");
  const children = draft.dependants.filter(d => d.relationship === "child").length;
  const add = (relationship: "spouse" | "child") => {
    const dep = newDependant(relationship);
    if (!form.allow_dependant_changes) dep.action = "update";
    onChange({ ...draft, dependants: [...draft.dependants, dep] });
  };
  const update = (next: DependantInput) =>
    onChange({ ...draft, dependants: draft.dependants.map(d => (d.key === next.key ? next : d)) });
  const remove = (key: string) => {
    const products = Object.fromEntries(Object.entries(draft.products).map(([code, c]) => [
      code, { ...c, covered: c.covered.filter(k => k !== key) },
    ]));
    onChange({ ...draft, dependants: draft.dependants.filter(d => d.key !== key), products });
  };
  let childNo = 0;
  return (
    <div className="space-y-3">
      <Mount label="Your family" gloss="Name the family members you want to enrol, withdraw or update. Leave this empty if there are no changes." rise={false}>
        {form.eligibility_notes.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-row text-label">
            {form.eligibility_notes.map(n => <li key={n}>{pt(n)}</li>)}
          </ul>
        )}
        {!form.allow_dependant_changes && (
          <p className="rounded-control bg-action-wash px-3 py-2 text-row text-record">
            {pt("Family members cannot be added or withdrawn in this period. You can still update their details.")}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Action tone="neutral" type="button" onClick={() => add("spouse")} disabled={hasSpouse}>
            <Plus className="size-4" aria-hidden /> {pt("Add spouse")}
          </Action>
          <Action tone="neutral" type="button" onClick={() => add("child")} disabled={children >= form.max_children}>
            <Plus className="size-4" aria-hidden /> {pt("Add child")}
          </Action>
        </div>
      </Mount>
      {draft.dependants.length > 0 && (
        <ul className="space-y-3" aria-label={pt("Family members on this form")}>
          {draft.dependants.map(dep => {
            const title = dep.relationship === "spouse" ? pt("Spouse") : pt("Child {0}", [++childNo]);
            return (
              <DependantCard key={dep.key} dep={dep} title={title} canChange={form.allow_dependant_changes}
                onChange={update} onRemove={() => remove(dep.key)} />
            );
          })}
        </ul>
      )}
    </div>
  );
}
