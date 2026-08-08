import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { useUpdateClaimAssessment, type BrokerClaim } from "@/api/claims";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import {
  canAmendPayment,
  hasSettlement,
} from "@/components/claims/ClaimSettlementFacts";
import { formatError } from "@/lib/errors";
import { toast } from "sonner";

/**
 * Assessor-entered claim detail — sector, admission window, payroll treatment
 * and the broker-only note.
 *
 * These are the six fields no document extraction supplies, and every one of
 * them is a COLUMN on the claims reports (`services/claims_reports.py`). The
 * endpoint and its hook shipped with no caller, so the columns were blank on
 * every row with nothing in the product able to fill them. This is that
 * caller. It lives inside the claim sheet on `/claims/review` rather than on a
 * surface of its own: assessing a claim is what that sheet is for, and a second
 * page would mean opening the claim twice to do one job.
 *
 * Three rules it depends on:
 *
 * - **The PATCH is partial and this form sends only what CHANGED.** The server
 *   applies `model_fields_set`, so an untouched field must not appear in the
 *   body — sending the whole object would let this form overwrite an admission
 *   date another assessor keyed in between the load and the save.
 * - **Tri-state booleans are three options, not a checkbox.** NULL means "not
 *   assessed" and `false` means "assessed, and no"; a payroll team acts
 *   differently on each, and `_flag` in the report prints them differently. A
 *   checkbox can only express two of the three and would silently answer "No"
 *   on every claim nobody has looked at.
 * - **Sector and admission dates are offered only for an inpatient claim**, and
 *   `is_inpatient` is SERVED (see `BrokerClaimOut.is_inpatient`). The report
 *   drops the same three columns on an outpatient sheet from the same helper,
 *   so the form and the sheet cannot disagree about which claims have them.
 */

type Tri = "" | "yes" | "no";

const SECTOR_LABELS: Record<string, string> = {
  government: "Government",
  private: "Private / overseas",
};

/** Sector is genuinely unknown until somebody reads the invoice, so it keeps a
 *  "Not assessed" option. The two PAYROLL flags do not: the ordinary answer for
 *  a medical reimbursement is No, so they default to it (broker decision) and
 *  offer only Yes/No. `_flag` in `claims_reports.py` renders a NULL as "No" to
 *  match — the form, the stored value and the sheet have to say the same thing,
 *  and a control showing "No" over a column printing blank is the kind of
 *  disagreement nobody notices until payroll acts on the wrong one. */
function toFlag(value: boolean | null): Exclude<Tri, ""> {
  return value ? "yes" : "no";
}

function fromFlag(value: Exclude<Tri, "">): boolean {
  return value === "yes";
}

/** A form's own view of the six fields, all as strings so an untouched control
 *  and a cleared one are the same value and neither reads as a change. */
interface Draft {
  hospital_type: string;
  admission_date: string;
  discharge_date: string;
  taxable: Exclude<Tri, "">;
  cpf_claimable: Exclude<Tri, "">;
  admin_remarks: string;
  sent_to_insurer_on: string;
  insurer_deadline_on: string;
  paid_on: string;
  payment_amount: string;
}

/** The stored timestamp as the `yyyy-mm-dd` a date input wants. Only the DATE
 *  is ever read off it — the wall-clock half exists so the column stays a real
 *  timestamp. */
function dateInput(value: string | null): string {
  return value ? value.slice(0, 10) : "";
}

function draftOf(claim: BrokerClaim): Draft {
  return {
    hospital_type: claim.hospital_type ?? "",
    admission_date: claim.admission_date ?? "",
    discharge_date: claim.discharge_date ?? "",
    taxable: toFlag(claim.taxable),
    cpf_claimable: toFlag(claim.cpf_claimable),
    admin_remarks: claim.admin_remarks ?? "",
    sent_to_insurer_on: dateInput(claim.sent_to_insurer_at),
    insurer_deadline_on: dateInput(claim.insurer_deadline_on),
    paid_on: dateInput(claim.paid_on),
    payment_amount:
      claim.payment_amount != null ? String(claim.payment_amount) : "",
  };
}

/** Only the fields that MOVED, in the shape the PATCH expects. An empty object
 *  means there is nothing to save — which is what disables the button. */
