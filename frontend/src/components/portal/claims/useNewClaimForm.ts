/** The claim form's state machine.
 *
 * Everything the form knows lives here; the section components under
 * `components/portal/claims/` are presentation over this one object. Extracted
 * from the old single-component form WITHOUT behaviour change — the ordering
 * rules, the rollback, the concurrent uploads and the multi-invoice queue are
 * all carried over as they were, comments included, because each of them is a
 * bug that was already found once.
 *
 * Flow:
 *   Who is this claim for?  (Myself / a dependant — only when they have one)
 *     → Claim type          (one grouped dropdown, filtered to the claimant)
 *       → conditional intake fields from the product's claim profile
 *       → required documents (one tagged upload per slot; submit blocks until
 *         each is filled) */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import {
  useCoverageOptions,
  useCreateClaim,
  useDeleteDraftClaim,
  useDeleteReferralLetter,
  useExtractClaimIntake,
  useReferralLetters,
  useSubmitClaim,
  useUploadClaimDocument,
  useUploadReferralLetter,
  type ClaimIntakeSuggestion,
  type InsuredClaimOption,
} from "@/api/portal";
import { usePortalSession } from "@/stores/portalSession";
import { todayISO } from "@/components/portal/leaf/date";
import { formatError } from "@/lib/errors";
import { validateClaim } from "./claimValidation";
import {
  FLEX_PREFIX,
  INSURED_PREFIX,
  MAX_AUTOFILL_FILES,
  MAX_BYTES,
  OTHER_HOSPITAL,
  GROUP_LABELS,
  normHospital,
  planFromSuggestion,
  sectorForHospital,
  type AutofillDoc,
  type InsuredGroupKey,
  type PendingClaim,
  type ReferralMode,
  type TypeEntry,
} from "./claimForm";
import { useCompany } from "@/components/portal/useCompany";

export type NewClaimForm = ReturnType<typeof useNewClaimForm>;

