import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useState,
} from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  usePolicyYears,
  useResetProductTerm,
  useSetProductTerm,
} from "@/api/hooks";
import { formatError } from "@/lib/errors";
import type { ProductTerm } from "@/types";
import { toast } from "sonner";

// Product premiums are GST-exclusive unless the broker explicitly includes it.
// Legacy null values therefore render as Exclude.
type GstOpinion = "include" | "exclude";

export interface CoveragePeriodEditorHandle {
  save: () => Promise<void>;
}

interface CoveragePeriodEditorProps {
  policyYearId: string;
  term: ProductTerm;
  onStatusChange?: (status: {
    dirty: boolean;
    valid: boolean;
    busy: boolean;
  }) => void;
}

function toOpinion(v: boolean | null): GstOpinion {
  return v ? "include" : "exclude";
}
function fromOpinion(o: GstOpinion): boolean {
  return o === "include";
}

/**
 * Compact staged editor for per-product terms. Confirm Setup calls the exposed
 * save method, which sends only changed fields. A server-value key remounts the
 * editor after confirmation or reset so local state follows persisted values.
 */
export const CoveragePeriodEditor = forwardRef<
  CoveragePeriodEditorHandle,
  CoveragePeriodEditorProps
>(function CoveragePeriodEditor(
  { policyYearId, term, onStatusChange },
  ref,
) {
  const [start, setStart] = useState(term.coverage_start);
  const [end, setEnd] = useState(term.coverage_end);
  const [gstOpinion, setGstOpinion] = useState<GstOpinion>(
    toOpinion(term.gst_included),
  );
  const [gstRate, setGstRate] = useState<string>(
    term.gst_rate != null ? String(term.gst_rate) : "",
  );
  const [fcl, setFcl] = useState<string>(
    term.free_cover_limit != null ? String(term.free_cover_limit) : "",
  );
  const [nelAge, setNelAge] = useState<string>(
    term.nel_age_limit != null ? String(term.nel_age_limit) : "",
  );
  const [underwritingRequired, setUnderwritingRequired] = useState(
    term.underwriting_required,
  );
  // Pre-/post-hospitalisation claim window. Blank is NOT zero — it means the
  // product states no window, and the review's check does not run.
  const [preDays, setPreDays] = useState<string>(
    term.pre_hosp_days != null ? String(term.pre_hosp_days) : "",
  );
  const [postDays, setPostDays] = useState<string>(
    term.post_hosp_days != null ? String(term.post_hosp_days) : "",
  );
  const setTerm = useSetProductTerm(policyYearId);
  const resetTerm = useResetProductTerm(policyYearId);
  // Reset (DELETE) clears the whole row incl. the activation-locked coverage
  // dates / GST, so the server rejects it on a non-draft year. Only the
  // operational fields (FCL, policy no.) stay editable there — cleared by
  // blanking + confirmation — so don't offer a Reset that would only 409.
  const { data: policyYears = [] } = usePolicyYears();
  const activeYear = policyYears.find((y) => y.id === policyYearId);
  const locked = activeYear !== undefined && activeYear.status !== "draft";
  // SERVED (`ProductTermOut.is_inpatient`), never matched on the code or the
  // line here: product-type knowledge lives in `product_registry.py` alone.
  const isInpatientLine = term.is_inpatient;
  const isLife = term.line === "life";
  const hasUnderwritingChoice =
    term.line === "medical" || term.line === "general";

  const datesDirty = start !== term.coverage_start || end !== term.coverage_end;
  const parsedRate = gstRate.trim() === "" ? null : Number(gstRate);
  const initialOpinion = toOpinion(term.gst_included);
  const gstDirty =
    gstOpinion !== initialOpinion ||
    (gstOpinion === "include" && parsedRate !== term.gst_rate);
  const parsedFcl = fcl.trim() === "" ? null : Number(fcl.replace(/,/g, ""));
  const fclDirty = isLife && parsedFcl !== term.free_cover_limit;
  const parsedNelAge = nelAge.trim() === "" ? null : Number(nelAge);
  const nelAgeDirty = isLife && parsedNelAge !== term.nel_age_limit;
  const underwritingDirty =
    hasUnderwritingChoice &&
    underwritingRequired !== term.underwriting_required;
  const parsedPre = preDays.trim() === "" ? null : Number(preDays);
  const parsedPost = postDays.trim() === "" ? null : Number(postDays);
  const preDirty = parsedPre !== term.pre_hosp_days;
  const postDirty = parsedPost !== term.post_hosp_days;
  const dirty =
    datesDirty ||
    gstDirty ||
    fclDirty ||
    nelAgeDirty ||
    underwritingDirty ||
    preDirty ||
    postDirty;

  const datesValid = Boolean(start) && Boolean(end) && end >= start;
  const rateValid =
    gstOpinion !== "include" ||
    parsedRate === null ||
    (Number.isFinite(parsedRate) && parsedRate >= 0 && parsedRate <= 100);
  const fclValid =
    parsedFcl === null || (Number.isFinite(parsedFcl) && parsedFcl >= 0);
  const nelAgeValid =
    parsedNelAge === null ||
    (Number.isInteger(parsedNelAge) && parsedNelAge >= 1 && parsedNelAge <= 120);
  const daysValid = (v: number | null) =>
    v === null || (Number.isInteger(v) && v >= 0 && v <= 365);
  const valid =
    datesValid &&
    rateValid &&
    fclValid &&
    nelAgeValid &&
    daysValid(parsedPre) &&
    daysValid(parsedPost);
  const busy = setTerm.isPending || resetTerm.isPending;
  // The server row exists in some non-default form (dates or a GST opinion).
  const hasOverride =
    !term.is_default ||
    term.gst_included !== null ||
    term.gst_rate != null ||
    term.free_cover_limit != null ||
    term.nel_age_limit != null ||
    (hasUnderwritingChoice && term.underwriting_required) ||
    term.pre_hosp_days != null ||
    term.post_hosp_days != null;

  const save = useCallback(async () => {
    if (!dirty) return;
    if (!valid) throw new Error("Review the policy term fields before confirming.");

    // Partial update — send only the dimension(s) actually changed so one
    // never resets the other. Dates ride along only when edited.
    await setTerm.mutateAsync({
      productId: term.product_id,
      ...(datesDirty ? { coverageStart: start, coverageEnd: end } : {}),
      ...(gstDirty
        ? {
            gstIncluded: fromOpinion(gstOpinion),
            gstRate: gstOpinion === "include" ? parsedRate : null,
          }
        : {}),
      ...(fclDirty ? { freeCoverLimit: parsedFcl } : {}),
      ...(nelAgeDirty ? { nelAgeLimit: parsedNelAge } : {}),
      ...(underwritingDirty ? { underwritingRequired } : {}),
      ...(preDirty ? { preHospDays: parsedPre } : {}),
      ...(postDirty ? { postHospDays: parsedPost } : {}),
    });
  }, [
    datesDirty,
    dirty,
    end,
    fclDirty,
    gstDirty,
    gstOpinion,
    nelAgeDirty,
    parsedFcl,
    parsedNelAge,
    parsedPost,
    parsedPre,
    parsedRate,
    postDirty,
    preDirty,
    setTerm,
    start,
    term.product_id,
    underwritingDirty,
    underwritingRequired,
    valid,
  ]);

  useImperativeHandle(ref, () => ({ save }), [save]);

  useEffect(() => {
    onStatusChange?.({ dirty, valid, busy });
  }, [busy, dirty, onStatusChange, valid]);

  const reset = async () => {
    try {
      await resetTerm.mutateAsync(term.product_id);
      toast.success(`${term.code} now inherits the policy year's dates`);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-muted/20 p-3">
      <div className="flex min-w-max items-center gap-3">
          <div className="flex shrink-0 items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">GST</Label>
            <InfoHint>
              Product premiums exclude GST. Choose Include GST to gross
              premiums and flex price tags by this rate (normally 9%).
            </InfoHint>
            <Select
              value={gstOpinion}
              onValueChange={(v) => setGstOpinion(v as GstOpinion)}
            >
              <SelectTrigger
                className="h-8 w-[136px] whitespace-nowrap"
                aria-label={`${term.code} GST`}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="exclude">Exclude GST</SelectItem>
                <SelectItem value="include">Include GST</SelectItem>
              </SelectContent>
            </Select>
            {gstOpinion === "include" && (
              <div className="flex items-center gap-1.5">
                <Input
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  className="h-8 w-[64px] px-2"
                  value={gstRate}
                  onChange={(e) => setGstRate(e.target.value)}
                  placeholder="9"
                  aria-label={`${term.code} GST rate (%)`}
                />
                <span className="text-sm text-muted-foreground">%</span>
              </div>
            )}
          </div>

          {isLife && (
            <>
              <div className="flex shrink-0 items-center gap-1.5">
                <Label className="text-xs text-muted-foreground">
                  FCL
                </Label>
                <InfoHint>
                  Sum insured auto-accepted without medical underwriting.
                  Members whose eligible SI exceeds it appear in the
                  Underwriting queue. Blank = no limit.
                </InfoHint>
                <Input
                  type="number"
                  min={0}
                  step={1000}
                  className="h-8 w-[108px] px-2"
                  value={fcl}
                  onChange={(e) => setFcl(e.target.value)}
                  placeholder="No limit"
                  aria-label={`${term.code} free cover limit`}
                />
              </div>

              <div className="flex shrink-0 items-center gap-1.5">
                <Label className="text-xs text-muted-foreground">NEL age</Label>
                <InfoHint>
                  Non-Evidence-Limit age (age next birthday). Members at or
                  above it require underwriting regardless of sum insured.
                  Blank = no age gate.
                </InfoHint>
                <Input
                  type="number"
                  min={1}
                  max={120}
                  className="h-8 w-[64px] px-2"
                  value={nelAge}
                  onChange={(e) => setNelAge(e.target.value)}
                  placeholder="—"
                  aria-label={`${term.code} NEL age limit`}
                />
              </div>
            </>
          )}

          {hasUnderwritingChoice && (
            <div className="flex shrink-0 items-center gap-1.5">
              <Label className="text-xs text-muted-foreground">
                Underwriting
              </Label>
              <InfoHint>
                Whether this product requires insurer underwriting. New
                Medical and General products default to No.
              </InfoHint>
              <Select
                value={underwritingRequired ? "yes" : "no"}
                onValueChange={(value) =>
                  setUnderwritingRequired(value === "yes")
                }
              >
                <SelectTrigger
                  className="h-8 w-[88px]"
                  aria-label={`${term.code} underwriting required`}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="no">No</SelectItem>
                  <SelectItem value="yes">Yes</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Only on products whose claims draw on an inpatient benefit — the
              window is meaningless on a dental or GP line, and an input that
              can never matter is noise on a row that already carries five. */}
          {isInpatientLine && (
            <div className="flex shrink-0 items-center gap-1.5">
              <Label className="text-xs text-muted-foreground">
                Pre / post days
              </Label>
              <InfoHint>
                How long before an admission and after a discharge a
                consultation is still claimable against it ("within 90 days
                prior / 100 days after" in the policy wording). Blank = no
                window stated, and the claim review simply won't check it —
                blank is not zero.
              </InfoHint>
              <Input
                type="number"
                min={0}
                max={365}
                className="h-8 w-[58px] px-2"
                value={preDays}
                onChange={(e) => setPreDays(e.target.value)}
                placeholder="—"
                aria-label={`${term.code} pre-hospitalisation days`}
              />
              <span className="text-xs text-muted-foreground">/</span>
              <Input
                type="number"
                min={0}
                max={365}
                className="h-8 w-[58px] px-2"
                value={postDays}
                onChange={(e) => setPostDays(e.target.value)}
                placeholder="—"
                aria-label={`${term.code} post-hospitalisation days`}
              />
            </div>
          )}

          <div className="flex shrink-0 items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">Start</Label>
            <Input
              type="date"
              aria-label={`${term.code} coverage start`}
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="h-8 w-[152px] min-w-[152px] px-2"
            />
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">End</Label>
            <Input
              type="date"
              aria-label={`${term.code} coverage end`}
              value={end}
              min={start || undefined}
              onChange={(e) => setEnd(e.target.value)}
              className="h-8 w-[152px] min-w-[152px] px-2"
            />
          </div>
          {hasOverride && !locked && (
            <Button
              className="shrink-0"
              size="icon-sm"
              variant="outline"
              disabled={busy}
              onClick={reset}
              aria-label={`Reset ${term.code} terms to defaults`}
              title="Reset product terms to defaults"
            >
              <RotateCcw className="size-3.5" />
            </Button>
          )}
      </div>
      {((!datesValid && dirty) || !rateValid) && (
        <div className="mt-2 space-y-1">
          {!datesValid && dirty && (
            <p className="text-xs text-error">
              End date must be on or after the start date.
            </p>
          )}
          {!rateValid && (
            <p className="text-xs text-error">
              GST rate must be between 0 and 100.
            </p>
          )}
        </div>
      )}
    </div>
  );
});
