/** Record a LOG case from an emailed request.
 *
 * ONE component, mounted twice: on Coverage & Members with the employee already
 * chosen (`employeeId` set), and from the claims queue with a member picker as
 * the first field. Two forms would drift, and the one that drifts is the one
 * used less.
 *
 * **Deliberately laxer than the member's claim form.** Only the member,
 * coverage, incurred date and amount are required — an admission-guarantee
 * email routinely carries no diagnosis, no invoice number and no documents, and
 * a form that refuses to save without them means the request never gets
 * recorded at all. The backend applies the matching relaxed validation
 * (`services/log_cases.py`); everything optional here is optional there.
 *
 * The product list comes from the member's BENEFIT STATEMENT, never from
 * `/coverage-options`: that endpoint drops products members may not self-file
 * (Major Medical, term life), which is precisely the gate a LOG case is exempt
 * from. Sourcing the picker there would silently hide the products an assessor
 * most often logs.
 */
import { useEffect, useMemo, useState } from "react";
import { Loader2, Paperclip, X } from "lucide-react";
import { toast } from "sonner";
import { useCreateLogCase } from "@/api/claims";
import { useEmployeeUtilization } from "@/api/claims";
import { useBenefitStatement, useCoverageSummary } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import { EmployeePicker } from "@/components/operations/EmployeePicker";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";

/** Mirrors `services/claim_intake.ALLOWED_CURRENCIES`. A drift here is loud,
 * not silent: the server rejects an unlisted currency with a readable 422. */
const CURRENCIES = [
  "SGD", "USD", "MYR", "EUR", "GBP", "AUD",
  "HKD", "CNY", "JPY", "INR", "IDR", "THB", "PHP",
];

/** Utilization limits are denominated in the policy currency, never the
 * currency the bill happens to be in. */
const POLICY_CURRENCY = "SGD";

const RECEIVED_VIA = [
  { value: "", label: "Not stated" },
  { value: "email", label: "Email" },
  { value: "phone", label: "Phone" },
  { value: "hr", label: "HR" },
  { value: "hospital", label: "Hospital" },
  { value: "other", label: "Other" },
];

const SELF = "__self__";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** One labelled field. `optional` is stated on the label rather than marking
 * the required ones with an asterisk: on this form most fields are optional,
 * so the exception is what should carry the ink. */
function Field({
  label,
  htmlFor,
  optional,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  optional?: boolean;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <Label htmlFor={htmlFor}>{label}</Label>
        {optional && <span className="text-2xs text-subtle">Optional</span>}
        {hint && <InfoHint>{hint}</InfoHint>}
      </div>
      {children}
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 border-t border-border pt-5 first:border-t-0 first:pt-0">
      <SectionLabel as="h3">{title}</SectionLabel>
      {children}
    </section>
  );
}

