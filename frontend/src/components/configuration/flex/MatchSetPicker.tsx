import { useId, useMemo, useState } from "react";
import { X, ChevronDown } from "lucide-react";
import { Input } from "@/components/ui/input";
import { FieldLabel } from "@/components/ui/tooltip";
import type { VocabValue } from "@/types";

interface Props {
  label: string;
  hint?: string;
  /** Currently selected roster values (raw strings). */
  selected: string[];
  /** Distinct roster values to suggest, with headcounts. */
  options: VocabValue[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}

const norm = (s: string) => s.trim().toLowerCase();
// Cap the visible list so a large roster (100s of job titles) stays snappy;
// the search box narrows it, and a footer nudges the broker to keep typing.
const MAX_VISIBLE = 50;

/**
 * Searchable, SELECT-ONLY chip multi-select for a tier's roster-anchored match
 * set. Values come exclusively from the roster vocabulary (with headcounts) — a
 * value that isn't on the roster would match no employee, so free text isn't
 * allowed; the broker searches and picks. Already-selected values that are no
 * longer on the roster (e.g. an AI-seeded term, or the roster changed) still show
 * with a "not found" warning so they can be reviewed and removed.
 */
export function MatchSetPicker({
  label,
  hint,
  selected,
  options,
  onChange,
  placeholder,
}: Props) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const sel = selected ?? [];
  const has = (v: string) => sel.some((s) => norm(s) === norm(v));
  const countFor = (v: string) =>
    options.find((o) => norm(o.value) === norm(v))?.count;

  // Roster options not already selected, filtered by the search text.
  const matches = useMemo(() => {
    const q = norm(draft);
    return options
      .filter((o) => !has(o.value))
      .filter((o) => !q || norm(o.value).includes(q));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, draft, sel]);
  const shown = matches.slice(0, MAX_VISIBLE);
  const overflow = matches.length - shown.length;

  const addValue = (v: string) => {
    if (!v || has(v)) return;
    onChange([...sel, v]);
    setDraft("");
    setActive(0);
  };
  const remove = (v: string) => onChange(sel.filter((s) => s !== v));

  return (
    <div className="space-y-1.5">
      <FieldLabel hint={hint || undefined}>{label}</FieldLabel>
      <div className="relative">
        <Input
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          autoComplete="off"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          // Blur closes the list; option clicks use onMouseDown (fires before blur)
          // so a selection isn't cancelled. Pending search text is discarded — it's
          // a filter, never a committed value.
          onBlur={() => setOpen(false)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setActive((a) => Math.min(a + 1, shown.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (shown[active]) addValue(shown[active].value);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          placeholder={placeholder ?? "Search roster…"}
          className="h-8 pr-8"
        />
        <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        {open && (
          <ul
            id={listId}
            role="listbox"
            className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border bg-card p-1 shadow-md"
          >
            {shown.length === 0 ? (
              <li className="px-2 py-1.5 text-xs text-muted-foreground">
                {options.length === 0
                  ? "No roster values yet — upload a roster first."
                  : "No matching roster value."}
              </li>
            ) : (
              shown.map((o, i) => (
                <li key={o.value} role="option" aria-selected={i === active}>
                  <button
                    type="button"
                    // preventDefault keeps focus on the input so the list stays
                    // open for multi-select and blur doesn't cancel the click.
                    onMouseDown={(e) => {
                      e.preventDefault();
                      addValue(o.value);
                    }}
                    onMouseEnter={() => setActive(i)}
                    className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      i === active ? "bg-muted" : "hover:bg-muted/60"
                    }`}
                  >
                    <span className="truncate text-foreground">{o.value}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {o.count} staff{o.claimed ? " · in another tier" : ""}
                    </span>
                  </button>
                </li>
              ))
            )}
            {overflow > 0 && (
              <li className="px-2 py-1.5 text-xs text-muted-foreground">
                …{overflow} more — keep typing to narrow.
              </li>
            )}
          </ul>
        )}
      </div>
      {sel.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {sel.map((v) => {
            const c = countFor(v);
            const inRoster = c !== undefined;
            return (
              <span
                key={v}
                className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs ${
                  inRoster
                    ? "border-border bg-muted/40"
                    : "border-warn/50 bg-warn-soft/40"
                }`}
                title={
                  inRoster
                    ? `${c} employee${c === 1 ? "" : "s"} on the roster`
                    : "Not found on the current roster"
                }
              >
                <span className="text-foreground">{v}</span>
                {inRoster && <span className="text-muted-foreground">· {c}</span>}
                <button
                  type="button"
                  onClick={() => remove(v)}
                  aria-label={`Remove ${v}`}
                  className="text-muted-foreground hover:text-error"
                >
                  <X className="size-3" />
                </button>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
