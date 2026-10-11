import { usePortalTranslation, type PortalTranslator } from "@/i18n/portal";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { AlertTriangle, ArrowLeft, Check, Loader2, Search, UserRound } from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useAmendHrClaim,
  useCreateHrClaim,
  useHrClaim,
  useHrCoverageOptions,
  useHrEmployees,
  useHrFxQuote,
  useUploadHrReferral,
  type HrClaim,
  type HrReferral,
  type HrEmployee,
} from "@/api/hrClaims";
import type { CoverageOptions } from "@/api/portal";
import { ConversionNotice } from "@/components/portal/claims/ConversionNotice";
import { formatClaimDate } from "@/components/hr/claimPresentation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { singaporeTodayISO } from "@/lib/business-date";
import { CLAIM_DOCUMENT_MAX_BYTES } from "@/lib/claim-files";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

interface ClaimChoice {
  id: string;
  label: string;
  kind: "insured" | "flex";
  productCode: string | null;
  flexCategory: string | null;
  claimType: string;
  subType: string | null;
  requiresReferral: boolean;
  requiresDoctor: boolean;
  diagnosisRequired: boolean;
  supportsStayDates: boolean;
  from: string | null;
  to: string | null;
}

function choicesFrom(options: CoverageOptions, pt: PortalTranslator): ClaimChoice[] {
  const insured = options.insured.flatMap((product) =>
    product.claim_types.map((claimType) => ({
      id: `insured:${product.product_code}:${claimType.scope_key}`,
      label: `${pt(claimType.label)} · ${pt(product.product_name ?? product.product_code)}${product.plan_display_name ? ` · ${pt(product.plan_display_name)}` : ""}`,
      kind: "insured" as const,
      productCode: product.product_code,
      flexCategory: null,
      claimType: claimType.label,
      subType: claimType.sub_type,
      requiresReferral: product.requires_referral,
      requiresDoctor: claimType.requires_doctor_name,
      diagnosisRequired: product.diagnosis_required,
      supportsStayDates: claimType.supports_stay_dates,
      from: product.claimable_from ?? options.claimable_from,
      to: product.claimable_to ?? options.claimable_to,
    })),
  );
  const flex = (options.flex?.categories ?? []).map((category) => ({
    id: `flex:${category.name}`,
    label: `${pt(category.name)} · ${pt("Flexible benefits")}`,
    kind: "flex" as const,
    productCode: null,
    flexCategory: category.name,
    claimType: category.name,
    subType: null,
    requiresReferral: false,
    requiresDoctor: false,
    diagnosisRequired: false,
    supportsStayDates: false,
    from: options.flex?.claimable_from ?? null,
    to: options.flex?.claimable_to ?? null,
  }));
  return [...insured, ...flex];
}

/** The option a saved draft was filed under: product plus sub-type and label,
 *  then the label alone (a sub-type the server has since normalised), then the
 *  sub-type alone (a renamed label). */
function draftChoice(choices: ClaimChoice[], claim: HrClaim): ClaimChoice | null {
  const draft = claim.draft;
  if (!draft) return null;
  if (claim.claim_kind === "flex") {
    return choices.find((item) => item.kind === "flex" && item.flexCategory === draft.flex_category_name) ?? null;
  }
  const insured = choices.filter((item) => item.kind === "insured" && item.productCode === draft.product_code);
  return (
    insured.find((item) => item.subType === draft.sub_type && item.claimType === claim.claim_type) ??
    insured.find((item) => item.claimType === claim.claim_type) ??
    insured.find((item) => item.subType === draft.sub_type) ??
    null
  );
}

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
}) {
  const pt = usePortalTranslation();
  return (
    <label className="block space-y-1.5 text-sm font-medium">
      <span>
        {pt(label)}
        {required && <span className="text-error"> *</span>}
      </span>
      {children}
      {hint && <span className="block text-xs font-normal text-muted-foreground">{pt(hint)}</span>}
    </label>
  );
}

