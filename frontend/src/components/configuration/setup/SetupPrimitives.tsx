import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { InsurerSelect } from "@/components/configuration/InsurerSelect";
import type { TemplateField } from "@/types";

/** Comma-joined string ↔ list, used by multichoice + taglist fields. Trims and
 *  drops empties so a stray comma never yields a blank chip. */
export function splitList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function joinList(items: string[]): string {
  return items.join(",");
}

/** Fixed set of checkboxes (e.g. Member Cover Eligibility). Value persists as a
 *  comma-joined string of the ticked option labels. */
function MultiChoiceControl({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  const selected = new Set(splitList(value));
  const toggle = (opt: string) => {
    const next = new Set(selected);
    if (next.has(opt)) next.delete(opt);
    else next.add(opt);
    // Preserve the option order, not click order.
    onChange(joinList(options.filter((o) => next.has(o))));
  };
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2 rounded-md border border-input bg-card px-3 py-2.5">
      {options.map((opt) => (
        <label
          key={opt}
          className="flex cursor-pointer items-center gap-2 text-sm text-foreground"
        >
          <Checkbox
            checked={selected.has(opt)}
            onCheckedChange={() => toggle(opt)}
          />
          {opt}
        </label>
      ))}
    </div>
  );
}

/** Free-text chip input (e.g. employee IDs). Type a value, press Enter/comma to
 *  add it as a removable chip. Value persists as a comma-joined string. */
function TagListControl({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const tags = splitList(value);
  const add = () => {
    // Split on commas too, so a pasted "E1, E2, E3" becomes three chips and no
    // single chip can ever contain a comma (which the comma-joined storage
    // could not round-trip — it would re-split into phantom chips).
    const parts = splitList(draft);
    if (parts.length) {
      const next = [...tags];
      for (const p of parts) if (!next.includes(p)) next.push(p);
      onChange(joinList(next));
    }
    setDraft("");
  };
  const remove = (tag: string) =>
    onChange(joinList(tags.filter((t) => t !== tag)));
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-input bg-card px-2 py-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/40">
      {tags.map((tag) => (
        <span
          key={tag}
          className="flex items-center gap-1 rounded bg-muted px-2 py-0.5 text-xs text-foreground"
        >
          {tag}
          <button
            type="button"
            onClick={() => remove(tag)}
            aria-label={`Remove ${tag}`}
            className="text-muted-foreground hover:text-error"
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          } else if (e.key === "Backspace" && !draft && tags.length) {
            remove(tags[tags.length - 1]);
          }
        }}
        onBlur={add}
        placeholder={tags.length ? "" : "Type an ID and press Enter…"}
        className="min-w-[8rem] flex-1 bg-transparent px-1 py-0.5 text-sm focus:outline-none"
      />
    </div>
  );
}

export function Section({
  title,
  subtitle,
  defaultOpen = false,
  children,
}: {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
      >
        {open ? (
          <ChevronDown className="size-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-4 text-muted-foreground" />
        )}
        <span className="text-sm font-semibold text-foreground">{title}</span>
        {subtitle && (
          <span className="text-xs text-muted-foreground">· {subtitle}</span>
        )}
      </button>
      {open && <div className="border-t border-border p-4">{children}</div>}
    </div>
  );
}

/** Fields rendered as a wrapping, auto-growing textarea rather than a
 *  single-line input. `insured` is a comma list of every covered legal entity —
 *  routinely longer than one line, and it must be readable in full because it
 *  is the wording reproduced on the exported placement slip. */
const WIDE_FIELD_IDS = new Set(["insured"]);

export const isWideField = (f: TemplateField) =>
  f.type === "textarea" || WIDE_FIELD_IDS.has(f.id);

/** Textarea that grows to fit its content, so a long value is never hidden
 *  behind a scrollbar. Height is recomputed on every value change (including
 *  external ones like a slip pre-fill), not just on typing. */
function AutoTextarea({
  value,
  onChange,
  minRows = 2,
}: {
  value: string;
  onChange: (v: string) => void;
  minRows?: number;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={minRows}
      className="resize-none overflow-hidden rounded-md border border-input bg-card px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring/40"
    />
  );
}

export function FieldControl({
  field,
  value,
  onChange,
  suggestions = [],
}: {
  field: TemplateField;
  value: string;
  onChange: (v: string) => void;
  /** Values used before for this field — shown as a quick-pick. Read live from
   *  the client's prior setups, never hardcoded. */
  suggestions?: string[];
}) {
  const hasSuggestions = suggestions.length > 0;
  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {field.label}
      </Label>
      {field.type === "multichoice" ? (
        <MultiChoiceControl
          options={field.options ?? []}
          value={value}
          onChange={onChange}
        />
      ) : field.type === "taglist" ? (
        <TagListControl value={value} onChange={onChange} />
      ) : isWideField(field) ? (
        <AutoTextarea value={value} onChange={onChange} />
      ) : field.id === "insurer" ? (
        // Every product template carries an `insurer` header field, so routing
        // it here turns all of them into the catalog dropdown at once. Prior
        // values for this client ride along as extra options rather than the
        // separate "Suggestions…" select the other text fields get.
        <InsurerSelect
          value={value}
          onChange={onChange}
          extraOptions={suggestions}
        />
      ) : (
        <div className="flex items-center gap-2">
          <Input
            value={value}
            type={field.type === "number" ? "number" : "text"}
            onChange={(e) => onChange(e.target.value)}
            className="flex-1"
          />
          {hasSuggestions && (
            <Select value="" onValueChange={onChange}>
              <SelectTrigger className="w-[150px] shrink-0">
                <SelectValue placeholder="Suggestions…" />
              </SelectTrigger>
              <SelectContent>
                {suggestions.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      )}
    </div>
  );
}
