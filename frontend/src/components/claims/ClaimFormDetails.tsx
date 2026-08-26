import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Pencil } from "lucide-react";
import {
  useAmendClaim,
  useRefreshClaimConversion,
  useUpdateClaimAssessment,
  type BrokerClaim,
  type BrokerClaimAmendInput,
} from "@/api/claims";
import { ConversionLine, policyAmount } from "@/components/claims/ConversionLine";
import {
  canAmendPayment,
  hasSettlement,
} from "@/components/claims/ClaimSettlementFacts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtDate } from "@/lib/format";
import { toast } from "sonner";

type Flag = "yes" | "no";

interface Draft {
  incurred_date: string;
  provider_name: string;
  invoice_number: string;
  doctor_name: string;
  diagnosis: string;
  amount_claimed: string;
  hospital_type: string;
  admission_date: string;
  discharge_date: string;
  taxable: Flag;
  cpf_claimable: Flag;
  admin_remarks: string;
  sent_to_insurer_on: string;
  insurer_deadline_on: string;
  paid_on: string;
  payment_amount: string;
}

const REASON_REQUIRED = new Set([
  "approved",
  "sent_to_insurer",
  "paid",
  "rejected",
]);

const SECTOR_LABELS: Record<string, string> = {
  government: "Government",
  private: "Private / overseas",
};

function dateInput(value: string | null): string {
  return value ? value.slice(0, 10) : "";
}

function flag(value: boolean | null): Flag {
  return value ? "yes" : "no";
}

function draftOf(claim: BrokerClaim): Draft {
  return {
    incurred_date: claim.incurred_date,
    provider_name: (claim.provider_name ?? "").trim(),
    invoice_number: (claim.invoice_number ?? "").trim(),
    doctor_name: (claim.doctor_name ?? "").trim(),
    diagnosis: (claim.diagnosis ?? "").trim(),
    amount_claimed: String(claim.amount_claimed),
    hospital_type: claim.hospital_type ?? "",
    admission_date: claim.admission_date ?? "",
    discharge_date: claim.discharge_date ?? "",
    taxable: flag(claim.taxable),
    cpf_claimable: flag(claim.cpf_claimable),
    admin_remarks: claim.admin_remarks ?? "",
    sent_to_insurer_on: dateInput(claim.sent_to_insurer_at),
    insurer_deadline_on: dateInput(claim.insurer_deadline_on),
    paid_on: dateInput(claim.paid_on),
    payment_amount:
      claim.payment_amount != null ? String(claim.payment_amount) : "",
  };
}

function claimChanges(
  draft: Draft,
  base: Draft,
): BrokerClaimAmendInput["patch"] {
  const patch: BrokerClaimAmendInput["patch"] = {};
  if (draft.incurred_date !== base.incurred_date) {
    patch.incurred_date = draft.incurred_date;
  }
  if (draft.provider_name.trim() !== base.provider_name) {
    patch.provider_name = draft.provider_name.trim();
  }
  if (draft.invoice_number.trim() !== base.invoice_number) {
    patch.invoice_number = draft.invoice_number.trim();
  }
  if (draft.doctor_name.trim() !== base.doctor_name) {
    patch.doctor_name = draft.doctor_name.trim() || null;
  }
  if (draft.diagnosis.trim() !== base.diagnosis) {
    patch.diagnosis = draft.diagnosis.trim() || null;
  }
  if (draft.amount_claimed !== base.amount_claimed) {
    patch.amount_claimed = Number(draft.amount_claimed);
  }
  return patch;
}

function assessmentChanges(draft: Draft, base: Draft) {
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
  if (draft.taxable !== base.taxable) patch.taxable = draft.taxable === "yes";
  if (draft.cpf_claimable !== base.cpf_claimable) {
    patch.cpf_claimable = draft.cpf_claimable === "yes";
  }
  if (draft.admin_remarks !== base.admin_remarks) {
    patch.admin_remarks = draft.admin_remarks.trim() || null;
  }
  for (const key of [
    "sent_to_insurer_on",
    "insurer_deadline_on",
    "paid_on",
  ] as const) {
    if (draft[key] !== base[key]) patch[key] = draft[key] || null;
  }
  if (draft.payment_amount !== base.payment_amount) {
    const value = draft.payment_amount.trim();
    patch.payment_amount = value === "" ? null : Number(value);
  }
  return patch;
}

