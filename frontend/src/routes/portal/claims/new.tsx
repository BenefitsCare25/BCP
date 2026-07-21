/** Submit-a-claim form.
 *
 * There is no separate "claim category" step — the claim-type dropdown is
 * grouped Outpatient (GP/SP/Dental/TCM/Physio) / Inpatient (the four
 * hospital sub-claim types) / Other insurance / Flexible Benefits, and both
 * `claim_kind` AND `sub_type` are DERIVED from the chosen entry, which makes
 * a category/type/sub-type mismatch structurally impossible. Entries come
 * from `/portal/coverage-options` `claim_types`, so the list is plan-aware:
 * TCM/Physio appear only when the member's GP schedule carries a matching
 * row, and Inpatient only when they hold a GHS-family product.
 *
 * Flow:
 *   Who is this claim for?  (Myself / a dependant — only when they have one)
 *     → Claim type          (one grouped dropdown, filtered to the claimant)
 *       → conditional intake fields from the product's claim profile:
 *         specialist → referral letter (upload / reuse / not applicable);
 *         medical types → searchable diagnosis (curated ICD-10 catalog,
 *         "Other" free text).
 *
 * A dependant sees the flex categories PLUS the insured products that cover
 * them (GHS/GMM/GD); products that don't extend to dependants are hidden. The
 * claim is created as a draft, receipts attach, and submit runs the backend
 * validations (intake profile, coverage/eligibility, in-period, duplicates). */
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { AlertTriangle, ArrowLeft, Loader2, Paperclip, Send, X } from "lucide-react";
import { toast } from "sonner";
import {
  useCoverageOptions,
  useCreateClaim,
  useDeleteDraftClaim,
  useDeleteReferralLetter,
  useReferralLetters,
  useSubmitClaim,
  useUploadClaimDocument,
  useUploadReferralLetter,
  type InsuredClaimOption,
} from "@/api/portal";
import { DiagnosisPicker } from "@/components/portal/DiagnosisPicker";
import { formatError } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const ACCEPT = ".pdf,.png,.jpg,.jpeg";
const MAX_BYTES = 15 * 1024 * 1024;

// Fallback only — the live list rides on /portal/coverage-options so the
// backend's ALLOWED_CURRENCIES stays the single source of truth.
const FALLBACK_CURRENCIES = ["SGD", "USD", "MYR", "EUR", "GBP", "AUD"];

// The unified claim-type dropdown encodes the kind, the product, and the
// claim-type entry index in one value (`insured:<code>:<idx>` / `flex:<name>`)
// so `claim_kind` and `sub_type` are derived, never separately chosen.
const INSURED_PREFIX = "insured:";
const FLEX_PREFIX = "flex:";

const MAX_REMARKS = 500;

const GROUP_LABELS = {
  outpatient: "Outpatient",
  inpatient: "Inpatient",
  other: "Other insurance",
} as const;

type InsuredGroupKey = keyof typeof GROUP_LABELS;

interface TypeEntry {
  value: string;
  label: string;
  product: InsuredClaimOption;
}

type ReferralMode = "" | "upload" | "existing" | "na";

const selectClass =
  "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-ring disabled:cursor-not-allowed disabled:opacity-60";

function FieldError({ msg }: { msg?: string }) {
  if (!msg) return null;
  return <p className="text-xs text-error">{msg}</p>;
}

