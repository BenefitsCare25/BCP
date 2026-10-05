import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ProductTerm, SetupPolicyTerms } from "@/types";

interface Props {
  term: ProductTerm;
  value: SetupPolicyTerms;
  onChange: (value: SetupPolicyTerms) => void;
  disabled?: boolean;
}

export function validSetupTerms(term: ProductTerm | null, value: SetupPolicyTerms): boolean {
  if (!term) return Object.keys(value).length === 0;
  const start = value.coverage_start ?? term.coverage_start;
  const end = value.coverage_end ?? term.coverage_end;
  const numberValid = (raw: string | undefined, min: number, max: number, integer = false) => {
    if (raw === undefined || raw.trim() === "") return true;
    const n = Number(raw.replace(/,/g, ""));
    return Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n));
  };
  return Boolean(start && end && end >= start)
    && numberValid(value.gst_rate, 0, 100)
    && numberValid(value.free_cover_limit, 0, Number.MAX_VALUE)
    && numberValid(value.nel_age_limit, 1, 120, true)
    && numberValid(value.pre_hosp_days, 0, 365, true)
    && numberValid(value.post_hosp_days, 0, 365, true);
}

/** Controlled by the product form: section unmounts cannot discard staged terms. */
export function CoveragePeriodEditor({ term, value, onChange, disabled = false }: Props) {
  const start = value.coverage_start ?? term.coverage_start;
  const end = value.coverage_end ?? term.coverage_end;
  const gstOpinion = (value.gst_included ?? term.gst_included) ? "include" : "exclude";
  const gstRate = value.gst_rate ?? String(term.gst_rate ?? "");
  const fcl = value.free_cover_limit ?? String(term.free_cover_limit ?? "");
  const nelAge = value.nel_age_limit ?? String(term.nel_age_limit ?? "");
  const underwritingRequired = value.underwriting_required ?? term.underwriting_required;
  const preDays = value.pre_hosp_days ?? String(term.pre_hosp_days ?? "");
  const postDays = value.post_hosp_days ?? String(term.post_hosp_days ?? "");
  const isInpatientLine = term.is_inpatient;
  const isLife = term.line === "life";
  const hasUnderwritingChoice = term.line === "medical" || term.line === "general";
  const datesValid = Boolean(start && end && end >= start);
  const parsedRate = gstRate.trim() === "" ? null : Number(gstRate);
  const rateValid = gstOpinion !== "include" || parsedRate === null
    || (Number.isFinite(parsedRate) && parsedRate >= 0 && parsedRate <= 100);
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
              onValueChange={(v) => onChange({ ...value, gst_included: v === "include", gst_rate: v === "include" ? gstRate : "" })}
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
                  onChange={(e) => onChange({ ...value, gst_rate: e.target.value })}
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
                  onChange={(e) => onChange({ ...value, free_cover_limit: e.target.value })}
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
                  onChange={(e) => onChange({ ...value, nel_age_limit: e.target.value })}
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
                onValueChange={(choice) =>
                  onChange({ ...value, underwriting_required: choice === "yes" })
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
                onChange={(e) => onChange({ ...value, pre_hosp_days: e.target.value })}
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
                onChange={(e) => onChange({ ...value, post_hosp_days: e.target.value })}
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
              onChange={(e) => onChange({ ...value, coverage_start: e.target.value, coverage_end: end })}
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
              onChange={(e) => onChange({ ...value, coverage_start: start, coverage_end: e.target.value })}
              className="h-8 w-[152px] min-w-[152px] px-2"
            />
          </div>
          {Object.keys(value).length > 0 && (
            <Button
              className="shrink-0"
              size="icon-sm"
              variant="outline"
              disabled={disabled}
              onClick={() => onChange({})}
              aria-label={`Revert ${term.code} term edits`}
              title="Revert pending term edits"
            >
              <RotateCcw className="size-3.5" />
            </Button>
          )}
      </div>
      {((!datesValid && Object.keys(value).length > 0) || !rateValid) && (
        <div className="mt-2 space-y-1">
          {!datesValid && Object.keys(value).length > 0 && (
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
}