function Detail({ label, wide, children }: {
  label: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[minmax(8.5rem,0.42fr)_minmax(0,0.58fr)] sm:gap-4"
      data-wide={wide || undefined}
    >
      <SectionLabel as="dt" className="sm:pt-0.5">{label}</SectionLabel>
      <dd className="min-w-0 text-sm text-foreground">{children}</dd>
    </div>
  );
}

function FormField({ label, htmlFor, hint, children }: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function coverageLabel(claim: BrokerClaim): string {
  if (claim.claim_kind === "flex") {
    return `Flex · ${claim.flex_category_name ?? claim.claim_type}`;
  }
  return `${claim.product_code ?? claim.claim_type}${
    claim.sub_type
      ? ` · ${claim.sub_type}`
      : claim.benefit_key
        ? ` · ${claim.benefit_key}`
        : ""
  }`;
}

function memberLabel(claim: BrokerClaim): ReactNode {
  return (
    <>
      {claim.employee_name ?? "—"}{" "}
      {claim.staff_id && (
        <span className="text-muted-foreground">({claim.staff_id})</span>
      )}
    </>
  );
}

/**
 * One broker-facing representation of the submitted form and assessor-owned
 * facts. It reads as a record by default and deliberately enters edit mode only
 * after an explicit action; the two audit endpoints remain distinct under the
 * hood even though the operator completes one coherent task.
 */
export function ClaimFormDetails({
  claim,
  editable,
}: {
  claim: BrokerClaim;
  editable: boolean;
}) {
  const sectionRef = useRef<HTMLElement>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const [viewClaim, setViewClaim] = useState(claim);
  const [editing, setEditing] = useState(false);
  const [base, setBase] = useState(() => ({
    draft: draftOf(claim),
    revision: claim.revision,
  }));
  const [draft, setDraft] = useState(() => draftOf(claim));
  const [reason, setReason] = useState("");
  const [overpaymentWarning, setOverpaymentWarning] = useState<string | null>(null);
  const amend = useAmendClaim();
  const assess = useUpdateClaimAssessment();
  const refreshFx = useRefreshClaimConversion();

  useEffect(() => {
    if (editing) return;
    setViewClaim(claim);
    setBase({ draft: draftOf(claim), revision: claim.revision });
    setDraft(draftOf(claim));
  }, [claim]);

  const original = base.draft;
  const claimPatch = useMemo(() => claimChanges(draft, original), [draft, original]);
  const assessmentPatch = useMemo(
    () => assessmentChanges(draft, original),
    [draft, original],
  );
  const claimDirty = Object.keys(claimPatch).length > 0;
  const assessmentDirty = Object.keys(assessmentPatch).length > 0;
  const dirty = claimDirty || assessmentDirty;
  const movedUnderUs = editing && claim.revision !== base.revision;
  const needsReason = claimDirty && REASON_REQUIRED.has(viewClaim.status);
  const amountValid = Number(draft.amount_claimed) > 0;
  const invertedDates =
    Boolean(draft.admission_date) &&
    Boolean(draft.discharge_date) &&
    draft.discharge_date < draft.admission_date;
  const clearedDispatch =
    hasSettlement(viewClaim) &&
    original.sent_to_insurer_on !== "" &&
    draft.sent_to_insurer_on === "";
  const pending = amend.isPending || assess.isPending;
  const canSave =
    dirty &&
    amountValid &&
    !invertedDates &&
    !clearedDispatch &&
    !movedUnderUs &&
    (!needsReason || reason.trim() !== "");

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    if (key === "payment_amount") setOverpaymentWarning(null);
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const startEditing = () => {
    const next = draftOf(viewClaim);
    setBase({ draft: next, revision: viewClaim.revision });
    setDraft(next);
    setReason("");
    setOverpaymentWarning(null);
    setEditing(true);
  };

  const returnToDetails = () => {
    setEditing(false);
    requestAnimationFrame(() => {
      sectionRef.current?.scrollIntoView({ block: "start" });
      editButtonRef.current?.focus();
    });
  };

  const cancelEditing = () => {
    const next = draftOf(claim);
    setViewClaim(claim);
    setBase({ draft: next, revision: claim.revision });
    setDraft(next);
    setReason("");
    setOverpaymentWarning(null);
    returnToDetails();
  };

  const rebase = () => {
    const next = draftOf(claim);
    setViewClaim(claim);
    setBase({ draft: next, revision: claim.revision });
    setDraft(next);
    setReason("");
    setOverpaymentWarning(null);
  };

  const settleSavedClaimFields = (saved: BrokerClaim) => {
    const savedDraft = draftOf(saved);
    setViewClaim(saved);
    setBase({ draft: savedDraft, revision: saved.revision });
    setDraft((current) => ({
      ...current,
      incurred_date: savedDraft.incurred_date,
      provider_name: savedDraft.provider_name,
      invoice_number: savedDraft.invoice_number,
      doctor_name: savedDraft.doctor_name,
      diagnosis: savedDraft.diagnosis,
      amount_claimed: savedDraft.amount_claimed,
    }));
  };

  const save = async () => {
    let saved = viewClaim;
    let claimFactsSaved = false;
    try {
      if (claimDirty) {
        saved = await amend.mutateAsync({
          claimId: viewClaim.id,
          patch: claimPatch,
          reason: needsReason ? reason.trim() : undefined,
          expectedRevision: base.revision,
        });
        claimFactsSaved = true;
      }

      if (assessmentDirty) {
        try {
          saved = await assess.mutateAsync({
            claimId: viewClaim.id,
            patch: {
              ...assessmentPatch,
              ...(overpaymentWarning ? { acknowledge_overpayment: true } : {}),
            },
          });
        } catch (error) {
          if (claimFactsSaved) settleSavedClaimFields(saved);
          if (
            error instanceof ConflictDetailError &&
            error.detail.code === "payment_exceeds_approval"
          ) {
            setOverpaymentWarning(error.message);
          } else {
            toast.error(
              claimFactsSaved
                ? `Claim facts were saved, but the assessor details were not. ${formatError(error)}`
                : formatError(error),
            );
          }
          return;
        }
      }

      const next = draftOf(saved);
      setViewClaim(saved);
      setBase({ draft: next, revision: saved.revision });
      setDraft(next);
      setReason("");
      setOverpaymentWarning(null);
      returnToDetails();
      toast.success("Form details updated");
    } catch (error) {
      toast.error(formatError(error));
    }
  };

  const claimedInPolicyCurrency = policyAmount(viewClaim);
  const remainingLimit = viewClaim.remaining_limit ?? null;
  const effectiveSector = viewClaim.hospital_type ?? viewClaim.hospital_type_derived;

  return (
    <section ref={sectionRef} className="space-y-3">
      <div className="flex min-h-9 items-center justify-between gap-4">
        <SectionLabel as="h3">Form details</SectionLabel>
        {editable && !editing && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-11 sm:h-8"
            ref={editButtonRef}
            onClick={startEditing}
          >
            <Pencil className="size-3.5" aria-hidden />
            Edit
          </Button>
        )}
      </div>

      {!editing ? (
        <dl className="divide-y divide-border border-y border-border">
          <Detail label="Amount claimed">
            <span className="font-medium tabular-nums">
              {viewClaim.currency} {viewClaim.amount_claimed.toFixed(2)}
            </span>
            <ConversionLine claim={viewClaim} />
            {viewClaim.fx_state === "unavailable" && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2"
                loading={refreshFx.isPending}
                onClick={async () => {
                  try {
                    const refreshed = await refreshFx.mutateAsync(viewClaim.id);
                    setViewClaim(refreshed);
                    toast[
                      refreshed.fx_state === "converted" ? "success" : "error"
                    ](
                      refreshed.fx_state === "converted"
                        ? `Converted to ${refreshed.policy_currency} ${(
                            refreshed.amount_converted ?? 0
                          ).toFixed(2)}`
                        : "Still no exchange rate — enter the value during approval.",
                    );
                  } catch (error) {
                    toast.error(formatError(error));
                  }
                }}
              >
                Retry exchange rate
              </Button>
            )}
          </Detail>
          {remainingLimit != null && (
            <Detail label="Remaining limit">
              <span
                className={
                  claimedInPolicyCurrency != null &&
                  claimedInPolicyCurrency > remainingLimit
                    ? "font-medium tabular-nums text-warn"
                    : "tabular-nums"
                }
              >
                {viewClaim.policy_currency} {remainingLimit.toFixed(2)}
              </span>
            </Detail>
          )}
          <Detail label="Member">{memberLabel(viewClaim)}</Detail>
          {viewClaim.dependant_name && (
            <Detail label="Claimant">
              {viewClaim.dependant_name}{" "}
              <span className="text-muted-foreground">(dependant)</span>
            </Detail>
          )}
          <Detail label="Coverage">{coverageLabel(viewClaim)}</Detail>
          <Detail label="Date of treatment">
            <span className="tabular-nums">{fmtDate(viewClaim.incurred_date)}</span>
          </Detail>
          <Detail label="Provider">{viewClaim.provider_name || "—"}</Detail>
          <Detail label="Invoice number">{viewClaim.invoice_number || "—"}</Detail>
          {viewClaim.doctor_name && (
            <Detail label="Doctor seen">{viewClaim.doctor_name}</Detail>
          )}
          <Detail label="Diagnosis / description" wide>
            {viewClaim.diagnosis || "—"}
          </Detail>
          {viewClaim.claim_kind === "insured" &&
            (viewClaim.referral_document || viewClaim.referral_not_applicable) && (
              <Detail label="Referral letter" wide>
                {viewClaim.referral_document
                  ? `${viewClaim.referral_document.file_name} · available in Documents`
                  : "Declared not applicable by the member"}
              </Detail>
            )}
          {viewClaim.is_inpatient && (
            <>
              <Detail label="Hospital sector">
                {effectiveSector ? SECTOR_LABELS[effectiveSector] : "—"}
                {viewClaim.hospital_type && (
                  <span className="text-muted-foreground"> · overridden</span>
                )}
              </Detail>
              <Detail label="Admission date">
                {viewClaim.admission_date ? fmtDate(viewClaim.admission_date) : "—"}
              </Detail>
              <Detail label="Discharge date">
                {viewClaim.discharge_date ? fmtDate(viewClaim.discharge_date) : "—"}
              </Detail>
            </>
          )}
          {viewClaim.claim_kind === "flex" && (
            <>
              <Detail label="Taxable">{viewClaim.taxable ? "Yes" : "No"}</Detail>
              <Detail label="CPF claimable">
                {viewClaim.cpf_claimable ? "Yes" : "No"}
              </Detail>
            </>
          )}
          <Detail label="Admin remark" wide>
            {viewClaim.admin_remarks || "—"}
          </Detail>
          {viewClaim.related_claim && (
            <Detail label="Follows" wide>
              {[
                viewClaim.related_claim.provider_name,
                viewClaim.related_claim.admission_date &&
                viewClaim.related_claim.discharge_date
                  ? `${fmtDate(viewClaim.related_claim.admission_date)} – ${fmtDate(
                      viewClaim.related_claim.discharge_date,
                    )}`
                  : fmtDate(
                      viewClaim.related_claim.admission_date ??
                        viewClaim.related_claim.incurred_date,
                    ),
                viewClaim.related_claim.diagnosis,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Detail>
          )}
        </dl>
      ) : (
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSave && !pending) void save();
          }}
        >
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 border-b border-border pb-4 sm:grid-cols-2">
            <Detail label="Member">{memberLabel(viewClaim)}</Detail>
            <Detail label="Coverage">{coverageLabel(viewClaim)}</Detail>
          </dl>

          {movedUnderUs && (
            <div className="space-y-2 rounded-md border border-border bg-muted p-3 text-xs">
              <p className="text-foreground">
                This claim changed after you opened it. Load the current details
                before saving so another person&rsquo;s update is not overwritten.
              </p>
              <Button type="button" size="sm" variant="outline" onClick={rebase}>
                Load current details
              </Button>
            </div>
          )}

          <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
            <FormField label="Date of treatment" htmlFor="form-incurred-date">
              <Input
                id="form-incurred-date"
                type="date"
                value={draft.incurred_date}
                onChange={(event) => set("incurred_date", event.target.value)}
              />
            </FormField>
            <FormField
              label={`Amount claimed (${viewClaim.currency})`}
              htmlFor="form-amount"
            >
              <Input
                id="form-amount"
                type="number"
                min="0.01"
                step="0.01"
                value={draft.amount_claimed}
                aria-invalid={!amountValid || undefined}
                onChange={(event) => set("amount_claimed", event.target.value)}
              />
            </FormField>
            <FormField label="Provider" htmlFor="form-provider">
              <Input
                id="form-provider"
                value={draft.provider_name}
                maxLength={255}
                onChange={(event) => set("provider_name", event.target.value)}
              />
            </FormField>
            <FormField label="Invoice number" htmlFor="form-invoice">
              <Input
                id="form-invoice"
                value={draft.invoice_number}
                maxLength={128}
                onChange={(event) => set("invoice_number", event.target.value)}
              />
            </FormField>
            <FormField label="Doctor seen" htmlFor="form-doctor">
              <Input
                id="form-doctor"
                value={draft.doctor_name}
                maxLength={255}
                onChange={(event) => set("doctor_name", event.target.value)}
              />
            </FormField>
            <FormField label="Diagnosis / description" htmlFor="form-diagnosis">
              <Input
                id="form-diagnosis"
                value={draft.diagnosis}
                maxLength={512}
                onChange={(event) => set("diagnosis", event.target.value)}
              />
            </FormField>

            {viewClaim.is_inpatient && (
              <>
                <FormField
                  label="Hospital sector"
                  htmlFor="form-hospital-sector"
                  hint={
                    draft.hospital_type
                      ? "Overrides the sector read from the provider."
                      : viewClaim.hospital_type_derived
                        ? `Currently read from ${viewClaim.provider_name ?? "the provider"}.`
                        : "The provider is not in the hospital registry."
                  }
                >
                  <NativeSelect
                    id="form-hospital-sector"
                    className="h-9 w-full"
                    value={draft.hospital_type}
                    onChange={(event) => set("hospital_type", event.target.value)}
                  >
                    <option value="">
                      {viewClaim.hospital_type_derived
                        ? `Auto — ${SECTOR_LABELS[viewClaim.hospital_type_derived]}`
                        : "Auto — not recognised"}
                    </option>
                    <option value="government">Government</option>
                    <option value="private">Private / overseas</option>
                  </NativeSelect>
                </FormField>
                <div className="hidden sm:block" aria-hidden />
                <FormField label="Admission date" htmlFor="form-admission-date">
                  <Input
                    id="form-admission-date"
                    type="date"
                    value={draft.admission_date}
                    onChange={(event) => set("admission_date", event.target.value)}
                  />
                </FormField>
                <FormField label="Discharge date" htmlFor="form-discharge-date">
                  <Input
                    id="form-discharge-date"
                    type="date"
                    value={draft.discharge_date}
                    aria-invalid={invertedDates || undefined}
                    onChange={(event) => set("discharge_date", event.target.value)}
                  />
                </FormField>
              </>
            )}

            {viewClaim.claim_kind === "flex" && (
              <>
                <FormField label="Taxable" htmlFor="form-taxable">
                  <NativeSelect
                    id="form-taxable"
                    className="h-9 w-full"
                    value={draft.taxable}
                    onChange={(event) => set("taxable", event.target.value as Flag)}
                  >
                    <option value="no">No</option>
                    <option value="yes">Yes</option>
                  </NativeSelect>
                </FormField>
                <FormField label="CPF claimable" htmlFor="form-cpf-claimable">
                  <NativeSelect
                    id="form-cpf-claimable"
                    className="h-9 w-full"
                    value={draft.cpf_claimable}
                    onChange={(event) =>
                      set("cpf_claimable", event.target.value as Flag)
                    }
                  >
                    <option value="no">No</option>
                    <option value="yes">Yes</option>
                  </NativeSelect>
                </FormField>
              </>
            )}

            <div className="sm:col-span-2">
              <FormField
                label="Admin remark"
                htmlFor="form-admin-remark"
                hint="Internal only — the member never sees this."
              >
                <textarea
                  id="form-admin-remark"
                  rows={3}
                  maxLength={4000}
                  value={draft.admin_remarks}
                  onChange={(event) => set("admin_remarks", event.target.value)}
                  className="w-full resize-y rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground shadow-sm focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                />
              </FormField>
            </div>
          </div>

          {hasSettlement(viewClaim) && (
            <div className="space-y-3 border-t border-border pt-4">
              <SectionLabel as="h4">Settlement corrections</SectionLabel>
              <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                <FormField label="Sent to insurer" htmlFor="form-sent-to-insurer">
                  <Input
                    id="form-sent-to-insurer"
                    type="date"
                    required
                    value={draft.sent_to_insurer_on}
                    aria-invalid={clearedDispatch || undefined}
                    onChange={(event) =>
                      set("sent_to_insurer_on", event.target.value)
                    }
                  />
                </FormField>
                <FormField
                  label="Insurer deadline"
                  htmlFor="form-insurer-deadline"
                  hint="Blank leaves the claim off the overdue list."
                >
                  <Input
                    id="form-insurer-deadline"
                    type="date"
                    value={draft.insurer_deadline_on}
                    onChange={(event) =>
                      set("insurer_deadline_on", event.target.value)
                    }
                  />
                </FormField>
                {canAmendPayment(viewClaim) && (
                  <>
                    <FormField label="Payment date" htmlFor="form-payment-date">
                      <Input
                        id="form-payment-date"
                        type="date"
                        value={draft.paid_on}
                        onChange={(event) => set("paid_on", event.target.value)}
                      />
                    </FormField>
                    <FormField
                      label={`Amount paid (${viewClaim.policy_currency})`}
                      htmlFor="form-payment-amount"
                      hint="Kept apart from the approved amount."
                    >
                      <Input
                        id="form-payment-amount"
                        type="number"
                        min="0"
                        step="0.01"
                        value={draft.payment_amount}
                        onChange={(event) =>
                          set("payment_amount", event.target.value)
                        }
                      />
                    </FormField>
                  </>
                )}
              </div>
            </div>
          )}

          {needsReason && (
            <FormField
              label="Reason for correction (required)"
              htmlFor="form-correction-reason"
              hint={`This claim is already ${viewClaim.status.replace(/_/g, " ")}; the audit trail must explain the change.`}
            >
              <Input
                id="form-correction-reason"
                value={reason}
                maxLength={500}
                placeholder="e.g. Invoice total was read incorrectly"
                onChange={(event) => setReason(event.target.value)}
              />
            </FormField>
          )}

          {invertedDates && (
            <p className="text-sm text-error">Discharge cannot precede admission.</p>
          )}
          {clearedDispatch && (
            <p className="text-sm text-error">
              A claim sent to the insurer must keep a dispatch date.
            </p>
          )}
          {overpaymentWarning && (
            <p className="rounded-md border border-warn/40 bg-warn-soft px-3 py-2.5 text-sm text-warn">
              {overpaymentWarning} Save again to record this exception.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            <Button type="submit" size="sm" loading={pending} disabled={!canSave}>
              {overpaymentWarning ? "Save exception" : "Save changes"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={cancelEditing}
            >
              Cancel
            </Button>
            {!dirty && (
              <span className="text-xs text-muted-foreground">
                Nothing changed yet.
              </span>
            )}
          </div>
        </form>
      )}
    </section>
  );
}
