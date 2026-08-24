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
  useClaimAnchors,
  type ClaimFormDraftData,
  useCoverageOptions,
  useCreateClaim,
  useDeleteDraftClaim,
  useDeleteReferralLetter,
  useExtractClaimIntake,
  useFxQuote,
  useReferralLetters,
  useSubmitClaim,
  useUploadClaimDocument,
  useUploadReferralLetter,
  type AnchorMode,
  type ClaimIntakeSuggestion,
  type InsuredClaimOption,
} from "@/api/portal";
import { ConflictDetailError } from "@/api/client";
import { usePortalSession } from "@/stores/portalSession";
import { useDebouncedValue } from "@/lib/use-debounced-value";
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
import { useClaimDraftSync } from "./useClaimDraftSync";

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
  const [admissionDate, setAdmissionDateState] = useState("");
  const [dischargeDate, setDischargeDate] = useState("");
  const [provider, setProvider] = useState("");
  // Hospitalisation claims: hospital picked from the registry ("" = not yet,
  // OTHER_HOSPITAL = unlisted → free-text provider input).
  const [hospital, setHospital] = useState("");
  const [visitType, setVisitType] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  // Pre-/post-hospitalisation only (the claim type says so — see
  // `requiresDoctorName` below).
  const [doctorName, setDoctorName] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("SGD");
  const [diagnosis, setDiagnosis] = useState("");
  const [remarks, setRemarks] = useState("");
  const [referralMode, setReferralMode] = useState<ReferralMode>("");
  const [referralFile, setReferralFile] = useState<File | null>(null);
  // The date printed on a NEWLY uploaded referral letter. Optional — a member
  // who can't read a date off their letter must still be able to attach it, and
  // the review's validity check simply doesn't run without one.
  const [referralIssuedOn, setReferralIssuedOn] = useState("");
  const [referralExistingId, setReferralExistingId] = useState("");
  // One file per required-document slot (keyed by slot key) + optional extras.
  const [slotFiles, setSlotFiles] = useState<Record<string, File | null>>({});
  const [files, setFiles] = useState<File[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submitInFlight = useRef(false);
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
  // The earlier visit this claim continues ("" = none chosen yet or "a new
  // condition"). `anchorTouched` records that the member has ANSWERED the
  // question — including answering "not related" — so the single-candidate
  // auto-select below can never undo their choice.
  const [anchorId, setAnchorId] = useState("");
  const anchorTouched = useRef(false);

  const draftData: ClaimFormDraftData = {
    dependant_id: dependantId,
    selection,
    incurred_date: incurredDate,
    admission_date: admissionDate,
    discharge_date: dischargeDate,
    provider,
    hospital,
    visit_type: visitType,
    invoice_number: invoiceNumber,
    doctor_name: doctorName,
    amount,
    currency,
    diagnosis,
    remarks,
    referral_mode: referralMode,
    referral_issued_on: referralIssuedOn,
    referral_existing_id: referralExistingId,
    anchor_id: anchorId,
  };
  const restoreDraft = (draft: ClaimFormDraftData) => {
    setDependantId(draft.dependant_id);
    setSelection(draft.selection);
    setIncurredDate(draft.incurred_date);
    setAdmissionDateState(draft.admission_date);
    setDischargeDate(draft.discharge_date);
    setProvider(draft.provider);
    setHospital(draft.hospital);
    setVisitType(draft.visit_type);
    setInvoiceNumber(draft.invoice_number);
    setDoctorName(draft.doctor_name);
    setAmount(draft.amount);
    setCurrency(draft.currency);
    setDiagnosis(draft.diagnosis);
    setRemarks(draft.remarks);
    setReferralMode(draft.referral_mode as ReferralMode);
    setReferralIssuedOn(draft.referral_issued_on);
    setReferralExistingId(draft.referral_existing_id);
    setAnchorId(draft.anchor_id);
  };
  const draftSync = useClaimDraftSync({
    data: draftData,
    ready: options.isSuccess,
    meaningful: Object.entries(draftData).some(
      ([key, value]) => key !== "currency" && value.trim().length > 0,
    ),
    busy,
    onRestore: restoreDraft,
  });

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
  const policyCurrency = options.data?.policy_currency ?? "SGD";
  const amountValue = Number(amount);
  const amountUsable =
    Number.isFinite(amountValue) && amountValue > 0 ? amountValue : null;
  // **Debounced, because the amount is in the query key.** Undebounced, typing
  // "1200" fired four requests (1, 12, 120, 1200) against a 60/min limit — and
  // a 429 leaves the quote unresolved, which now BLOCKS sending. The member
  // would have rate-limited themselves out of their own claim by typing.
  const quotedAmount = useDebouncedValue(amountUsable, 400);
  // The live conversion. Only asked for on a foreign amount with a date — a
  // rate is per-DAY, so quoting before the member has picked one would price
  // the claim at the wrong day and then silently change under them.
  const fxQuote = useFxQuote(effectiveCurrency, quotedAmount, incurredDate);
  // **"We asked and there is no rate" and "we have not got an answer" are
  // different states, and only the first waives the confirmation.** Keying this
  // off `data?.available ?? false` collapsed them: a 429 (one request per
  // keystroke against a 60/min limit) or any network blip left `data` undefined,
  // so no confirmation was required, no checkbox rendered — and submit then
  // 409'd `fx_confirmation_required` on a control the member could not see,
  // taking the draft and every uploaded document down with it in the rollback.
  const fxForeign = effectiveCurrency !== policyCurrency;
  const fxUnavailable = fxQuote.isSuccess && !fxQuote.data.available;
  // A quote for a DIFFERENT amount than the one on screen is not an answer —
  // it is the previous answer, still showing while the debounce settles. The
  // server echoes the amount it priced, so this compares the two rather than
  // tracking the timer: the figure displayed and the figure submitted are then
  // always about the number the member actually typed.
  const fxMatchesInput =
    fxQuote.isSuccess &&
    fxQuote.data.amount === amountUsable &&
    fxQuote.data.currency === effectiveCurrency &&
    fxQuote.data.as_of_date === incurredDate;
  const fxReady = fxForeign && amountUsable !== null && Boolean(incurredDate);
  const fxUnresolved = fxReady && !fxMatchesInput;
  // Unresolved because the answer is still COMING, as opposed to having failed.
  // `isFetching` cannot carry this on its own: during the debounce the query key
  // still holds the previous amount, so nothing is in flight and the old quote
  // is still `data`. Without this the notice renders a settled conversion for a
  // figure the member has already typed over, while submit refuses it. A failed
  // request is excluded — that state has its own retry, and treating it as a
  // wait would hide the retry behind a spinner that never stops.
  const fxAwaiting = fxUnresolved && !fxQuote.isError;
  // A quote for THIS amount is on screen, so submitting the claim accepts it.
  // There is no separate tick — see `ConversionNotice`.
  const fxShown = fxForeign && fxMatchesInput && !fxUnavailable;
  // Wait only while an eligible request is genuinely pending. A transport
  // failure is fail-open: create/submit tries server-side and can route to the
  // saved claim for confirmation if that succeeds.
  const fxBlocked = fxUnresolved && !fxQuote.isError;
  // Same gate as `fxShown`: the acknowledged figure and the quoted figure must
  // be the same one, and neither may be the previous amount's answer.
  const fxQuoteForInput = fxMatchesInput ? (fxQuote.data ?? null) : null;
  const convertedAmount = fxQuoteForInput?.converted ?? null;

  const selectedProduct: InsuredClaimOption | null = useMemo(
    () => insured.find((p) => p.product_code === productCode) ?? null,
    [insured, productCode],
  );
  const selectedClaimType = selectedProduct?.claim_types[claimTypeIndex] ?? null;
  const subType = selectedClaimType?.sub_type ?? null;
  // Served by the backend's intake profile, never derived from the sub-type
  // label here: the label is broker-facing wording and a relabel would
  // silently stop this field being asked for while submit still requires it.
  const requiresDoctorName = selectedClaimType?.requires_doctor_name ?? false;
  const supportsStayDates = selectedClaimType?.supports_stay_dates ?? false;

  // GHS choices can own independent government/private document sets. The
  // hospital picker or typed provider decides which scoped set is shown. An
  // "Other" hospital is classified by the typed name — mirroring the backend
  // `hospital_sector`, so a member who types a listed hospital into the free
  // text still gets that hospital's sector (and the form/backend can't
  // disagree about which documents are required). Unlisted → the private set.
  const hospitals = options.data?.hospitals ?? [];
  const isHospitalisation = supportsStayDates;
  const hasSectorDocuments = Boolean(selectedClaimType?.doc_slots_by_sector);
  const effectiveProvider =
    isHospitalisation && hospital && hospital !== OTHER_HOSPITAL
      ? hospital
      : provider;
  const hospitalSector = hasSectorDocuments
    ? sectorForHospital(hospitals, effectiveProvider)
    : null;
  const selectedFlexCategory = flex?.categories.find(
    (category) => category.name === flexCategory,
  );
  const docSlots =
    effectiveKind === "flex"
      ? (selectedFlexCategory?.doc_slots ?? flex?.doc_slots ?? [])
      : hasSectorDocuments && hospitalSector
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

  // ── The episode: which earlier visit this claim continues ─────────────────
  //
  // SERVED per claim type (`anchor_mode`), never matched on a sub-type label
  // here. A specialist type reports "sp_course" on both visit types because the
  // TYPE can carry a course; only a follow-up may actually name one, and
  // `visitType` is a control this form already owns, so narrowing it here is
  // not the label-matching drift the served flag guards against.
  const rawAnchorMode = selectedClaimType?.anchor_mode ?? null;
  const anchorMode: AnchorMode | null =
    rawAnchorMode === "sp_course"
      ? visitType === "follow_up"
        ? "sp_course"
        : null
      : rawAnchorMode;
  const claimAnchors = useClaimAnchors(anchorMode, dependantId, true);
  const anchorOptions = claimAnchors.data ?? [];
  const selectedAnchor = anchorOptions.find((a) => a.id === anchorId) ?? null;

  // One plausible previous visit and no answer yet → pick it. This is what
  // makes the control feel like autofill instead of an interrogation; anything
  // the member does to the picker sets `anchorTouched` and stops it forever.
  useEffect(() => {
    if (anchorTouched.current || anchorId || anchorMode === null) return;
    if (claimAnchors.data?.length === 1) setAnchorId(claimAnchors.data[0].id);
  }, [claimAnchors.data, anchorMode, anchorId]);

  // Prefill from the anchor — the CLINICAL context only, never the bill.
  //
  // **The member beats the document, which beats the anchor.** A field the
  // member typed or the AI read off THIS visit's invoice is already non-empty,
  // so filling only blanks is the whole precedence rule. The one exception is a
  // value extraction produced with LOW confidence: the anchor's is a confirmed
  // fact from a claim that was already assessed, which a blurry read is not, so
  // it wins and the "double-check this" hint drops the field it no longer
  // applies to.
  //
  // Amount, date, invoice number and documents are never carried: those are
  // facts about one visit, and copying them forward walks into the
  // duplicate-invoice refusal — which has no member-side override.
  // What the anchor put there, so deselecting it can take it back. Anything the
  // member has since retyped is no longer the anchor's and stays.
  const anchorFilled = useRef<Record<string, string>>({});

  useEffect(() => {
    if (!selectedAnchor) {
      // Deselected — including "This is for a new condition" after the
      // single-candidate auto-select fired. Undoing the prefill is not tidiness:
      // leaving `referralMode`/`referralExistingId` set files the claim with the
      // PREVIOUS course's referral letter and no link to explain it, which is
      // exactly the wrong-letter failure anchor precedence exists to prevent —
      // and the review's `_check_referral` passes it, because a letter IS
      // attached.
      const filled = anchorFilled.current;
      if (Object.keys(filled).length === 0) return;
      if (filled.diagnosis && diagnosis === filled.diagnosis) setDiagnosis("");
      if (filled.doctor_name && doctorName === filled.doctor_name)
        setDoctorName("");
      if (filled.provider_name && provider === filled.provider_name)
        setProvider("");
      if (
        filled.referral_document_id &&
        referralExistingId === filled.referral_document_id
      ) {
        // Back to unset, which re-arms the "latest letter on file" fallback
        // below — the documented behaviour for a follow-up naming no course.
        setReferralMode("");
        setReferralExistingId("");
      }
      anchorFilled.current = {};
      return;
    }
    const anchorWins = (value: string, field: string) =>
      !value || lowConfidence.includes(field);
    // Rebuilt, not appended: switching straight from one anchor to another must
    // not leave the first one's values recorded as the second's.
    const filled: Record<string, string> = {};
    if (selectedAnchor.diagnosis && anchorWins(diagnosis, "diagnosis")) {
      setDiagnosis(selectedAnchor.diagnosis);
      setLowConfidence((prev) => prev.filter((f) => f !== "diagnosis"));
      filled.diagnosis = selectedAnchor.diagnosis;
    }
    if (selectedAnchor.doctor_name && anchorWins(doctorName, "doctor_name")) {
      setDoctorName(selectedAnchor.doctor_name);
      setLowConfidence((prev) => prev.filter((f) => f !== "doctor_name"));
      filled.doctor_name = selectedAnchor.doctor_name;
    }
    // The clinic, on a specialist course only. A pre-/post- consult is billed
    // by the specialist while the ANCHOR is the hospital, so carrying the
    // provider there would be wrong on every single claim.
    if (
      anchorMode === "sp_course" &&
      selectedAnchor.provider_name &&
      anchorWins(provider, "provider_name")
    ) {
      setProvider(selectedAnchor.provider_name);
      setLowConfidence((prev) => prev.filter((f) => f !== "provider_name"));
      filled.provider_name = selectedAnchor.provider_name;
    }
    // The referral of the course this claim CONTINUES — authoritative, not a
    // blank-fill: the anchor is the course, so its letter is the right one even
    // if the newest-on-file rule below has already chosen another.
    if (selectedAnchor.referral_document_id) {
      setReferralMode("existing");
      setReferralExistingId(selectedAnchor.referral_document_id);
      filled.referral_document_id = selectedAnchor.referral_document_id;
    }
    anchorFilled.current = filled;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAnchor, anchorMode]);

  // Follow-up visits with no anchor chosen fall back to the member's latest
  // referral letter on file — auto-select it once the letters load; the member
  // can still change it. Guarded on the anchor because "newest" is wrong the
  // moment a member is under two specialists at once, and the anchor knows
  // which course this is.
  useEffect(() => {
    if (
      visitType === "follow_up" &&
      !referralMode &&
      !selectedAnchor?.referral_document_id &&
      (referralLetters.data?.length ?? 0) > 0
    ) {
      const latest = [...(referralLetters.data ?? [])].sort((a, b) =>
        b.created_at.localeCompare(a.created_at),
      )[0];
      setReferralMode("existing");
      setReferralExistingId(latest.id);
    }
  }, [visitType, referralMode, referralLetters.data, selectedAnchor]);

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

  // The dates this member may claim on, SERVED by the same function submit
  // enforces (`claims.claim_period_window`) rather than re-derived from the
  // policy year here. Two things that window knows and the year does not: a
  // flex scheme can start mid-year, and a LEAVER's window closes on their last
  // day. Both used to pass the form and be refused at submit, after the member
  // had filled everything in.
  const flexWindow = options.data?.flex;
  const claimableFrom =
    (effectiveKind === "flex" ? flexWindow?.claimable_from : null) ??
    options.data?.claimable_from ??
    "";
  const claimableTo =
    (effectiveKind === "flex" ? flexWindow?.claimable_to : null) ??
    options.data?.claimable_to ??
    "";
  // Both bounds are NULL together when the server has no claimable window to
  // offer for this kind — a leaver whose cover ended before the period began.
  // It withholds that kind's options too, so `claimBlock` is what the page has
  // left to say; the empty bounds here just keep the date input unconstrained
  // rather than handing it `min > max`.
  const claimBlock = options.data?.claim_block ?? null;
  // Claims can't be incurred in the future — clamp to today when today falls
  // inside the claimable window (a seeded future-dated year keeps its own span).
  const today = todayISO();
  const maxIncurred =
    claimableFrom && claimableTo && today >= claimableFrom && today <= claimableTo
      ? today
      : claimableTo;

  // Fields that depend on the chosen claim type — reset when the type changes.
  const resetTypeFields = () => {
    setDiagnosis("");
    setDoctorName("");
    setAdmissionDateState("");
    setDischargeDate("");
    setVisitType("");
    setHospital("");
    setSlotFiles({});
    setReferralMode("");
    setReferralFile(null);
    setReferralIssuedOn("");
    setReferralExistingId("");
    setFieldErrors({});
    // The anchor belongs to the claim TYPE — an admission cannot be the
    // follow-up target of a dental claim. Clearing `anchorTouched` too so the
    // single-candidate auto-select is offered again for the new type.
    setAnchorId("");
    anchorTouched.current = false;
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
    if (f.admission_date) setAdmissionDateState(f.admission_date);
    if (f.discharge_date) setDischargeDate(f.discharge_date);
    if (f.invoice_number) setInvoiceNumber(f.invoice_number);
    if (f.amount != null) setAmount(String(f.amount));
    if (f.currency && insuredPick) setCurrency(f.currency);
    // The backend returns the diagnosis in its final form — a catalog label to
    // select, or "Other: <text>" free text — so set it directly.
    if (f.diagnosis) setDiagnosis(f.diagnosis);
    if (f.doctor_name) setDoctorName(f.doctor_name);
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
    setAdmissionDateState(f?.admission_date ?? "");
    setDischargeDate(f?.discharge_date ?? "");
    setProvider(f?.provider_name ?? "");
    setInvoiceNumber(f?.invoice_number ?? "");
    setAmount(f?.amount != null ? String(f.amount) : "");
    if (f?.currency && effectiveKind === "insured") setCurrency(f.currency);
    setDiagnosis(f?.diagnosis ?? "");
    setDoctorName(f?.doctor_name ?? "");
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
      supportsStayDates,
      admissionDate,
      dischargeDate,
      claimableFrom,
      claimableTo,
      today,
      isHospitalisation,
      hospital,
      provider,
      invoiceNumber,
      requiresDoctorName,
      doctorName,
      amount,
      fxBlocked,
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
    if (submitInFlight.current) return false;
    const errs = validate();
    setFieldErrors(errs);
    if (Object.keys(errs).length > 0) {
      setError("Fix the highlighted fields before submitting.");
      return false;
    }
    if (!effectiveKind) return; // guarded by validate; satisfies the type
    submitInFlight.current = true;
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
        const letter = await uploadReferral.mutateAsync({
          file: referralFile,
          issuedOn: referralIssuedOn || null,
        });
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
        admission_date: supportsStayDates ? admissionDate || null : null,
        discharge_date: supportsStayDates ? dischargeDate || null : null,
        provider_name: effectiveProvider.trim(),
        invoice_number: invoiceNumber.trim(),
        doctor_name: requiresDoctorName ? doctorName.trim() : null,
        diagnosis: diagnosis.trim() || null,
        remarks: remarks.trim() || null,
        amount_claimed: Number(amount),
        currency: effectiveCurrency,
        dependant_id: dependantId || null,
        referral_document_id: referralDocumentId,
        referral_not_applicable: false,
        // Only when this claim type actually takes one — a stale id left over
        // from a type the member switched away from would be refused.
        related_claim_id: anchorMode ? anchorId || null : null,
        // Sending the claim with the figure on screen IS the acceptance, so
        // this rides along automatically. The amount is what was ACTUALLY
        // DISPLAYED: the server re-computes and stamps the acknowledgement only
        // if the two agree, so a rate that published between this screen
        // rendering and the claim saving re-asks on the claim page rather than
        // binding them to a figure they never saw.
        fx_acknowledged: fxShown,
        fx_quoted_amount: convertedAmount,
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
        try {
          await draftSync.clear();
        } catch {
          // The claim is committed. A stale working-copy cleanup must never
          // turn a successful submission into a visible failure.
        }
        // THE RECEIPT: the claim's own page, which states what was sent, lists
        // every document and carries the status from here on. It replaces a
        // three-second toast that left nothing behind.
        toast.success("Claim submitted");
        try {
          await navigate({
            to: "/portal/$company/claims/$claimId",
            params: { company, claimId: claim.id },
            search: { submitted: true },
          });
        } catch {
          // The claim is already committed. A client-side router failure must
          // never leave the member on a form that appears safe to submit again.
          window.location.assign(
            `/portal/${encodeURIComponent(company)}/claims/${encodeURIComponent(claim.id)}?submitted=true`,
          );
        }
      }
      return true;
    } catch (err) {
      // **A conversion the member has not confirmed is RECOVERABLE, so the
      // draft must survive it.** The rollback below deletes the claim and with
      // it every document just uploaded — right for a claim that can never be
      // filed (a duplicate invoice), catastrophic for one that only needs a
      // box ticked. The claim's own page carries that control, so send them
      // there rather than making them re-enter the form and re-attach the
      // receipts. Reachable even with the guard above: the rate can publish
      // between this screen rendering and the claim being saved.
      if (
        claimId &&
        err instanceof ConflictDetailError &&
        err.detail.code === "fx_confirmation_required"
      ) {
        setBusy(false);
        void navigate({
          to: "/portal/$company/claims/$claimId",
          params: { company, claimId },
        });
        return;
      }
      // Includes the duplicate-invoice 409, which is a HARD refusal with no
      // member-side override — `ConflictDetailError` carries the server's own
      // message, so it reads as the specific reason rather than a generic
      // failure.
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
      submitInFlight.current = false;
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
  const hasLocalAttachments =
    Object.values(slotFiles).some(Boolean) ||
    files.length > 0 ||
    autofillDocs.length > 0 ||
    referralFile !== null ||
    pendingClaims.some((claim) => claim.file !== null);

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
    claimableFrom,
    maxIncurred,
    claimBlock,
    // selection
    dependantId,
    selection,
    effectiveKind,
    selectedProduct,
    subType,
    isHospitalisation,
    needsReferral,
    requiresDoctorName,
    supportsStayDates,
    // episode
    anchorMode,
    anchorOptions,
    anchorId,
    changeAnchor: (next: string) => {
      anchorTouched.current = true;
      setAnchorId(next);
    },
    showDiagnosisPicker:
      effectiveKind === "insured" &&
      (selectedProduct?.diagnosis_group ?? null) !== null,
    docSlots,
    effectiveCurrency,
    policyCurrency,
    // currency conversion
    // Only the quote for the amount on screen is shown. Handing over the raw
    // `data` let the previous amount's conversion sit there looking settled
    // while submit answered "we're still checking the exchange rate".
    fxQuote: fxQuoteForInput,
    fxLoading: fxQuote.isFetching || fxAwaiting,
    fxFailed: fxForeign && fxQuote.isError,
    retryFxQuote: () => void fxQuote.refetch(),
    // fields
    incurredDate,
    setIncurredDate,
    admissionDate,
    setAdmissionDate: setAdmissionDateState,
    dischargeDate,
    setDischargeDate,
    maxDischarge: maxIncurred,
    provider,
    setProvider,
    hospital,
    setHospital,
    visitType,
    invoiceNumber,
    setInvoiceNumber,
    doctorName,
    setDoctorName,
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
    referralIssuedOn,
    setReferralIssuedOn,
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
    draftStatus: draftSync.status,
    draftRestored: draftSync.restored,
    hasLocalAttachments,
    hasUnsubmittedWork: hasLocalAttachments || draftSync.hasUnsavedChanges,
    // actions
    changeClaimant,
    changeSelection,
    setVisitType: (next: string) => {
      setVisitType(next);
      setReferralMode("");
      setReferralFile(null);
      setReferralIssuedOn("");
      setReferralExistingId("");
      // First visit ⇄ follow-up decides whether this claim continues anything
      // at all, so a link chosen under the other answer must not survive it.
      setAnchorId("");
      anchorTouched.current = false;
    },
    setReferralMode: (next: ReferralMode) => {
      setReferralMode(next);
      setReferralFile(null);
      setReferralIssuedOn("");
      setReferralExistingId("");
    },
    runAutofill,
    pickFiles,
    dropAutofillFile,
    submit,
  };
}
