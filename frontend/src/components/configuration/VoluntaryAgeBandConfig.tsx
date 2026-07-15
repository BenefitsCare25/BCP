import { useEffect, useState } from "react";
import { Plus, Save, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { useSetVoluntaryRates, type VoluntaryRateBandInput } from "@/api/hooks";
import { formatError } from "@/lib/errors";
import type { VoluntaryRateBand } from "@/types";

// Editable row state — bounds/rate are strings so the inputs can go blank
// ("" min/max = open-ended) without snapping to 0.
interface Row {
  label: string;
  min: string;
  max: string;
  rate: string;
}

const toRow = (b: VoluntaryRateBand): Row => ({
  label: b.label ?? "",
  min: b.min != null ? String(b.min) : "",
  max: b.max != null ? String(b.max) : "",
  rate: b.rate != null ? String(b.rate) : "",
});

const numOrNull = (s: string): number | null =>
  s.trim() === "" ? null : Number(s);

/**
 * One product-wide voluntary age-band rate table, shared by ALL the product's
 * voluntary plans (instead of repeating the table under every plan). Premium for
 * a voluntary plan = the member's amount covered ÷ 1,000 × the rate for their age
 * band. Saving fans the table out to every age-banded voluntary category.
 */
export function VoluntaryAgeBandConfig({
  policyYearId,
  productId,
  bands,
  planCount,
}: {
  policyYearId: string;
  productId: string;
  bands: VoluntaryRateBand[];
  planCount: number;
}) {
  const save = useSetVoluntaryRates();
  const [rows, setRows] = useState<Row[]>(() => bands.map(toRow));

  // Re-seed only when the SERVER table actually changes (content, not array
  // identity) — keep the JSON-keyed dep so in-progress edits aren't wiped by a
  // parent re-render, mirroring the leave-rates editor.
  const serverKey = JSON.stringify(bands);
  useEffect(() => {
    setRows(bands.map(toRow));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey]);

  const setRow = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () =>
    setRows((rs) => [...rs, { label: "", min: "", max: "", rate: "" }]);
  const removeRow = (i: number) =>
    setRows((rs) => rs.filter((_, idx) => idx !== i));

  const onSave = () => {
    const payload: VoluntaryRateBandInput[] = rows.map((r) => ({
      label: r.label.trim() || "—",
      min: numOrNull(r.min),
      max: numOrNull(r.max),
      rate: Number(r.rate) || 0,
    }));
    save.mutate(
      { policyYearId, productId, bands: payload },
      {
        onSuccess: () => toast.success("Voluntary age-band rates saved"),
        onError: (e) => toast.error(formatError(e)),
      },
    );
  };

  return (
    <div className="rounded-lg border border-border bg-muted/20 p-4">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-1">
          <h4 className="text-sm font-medium text-foreground">
            Voluntary Age-Band Rates
          </h4>
          <InfoHint>
            Per S$1,000 sum assured, by age last birthday. Applies to all{" "}
            {planCount} voluntary plan{planCount === 1 ? "" : "s"} of this
            product — premium per employee = amount covered ÷ 1,000 × the rate
            for the member's age band.
          </InfoHint>
        </div>
        <Button size="sm" onClick={onSave} disabled={save.isPending}>
          <Save className="size-3.5" /> Save rates
        </Button>
      </div>

      <div className="grid grid-cols-[1.4fr_0.7fr_0.7fr_0.8fr_auto] items-end gap-2">
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Age band
        </Label>
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Min age
        </Label>
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Max age
        </Label>
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Rate / S$1k
        </Label>
        <span />
        {rows.map((r, i) => (
          <div key={i} className="contents">
            <Input
              value={r.label}
              onChange={(e) => setRow(i, { label: e.target.value })}
              placeholder="e.g. 35 to 44"
              className="h-8 text-sm"
            />
            <Input
              type="number"
              value={r.min}
              onChange={(e) => setRow(i, { min: e.target.value })}
              placeholder="—"
              className="h-8 text-sm"
            />
            <Input
              type="number"
              value={r.max}
              onChange={(e) => setRow(i, { max: e.target.value })}
              placeholder="—"
              className="h-8 text-sm"
            />
            <Input
              type="number"
              value={r.rate}
              onChange={(e) => setRow(i, { rate: e.target.value })}
              placeholder="0.00"
              className="h-8 text-sm"
            />
            <Button
              size="icon"
              variant="ghost"
              aria-label="Remove band"
              className="h-8 w-8 shrink-0 text-error hover:text-error"
              onClick={() => removeRow(i)}
            >
              <X className="size-4" />
            </Button>
          </div>
        ))}
      </div>

      <Button size="sm" variant="outline" onClick={addRow} className="mt-3">
        <Plus className="size-3.5" /> Add band
      </Button>
    </div>
  );
}
