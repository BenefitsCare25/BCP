import { Ban, Undo2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/cn";
import { NOT_COVERED, parsePastedColumn } from "@/lib/sob";

const BOOLEAN_OPTIONS = ["YES", "NO", "NA"];

/** Inline affix for the value's kind, so the type is legible without a column. */
export function affixes(kind: string): { prefix: string | null; suffix: string | null } {
  return {
    prefix: kind === "currency" ? "S$" : null,
    suffix: kind === "days" ? "d" : kind === "percent" ? "%" : null,
  };
}

interface CellProps {
  kind: string;
  value: string;
  /** This column carries its own value rather than inheriting the base. */
  overridden?: boolean;
  /** This column is rendering another column's value (base inheritance). */
  inherited?: boolean;
  onChange: (value: string) => void;
  onReset?: () => void;
  /** Multi-row clipboard paste: fill this column downwards from this row. */
  onPasteColumn?: (values: string[]) => void;
  ariaLabel: string;
}

/**
 * One value cell. Chrome that used to sit permanently under every cell (the
 * "Not covered" toggle, the "reset" link) is now hover/focus-revealed, so a
 * 69-row schedule stops paying for affordances 99% of its rows never use.
 */
export function SobCell({
  kind,
  value,
  overridden,
  inherited,
  onChange,
  onReset,
  onPasteColumn,
  ariaLabel,
}: CellProps) {
  const notCovered = value === NOT_COVERED;
  const { prefix, suffix } = affixes(kind);

  if (notCovered) {
    return (
      <div className="group/cell relative">
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label={`${ariaLabel} — not covered, click to cover`}
          className="flex h-8 w-full items-center gap-1.5 rounded-md border border-dashed border-border px-2 text-left text-xs italic text-muted-foreground hover:border-foreground/30 hover:text-foreground"
        >
          <Ban className="size-3 shrink-0" />
          Not covered
        </button>
      </div>
    );
  }

  return (
    <div className="group/cell relative">
      {kind === "boolean" ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger
            aria-label={ariaLabel}
            className={cn("h-8 text-sm", overridden && "border-warn")}
          >
            <SelectValue placeholder="—" />
          </SelectTrigger>
          <SelectContent>
            {BOOLEAN_OPTIONS.map((o) => (
              <SelectItem key={o} value={o}>
                {o}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <>
          {prefix && (
            <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">
              {prefix}
            </span>
          )}
          <Input
            aria-label={ariaLabel}
            value={value}
            onPaste={(e) => {
              if (!onPasteColumn) return;
              const text = e.clipboardData.getData("text/plain");
              // Only intercept a genuine MULTI-row paste; a normal single-value
              // paste must keep the browser's default behaviour (respecting the
              // caret and any selection inside the field).
              if (!/[\r\n]/.test(text.trim())) return;
              e.preventDefault();
              onPasteColumn(parsePastedColumn(text));
            }}
            inputMode={
              kind === "currency" || kind === "days" || kind === "percent"
                ? "decimal"
                : undefined
            }
            onChange={(e) => onChange(e.target.value)}
            className={cn(
              "h-8 text-sm tabular-nums",
              prefix && "pl-7",
              suffix && "pr-12",
              overridden && "border-warn",
              // An inherited cell is shown muted so it reads as "same as the
              // base column" rather than as a value someone typed here.
              inherited && !overridden && "text-muted-foreground",
            )}
          />
          {suffix && (
            <span className="pointer-events-none absolute right-8 top-1/2 -translate-y-1/2 text-2xs text-muted-foreground">
              {suffix}
            </span>
          )}
        </>
      )}

      <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center opacity-0 transition-opacity focus-within:opacity-100 group-hover/cell:opacity-100">
        {overridden && onReset && (
          <button
            type="button"
            onClick={onReset}
            title="Reset to the base value"
            aria-label={`${ariaLabel} — reset to base value`}
            className="rounded p-1 text-warn hover:bg-muted"
          >
            <Undo2 className="size-3" />
          </button>
        )}
        <button
          type="button"
          onClick={() => onChange(NOT_COVERED)}
          title="Mark not covered"
          aria-label={`${ariaLabel} — mark not covered`}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Ban className="size-3" />
        </button>
      </div>
    </div>
  );
}
