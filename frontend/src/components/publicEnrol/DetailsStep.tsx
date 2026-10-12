import { usePortalTranslation } from "@/i18n/portal";
import type { ParticularsInput, PublicForm } from "@/api/publicLinks";
import { Field, leafControl, RequiredMark } from "@/components/portal/leaf/Field";
import { Mount } from "@/components/portal/leaf/Mount";
import { choiceControl, choiceRowClass } from "@/components/portal/enrollment/choiceRow";
import type { Draft, LifeEventInput } from "./formState";

const SEX: { value: "M" | "F"; label: string }[] = [
  { value: "M", label: "Male" },
  { value: "F", label: "Female" },
];

const MARITAL: { value: ParticularsInput["marital_status"]; label: string }[] = [
  { value: "", label: "Choose" },
  { value: "single", label: "Never married" },
  { value: "married", label: "Married" },
  { value: "divorced", label: "Divorced" },
  { value: "widowed", label: "Widowed" },
];

const EVENTS: { value: LifeEventInput["kind"]; label: string }[] = [
  { value: "", label: "Choose" },
  { value: "new_hire", label: "I joined the company" },
  { value: "marriage", label: "I got married" },
  { value: "birth", label: "I had a baby" },
  { value: "other", label: "Something else" },
];

function LifeEventFields({ draft, onChange }: { draft: Draft; onChange: (next: Draft) => void }) {
  const pt = usePortalTranslation();
  const set = (next: Partial<LifeEventInput>) => onChange({ ...draft, lifeEvent: { ...draft.lifeEvent, ...next } });
  return (
    <div className="grid gap-4 rounded-control bg-action-wash p-3 sm:grid-cols-2">
      <Field label="What changed?" required hint="Submit within 30 days of the event.">
        {props => (
          <select {...props} className={leafControl} value={draft.lifeEvent.kind}
            onChange={e => set({ kind: e.target.value as LifeEventInput["kind"] })}>
            {EVENTS.map(ev => <option key={ev.value} value={ev.value}>{pt(ev.label)}</option>)}
          </select>
        )}
      </Field>
      <Field label="Date of the event" required>
        {props => (
          <input {...props} type="date" className={leafControl} value={draft.lifeEvent.date}
            max={new Date().toISOString().slice(0, 10)} onChange={e => set({ date: e.target.value })} />
        )}
      </Field>
    </div>
  );
}

/** Left empty by people; filled by form-filling bots. */
function Honeypot({ draft, onChange }: { draft: Draft; onChange: (next: Draft) => void }) {
  return (
    <div aria-hidden className="absolute -left-[10000px] h-px w-px overflow-hidden">
      <label>
        Website
        <input tabIndex={-1} autoComplete="off" value={draft.website}
          onChange={e => onChange({ ...draft, website: e.target.value })} />
      </label>
    </div>
  );
}

export function DetailsStep({
  form,
  draft,
  onChange,
}: {
  form: PublicForm;
  draft: Draft;
  onChange: (next: Draft) => void;
}) {
  const pt = usePortalTranslation();
  const p = draft.particulars;
  const set = <K extends keyof ParticularsInput>(key: K, value: ParticularsInput[K]) =>
    onChange({ ...draft, particulars: { ...p, [key]: value } });
  const lifeEventWindow = form.window_type === "new_hire" || form.window_type === "life_event";

  return (
    <Mount label="Your details" gloss="As shown in your NRIC or FIN. We check them against your company's records." rise={false}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name" required className="sm:col-span-2">
          {props => (
            <input {...props} className={leafControl} autoComplete="name" value={p.name}
              maxLength={120} onChange={e => set("name", e.target.value)} />
          )}
        </Field>
        <Field label="NRIC / FIN" required hint="For example S1234567A">
          {props => (
            <input {...props} className={leafControl} autoComplete="off" value={p.id_no}
              maxLength={20} spellCheck={false} onChange={e => set("id_no", e.target.value.toUpperCase())} />
          )}
        </Field>
        <Field label="Staff ID" required>
          {props => (
            <input {...props} className={leafControl} autoComplete="off" value={p.staff_id}
              maxLength={64} onChange={e => set("staff_id", e.target.value)} />
          )}
        </Field>
        <fieldset className="space-y-1.5">
          <legend className="leaf-label">{pt("Sex")}<RequiredMark /></legend>
          <div className="flex gap-2">
            {SEX.map(s => (
              <label key={s.value} className={choiceRowClass(p.gender === s.value, "mx-0 flex-1 items-center")}>
                <input type="radio" name="sex" className={choiceControl} checked={p.gender === s.value}
                  onChange={() => set("gender", s.value)} />
                <span className="text-row text-record">{pt(s.label)}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <Field label="Date of birth">
          {props => (
            <input {...props} type="date" className={leafControl} value={p.dob}
              max={new Date().toISOString().slice(0, 10)} onChange={e => set("dob", e.target.value)} />
          )}
        </Field>
        <Field label="Mobile number" required>
          {props => (
            <input {...props} type="tel" inputMode="tel" className={leafControl} autoComplete="tel"
              value={p.contact_no} maxLength={20} onChange={e => set("contact_no", e.target.value)} />
          )}
        </Field>
        <Field label="Email" required>
          {props => (
            <input {...props} type="email" className={leafControl} autoComplete="email" value={p.email}
              maxLength={160} onChange={e => set("email", e.target.value)} />
          )}
        </Field>
        <Field label="Job grade">
          {props => (
            <input {...props} className={leafControl} value={p.job_grade} maxLength={60}
              onChange={e => set("job_grade", e.target.value)} />
          )}
        </Field>
        <Field label="Date you joined the company">
          {props => (
            <input {...props} type="date" className={leafControl} value={p.date_of_hire}
              onChange={e => set("date_of_hire", e.target.value)} />
          )}
        </Field>
        <Field label="Marital status">
          {props => (
            <select {...props} className={leafControl} value={p.marital_status}
              onChange={e => set("marital_status", e.target.value as ParticularsInput["marital_status"])}>
              {MARITAL.map(m => <option key={m.value} value={m.value}>{pt(m.label)}</option>)}
            </select>
          )}
        </Field>
      </div>
      {lifeEventWindow && <LifeEventFields draft={draft} onChange={onChange} />}
      <Honeypot draft={draft} onChange={onChange} />
    </Mount>
  );
}