export function PortalNewClaimPage() {
  const navigate = useNavigate();
  const options = useCoverageOptions();
  const createClaim = useCreateClaim();
  const uploadDoc = useUploadClaimDocument();
  const uploadReferral = useUploadReferralLetter();
  const deleteReferral = useDeleteReferralLetter();
  const submitClaim = useSubmitClaim();
  const deleteDraft = useDeleteDraftClaim();

  const insured = options.data?.insured ?? [];
  const flex = options.data?.flex ?? null;
  const dependants = options.data?.dependants ?? [];
  const hasInsured = insured.length > 0;
  const hasFlex = (flex?.categories.length ?? 0) > 0;
  const hasDependants = dependants.length > 0;
  const walletCurrency = flex?.currency ?? "SGD";
  const currencies = options.data?.currencies?.length
    ? options.data.currencies
    : FALLBACK_CURRENCIES;

  // Claimant ("" = the member themself) and the merged claim-type selection.
  const [dependantId, setDependantId] = useState("");
  const [selection, setSelection] = useState("");
  const [incurredDate, setIncurredDate] = useState("");
  const [provider, setProvider] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("SGD");
  const [diagnosis, setDiagnosis] = useState("");
  const [remarks, setRemarks] = useState("");
  const [referralMode, setReferralMode] = useState<ReferralMode>("");
  const [referralFile, setReferralFile] = useState<File | null>(null);
  const [referralExistingId, setReferralExistingId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const referralInput = useRef<HTMLInputElement>(null);

  // Kind + identifiers + sub-type are DERIVED from the single selection.
  const effectiveKind: "insured" | "flex" | null = selection.startsWith(
    INSURED_PREFIX,
  )
    ? "insured"
    : selection.startsWith(FLEX_PREFIX)
      ? "flex"
      : null;
  const insuredParts =
    effectiveKind === "insured"
      ? selection.slice(INSURED_PREFIX.length).split(":")
      : null;
  const productCode = insuredParts ? insuredParts[0] : "";
  const claimTypeIndex =
    insuredParts && insuredParts.length > 1 ? Number(insuredParts[1]) : -1;
  const flexCategory =
    effectiveKind === "flex" ? selection.slice(FLEX_PREFIX.length) : "";
  const effectiveCurrency = effectiveKind === "flex" ? walletCurrency : currency;

  const selectedProduct: InsuredClaimOption | null = useMemo(
    () => insured.find((p) => p.product_code === productCode) ?? null,
    [insured, productCode],
  );
  const selectedClaimType =
    selectedProduct?.claim_types[claimTypeIndex] ?? null;
  const subType = selectedClaimType?.sub_type ?? null;

  // Insured products offered for the current claimant: everything for the
  // member; for a dependant, only products that actually cover them.
  const claimantInsured = useMemo(() => {
    if (!dependantId) return insured;
    return insured.filter(
      (p) => p.covers_dependants && p.covered_dependant_ids.includes(dependantId),
    );
  }, [insured, dependantId]);

  // Grouped dropdown entries: Outpatient / Inpatient / Other insurance.
  // Duplicate labels within a group (two GP products, GHS + GMM inpatient)
  // get the product name appended so the entries stay distinguishable.
  const insuredGroups = useMemo(() => {
    const groups: Record<InsuredGroupKey, TypeEntry[]> = {
      outpatient: [],
      inpatient: [],
      other: [],
    };
    for (const p of claimantInsured) {
      const cat: InsuredGroupKey =
        p.category === "outpatient" || p.category === "inpatient"
          ? p.category
          : "other";
      p.claim_types.forEach((t, i) => {
        groups[cat].push({
          value: `${INSURED_PREFIX}${p.product_code}:${i}`,
          label: t.label,
          product: p,
        });
      });
    }
    for (const key of Object.keys(groups) as InsuredGroupKey[]) {
      const counts = new Map<string, number>();
      for (const e of groups[key]) {
        counts.set(e.label, (counts.get(e.label) ?? 0) + 1);
      }
      groups[key] = groups[key].map((e) =>
        (counts.get(e.label) ?? 0) > 1
          ? {
              ...e,
              label: `${e.label} — ${e.product.product_name || e.product.product_code}`,
            }
          : e,
      );
    }
    return groups;
  }, [claimantInsured]);

  const noTypesForClaimant = claimantInsured.length === 0 && !hasFlex;
  const needsReferral = selectedProduct?.requires_referral ?? false;
  const referralLetters = useReferralLetters(needsReferral);
  const showDiagnosisPicker =
    effectiveKind === "insured" && (selectedProduct?.diagnosis_group ?? null) !== null;

  if (options.isLoading) return <Skeleton className="h-64 w-full" />;
  if (options.isError || !options.data || (!hasInsured && !hasFlex)) {
    return (
      <p className="text-sm text-muted-foreground">
        No active coverage — claims can't be submitted right now.
      </p>
    );
  }

  const yearStart = options.data.policy_year_start;
  const yearEnd = options.data.policy_year_end;
  // Claims can't be incurred in the future — clamp to today when today falls
  // inside the policy window (a seeded future-dated year keeps its own span).
  const today = new Date().toISOString().slice(0, 10);
  const maxIncurred = today >= yearStart && today <= yearEnd ? today : yearEnd;

  // Fields that depend on the chosen claim type — reset when the type changes.
  const resetTypeFields = () => {
    setDiagnosis("");
    setReferralMode("");
    setReferralFile(null);
    setReferralExistingId("");
    setFieldErrors({});
  };

  const changeSelection = (next: string) => {
    setSelection(next);
    resetTypeFields();
    // Insured currency is member-selectable (default SGD); flex locks to the
    // wallet currency, handled by effectiveCurrency.
    if (!next.startsWith(INSURED_PREFIX)) setCurrency("SGD");
  };

  // Changing the claimant can invalidate the chosen insured product (it may
  // not cover the new claimant), so reset the type selection too.
  const changeClaimant = (next: string) => {
    setDependantId(next);
    setSelection("");
    resetTypeFields();
    setCurrency("SGD");
  };

  const pickFiles = (picked: FileList | null) => {
    if (!picked) return;
    const next: File[] = [];
    for (const f of Array.from(picked)) {
      if (f.size > MAX_BYTES) {
        toast.error(`${f.name} exceeds 15 MB`);
        continue;
      }
      next.push(f);
    }
    setFiles((prev) => [...prev, ...next]);
    if (fileInput.current) fileInput.current.value = "";
  };

  const validate = (): Record<string, string> => {
    const errs: Record<string, string> = {};
    if (!effectiveKind) {
      errs.claim_type = "Select what you're claiming for.";
    } else if (effectiveKind === "insured") {
      if (
        selectedProduct?.diagnosis_required &&
        !diagnosis.trim().replace(/^Other:\s*$/, "")
      ) {
        errs.diagnosis =
          "Select the diagnosis (choose 'Other' if it isn't listed).";
      }
      if (needsReferral) {
        if (!referralMode) errs.referral = "Choose how to provide the referral letter.";
        if (referralMode === "upload" && !referralFile) {
          errs.referral = "Attach the referral letter.";
        }
        if (referralMode === "existing" && !referralExistingId) {
          errs.referral = "Pick one of your previous referral letters.";
        }
      }
    }
    if (!incurredDate) {
      errs.incurred_date = "Enter the date on the bill.";
    } else if (incurredDate < yearStart || incurredDate > yearEnd) {
      errs.incurred_date = `Must fall within your policy year (${yearStart} to ${yearEnd}).`;
    } else if (incurredDate > today) {
      errs.incurred_date = "The incurred date can't be in the future.";
    }
    if (provider.trim().length < 2) errs.provider = "Enter the clinic or provider name.";
    if (!invoiceNumber.trim()) errs.invoice = "Enter the invoice or receipt number.";
    const amt = Number(amount);
    if (!(amt > 0)) {
      errs.amount = "Enter the amount on the receipt.";
    } else if (amt > 1_000_000) {
      errs.amount = "Amount looks too large — check the receipt.";
    }
    if (files.length === 0) errs.files = "Attach at least one receipt.";
    return errs;
  };

  const submit = async () => {
    const errs = validate();
    setFieldErrors(errs);
    if (Object.keys(errs).length > 0) {
      setError("Fix the highlighted fields before submitting.");
      return;
    }
    if (!effectiveKind) return; // guarded by validate; satisfies the type
    setError(null);
    setBusy(true);
    let claimId: string | null = null;
    // A referral letter we uploaded THIS attempt — deleted on rollback so a
    // failed submission doesn't leave an orphaned letter in storage. A reused
    // existing letter is never touched.
    let uploadedReferralId: string | null = null;
    try {
      // Specialist flow: the referral letter is a member-level document —
      // upload it first, then reference it from the claim.
      let referralDocumentId: string | null = null;
      if (needsReferral && referralMode === "upload" && referralFile) {
        const letter = await uploadReferral.mutateAsync(referralFile);
        referralDocumentId = letter.id;
        uploadedReferralId = letter.id;
      } else if (needsReferral && referralMode === "existing") {
        referralDocumentId = referralExistingId;
      }

      const claim = await createClaim.mutateAsync({
        claim_kind: effectiveKind,
        product_code: effectiveKind === "insured" ? productCode : null,
        flex_category_name: effectiveKind === "flex" ? flexCategory : null,
        claim_type:
          effectiveKind === "flex"
            ? flexCategory
            : selectedClaimType?.label || productCode,
        sub_type: effectiveKind === "insured" ? subType : null,
        incurred_date: incurredDate,
        provider_name: provider.trim(),
        invoice_number: invoiceNumber.trim(),
        diagnosis: diagnosis.trim() || null,
        remarks: remarks.trim() || null,
        amount_claimed: Number(amount),
        currency: effectiveCurrency,
        dependant_id: dependantId || null,
        referral_document_id: referralDocumentId,
        referral_not_applicable: needsReferral && referralMode === "na",
      });
      claimId = claim.id;
      for (const file of files) {
        await uploadDoc.mutateAsync({ claimId: claim.id, file });
      }
      await submitClaim.mutateAsync(claim.id);
      toast.success("Claim submitted");
      void navigate({ to: "/portal/claims" });
    } catch (err) {
      setError(formatError(err));
      // Roll the draft back so a failed validation doesn't strand it — before
      // the referral, so the letter is no longer referenced when we delete it.
      if (claimId) {
        try {
          await deleteDraft.mutateAsync(claimId);
        } catch {
          /* already submitted or gone — leave it for the list view */
        }
      }
      if (uploadedReferralId) {
        try {
          await deleteReferral.mutateAsync(uploadedReferralId);
        } catch {
          /* still referenced or already gone — reusable, so harmless */
        }
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-lg space-y-4">
      <button
        type="button"
        onClick={() => void navigate({ to: "/portal/claims" })}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> Back to claims
      </button>

      <div className="rounded-lg border border-border bg-card p-5 space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Submit a claim</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Attach at least one receipt. Claims must be incurred within your
            policy year ({yearStart} to {yearEnd}); your broker reviews every
            claim before it's approved.
          </p>
        </div>

        {/* Who is this claim for? — a covered dependant filters the claim-type
            list to what applies to them (flex + the products that cover them). */}
        {hasDependants && (
          <div className="space-y-1.5">
            <Label>Who is this claim for?</Label>
            <select
              className={selectClass}
              value={dependantId}
              onChange={(e) => changeClaimant(e.target.value)}
            >
              <option value="">Myself</option>
              {dependants.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name ?? "Dependant"}
                  {d.relationship ? ` (${d.relationship})` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Claim type — one grouped dropdown; category is derived, not chosen. */}
        <div className="space-y-1.5">
          <Label>
            Claim type <span className="text-error">*</span>
          </Label>
          {noTypesForClaimant ? (
            <p className="text-xs text-muted-foreground">
              This dependant has no claimable benefits — pick a different
              claimant.
            </p>
          ) : (
            <select
              className={selectClass}
              value={selection}
              onChange={(e) => changeSelection(e.target.value)}
            >
              <option value="">Select an option</option>
              {(Object.keys(GROUP_LABELS) as InsuredGroupKey[]).map(
                (key) =>
                  insuredGroups[key].length > 0 && (
                    <optgroup key={key} label={GROUP_LABELS[key]}>
                      {insuredGroups[key].map((entry) => (
                        <option key={entry.value} value={entry.value}>
                          {entry.label}
                        </option>
                      ))}
                    </optgroup>
                  ),
              )}
              {hasFlex && (
                <optgroup label="Flexible Benefits">
                  {flex?.categories.map((c) => (
                    <option key={c.name} value={`${FLEX_PREFIX}${c.name}`}>
                      {c.name}
                      {c.sub_limit != null ? ` (up to ${c.sub_limit})` : ""}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
          )}
          <FieldError msg={fieldErrors.claim_type} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="claim-date">
              Incurred date <span className="text-error">*</span>
            </Label>
            <Input
              id="claim-date"
              type="date"
              min={yearStart}
              max={maxIncurred}
              value={incurredDate}
              onChange={(e) => setIncurredDate(e.target.value)}
            />
            <FieldError msg={fieldErrors.incurred_date} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="claim-provider">
              Provider / clinic <span className="text-error">*</span>
            </Label>
            <Input
              id="claim-provider"
              placeholder="e.g. Raffles Medical"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            />
            <FieldError msg={fieldErrors.provider} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="claim-invoice">
              Invoice number <span className="text-error">*</span>
            </Label>
            <Input
              id="claim-invoice"
              placeholder="e.g. INV-00123"
              value={invoiceNumber}
              maxLength={128}
              onChange={(e) => setInvoiceNumber(e.target.value)}
            />
            <FieldError msg={fieldErrors.invoice} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="claim-currency">
              Currency <span className="text-error">*</span>
            </Label>
            <select
              id="claim-currency"
              className={selectClass}
              value={effectiveCurrency}
              disabled={effectiveKind === "flex"}
              onChange={(e) => setCurrency(e.target.value)}
            >
              {effectiveKind === "flex" ? (
                <option value={walletCurrency}>{walletCurrency}</option>
              ) : (
                currencies.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))
              )}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="claim-amount">
              Incurred amount <span className="text-error">*</span>
            </Label>
            <Input
              id="claim-amount"
              type="number"
              min="0.01"
              step="0.01"
              placeholder="0.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
            <FieldError msg={fieldErrors.amount} />
          </div>
        </div>

        {/* Wrong-currency guard: bills incurred in Singapore are almost always
            SGD — nudge before the AI review flags a mismatch. */}
        {effectiveKind === "insured" && effectiveCurrency !== "SGD" && (
          <p className="flex items-start gap-1.5 text-xs text-warn">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            Double-check the receipt — most Singapore bills are in SGD. Claims
            in {effectiveCurrency} need broker confirmation of the conversion.
          </p>
        )}

        {/* Diagnosis — searchable catalog scoped to the claim type. */}
        {showDiagnosisPicker && selectedProduct && (
          <div className="space-y-1.5">
            <Label>
              Diagnosis{" "}
              {selectedProduct.diagnosis_required && (
                <span className="text-error">*</span>
              )}
            </Label>
            <DiagnosisPicker
              // Remount on product change so the internal search text/open
              // state can't carry over to a different diagnosis group.
              key={selectedProduct.product_code}
              productCode={selectedProduct.product_code}
              value={diagnosis}
              onChange={setDiagnosis}
            />
            <FieldError msg={fieldErrors.diagnosis} />
          </div>
        )}

        {/* Specialist claims: referral letter (upload / reuse / N/A). */}
        {needsReferral && (
          <div className="space-y-1.5">
            <Label>
              Upload or select referral letter <span className="text-error">*</span>
            </Label>
            <select
              className={selectClass}
              value={referralMode}
              onChange={(e) => {
                setReferralMode(e.target.value as ReferralMode);
                setReferralFile(null);
                setReferralExistingId("");
              }}
            >
              <option value="">Select an option</option>
              <option value="upload">Upload referral letter</option>
              <option
                value="existing"
                disabled={(referralLetters.data?.length ?? 0) === 0}
              >
                Select existing referral letter
              </option>
              <option value="na">Not applicable</option>
            </select>
            {referralMode === "upload" && (
              <div>
                <input
                  ref={referralInput}
                  type="file"
                  accept={ACCEPT}
                  className="hidden"
                  onChange={(e) => setReferralFile(e.target.files?.[0] ?? null)}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => referralInput.current?.click()}
                >
                  <Paperclip className="size-4" />
                  {referralFile ? referralFile.name : "Attach referral letter"}
                </Button>
              </div>
            )}
            {referralMode === "existing" && (
              <select
                className={selectClass}
                value={referralExistingId}
                onChange={(e) => setReferralExistingId(e.target.value)}
              >
                <option value="">Select a letter</option>
                {(referralLetters.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.file_name} ({new Date(d.created_at).toLocaleDateString()})
                  </option>
                ))}
              </select>
            )}
            {referralMode === "na" && (
              <p className="text-xs text-muted-foreground">
                Your broker will confirm the visit didn't need a referral.
              </p>
            )}
            <FieldError msg={fieldErrors.referral} />
          </div>
        )}

        <div className="space-y-1.5">
          <Label>
            Invoice &amp; receipt / supporting documents{" "}
            <span className="text-error">*</span>
          </Label>
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => pickFiles(e.target.files)}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => fileInput.current?.click()}
          >
            <Paperclip className="size-4" /> Attach receipt (PDF or photo)
          </Button>
          {files.length > 0 && (
            <ul className="space-y-1 pt-1">
              {files.map((f, i) => (
                <li
                  key={`${f.name}-${i}`}
                  className="flex items-center justify-between rounded-md bg-muted px-2.5 py-1.5 text-xs"
                >
                  <span className="truncate text-foreground">{f.name}</span>
                  <button
                    type="button"
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                    className="ml-2 text-muted-foreground hover:text-foreground"
                  >
                    <X className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <FieldError msg={fieldErrors.files} />
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="claim-remarks">Remarks</Label>
            <span className="text-xs text-muted-foreground/60">
              {remarks.length}/{MAX_REMARKS}
            </span>
          </div>
          <textarea
            id="claim-remarks"
            rows={3}
            className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-ring"
            placeholder="Anything your broker should know about this claim (optional)"
            value={remarks}
            maxLength={MAX_REMARKS}
            onChange={(e) => setRemarks(e.target.value)}
          />
        </div>

        {error && <p className="text-xs text-error">{error}</p>}

        <Button className="w-full" disabled={busy} onClick={submit}>
          {busy ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
          <span className="ml-1.5">Submit claim</span>
        </Button>
      </div>
    </div>
  );
}