export function useNewClaimForm() {
  const navigate = useNavigate();
  const company = useCompany();
  const options = useCoverageOptions();
  const createClaim = useCreateClaim();
  const uploadDoc = useUploadClaimDocument();
  const uploadReferral = useUploadReferralLetter();
  const deleteReferral = useDeleteReferralLetter();
  const submitClaim = useSubmitClaim();
  const deleteDraft = useDeleteDraftClaim();
  const extractIntake = useExtractClaimIntake();

  const member = usePortalSession((s) => s.member);

  const insured = options.data?.insured ?? [];
  const flex = options.data?.flex ?? null;
  const dependants = options.data?.dependants ?? [];
  const hasFlex = (flex?.categories.length ?? 0) > 0;
  const walletCurrency = flex?.currency ?? "SGD";

  // Claimant ("" = the member themself) and the merged claim-type selection.
  const [dependantId, setDependantId] = useState("");
  const [selection, setSelection] = useState("");
  const [incurredDate, setIncurredDate] = useState("");
  const [provider, setProvider] = useState("");
  // Hospitalisation claims: hospital picked from the registry ("" = not yet,
  // OTHER_HOSPITAL = unlisted → free-text provider input).
  const [hospital, setHospital] = useState("");
  const [visitType, setVisitType] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("SGD");
  const [diagnosis, setDiagnosis] = useState("");
  const [remarks, setRemarks] = useState("");
  const [referralMode, setReferralMode] = useState<ReferralMode>("");
  const [referralFile, setReferralFile] = useState<File | null>(null);
  const [referralExistingId, setReferralExistingId] = useState("");
  // One file per required-document slot (keyed by slot key) + optional extras.
  const [slotFiles, setSlotFiles] = useState<Record<string, File | null>>({});
  const [files, setFiles] = useState<File[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Document-driven autofill: the document set (up to 3) the member uploaded to
  // prefill the form — each reused as the claim's evidence in the slot the AI
  // identified it as, so they don't upload twice.
  const [autofillDocs, setAutofillDocs] = useState<AutofillDoc[]>([]);
  const [autofillNote, setAutofillNote] = useState<string | null>(null);
  // Multi-invoice upload: the claims still to submit after this one (one per
  // distinct invoice) and how many were already submitted in this run.
  const [pendingClaims, setPendingClaims] = useState<PendingClaim[]>([]);
  const [multiDone, setMultiDone] = useState(0);
  const [lowConfidence, setLowConfidence] = useState<string[]>([]);
  // Autofill files the member has manually removed from a slot — the auto-place
  // effect must not re-add them on a later claim-type change.
  const clearedFiles = useRef<Set<File>>(new Set());

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
  const selectedClaimType = selectedProduct?.claim_types[claimTypeIndex] ?? null;
  const subType = selectedClaimType?.sub_type ?? null;

  // Hospitalisation/Day Surgery: the provider is a hospital picked from the
  // registry, and its sector (govt/private) decides the document slots. An
  // "Other" hospital is classified by the typed name — mirroring the backend
  // `hospital_sector`, so a member who types a listed hospital into the free
  // text still gets that hospital's sector (and the form/backend can't
  // disagree about which documents are required). Unlisted → the private set.
  const hospitals = options.data?.hospitals ?? [];
  const isHospitalisation = !!selectedClaimType?.doc_slots_by_sector;
  const effectiveProvider =
    isHospitalisation && hospital && hospital !== OTHER_HOSPITAL
      ? hospital
      : provider;
  const hospitalSector = isHospitalisation
    ? sectorForHospital(hospitals, effectiveProvider)
    : null;
  const docSlots =
    effectiveKind === "flex"
      ? (flex?.doc_slots ?? [])
      : isHospitalisation && hospitalSector
        ? (selectedClaimType?.doc_slots_by_sector?.[hospitalSector] ??
          selectedClaimType?.doc_slots ??
          [])
        : (selectedClaimType?.doc_slots ?? []);
  const docSlotKey = docSlots.map((s) => s.key).join(",");

  // Insured products offered for the current claimant: everything for the
  // member; for a dependant, only products that actually cover them.
  const claimantInsured = useMemo(() => {
    if (!dependantId) return insured;
    return insured.filter(
      (p) =>
        p.covers_dependants && p.covered_dependant_ids.includes(dependantId),
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

  const needsReferral = selectedProduct?.requires_referral ?? false;
  const referralLetters = useReferralLetters(needsReferral);

  // Follow-up visits reuse the member's latest referral letter on file —
  // auto-select it once the letters load; the member can still change it.
  useEffect(() => {
    if (
      visitType === "follow_up" &&
      !referralMode &&
      (referralLetters.data?.length ?? 0) > 0
    ) {
      const latest = [...(referralLetters.data ?? [])].sort((a, b) =>
        b.created_at.localeCompare(a.created_at),
      )[0];
      setReferralMode("existing");
      setReferralExistingId(latest.id);
    }
  }, [visitType, referralMode, referralLetters.data]);

  // Each autofill document fills a required-document slot once a claim type is
  // chosen — the slot the AI matched it to (discharge summary → the
  // discharge_summary slot), else the first free slot — so they count as the
  // claim's evidence and validation sees them (idempotent: never overwrites a
  // slot the member already filled, and skips files they've removed).
  useEffect(() => {
    if (autofillDocs.length === 0 || docSlots.length === 0) return;
    setSlotFiles((prev) => {
      const next = { ...prev };
      const placed = new Set(Object.values(next));
      let changed = false;
      for (const { file, slot } of autofillDocs) {
        if (clearedFiles.current.has(file) || placed.has(file)) continue;
        const matched = slot && docSlots.find((s) => s.key === slot)?.key;
        const target = matched || docSlots.find((s) => !next[s.key])?.key;
        if (target && !next[target]) {
          next[target] = file;
          placed.add(file);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autofillDocs, docSlotKey]);

  // Hospitalisation: map the extracted provider to a registry hospital (or the
  // "Other" free-text path) once the type resolves the picker into view.
  useEffect(() => {
    if (autofillDocs.length === 0 || !isHospitalisation || hospital || !provider)
      return;
    const match = hospitals.find(
      (h) => normHospital(h.name) === normHospital(provider),
    );
    setHospital(match ? match.name : OTHER_HOSPITAL);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autofillDocs, isHospitalisation, hospital, provider]);

  const yearStart = options.data?.policy_year_start ?? "";
  const yearEnd = options.data?.policy_year_end ?? "";
  // Claims can't be incurred in the future — clamp to today when today falls
  // inside the policy window (a seeded future-dated year keeps its own span).
  const today = todayISO();
  const maxIncurred = today >= yearStart && today <= yearEnd ? today : yearEnd;

  // Fields that depend on the chosen claim type — reset when the type changes.
  const resetTypeFields = () => {
    setDiagnosis("");
    setVisitType("");
    setHospital("");
    setSlotFiles({});
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

  // Apply the AI reading of the document set to the form. Order matters:
  // claimant first (it filters the claim-type list), then the type, then the
  // fields. Each uploaded file is paired with the slot the AI matched it to.
  const applySuggestion = (s: ClaimIntakeSuggestion, picked: File[]) => {
    const plan = planFromSuggestion(s, picked);
    setAutofillDocs(plan.autofillDocs);
    setPendingClaims(plan.pendingClaims);
    setMultiDone(0);
    if (!s.available) {
      setLowConfidence([]);
      setAutofillNote(
        s.reason ?? "Autofill is unavailable — please fill in the claim below.",
      );
      return;
    }
    if (s.claimant) {
      setDependantId(
        s.claimant.kind === "dependant" ? (s.claimant.dependant_id ?? "") : "",
      );
    }
    resetTypeFields();
    const insuredPick = s.claim_selection?.startsWith(INSURED_PREFIX) ?? false;
    if (s.claim_selection) {
      setSelection(s.claim_selection);
      if (!insuredPick) setCurrency("SGD");
    }
    const f = s.fields;
    if (f.provider_name) setProvider(f.provider_name);
    if (f.incurred_date) setIncurredDate(f.incurred_date);
    if (f.invoice_number) setInvoiceNumber(f.invoice_number);
    if (f.amount != null) setAmount(String(f.amount));
    if (f.currency && insuredPick) setCurrency(f.currency);
    // The backend returns the diagnosis in its final form — a catalog label to
    // select, or "Other: <text>" free text — so set it directly.
    if (f.diagnosis) setDiagnosis(f.diagnosis);
    setLowConfidence(s.low_confidence);
    const parts = [
      "We filled in what we could read from your documents — please check everything before submitting.",
    ];
    if (!s.claim_selection && s.claim_candidates.length > 0) {
      parts.push("Confirm the claim type below.");
    }
    if (!s.claimant) parts.push("Confirm who this claim is for.");
    setAutofillNote(parts.join(" "));
  };

  const runAutofill = async (picked: File[]) => {
    const capped = picked.slice(0, MAX_AUTOFILL_FILES);
    if (picked.length > MAX_AUTOFILL_FILES) {
      toast.error(`Upload at most ${MAX_AUTOFILL_FILES} documents to autofill.`);
    }
    const withinSize = capped.filter((f) => {
      if (f.size > MAX_BYTES) {
        toast.error(`${f.name} exceeds 15 MB`);
        return false;
      }
      return true;
    });
    if (withinSize.length === 0) return;
    clearedFiles.current = new Set(); // a fresh set — allow auto-placement
    try {
      const suggestion = await extractIntake.mutateAsync(withinSize);
      applySuggestion(suggestion, withinSize);
    } catch (err) {
      // Extraction failed — keep the files as evidence, just no prefill.
      setAutofillDocs(
        withinSize.map((file) => ({ file, slot: null, detectedType: null })),
      );
      setPendingClaims([]);
      setMultiDone(0);
      setLowConfidence([]);
      setAutofillNote(
        "We couldn't read these files for autofill — fill in the claim and they'll still be attached.",
      );
      toast.error(formatError(err));
    }
  };

  // Multi-invoice flow: after one claim submits, load the next queued invoice
  // into a fresh form. Claimant + claim type carry over (the invoices almost
  // always share them and both stay editable); everything per-visit resets.
  const advanceToNextClaim = () => {
    const [next, ...rest] = pendingClaims;
    if (!next) return;
    setMultiDone((d) => d + 1);
    setPendingClaims(rest);
    resetTypeFields();
    setFiles([]);
    setRemarks("");
    setError(null);
    setLowConfidence(next.lowConfidence);
    clearedFiles.current = new Set();
    const f = next.fields;
    setIncurredDate(f?.incurred_date ?? "");
    setProvider(f?.provider_name ?? "");
    setInvoiceNumber(f?.invoice_number ?? "");
    setAmount(f?.amount != null ? String(f.amount) : "");
    if (f?.currency && effectiveKind === "insured") setCurrency(f.currency);
    setDiagnosis(f?.diagnosis ?? "");
    setAutofillDocs(
      next.file
        ? [{ file: next.file, slot: next.slot, detectedType: next.detectedType }]
        : [],
    );
    setAutofillNote(
      next.file
        ? `We've filled in the next claim from ${next.fileName} — check everything before submitting.`
        : `We've filled in the next claim from ${next.fileName}, but couldn't reuse the file — attach the invoice below.`,
    );
    window.scrollTo({ top: 0, behavior: "smooth" });
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
  };

  const removeSlotFile = (key: string) => {
    const current = slotFiles[key];
    // If this was an autofilled document, remember the removal so the
    // auto-place effect won't re-add it.
    if (current && autofillDocs.some((d) => d.file === current)) {
      clearedFiles.current.add(current);
    }
    setSlotFiles((prev) => ({ ...prev, [key]: null }));
  };

  const setSlotFile = (key: string, file: File) =>
    setSlotFiles((prev) => ({ ...prev, [key]: file }));

  /** Full removal of an autofill file: never re-place it into a slot AND don't
   * attach it on submit. */
  const dropAutofillFile = (file: File) => {
    clearedFiles.current.add(file);
    setAutofillDocs((prev) => prev.filter((d) => d.file !== file));
  };

  const validate = () =>
    validateClaim({
      effectiveKind,
      selectedProduct,
      diagnosis,
      needsReferral,
      visitType,
      referralLoading: referralLetters.isLoading,
      referralCount: referralLetters.data?.length ?? 0,
      referralMode,
      referralFile,
      referralExistingId,
      incurredDate,
      yearStart,
      yearEnd,
      today,
      isHospitalisation,
      hospital,
      provider,
      invoiceNumber,
      amount,
      docSlots,
      slotFiles,
    });

  /** Documents upload CONCURRENTLY. A hospitalisation claim carries an invoice,
   * an itemised bill and a discharge summary, and awaiting them one at a time
   * spent four round-trips of phone-network latency behind a single
   * undifferentiated spinner — on the slowest step of the slowest task in the
   * product.
   *
   * `allSettled`, not `all`: `all` rejects on the FIRST failure while the others
   * are still in flight, and the caller's rollback would then delete the draft
   * claim out from under an upload still arriving against it. Waiting for every
   * upload to settle means the claim is quiet before it is torn down; the first
   * rejection is then re-thrown unchanged so the member still sees the real
   * reason. */
  const uploadEvidence = async (claimId: string) => {
    // Each autofill document is evidence — attach any not already placed in a
    // slot (and not among the additional documents) as an untagged extra.
    const slotted = Object.values(slotFiles);
    const extras = [...files];
    for (const { file } of autofillDocs) {
      if (!slotted.includes(file) && !extras.includes(file)) extras.push(file);
    }
    const uploads = [
      ...docSlots.flatMap((slot) => {
        const file = slotFiles[slot.key];
        return file
          ? [uploadDoc.mutateAsync({ claimId, file, docType: slot.key })]
          : [];
      }),
      ...extras.map((file) => uploadDoc.mutateAsync({ claimId, file })),
    ];
    const settled = await Promise.allSettled(uploads);
    const failure = settled.find((r) => r.status === "rejected");
    if (failure) throw (failure as PromiseRejectedResult).reason;
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
        visit_type: needsReferral && visitType ? visitType : null,
        incurred_date: incurredDate,
        provider_name: effectiveProvider.trim(),
        invoice_number: invoiceNumber.trim(),
        diagnosis: diagnosis.trim() || null,
        remarks: remarks.trim() || null,
        amount_claimed: Number(amount),
        currency: effectiveCurrency,
        dependant_id: dependantId || null,
        referral_document_id: referralDocumentId,
        referral_not_applicable: false,
      });
      claimId = claim.id;
      await uploadEvidence(claim.id);
      await submitClaim.mutateAsync(claim.id);
      if (pendingClaims.length > 0) {
        // Mid-queue: stay on the form and load the next invoice. The receipt
        // waits until the last one, so the member isn't bounced out of a run.
        const done = multiDone + 1;
        toast.success(
          `Claim ${done} of ${done + pendingClaims.length} submitted`,
        );
        advanceToNextClaim();
      } else {
        // THE RECEIPT: the claim's own page, which states what was sent, lists
        // every document and carries the status from here on. It replaces a
        // three-second toast that left nothing behind.
        void navigate({
          to: "/portal/$company/claims/$claimId",
          params: { company, claimId: claim.id },
          search: { submitted: true },
        });
      }
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

  // Autofill files not placed into a required-document slot are attached as
  // additional documents on submit — surfaced in that section (removable) so
  // nothing rides along invisibly.
  const slottedSet = new Set(Object.values(slotFiles).filter(Boolean));
  const unplacedAutofill = autofillDocs
    .map((d) => d.file)
    .filter((f) => !slottedSet.has(f) && !files.includes(f));

  return {
    // queries
    options,
    insured,
    flex,
    dependants,
    hospitals,
    referralLetters,
    extractIntake,
    // vocabulary
    hasFlex,
    hasDependants: dependants.length > 0,
    /** The member's own name, for the claimant picker's first option. Every
     * other entry in that list is a person by name, so "Myself" was the one
     * row phrased differently from its siblings — and on a shared or
     * HR-assisted screen it is also the one row that does not say who it
     * means. Falls back to "Myself" when the roster carries no display name,
     * which is the same fallback the shell's heading makes. */
    memberName: member?.display_name?.trim() || "Myself",
    noTypesForClaimant: claimantInsured.length === 0 && !hasFlex,
    insuredGroups,
    groupLabels: GROUP_LABELS,
    currencies: options.data?.currencies ?? [],
    walletCurrency,
    yearStart,
    maxIncurred,
    // selection
    dependantId,
    selection,
    effectiveKind,
    selectedProduct,
    subType,
    isHospitalisation,
    needsReferral,
    showDiagnosisPicker:
      effectiveKind === "insured" &&
      (selectedProduct?.diagnosis_group ?? null) !== null,
    docSlots,
    effectiveCurrency,
    // fields
    incurredDate,
    setIncurredDate,
    provider,
    setProvider,
    hospital,
    setHospital,
    visitType,
    invoiceNumber,
    setInvoiceNumber,
    amount,
    setAmount,
    setCurrency,
    diagnosis,
    setDiagnosis,
    remarks,
    setRemarks,
    referralMode,
    referralFile,
    setReferralFile,
    referralExistingId,
    setReferralExistingId,
    slotFiles,
    setSlotFile,
    removeSlotFile,
    files,
    setFiles,
    autofillDocs,
    autofillNote,
    lowConfidence,
    pendingClaims,
    setPendingClaims,
    multiDone,
    unplacedAutofill,
    // state
    fieldErrors,
    error,
    busy,
    // actions
    changeClaimant,
    changeSelection,
    setVisitType: (next: string) => {
      setVisitType(next);
      setReferralMode("");
      setReferralFile(null);
      setReferralExistingId("");
    },
    setReferralMode: (next: ReferralMode) => {
      setReferralMode(next);
      setReferralFile(null);
      setReferralExistingId("");
    },
    runAutofill,
    pickFiles,
    dropAutofillFile,
    submit,
  };
}
