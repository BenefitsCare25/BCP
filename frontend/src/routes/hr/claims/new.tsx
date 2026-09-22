import { Link, useNavigate } from "@tanstack/react-router";
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
  useCreateHrClaim,
  useHrCoverageOptions,
  useHrEmployees,
  useHrFxQuote,
  useUploadHrReferral,
  type HrReferral,
  type HrEmployee,
} from "@/api/hrClaims";
import type { CoverageOptions } from "@/api/portal";
import { ConversionNotice } from "@/components/portal/claims/ConversionNotice";
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

function choicesFrom(options: CoverageOptions): ClaimChoice[] {
  const insured = options.insured.flatMap((product) =>
    product.claim_types.map((claimType) => ({
      id: `insured:${product.product_code}:${claimType.scope_key}`,
      label: `${claimType.label} · ${product.product_name ?? product.product_code}`,
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
    label: `${category.name} · Flexible benefits`,
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
  return (
    <label className="block space-y-1.5 text-sm font-medium">
      <span>
        {label}
        {required && <span className="text-error"> *</span>}
      </span>
      {children}
      {hint && <span className="block text-xs font-normal text-muted-foreground">{hint}</span>}
    </label>
  );
}

export function HrNewClaimPage() {
  useDocumentTitle("New employee claim");
  const navigate = useNavigate();
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employee, setEmployee] = useState<HrEmployee | null>(null);
  const [choiceId, setChoiceId] = useState("");
  const [visitType, setVisitType] = useState("");
  const [incurredDate, setIncurredDate] = useState("");
  const [admissionDate, setAdmissionDate] = useState("");
  const [dischargeDate, setDischargeDate] = useState("");
  const [provider, setProvider] = useState("");
  const [invoice, setInvoice] = useState("");
  const [doctor, setDoctor] = useState("");
  const [diagnosis, setDiagnosis] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("");
  const [remarks, setRemarks] = useState("");
  const [referralFile, setReferralFile] = useState<File | null>(null);
  const [referralIssuedOn, setReferralIssuedOn] = useState("");
  const [error, setError] = useState<string | null>(null);
  const uploadedReferral = useRef<{
    employeeId: string;
    file: File;
    issuedOn: string | null;
    result: HrReferral;
  } | null>(null);

  const employees = useHrEmployees(employeeSearch);
  const options = useHrCoverageOptions(employee?.id ?? null);
  const choices = useMemo(
    () => (options.data ? choicesFrom(options.data) : []),
    [options.data],
  );
  const choice = choices.find((item) => item.id === choiceId) ?? null;
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
  const uploadReferral = useUploadHrReferral();
  const busy = createClaim.isPending || uploadReferral.isPending || fxWaiting;

  useEffect(() => {
    if (options.data && !currency) setCurrency(options.data.policy_currency);
  }, [options.data, currency]);

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
    if (choice.requiresReferral && visitType && !referralFile) {
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
      const claim = await createClaim.mutateAsync({
        employee_id: employee.id,
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
        referral_document_id: referral?.id ?? null,
        related_claim_id: null,
        fx_acknowledged:
          fxForeign && fxMatchesInput && Boolean(fxQuote.data.available),
        fx_quoted_amount:
          fxForeign && fxMatchesInput && fxQuote.data.available
            ? fxQuote.data.converted
            : null,
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
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <Button asChild variant="ghost" className="-ml-3 h-11 sm:h-9">
          <Link to="/hr/claims">
            <ArrowLeft className="size-4" aria-hidden />
            All claims
          </Link>
        </Button>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">New employee claim</h1>
      </div>

      <Card className="p-5 sm:p-6">
        <h2 className="text-base font-semibold">1. Choose the employee</h2>
        {employee ? (
          <div className="mt-4 flex items-center gap-3 rounded-lg bg-muted p-4">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-card">
              <UserRound className="size-5 text-muted-foreground" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{employee.name ?? "Employee"}</p>
              <p className="truncate text-sm text-muted-foreground">
                {employee.staff_id} · {employee.period}
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              className="h-11 sm:h-9"
              onClick={() => setEmployee(null)}
            >
              Change
            </Button>
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            <label className="relative block">
              <span className="sr-only">Search employees</span>
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                type="search"
                className="h-11 pl-9"
                placeholder="Search by name or staff ID"
                value={employeeSearch}
                onChange={(event) => setEmployeeSearch(event.target.value)}
              />
            </label>
            {employees.isLoading ? (
              <div className="space-y-2" aria-label="Loading employees">
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
              </div>
            ) : employees.isError ? (
              <p className="text-sm text-error" role="alert">
                {formatError(employees.error)}
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
                        {item.name ?? "Employee"}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {item.staff_id} · {item.period}
                      </span>
                    </span>
                    <Check className="size-4 text-muted-foreground" aria-hidden />
                  </button>
                ))}
                {employees.data?.items.length === 0 && (
                  <p className="p-4 text-sm text-muted-foreground">No employees found.</p>
                )}
              </div>
            )}
          </div>
        )}
      </Card>

      {employee && (
        <Card className="p-5 sm:p-6">
          <h2 className="text-base font-semibold">2. Enter the claim</h2>
          {options.isLoading ? (
            <div className="mt-4 space-y-3" aria-label="Loading claim options">
              <Skeleton className="h-11" />
              <Skeleton className="h-28" />
            </div>
          ) : options.isError ? (
            <div className="mt-4" role="alert">
              <p className="text-sm text-error">{formatError(options.error)}</p>
              <Button
                variant="outline"
                className="mt-3 h-11 sm:h-9"
                onClick={() => void options.refetch()}
              >
                Try again
              </Button>
            </div>
          ) : choices.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">
              This employee has no claimable cover in the selected benefit period.
            </p>
          ) : (
            <form className="mt-5 space-y-5" onSubmit={(event) => void submit(event)}>
              <Field label="Claim type" required>
                <NativeSelect
                  className="h-11 w-full px-3"
                  value={choiceId}
                  required
                  onChange={(event) => {
                    setChoiceId(event.target.value);
                    setVisitType("");
                    setReferralFile(null);
                    uploadedReferral.current = null;
                  }}
                >
                  <option value="">Select claim type</option>
                  {choices.map((item) => (
                    <option key={item.id} value={item.id}>{item.label}</option>
                  ))}
                </NativeSelect>
              </Field>

              {choice && (
                <>
                  <div className="grid gap-4 sm:grid-cols-3">
                    <Field label="Date incurred" required>
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
                    <Field label="Currency" required>
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
                    <Field label="Claim amount" required>
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
                        The {policyCurrency} conversion could not be loaded. Try again before
                        saving this claim.
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void fxQuote.refetch()}
                      >
                        Try conversion again
                      </Button>
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
                    <Field label="Clinic or provider" required>
                      <Input className="h-11" required minLength={2} maxLength={255} value={provider} onChange={(event) => setProvider(event.target.value)} />
                    </Field>
                    <Field label="Invoice or receipt number" required>
                      <Input className="h-11" required maxLength={128} value={invoice} onChange={(event) => setInvoice(event.target.value)} />
                    </Field>
                  </div>

                  {choice.supportsStayDates && (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label="Admission date">
                        <Input type="date" className="h-11" max={incurredDate || undefined} value={admissionDate} onChange={(event) => setAdmissionDate(event.target.value)} />
                      </Field>
                      <Field label="Discharge date">
                        <Input type="date" className="h-11" min={admissionDate || undefined} value={dischargeDate} onChange={(event) => setDischargeDate(event.target.value)} />
                      </Field>
                    </div>
                  )}

                  {choice.requiresReferral && (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label="Visit type" required>
                        <NativeSelect className="h-11 w-full px-3" required value={visitType} onChange={(event) => setVisitType(event.target.value)}>
                          <option value="">Select visit type</option>
                          <option value="first">First specialist visit</option>
                          <option value="follow_up">Follow-up specialist visit</option>
                        </NativeSelect>
                      </Field>
                      <Field
                        label="Referral letter"
                        required={Boolean(visitType)}
                        hint="PDF, JPG or PNG · 15 MB maximum"
                      >
                        <Input
                          type="file"
                          className="h-11 py-2"
                          accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                          required={Boolean(visitType)}
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
                        <Field label="Referral issued on">
                          <Input type="date" className="h-11" max={singaporeTodayISO()} value={referralIssuedOn} onChange={(event) => setReferralIssuedOn(event.target.value)} />
                        </Field>
                      )}
                    </div>
                  )}

                  {choice.requiresDoctor && (
                    <Field label="Treating doctor" required>
                      <Input className="h-11" required maxLength={255} value={doctor} onChange={(event) => setDoctor(event.target.value)} />
                    </Field>
                  )}

                  <Field label="Diagnosis" required={choice.diagnosisRequired}>
                    <Input className="h-11" required={choice.diagnosisRequired} maxLength={512} value={diagnosis} onChange={(event) => setDiagnosis(event.target.value)} />
                  </Field>

                  <Field label="Remarks">
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

                  {error && <p className="rounded-lg bg-error-soft p-3 text-sm text-error" role="alert">{error}</p>}

                  <div className="flex flex-col-reverse gap-3 border-t border-border pt-5 sm:flex-row sm:justify-end">
                    <Button asChild variant="outline" className="h-11 sm:h-9">
                      <Link to="/hr/claims">Cancel</Link>
                    </Button>
                    <Button type="submit" className="h-11 sm:h-9" disabled={busy}>
                      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
                      Save and add evidence
                    </Button>
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
