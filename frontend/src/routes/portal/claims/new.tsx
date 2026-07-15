/** Submit-a-claim form. Every picker is populated from the member's OWN
 * resolved coverage (`/portal/coverage-options`):
 *   Claim Category (Insurance / Flexible Benefits — only the ones the member has)
 *     → Claim Type (their covered products, or claimable flex categories)
 *       → Benefit (optional SOB refinement) + Claimant (self / covered dependants)
 * The claim is created as a draft, receipts attach to it, and submit runs the
 * backend validations (in-period, coverage exists, duplicate receipts). */
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Loader2, Paperclip, Send, X } from "lucide-react";
import { toast } from "sonner";
import {
  useCoverageOptions,
  useCreateClaim,
  useDeleteDraftClaim,
  useSubmitClaim,
  useUploadClaimDocument,
} from "@/api/portal";
import { formatError } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const ACCEPT = ".pdf,.png,.jpg,.jpeg";
const MAX_BYTES = 15 * 1024 * 1024;

// Currencies a member may incur a bill in. Insured claims default to SGD;
// flex claims lock to the wallet's own currency (often POINTS/units).
const CURRENCIES = [
  "SGD", "USD", "MYR", "EUR", "GBP", "AUD",
  "HKD", "CNY", "JPY", "INR", "IDR", "THB", "PHP",
];