export function HrNewClaimPage() {
  return <HrClaimForm />;
}

/** The new-claim form opened on a draft HR already saved. Only a draft can be
 *  edited here; once sent, the claim is the assessor's to correct. */
export function HrEditClaimPage() {
  const pt = usePortalTranslation();
  const { claimId } = useParams({ strict: false }) as { claimId: string };
  const claim = useHrClaim(claimId);
  useDocumentTitle("Edit employee claim");

  if (claim.isLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4" aria-label={pt("Loading claim")}>
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-56 rounded-xl" />
      </div>
    );
  }
  if (claim.isError || !claim.data || !claim.data.can_edit || !claim.data.draft) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <Button asChild variant="ghost" className="-ml-3 h-11 sm:h-9">
          <Link to="/hr/claims/$claimId" params={{ claimId }}>
            <ArrowLeft className="size-4" aria-hidden />
            {pt("Back to claim")}
          </Link>
        </Button>
        <Card className="p-5" role="alert">
          <p className="font-medium">
            {claim.isError || !claim.data
              ? pt("This claim could not be loaded")
              : pt("Only a draft can be edited")}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {claim.isError
              ? pt(formatError(claim.error))
              : pt("This claim has been sent, so its details are now corrected by the claims team.")}
          </p>
        </Card>
      </div>
    );
  }
  return <HrClaimForm key={claim.data.id} editing={claim.data} />;
}

