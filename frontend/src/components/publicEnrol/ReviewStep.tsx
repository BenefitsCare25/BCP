import { AlertCircle, ArrowRight } from "lucide-react";
import { usePortalTranslation } from "@/i18n/portal";
import type { PublicForm } from "@/api/publicLinks";
import { Action } from "@/components/portal/leaf/Action";
import { Field, FormAlert, leafControl } from "@/components/portal/leaf/Field";
import { Money } from "@/components/portal/leaf/Figure";
import { Mount, MountRow, MountRule } from "@/components/portal/leaf/Mount";
import {
  choiceFigures,
  cohortOf,
  signatureMatches,
  squash,
  tierOf,
  type Draft,
  type Problem,
  type StepKey,
} from "./formState";

const STEP_LABEL: Record<StepKey, string> = {
  details: "Your details",
  family: "Family",
  benefits: "Benefit choices",
  declarations: "Declarations",
  review: "Review and sign",
};

const ACTION_LABEL: Record<string, string> = {
  add: "Add to cover",
  withdraw: "Withdraw",
  update: "Update details",
};

function choiceLine(form: PublicForm, draft: Draft, code: string): string {
  const product = form.products.find(p => p.product_code === code);
  const choice = draft.products[code];
  if (!product || !choice?.action) return "-";
  if (choice.action === "no_change") return "No change";
  const names = draft.dependants.filter(d => choice.covered.includes(d.key)).map(d => squash(d.name));
  if (choice.action === "withdraw") {
    return [choice.cover_self ? "my own cover" : null, ...names].filter(Boolean).join(", ");
  }
  const tier = tierOf(cohortOf(product, choice), choice.tier_key);
  return [tier?.label, ...names].filter(Boolean).join(" + ");
}

function ProblemList({ problems, onGo }: { problems: Problem[]; onGo: (step: StepKey) => void }) {
  const pt = usePortalTranslation();
  return (
    <Mount label="Before you sign" gloss="Fix these and come back to sign." rise={false}>
      <ul className="space-y-1">
        {problems.map((problem, i) => (
          <li key={`${problem.step}-${i}`}>
            <button type="button" onClick={() => onGo(problem.step)}
              className="leaf-focus group flex min-h-11 w-full items-center gap-3 rounded-control px-2 text-left hover:bg-shade/50">
              <AlertCircle className="size-4 shrink-0 text-strike-pending" aria-hidden />
              <span className="min-w-0 flex-1 text-row text-record">{pt(problem.text, problem.values)}</span>
              <span className="shrink-0 text-row font-semibold text-action-ink">{pt(STEP_LABEL[problem.step])}</span>
              <ArrowRight className="size-4 shrink-0 text-action-ink transition-transform group-hover:translate-x-0.5" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </Mount>
  );
}

function Summary({ form, draft }: { form: PublicForm; draft: Draft }) {
  const pt = usePortalTranslation();
  const p = draft.particulars;
  const figures = form.products.map(product => ({ product, f: choiceFigures(form, draft, product) }));
  const total = figures.reduce((sum, { f }) => sum + (f?.share ?? 0), 0);
  const unknown = figures.some(({ f }) => f !== null && f.share === null);
  return (
    <Mount label="Check your form" rise={false}>
      <div className="grid gap-x-8 md:grid-cols-2">
        <dl>
          <MountRow term="Name">{squash(p.name) || "-"}</MountRow>
          <MountRow term="NRIC / FIN">{p.id_no || "-"}</MountRow>
          <MountRow term="Staff ID">{squash(p.staff_id) || "-"}</MountRow>
          <MountRow term="Mobile number">{p.contact_no || "-"}</MountRow>
          <MountRow term="Email">{p.email || "-"}</MountRow>
        </dl>
        <dl>
          {draft.dependants.length === 0 ? (
            <MountRow term="Family">{pt("No changes")}</MountRow>
          ) : draft.dependants.map(d => (
            <MountRow key={d.key} term={squash(d.name) || pt(d.relationship === "spouse" ? "Spouse" : "Child")}
              gloss={d.relationship === "spouse" ? "Spouse" : "Child"}>
              {pt(ACTION_LABEL[d.action])}
            </MountRow>
          ))}
        </dl>
      </div>
      {form.products.length > 0 && (
        <>
          <MountRule />
          <dl>
            {figures.map(({ product, f }) => (
              <MountRow key={product.product_code} term={product.product_name ?? product.product_code}
                gloss={choiceLine(form, draft, product.product_code)}>
                {f ? <Money value={f.share} /> : <span className="text-label">-</span>}
              </MountRow>
            ))}
            <MountRow term="Your total share (annual)"
              gloss={unknown ? "Plus amounts confirmed after verification" : undefined}>
              <Money value={total} emphasis="strong" />
            </MountRow>
          </dl>
        </>
      )}
    </Mount>
  );
}

export function ReviewStep({
  form,
  draft,
  problems,
  serverProblems,
  submitting,
  onChange,
  onGo,
  onSubmit,
}: {
  form: PublicForm;
  draft: Draft;
  problems: Problem[];
  serverProblems: string[];
  submitting: boolean;
  onChange: (next: Draft) => void;
  onGo: (step: StepKey) => void;
  onSubmit: () => void;
}) {
  const pt = usePortalTranslation();
  const signed = signatureMatches(draft);
  const ready = problems.length === 0 && signed && !submitting;
  return (
    <div className="space-y-3">
      {problems.length > 0 && <ProblemList problems={problems} onGo={onGo} />}
      <Summary form={form} draft={draft} />
      <Mount label="Sign" gloss="Type your full name exactly as entered in Your details." rise={false}>
        <Field label="Your full name" required
          error={draft.signature && !signed ? "This must match the name in Your details." : null}>
          {props => (
            <input {...props} className={`${leafControl} font-semibold`} value={draft.signature}
              autoComplete="off" maxLength={120} onChange={e => onChange({ ...draft, signature: e.target.value })} />
          )}
        </Field>
        {serverProblems.length > 0 && (
          <FormAlert>
            <span className="block font-semibold">{pt("Please fix the highlighted details and submit again.")}</span>
            <span className="mt-1 block">{serverProblems.map(t => pt(t)).join(" ")}</span>
          </FormAlert>
        )}
        <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-row text-label">
            {pt("Your form goes to your company's insurance broker. Nothing changes until they confirm it.")}
          </p>
          <Action tone="primary" type="button" block="phone" disabled={!ready} aria-busy={submitting} onClick={onSubmit}>
            {submitting ? pt("Submitting…") : pt("Sign and submit")}
          </Action>
        </div>
        {form.helpline && <p className="text-row text-label">{pt(form.helpline)}</p>}
      </Mount>
    </div>
  );
}