export function PortalNewClaimPage() {
  const navigate = useNavigate();
  const options = useCoverageOptions();
  const createClaim = useCreateClaim();
  const uploadDoc = useUploadClaimDocument();
  const submitClaim = useSubmitClaim();
  const deleteDraft = useDeleteDraftClaim();

  const insured = options.data?.insured ?? [];
  const flex = options.data?.flex ?? null;
  const hasInsured = insured.length > 0;
  const hasFlex = (flex?.categories.length ?? 0) > 0;
  const walletCurrency = flex?.currency ?? "SGD";

  const [kind, setKind] = useState<"insured" | "flex">("insured");
  const [productCode, setProductCode] = useState("");
  const [benefitKey, setBenefitKey] = useState("");
  const [flexCategory, setFlexCategory] = useState("");
  const [dependantId, setDependantId] = useState("");
  const [incurredDate, setIncurredDate] = useState("");
  const [provider, setProvider] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("SGD");
  const [remarks, setRemarks] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // The effective category: force to whichever the member actually has when
  // only one exists (so a member with no flex never sits on an empty "flex").
  const effectiveKind: "insured" | "flex" =
    kind === "flex" && hasFlex ? "flex" : hasInsured ? "insured" : "flex";
  const effectiveCurrency = effectiveKind === "flex" ? walletCurrency : currency;

  const selectedProduct = useMemo(
    () => insured.find((p) => p.product_code === productCode) ?? null,
    [insured, productCode],
  );
  const eligibleDependants = useMemo(() => {
    if (!selectedProduct?.covers_dependants) return [];
    const covered = new Set(selectedProduct.covered_dependant_ids);
    return (options.data?.dependants ?? []).filter((d) => covered.has(d.id));
  }, [selectedProduct, options.data]);

  if (options.isLoading) return <Skeleton className="h-64 w-full" />;
  if (options.isError || !options.data || (!hasInsured && !hasFlex)) {
    return (
      <p className="text-sm text-muted-foreground">
        No active coverage — claims can't be submitted right now.
      </p>
    );
  }

  const changeKind = (next: "insured" | "flex") => {
    setKind(next);
    // Reset the cascaded selections + currency when the category changes.
    setProductCode("");
    setBenefitKey("");
    setFlexCategory("");
    setDependantId("");
    setCurrency(next === "flex" ? walletCurrency : "SGD");
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

  const canSubmit =
    (effectiveKind === "insured" ? Boolean(productCode) : Boolean(flexCategory)) &&
    incurredDate !== "" &&
    provider.trim() !== "" &&
    invoiceNumber.trim() !== "" &&
    Number(amount) > 0 &&
    files.length > 0;

  const submit = async () => {
    setError(null);
    setBusy(true);
    let claimId: string | null = null;
    try {
      const claim = await createClaim.mutateAsync({
        claim_kind: effectiveKind,
        product_code: effectiveKind === "insured" ? productCode : null,
        benefit_key: effectiveKind === "insured" && benefitKey ? benefitKey : null,
        flex_category_name: effectiveKind === "flex" ? flexCategory : null,
        claim_type:
          effectiveKind === "flex"
            ? flexCategory
            : benefitKey || selectedProduct?.product_name || productCode,
        incurred_date: incurredDate,
        provider_name: provider.trim(),
        invoice_number: invoiceNumber.trim(),
        remarks: remarks.trim() || null,
        amount_claimed: Number(amount),
        currency: effectiveCurrency,
        dependant_id: dependantId || null,
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
      // Roll the draft back so a failed validation doesn't strand it.
      if (claimId) {
        try {
          await deleteDraft.mutateAsync(claimId);
        } catch {
          /* already submitted or gone — leave it for the list view */
        }
      }
    } finally {
      setBusy(false);
    }
  };

  const selectClass =
    "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-ring disabled:cursor-not-allowed disabled:opacity-60";

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
            policy year ({options.data.policy_year_start} to{" "}
            {options.data.policy_year_end}); your broker reviews every claim
            before it's approved.
          </p>
        </div>

        {/* Claim Category — only the surfaces the member actually has. */}
        <div className="space-y-1.5">
          <Label>
            Claim category <span className="text-error">*</span>
          </Label>
          <select
            className={selectClass}
            value={effectiveKind}
            disabled={!(hasInsured && hasFlex)}
            onChange={(e) => changeKind(e.target.value as "insured" | "flex")}
          >
            {hasInsured && <option value="insured">Insurance</option>}
            {hasFlex && <option value="flex">Flexible Benefits</option>}
          </select>
        </div>

        {/* Claim Type — cascades from the category. */}
        {effectiveKind === "insured" ? (
          <>
            <div className="space-y-1.5">
              <Label>
                Claim type <span className="text-error">*</span>
              </Label>
              <select
                className={selectClass}
                value={productCode}
                onChange={(e) => {
                  setProductCode(e.target.value);
                  setBenefitKey("");
                  setDependantId("");
                }}
              >
                <option value="">Select an option</option>
                {insured.map((p) => (
                  <option key={p.product_code} value={p.product_code}>
                    {p.product_name || p.product_code}
                    {p.product_name && p.product_code
                      ? ` (${p.product_code})`
                      : ""}
                  </option>
                ))}
              </select>
            </div>
            {selectedProduct && selectedProduct.benefit_items.length > 0 && (
              <div className="space-y-1.5">
                <Label>Benefit</Label>
                <select
                  className={selectClass}
                  value={benefitKey}
                  onChange={(e) => setBenefitKey(e.target.value)}
                >
                  <option value="">General / not sure</option>
                  {selectedProduct.benefit_items.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {eligibleDependants.length > 0 && (
              <div className="space-y-1.5">
                <Label>Claimant</Label>
                <select
                  className={selectClass}
                  value={dependantId}
                  onChange={(e) => setDependantId(e.target.value)}
                >
                  <option value="">Myself</option>
                  {eligibleDependants.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name ?? "Dependant"}
                      {d.relationship ? ` (${d.relationship})` : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </>
        ) : (
          <div className="space-y-1.5">
            <Label>
              Claim type <span className="text-error">*</span>
            </Label>
            <select
              className={selectClass}
              value={flexCategory}
              onChange={(e) => setFlexCategory(e.target.value)}
            >
              <option value="">Select an option</option>
              {flex?.categories.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                  {c.sub_limit != null ? ` (up to ${c.sub_limit})` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="claim-date">
              Incurred date <span className="text-error">*</span>
            </Label>
            <Input
              id="claim-date"
              type="date"
              min={options.data.policy_year_start}
              max={options.data.policy_year_end}
              value={incurredDate}
              onChange={(e) => setIncurredDate(e.target.value)}
            />
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
                CURRENCIES.map((c) => (
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
          </div>
        </div>

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
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="claim-remarks">Remarks</Label>
          <textarea
            id="claim-remarks"
            rows={3}
            className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-ring"
            placeholder="Anything your broker should know about this claim (optional)"
            value={remarks}
            maxLength={2000}
            onChange={(e) => setRemarks(e.target.value)}
          />
        </div>

        {error && <p className="text-xs text-error">{error}</p>}

        <Button className="w-full" disabled={!canSubmit || busy} onClick={submit}>
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