function changedFields(draft: Draft, base: Draft) {
  const patch: Record<string, string | boolean | number | null> = {};
  if (draft.hospital_type !== base.hospital_type) {
    patch.hospital_type = draft.hospital_type || null;
  }
  if (draft.admission_date !== base.admission_date) {
    patch.admission_date = draft.admission_date || null;
  }
  if (draft.discharge_date !== base.discharge_date) {
    patch.discharge_date = draft.discharge_date || null;
  }
  if (draft.taxable !== base.taxable) patch.taxable = fromFlag(draft.taxable);
  if (draft.cpf_claimable !== base.cpf_claimable) {
    patch.cpf_claimable = fromFlag(draft.cpf_claimable);
  }
  if (draft.admin_remarks !== base.admin_remarks) {
    patch.admin_remarks = draft.admin_remarks.trim() || null;
  }
  for (const key of ["sent_to_insurer_on", "insurer_deadline_on", "paid_on"] as const) {
    if (draft[key] !== base[key]) patch[key] = draft[key] || null;
  }
  if (draft.payment_amount !== base.payment_amount) {
    const raw = draft.payment_amount.trim();
    patch.payment_amount = raw === "" ? null : Number(raw);
  }
  return patch;
}

function FormField({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function ClaimAssessmentPanel({ claim }: { claim: BrokerClaim }) {
  const base = useMemo(() => draftOf(claim), [claim]);
  const [draft, setDraft] = useState<Draft>(base);
  const save = useUpdateClaimAssessment();

  // Re-seed when the sheet moves to another claim, or when a save lands and the
  // server's copy becomes the new baseline.
  useEffect(() => {
    setDraft(base);
  }, [base]);

  const patch = changedFields(draft, base);
  const dirty = Object.keys(patch).length > 0;

  // Caught here as well as server-side: the assessor should see it while both
  // dates are in front of them, not as a toast after the save is refused.
  const invertedDates =
    Boolean(draft.admission_date) &&
    Boolean(draft.discharge_date) &&
    draft.discharge_date < draft.admission_date;

  // Caught here as well as server-side, for the same reason as the admission
  // pair: the assessor should see it with the field in front of them.
  const clearedDispatch =
    hasSettlement(claim) && base.sent_to_insurer_on !== "" &&
    draft.sent_to_insurer_on === "";

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const onSave = async () => {
    try {
      await save.mutateAsync({ claimId: claim.id, patch });
      toast.success("Assessment saved");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
        {/* Sector and the admission window are inpatient facts. On an
            outpatient claim they are three controls nobody can meaningfully
            answer — and the report drops the same columns. */}
        {claim.is_inpatient && (
          <>
            {/* Blank means DERIVE, not "unassessed": the sector already comes
                from the provider (`sg_hospitals.sector_from_provider`), which
                is what the intake autofill and the review's document check
                have always used — and what the report now labels its column
                from. This control is the OVERRIDE, for an overseas admission,
                a hospital the registry doesn't list, or a derivation an
                assessor knows is wrong. The blank option names the answer that
                will actually be used, so nobody has to pick one to get it. */}
            <FormField
              label="Hospital sector"
              htmlFor="assess-sector"
              hint={
                draft.hospital_type
                  ? "Overriding the sector read from the provider."
                  : claim.hospital_type_derived
                    ? `From ${claim.provider_name ?? "the provider"}.`
                    : "Not in the hospital registry — set it if this is an inpatient claim."
              }
            >
              <NativeSelect
                id="assess-sector"
                className="w-full"
                value={draft.hospital_type}
                onChange={(e) => set("hospital_type", e.target.value)}
              >
                <option value="">
                  {claim.hospital_type_derived
                    ? `Auto — ${SECTOR_LABELS[claim.hospital_type_derived]}`
                    : "Auto — not recognised"}
                </option>
                <option value="government">Government</option>
                <option value="private">Private / overseas</option>
              </NativeSelect>
            </FormField>
            <div className="hidden sm:block" aria-hidden />
            <FormField label="Admission date" htmlFor="assess-admission">
              <Input
                id="assess-admission"
                type="date"
                value={draft.admission_date}
                onChange={(e) => set("admission_date", e.target.value)}
              />
            </FormField>
            <FormField label="Discharge date" htmlFor="assess-discharge">
              <Input
                id="assess-discharge"
                type="date"
                value={draft.discharge_date}
                onChange={(e) => set("discharge_date", e.target.value)}
                aria-invalid={invertedDates || undefined}
              />
            </FormField>
          </>
        )}

        {/* Default No — the ordinary payroll treatment of a medical
            reimbursement. Only Yes/No: an "unassessed" third state here would
            be a value the sheet has to print as something, and it would print
            it as No anyway. */}
        <FormField label="Taxable" htmlFor="assess-taxable">
          <NativeSelect
            id="assess-taxable"
            className="w-full"
            value={draft.taxable}
            onChange={(e) =>
              set("taxable", e.target.value as Exclude<Tri, "">)
            }
          >
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </NativeSelect>
        </FormField>
        <FormField label="CPF claimable" htmlFor="assess-cpf">
          <NativeSelect
            id="assess-cpf"
            className="w-full"
            value={draft.cpf_claimable}
            onChange={(e) =>
              set("cpf_claimable", e.target.value as Exclude<Tri, "">)
            }
          >
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </NativeSelect>
        </FormField>

        <div className="sm:col-span-2">
          <FormField
            label="Admin remark"
            htmlFor="assess-remark"
            hint="Internal only — the member never sees this."
          >
            <textarea
              id="assess-remark"
              rows={3}
              maxLength={4000}
              value={draft.admin_remarks}
              onChange={(e) => set("admin_remarks", e.target.value)}
              className="w-full resize-y rounded-md border border-input bg-card px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring/40"
            />
          </FormField>
        </div>
      </div>

      {/* The settlement dates, editable. `send-to-insurer` and `payment` are
          TRANSITIONS and each is offered from one status only, so once a claim
          is past that point there was no way to correct a date it recorded
          wrongly — or to fill one in at all on a claim that reached `paid`
          without passing through dispatch. Amending here writes the record and
          deliberately leaves the status alone: re-running the transition would
          repost the member's "your claim has been paid" notice for a typo. */}
      {hasSettlement(claim) && (
        <div className="space-y-3 border-t border-border pt-4">
          <SectionLabel as="h4">Correct the settlement dates</SectionLabel>
          <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
            {/* Required, not optional: a claim that IS with the insurer must
                keep a dispatch date. Cleared, `insurer_days` goes blank while
                `days_over_deadline` keeps counting against a deadline nothing
                explains. The server refuses it too. */}
            <FormField
              label="Sent to insurer"
              htmlFor="assess-sent"
              hint={
                clearedDispatch ? undefined : "Correct it rather than clearing it."
              }
            >
              <Input
                id="assess-sent"
                type="date"
                required
                value={draft.sent_to_insurer_on}
                onChange={(e) => set("sent_to_insurer_on", e.target.value)}
                aria-invalid={clearedDispatch || undefined}
              />
            </FormField>
            <FormField
              label="Insurer deadline"
              htmlFor="assess-deadline"
              hint="Blank leaves the claim off the overdue list."
            >
              <Input
                id="assess-deadline"
                type="date"
                value={draft.insurer_deadline_on}
                onChange={(e) => set("insurer_deadline_on", e.target.value)}
              />
            </FormField>
            {/* Only on a claim recorded as PAID. Writing a payment date onto
                one still with the insurer would not move the status but would
                stop the SLA clock — an unpaid claim silently leaving the
                overdue list. The server refuses it; offering the control
                anyway would just produce a 409. */}
            {canAmendPayment(claim) && (
              <>
                <FormField label="Payment date" htmlFor="assess-paid">
                  <Input
                    id="assess-paid"
                    type="date"
                    value={draft.paid_on}
                    onChange={(e) => set("paid_on", e.target.value)}
                  />
                </FormField>
                <FormField
                  label="Amount paid"
                  htmlFor="assess-amount"
                  hint="Kept apart from the approved amount — the gap is the shortfall."
                >
                  <Input
                    id="assess-amount"
                    type="number"
                    min="0"
                    step="0.01"
                    value={draft.payment_amount}
                    onChange={(e) => set("payment_amount", e.target.value)}
                  />
                </FormField>
              </>
            )}
          </div>
        </div>
      )}

      {invertedDates && (
        <p className="text-sm text-error">
          Discharge cannot precede admission.
        </p>
      )}
      {clearedDispatch && (
        <p className="text-sm text-error">
          This claim has been sent to the insurer, so it must keep a dispatch
          date.
        </p>
      )}

      <div className="flex items-center gap-3">
        <Button
          size="sm"
          disabled={!dirty || invertedDates || clearedDispatch || save.isPending}
          onClick={onSave}
        >
          {save.isPending && <Loader2 className="size-4 animate-spin" />}
          Save assessment
        </Button>
        {dirty && !save.isPending && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setDraft(base)}
          >
            Discard
          </Button>
        )}
      </div>
    </div>
  );
}
