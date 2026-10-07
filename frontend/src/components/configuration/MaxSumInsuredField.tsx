import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { parseAmount } from "@/lib/basis";
import { fmtAmount } from "@/lib/format";

const display = (raw: string) => {
  const n = parseAmount(raw);
  return n === null ? raw : fmtAmount(n);
};

/**
 * The slip's "Maximum Limit per Insured Person" for a sum-assured product.
 *
 * It caps salary-multiple cover once the setup is confirmed, but used to live
 * only in hidden report metadata — so a broker could not see that GTL stops
 * at S$1.6M. Edits stay in the form until Save draft, like every other field.
 */
export function MaxSumInsuredField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [text, setText] = useState(() => display(value));
  useEffect(() => setText(display(value)), [value]);
  const parsed = parseAmount(text);
  const invalid = text.trim() !== "" && !(parsed !== null && parsed > 0);

  const commit = () => {
    if (invalid) return;
    const clean = parsed === null ? "" : String(parsed);
    // Numeric compare: the slip stores "1600000.0", which is not an edit.
    const unchanged = parsed === null ? value.trim() === "" : parseAmount(value) === parsed;
    if (!unchanged) onChange(clean);
    setText(display(clean));
  };

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-card px-3 py-2">
      <Label
        htmlFor="setup-max-sum-insured"
        className="text-2xs uppercase tracking-wider text-muted-foreground"
      >
        Maximum sum insured per insured person
      </Label>
      <div className="flex items-center gap-1.5">
        <span className="text-sm text-muted-foreground">S$</span>
        <Input
          id="setup-max-sum-insured"
          inputMode="decimal"
          value={text}
          placeholder="Not stated"
          onChange={(event) => setText(event.target.value)}
          onBlur={commit}
          aria-invalid={invalid || undefined}
          aria-describedby="setup-max-sum-insured-hint"
          className={invalid ? "h-8 w-36 border-error text-sm" : "h-8 w-36 text-sm tabular-nums"}
        />
      </div>
      <p id="setup-max-sum-insured-hint" className="text-xs text-muted-foreground">
        {invalid
          ? "Enter a positive amount, or leave it blank if the slip states no maximum."
          : "From the slip. Caps salary-based cover once the setup is confirmed. Leave blank if there is no maximum."}
      </p>
    </div>
  );
}
