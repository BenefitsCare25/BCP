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
  /**
   * Allow committing a value that isn't in `options` (Enter or Tab).
   *
   * OFF by default, and that default is load-bearing for flex tiers: a tier
   * match set that names a value absent from the roster matches nobody, so
   * free text there is always a mistake. The Insured picker turns it ON
   * because product setup routinely happens BEFORE the roster is uploaded —
   * there the vocabulary is legitimately empty and the broker must still be
   * able to record the entities the slip names.
   */
  allowCustom?: boolean;
  /** Suffix on an option already selected elsewhere (context-specific). */
  claimedNote?: string;
  /** Shown when there are no options at all. */
  emptyHint?: string;
  /** Tooltip on a chip that matches no option. */
  unknownNote?: string;
  /** Noun used in chip tooltips ("employee" → "3 employees on the roster"). */
  countNoun?: string;
}

const norm = (s: string) => s.trim().toLowerCase();
// Cap the visible list so a large roster (100s of job titles) stays snappy;
// the search box narrows it, and a footer nudges the broker to keep typing.
const MAX_VISIBLE = 50;

/**
 * Searchable chip multi-select over a roster-anchored vocabulary.
 *
 * SELECT-ONLY by default (flex tier match sets): values come exclusively from
 * the roster vocabulary with headcounts, because a value that isn't on the
 * roster would match no employee. Pass `allowCustom` to also accept typed
 * values — see that prop for when that's correct.
 *
 * Already-selected values that are no longer on the roster (an AI-seeded term,
 * a changed roster, or a slip spelling the roster doesn't use) still show with
 * a "not found" warning so they can be reviewed and remapped.
 */
export function MatchSetPicker({
  label,
  hint,
  selected,
  options,
  onChange,
  placeholder,
  allowCustom = false,
  claimedNote = " · in another tier",
  emptyHint = "No roster values yet — upload a roster first.",
  unknownNote = "Not found on the current roster",
  countNoun = "employee",
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
              // The highlighted suggestion wins; otherwise commit the typed
              // text as its own token when free entry is allowed.
              if (shown[active]) addValue(shown[active].value);
              else if (allowCustom) addValue(draft.trim());
            } else if (e.key === "Tab" && allowCustom && draft.trim()) {
              // Tab commits the token and keeps focus, so several entities can
              // be entered in a row without reaching for the mouse.
              e.preventDefault();
              addValue(draft.trim());
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
                {allowCustom && draft.trim()
                  ? `Press Enter to add “${draft.trim()}”.`
                  : options.length === 0
                    ? emptyHint
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
                      {o.count} staff{o.claimed ? claimedNote : ""}
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
                    ? `${c} ${countNoun}${c === 1 ? "" : "s"} on the roster`
                    : unknownNote
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
