import { useMemo, useRef, useState } from "react";
import {
  Upload,
  CheckCircle2,
  AlertTriangle,
  CalendarClock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AlertDialog } from "@/components/ui/alert-dialog";
import {
  useCreateProduct,
  usePlacementSlips,
  useProducts,
  useSaveTemplateProfile,
  useUpdateProduct,
  useUploadSlip,
} from "@/api/hooks";
import { useRegistry } from "@/api/registry";
import { PeriodMismatchError, type PeriodMismatchDetail } from "@/api/client";
import { useSession } from "@/stores/session";
import type {
  InsuranceLine,
  ParseResult,
  PlacementSlipSummary,
  ProductDiagnostic,
  RegistryEntry,
  SlipColumnRoles,
} from "@/types";
import { formatError } from "@/lib/errors";
import { formatPolicyRange } from "@/lib/policy-year";
import { LINE_LABELS, lineForCode } from "@/lib/insuranceLines";
import { toast } from "sonner";

const ALLOWED_EXT = [".xls", ".xlsx", ".xlsm"];
const MAX_BYTES = 50 * 1024 * 1024; // mirrors backend DEFAULT_MAX_BYTES

function validateFile(file: File): string | null {
  const lower = file.name.toLowerCase();
  if (!ALLOWED_EXT.some((ext) => lower.endsWith(ext))) {
    return `Unsupported file type — use ${ALLOWED_EXT.join(", ")}.`;
  }
  if (file.size === 0) {
    return "File is empty.";
  }
  if (file.size > MAX_BYTES) {
    return `File is too large (${(file.size / 1024 / 1024).toFixed(1)} MB) — max 50 MB.`;
  }
  return null;
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Summarize which line tabs the extracted products landed in, e.g.
 *  "3 → Medical Insurance · 1 → Life Insurance". */
function describeLineRouting(
  codes: string[],
  registryEntries?: RegistryEntry[],
): string | undefined {
  if (!codes.length) return undefined;
  const counts = new Map<string, number>();
  for (const code of codes) {
    const label = LINE_LABELS[lineForCode(code, registryEntries)];
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return Array.from(counts, ([label, n]) => `${n} → ${label}`).join(" · ");
}

/** Per-product parse/reconciliation summary: green when every category mapped
 *  to a plan, otherwise the products that need review and why. */
function ProductDiagnostics({
  products,
  onClassified,
}: {
  products: ProductDiagnostic[];
  onClassified: (code: string) => void;
}) {
  if (!products || products.length === 0) return null;
  const flagged = products.filter((p) => p.needs_attention);
  const usedAi = products.filter((p) => p.used_ai);

  const totalBenefitLines = products.reduce(
    (sum, p) => sum + (p.n_benefit_items ?? 0),
    0,
  );

  if (flagged.length === 0) {
    return (
      <div className="flex items-center gap-1.5 text-good">
        <CheckCircle2 className="size-3.5" />
        All {products.length} product{products.length === 1 ? "" : "s"} mapped to
        plans cleanly
        {totalBenefitLines > 0 && (
          <span className="text-muted-foreground">
            {" "}
            · {totalBenefitLines} benefit line{totalBenefitLines === 1 ? "" : "s"}{" "}
            extracted
          </span>
        )}
        {usedAi.length > 0 && (
          <span className="text-muted-foreground"> ({usedAi.length} via AI)</span>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1.5 pt-1">
      <div className="text-warn">
        {flagged.length} product{flagged.length === 1 ? "" : "s"} need review —
        open Product Setup to map plans:
      </div>
      {flagged.map((p) => (
        <div
          key={p.sheet}
          className="rounded-md border border-border bg-card px-2.5 py-1.5"
        >
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-foreground">
              {p.product_code}
            </span>
            <Badge variant="warn">
              {p.needs_classification
                ? "needs classification"
                : p.empty_sob
                  ? "empty SOB"
                  : p.reconciliation.replace("_", " ")}
            </Badge>
            {p.used_ai && <Badge variant="outline">AI</Badge>}
            <span className="text-muted-foreground">
              {Math.round(p.confidence * 100)}% confidence · {p.n_benefit_items}{" "}
              benefit line{p.n_benefit_items === 1 ? "" : "s"}
            </span>
          </div>
          {p.issues.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-muted-foreground">
              {p.issues.map((issue, i) => (
                <li key={i}>{issue}</li>
              ))}
            </ul>
          )}
          {p.needs_classification && (
            <ClassifyProduct product={p} onClassified={onClassified} />
          )}
          {p.fingerprint && <ColumnMappingFixer product={p} />}
        </div>
      ))}
    </div>
  );
}

/** Lets the broker classify an unrecognized product code (pick its form
 *  profile + line). Persisted on the product's metadata, so the NEXT upload of
 *  the slip extracts with the right structure. */
function ClassifyProduct({
  product,
  onClassified,
}: {
  product: ProductDiagnostic;
  onClassified: (code: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [profileId, setProfileId] = useState("tiered_medical");
  const [line, setLine] = useState<InsuranceLine>("medical");
  const { data: registry } = useRegistry();
  const { data: products = [] } = useProducts();
  const createProduct = useCreateProduct();
  const updateProduct = useUpdateProduct();

  const profiles = registry?.profiles ?? [];
  const lines = registry?.lines ?? ["medical", "general", "life", "flex"];
  const saving = createProduct.isPending || updateProduct.isPending;

  const save = async () => {
    const profile = profiles.find((pr) => pr.id === profileId);
    const layoutFamily = profile?.layout_family ?? "plan_tier";
    const code = product.product_code.trim().toUpperCase();
    const existing = products.find(
      (row) => row.code.trim().toUpperCase() === code && row.client_id,
    );
    try {
      if (existing) {
        await updateProduct.mutateAsync({
          id: existing.id,
          patch: {
            form_profile: profileId,
            layout_family: layoutFamily,
            line,
          },
        });
      } else {
        await createProduct.mutateAsync({
          code,
          display_name: code,
          participation_model: "standard",
          has_dependants: false,
          is_outpatient: false,
          line,
          form_profile: profileId,
          layout_family: layoutFamily,
        });
      }
      toast.success(
        `Classified ${code} — re-upload the slip to re-extract with this product type.`,
      );
      onClassified(code);
      setOpen(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  if (!open) {
    return (
      <Button
        variant="outline"
        size="sm"
        className="mt-1.5"
        onClick={() => setOpen(true)}
      >
        Classify product type
      </Button>
    );
  }
  return (
    <div className="mt-2 space-y-2 rounded-md border border-border bg-muted/30 p-2.5">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-muted-foreground">
          Product type
          <select
            className="w-full rounded-md border border-input bg-card px-2 py-1.5 text-xs text-foreground"
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
          >
            {profiles.map((pr) => (
              <option key={pr.id} value={pr.id}>
                {pr.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          Insurance line (tab)
          <select
            className="w-full rounded-md border border-input bg-card px-2 py-1.5 text-xs text-foreground"
            value={line}
            onChange={(e) => setLine(e.target.value as InsuranceLine)}
          >
            {lines.map((l) => (
              <option key={l} value={l}>
                {LINE_LABELS[l]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save classification"}
        </Button>
        <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** Lets the broker correct the Schedule-of-Benefits column mapping for a sheet
 *  whose layout the auto-profiler mis-read. Saved per template fingerprint and
 *  reused on the next upload. Columns are 0-based (0 = first/leftmost). */
function ColumnMappingFixer({ product }: { product: ProductDiagnostic }) {
  const detected = product.column_roles;
  const [open, setOpen] = useState(false);
  const [roles, setRoles] = useState<SlipColumnRoles>(
    detected ?? {
      name_col: 0,
      key_col: null,
      value_col: null,
      allow_letter_keys: false,
      name_first: true,
    },
  );
  const save = useSaveTemplateProfile();

  const numField = (
    label: string,
    key: "name_col" | "key_col" | "value_col",
    optional = false,
  ) => (
    <label className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <input
        type="number"
        min={0}
        value={roles[key] ?? ""}
        placeholder={optional ? "none" : "0"}
        onChange={(e) =>
          setRoles((r) => ({
            ...r,
            [key]: e.target.value === "" ? null : Number(e.target.value),
          }))
        }
        className="w-16 rounded border border-border bg-background px-1.5 py-0.5 text-right text-foreground"
      />
    </label>
  );

  const onSave = () => {
    if (!product.fingerprint) return;
    save.mutate(
      {
        fingerprint: product.fingerprint,
        product_code: product.product_code,
        sheet_label: product.sheet,
        roles,
      },
      {
        onSuccess: () => {
          toast.success("Column mapping saved", {
            description: "Re-upload this slip to apply the corrected mapping.",
          });
          setOpen(false);
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-1.5 text-xs text-primary underline-offset-2 hover:underline"
      >
        Fix column mapping
      </button>
    );
  }

  return (
    <div className="mt-2 space-y-1.5 rounded-md border border-border bg-muted/40 p-2 text-xs">
      <div className="text-muted-foreground">
        Map the Schedule-of-Benefits columns (0 = first column):
      </div>
      {numField("Benefit name column", "name_col")}
      {numField("Number/letter column (optional)", "key_col", true)}
      {numField("Value column (optional)", "value_col", true)}
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={roles.name_first}
          onChange={(e) =>
            setRoles((r) => ({ ...r, name_first: e.target.checked }))
          }
        />
        <span className="text-muted-foreground">
          Names are in the first column (no number column)
        </span>
      </label>
      <div className="flex gap-2 pt-1">
        <Button size="sm" onClick={onSave} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save mapping"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** All placement-slip upload state + logic, so the trigger button and the
 *  result/diagnostics panel can render in separate places (the button sits on
 *  the tab row; the results render below the tabs). */
export function useSlipUpload(policyYearId: string) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [result, setResult] = useState<ParseResult | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [duplicateOf, setDuplicateOf] = useState<PlacementSlipSummary | null>(null);
  const [periodMismatch, setPeriodMismatch] = useState<PeriodMismatchDetail | null>(
    null,
  );
  const [mismatchFile, setMismatchFile] = useState<File | null>(null);

  // Codes classified in this session — a persistent (non-toast) reminder to
  // re-upload the slip stays on the card until the next upload.
  const [classifiedCodes, setClassifiedCodes] = useState<string[]>([]);

  const upload = useUploadSlip();
  const history = usePlacementSlips(policyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);
  const { data: registry } = useRegistry();
  const { data: clientProducts = [] } = useProducts();

  // Also derive the reminder from data: diagnostics flagged a product as
  // needing classification, and the catalog now HAS one (classified from any
  // surface) — the extraction on screen still used the old/unknown type.
  const derivedClassified = useMemo(() => {
    if (!result) return [] as string[];
    return result.products
      .filter((p) => p.needs_classification)
      .map((p) => p.product_code.trim().toUpperCase())
      .filter((code) =>
        clientProducts.some(
          (row) =>
            row.code.trim().toUpperCase() === code &&
            row.client_id &&
            row.form_profile,
        ),
      );
  }, [result, clientProducts]);

  const reuploadNeeded = useMemo(
    () => Array.from(new Set([...classifiedCodes, ...derivedClassified])),
    [classifiedCodes, derivedClassified],
  );

  const priorParsed = (history.data ?? []).filter(
    (s) => s.parse_status === "parsed",
  );

  const runUpload = (
    file: File,
    opts?: { acknowledge?: boolean; targetYearId?: string },
  ) => {
    setValidationError(null);
    setResult(null);
    setFilename(file.name);
    upload.mutate(
      {
        file,
        policyYearId: opts?.targetYearId ?? policyYearId,
        acknowledgePeriodMismatch: opts?.acknowledge ?? false,
      },
      {
        onSuccess: (r) => {
          setResult(r);
          setClassifiedCodes([]); // this upload used the latest classifications
          const parts = [
            `${r.total_categories} categories extracted`,
            `${r.high_confidence} high confidence`,
          ];
          if (r.replaced_categories > 0) {
            parts.push(`${r.replaced_categories} prior rows replaced`);
          }
          if (r.rematched) {
            parts.push(
              `employees re-matched${
                r.employees_matched != null
                  ? ` (${r.employees_matched} matched)`
                  : ""
              }`,
            );
          }
          toast.success(parts.join(" · "), {
            description: describeLineRouting(r.prefilled_setups, registry?.entries),
          });
        },
        onError: (e) => {
          if (e instanceof PeriodMismatchError) {
            // Nothing was written — prompt the user to switch years or proceed.
            setPeriodMismatch(e.detail);
            setMismatchFile(file);
            return;
          }
          toast.error(formatError(e));
        },
      },
    );
  };

  const dismissMismatch = () => {
    setPeriodMismatch(null);
    setMismatchFile(null);
  };

  const switchYearAndUpload = () => {
    if (!periodMismatch?.matching_policy_year_id || !mismatchFile) return;
    setPolicyYear(periodMismatch.matching_policy_year_id);
    runUpload(mismatchFile, {
      targetYearId: periodMismatch.matching_policy_year_id,
    });
    dismissMismatch();
  };

  const uploadAnyway = () => {
    if (!mismatchFile) return;
    runUpload(mismatchFile, { acknowledge: true });
    dismissMismatch();
  };

  const onPick = (file: File | null) => {
    if (!file) return;
    setResult(null);
    dismissMismatch();
    const err = validateFile(file);
    if (err) {
      setFilename(file.name);
      setValidationError(err);
      toast.error(err);
      return;
    }
    setValidationError(null);

    // Duplicate detection: a prior parsed upload of the same filename will be
    // superseded (its unreviewed auto rows replaced). Confirm before doing so.
    const dup = priorParsed.find(
      (s) => s.filename.toLowerCase() === file.name.toLowerCase(),
    );
    if (dup) {
      setPendingFile(file);
      setDuplicateOf(dup);
      return;
    }
    runUpload(file);
  };

  const confirmReplace = () => {
    if (pendingFile) runUpload(pendingFile);
    setPendingFile(null);
    setDuplicateOf(null);
  };

  return {
    fileInput,
    filename,
    validationError,
    result,
    periodMismatch,
    duplicateOf,
    reuploadNeeded,
    registry,
    upload,
    onPick,
    confirmReplace,
    switchYearAndUpload,
    uploadAnyway,
    dismissMismatch,
    setClassifiedCodes,
    setPendingFile,
    setDuplicateOf,
  };
}

export type SlipUpload = ReturnType<typeof useSlipUpload>;

/** Compact "Choose file" trigger (+ status badge) for the tab row. */
export function SlipUploadButton({ slip }: { slip: SlipUpload }) {
  return (
    <div className="flex items-center gap-3">
      {slip.filename && (
        <div className="flex items-center gap-2 text-sm">
          {slip.upload.isPending ? (
            <Badge variant="warn">Parsing…</Badge>
          ) : slip.validationError ? (
            <Badge variant="error" className="gap-1">
              <AlertTriangle className="size-3" /> Invalid
            </Badge>
          ) : slip.upload.isSuccess ? (
            <Badge variant="good" className="gap-1">
              <CheckCircle2 className="size-3" /> Done
            </Badge>
          ) : null}
          <span className="text-muted-foreground max-w-[180px] truncate">
            {slip.filename}
          </span>
        </div>
      )}
      <input
        ref={slip.fileInput}
        type="file"
        accept=".xls,.xlsx,.xlsm"
        className="hidden"
        onChange={(e) => {
          slip.onPick(e.target.files?.[0] ?? null);
          e.target.value = ""; // allow re-picking the same file
        }}
      />
      <Button
        variant="outline"
        size="sm"
        onClick={() => slip.fileInput.current?.click()}
        disabled={slip.upload.isPending}
        title="Upload a placement slip (.xls, .xlsx, .xlsm — max 50 MB). Re-uploading replaces this year's unreviewed auto-generated categories."
      >
        <Upload className="size-3.5" /> Upload slip
      </Button>
    </div>
  );
}

/** Validation / period-mismatch / extraction-result panel + duplicate dialog.
 *  Renders nothing (no empty card) until there is something to show. */
export function SlipUploadPanel({ slip }: { slip: SlipUpload }) {
  const {
    validationError,
    periodMismatch,
    result,
    reuploadNeeded,
    upload,
    duplicateOf,
    switchYearAndUpload,
    uploadAnyway,
    dismissMismatch,
    confirmReplace,
    setClassifiedCodes,
    setPendingFile,
    setDuplicateOf,
  } = slip;
  const hasContent =
    validationError ||
    periodMismatch ||
    (result && !upload.isPending) ||
    reuploadNeeded.length > 0;

  return (
    <>
      {hasContent && (
        <Card>
          <CardContent className="p-4 space-y-3">
        {validationError && (
          <div className="flex items-start gap-2 rounded-lg border border-border bg-error-soft/40 p-3 text-sm text-foreground">
            <AlertTriangle className="size-4 text-error mt-0.5 shrink-0" />
            <span>{validationError}</span>
          </div>
        )}

        {periodMismatch && (
          <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 p-3 text-sm text-foreground">
            <CalendarClock className="size-4 text-warn mt-0.5 shrink-0" />
            <div className="space-y-2">
              <p>
                This slip covers{" "}
                <strong>
                  {formatPolicyRange(
                    periodMismatch.slip_start,
                    periodMismatch.slip_end,
                  )}
                </strong>
                , but you're uploading into the{" "}
                <strong>
                  {formatPolicyRange(
                    periodMismatch.policy_year_start,
                    periodMismatch.policy_year_end,
                  )}
                </strong>{" "}
                policy year. Nothing was saved.
              </p>
              <div className="flex flex-wrap gap-2">
                {periodMismatch.matching_policy_year_id && (
                  <Button size="sm" onClick={switchYearAndUpload}>
                    Switch to{" "}
                    {formatPolicyRange(
                      periodMismatch.slip_start,
                      periodMismatch.slip_end,
                    )}{" "}
                    & upload
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={uploadAnyway}>
                  Upload here anyway
                </Button>
                <Button size="sm" variant="ghost" onClick={dismissMismatch}>
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        )}

        {result && !upload.isPending && (
          <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm space-y-1">
            <div className="text-foreground">
              <span className="font-medium">{result.total_categories}</span>{" "}
              categories extracted ·{" "}
              <span className="font-medium">{result.high_confidence}</span> high
              confidence ·{" "}
              <span className="font-medium">{result.needs_review}</span> need
              review
            </div>
            {result.replaced_categories > 0 && (
              <div className="text-muted-foreground">
                Replaced {result.replaced_categories} unreviewed row
                {result.replaced_categories === 1 ? "" : "s"} from a previous
                upload.
              </div>
            )}
            {result.skipped_sheets.length > 0 && (
              <div className="text-muted-foreground">
                Skipped {result.skipped_sheets.length} sheet
                {result.skipped_sheets.length === 1 ? "" : "s"}:{" "}
                {result.skipped_sheets.map((s) => s.sheet).join(", ")}
              </div>
            )}
            <ProductDiagnostics
              products={result.products}
              onClassified={(code) =>
                setClassifiedCodes((prev) =>
                  prev.includes(code) ? prev : [...prev, code],
                )
              }
            />
          </div>
        )}

        {reuploadNeeded.length > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 p-3 text-sm text-foreground">
            <AlertTriangle className="size-4 text-warn mt-0.5 shrink-0" />
            <span>
              Products classified ({reuploadNeeded.join(", ")}) — re-upload the
              slip to re-extract with the new classification. Workbooks aren't
              retained, so the stored data still reflects the old product type.
            </span>
          </div>
        )}
          </CardContent>
        </Card>
      )}

      <AlertDialog
        open={duplicateOf !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingFile(null);
            setDuplicateOf(null);
          }
        }}
        title="Replace previous upload?"
        description={
          duplicateOf && (
            <span>
              <span className="font-medium text-foreground">
                {duplicateOf.filename}
              </span>{" "}
              was already uploaded on {fmtDate(duplicateOf.created_at)} (
              {duplicateOf.total_categories} categories). Re-uploading replaces
              this year's unreviewed auto-generated categories. Confirmed and
              manually-edited categories are kept.
            </span>
          )
        }
        confirmLabel="Replace & re-upload"
        confirmVariant="destructive"
        onConfirm={confirmReplace}
        loading={upload.isPending}
      />
    </>
  );
}