export function LogCaseForm({
  open,
  onOpenChange,
  employeeId: fixedEmployeeId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Set when opened at employee level — the member is then locked. */
  employeeId?: string;
  onCreated?: (claimId: string) => void;
}) {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const create = useCreateLogCase();

  const [pickedEmployee, setPickedEmployee] = useState("");
  const employeeId = fixedEmployeeId ?? (pickedEmployee || undefined);

  const [productCode, setProductCode] = useState("");
  const [claimant, setClaimant] = useState(SELF);
  const [incurredDate, setIncurredDate] = useState("");
  const [provider, setProvider] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [diagnosis, setDiagnosis] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("SGD");
  const [receivedVia, setReceivedVia] = useState("email");
  const [receivedOn, setReceivedOn] = useState(today());
  const [requestedBy, setRequestedBy] = useState("");
  const [remarks, setRemarks] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [memberQuery, setMemberQuery] = useState("");

  const { data: summary } = useCoverageSummary(
    fixedEmployeeId ? undefined : policyYearId ?? undefined,
  );
  const statement = useBenefitStatement(employeeId ?? null);
  const utilization = useEmployeeUtilization(employeeId ?? null);

  // Reset on close so the next case never inherits the last one's answers.
  useEffect(() => {
    if (open) return;
    setPickedEmployee("");
    setMemberQuery("");
    setProductCode("");
    setClaimant(SELF);
    setIncurredDate("");
    setProvider("");
    setInvoiceNumber("");
    setDiagnosis("");
    setAmount("");
    setCurrency("SGD");
    setReceivedVia("email");
    setReceivedOn(today());
    setRequestedBy("");
    setRemarks("");
    setFiles([]);
  }, [open]);

  const rosterItems = useMemo(() => summary?.items ?? [], [summary]);
  const selectedMember = useMemo(
    () => rosterItems.find((it) => it.id === employeeId) ?? null,
    [rosterItems, employeeId],
  );
  const memberItems = useMemo(() => {
    const needle = memberQuery.trim().toLowerCase();
    return rosterItems
      .filter(
        (it) =>
          !needle ||
          it.employee_name?.toLowerCase().includes(needle) ||
          it.staff_id.toLowerCase().includes(needle),
      )
      .map((it) => ({
        id: it.id,
        name: it.employee_name ?? it.staff_id,
        subtitle: it.staff_id,
      }));
  }, [rosterItems, memberQuery]);

  const coverage = useMemo(
    () => statement.data?.coverage ?? [],
    [statement.data],
  );

  // Default to the member's inpatient line — the coverage a LOG request is
  // about nine times in ten — and otherwise to their first.
  useEffect(() => {
    if (productCode || coverage.length === 0) return;
    const inpatient = coverage.find((c) =>
      ["GHS", "GHS2", "IMP", "GMM", "GMM2"].includes(c.product_code),
    );
    setProductCode((inpatient ?? coverage[0]).product_code);
  }, [coverage, productCode]);

  const line = coverage.find((c) => c.product_code === productCode) ?? null;
  // A dependant may only be named when this product actually covers them, and
  // only from the elected subset — the same rule the server enforces.
  const claimants = line?.covers_dependants ? line.covered_dependants : [];

  // Drop a claimant the newly-picked product doesn't cover, so the form can't
  // submit a combination the server will refuse.
  useEffect(() => {
    if (claimant === SELF) return;
    if (!claimants.some((d) => d.id === claimant)) setClaimant(SELF);
  }, [claimant, claimants]);

  const remaining = useMemo(() => {
    const bucket = utilization.data?.insured.find(
      (b) => b.product_code === productCode && !b.benefit_key,
    );
    return bucket?.remaining ?? null;
  }, [utilization.data, productCode]);

  const inPolicyCurrency = currency === POLICY_CURRENCY;
  const amountValue = Number(amount);
  const amountValid = amount.trim() !== "" && isFinite(amountValue) && amountValue > 0;
  const canSave =
    !!employeeId && !!productCode && !!incurredDate && amountValid && !create.isPending;

  async function submit() {
    if (!employeeId || !canSave) return;
    try {
      const { claim, failedUploads } = await create.mutateAsync({
        employeeId,
        claim_kind: "insured",
        product_code: productCode,
        dependant_id: claimant === SELF ? null : claimant,
        incurred_date: incurredDate,
        provider_name: provider.trim() || null,
        invoice_number: invoiceNumber.trim() || null,
        diagnosis: diagnosis.trim() || null,
        remarks: remarks.trim() || null,
        amount_claimed: amountValue,
        currency,
        received_via: receivedVia || null,
        received_on: receivedOn || null,
        requested_by: requestedBy.trim() || null,
        files,
      });
      if (failedUploads.length > 0) {
        // The case is saved either way — say exactly what didn't attach rather
        // than reporting a failure that would send the assessor re-entering it.
        toast.warning(
          `LOG case recorded, but ${failedUploads.join(", ")} didn't upload. Attach again from the case.`,
        );
      } else {
        toast.success("LOG case recorded");
      }
      onOpenChange(false);
      onCreated?.(claim.id);
    } catch (err) {
      toast.error(formatError(err));
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="sm:max-w-xl">
        <SheetHeader className="pr-10">
          <SheetTitle>New LOG case</SheetTitle>
          <p className="text-sm text-muted-foreground">
            Record a request that arrived outside the portal. It enters the
            claims queue for review like any other case.
          </p>
        </SheetHeader>

        <SheetBody className="space-y-5">
          <Block title="Who">
            {/* A roster is thousands of people, so the member is SEARCHED, not
             * scrolled: a plain dropdown of 491 options is unusable on CDL and
             * gets worse with every hire. Same picker the coverage page uses.
             * Once chosen it collapses to a line, so the form stays short. */}
            {!fixedEmployeeId &&
              (employeeId ? (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted px-3 py-2">
                  <span className="min-w-0 text-sm">
                    <span className="font-medium">
                      {selectedMember?.employee_name ?? "Selected employee"}
                    </span>
                    {selectedMember?.staff_id && (
                      <span className="ml-2 font-mono text-2xs text-muted-foreground">
                        {selectedMember.staff_id}
                      </span>
                    )}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setPickedEmployee("");
                      setProductCode("");
                      setClaimant(SELF);
                    }}
                  >
                    Change
                  </Button>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="log-member-search">Member</Label>
                  <EmployeePicker
                    items={memberItems}
                    selectedId={null}
                    onSelect={setPickedEmployee}
                    isLoading={!summary}
                    query={memberQuery}
                    onQueryChange={setMemberQuery}
                    listMaxHeight="max-h-56"
                    emptyText="No employee matches that name or staff ID."
                  />
                </div>
              ))}

            {employeeId && (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Claim is for" htmlFor="log-claimant">
                  <NativeSelect
                    id="log-claimant"
                    className="h-9 w-full"
                    value={claimant}
                    onChange={(e) => setClaimant(e.target.value)}
                    disabled={claimants.length === 0}
                  >
                    <option value={SELF}>
                      {statement.data?.employee.employee_name ?? "The member"}
                    </option>
                    {claimants.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name} ({d.relationship})
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
              </div>
            )}
          </Block>

          {/* The rest of the form depends on WHO it is for — the coverage list,
           * the claimants and the remaining limit all come from that member. A
           * blank product select reading "No coverage on record" before anyone
           * is chosen states a fact about nobody. */}
          {!employeeId ? null : (
            <>
          <Block title="What">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                label="Coverage"
                htmlFor="log-product"
                hint="Every product the member holds — including those they can't file themselves, which is what an assessor most often logs."
              >
                <NativeSelect
                  id="log-product"
                  className="h-9 w-full"
                  value={productCode}
                  onChange={(e) => setProductCode(e.target.value)}
                  disabled={statement.isLoading || coverage.length === 0}
                >
                  {coverage.length === 0 && (
                    <option value="">
                      {statement.isLoading ? "Loading…" : "No coverage on record"}
                    </option>
                  )}
                  {coverage.map((c) => (
                    <option key={c.product_code} value={c.product_code}>
                      {c.product_name ?? c.product_code}
                      {c.plan_code ? ` · ${c.plan_code}` : ""}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Incurred / admission date" htmlFor="log-date">
                <Input
                  id="log-date"
                  type="date"
                  value={incurredDate}
                  onChange={(e) => setIncurredDate(e.target.value)}
                />
              </Field>
              <Field label="Provider" htmlFor="log-provider" optional>
                <Input
                  id="log-provider"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  placeholder="e.g. Mount Elizabeth"
                />
              </Field>
              <Field label="Invoice / reference" htmlFor="log-invoice" optional>
                <Input
                  id="log-invoice"
                  value={invoiceNumber}
                  onChange={(e) => setInvoiceNumber(e.target.value)}
                />
              </Field>
              <div className="sm:col-span-2">
                <Field label="Diagnosis / description" htmlFor="log-diagnosis" optional>
                  <Input
                    id="log-diagnosis"
                    value={diagnosis}
                    onChange={(e) => setDiagnosis(e.target.value)}
                  />
                </Field>
              </div>
            </div>
          </Block>

          <Block title="How much">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Amount" htmlFor="log-amount">
                <Input
                  id="log-amount"
                  type="number"
                  min="0.01"
                  step="0.01"
                  className="tabular-nums"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="0.00"
                />
                {/* The limit is on screen while the number is typed. The server
                    guard is still the authority, but an assessor should never
                    learn about a limit from an error after they finished.

                    Utilization buckets are always in the POLICY currency, so
                    the comparison is only made when the case is being entered
                    in it — against a foreign-currency amount it would both
                    mislabel the limit and invent (or suppress) an overrun. */}
                {remaining != null && (
                  <p
                    className={
                      inPolicyCurrency && amountValid && amountValue > remaining
                        ? "text-xs tabular-nums text-warn"
                        : "text-xs tabular-nums text-muted-foreground"
                    }
                  >
                    Remaining limit {POLICY_CURRENCY} {remaining.toFixed(2)}
                    {!inPolicyCurrency
                      ? ` — this case is in ${currency}`
                      : amountValid && amountValue > remaining
                        ? " — this exceeds it"
                        : ""}
                  </p>
                )}
              </Field>
              <Field label="Currency" htmlFor="log-currency">
                <NativeSelect
                  id="log-currency"
                  className="h-9 w-full"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                >
                  {CURRENCIES.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </div>
          </Block>

          <Block title="Where it came from">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Received via" htmlFor="log-via" optional>
                <NativeSelect
                  id="log-via"
                  className="h-9 w-full"
                  value={receivedVia}
                  onChange={(e) => setReceivedVia(e.target.value)}
                >
                  {RECEIVED_VIA.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Received on" htmlFor="log-received" optional>
                <Input
                  id="log-received"
                  type="date"
                  value={receivedOn}
                  onChange={(e) => setReceivedOn(e.target.value)}
                />
              </Field>
              <div className="sm:col-span-2">
                <Field label="Requested by" htmlFor="log-requester" optional>
                  <Input
                    id="log-requester"
                    value={requestedBy}
                    onChange={(e) => setRequestedBy(e.target.value)}
                    placeholder="e.g. HR — Serene Lim"
                  />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Field label="Internal note" htmlFor="log-remarks" optional>
                  <Input
                    id="log-remarks"
                    value={remarks}
                    onChange={(e) => setRemarks(e.target.value)}
                  />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Field label="Documents" htmlFor="log-files" optional>
                  <Input
                    id="log-files"
                    type="file"
                    multiple
                    accept=".pdf,.png,.jpg,.jpeg"
                    className="h-auto py-1.5"
                    onChange={(e) =>
                      setFiles((prev) => [...prev, ...Array.from(e.target.files ?? [])])
                    }
                  />
                  {files.length > 0 && (
                    <ul className="space-y-1.5 pt-1">
                      {files.map((f, i) => (
                        <li
                          key={`${f.name}-${i}`}
                          className="flex items-center gap-2 rounded-md border border-border bg-muted px-2.5 py-1.5 text-xs"
                        >
                          <Paperclip className="size-3.5 shrink-0 text-muted-foreground" />
                          <span className="truncate">{f.name}</span>
                          <button
                            type="button"
                            className="ml-auto shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                            aria-label={`Remove ${f.name}`}
                            onClick={() =>
                              setFiles((prev) => prev.filter((_, j) => j !== i))
                            }
                          >
                            <X className="size-3.5" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </Field>
              </div>
            </div>
          </Block>
            </>
          )}
        </SheetBody>

        <SheetFooter className="justify-start">
          <Button onClick={submit} disabled={!canSave}>
            {create.isPending && <Loader2 className="size-4 animate-spin" />}
            Record LOG case
          </Button>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
