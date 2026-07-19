import { useState } from "react";
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

// Tri-state GST opinion: inherit (null) / include (true) / exclude (false).
type GstOpinion = "inherit" | "include" | "exclude";

function toOpinion(v: boolean | null): GstOpinion {
  if (v === null) return "inherit";
  return v ? "include" : "exclude";
}
function fromOpinion(o: GstOpinion): boolean | null {
  if (o === "inherit") return null;
  return o === "include";
}

/**
 * Compact per-product terms editor: coverage period + GST. Inputs are a pure
 * function of server state — remount (via a key on the server values) after a
 * save/reset to discard local edits. Coverage dates and GST are independent
 * dimensions; each saves only the fields it changed (partial update).
 */
export function CoveragePeriodEditor({
  policyYearId,
  term,
}: {
  policyYearId: string;
  term: ProductTerm;
}) {
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
  const setTerm = useSetProductTerm(policyYearId);
  const resetTerm = useResetProductTerm(policyYearId);
  // Reset (DELETE) clears the whole row incl. the activation-locked coverage
  // dates / GST, so the server rejects it on a non-draft year. Only the
  // operational fields (FCL, policy no.) stay editable there — cleared by
  // blanking + Save — so don't offer a Reset that would only 409.
  const { data: policyYears = [] } = usePolicyYears();
  const activeYear = policyYears.find((y) => y.id === policyYearId);
  const locked = activeYear !== undefined && activeYear.status !== "draft";

  const datesDirty = start !== term.coverage_start || end !== term.coverage_end;
  const parsedRate = gstRate.trim() === "" ? null : Number(gstRate);
  const initialOpinion = toOpinion(term.gst_included);
  const gstDirty =
    gstOpinion !== initialOpinion ||
    (gstOpinion === "include" && parsedRate !== term.gst_rate);
  const parsedFcl = fcl.trim() === "" ? null : Number(fcl.replace(/,/g, ""));
  const fclDirty = parsedFcl !== term.free_cover_limit;
  const dirty = datesDirty || gstDirty || fclDirty;

  const datesValid = Boolean(start) && Boolean(end) && end >= start;
  const rateValid =
    gstOpinion !== "include" ||
    parsedRate === null ||
    (Number.isFinite(parsedRate) && parsedRate >= 0 && parsedRate <= 100);
  const fclValid =
    parsedFcl === null || (Number.isFinite(parsedFcl) && parsedFcl >= 0);
  const valid = datesValid && rateValid && fclValid;
  const busy = setTerm.isPending || resetTerm.isPending;
  // The server row exists in some non-default form (dates or a GST opinion).
  const hasOverride =
    !term.is_default ||
    term.gst_included !== null ||
    term.gst_rate != null ||
    term.free_cover_limit != null;

  const save = async () => {
    try {
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
      });
      toast.success(`Updated ${term.code} terms`);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const reset = async () => {
    try {
      await resetTerm.mutateAsync(term.product_id);
      toast.success(`${term.code} now inherits the policy year's dates`);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="rounded-lg border border-border bg-muted/20 p-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <div className="flex items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">GST</Label>
            <InfoHint>
              Slip amounts are GST-exclusive. “Include” grosses premiums and flex
              price tags by this rate (default 9%). “Inherit” follows the flex
              scheme’s GST setting.
            </InfoHint>
            <Select
              value={gstOpinion}
              onValueChange={(v) => setGstOpinion(v as GstOpinion)}
            >
              <SelectTrigger
                className="w-[224px] whitespace-nowrap"
                aria-label={`${term.code} GST`}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inherit">Inherit (scheme default)</SelectItem>
                <SelectItem value="include">Include GST</SelectItem>
                <SelectItem value="exclude">Exclude GST</SelectItem>
              </SelectContent>
            </Select>
            {gstOpinion === "include" && (
              <div className="flex items-center gap-1.5">
                <Input
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  className="w-[90px]"
                  value={gstRate}
                  onChange={(e) => setGstRate(e.target.value)}
                  placeholder="9"
                  aria-label={`${term.code} GST rate (%)`}
                />
                <span className="text-sm text-muted-foreground">%</span>
              </div>
            )}
          </div>

          <div className="flex items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">
              Free cover limit
            </Label>
            <InfoHint>
              Sum insured auto-accepted without medical underwriting. Members
              (or covered dependants) whose eligible SI exceeds it appear in
              the Underwriting queue; insurer listings show the excess as
              “Pending U/W” until a decision is recorded. Blank = no limit.
            </InfoHint>
            <Input
              type="number"
              min={0}
              step={1000}
              className="w-[130px]"
              value={fcl}
              onChange={(e) => setFcl(e.target.value)}
              placeholder="No limit"
              aria-label={`${term.code} free cover limit`}
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">Starts</Label>
            <Input
              type="date"
              aria-label={`${term.code} coverage start`}
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="w-[150px]"
            />
          </div>
          <div className="flex items-center gap-1.5">
            <Label className="text-xs text-muted-foreground">Ends</Label>
            <Input
              type="date"
              aria-label={`${term.code} coverage end`}
              value={end}
              min={start || undefined}
              onChange={(e) => setEnd(e.target.value)}
              className="w-[150px]"
            />
          </div>
          <Button size="sm" disabled={!dirty || !valid || busy} onClick={save}>
            Save
          </Button>
          {hasOverride && !locked && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={reset}
              title="Reset to the policy year's dates (clears GST too)"
            >
              <RotateCcw className="size-3.5" /> Reset
            </Button>
          )}
        </div>
        {!datesValid && dirty && (
          <p className="w-full text-xs text-error">
            End date must be on or after the start date.
          </p>
        )}
        {!rateValid && (
          <p className="w-full text-xs text-error">
            GST rate must be between 0 and 100.
          </p>
        )}
      </div>
    </div>
  );
}