function HrClaimForm({ editing }: { editing?: HrClaim }) {
  const pt = usePortalTranslation();
  useDocumentTitle(editing ? null : "New employee claim");
  const navigate = useNavigate();
  const draft = editing?.draft ?? null;
  // The revision the form opened on, not the live query's: a focus refetch
  // after another HR user's edit must still make this save conflict.
  const [openedRevision] = useState(() => draft?.revision ?? 0);
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employee, setEmployee] = useState<HrEmployee | null>(
    editing
      ? {
          id: editing.employee_id,
          name: editing.employee_name,
          staff_id: draft?.staff_id ?? "",
          period: "",
        }
      : null,
  );
  const [choiceId, setChoiceId] = useState("");
  const [visitType, setVisitType] = useState(draft?.visit_type ?? "");
  const [incurredDate, setIncurredDate] = useState(editing?.incurred_date ?? "");
  const [admissionDate, setAdmissionDate] = useState(draft?.admission_date ?? "");
  const [dischargeDate, setDischargeDate] = useState(draft?.discharge_date ?? "");
  const [provider, setProvider] = useState(editing?.provider_name ?? "");
  const [invoice, setInvoice] = useState(editing?.invoice_number ?? "");
  const [doctor, setDoctor] = useState(draft?.doctor_name ?? "");
  const [diagnosis, setDiagnosis] = useState(draft?.diagnosis ?? "");
  const [amount, setAmount] = useState(editing ? String(editing.amount_claimed) : "");
  const [currency, setCurrency] = useState(editing?.currency ?? "");
  const [remarks, setRemarks] = useState(draft?.remarks ?? "");
  const [referralFile, setReferralFile] = useState<File | null>(null);
  // The letter the draft already carries. Kept unless HR picks a replacement
  // or moves the claim to another type.
  const [keptReferral, setKeptReferral] = useState(draft?.referral ?? null);
  const [referralIssuedOn, setReferralIssuedOn] = useState("");
  const [error, setError] = useState<string | null>(null);
  const choiceSeeded = useRef(false);
  const uploadedReferral = useRef<{
    employeeId: string;
    file: File;
    issuedOn: string | null;
    result: HrReferral;
  } | null>(null);

  const employees = useHrEmployees(employeeSearch);
  const options = useHrCoverageOptions(employee?.id ?? null);
  const choices = useMemo(
    () => (options.data ? choicesFrom(options.data, pt) : []),
    [options.data, pt],
  );
  const choice = choices.find((item) => item.id === choiceId) ?? null;
  const planLabel = choice?.kind === "insured"
    ? options.data?.insured.find(product => product.product_code === choice.productCode)?.plan_display_name
    : null;
  const policyCurrency = options.data?.policy_currency ?? "SGD";
  const effectiveCurrency =
    choice?.kind === "flex"
      ? options.data?.flex?.currency ?? policyCurrency
      : currency || policyCurrency;
  const amountValue = Number(amount);
  const amountUsable =
    Number.isFinite(amountValue) && amountValue > 0 ? amountValue : null;
  const quotedAmount = useDebouncedValue(amountUsable, 400);
  const fxQuote = useHrFxQuote(
    effectiveCurrency,
    policyCurrency,
    quotedAmount,
    incurredDate,
  );
  const fxForeign = effectiveCurrency !== policyCurrency;
  const fxMatchesInput =
    fxQuote.isSuccess &&
    fxQuote.data.amount === amountUsable &&
    fxQuote.data.currency === effectiveCurrency &&
    fxQuote.data.as_of_date === incurredDate;
  const fxWaiting =
    fxForeign &&
    amountUsable !== null &&
    Boolean(incurredDate) &&
    !fxMatchesInput &&
    !fxQuote.isError;
  const createClaim = useCreateHrClaim();
  const amendClaim = useAmendHrClaim();
  const uploadReferral = useUploadHrReferral();
  const busy =
    createClaim.isPending || amendClaim.isPending || uploadReferral.isPending || fxWaiting;

  useEffect(() => {
    if (options.data && !currency) setCurrency(options.data.policy_currency);
  }, [options.data, currency]);

  // Open an edit on the option the draft was filed under, once the options load.
  useEffect(() => {
    if (!editing || choiceSeeded.current || choices.length === 0) return;
    choiceSeeded.current = true;
    const filed = draftChoice(choices, editing);
    if (filed) setChoiceId(filed.id);
  }, [editing, choices]);

  const chooseEmployee = (next: HrEmployee) => {
    setEmployee(next);
    setChoiceId("");
    setCurrency("");
    uploadedReferral.current = null;
    setError(null);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!employee || !choice) return;
    if (choice.requiresReferral && visitType && !referralFile && !keptReferral) {
      setError("Attach the referral letter for this specialist visit.");
      return;
    }
    if (fxForeign && (!fxQuote.isSuccess || !fxMatchesInput)) {
      setError("Wait for the currency conversion, or try it again before saving.");
      return;
    }
    setError(null);
    try {
      const issuedOn = referralIssuedOn || null;
      let referral: HrReferral | null = null;
      if (referralFile) {
        const cached = uploadedReferral.current;
        if (
          cached?.employeeId === employee.id &&
          cached.file === referralFile &&
          cached.issuedOn === issuedOn
        ) {
          referral = cached.result;
        } else {
          referral = await uploadReferral.mutateAsync({
            employeeId: employee.id,
            file: referralFile,
            issuedOn,
          });
          uploadedReferral.current = {
            employeeId: employee.id,
            file: referralFile,
            issuedOn,
            result: referral,
          };
        }
      }
      const fields = {
        claim_kind: choice.kind,
        product_code: choice.productCode,
        flex_category_name: choice.flexCategory,
        claim_type: choice.claimType,
        sub_type: choice.subType,
        visit_type: choice.requiresReferral ? visitType : null,
        incurred_date: incurredDate,
        admission_date: choice.supportsStayDates ? admissionDate || null : null,
        discharge_date: choice.supportsStayDates ? dischargeDate || null : null,
        provider_name: provider.trim(),
        invoice_number: invoice.trim(),
        doctor_name: choice.requiresDoctor ? doctor.trim() : null,
        diagnosis: diagnosis.trim() || null,
        remarks: remarks.trim() || null,
        amount_claimed: amountValue,
        currency: effectiveCurrency,
        referral_document_id:
          referral?.id ?? (choice.requiresReferral ? keptReferral?.id ?? null : null),
        fx_acknowledged:
          fxForeign && fxMatchesInput && Boolean(fxQuote.data.available),
        fx_quoted_amount:
          fxForeign && fxMatchesInput && fxQuote.data.available
            ? fxQuote.data.converted
            : null,
      };
      // An edit leaves the prior-visit link alone: the server re-resolves it
      // against the corrected claim and clears it if the type no longer takes one.
      const claim = editing && draft
        ? await amendClaim.mutateAsync({
            claimId: editing.id,
            patch: { ...fields, expected_revision: openedRevision },
          })
        : await createClaim.mutateAsync({
            ...fields,
            employee_id: employee.id,
            related_claim_id: null,
          });
      await navigate({
        to: "/hr/claims/$claimId",
        params: { claimId: claim.id },
      });
    } catch (caught) {
      setError(formatError(caught));
    }
  };

  return (
    <div className="hr-new-claim grid items-start gap-3 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <div className="lg:col-span-2">
        <Button asChild variant="ghost" className="-ml-3 h-11 sm:h-9">
          {editing ? (
            <Link to="/hr/claims/$claimId" params={{ claimId: editing.id }}>
              <ArrowLeft className="size-4" aria-hidden />
              {pt("Back to claim")}
            </Link>
          ) : (
            <Link to="/hr/claims">
              <ArrowLeft className="size-4" aria-hidden />
              {pt("All claims")} </Link>
          )}
        </Button>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {editing ? pt("Edit claim details") : pt("New employee claim")}
        </h1>
      </div>

      <Card className={cn("p-5", !employee && "lg:col-span-2")}>
        <h2 className="text-xl font-bold">{employee ? pt("Employee") : pt("Choose the employee")}</h2>
        {employee ? (
          <div className="mt-3 grid grid-cols-[2.5rem_minmax(0,1fr)] items-start gap-3 rounded-xl bg-muted p-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-card">
              <UserRound className="size-5 text-muted-foreground" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <p className="break-words font-medium">{employee.name ?? pt("Employee")}</p>
              <p className="text-sm text-muted-foreground">
                {employee.staff_id}
                {employee.period &&
                  ` · ${employee.period.split(" - ").map(formatClaimDate).join(" – ")}`}
              </p>
            </div>
            {/* A saved draft stays with its employee: discard it and start a new
                claim to file for someone else. */}
            {!editing && (
              <Button
                type="button"
                variant="outline"
                className="col-span-2 h-11 justify-self-end sm:h-9"
                onClick={() => setEmployee(null)}
              >
                {pt("Change")} </Button>
            )}
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            <label className="relative block">
              <span className="sr-only">{pt("Search employees")}</span>
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="search"
                className="h-11 pl-9"
                placeholder={pt("Search by name or staff ID")}
                value={employeeSearch}
                onChange={(event) => setEmployeeSearch(event.target.value)}
              />
            </label>
            {employees.isLoading ? (
              <div className="space-y-2" aria-label={pt("Loading employees")}>
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
              </div>
            ) : employees.isError ? (
              <p className="text-sm text-error" role="alert">
                {pt(formatError(employees.error))}
              </p>
            ) : (
              <div className="max-h-72 divide-y divide-border overflow-y-auto rounded-lg border border-border">
                {(employees.data?.items ?? []).map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => chooseEmployee(item)}
                    className="flex min-h-14 w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {item.name ?? pt("Employee")}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {item.staff_id} · {item.period}
                      </span>
                    </span>
                    <Check className="size-4 text-muted-foreground" aria-hidden />
                  </button>
                ))}
                {employees.data?.items.length === 0 && (
                  <p className="p-4 text-sm text-muted-foreground">{pt("No employees found.")}</p>
                )}
              </div>
            )}
          </div>
        )}
      </Card>

      {employee && (
        <Card className="p-5">
          <h2 className="text-xl font-bold">{pt("Claim details")}</h2>
          {options.isLoading ? (
            <div className="mt-4 space-y-3" aria-label={pt("Loading claim options")}>
              <Skeleton className="h-11" />
              <Skeleton className="h-28" />
            </div>
          ) : options.isError ? (
            <div className="mt-4" role="alert">
              <p className="text-sm text-error">{pt(formatError(options.error))}</p>
              <Button
                variant="outline"
                className="mt-3 h-11 sm:h-9"
                onClick={() => void options.refetch()}
              >
                {pt("Try again")} </Button>
            </div>
          ) : choices.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">
              {pt("This employee has no claimable cover in the selected benefit period.")} </p>
          ) : (
            <form className="mt-3 space-y-3" onSubmit={(event) => void submit(event)}>
              <Field label={pt("Claim type")} required>
                <NativeSelect
                  className="h-11 w-full px-3"
                  value={choiceId}
                  required
                  onChange={(event) => {
                    setChoiceId(event.target.value);
                    setVisitType("");
                    setReferralFile(null);
                    setKeptReferral(null);
                    uploadedReferral.current = null;
                  }}
                >
                  <option value="">{pt("Select claim type")}</option>
                  {choices.map((item) => (
                    <option key={item.id} value={item.id}>{pt(item.label)}</option>
                  ))}
                </NativeSelect>
              </Field>
              {planLabel && <p className="break-words text-sm text-muted-foreground">{pt("Plan:")} {pt(planLabel)}</p>}

              {choice && (
                <>
                  <div className="hr-claim-money grid grid-cols-2 gap-3 sm:grid-cols-3">
                    <Field label={pt("Date incurred")} required>
                      <Input
                        type="date"
                        className="h-11"
                        min={choice.from ?? undefined}
                        max={choice.to ?? undefined}
                        required
                        value={incurredDate}
                        onChange={(event) => setIncurredDate(event.target.value)}
                      />
                    </Field>
                    <Field label={pt("Currency")} required>
                      <NativeSelect
                        className="h-11 w-full px-3"
                        value={effectiveCurrency}
                        disabled={choice.kind === "flex"}
                        onChange={(event) => setCurrency(event.target.value)}
                      >
                        {(options.data?.currencies?.length
                          ? options.data.currencies
                          : [policyCurrency]
                        ).map((code) => (
                          <option key={code} value={code}>{code}</option>
                        ))}
                      </NativeSelect>
                    </Field>
                    <Field label={pt("Claim amount")} required>
                      <Input
                        type="number"
                        className="h-11 tabular-nums"
                        min="0.01"
                        max="1000000"
                        step="0.01"
                        inputMode="decimal"
                        required
                        value={amount}
                        onChange={(event) => setAmount(event.target.value)}
                      />
                    </Field>
                  </div>

                  {fxForeign && fxQuote.isError ? (
                    <div
                      className="space-y-2 rounded-lg bg-warn-soft/30 p-3 text-sm text-foreground"
                      role="alert"
                    >
                      <p className="flex items-start gap-2">
                        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
                        {pt("The")} {policyCurrency}  {pt("conversion could not be loaded. Try again before saving this claim.")} </p>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void fxQuote.refetch()}
                      >
                        {pt("Try conversion again")} </Button>
                    </div>
                  ) : (
                    <ConversionNotice
                      quote={fxMatchesInput ? fxQuote.data : null}
                      loading={fxWaiting}
                      currency={effectiveCurrency}
                      policyCurrency={policyCurrency}
                    />
                  )}

                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label={pt("Clinic or provider")} required>
                      <Input className="h-11" required minLength={2} maxLength={255} value={provider} onChange={(event) => setProvider(event.target.value)} />
                    </Field>
                    <Field label={pt("Invoice or receipt number")} required>
                      <Input className="h-11" required maxLength={128} value={invoice} onChange={(event) => setInvoice(event.target.value)} />
                    </Field>
                  </div>

                  {choice.supportsStayDates && (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label={pt("Admission date")}>
                        <Input type="date" className="h-11" max={incurredDate || undefined} value={admissionDate} onChange={(event) => setAdmissionDate(event.target.value)} />
                      </Field>
                      <Field label={pt("Discharge date")}>
                        <Input type="date" className="h-11" min={admissionDate || undefined} value={dischargeDate} onChange={(event) => setDischargeDate(event.target.value)} />
                      </Field>
                    </div>
                  )}

                  {choice.requiresReferral && (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label={pt("Visit type")} required>
                        <NativeSelect className="h-11 w-full px-3" required value={visitType} onChange={(event) => setVisitType(event.target.value)}>
                          <option value="">{pt("Select visit type")}</option>
                          <option value="first">{pt("First specialist visit")}</option>
                          <option value="follow_up">{pt("Follow-up specialist visit")}</option>
                        </NativeSelect>
                      </Field>
                      <Field
                        label={pt("Referral letter")}
                        required={Boolean(visitType) && !keptReferral}
                        hint={pt("PDF, JPG or PNG · 15 MB maximum")}
                      >
                        {keptReferral && !referralFile && (
                          <span className="block break-all text-sm font-normal text-muted-foreground">
                            {pt("On file: {0}. Choose a file only to replace it.", [keptReferral.file_name])}
                          </span>
                        )}
                        <Input
                          type="file"
                          className="h-11 py-2"
                          accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                          required={Boolean(visitType) && !keptReferral}
                          onChange={(event) => {
                            const file = event.target.files?.[0] ?? null;
                            if (file && file.size > CLAIM_DOCUMENT_MAX_BYTES) {
                              event.target.value = "";
                              setReferralFile(null);
                              setError("Choose a referral letter no larger than 15 MB.");
                              return;
                            }
                            setReferralFile(file);
                            uploadedReferral.current = null;
                            setError(null);
                          }}
                        />
                      </Field>
                      {referralFile && (
                        <Field label={pt("Referral issued on")}>
                          <Input type="date" className="h-11" max={singaporeTodayISO()} value={referralIssuedOn} onChange={(event) => setReferralIssuedOn(event.target.value)} />
                        </Field>
                      )}
                    </div>
                  )}

                  {choice.requiresDoctor && (
                    <Field label={pt("Treating doctor")} required>
                      <Input className="h-11" required maxLength={255} value={doctor} onChange={(event) => setDoctor(event.target.value)} />
                    </Field>
                  )}

                  <Field label={pt("Diagnosis")} required={choice.diagnosisRequired}>
                    <Input className="h-11" required={choice.diagnosisRequired} maxLength={512} value={diagnosis} onChange={(event) => setDiagnosis(event.target.value)} />
                  </Field>

                  <Field label={pt("Remarks")}>
                    <textarea
                      className={cn(
                        "min-h-24 w-full resize-y rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground shadow-sm",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:border-ring",
                      )}
                      maxLength={500}
                      value={remarks}
                      onChange={(event) => setRemarks(event.target.value)}
                    />
                  </Field>

                  {error && <p className="rounded-lg bg-error-soft p-3 text-sm text-error" role="alert">{pt(error)}</p>}

                  <div className="flex flex-col-reverse gap-3 border-t border-border pt-5 sm:flex-row sm:justify-end">
                    <Button asChild variant="outline" className="h-11 sm:h-9">
                      {editing ? (
                        <Link to="/hr/claims/$claimId" params={{ claimId: editing.id }}>{pt("Cancel")}</Link>
                      ) : (
                        <Link to="/hr/claims">{pt("Cancel")}</Link>
                      )}
                    </Button>
                    <Button type="submit" className="h-11 sm:h-9" disabled={busy}>
                      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
                      {editing ? pt("Save changes") : pt("Save and add evidence")} </Button>
                  </div>
                </>
              )}
            </form>
          )}
        </Card>
      )}
    </div>
  );
}
