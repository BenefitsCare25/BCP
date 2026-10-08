import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { HEX_COLOR, contrastRatio, readableForeground } from "@/lib/brand";

/** WCAG thresholds: body text, and text or controls at large size / UI. */
const TEXT_AA = 4.5;
const UI_AA = 3;

function Ratio({ label, ratio, need }: { label: string; ratio: number; need: number }) {
  const ok = ratio >= need;
  return (
    <li className="flex items-center gap-2">
      <Badge variant={ok ? "good" : "warn"}>{ok ? "Passes" : "Low contrast"}</Badge>
      <span>
        {label}: {ratio.toFixed(2)}:1 <span className="text-subtle">(needs {need}:1)</span>
      </span>
    </li>
  );
}

/** A brand colour: native picker plus hex text, blank to inherit. Shows a
 *  preview chip with the text colour the platform derives for it, and live
 *  contrast checks for that text and for the colour used as a link or focus
 *  ring on a white page. */
export function BrandColorField({
  id,
  label,
  help,
  value,
  inherited,
  error,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  help: string;
  /** "#rrggbb", or "" to inherit. */
  value: string;
  /** What blank resolves to. */
  inherited: string;
  error?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const typed = value.trim();
  const valid = HEX_COLOR.test(typed);
  const shown = valid ? typed.toLowerCase() : inherited;
  const foreground = readableForeground(shown);
  const describedBy = [`${id}-help`, `${id}-checks`, error ? `${id}-error` : ""].filter(Boolean).join(" ");

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`${id}-hex`}>{label}</Label>
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="color"
          aria-label={`${label} picker`}
          value={shown}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="h-8 w-10 cursor-pointer rounded-md border border-input bg-card p-0.5 disabled:cursor-not-allowed disabled:opacity-50"
        />
        <Input
          id={`${id}-hex`}
          value={value}
          placeholder={inherited}
          spellCheck={false}
          autoComplete="off"
          maxLength={7}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value.trim())}
          className="w-28 font-mono"
        />
        {/* Data-driven preview: the brand colour itself, not a theme colour. */}
        <span
          className="inline-flex h-8 items-center rounded-md px-3 text-sm font-medium"
          style={{ background: shown, color: foreground }}
          aria-hidden="true"
        >
          Button
        </span>
        {typed && !disabled && (
          <Button type="button" variant="ghost" size="sm" onClick={() => onChange("")}>
            Use inherited
          </Button>
        )}
      </div>
      <p id={`${id}-help`} className="text-xs text-muted-foreground">
        {help} {typed ? "" : `Inherited: ${inherited}.`}
      </p>
      <ul id={`${id}-checks`} className="space-y-1 text-xs text-muted-foreground">
        <Ratio label="Button text on this colour" ratio={contrastRatio(shown, foreground)} need={TEXT_AA} />
        <Ratio label="As a link or focus ring on white" ratio={contrastRatio(shown, "#ffffff")} need={UI_AA} />
      </ul>
      {error && (
        <p id={`${id}-error`} role="alert" className="text-xs text-error">
          {error}
        </p>
      )}
    </div>
  );
}
